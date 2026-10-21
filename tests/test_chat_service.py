"""The bot's background side (P3): the worker thread and the idle-unloading searcher (D21)."""

import logging
import sqlite3
import threading

import pytest

from imgsearch.chat.service import Incoming, LazySearcher, Worker
from imgsearch.store.db import connect


def message(message_id: str) -> Incoming:
    return Incoming(message_id, f"log-{message_id}", "chat", "text", "x")


def test_lazy_searcher_loads_once_and_unloads(config, chat_library, make_searcher):
    lazy = LazySearcher(lambda: make_searcher(connect(config)))
    assert not lazy.loaded
    first = lazy.get()
    assert lazy.get() is first and lazy.loads == 1
    lazy.unload()
    assert not lazy.loaded
    with pytest.raises(sqlite3.ProgrammingError):  # its DB connection was closed
        first.conn.execute("SELECT 1")
    lazy.unload()  # nothing loaded: no-op
    assert lazy.get() is not first and lazy.loads == 2


def test_worker_calls_on_idle_after_the_timeout():
    handled, idle = [], threading.Event()
    worker = Worker(lambda m: handled.append(m.message_id), idle.set, idle_s=0.01)
    worker.start()
    worker.submit(message("1"))
    assert idle.wait(timeout=5)
    worker.stop()
    assert handled == ["1"]


def test_worker_survives_a_failing_message(caplog):
    handled = []
    private = "private text"  # an exception message can quote a query; it isn't logged

    def handle(m: Incoming) -> None:
        if m.message_id == "bad":
            raise RuntimeError(private)
        handled.append(m.message_id)

    worker = Worker(handle, lambda: None, idle_s=60)
    with caplog.at_level(logging.INFO):
        worker.start()
        worker.submit(message("bad"))
        worker.submit(message("good"))
        worker.stop()  # finishes the queue first
    assert handled == ["good"]
    assert "RuntimeError" in caplog.text and "private text" not in caplog.text
    assert "log-good handled" in caplog.text
