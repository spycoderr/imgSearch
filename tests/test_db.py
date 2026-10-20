import sqlite3

from imgsearch.config import Config
from imgsearch.store.db import MIGRATIONS_DIR, connect, migrate

EXPECTED_TABLES = {"images", "stage_status", "embeddings", "ocr", "captions", "text_fts"}


def latest_migration() -> int:
    return max(int(p.name.split("_")[0]) for p in MIGRATIONS_DIR.glob("*.sql"))


def test_connect_creates_schema(conn: sqlite3.Connection):
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master")}
    assert EXPECTED_TABLES <= tables
    assert conn.execute("PRAGMA user_version").fetchone()[0] == latest_migration()


def test_migrations_are_idempotent(config: Config):
    connect(config).close()
    conn = connect(config)  # second open must not re-apply anything
    migrate(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == latest_migration()
    conn.close()


def test_sqlite_vec_and_foreign_keys(conn: sqlite3.Connection):
    assert conn.execute("SELECT vec_version()").fetchone()[0].startswith("v")
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
