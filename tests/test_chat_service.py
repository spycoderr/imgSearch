"""The bot's background side (P3): the worker and the idle-unloading searcher (D21).

The worker is driven with `step()` and `idle_s=0` (an empty queue counts as idle at once),
so nothing here waits on the clock.
"""

import logging
import sqlite3

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


def test_worker_handles_messages_in_order_then_goes_idle():
    handled, idle = [], []
    worker = Worker(lambda m: handled.append(m.message_id), lambda: idle.append(1), idle_s=0)
    worker.submit(message("1"))
    worker.submit(message("2"))
    assert worker.step() and worker.step()
    assert handled == ["1", "2"] and idle == []
    assert worker.step()  # nothing came within idle_s: on_idle (D21 unloads the models)
    assert idle == [1]


def test_worker_unloads_and_stops_when_asked():
    idle = []
    worker = Worker(lambda m: None, lambda: idle.append(1), idle_s=0)
    worker.queue.put(None)  # what stop() queues
    assert worker.step() is False
    assert idle == [1]


def test_worker_survives_a_failing_message(caplog):
    handled = []
    private = "private text"  # an exception message can quote a query; it isn't logged

    def handle(m: Incoming) -> None:
        if m.message_id == "bad":
            raise RuntimeError(private)
        handled.append(m.message_id)

    worker = Worker(handle, lambda: None, idle_s=0)
    worker.submit(message("bad"))
    worker.submit(message("good"))
    with caplog.at_level(logging.INFO):
        assert worker.step() and worker.step()
    assert handled == ["good"]
    assert "RuntimeError" in caplog.text and private not in caplog.text
    assert "log-good handled" in caplog.text


def test_worker_thread_finishes_the_queue_on_stop():
    """The real thread: stop() queues the end marker after the messages and joins."""
    handled = []
    worker = Worker(lambda m: handled.append(m.message_id), lambda: None, idle_s=60)
    worker.start()
    for n in range(5):
        worker.submit(message(str(n)))
    worker.stop()
    assert handled == ["0", "1", "2", "3", "4"]
    assert not worker.thread.is_alive()
