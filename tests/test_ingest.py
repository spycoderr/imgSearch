import hashlib
import os
import shutil
import sqlite3
from pathlib import Path

import pillow_heif
import pytest
from PIL import ExifTags, Image
from typer.testing import CliRunner

from imgsearch.cli import app
from imgsearch.config import Config
from imgsearch.interfaces import Context
from imgsearch.sources.local import LocalFolderSource
from imgsearch.stages.ingest import IngestStage, register_new
from imgsearch.stages.runner import run_stage


def make_jpeg(path: Path, size=(400, 200), color="red", **exif_fields) -> Path:
    """Write a JPEG with optional EXIF: orientation, original, datetime, gps=(lat, lon)."""
    exif = Image.Exif()
    if "orientation" in exif_fields:
        exif[ExifTags.Base.Orientation] = exif_fields["orientation"]
    if "original" in exif_fields:
        exif.get_ifd(ExifTags.IFD.Exif)[ExifTags.Base.DateTimeOriginal] = exif_fields["original"]
    if "datetime" in exif_fields:
        exif[ExifTags.Base.DateTime] = exif_fields["datetime"]
    if "gps" in exif_fields:
        lat, lon = exif_fields["gps"]
        gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
        gps[ExifTags.GPS.GPSLatitudeRef] = "N" if lat >= 0 else "S"
        gps[ExifTags.GPS.GPSLatitude] = (float(int(abs(lat))), (abs(lat) % 1) * 60, 0.0)
        gps[ExifTags.GPS.GPSLongitudeRef] = "E" if lon >= 0 else "W"
        gps[ExifTags.GPS.GPSLongitude] = (float(int(abs(lon))), (abs(lon) % 1) * 60, 0.0)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path, "JPEG", exif=exif)
    return path


def ingest(config: Config, conn: sqlite3.Connection):
    found = register_new(LocalFolderSource(config.originals_root), config.originals_root, conn)
    counts = run_stage(IngestStage(config), Context(config=config, conn=conn))
    return found, counts


def row(conn: sqlite3.Connection, relpath: str) -> sqlite3.Row:
    return conn.execute("SELECT * FROM images WHERE orig_relpath = ?", (relpath,)).fetchone()


