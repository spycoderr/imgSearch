import sqlite3
from collections.abc import Collection
from pathlib import Path

import pytest
from PIL import Image

from imgsearch.config import CaptionerConfig, Config, load_config
from imgsearch.interfaces import Context
from imgsearch.models.captioner import clean_caption, fit_to_area
from imgsearch.stages.caption import CaptionStage
from imgsearch.stages.runner import run_stage
from imgsearch.store.status import set_status

# ---- fit_to_area ----


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        ((6203, 507), (3581, 292)),  # the real panorama keeps a readable height
        ((2364, 1330), (1365, 768)),
        ((1024, 1024), (1024, 1024)),  # exactly at the cap
        ((300, 100), (300, 100)),  # never upscaled
    ],
)
def test_fit_to_area(size: tuple[int, int], expected: tuple[int, int]):
    resized = fit_to_area(Image.new("RGB", size), 1024 * 1024)
    assert resized.size == expected
    assert resized.width * resized.height <= 1024 * 1024


# ---- clean_caption ----


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("A dog on a beach.<|im_end|>", "A dog on a beach."),
        ("<|im_start|>A dog on a beach.<|endoftext|>\n", "A dog on a beach."),
        ("Caption: A dog on a beach.", "A dog on a beach."),
        ("  caption:A menu.\nText: Paneer Tikka 250", "A menu.\nText: Paneer Tikka 250"),
        # Only a leading label is removed; the rest of the text is kept as written.
        ("A sign that reads 'Caption: here' | 5", "A sign that reads 'Caption: here' | 5"),
    ],
)
def test_clean_caption(raw: str, expected: str):
    assert clean_caption(raw) == expected


# ---- CaptionStage ----


class FakeCaptioner:
    """Returns a caption naming the file; raises for files listed in `fail_on`."""

    def __init__(self, config: CaptionerConfig, fail_on: Collection[str] = ()) -> None:
        self.config = config
        self.fail_on = set(fail_on)
        self.paths: list[Path] = []
        self.unloaded = False

    def caption(self, path: Path) -> str:
        self.paths.append(path)
        if path.stem in self.fail_on:
            raise ValueError(f"cannot caption {path.stem}")
        Image.open(path).verify()  # the stage must hand over a real image file
        return f"a photo of {path.stem} with a red umbrella"

    def unload(self) -> None:
        self.unloaded = True


class Loader:
    """Builds FakeCaptioners and remembers them, as the stage's `load_captioner`."""

    def __init__(self, fail_on: Collection[str] = ()) -> None:
        self.fail_on = fail_on
        self.built: list[FakeCaptioner] = []

    def __call__(self, config: CaptionerConfig) -> FakeCaptioner:
        captioner = FakeCaptioner(config, self.fail_on)
        self.built.append(captioner)
        return captioner


@pytest.fixture
def ctx(config: Config, conn: sqlite3.Connection, add_image) -> Context:
    """Three images with derivatives on disk and ingest done."""
    for image_id in ("a", "b", "c"):
        add_image(image_id)
        relpath = f"derivatives/{image_id}.jpg"
        path = config.derivatives_root / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (64, 48), "white").save(path)
        with conn:
            conn.execute("UPDATE images SET derivative_path = ? WHERE id = ?", (relpath, image_id))
        set_status(conn, image_id, "ingest", config.stages["ingest"], "done")
    return Context(config=config, conn=conn)


def captions(conn: sqlite3.Connection) -> dict[str, tuple[str, str]]:
    rows = conn.execute("SELECT image_id, text, model FROM captions")
    return {r[0]: (r[1], r[2]) for r in rows}


def test_stage_reads_config(config: Config):
    stage = CaptionStage(config)
    assert stage.name == "caption"
    assert stage.requires == ["ingest"]
    assert stage.version == config.stages["caption"]


def test_captions_saved_with_model_name_and_searchable(ctx: Context):
    loader = Loader()
    assert run_stage(CaptionStage(ctx.config, loader), ctx) == {"done": 3, "failed": 0}

    model = ctx.config.models.captioner.name
    assert captions(ctx.conn) == {k: (f"a photo of {k} with a red umbrella", model) for k in "abc"}
    (captioner,) = loader.built
    assert captioner.config == ctx.config.models.captioner
    assert captioner.paths == [ctx.config.derivatives_root / f"derivatives/{k}.jpg" for k in "abc"]
    assert captioner.unloaded

    rows = ctx.conn.execute(
        "SELECT image_id, kind FROM text_fts WHERE text_fts MATCH 'umbrella' ORDER BY image_id"
    )
    assert [tuple(r) for r in rows] == [("a", "caption"), ("b", "caption"), ("c", "caption")]


def test_second_run_does_nothing(ctx: Context):
    run_stage(CaptionStage(ctx.config, Loader()), ctx)
    loader = Loader()
    assert run_stage(CaptionStage(ctx.config, loader), ctx) == {"done": 0, "failed": 0}
    assert loader.built == []  # the model is not even loaded


def test_waits_for_ingest(ctx: Context, add_image):
    add_image("d")  # registered, but not ingested yet
    loader = Loader()
    run_stage(CaptionStage(ctx.config, loader), ctx)
    assert [p.stem for p in loader.built[0].paths] == ["a", "b", "c"]
    assert "d" not in captions(ctx.conn)


def test_failure_is_recorded_and_model_unloaded(ctx: Context):
    loader = Loader(fail_on={"b"})
    assert run_stage(CaptionStage(ctx.config, loader), ctx) == {"done": 2, "failed": 1}
    assert loader.built[0].unloaded
    assert set(captions(ctx.conn)) == {"a", "c"}
    error = ctx.conn.execute(
        "SELECT error FROM stage_status WHERE image_id = 'b' AND stage = 'caption'"
    ).fetchone()[0]
    assert error == "ValueError: cannot caption b"


def test_version_bump_recaptions(ctx: Context, config_path: Path):
    run_stage(CaptionStage(ctx.config, Loader()), ctx)
    config_path.write_text(config_path.read_text().replace('caption: "1"', 'caption: "2"'))
    bumped = load_config(config_path)
    loader = Loader()
    stage = CaptionStage(bumped, loader)
    assert run_stage(stage, Context(config=bumped, conn=ctx.conn)) == {"done": 3, "failed": 0}


def test_unload_without_load_is_safe(config: Config):
    CaptionStage(config, Loader()).unload()
