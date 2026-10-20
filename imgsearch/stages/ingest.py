"""Ingest: register new originals by hash, then read EXIF and write derivatives.

Ingest runs in two steps:
1. `register_new` hashes every file the source offers and inserts an `images` row for
   each new hash. The runner works on existing rows, so this step must come first.
2. `IngestStage`, run through `run_stage`, opens each registered original read-only and
   fills in size, EXIF date and GPS, pHash, the 2048px derivative and the 256px thumbnail.
"""

import hashlib
import math
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import imagehash
import pillow_heif
from PIL import ExifTags, Image, ImageOps

from imgsearch.config import Config
from imgsearch.interfaces import Context, ImageRecord, ImageSource

DERIVATIVE_EDGE = 2048  # long edge in pixels (plan D8)
THUMB_EDGE = 256
DERIVATIVE_QUALITY = 90
THUMB_QUALITY = 85


@dataclass
class RegisterResult:
    new: int = 0  # rows added
    known: int = 0  # already registered at the same path
    duplicate: int = 0  # same bytes as an image registered under another path
    unreadable: list[tuple[str, str]] = field(default_factory=list)  # (relpath, error)


def register_new(source: ImageSource, root: Path, conn: sqlite3.Connection) -> RegisterResult:
    """Hash each file from `source` and add an `images` row for every new hash.

    The first path seen for a hash is kept. A file that can't be read is recorded in
    `unreadable` and the scan continues.
    """
    result = RegisterResult()
    for item in source.scan():
        relpath = item.path.relative_to(root).as_posix()
        try:
            with source.open(item) as f:
                image_id = hashlib.file_digest(f, "sha256").hexdigest()  # reads in chunks
        except OSError as e:  # e.g. permission denied; one bad file must not stop the scan
            result.unreadable.append((relpath, f"{type(e).__name__}: {e}"))
            continue
        row = conn.execute("SELECT orig_relpath FROM images WHERE id = ?", (image_id,)).fetchone()
        if row is None:
            now = datetime.now(UTC).isoformat(timespec="seconds")
            with conn:
                conn.execute(
                    "INSERT INTO images (id, orig_relpath, created_at) VALUES (?, ?, ?)",
                    (image_id, relpath, now),
                )
            result.new += 1
        elif row["orig_relpath"] == relpath:
            result.known += 1
        else:
            result.duplicate += 1
    return result


class IngestStage:
    """Fill in metadata and write the derivative and thumbnail for a registered image.

    `derivative_path` and `thumb_path` are stored relative to `derivatives_root`.
    `width` and `height` are the original's size after EXIF orientation is applied.
    """

    name = "ingest"
    requires: list[str] = []

    def __init__(self, config: Config) -> None:
        self.version = config.stages[self.name]

    def load(self, ctx: Context) -> None:
        pillow_heif.register_heif_opener()

    def unload(self) -> None:
        pass  # no model to free

    def run(self, image: ImageRecord, ctx: Context) -> None:
        config = ctx.config
        original = config.originals_root / image.orig_relpath
        with original.open("rb") as f, Image.open(f) as src:
            exif = src.getexif()
            icc_profile = src.info.get("icc_profile")
            derivative = ImageOps.exif_transpose(src).convert("RGB")

        width, height = derivative.size  # before resizing
        derivative.thumbnail((DERIVATIVE_EDGE, DERIVATIVE_EDGE), Image.Resampling.LANCZOS)
        thumb = derivative.copy()
        thumb.thumbnail((THUMB_EDGE, THUMB_EDGE), Image.Resampling.LANCZOS)

        shard = image.id[:2]
        derivative_rel = f"derivatives/{shard}/{image.id}.jpg"
        thumb_rel = f"thumbs/{shard}/{image.id}.jpg"
        _save_jpeg(
            derivative, config.derivatives_root / derivative_rel, DERIVATIVE_QUALITY, icc_profile
        )
        _save_jpeg(thumb, config.derivatives_root / thumb_rel, THUMB_QUALITY, icc_profile)

        lat, lon = _gps(exif)
        with ctx.conn:
            ctx.conn.execute(
                "UPDATE images SET derivative_path = ?, thumb_path = ?, width = ?, height = ?,"
                " taken_at = ?, lat = ?, lon = ?, phash = ? WHERE id = ?",
                (
                    derivative_rel,
                    thumb_rel,
                    width,
                    height,
                    _taken_at(exif),
                    lat,
                    lon,
                    str(imagehash.phash(derivative)),
                    image.id,
                ),
            )


def _save_jpeg(img: Image.Image, path: Path, quality: int, icc_profile: bytes | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "JPEG", quality=quality, icc_profile=icc_profile)


def _taken_at(exif: Image.Exif) -> str | None:
    """EXIF DateTimeOriginal, else DateTime, as ISO 8601; None if missing or invalid.

    The UTC offset is appended when the file records OffsetTimeOriginal.
    """
    exif_ifd = exif.get_ifd(ExifTags.IFD.Exif)
    taken = _parse_exif_datetime(exif_ifd.get(ExifTags.Base.DateTimeOriginal))
    if taken is not None:
        offset = exif_ifd.get(ExifTags.Base.OffsetTimeOriginal)
        if isinstance(offset, str) and re.fullmatch(r"[+-]\d\d:\d\d", offset.strip("\x00 ")):
            return taken + offset.strip("\x00 ")
        return taken
    return _parse_exif_datetime(exif.get(ExifTags.Base.DateTime))


def _parse_exif_datetime(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value.strip("\x00 "), "%Y:%m:%d %H:%M:%S").isoformat()
    except ValueError:  # cameras write placeholders such as "0000:00:00 00:00:00"
        return None


def _gps(exif: Image.Exif) -> tuple[float | None, float | None]:
    """EXIF GPS position as signed decimal degrees, or (None, None) if absent or invalid."""
    gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
    keys = (
        ExifTags.GPS.GPSLatitude,
        ExifTags.GPS.GPSLatitudeRef,
        ExifTags.GPS.GPSLongitude,
        ExifTags.GPS.GPSLongitudeRef,
    )
    if not all(k in gps for k in keys):
        return None, None
    lat = _degrees(gps[ExifTags.GPS.GPSLatitude], gps[ExifTags.GPS.GPSLatitudeRef], "S")
    lon = _degrees(gps[ExifTags.GPS.GPSLongitude], gps[ExifTags.GPS.GPSLongitudeRef], "W")
    if lat is None or lon is None:
        return None, None
    return lat, lon


def _degrees(dms: tuple, ref: str, negative_ref: str) -> float | None:
    # Phones without a fix write 0/0 rationals, which become NaN.
    if len(dms) != 3:
        return None
    d, m, s = (float(x) for x in dms)
    value = d + m / 60 + s / 3600
    if math.isnan(value):
        return None
    return -value if ref.strip("\x00 ").upper() == negative_ref else value
