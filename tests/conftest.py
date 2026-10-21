import hashlib
import re
import sqlite3
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from imgsearch.config import DEFAULT_CONFIG_PATH, Config, load_config
from imgsearch.interfaces import ObjectInfo, StoreUnavailable
from imgsearch.store.db import connect

# Every storage path points into the test's temp folder, never at the pen drive or data/.
TEST_PATHS = """\
originals_root: data/originals
derivatives_root: data/derivatives
thumbs_root: data/thumbs
db_path: data/imgsearch.db
inboxes: [data/inbox/phone]
"""
# Tests never reach the real bucket or the real Keychain item: S3 is a fake (`store`),
# and no test creates a Keychain item with this name, so reading it finds nothing.
S3_TEST_VALUES = {
    "bucket": "test-bucket",
    "profile": "imgsearch-test-no-such-profile",
    "keychain_service": "imgsearch-test-no-such-item",
}
# The project config's storage block: from `originals_root:` to the last `inboxes` item.
STORAGE_BLOCK = re.compile(r"^originals_root:.*?^inboxes:[^\n]*\n(?:[ #][^\n]*\n)*", re.M | re.S)


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    """The project config.yaml with its storage paths moved into a temp folder."""
    text = DEFAULT_CONFIG_PATH.read_text(encoding="utf-8")
    text, n = STORAGE_BLOCK.subn(TEST_PATHS, text)
    assert n == 1, "config.yaml storage block not found"
    for key, value in S3_TEST_VALUES.items():
        text, n = re.subn(rf"^  {key}: .*$", f"  {key}: {value}", text, flags=re.M)
        assert n == 1, f"config.yaml s3.{key} not found"
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


class FakeObjectStore:
    """In-memory, versioned ObjectStore. Like S3, it rejects a put whose bytes don't match
    the SHA-256 sent with it, and it never deletes."""

    def __init__(self) -> None:
        self.versions: dict[str, list[tuple[str, bytes, dict[str, str]]]] = {}
        self.offline = False  # every call raises StoreUnavailable
        self.offline_after: int | None = None  # this many more calls succeed, then offline
        self.failing_keys: set[str] = set()  # puts to these keys fail
        self.puts = 0

    def put(
        self, key: str, data: bytes, sha256: str, metadata: dict[str, str] | None = None
    ) -> str:
        self._check()
        if key in self.failing_keys:
            raise OSError(f"test failure for {key}")
        if hashlib.sha256(data).hexdigest() != sha256:
            raise ValueError("checksum mismatch: upload rejected")
        self.puts += 1
        version = f"v{self.puts}"
        self.versions.setdefault(key, []).append((version, data, dict(metadata or {})))
        return version

    def get(self, key: str, version_id: str | None = None) -> bytes:
        self._check()
        found = self._find(key, version_id)
        if found is None:
            raise KeyError(key)
        return found[1]

    def head(self, key: str, version_id: str | None = None) -> ObjectInfo | None:
        self._check()
        found = self._find(key, version_id)
        if found is None:
            return None
        version, data, metadata = found
        return ObjectInfo(len(data), version, hashlib.sha256(data).hexdigest(), metadata)

    def replace_bytes(self, key: str, data: bytes) -> None:
        """Change the latest version's stored bytes in place (simulates corruption)."""
        version, _, metadata = self.versions[key][-1]
        self.versions[key][-1] = (version, data, metadata)

    def _find(self, key: str, version_id: str | None):
        versions = self.versions.get(key, [])
        if version_id is None:
            return versions[-1] if versions else None
        return next((v for v in versions if v[0] == version_id), None)

    def _check(self) -> None:
        if self.offline_after is not None:
            if self.offline_after == 0:
                self.offline = True
            self.offline_after -= 1
        if self.offline:
            raise StoreUnavailable("S3 unreachable: test is offline")


@pytest.fixture
def store() -> FakeObjectStore:
    return FakeObjectStore()
