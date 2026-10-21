"""Upload originals and a DB copy to the object store, encrypted (plan D19, P1.6).

An original is uploaded as `<originals_prefix><image id>`, so S3 never sees file names or
dates. It counts as safely in S3 (`s3_copies.verified_at`) only after the store reports
the same size and SHA-256 for the exact version just written. Uploading is idempotent:
originals with a verified copy are skipped.

The DB copy is taken with SQLite's online backup API, encrypted the same way, and put at
`s3.db_key`; bucket versioning keeps the older copies. It is skipped when the DB hasn't
changed since the last copy. `update` uploads the originals early and the DB copy last, so
the copy includes that run's index (review R7).
"""

import hashlib
import random
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from imgsearch.config import Config
from imgsearch.interfaces import ObjectStore, StoreUnavailable
from imgsearch.store.crypto import decrypt, encrypt

DB_HASH_META = "plain-sha256"  # metadata on the DB copy: SHA-256 of the unencrypted DB


@dataclass
class UploadResult:
    uploaded: list[str] = field(default_factory=list)  # orig_relpath of each original uploaded
    uploaded_bytes: int = 0  # encrypted bytes sent for those originals
    failed: list[tuple[str, str]] = field(default_factory=list)  # (orig_relpath, error)
    db: str | None = None  # what happened to the DB copy, e.g. "unchanged"
    db_error: str | None = None  # why the DB copy failed; None if it didn't
    error: str | None = None  # why the run stopped early (offline, no key); None if it didn't


@dataclass
class RestoreCheck:
    sampled: int = 0  # originals picked for the check
    checked: list[str] = field(default_factory=list)  # orig_relpath of originals that matched
    failed: list[tuple[str, str]] = field(default_factory=list)  # (orig_relpath, error)
    db: str = ""  # summary of the DB copy check
    error: str | None = None  # why the check stopped early (offline); None if it didn't


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def object_key(config: Config, image_id: str) -> str:
    return f"{config.s3.originals_prefix}{image_id}"


