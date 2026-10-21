"""Import: move new photos from the inbox folders into originals_root (plan P1.5, D18, D20).

This is the only code that adds files under originals_root. It never overwrites or
changes a file there. For each image file in an inbox (found by `LocalFolderSource`, so
hidden files and Syncthing's `.syncthing.*.tmp` files are skipped):

1. Skip it if it changed in the last MIN_AGE_S seconds: it may still be arriving.
2. Hash it. A hash already in `images` is a duplicate, and the inbox file is removed (only
   if the registered original is in place, or was evicted with a verified S3 copy).
3. Copy it to originals_root/YYYY/MM/<name>, dated by EXIF, else by the file's mtime. The
   copy goes to a hidden temp name in the target folder, is fsynced, re-hashed and only
   then renamed to its final name.
4. Insert the `images` row, with the inbox-relative path and the file's mtime. Only
   after that commit, delete the inbox file.
"""

import hashlib
import os
import shutil
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pillow_heif
from PIL import Image

from imgsearch.config import Config
from imgsearch.sources.local import LocalFolderSource
from imgsearch.stages.ingest import taken_at
from imgsearch.store.dates import local_iso

MIN_AGE_S = 60  # Syncthing writes to a temp name and renames, but a copy by hand may not
COPY_CHUNK = 1 << 20


class HashMismatch(Exception):
    pass


@dataclass
class ImportResult:
    imported: list[str] = field(default_factory=list)  # relpaths under originals_root
    duplicates: int = 0  # already registered; removed from the inbox
    too_new: int = 0  # changed in the last MIN_AGE_S seconds; left for the next run
    failed: list[tuple[str, str]] = field(default_factory=list)  # (inbox path, error)


def write_synced(src: Path, dest: Path) -> None:
    """Copy `src` to the new file `dest`, flushed and fsynced. Never overwrites."""
    with src.open("rb") as fin, dest.open("xb") as fout:
        shutil.copyfileobj(fin, fout, COPY_CHUNK)
        fout.flush()
        os.fsync(fout.fileno())


def import_inbox(
    config: Config,
    conn: sqlite3.Connection,
    write_copy: Callable[[Path, Path], None] = write_synced,
) -> ImportResult:
    """Import every settled image file from `config.inboxes`.

    `write_copy` writes the temp copy; tests pass one that corrupts it.
    """
    pillow_heif.register_heif_opener()
    result = ImportResult()
    now = time.time()
    for inbox in config.inboxes:
        inbox.mkdir(parents=True, exist_ok=True)
        for item in LocalFolderSource(inbox).scan():
            if now - item.mtime < MIN_AGE_S:
                result.too_new += 1
                continue
            try:
                relpath = _import_one(item.path, inbox, config, conn, write_copy)
            except Exception as e:  # one bad file is reported and must not stop the others
                result.failed.append((str(item.path), f"{type(e).__name__}: {e}"))
                continue
            if relpath is None:
                result.duplicates += 1
            else:
                result.imported.append(relpath)
    return result


def _import_one(
    path: Path,
    inbox: Path,
    config: Config,
    conn: sqlite3.Connection,
    write_copy: Callable[[Path, Path], None],
) -> str | None:
    """Import one inbox file. Returns its new relpath, or None for a duplicate.

    The row records the inbox-relative path (`source_relpath`, which keeps the phone
    folder such as "Screenshots") and the file's mtime (`file_mtime`)."""
    image_id = sha256_file(path)
    row = conn.execute("SELECT orig_relpath FROM images WHERE id = ?", (image_id,)).fetchone()
    if row is not None:
        in_s3 = conn.execute(
            "SELECT 1 FROM s3_copies WHERE image_id = ? AND verified_at IS NOT NULL", (image_id,)
        ).fetchone()
        if not in_s3 and not (config.originals_root / row["orig_relpath"]).is_file():
            raise FileNotFoundError(
                f"same photo as {row['orig_relpath']}, which is missing from originals_root"
                " and has no verified S3 copy; kept in the inbox"
            )
        path.unlink()
        return None

    target = _target(config, conn, path, image_id)
    if not target.exists():  # else: an earlier run copied it, then stopped before the insert
        _copy_verified(path, target, image_id, write_copy)
    relpath = target.relative_to(config.originals_root).as_posix()
    now = datetime.now(UTC).isoformat(timespec="seconds")
    with conn:
        conn.execute(
            "INSERT INTO images (id, orig_relpath, created_at, source_relpath, file_mtime)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                image_id,
                relpath,
                now,
                path.relative_to(inbox).as_posix(),
                local_iso(path.stat().st_mtime),
            ),
        )
    path.unlink()
    return relpath


def _target(config: Config, conn: sqlite3.Connection, path: Path, image_id: str) -> Path:
    """originals_root/YYYY/MM/<name>, or <stem>-<hash8><suffix> if that name is taken.

    A name holding the same bytes already counts as free (it is this photo).
    """
    folder = config.originals_root / date_folder(path)
    for name in (path.name, f"{path.stem}-{image_id[:8]}{path.suffix}"):
        candidate = folder / name
        relpath = candidate.relative_to(config.originals_root).as_posix()
        in_db = conn.execute("SELECT 1 FROM images WHERE orig_relpath = ?", (relpath,)).fetchone()
        if in_db:
            continue
        if not candidate.exists() or sha256_file(candidate) == image_id:
            return candidate
    raise FileExistsError(f"{folder / path.name} and its -{image_id[:8]} name are both taken")


def date_folder(path: Path) -> str:
    """The `YYYY/MM` folder: EXIF DateTimeOriginal, else DateTime, else the mtime (local)."""
    try:
        with Image.open(path) as img:
            taken = taken_at(img.getexif())
    except OSError:  # not a readable image; ingest will report it, file it by date
        taken = None
    if taken is not None:
        return f"{taken[:4]}/{taken[5:7]}"
    return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y/%m")


def _copy_verified(
    src: Path, dest: Path, image_id: str, write_copy: Callable[[Path, Path], None]
) -> None:
    """Copy via a hidden temp file in `dest`'s folder; rename only if its hash matches."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    temp = dest.with_name(f".{dest.name}.{image_id[:8]}.importing")
    temp.unlink(missing_ok=True)  # left by an interrupted run; never an original
    try:
        write_copy(src, temp)
        stat = src.stat()
        os.utime(temp, ns=(stat.st_atime_ns, stat.st_mtime_ns))  # keep the phone's date
        copied = sha256_file(temp)
        if copied != image_id:
            raise HashMismatch(
                f"copy of {src.name} has sha256 {copied[:12]}…, not {image_id[:12]}…"
            )
        if dest.exists():
            raise FileExistsError(f"{dest} appeared during the copy")
        os.rename(temp, dest)
    finally:
        temp.unlink(missing_ok=True)


def sha256_file(path: Path) -> str:
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()
