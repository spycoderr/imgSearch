"""The chat bot (implements `ChatAdapter`), shared by every chat app: turns a message into a
search and sends the results back as photos. Each app supplies a `Messenger` that knows how
to send (Telegram: `telegram.TelegramClient`; WhatsApp, parked: `whatsapp.GraphClient`).

Replies:
- any text: search it (same defaults as `imgsearch search`) and send the top
  `search.top_k` as photos: the derivative, or the thumbnail when the pen drive is out.
- `help` (or `/start`): what the bot understands.
- `more`: the next `search.top_k` results of the last search.
- `original N`: result N's original file as a document, if it is on the pen drive or can
  be fetched from S3, and under the app's size limit.
A leading "/" is ignored, so `/more` and `/original 2` work too.

Runs on the service's worker thread, one message at a time. Logs hold counts and timings,
never the message text, the chat or photo paths.
"""

import logging
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from imgsearch.chat.service import LazySearcher, traceback_lines
from imgsearch.config import Config
from imgsearch.interfaces import Result
from imgsearch.search.show import short
from imgsearch.store.dates import SEARCH_DATE_SQL
from imgsearch.store.roots import is_available

log = logging.getLogger(__name__)

MB = 1e6

HELP = (
    "Send what you're looking for, in English, Hindi or Hinglish, e.g. "
    '"mummy ke saath beach pe". I reply with the best matching photos.\n'
    "more: the next photos for your last search\n"
    "original N: the full original file of photo N\n"
    "help: this message"
)
ERROR_REPLY = "Sorry, something went wrong with that search. Please try again."
NO_RESULTS = "No matching photos."
REWRITE_SKIPPED = "(Couldn't translate the query, so I searched it as written.)"
THUMBNAILS_ONLY = (
    "The pen drive isn't plugged in: full-size images are unavailable, so these are thumbnails."
)
UNSUPPORTED = "I can only search by text. Send help to see what I understand."
NEED_SEARCH = "Send a search first."
NO_MORE = "No more photos for that search."
NO_DRIVE = "The pen drive isn't plugged in, so originals are unavailable."
NOT_FETCHABLE = "That original isn't on the pen drive and couldn't be fetched from S3 right now."

_ORIGINAL = re.compile(r"original\s+(\d+)")


class OriginalUnavailable(Exception):
    """The original can't be restored now (no S3 copy, no key, or S3 unreachable)."""


class Messenger(Protocol):
    """How one chat app sends. `to` is that app's chat id (Telegram) or phone number."""

    def send_text(self, to: str, text: str) -> None: ...

    def send_photos(self, to: str, photos: list[tuple[Path, str]]) -> None:
        """JPEG files with a caption each, in order (as one album where the app can)."""
        ...

    def send_document(self, to: str, path: Path, filename: str, caption: str) -> None: ...

    def show_activity(self, to: str, action: str) -> None:
        """Show "typing" or "upload_photo" / "upload_document" (a no-op where unsupported)."""
        ...


@dataclass
class LastSearch:
    """One chat's last search, for `more` and `original N`."""

    results: list[Result]  # up to chat.max_results
    shown: int  # how many were sent so far


