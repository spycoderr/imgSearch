"""P1.5-A storage layout: the pen-drive check, search without the drive, verify, update."""

import os
import re
import time
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from typer.testing import CliRunner

from imgsearch.cli import app
from imgsearch.config import Config, EmbedderConfig, load_config
from imgsearch.stages.caption import CaptionStage
from imgsearch.stages.embed import EmbedStage
from imgsearch.stages.ingest import IngestStage
from imgsearch.stages.ocr import OcrStage
from imgsearch.stages.update import UPDATE_STAGES, update
from imgsearch.store.db import connect
from imgsearch.store.roots import DriveNotMounted, is_available, mount_point, require_mounted
from imgsearch.store.text import save_ocr
from imgsearch.store.upload import UploadResult

# A volume that is never mounted. Nothing is ever written there: the check runs first.
MISSING_DRIVE = Path("/Volumes/imgsearch-test-no-such-drive")


def run(config_path: Path, *args: str):
    return CliRunner().invoke(app, ["--config", str(config_path), *args])


def on_missing_drive(config_path: Path, *keys: str) -> Config:
    """Move the named roots onto MISSING_DRIVE in the test's config file."""
    text = config_path.read_text(encoding="utf-8")
    for key in keys:
        text = re.sub(rf"^{key}: .*$", f'{key}: "{MISSING_DRIVE}/{key}"', text, flags=re.M)
    config_path.write_text(text, encoding="utf-8")
    return load_config(config_path)


def photo(path: Path, color="red") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (40, 30), color).save(path, "JPEG")
    old = time.time() - 600
    os.utime(path, (old, old))
    return path


class FakeEmbedder:
    def __init__(self, config: EmbedderConfig) -> None:
        self.dim = config.dim

    def embed_images(self, paths: list[Path]) -> np.ndarray:
        return np.ones((len(paths), self.dim), dtype=np.float32)

    def embed_text(self, text: str) -> np.ndarray:
        raise AssertionError("not used by the stage")


class FakeOCR:
    def read(self, path: Path) -> str:
        return "text"


def never_called() -> UploadResult:
    raise AssertionError("the upload step must not run")


def no_db_copy(result: UploadResult) -> None:
    pass


def fake_update_stages(config: Config):
    return [
        IngestStage(config),
        EmbedStage(config, make_embedder=FakeEmbedder),
        OcrStage(config, engine=FakeOCR()),
    ]


# ---- the pen-drive check ----


def test_mount_point_is_the_volume_folder():
    assert mount_point(Path("/Volumes/PEN DRIVE/imgsearch/originals")) == Path("/Volumes/PEN DRIVE")
    assert mount_point(Path("/Users/me/data/thumbs")) is None
    assert mount_point(Path("/Volumes")) is None


def test_paths_on_the_mac_are_always_available(tmp_path: Path):
    assert is_available(tmp_path / "anything")


def test_unmounted_volume_fails_with_a_clear_message(config_path: Path):
    config = on_missing_drive(config_path, "originals_root")
    assert not is_available(config.originals_root)
    with pytest.raises(DriveNotMounted, match=f"pen drive not mounted: {MISSING_DRIVE}"):
        require_mounted(config, "derivatives_root", "originals_root")
    require_mounted(config, "derivatives_root", "thumbs_root")  # still on the Mac: fine


@pytest.mark.parametrize(
    ("command", "keys"),
    [
        (["import"], ["originals_root"]),
        (["update"], ["originals_root", "derivatives_root"]),
        (["update"], ["derivatives_root"]),
        (["ingest"], ["originals_root"]),
        (["run", "embed"], ["derivatives_root"]),
        (["verify"], ["originals_root"]),
        (["upload"], ["originals_root"]),
        (["evict", "--dry-run"], ["originals_root"]),
        (["fetch", "a.jpg"], ["originals_root"]),
    ],
)
def test_commands_that_need_the_drive_stop_first(config_path: Path, command, keys):
    on_missing_drive(config_path, *keys)
    result = run(config_path, *command)
    assert result.exit_code == 1
    assert f"pen drive not mounted: {MISSING_DRIVE}" in result.output
    assert not MISSING_DRIVE.exists()


