"""The shared chat bot (P3): search, commands and fallbacks, with a fake Messenger.

Fixtures `chat_library` and `make_searcher` are in conftest.py. No network, no models.
"""

import logging
from pathlib import Path

import pytest

from imgsearch.chat.bot import (
    ERROR_REPLY,
    HELP,
    NEED_SEARCH,
    NO_DRIVE,
    NO_MORE,
    NO_RESULTS,
    NOT_FETCHABLE,
    REWRITE_SKIPPED,
    THUMBNAILS_ONLY,
    UNSUPPORTED,
    ChatBot,
    OriginalUnavailable,
    caption_line,
    requested_count,
)
from imgsearch.chat.service import LazySearcher
from imgsearch.config import Config
from imgsearch.interfaces import ParsedQuery, Result
from imgsearch.store.db import connect

CHAT = "5550001"
QUERY = "kutte ki photo"
MISSING_DRIVE = Path("/Volumes/imgsearch-test-no-such-drive")


class FakeMessenger:
    """Records what the bot sends, in order."""

    def __init__(self) -> None:
        self.events: list[tuple] = []

    def send_text(self, to: str, text: str) -> None:
        self.events.append(("text", to, text))

    def send_photos(self, to: str, photos: list[tuple[Path, str]]) -> None:
        self.events.append(("photos", to, list(photos)))

    def send_document(self, to: str, path: Path, filename: str, caption: str) -> None:
        self.events.append(("document", to, path, filename, caption))

    def show_activity(self, to: str, action: str) -> None:
        self.events.append(("activity", to, action))

    def texts(self) -> list[str]:
        return [e[2] for e in self.events if e[0] == "text"]

    def albums(self) -> list[list[tuple[Path, str]]]:
        return [e[2] for e in self.events if e[0] == "photos"]

    def documents(self) -> list[tuple]:
        return [e[2:] for e in self.events if e[0] == "document"]

    def kinds(self) -> list[str]:
        return [e[2] if e[0] == "activity" else e[0] for e in self.events]


def unplugged(config: Config) -> Config:
    return config.model_copy(
        update={
            "originals_root": MISSING_DRIVE / "originals",
            "derivatives_root": MISSING_DRIVE / "derivatives",
        }
    )


@pytest.fixture
def messenger() -> FakeMessenger:
    return FakeMessenger()


def make_bot(config, messenger, searcher, fetch_original=None, max_mb=50.0) -> ChatBot:
    def no_fetch(image_id: str) -> Path:
        raise AssertionError("fetch_original must not be called")

    return ChatBot(
        config, messenger, LazySearcher(lambda: searcher), fetch_original or no_fetch, max_mb
    )


@pytest.fixture
def bot(config, chat_library, make_searcher, messenger) -> ChatBot:
    return make_bot(config, messenger, make_searcher(chat_library))


# ---- search ----


def test_text_message_sends_top_results_as_one_album_of_derivatives(bot, messenger, config):
    bot.on_message(QUERY, CHAT)
    [album] = messenger.albums()
    assert len(album) == config.search.top_k == 5
    assert [path for path, _ in album] == [
        config.derivatives_root / f"img0{n}.jpg" for n in range(1, 6)
    ]
    assert [caption for _, caption in album] == [
        "1 · 2024-05-01 · A brown dog sleeping on a sofa.",
        "2 · 2024-05-02",
        "3 · 2024-05-03",
        "4 · 2024-05-04",
        "5 · 2024-05-05",
    ]
    assert messenger.texts() == []
    assert all(e[1] == CHAT for e in messenger.events)


def test_chat_actions_while_searching_and_sending(bot, messenger):
    bot.on_message(QUERY, CHAT)
    # typing, the cold load, typing again (the status lasts 5 s), search, then the upload.
    assert messenger.kinds() == ["typing", "typing", "upload_photo", "photos"]
    messenger.events.clear()
    bot.on_message(QUERY, CHAT)  # warm: no second "typing"
    assert messenger.kinds() == ["typing", "upload_photo", "photos"]


