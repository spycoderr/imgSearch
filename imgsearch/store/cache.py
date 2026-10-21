"""The pen drive as a least-recently-used cache of originals (plan D19, P1.6).

`evict` is the only code that removes files under originals_root. It removes an original
only if it has a verified S3 copy (re-checked with the store just before) and its ingest
is done, least recently used first, until the originals left are under the cap. The
`images` row is never touched; `original_cache.evicted_at` records the eviction.

`fetch` brings an evicted original back: download its recorded version, check the
encrypted hash, decrypt, check the plaintext hash equals the image id, then write it to
its original path (temp file, fsync, rename; never over an existing file).
"""

import os
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from imgsearch.config import Config
from imgsearch.interfaces import ObjectStore
from imgsearch.store.upload import download_original, now

GB = 1e9
_IMAGE_ID = re.compile(r"[0-9a-f]{64}")


@dataclass
class EvictResult:
    cap: int  # bytes
    before: int = 0  # bytes of originals on the drive before
    after: int = 0  # ... and after (what it would be, for a dry run)
    removed: list[tuple[str, int]] = field(default_factory=list)  # (orig_relpath, bytes)
    not_safe: int = 0  # originals over the cap but kept: no verified S3 copy or not ingested


@dataclass
class FetchResult:
    path: Path
    downloaded: bool  # False: it was already on the drive


def cached_originals(config: Config, conn: sqlite3.Connection) -> list[tuple[sqlite3.Row, int]]:
    """Originals on the drive with their file size, least recently used first.

    Each row has `id`, `orig_relpath` and `safe` (verified S3 copy and ingest done).
    """
    rows = conn.execute(
        "SELECT i.id, i.orig_relpath,"
        " (s.verified_at IS NOT NULL AND g.state = 'done') AS safe"
        " FROM images i"
        " LEFT JOIN original_cache c ON c.image_id = i.id"
        " LEFT JOIN s3_copies s ON s.image_id = i.id"
        " LEFT JOIN stage_status g ON g.image_id = i.id AND g.stage = 'ingest'"
        " WHERE c.evicted_at IS NULL"
        " ORDER BY COALESCE(c.last_access, i.created_at), i.id"
    ).fetchall()
    found = []
    for row in rows:
        path = config.originals_root / row["orig_relpath"]
        if path.is_file():
            found.append((row, path.stat().st_size))
    return found


def evict(
    config: Config,
    conn: sqlite3.Connection,
    store: ObjectStore | None,
    cap_gb: float,
    dry_run: bool = False,
) -> EvictResult:
    """Remove least recently used originals until the rest fit under `cap_gb`.

    A dry run lists what would go and needs no store (pass None).
    """
    cached = cached_originals(config, conn)
    result = EvictResult(cap=int(cap_gb * GB))
    result.before = result.after = sum(size for _, size in cached)
    for row, size in cached:
        if result.after <= result.cap:
            break
        if not row["safe"]:
            result.not_safe += 1
            continue
        if not dry_run:
            if store is None:
                raise ValueError("a real eviction needs the store, to re-check each S3 copy")
            _remove(config, conn, store, row["id"], row["orig_relpath"])
        result.after -= size
        result.removed.append((row["orig_relpath"], size))
    return result


def _remove(
    config: Config, conn: sqlite3.Connection, store: ObjectStore, image_id: str, relpath: str
) -> None:
    copy = conn.execute(
        "SELECT object_key, version_id, encrypted_sha256, size FROM s3_copies WHERE image_id = ?",
        (image_id,),
    ).fetchone()
    info = store.head(copy["object_key"], copy["version_id"])
    if info is None or info.size != copy["size"] or info.sha256 != copy["encrypted_sha256"]:
        raise RuntimeError(f"S3 copy of {relpath} is missing or differs; not evicting it")
    (config.originals_root / relpath).unlink()
    stamp = now()
    with conn:
        conn.execute(
            "INSERT INTO original_cache (image_id, last_access, evicted_at) VALUES (?, ?, ?)"
            " ON CONFLICT (image_id) DO UPDATE SET evicted_at = excluded.evicted_at",
            (image_id, stamp, stamp),
        )


def find_image(conn: sqlite3.Connection, ref: str) -> sqlite3.Row:
    """The `images` row for an image id or an orig_relpath."""
    column = "id" if _IMAGE_ID.fullmatch(ref) else "orig_relpath"
    row = conn.execute(f"SELECT id, orig_relpath FROM images WHERE {column} = ?", (ref,)).fetchone()
    if row is None:
        raise LookupError(f"no image with {column} {ref!r}")
    return row


def fetch(
    config: Config, conn: sqlite3.Connection, store: ObjectStore, key: bytes, ref: str
) -> FetchResult:
    """Return the original's local path, restoring it from S3 if it isn't on the drive."""
    row = find_image(conn, ref)
    path = config.originals_root / row["orig_relpath"]
    downloaded = not path.exists()
    if downloaded:
        data = download_original(conn, store, key, row["id"])
        mtime_ns = conn.execute(
            "SELECT orig_mtime_ns FROM s3_copies WHERE image_id = ?", (row["id"],)
        ).fetchone()[0]
        _write_new(path, data, mtime_ns, row["id"])
    with conn:
        conn.execute(
            "INSERT INTO original_cache (image_id, last_access, evicted_at) VALUES (?, ?, NULL)"
            " ON CONFLICT (image_id) DO UPDATE SET last_access = excluded.last_access,"
            " evicted_at = NULL",
            (row["id"], now()),
        )
    return FetchResult(path, downloaded)


def _write_new(dest: Path, data: bytes, mtime_ns: int, image_id: str) -> None:
    """Write `data` to the new file `dest` via a hidden temp file; never overwrite."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    temp = dest.with_name(f".{dest.name}.{image_id[:8]}.fetching")
    temp.unlink(missing_ok=True)  # left by an interrupted fetch; never an original
    try:
        with temp.open("xb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.utime(temp, ns=(mtime_ns, mtime_ns))
        if dest.exists():
            raise FileExistsError(f"{dest} appeared during the fetch")
        os.rename(temp, dest)
    finally:
        temp.unlink(missing_ok=True)


@dataclass
class CloudCounts:
    in_s3: int  # verified S3 copy
    not_uploaded: int
    evicted: int


def cloud_counts(conn: sqlite3.Connection) -> CloudCounts:
    total = conn.execute("SELECT COUNT(*) FROM images").fetchone()[0]
    in_s3 = conn.execute("SELECT COUNT(*) FROM s3_copies WHERE verified_at IS NOT NULL").fetchone()[
        0
    ]
    evicted = conn.execute(
        "SELECT COUNT(*) FROM original_cache WHERE evicted_at IS NOT NULL"
    ).fetchone()[0]
    return CloudCounts(in_s3=in_s3, not_uploaded=total - in_s3, evicted=evicted)
