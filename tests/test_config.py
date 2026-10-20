from pathlib import Path

import pydantic
import pytest

from imgsearch.config import DEFAULT_CONFIG_PATH, PROJECT_ROOT, load_config


def test_project_config_loads():
    config = load_config(DEFAULT_CONFIG_PATH)
    assert config.models.embedder.dim == 768
    assert config.stages == {"ingest": "1", "embed": "1", "ocr": "1", "caption": "1"}
    assert config.search.top_k == 5


def test_relative_paths_resolve_against_config_folder(config_path: Path, tmp_path: Path):
    config = load_config(config_path)
    assert config.originals_root == tmp_path / "data" / "originals"
    assert config.derivatives_root == tmp_path / "data" / "derivatives"
    assert config.db_path == tmp_path / "data" / "imgsearch.db"


def test_absolute_paths_are_kept(config_path: Path, tmp_path: Path):
    elsewhere = tmp_path / "elsewhere"
    text = config_path.read_text().replace(
        "originals_root: data/originals", f"originals_root: {elsewhere}"
    )
    config_path.write_text(text)
    assert load_config(config_path).originals_root == elsewhere


def test_unknown_key_is_an_error(config_path: Path):
    config_path.write_text(config_path.read_text() + "\nunexpected: 1\n")
    with pytest.raises(pydantic.ValidationError):
        load_config(config_path)


def test_default_config_ignores_current_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    config = load_config()
    assert config.originals_root == PROJECT_ROOT / "data" / "originals"