def test_search_runs_once_with_the_cli_defaults(config, chat_library, make_searcher, messenger):
    searcher = make_searcher(chat_library)
    calls = []
    search = searcher.search

    def recording_search(query, **kwargs):
        calls.append(kwargs)
        return search(query, **kwargs)

    searcher.search = recording_search
    make_bot(config, messenger, searcher).on_message(QUERY, CHAT)
    assert searcher.rewriter.queries == [QUERY]  # the rewrite is on, as in the CLI
    # No signals given: the searcher's own, built from search.default_signals.
    assert calls == [{"k": config.chat.max_results}]


def test_caption_date_falls_back_to_the_file_mtime(bot, messenger, chat_library):
    with chat_library:
        chat_library.execute(
            "UPDATE images SET taken_at = NULL, file_mtime = '2023-01-02T03:04:05'"
            " WHERE id = 'img02'"
        )
    bot.on_message(QUERY, CHAT)
    assert messenger.albums()[0][1][1] == "2 · 2023-01-02"


def dated_searcher(make_searcher, chat_library, date_range):
    """A searcher whose rewrite finds `date_range`; its retriever honours the range like
    the real ones (images are dated 2024-05-01 to 2024-05-12)."""
    searcher = make_searcher(chat_library)
    searcher.rewriter.rewrite = lambda q: ParsedQuery("dogs", q, date_range=date_range)
    every_date = searcher.retrievers["vector"].retrieve

    def retrieve(q, k):
        found = every_date(q, k)
        if q.date_range is None:
            return found
        start, end = q.date_range
        return [c for c in found if start <= f"2024-05-{c.image_id[3:]}" <= end]

    searcher.retrievers["vector"].retrieve = retrieve
    return searcher


def test_date_fallback_says_no_photos_matched_the_dates(
    config, chat_library, make_searcher, messenger
):
    """P1.7 review N3: with `date_fallback`, the reply says the dates matched nothing."""
    searcher = dated_searcher(make_searcher, chat_library, ("2019-09-15", "2019-09-16"))
    make_bot(config, messenger, searcher).on_message("birthday images from 15-16 sept", CHAT)
    note = "No photos matched the dates 2019-09-15 to 2019-09-16, so these are from all dates."
    assert messenger.texts() == [note]
    kinds = [e[0] for e in messenger.events if e[0] != "activity"]
    assert kinds == ["text", "photos"]  # the note comes before the photos
    assert len(messenger.albums()[0]) == 5


def test_no_date_note_when_the_dates_matched(config, chat_library, make_searcher, messenger):
    searcher = dated_searcher(make_searcher, chat_library, ("2024-05-02", "2024-05-03"))
    make_bot(config, messenger, searcher).on_message("dogs 2-3 may", CHAT)
    assert messenger.texts() == []
    assert [c.split(" · ")[1] for _, c in messenger.albums()[0]] == ["2024-05-02", "2024-05-03"]


def test_more_pages_through_the_last_search(bot, messenger, config):
    bot.on_message(QUERY, CHAT)
    bot.on_message("More", CHAT)
    first, second = messenger.albums()
    assert [c.split(" · ")[0] for _, c in second] == ["6", "7", "8", "9", "10"]
    assert second[0][0] == config.derivatives_root / "img06.jpg"
    # The search kept chat.max_results (20) of the 22 images: two more pages, then none.
    bot.on_message("/more", CHAT)
    bot.on_message("more", CHAT)
    assert [c.split(" · ")[0] for _, c in messenger.albums()[3]] == ["16", "17", "18", "19", "20"]
    bot.on_message("more", CHAT)
    assert messenger.texts() == [NO_MORE]


@pytest.mark.parametrize(
    ("text", "count"),
    [
        ("give me 10 images of my pet dog", 10),
        ("3 photos of the beach", 3),
        ("top 7 dogs", 7),
        ("mummy ki 4 tasveerein", 4),
        ("कुत्ते की ५ तस्वीरें", 5),
        ("50 pics", 20),  # at most chat.max_results
        ("birthday images from 15-16 sept", None),
        ("trip to goa 2019", None),
        ("photos of 2 dogs", None),
        ("0 photos", None),
    ],
)
def test_requested_count(text, count):
    assert requested_count(text, 20) == count


