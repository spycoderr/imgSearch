"""The WhatsApp adapter (implements `ChatAdapter`): turns a message into a search and sends
the results back as images.

Replies:
- any text: search it (same defaults as `imgsearch search`) and send the top
  `search.top_k` as images: the derivative, or the thumbnail when the pen drive is out.
- `help`: what the bot understands.
- `more`: the next `search.top_k` results of the last search.
- `original N`: result N's original file as a document, if it is on the pen drive or can
  be fetched from S3, and under `whatsapp.original_max_mb`.

Runs on the service's worker thread, one message at a time. Logs hold counts and timings,
never the message text, the sender or photo paths.
"""

import logging
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from imgsearch.chat.service import LazySearcher, traceback_lines
from imgsearch.chat.whatsapp import GraphClient
from imgsearch.config import Config
from imgsearch.interfaces import Result
from imgsearch.search.show import short
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


@dataclass
class LastSearch:
    """One sender's last search, for `more` and `original N`."""

    results: list[Result]  # up to whatsapp.max_results
    shown: int  # how many were sent so far


class WhatsAppAdapter:
    """Implements `ChatAdapter` (`on_message`, `send_images`) over the WhatsApp Graph API.

    `fetch_original(image_id)` returns the original's path after restoring it from S3
    (P1.6 fetch), or raises OriginalUnavailable.
    """

    def __init__(
        self,
        config: Config,
        client: GraphClient,
        searcher: LazySearcher,
        fetch_original: Callable[[str], Path],
    ) -> None:
        self.config = config
        self.client = client
        self.searcher = searcher
        self.fetch_original = fetch_original
        self.page = config.search.top_k
        self.last: dict[str, LastSearch] = {}  # by sender
        self.taken_at: dict[str, str | None] = {}  # image id -> date, for captions

    def on_message(self, text: str, sender: str) -> None:
        """Reply to one text message. A failure becomes a short apology, never a traceback."""
        command = " ".join(text.lower().split())
        try:
            if command == "help":
                self.client.send_text(sender, HELP)
            elif command == "more":
                self._more(sender)
            elif match := _ORIGINAL.fullmatch(command):
                self._original(sender, int(match.group(1)))
            else:
                self._search(sender, text)
        except Exception as e:  # acceptance P3 #10: the user gets a short reply, not a trace
            log.error("reply failed: %s\n%s", type(e).__name__, traceback_lines(e))
            self.client.send_text(sender, ERROR_REPLY)

    def on_unsupported(self, sender: str) -> None:
        """A message that isn't text (photo, voice note, ...)."""
        self.client.send_text(sender, UNSUPPORTED)

    def send_images(self, to: str, results: list[Result], first_rank: int = 1) -> None:
        """Send each result as an image with a caption line; ranks start at `first_rank`.

        Sends the derivative, never the original. Without the pen drive (or the
        derivative file) it sends the thumbnail from the Mac.
        """
        full_size = is_available(self.config.derivatives_root)
        if not full_size:
            self.client.send_text(to, THUMBNAILS_ONLY)
        missing = 0
        for rank, r in enumerate(results, start=first_rank):
            path = self._image_file(r, full_size)
            if path is None:
                missing += 1
                continue
            self.client.send_image(to, path, caption_line(rank, self.taken_at.get(r.image_id), r))
        if missing:
            self.client.send_text(to, f"{missing} of the photos have no image file to send.")

    def _search(self, sender: str, text: str) -> None:
        searcher = self.searcher.get()
        response = searcher.search(text, k=self.config.whatsapp.max_results)
        t = response.timings
        log.info(
            "search: %d results, rewrite %.2fs, retrieval %.2fs%s",
            len(response.results),
            t.rewrite_s,
            t.retrieval_s,
            ", rewrite fell back" if response.query.rewrite_error else "",
        )
        results = response.results
        self.taken_at.update(taken_dates(searcher.conn, [r.image_id for r in results]))
        if response.query.rewrite_error:
            self.client.send_text(sender, REWRITE_SKIPPED)
        if not results:
            self.last.pop(sender, None)
            self.client.send_text(sender, NO_RESULTS)
            return
        self.last[sender] = LastSearch(results, shown=min(self.page, len(results)))
        self.send_images(sender, results[: self.page])

    def _more(self, sender: str) -> None:
        last = self.last.get(sender)
        if last is None:
            self.client.send_text(sender, NEED_SEARCH)
            return
        if last.shown >= len(last.results):
            self.client.send_text(sender, NO_MORE)
            return
        start = last.shown
        last.shown = min(start + self.page, len(last.results))
        self.send_images(sender, last.results[start : last.shown], first_rank=start + 1)

    def _original(self, sender: str, n: int) -> None:
        last = self.last.get(sender)
        if last is None:
            self.client.send_text(sender, NEED_SEARCH)
            return
        if not 1 <= n <= last.shown:
            self.client.send_text(sender, f"Choose N from 1 to {last.shown}: original N.")
            return
        if not is_available(self.config.originals_root):
            self.client.send_text(sender, NO_DRIVE)
            return
        r = last.results[n - 1]
        path = self.config.originals_root / r.orig_relpath
        if not path.is_file():
            try:
                path = self.fetch_original(r.image_id)
            except OriginalUnavailable as e:
                log.info("original not fetchable: %s", type(e.__cause__ or e).__name__)
                self.client.send_text(sender, NOT_FETCHABLE)
                return
        size = path.stat().st_size
        limit = self.config.whatsapp.original_max_mb
        if size > limit * MB:
            self.client.send_text(
                sender, f"Original {n} is {size / MB:.0f} MB, over the {limit:.0f} MB limit."
            )
            return
        self.client.send_document(sender, path, Path(r.orig_relpath).name, f"{n} · original")

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


def caption_line(rank: int, taken_at: str | None, r: Result) -> str:
    """e.g. "3 · 2024-05-03 · A brown dog sleeping on a sofa." """
    parts = [str(rank)]
    if taken_at:
        parts.append(taken_at[:10])
    if r.caption:
        parts.append(short(r.caption))
    return " · ".join(parts)


def taken_dates(conn: sqlite3.Connection, image_ids: list[str]) -> dict[str, str | None]:
    marks = ",".join("?" * len(image_ids))
    rows = conn.execute(f"SELECT id, taken_at FROM images WHERE id IN ({marks})", image_ids)
    return {row[0]: row[1] for row in rows}