def not_in_s3(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Images without a verified S3 copy, by path."""
    return conn.execute(
        "SELECT i.id, i.orig_relpath FROM images i"
        " LEFT JOIN s3_copies s ON s.image_id = i.id"
        " WHERE s.verified_at IS NULL ORDER BY i.orig_relpath"
    ).fetchall()


def upload(
    config: Config,
    conn: sqlite3.Connection,
    store: ObjectStore,
    key: bytes,
    progress: Callable[[int], None] | None = None,
) -> UploadResult:
    """Upload every original without a verified copy, then the DB copy."""
    result = upload_originals(config, conn, store, key, progress)
    if result.error is None:
        upload_db_copy(config, conn, store, key, result)
    return result


def upload_originals(
    config: Config,
    conn: sqlite3.Connection,
    store: ObjectStore,
    key: bytes,
    progress: Callable[[int], None] | None = None,
) -> UploadResult:
    """Upload every original without a verified copy.

    `progress` is called with the number of originals tried so far, after each one.

    One failed original is reported and doesn't stop the others. If the store becomes
    unreachable, the run stops there with `error` set; what was verified before stays.
    """
    result = UploadResult()
    try:
        for row in not_in_s3(conn):
            try:
                result.uploaded_bytes += upload_original(config, conn, store, key, row["id"])
            except StoreUnavailable:
                raise
            except Exception as e:  # one bad original must not stop the others (plan P1.6)
                result.failed.append((row["orig_relpath"], f"{type(e).__name__}: {e}"))
            else:
                result.uploaded.append(row["orig_relpath"])
            if progress is not None:
                progress(len(result.uploaded) + len(result.failed))
    except StoreUnavailable as e:
        result.error = str(e)
    return result


def upload_db_copy(
    config: Config, conn: sqlite3.Connection, store: ObjectStore, key: bytes, result: UploadResult
) -> None:
    """Upload the DB copy, recording the outcome in `result.db` or `result.db_error`."""
    try:
        result.db = upload_db(conn, store, key, config.s3.db_key)
    except Exception as e:  # e.g. expired credentials: reported, and indexing goes on (R1)
        result.db_error = f"{type(e).__name__}: {e}"


def upload_original(
    config: Config, conn: sqlite3.Connection, store: ObjectStore, key: bytes, image_id: str
) -> int:
    """Encrypt, upload and verify one original; record it in s3_copies. Returns its size."""
    row = conn.execute("SELECT orig_relpath FROM images WHERE id = ?", (image_id,)).fetchone()
    relpath = row[0]
    path = config.originals_root / relpath
    data = path.read_bytes()
    if sha256_hex(data) != image_id:
        raise ValueError(f"original changed (sha256 differs from the id), not uploaded: {path}")
    blob = encrypt(key, data)
    blob_sha = sha256_hex(blob)
    name = object_key(config, image_id)
    uploaded_at = now()
    version = store.put(name, blob, blob_sha)
    _check_stored(store, name, version, len(blob), blob_sha)
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO s3_copies (image_id, object_key, version_id,"
            " encrypted_sha256, size, orig_mtime_ns, uploaded_at, verified_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                image_id,
                name,
                version,
                blob_sha,
                len(blob),
                path.stat().st_mtime_ns,
                uploaded_at,
                now(),
            ),  # fmt: skip
        )
    return len(blob)


def db_snapshot(conn: sqlite3.Connection) -> bytes:
    """A consistent copy of the DB's bytes, via the online backup API (not a file copy)."""
    with sqlite3.connect(":memory:") as copy:
        conn.backup(copy)
        data = copy.serialize()
    copy.close()
    return data


def upload_db(conn: sqlite3.Connection, store: ObjectStore, key: bytes, name: str) -> str:
    """Upload an encrypted DB copy unless the latest copy has the same content."""
    data = db_snapshot(conn)
    plain_sha = sha256_hex(data)
    latest = store.head(name)
    if latest is not None and latest.metadata.get(DB_HASH_META) == plain_sha:
        return "unchanged since the last copy"
    blob = encrypt(key, data)
    blob_sha = sha256_hex(blob)
    version = store.put(name, blob, blob_sha, {DB_HASH_META: plain_sha})
    _check_stored(store, name, version, len(blob), blob_sha)
    return f"uploaded ({len(blob) / 1e6:.1f} MB)"


def _check_stored(store: ObjectStore, name: str, version: str, size: int, sha: str) -> None:
    info = store.head(name, version)
    if info is None or info.size != size or info.sha256 != sha:
        raise ValueError(f"S3 copy of {name} (version {version}) doesn't match what was sent")


def restore_check(
    config: Config, conn: sqlite3.Connection, store: ObjectStore, key: bytes, sample: int
) -> RestoreCheck:
    """Download, decrypt and hash-check `sample` random originals and the latest DB copy.

    Nothing is written to disk. A missing or unreadable DB copy is a failure like a bad
    original. If the store becomes unreachable, the check stops there with `error` set.
    """
    result = RestoreCheck()
    rows = conn.execute(
        "SELECT i.id, i.orig_relpath FROM s3_copies s JOIN images i ON i.id = s.image_id"
        " WHERE s.verified_at IS NOT NULL"
    ).fetchall()
    picked = random.sample(rows, min(sample, len(rows)))
    result.sampled = len(picked)
    try:
        for row in picked:
            try:
                download_original(conn, store, key, row["id"])
            except StoreUnavailable:
                raise
            except Exception as e:  # report every bad copy, not just the first
                result.failed.append((row["orig_relpath"], f"{type(e).__name__}: {e}"))
            else:
                result.checked.append(row["orig_relpath"])
        try:
            result.db, ok = _check_db_copy(config, store, key)
        except StoreUnavailable:
            raise
        except Exception as e:  # missing or undecryptable: reported with the rest (R6)
            result.db = f"FAILED: {type(e).__name__}: {e}"
            result.failed.append((config.s3.db_key, f"{type(e).__name__}: {e}"))
        else:
            if not ok:
                result.failed.append((config.s3.db_key, result.db))
    except StoreUnavailable as e:
        result.error = str(e)
    return result


def _check_db_copy(config: Config, store: ObjectStore, key: bytes) -> tuple[str, bool]:
    """Download, decrypt and check the latest DB copy: (summary, integrity_check is ok)."""
    data = decrypt(key, store.get(config.s3.db_key))
    with sqlite3.connect(":memory:") as copy:
        copy.deserialize(data)
        integrity = copy.execute("PRAGMA integrity_check").fetchone()[0]
        images = copy.execute("SELECT COUNT(*) FROM images").fetchone()[0]
        copies = copy.execute("SELECT COUNT(*) FROM s3_copies").fetchone()[0]
    copy.close()
    summary = (
        f"{len(data) / 1e6:.1f} MB, integrity_check {integrity}, {images} images,"
        f" {copies} S3 copies recorded"
    )
    return summary, integrity == "ok"


def download_original(
    conn: sqlite3.Connection, store: ObjectStore, key: bytes, image_id: str
) -> bytes:
    """The original's bytes from its recorded S3 version, checked against the image id."""
    row = conn.execute(
        "SELECT object_key, version_id, encrypted_sha256 FROM s3_copies"
        " WHERE image_id = ? AND verified_at IS NOT NULL",
        (image_id,),
    ).fetchone()
    if row is None:
        raise LookupError(f"no verified S3 copy of {image_id}")
    blob = store.get(row["object_key"], row["version_id"])
    if sha256_hex(blob) != row["encrypted_sha256"]:
        raise ValueError(f"downloaded object {row['object_key']} doesn't match its recorded hash")
    data = decrypt(key, blob)
    if sha256_hex(data) != image_id:
        raise ValueError(f"decrypted {row['object_key']} doesn't hash to its image id")
    return data
