from pathlib import Path

import pydantic
import pytest

from imgsearch.config import DEFAULT_CONFIG_PATH, PROJECT_ROOT, load_config


@pytest.fixture
def env_file(tmp_path: Path) -> Path:
    """A stand-in for the project's git-ignored .env, so tests don't depend on this machine."""
    path = tmp_path / ".env"
    path.write_text(
        "IMGSEARCH_STORAGE_ROOT=/Volumes/PEN DRIVE/imgsearch\n"
        "IMGSEARCH_S3_BUCKET=example-bucket\nAWS_PROFILE=example-profile\n"
    )
    return path


def test_project_config_loads(env_file: Path):
    config = load_config(DEFAULT_CONFIG_PATH, env_file)
    assert config.models.embedder.dim == 768
    # Names only: versions are bumped to re-run stages.
    assert set(config.stages) == {"ingest", "embed", "ocr", "caption"}
    assert config.search.top_k == 5


def test_project_storage_layout(env_file: Path):
    # Plan D18: originals and derivatives on the pen drive; DB, thumbnails, inbox on the Mac.
    config = load_config(DEFAULT_CONFIG_PATH, env_file)
    pen = Path("/Volumes/PEN DRIVE/imgsearch")
    assert config.originals_root == pen / "originals"
    assert config.derivatives_root == pen / "derivatives"
    assert config.thumbs_root == PROJECT_ROOT / "data" / "thumbs"
    assert config.db_path == PROJECT_ROOT / "data" / "imgsearch.db"
    inbox = PROJECT_ROOT / "data" / "inbox"
    assert config.inboxes == [inbox / "phone", inbox / "camera"]


def test_project_config_takes_the_drive_from_a_variable():
    text = DEFAULT_CONFIG_PATH.read_text()
    assert 'originals_root: "${IMGSEARCH_STORAGE_ROOT}/originals"' in text
    assert 'derivatives_root: "${IMGSEARCH_STORAGE_ROOT}/derivatives"' in text


def use_variable(config_path: Path) -> None:
    text = config_path.read_text().replace(
        "originals_root: data/originals", 'originals_root: "${IMGSEARCH_TEST_ROOT}/originals"'
    )
    config_path.write_text(text)


def test_variable_comes_from_the_env_file(config_path: Path, tmp_path: Path):
    use_variable(config_path)
    env_file = tmp_path / "test.env"
    env_file.write_text("IMGSEARCH_TEST_ROOT=/Volumes/A B/x\n")
    assert load_config(config_path, env_file).originals_root == Path("/Volumes/A B/x/originals")


def test_environment_wins_over_the_env_file(
    config_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    use_variable(config_path)
    env_file = tmp_path / "test.env"
    env_file.write_text("IMGSEARCH_TEST_ROOT=/from/file\n")
    monkeypatch.setenv("IMGSEARCH_TEST_ROOT", "/from/env")
    assert load_config(config_path, env_file).originals_root == Path("/from/env/originals")


def test_unset_variable_fails_naming_it(config_path: Path, tmp_path: Path):
    use_variable(config_path)
    with pytest.raises(ValueError, match="IMGSEARCH_TEST_ROOT is not set"):
        load_config(config_path, tmp_path / "no-such.env")


def test_relative_paths_resolve_against_config_folder(config_path: Path, tmp_path: Path):
    config = load_config(config_path)
    assert config.originals_root == tmp_path / "data" / "originals"
    assert config.derivatives_root == tmp_path / "data" / "derivatives"
    assert config.thumbs_root == tmp_path / "data" / "thumbs"
    assert config.db_path == tmp_path / "data" / "imgsearch.db"
    assert config.inboxes == [tmp_path / "data" / "inbox" / "phone"]


@pytest.mark.parametrize("db_path", ["/Volumes/PEN DRIVE/imgsearch.db", "/Volumes/X/a/b.db"])
def test_db_on_a_removable_drive_is_rejected(config_path: Path, db_path: str):
    text = config_path.read_text().replace("db_path: data/imgsearch.db", f'db_path: "{db_path}"')
    config_path.write_text(text)
    with pytest.raises(pydantic.ValidationError, match="db_path must be on the Mac"):
        load_config(config_path)


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


@pytest.mark.parametrize("key", ["db_path", "search"])
def test_missing_key_is_an_error(config_path: Path, key: str):
    lines = config_path.read_text().splitlines()
    config_path.write_text("\n".join(line for line in lines if not line.startswith(key)))
    with pytest.raises(pydantic.ValidationError, match=key):
        load_config(config_path)


def test_default_config_ignores_current_directory(
    tmp_path: Path, env_file: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.chdir(tmp_path)
    config = load_config(env_file=env_file)
    assert config.thumbs_root == PROJECT_ROOT / "data" / "thumbs"
    assert config.db_path == PROJECT_ROOT / "data" / "imgsearch.db"


def test_home_is_expanded(config_path: Path):
    text = config_path.read_text().replace(
        "originals_root: data/originals", "originals_root: ~/photos"
    )
    config_path.write_text(text)
    assert load_config(config_path).originals_root == Path.home() / "photos"
