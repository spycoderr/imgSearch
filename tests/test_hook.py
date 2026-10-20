"""The originals-protection hook expands ${IMGSEARCH_STORAGE_ROOT} like load_config does."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from imgsearch.config import PROJECT_ROOT

HOOK = PROJECT_ROOT / ".claude" / "hooks" / "protect_originals.py"
PEN = "/Volumes/PEN DRIVE/imgsearch"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A project folder whose config.yaml takes originals_root from the variable."""
    (tmp_path / "config.yaml").write_text(
        'originals_root: "${IMGSEARCH_STORAGE_ROOT}/originals"  # comment\n'
    )
    return tmp_path


def hook(project: Path, tool: str, tool_input: dict, **env: str) -> subprocess.CompletedProcess:
    child_env = {k: v for k, v in os.environ.items() if k != "IMGSEARCH_STORAGE_ROOT"}
    child_env.update(CLAUDE_PROJECT_DIR=str(project), **env)
    payload = {"tool_name": tool, "tool_input": tool_input, "cwd": str(project)}
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=child_env,
    )


def write(path: str) -> tuple[str, dict]:
    return "Write", {"file_path": path}


def test_expanded_pen_drive_path_is_blocked(project: Path):
    (project / ".env").write_text(f"# storage\nIMGSEARCH_STORAGE_ROOT={PEN}\n")
    result = hook(project, *write(f"{PEN}/originals/2019/04/a.jpg"))
    assert result.returncode == 2
    assert f"({PEN}/originals) is read-only" in result.stderr
    assert hook(project, *write(f"{PEN}/derivatives/ab/a.jpg")).returncode == 0


def test_bash_copy_onto_the_expanded_path_is_blocked(project: Path):
    (project / ".env").write_text(f"IMGSEARCH_STORAGE_ROOT={PEN}\n")
    result = hook(project, "Bash", {"command": f'cp a.jpg "{PEN}/originals/a.jpg"'})
    assert result.returncode == 2


def test_environment_wins_over_env_file(project: Path):
    (project / ".env").write_text("IMGSEARCH_STORAGE_ROOT=/Volumes/OTHER/imgsearch\n")
    result = hook(project, *write(f"{PEN}/originals/a.jpg"), IMGSEARCH_STORAGE_ROOT=PEN)
    assert result.returncode == 2


def test_unset_variable_protects_unexpanded_and_default_paths_and_says_why(project: Path):
    default = hook(project, *write(str(project / "data/originals/a.jpg")))
    assert default.returncode == 2
    assert "IMGSEARCH_STORAGE_ROOT is not set" in default.stderr
    literal = hook(project, *write(str(project / "${IMGSEARCH_STORAGE_ROOT}/originals/a.jpg")))
    assert literal.returncode == 2
    assert hook(project, *write(str(project / "data/thumbs/a.jpg"))).returncode == 0
