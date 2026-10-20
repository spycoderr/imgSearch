import sqlite3

import pytest

from imgsearch.config import Config
from imgsearch.interfaces import Context, ImageRecord
from imgsearch.stages.runner import run_stage
from imgsearch.store.status import set_status


class DummyStage:
    """Records calls; raises for image ids listed in `fail_on`."""

    def __init__(self, name="dummy", version="1", requires=(), fail_on=()):
        self.name = name
        self.version = version
        self.requires = list(requires)
        self.fail_on = set(fail_on)
        self.calls: list[str] = []

    def load(self, ctx: Context) -> None:
        self.calls.append("load")

    def run(self, image: ImageRecord, ctx: Context) -> None:
        self.calls.append(image.id)
        if image.id in self.fail_on:
            raise ValueError(f"cannot process {image.id}")

    def unload(self) -> None:
        self.calls.append("unload")


@pytest.fixture
def ctx(config: Config, conn: sqlite3.Connection, add_image) -> Context:
    for image_id in ("a", "b", "c"):
        add_image(image_id)
    return Context(config=config, conn=conn)


def states(conn: sqlite3.Connection, stage: str) -> dict[str, tuple[str, str]]:
    rows = conn.execute(
        "SELECT image_id, state, version FROM stage_status WHERE stage = ?", (stage,)
    )
    return {r[0]: (r[1], r[2]) for r in rows}


def test_second_run_does_nothing(ctx: Context):
    stage = DummyStage()
    assert run_stage(stage, ctx) == {"done": 3, "failed": 0}
    assert stage.calls == ["load", "a", "b", "c", "unload"]
    assert states(ctx.conn, "dummy") == {k: ("done", "1") for k in "abc"}

    again = DummyStage()
    assert run_stage(again, ctx) == {"done": 0, "failed": 0}
    assert again.calls == []  # not even loaded


def test_version_bump_reruns(ctx: Context):
    run_stage(DummyStage(version="1"), ctx)
    stage = DummyStage(version="2")
    assert run_stage(stage, ctx) == {"done": 3, "failed": 0}
    assert states(ctx.conn, "dummy") == {k: ("done", "2") for k in "abc"}


def test_failing_image_is_recorded_and_others_succeed(ctx: Context):
    stage = DummyStage(fail_on={"b"})
    assert run_stage(stage, ctx) == {"done": 2, "failed": 1}
    assert stage.calls[-1] == "unload"

    row = ctx.conn.execute(
        "SELECT state, error FROM stage_status WHERE image_id = 'b' AND stage = 'dummy'"
    ).fetchone()
    assert row["state"] == "failed"
    assert row["error"] == "ValueError: cannot process b"
    assert states(ctx.conn, "dummy")["a"] == ("done", "1")


def test_failed_and_interrupted_images_are_retried(ctx: Context):
    run_stage(DummyStage(fail_on={"b"}), ctx)
    set_status(ctx.conn, "c", "dummy", "1", "running")  # as if a previous run crashed

    stage = DummyStage()
    assert run_stage(stage, ctx) == {"done": 2, "failed": 0}
    assert stage.calls == ["load", "b", "c", "unload"]


def test_unload_called_when_load_fails(ctx: Context):
    class BrokenLoad(DummyStage):
        def load(self, ctx: Context) -> None:
            raise RuntimeError("out of memory")

    stage = BrokenLoad()
    with pytest.raises(RuntimeError):
        run_stage(stage, ctx)
    assert stage.calls == ["unload"]


def test_requires_waits_for_upstream_stage(ctx: Context):
    run_stage(DummyStage(name="first", fail_on={"a"}), ctx)
    stage = DummyStage(name="second", requires=["first"])
    run_stage(stage, ctx)
    assert stage.calls == ["load", "b", "c", "unload"]
