import re
import sqlite3
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from imgsearch.config import DEFAULT_CONFIG_PATH, Config, load_config
from imgsearch.store.db import connect

# Every storage path points into the test's temp folder, never at the pen drive or data/.
TEST_PATHS = """\
originals_root: data/originals
derivatives_root: data/derivatives
thumbs_root: data/thumbs
db_path: data/imgsearch.db
inboxes: [data/inbox/phone]
"""
# The project config's storage block: from `originals_root:` to the last `inboxes` item.
STORAGE_BLOCK = re.compile(r"^originals_root:.*?^inboxes:[^\n]*\n(?:[ #][^\n]*\n)*", re.M | re.S)


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    """The project config.yaml with its storage paths moved into a temp folder."""
    text = DEFAULT_CONFIG_PATH.read_text(encoding="utf-8")
    text, n = STORAGE_BLOCK.subn(TEST_PATHS, text)
    assert n == 1, "config.yaml storage block not found"
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def config(config_path: Path) -> Config:
    return load_config(config_path)


@pytest.fixture
def conn(config: Config) -> Iterator[sqlite3.Connection]:
    conn = connect(config)
    yield conn
    conn.close()


@pytest.fixture
def add_image(conn: sqlite3.Connection) -> Callable[[str], None]:
    """Insert a minimal `images` row with the given id."""

    def add(image_id: str) -> None:
        with conn:
            conn.execute(
                "INSERT INTO images (id, orig_relpath, created_at) VALUES (?, ?, ?)",
                (image_id, f"{image_id}.jpg", "2026-10-20T00:00:00+00:00"),
            )

    return add