def test_search_works_without_the_drive(config_path: Path, conn, add_image):
    config = on_missing_drive(config_path, "originals_root", "derivatives_root")
    add_image("sign")
    save_ocr(conn, "sign", "Welcome")

    result = run(config_path, "search", "welcome", "--signals", "text", "--no-rewrite")

    assert result.exit_code == 0, result.output
    assert "sign.jpg" in result.output
    assert "full-size images are unavailable" in result.output
    assert "pen drive: NOT MOUNTED" in run(config_path, "status").output
    assert not config.originals_root.exists()


def test_search_says_nothing_when_the_drive_is_there(config_path: Path, conn, add_image):
    add_image("sign")
    save_ocr(conn, "sign", "Welcome")
    result = run(config_path, "search", "welcome", "--signals", "text", "--no-rewrite")
    assert "unavailable" not in result.output
    assert "pen drive: mounted" in run(config_path, "status").output


# ---- update ----


def test_update_runs_import_ingest_embed_ocr_without_captions():
    assert [cls.name for cls in UPDATE_STAGES] == ["ingest", "embed", "ocr"]
    assert CaptionStage not in UPDATE_STAGES


def test_update_imports_then_indexes(config: Config, conn):
    photo(config.inboxes[0] / "a.jpg")
    photo(config.inboxes[0] / "b.jpg", color="blue")

    result = update(config, conn, fake_update_stages(config), UploadResult, no_db_copy)

    assert len(result.imported.imported) == 2
    assert result.stages == {
        "ingest": {"done": 2, "failed": 0},
        "embed": {"done": 2, "failed": 0},
        "ocr": {"done": 2, "failed": 0},
    }
    assert conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0] == 2
    assert (
        conn.execute("SELECT COUNT(*) FROM stage_status WHERE stage = 'caption'").fetchone()[0] == 0
    )

    again = update(config, conn, fake_update_stages(config), UploadResult, no_db_copy)
    assert again.imported.imported == []
    assert all(c == {"done": 0, "failed": 0} for c in again.stages.values())


def test_update_stops_before_anything_without_the_drive(config_path: Path):
    config = on_missing_drive(config_path, "derivatives_root")
    inbox_file = photo(config.inboxes[0] / "a.jpg")
    conn = connect(config)
    with pytest.raises(DriveNotMounted):
        update(config, conn, fake_update_stages(config), never_called, no_db_copy)
    assert inbox_file.exists()
    assert conn.execute("SELECT COUNT(*) FROM images").fetchone()[0] == 0
    conn.close()


def test_update_cli_reports_each_step(config_path: Path, config: Config):
    # Nothing new: the stages have no work, so no model is loaded.
    result = run(config_path, "update")
    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == [
        "import: 0 imported, 0 duplicates, 0 skipped (too new), 0 failed",
        # The test Keychain item never exists, so the upload step reports why it didn't run.
        "upload skipped: no encryption key in the Keychain (imgsearch-test-no-such-item);"
        " run `imgsearch s3-key create` once; 0 originals not yet in S3",
        "ingest: 0 processed (0 done, 0 failed)",
        "embed: 0 processed (0 done, 0 failed)",
        "ocr: 0 processed (0 done, 0 failed)",
    ]


# ---- verify ----


def test_verify_checks_originals_by_hash_and_files_exist(config_path: Path, config: Config, conn):
    photo(config.inboxes[0] / "a.jpg")
    photo(config.inboxes[0] / "b.jpg", color="blue")
    photo(config.inboxes[0] / "c.jpg", color="green")
    update(config, conn, fake_update_stages(config), UploadResult, no_db_copy)
    assert run(config_path, "verify").output.splitlines() == ["verify: 3 of 3 images ok"]

    by_name = {Path(r["orig_relpath"]).name: r for r in conn.execute("SELECT * FROM images")}
    (config.originals_root / by_name["a.jpg"]["orig_relpath"]).write_bytes(b"changed")
    (config.thumbs_root / by_name["b.jpg"]["thumb_path"]).unlink()
    (config.derivatives_root / by_name["c.jpg"]["derivative_path"]).write_bytes(b"\xff\xd8 cut")

    result = run(config_path, "verify")

    assert result.exit_code == 1
    assert "original changed (sha256 differs from the id)" in result.output
    assert "thumbnail missing" in result.output
    assert "derivative unreadable" in result.output
    assert result.output.splitlines()[-1] == "verify: 0 of 3 images ok"
