"""Open the SQLite database and apply schema migrations."""

import sqlite3
from pathlib import Path

import sqlite_vec

from imgsearch.config import Config

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def connect(config: Config) -> sqlite3.Connection:
    """Open the DB (creating it if needed), load sqlite-vec and apply pending migrations."""
    config.db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.db_path)
    conn.row_factory = sqlite3.Row
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.execute("PRAGMA foreign_keys = ON")
    migrate(conn)
    return conn


def migrate(conn: sqlite3.Connection, migrations_dir: Path = MIGRATIONS_DIR) -> None:
    """Apply each NNN_*.sql in `migrations_dir` newer than PRAGMA user_version, in order."""
    migrations: dict[int, Path] = {}
    for path in sorted(migrations_dir.glob("*.sql")):
        number = int(path.name.split("_")[0])
        if number in migrations:
            raise ValueError(
                f"duplicate migration number {number}: {migrations[number].name}, {path.name}"
            )
        migrations[number] = path

    current = conn.execute("PRAGMA user_version").fetchone()[0]
    for number, path in sorted(migrations.items()):
        if number <= current:
            continue
        sql = path.read_text(encoding="utf-8")
        # One transaction per migration, so a failed migration leaves no partial schema.
        conn.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {number};\nCOMMIT;")