def test_asking_for_ten_sends_ten_and_more_pages_by_ten(bot, messenger):
    bot.on_message("give me 10 images of my pet dog", CHAT)
    bot.on_message("more", CHAT)  # the search keeps chat.max_results (20): ranks 11-20
    first, second = messenger.albums()
    assert len(first) == 10 and first[9][1].startswith("10 · ")
    assert [c.split(" · ")[0] for _, c in second] == [str(n) for n in range(11, 21)]
    bot.on_message("more", CHAT)
    assert messenger.texts() == [NO_MORE]


def test_asking_for_three_pages_by_three(bot, messenger):
    bot.on_message("top 3 dogs", CHAT)
    bot.on_message("more", CHAT)
    first, second = messenger.albums()
    assert [c.split(" · ")[0] for _, c in first] == ["1", "2", "3"]
    assert [c.split(" · ")[0] for _, c in second] == ["4", "5", "6"]


def test_searches_are_kept_per_chat(bot, messenger):
    bot.on_message(QUERY, CHAT)
    bot.on_message("more", "other chat")
    assert messenger.texts() == [NEED_SEARCH]


@pytest.mark.parametrize("command", ["help", " HELP ", "/help", "/start"])
def test_help_and_start_do_not_load_the_models(bot, messenger, command):
    bot.on_message(command, CHAT)
    assert messenger.texts() == [HELP]
    assert bot.searcher.loads == 0


def test_more_and_original_need_a_search_first(bot, messenger):
    bot.on_message("more", CHAT)
    bot.on_message("original 1", CHAT)
    assert messenger.texts() == [NEED_SEARCH, NEED_SEARCH]


def test_pen_drive_unplugged_sends_thumbnails(config, chat_library, make_searcher, messenger):
    make_bot(unplugged(config), messenger, make_searcher(chat_library)).on_message(QUERY, CHAT)
    assert messenger.texts() == [THUMBNAILS_ONLY]
    assert messenger.events[2][0] == "text"  # the note comes before the album
    [album] = messenger.albums()
    assert [p for p, _ in album] == [config.thumbs_root / f"img0{n}.jpg" for n in range(1, 6)]


def test_missing_derivative_falls_back_to_thumbnail(bot, messenger, config):
    (config.derivatives_root / "img01.jpg").unlink()
    bot.on_message(QUERY, CHAT)
    album = messenger.albums()[0]
    assert album[0][0] == config.thumbs_root / "img01.jpg"
    assert album[1][0] == config.derivatives_root / "img02.jpg"


def test_rewrite_fallback_says_so_and_still_sends_results(
    config, chat_library, make_searcher, messenger
):
    searcher = make_searcher(chat_library, rewrite_error="ConnectError: refused")
    make_bot(config, messenger, searcher).on_message(QUERY, CHAT)
    assert messenger.texts() == [REWRITE_SKIPPED]
    assert len(messenger.albums()[0]) == 5


def test_no_results(config, chat_library, make_searcher, messenger):
    make_bot(config, messenger, make_searcher(chat_library, image_ids=[])).on_message(QUERY, CHAT)
    assert messenger.texts() == [NO_RESULTS]
    assert messenger.albums() == []


def test_search_error_gives_a_short_reply_not_a_traceback(config, messenger, caplog):
    def broken_build():
        raise RuntimeError(f"model failed on {QUERY}")

    bot = ChatBot(config, messenger, LazySearcher(broken_build), lambda i: Path(), 50.0)
    with caplog.at_level(logging.DEBUG):
        bot.on_message(QUERY, CHAT)
    assert messenger.texts() == [ERROR_REPLY]
    assert "RuntimeError" in caplog.text
    assert QUERY not in caplog.text  # the exception's message isn't logged


def test_sending_error_gives_the_short_reply(bot, messenger):
    def fail(to, photos):
        raise OSError("upload failed")

    messenger.send_photos = fail
    bot.on_message(QUERY, CHAT)
    assert messenger.texts() == [ERROR_REPLY]


def test_unsupported_message(bot, messenger):
    bot.on_unsupported(CHAT)
    assert messenger.texts() == [UNSUPPORTED]