class ChatBot:
    """Implements `ChatAdapter` (`on_message`, `send_images`) for any `Messenger`.

    `fetch_original(image_id)` returns the original's path after restoring it from S3
    (P1.6 fetch), or raises OriginalUnavailable. `original_max_mb` is the app's file limit.
    """

    def __init__(
        self,
        config: Config,
        messenger: Messenger,
        searcher: LazySearcher,
        fetch_original: Callable[[str], Path],
        original_max_mb: float,
    ) -> None:
        self.config = config
        self.messenger = messenger
        self.searcher = searcher
        self.fetch_original = fetch_original
        self.original_max_mb = original_max_mb
        self.page = config.search.top_k
        self.last: dict[str, LastSearch] = {}  # by chat
        self.dates: dict[str, str | None] = {}  # image id -> search date, for captions

    def on_message(self, text: str, sender: str) -> None:
        """Reply to one text message. A failure becomes a short apology, never a traceback."""
        command = " ".join(text.lower().split()).removeprefix("/")
        try:
            if command in ("help", "start"):
                self.messenger.send_text(sender, HELP)
            elif command == "more":
                self._more(sender)
            elif match := _ORIGINAL.fullmatch(command):
                self._original(sender, int(match.group(1)))
            else:
                self._search(sender, text)
        except Exception as e:  # acceptance P3 #10: the user gets a short reply, not a trace
            log.error("reply failed: %s\n%s", type(e).__name__, traceback_lines(e))
            self.messenger.send_text(sender, ERROR_REPLY)

    def on_unsupported(self, sender: str) -> None:
        """A message that isn't text (photo, voice note, sticker, ...)."""
        self.messenger.send_text(sender, UNSUPPORTED)

    def send_images(self, to: str, results: list[Result], first_rank: int = 1) -> None:
        """Send the results as photos with a caption line each; ranks start at `first_rank`.

        Sends the derivative, never the original. Without the pen drive (or the
        derivative file) it sends the thumbnail from the Mac.
        """
        full_size = is_available(self.config.derivatives_root)
        if not full_size:
            self.messenger.send_text(to, THUMBNAILS_ONLY)
        photos = []
        for rank, r in enumerate(results, start=first_rank):
            path = self._image_file(r, full_size)
            if path is not None:
                photos.append((path, caption_line(rank, self.dates.get(r.image_id), r)))
        if photos:
            self.messenger.show_activity(to, "upload_photo")
            self.messenger.send_photos(to, photos)
        if missing := len(results) - len(photos):
            self.messenger.send_text(to, f"{missing} of the photos have no image file to send.")

    def _search(self, sender: str, text: str) -> None:
        self.messenger.show_activity(sender, "typing")
        if not self.searcher.loaded:  # a cold start takes seconds; "typing…" lasts only 5
            self.searcher.get()
            self.messenger.show_activity(sender, "typing")
        searcher = self.searcher.get()
        response = searcher.search(text, k=self.config.chat.max_results)
        t = response.timings
        log.info(
            "search: %d results, rewrite %.2fs, retrieval %.2fs%s",
            len(response.results),
            t.rewrite_s,
            t.retrieval_s,
            ", rewrite fell back" if response.query.rewrite_error else "",
        )
        results = response.results
        self.dates.update(search_dates(searcher.conn, [r.image_id for r in results]))
        if response.query.rewrite_error:
            self.messenger.send_text(sender, REWRITE_SKIPPED)
        if response.date_fallback and response.query.date_range:
            start, end = response.query.date_range
            self.messenger.send_text(
                sender,
                f"No photos matched the dates {start} to {end}, so these are from all dates.",
            )
        if not results:
            self.last.pop(sender, None)
            self.messenger.send_text(sender, NO_RESULTS)
            return
        self.last[sender] = LastSearch(results, shown=min(self.page, len(results)))
        self.send_images(sender, results[: self.page])

    def _more(self, sender: str) -> None:
        last = self.last.get(sender)
        if last is None:
            self.messenger.send_text(sender, NEED_SEARCH)
            return
        if last.shown >= len(last.results):
            self.messenger.send_text(sender, NO_MORE)
            return
        start = last.shown
        last.shown = min(start + self.page, len(last.results))
        self.send_images(sender, last.results[start : last.shown], first_rank=start + 1)

    def _original(self, sender: str, n: int) -> None:
        last = self.last.get(sender)
        if last is None:
            self.messenger.send_text(sender, NEED_SEARCH)
            return
        if not 1 <= n <= last.shown:
            self.messenger.send_text(sender, f"Choose N from 1 to {last.shown}: original N.")
            return
        if not is_available(self.config.originals_root):
            self.messenger.send_text(sender, NO_DRIVE)
            return
        r = last.results[n - 1]
        path = self.config.originals_root / r.orig_relpath
        if not path.is_file():
            try:
                path = self.fetch_original(r.image_id)
            except OriginalUnavailable as e:
                log.info("original not fetchable: %s", type(e.__cause__ or e).__name__)
                self.messenger.send_text(sender, NOT_FETCHABLE)
                return
        size = path.stat().st_size
        if size > self.original_max_mb * MB:
            self.messenger.send_text(
                sender,
                f"Original {n} is {size / MB:.0f} MB, over the {self.original_max_mb:.0f} MB"
                " limit for files sent here.",
            )
            return
        self.messenger.show_activity(sender, "upload_document")
        self.messenger.send_document(sender, path, Path(r.orig_relpath).name, f"{n} · original")

    def _image_file(self, r: Result, full_size: bool) -> Path | None:
        if full_size and r.derivative_path:
            path = self.config.derivatives_root / r.derivative_path
            if path.is_file():
                return path
        if r.thumb_path:
            path = self.config.thumbs_root / r.thumb_path
            if path.is_file():
                return path
        return None


def caption_line(rank: int, date: str | None, r: Result) -> str:
    """e.g. "3 · 2024-05-03 · A brown dog sleeping on a sofa." """
    parts = [str(rank)]
    if date:
        parts.append(date[:10])
    if r.caption:
        parts.append(short(r.caption))
    return " · ".join(parts)


def search_dates(conn: sqlite3.Connection, image_ids: list[str]) -> dict[str, str | None]:
    """Each image's date as search uses it: EXIF `taken_at`, else the file's mtime."""
    marks = ",".join("?" * len(image_ids))
    rows = conn.execute(
        f"SELECT i.id, {SEARCH_DATE_SQL} FROM images i WHERE i.id IN ({marks})", image_ids
    )
    return {row[0]: row[1] for row in rows}
