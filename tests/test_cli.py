from pathlib import Path

from typer.testing import CliRunner

from imgsearch.cli import app
from imgsearch.store.status import set_status


def test_status_on_empty_db(config_path: Path, tmp_path: Path):
    result = CliRunner().invoke(app, ["--config", str(config_path), "status"])
    assert result.exit_code == 0, result.output
    assert "images: 0" in result.output
    for stage in ("ingest", "embed", "ocr", "caption"):
        assert stage in result.output
    assert (tmp_path / "data" / "imgsearch.db").exists()


def test_status_shows_old_versions_as_outdated(config_path: Path, add_image, conn):
    for image_id in ("a", "b"):
        add_image(image_id)
        set_status(conn, image_id, "embed", "1", "done")
    add_image("c")
    set_status(conn, "c", "embed", "1", "failed", "boom")
    config_path.write_text(config_path.read_text().replace('embed: "1"', 'embed: "2"'))
    result = CliRunner().invoke(app, ["--config", str(config_path), "status"])
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    header = lines[2].replace("not run", "not_run").split()
    row = dict(zip(header, lines[4].split(), strict=True))
    assert row["done"] == "0"
    assert row["outdated"] == "2"
    assert row["failed"] == "1"
