import sqlite3
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from imgsearch.config import DEFAULT_CONFIG_PATH, Config, load_config
from imgsearch.store.db import connect


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    """A copy of the project config.yaml in a temp folder, so all paths land there."""
    path = tmp_path / "config.yaml"
    path.write_text(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"), encoding="utf-8")
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