def test_logs_hold_no_text_chat_or_paths(bot, messenger, caplog):
    with caplog.at_level(logging.DEBUG):
        bot.on_message(QUERY, CHAT)
        bot.on_message("original 1", CHAT)
    assert "search: 20 results" in caplog.text
    for private in (QUERY, CHAT, "IMG_0001", "img01"):
        assert private not in caplog.text


# ---- original N ----


def test_original_sends_the_file_as_a_document(bot, messenger, config):
    bot.on_message(QUERY, CHAT)
    bot.on_message("original 2", CHAT)
    [(path, filename, caption)] = messenger.documents()
    assert path == config.originals_root / "2024/IMG_0002.HEIC"
    assert (filename, caption) == ("IMG_0002.HEIC", "2 · original")
    assert messenger.kinds()[-2:] == ["upload_document", "document"]


@pytest.mark.parametrize(
    "command", ["/original 1", "Original 1.", "original1", "orig 1", "original #1", "ORIGINAL 1!"]
)
def test_original_command_variants(bot, messenger, command):
    bot.on_message(QUERY, CHAT)
    bot.on_message(command, CHAT)
    assert len(messenger.documents()) == 1
    assert bot.searcher.get().rewriter.queries == [QUERY]  # not searched as a query


def test_original_number_must_be_among_the_shown_results(bot, messenger):
    bot.on_message(QUERY, CHAT)
    bot.on_message("original 6", CHAT)  # only 1-5 shown so far
    bot.on_message("original 0", CHAT)
    assert messenger.texts() == ["Choose N from 1 to 5: original N."] * 2
    assert messenger.documents() == []


def test_original_with_the_pen_drive_unplugged(config, chat_library, make_searcher, messenger):
    bot = make_bot(unplugged(config), messenger, make_searcher(chat_library))
    bot.on_message(QUERY, CHAT)
    bot.on_message("original 1", CHAT)
    assert messenger.texts()[-1] == NO_DRIVE
    assert messenger.documents() == []


def test_evicted_original_is_fetched_then_sent(config, chat_library, make_searcher, messenger):
    evicted = config.originals_root / "2024/IMG_0001.HEIC"
    evicted.unlink()
    fetched = []

    def fetch_original(image_id: str) -> Path:
        fetched.append(image_id)
        evicted.write_bytes(b"restored")  # what the P1.6 fetch does
        return evicted

    bot = make_bot(config, messenger, make_searcher(chat_library), fetch_original)
    bot.on_message(QUERY, CHAT)
    bot.on_message("original 1", CHAT)
    assert fetched == ["img01"]
    assert messenger.documents()[0][0] == evicted


def test_original_not_fetchable(config, chat_library, make_searcher, messenger):
    (config.originals_root / "2024/IMG_0001.HEIC").unlink()

    def fetch_original(image_id: str) -> Path:
        raise OriginalUnavailable("S3 offline")

    bot = make_bot(config, messenger, make_searcher(chat_library), fetch_original)
    bot.on_message(QUERY, CHAT)
    bot.on_message("original 1", CHAT)
    assert messenger.texts() == [NOT_FETCHABLE]
    assert messenger.documents() == []


def test_original_over_the_size_limit_is_refused(config, chat_library, make_searcher, messenger):
    bot = make_bot(config, messenger, make_searcher(chat_library), max_mb=0.000001)
    bot.on_message(QUERY, CHAT)
    bot.on_message("original 1", CHAT)
    assert messenger.texts()[-1] == "Original 1 is 0 MB, over the 0 MB limit for files sent here."
    assert messenger.documents() == []


def test_caption_line_without_date_or_caption():
    r = Result("x", "a.jpg", None, None, None, 0.1, {})
    assert caption_line(3, None, r) == "3"


def test_unloaded_searcher_reopens_the_db(config, chat_library, make_searcher, messenger):
    """After an idle unload (D21) the next search builds a new searcher and DB connection."""
    built = []

    def build():
        built.append(make_searcher(connect(config)))
        return built[-1]

    bot = ChatBot(config, messenger, LazySearcher(build), lambda i: Path(), 50.0)
    bot.on_message(QUERY, CHAT)
    bot.searcher.unload()
    bot.on_message(QUERY, CHAT)
    assert len(built) == 2 and len(messenger.albums()) == 2
