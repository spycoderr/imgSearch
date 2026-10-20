from pathlib import Path

from typer.testing import CliRunner

from imgsearch.cli import app


def test_status_on_empty_db(config_path: Path, tmp_path: Path):
    result = CliRunner().invoke(app, ["--config", str(config_path), "status"])
    assert result.exit_code == 0, result.output
    assert "images: 0" in result.output
    for stage in ("ingest", "embed", "ocr", "caption"):
        assert stage in result.output
    assert (tmp_path / "data" / "imgsearch.db").exists()
