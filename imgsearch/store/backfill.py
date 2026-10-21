"""One-off backfills for photos imported before P1.7: `file_mtime` and `source_relpath`.

Both only read the originals (`stat`), never change them.
"""

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from imgsearch.config import Config
from imgsearch.stages.filename import filename_words
from imgsearch.store.dates import local_iso
from imgsearch.store.text import save_filename_text

# Rows still missing the column, with whether the original was evicted to S3 (P1.6).
_MISSING_SQL = (
    "SELECT i.id, i.orig_relpath, c.evicted_at IS NOT NULL AS evicted FROM images i"
    " LEFT JOIN original_cache c ON c.image_id = i.id WHERE i.{column} IS NULL ORDER BY i.id"
)


@dataclass
class MtimeBackfill:
    updated: int = 0
    evicted: int = 0  # original not on the pen drive; skipped
    missing: list[str] = field(default_factory=list)  # relpaths not found; skipped


def backfill_file_mtime(config: Config, conn: sqlite3.Connection) -> MtimeBackfill:
    """Set `file_mtime` from the original's mtime for every row that lacks it. Commits."""
    result = MtimeBackfill()
    updates = []
    for row in conn.execute(_MISSING_SQL.format(column="file_mtime")).fetchall():
        if row["evicted"]:
            result.evicted += 1
            continue
        path = config.originals_root / row["orig_relpath"]
        if not path.is_file():
            result.missing.append(row["orig_relpath"])
            continue
        updates.append((local_iso(path.stat().st_mtime), row["id"]))
    with conn:
        conn.executemany("UPDATE images SET file_mtime = ? WHERE id = ?", updates)
    result.updated = len(updates)
    return result


@dataclass
class FolderBackfill:
    updated: list[tuple[str, str]] = field(default_factory=list)  # (orig_relpath, source)
    ambiguous: int = 0  # name and size match more than one synced file; skipped
    unmatched: int = 0  # no synced file has this name and size
    evicted: int = 0  # original not on the pen drive, so its size is unknown; skipped
    missing: list[str] = field(default_factory=list)  # relpaths not found; skipped


def backfill_source_folders(
    config: Config,
    conn: sqlite3.Connection,
    synced: Iterable[tuple[str, int]],
    dry_run: bool = False,
) -> FolderBackfill:
    """Set `source_relpath` for rows that lack it, by matching the original's file name and
    size against `synced`, the (inbox-relative path, size) of every file the sync tool
    knows. Only a single match is used. Also rewrites the image's `filename` text, so the
    folder words are searchable without re-running the stage. Commits unless `dry_run`."""
    by_key: dict[tuple[str, int], list[str]] = {}
    for relpath, size in synced:
        by_key.setdefault((PurePosixPath(relpath).name, size), []).append(relpath)

    result = FolderBackfill()
    updates = []
    for row in conn.execute(_MISSING_SQL.format(column="source_relpath")).fetchall():
        if row["evicted"]:
            result.evicted += 1
            continue
        path: Path = config.originals_root / row["orig_relpath"]
        if not path.is_file():
            result.missing.append(row["orig_relpath"])
            continue
        matches = by_key.get((path.name, path.stat().st_size), [])
        if len(matches) > 1:
            result.ambiguous += 1
        elif not matches:
            result.unmatched += 1
        else:
            updates.append((matches[0], row["id"]))
            result.updated.append((row["orig_relpath"], matches[0]))
    if not dry_run:
        with conn:
            conn.executemany("UPDATE images SET source_relpath = ? WHERE id = ?", updates)
        for source, image_id in updates:
            save_filename_text(conn, image_id, " ".join(filename_words(source)))
    return result
