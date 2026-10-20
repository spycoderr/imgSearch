"""Ingest: register new originals by hash, then read EXIF and write derivatives.

Ingest runs in two steps:
1. `register_new` hashes every file the source offers and inserts an `images` row for
   each new hash. The runner works on existing rows, so this step must come first.
2. `IngestStage`, run through `run_stage`, opens each registered original read-only and
   fills in size, EXIF date and GPS, pHash, the derivative (at most 2048x1536 pixels in
   area) and the 256px thumbnail.
"""

import hashlib
import math
import numbers
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import imagehash
import pillow_heif
from PIL import ExifTags, Image, ImageOps

from imgsearch.config import Config
from imgsearch.interfaces import Context, ImageRecord, ImageSource

# Derivatives are capped by pixel area, not long edge, so panoramas and long screenshots
# keep readable text (plan D8, changed in M1.1).
DERIVATIVE_MAX_PIXELS = 2048 * 1536
THUMB_EDGE = 256
DERIVATIVE_QUALITY = 85
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
            # A profile for another colour space (e.g. CMYK) would be wrong on RGB output.
            icc_profile = src.info.get("icc_profile") if src.mode == "RGB" else None
            oriented = _to_rgb(ImageOps.exif_transpose(src))

        width, height = oriented.size
        derivative = _cap_area(oriented, DERIVATIVE_MAX_PIXELS)
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


def _to_rgb(img: Image.Image) -> Image.Image:
    """Convert to RGB, putting transparent images on white so dark text stays visible."""
    if img.mode in ("RGBA", "LA", "PA") or "transparency" in img.info:
        rgba = img.convert("RGBA")
        flat = Image.new("RGB", rgba.size, "white")
        flat.paste(rgba, mask=rgba.getchannel("A"))
        return flat
    return img.convert("RGB")


def _cap_area(img: Image.Image, max_pixels: int) -> Image.Image:
    """Downscale to at most `max_pixels`, keeping the aspect ratio; never upscale."""
    width, height = img.size
    if width * height <= max_pixels:
        return img
    scale = math.sqrt(max_pixels / (width * height))
    size = (max(1, math.floor(width * scale)), max(1, math.floor(height * scale)))
    return img.resize(size, Image.Resampling.LANCZOS)


def _save_jpeg(img: Image.Image, path: Path, quality: int, icc_profile: bytes | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "JPEG", quality=quality, icc_profile=icc_profile)


def _taken_at(exif: Image.Exif) -> str | None:
    """EXIF DateTimeOriginal, else DateTime, as naive local time `YYYY-MM-DDTHH:MM:SS`.

    Any UTC offset is ignored: users ask by local time ("Diwali evening"). None if missing
    or invalid.
    """
    exif_ifd = exif.get_ifd(ExifTags.IFD.Exif)
    taken = _parse_exif_datetime(exif_ifd.get(ExifTags.Base.DateTimeOriginal))
    if taken is not None:
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
    """EXIF GPS position as signed decimal degrees.

    (None, None) when absent or malformed, when exactly (0, 0) (phones write that without a
    fix), or when out of range. Bad GPS never fails the image.
    """
    gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
    lat = _degrees(gps.get(ExifTags.GPS.GPSLatitude), gps.get(ExifTags.GPS.GPSLatitudeRef), "NS")
    lon = _degrees(gps.get(ExifTags.GPS.GPSLongitude), gps.get(ExifTags.GPS.GPSLongitudeRef), "EW")
    if lat is None or lon is None or (lat == 0 and lon == 0):
        return None, None
    if abs(lat) > 90 or abs(lon) > 180:
        return None, None
    return lat, lon


def _degrees(dms: object, ref: object, refs: str) -> float | None:
    """(degrees, minutes, seconds) + a ref from `refs` ("NS" or "EW"; the second is negative)."""
    if not isinstance(dms, tuple) or len(dms) != 3:
        return None
    if not all(isinstance(x, numbers.Real) for x in dms):
        return None
    if not isinstance(ref, str):
        return None
    ref = ref.strip("\x00 ").upper()
    if ref not in (refs[0], refs[1]):
        return None
    d, m, s = (float(x) for x in dms)
    value = d + m / 60 + s / 3600
    if not math.isfinite(value):  # 0/0 rationals become NaN
        return None
    return -value if ref == refs[1] else value
