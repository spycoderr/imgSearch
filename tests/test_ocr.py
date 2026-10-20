import sqlite3
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from typer.testing import CliRunner

from imgsearch.cli import app
from imgsearch.config import Config
from imgsearch.interfaces import Context
from imgsearch.models.ocr import AppleVisionOCR
from imgsearch.stages.ocr import OcrStage
from imgsearch.stages.runner import run_stage
from imgsearch.store.status import set_status


class FakeOCR:
    """Returns canned text per file name and records every path it was asked to read."""

    def __init__(self, texts: dict[str, str]) -> None:
        self.texts = texts
        self.paths: list[Path] = []

    def read(self, path: Path) -> str:
        self.paths.append(path)
        if path.name not in self.texts:
            raise OSError(f"cannot read {path.name}")
        return self.texts[path.name]


def text_image(path: Path, lines: list[str]) -> Path:
    """Write a white JPEG with each line drawn in large black text, top to bottom."""
    image = Image.new("RGB", (900, 120 + 160 * len(lines)), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=56)
    for i, line in enumerate(lines):
        draw.text((40, 60 + 160 * i), line, fill="black", font=font)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "JPEG", quality=95)
    return path


def add_ingested(conn: sqlite3.Connection, add_image, image_id: str) -> None:
    """An image row that ingest has finished, with a derivative path."""
    add_image(image_id)
    with conn:
        conn.execute(
            "UPDATE images SET derivative_path = ? WHERE id = ?",
            (f"derivatives/{image_id[:2]}/{image_id}.jpg", image_id),
        )
    set_status(conn, image_id, "ingest", "2", "done")


def ocr_rows(conn: sqlite3.Connection) -> dict[str, str]:
    return dict(conn.execute("SELECT image_id, text FROM ocr").fetchall())


def states(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute("SELECT image_id, state FROM stage_status WHERE stage = 'ocr'")
    return dict(rows.fetchall())


# ---- AppleVisionOCR (calls the macOS Vision framework; no model download) ----


def test_vision_joins_lines_in_reading_order(config: Config, tmp_path: Path):
    path = text_image(tmp_path / "menu.jpg", ["Paneer Tikka", "Masala Dosa"])
    assert AppleVisionOCR(config.models.ocr).read(path) == "Paneer Tikka\nMasala Dosa"


def test_vision_blank_image_gives_empty_string(config: Config, tmp_path: Path):
    path = text_image(tmp_path / "blank.jpg", [])
    assert AppleVisionOCR(config.models.ocr).read(path) == ""


def test_ocr_config_is_english_accurate(config: Config):
    assert config.models.ocr.recognition_level == "accurate"
    assert config.models.ocr.languages == ["en-US"]


# ---- OcrStage with a fake engine ----


def test_stage_reads_derivative_and_saves_text(config: Config, conn, add_image):
    add_ingested(conn, add_image, "aa1")
    add_ingested(conn, add_image, "bb2")
    engine = FakeOCR({"aa1.jpg": "CHAI 20", "bb2.jpg": ""})
    stage = OcrStage(config, engine)

    counts = run_stage(stage, Context(config=config, conn=conn))

    assert counts == {"done": 2, "failed": 0}
    assert engine.paths == [
        config.derivatives_root / "derivatives/aa/aa1.jpg",
        config.derivatives_root / "derivatives/bb/bb2.jpg",
    ]
    assert all(not p.is_relative_to(config.originals_root) for p in engine.paths)
    assert ocr_rows(conn) == {"aa1": "CHAI 20", "bb2": ""}  # no text is "", not an error
    assert states(conn) == {"aa1": "done", "bb2": "done"}
    fts = conn.execute("SELECT image_id, kind, text FROM text_fts ORDER BY image_id").fetchall()
    assert [tuple(r) for r in fts] == [("aa1", "ocr", "CHAI 20"), ("bb2", "ocr", "")]


def test_stage_second_run_does_nothing_until_version_changes(config: Config, conn, add_image):
    add_ingested(conn, add_image, "aa1")
    engine = FakeOCR({"aa1.jpg": "menu"})
    run_stage(OcrStage(config, engine), Context(config=config, conn=conn))

    assert run_stage(OcrStage(config, engine), Context(config=config, conn=conn)) == {
        "done": 0,
        "failed": 0,
    }
    assert len(engine.paths) == 1

    bumped = config.model_copy(update={"stages": {**config.stages, "ocr": "2"}})
    counts = run_stage(OcrStage(bumped, engine), Context(config=bumped, conn=conn))
    assert counts == {"done": 1, "failed": 0}


def test_stage_waits_for_ingest_and_records_failures(config: Config, conn, add_image):
    add_image("cc3")  # not ingested yet: skipped
    add_ingested(conn, add_image, "dd4")  # the engine can't read it: failed
    stage = OcrStage(config, FakeOCR({}))

    counts = run_stage(stage, Context(config=config, conn=conn))

    assert counts == {"done": 0, "failed": 1}
    assert states(conn) == {"dd4": "failed"}
    error = conn.execute(
        "SELECT error FROM stage_status WHERE image_id = 'dd4' AND stage = 'ocr'"
    ).fetchone()[0]
    assert error == "OSError: cannot read dd4.jpg"
    assert stage._engine is None  # unloaded


def test_stage_declares_requires_and_version(config: Config):
    stage = OcrStage(config)
    assert stage.name == "ocr"
    assert stage.requires == ["ingest"]
    assert stage.version == config.stages["ocr"]


# ---- `imgsearch run` ----


def test_cli_run_ocr_end_to_end(config_path: Path, config: Config, conn, add_image):
    add_ingested(conn, add_image, "aa1")
    add_ingested(conn, add_image, "bb2")
    text_image(config.derivatives_root / "derivatives/aa/aa1.jpg", ["Filter Coffee"])
    text_image(config.derivatives_root / "derivatives/bb/bb2.jpg", [])

    result = CliRunner().invoke(app, ["--config", str(config_path), "run", "ocr"])
    assert result.exit_code == 0, result.output
    assert "ocr: 2 processed (2 done, 0 failed)" in result.output
    assert ocr_rows(conn) == {"aa1": "Filter Coffee", "bb2": ""}

    again = CliRunner().invoke(app, ["--config", str(config_path), "run", "ocr"])
    assert "ocr: 0 processed (0 done, 0 failed)" in again.output


def test_cli_run_reports_failures(config_path: Path, conn, add_image):
    add_ingested(conn, add_image, "aa1")  # derivative file doesn't exist

    result = CliRunner().invoke(app, ["--config", str(config_path), "run", "ocr"])
    assert result.exit_code == 1
    assert "ocr: 1 processed (0 done, 1 failed)" in result.output
    assert "failed: aa1.jpg: FileNotFoundError" in result.output


def test_cli_run_unknown_stage(config_path: Path):
    result = CliRunner().invoke(app, ["--config", str(config_path), "run", "nope"])
    assert result.exit_code == 2
    assert "unknown stage 'nope'" in result.output