def snapshot(root: Path) -> dict[str, tuple[str, int]]:
    """sha256 and mtime_ns of every file under root."""
    return {
        p.relative_to(root).as_posix(): (
            hashlib.sha256(p.read_bytes()).hexdigest(),
            p.stat().st_mtime_ns,
        )
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


# ---- source ----


def test_scan_is_recursive_and_filters_extensions(config: Config):
    root = config.originals_root
    for name in ["a.jpg", "b.JPG", "c.jpeg", "d.png", "e.HEIC", "f.heif", "sub/deep/g.Jpg"]:
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_bytes(b"x")
    skipped = ["clip.mov", "x.aae", "meta.json", "all.zip", ".DS_Store", "._a.jpg", ".hid/h.jpg"]
    for name in skipped:
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_bytes(b"x")
    (root / "folder.jpg").mkdir()

    found = [i.path.relative_to(root).as_posix() for i in LocalFolderSource(root).scan()]
    assert found == ["a.jpg", "b.JPG", "c.jpeg", "d.png", "e.HEIC", "f.heif", "sub/deep/g.Jpg"]


def test_scan_missing_root_fails(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        list(LocalFolderSource(tmp_path / "missing").scan())


# ---- register ----


def test_id_is_sha256_and_relpath_is_relative(config: Config, conn: sqlite3.Connection):
    path = make_jpeg(config.originals_root / "trip" / "a.jpg")
    ingest(config, conn)
    r = row(conn, "trip/a.jpg")
    assert r["id"] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_duplicate_bytes_make_one_row(config: Config, conn: sqlite3.Connection):
    make_jpeg(config.originals_root / "a.jpg")
    (config.originals_root / "z").mkdir()
    shutil.copy(config.originals_root / "a.jpg", config.originals_root / "z" / "copy.jpg")

    found, _ = ingest(config, conn)
    assert (found.new, found.duplicate) == (1, 1)
    rows = conn.execute("SELECT orig_relpath FROM images").fetchall()
    assert [r[0] for r in rows] == ["a.jpg"]


def test_unreadable_file_is_reported_and_scan_continues(config: Config, conn: sqlite3.Connection):
    make_jpeg(config.originals_root / "a.jpg")
    locked = make_jpeg(config.originals_root / "b.jpg", color="blue")
    locked.chmod(0o000)
    try:
        found, counts = ingest(config, conn)
    finally:
        locked.chmod(0o644)
    assert found.new == 1
    assert [relpath for relpath, _ in found.unreadable] == ["b.jpg"]
    assert "PermissionError" in found.unreadable[0][1]
    assert counts == {"done": 1, "failed": 0}


# ---- stage ----


def test_rotated_image_is_stored_upright(config: Config, conn: sqlite3.Connection):
    make_jpeg(config.originals_root / "r.jpg", size=(400, 200), orientation=6)
    ingest(config, conn)
    r = row(conn, "r.jpg")
    assert (r["width"], r["height"]) == (200, 400)
    with Image.open(config.derivatives_root / r["derivative_path"]) as d:
        assert d.size == (200, 400)
        assert d.getexif().get(ExifTags.Base.Orientation, 1) == 1


def test_derivative_and_thumbnail_sizes_and_paths(config: Config, conn: sqlite3.Connection):
    make_jpeg(config.originals_root / "big.jpg", size=(3000, 1500))
    make_jpeg(config.originals_root / "small.jpg", size=(300, 100), color="blue")
    ingest(config, conn)

    big = row(conn, "big.jpg")
    assert big["derivative_path"] == f"derivatives/{big['id'][:2]}/{big['id']}.jpg"
    assert big["thumb_path"] == f"thumbs/{big['id'][:2]}/{big['id']}.jpg"
    with Image.open(config.derivatives_root / big["derivative_path"]) as d:
        assert (d.format, d.size) == ("JPEG", (2048, 1024))
    with Image.open(config.derivatives_root / big["thumb_path"]) as t:
        assert (t.format, t.size) == ("JPEG", (256, 128))

    small = row(conn, "small.jpg")
    with Image.open(config.derivatives_root / small["derivative_path"]) as d:
        assert d.size == (300, 100)  # never upscaled
    assert int(small["phash"], 16) >= 0 and len(small["phash"]) == 16


@pytest.mark.parametrize(
    ("exif_fields", "expected"),
    [
        ({"original": "2019:04:23 15:12:01"}, "2019-04-23T15:12:01"),
        ({"original": "2019:04:23 15:12:01\x00"}, "2019-04-23T15:12:01"),
        ({"datetime": "2018:01:02 03:04:05"}, "2018-01-02T03:04:05"),
        (
            {"original": "2019:04:23 15:12:01", "datetime": "2018:01:02 03:04:05"},
            "2019-04-23T15:12:01",
        ),
        ({"original": "0000:00:00 00:00:00"}, None),
        ({}, None),
    ],
    ids=["original", "nul-padded", "datetime-fallback", "original-wins", "invalid", "missing"],
)
def test_taken_at(config: Config, conn: sqlite3.Connection, exif_fields, expected):
    make_jpeg(config.originals_root / "a.jpg", **exif_fields)
    ingest(config, conn)
    assert row(conn, "a.jpg")["taken_at"] == expected


def test_gps_signed_degrees(config: Config, conn: sqlite3.Connection):
    make_jpeg(config.originals_root / "goa.jpg", gps=(15.5, 73.75))
    make_jpeg(config.originals_root / "rio.jpg", color="blue", gps=(-22.5, -43.25))
    make_jpeg(config.originals_root / "none.jpg", color="green")
    ingest(config, conn)
    assert (row(conn, "goa.jpg")["lat"], row(conn, "goa.jpg")["lon"]) == pytest.approx(
        (15.5, 73.75)
    )
    assert (row(conn, "rio.jpg")["lat"], row(conn, "rio.jpg")["lon"]) == pytest.approx(
        (-22.5, -43.25)
    )
    assert (row(conn, "none.jpg")["lat"], row(conn, "none.jpg")["lon"]) == (None, None)


def test_heic_is_ingested(config: Config, conn: sqlite3.Connection):
    pillow_heif.register_heif_opener()
    config.originals_root.mkdir(parents=True)
    Image.new("RGB", (640, 480), "orange").save(config.originals_root / "p.heic")
    _, counts = ingest(config, conn)
    assert counts == {"done": 1, "failed": 0}
    r = row(conn, "p.heic")
    assert (r["width"], r["height"]) == (640, 480)
    with Image.open(config.derivatives_root / r["derivative_path"]) as d:
        assert d.format == "JPEG"


def test_corrupt_image_fails_others_finish(config: Config, conn: sqlite3.Connection):
    make_jpeg(config.originals_root / "good.jpg")
    (config.originals_root / "bad.jpg").write_bytes(b"not an image")
    _, counts = ingest(config, conn)
    assert counts == {"done": 1, "failed": 1}
    states = dict(
        conn.execute(
            "SELECT i.orig_relpath, s.state || ':' || coalesce(s.error, '') FROM stage_status s"
            " JOIN images i ON i.id = s.image_id WHERE s.stage = 'ingest'"
        ).fetchall()
    )
    assert states["good.jpg"] == "done:"
    assert states["bad.jpg"].startswith("failed:UnidentifiedImageError")


def test_second_run_does_nothing(config: Config, conn: sqlite3.Connection):
    make_jpeg(config.originals_root / "a.jpg")
    make_jpeg(config.originals_root / "b.jpg", color="blue")
    found, counts = ingest(config, conn)
    assert (found.new, counts["done"]) == (2, 2)
    found, counts = ingest(config, conn)
    assert (found.new, found.known) == (0, 2)
    assert counts == {"done": 0, "failed": 0}


def test_originals_are_untouched(config: Config, conn: sqlite3.Connection):
    root = config.originals_root
    make_jpeg(root / "a.jpg", orientation=6, original="2019:04:23 15:12:01", gps=(15.5, 73.75))
    make_jpeg(root / "sub" / "b.jpg", color="blue")
    (root / "sub" / "c.jpg").write_bytes((root / "a.jpg").read_bytes())  # duplicate
    before = snapshot(root)
    # Read-only files and folders: any write attempt would raise and fail the image.
    for path in [*root.rglob("*"), root]:
        path.chmod(0o555 if path.is_dir() else 0o444)
    try:
        _, counts = ingest(config, conn)
    finally:
        for path in [root, *root.rglob("*")]:
            path.chmod(0o755 if path.is_dir() else 0o644)
    assert counts == {"done": 2, "failed": 0}
    assert snapshot(root) == before
    assert sorted(os.listdir(root)) == ["a.jpg", "sub"]


# ---- CLI ----


def test_cli_ingest_twice(config: Config, config_path: Path):
    make_jpeg(config.originals_root / "a.jpg")
    make_jpeg(config.originals_root / "b.jpg", color="blue")
    args = ["--config", str(config_path), "ingest"]

    first = CliRunner().invoke(app, args)
    assert first.exit_code == 0, first.output
    assert "2 new" in first.output
    assert "2 processed (2 done, 0 failed)" in first.output

    second = CliRunner().invoke(app, args)
    assert second.exit_code == 0, second.output
    assert "0 new" in second.output
    assert "0 processed" in second.output


def test_cli_exits_nonzero_on_failure(config: Config, config_path: Path):
    config.originals_root.mkdir(parents=True)
    (config.originals_root / "bad.jpg").write_bytes(b"not an image")
    result = CliRunner().invoke(app, ["--config", str(config_path), "ingest"])
    assert result.exit_code == 1
    assert "failed: bad.jpg" in result.output
