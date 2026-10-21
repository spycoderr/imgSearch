"""The bot's background side: one worker thread, and a searcher that unloads when idle.

The webhook must answer Meta at once, so it only queues messages. One worker thread
handles them in order. It owns the DB connection and the models, so nothing is shared
between threads. After `idle_s` without a message the searcher is dropped and SigLIP's
memory freed (D21); the next message loads it again (a cold start of a few seconds).
"""

import gc
import logging
import queue
import sys
import threading
import time
import traceback
from collections.abc import Callable

from imgsearch.chat.whatsapp import Incoming
from imgsearch.search.hybrid import Searcher

log = logging.getLogger(__name__)


class LazySearcher:
    """Builds the searcher (DB connection and models) on first use; `unload` drops it.

    Use it from one thread only: the worker's.
    """

    def __init__(self, build: Callable[[], Searcher]) -> None:
        self.build = build
        self.searcher: Searcher | None = None
        self.loads = 0  # how many times the models were loaded

    @property
    def loaded(self) -> bool:
        return self.searcher is not None

    def get(self) -> Searcher:
        if self.searcher is None:
            start = time.perf_counter()
            self.searcher = self.build()
            self.loads += 1
            log.info("searcher loaded in %.2fs", time.perf_counter() - start)
        return self.searcher

    def unload(self) -> None:
        if self.searcher is None:
            return
        self.searcher.conn.close()
        self.searcher = None
        free_device_memory()
        log.info("searcher unloaded")


def free_device_memory() -> None:
    gc.collect()
    # Only if a model loaded torch: importing it just to empty its cache would cost memory.
    torch = sys.modules.get("torch")
    if torch is not None and torch.backends.mps.is_available():
        torch.mps.empty_cache()


class Worker:
    """Handles queued messages one at a time on its own thread; calls `on_idle` after
    `idle_s` seconds without one (and again every `idle_s` while idle)."""

    def __init__(
        self,
        handle: Callable[[Incoming], None],
        on_idle: Callable[[], None],
        idle_s: float,
    ) -> None:
        self.handle = handle
        self.on_idle = on_idle
        self.idle_s = idle_s
        self.queue: queue.Queue[Incoming | None] = queue.Queue()
        self.thread = threading.Thread(target=self._run, name="imgsearch-chat", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def submit(self, message: Incoming) -> None:
        self.queue.put(message)

    def stop(self) -> None:
        """Finish the queued messages, then stop."""
        self.queue.put(None)
        self.thread.join()

    def _run(self) -> None:
        while True:
            try:
                message = self.queue.get(timeout=self.idle_s)
            except queue.Empty:
                self.on_idle()
                continue
            try:
                if message is None:
                    self.on_idle()
                    return
                start = time.perf_counter()
                self.handle(message)
                log.info(
                    "message %s handled in %.2fs", message.message_id, time.perf_counter() - start
                )
            except Exception as e:  # one failed message must not stop the bot
                log.error("message failed: %s\n%s", type(e).__name__, traceback_lines(e))
            finally:
                self.queue.task_done()


def traceback_lines(e: BaseException) -> str:
    """Where `e` was raised, without its message: an exception's text can quote the query."""
    return "".join(traceback.format_tb(e.__traceback__))
