import hashlib
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path

import pytest
from PIL import ExifTags, Image
from typer.testing import CliRunner

from imgsearch.cli import app
from imgsearch.config import Config
from imgsearch.sources.importer import MIN_AGE_S, import_inbox, write_synced


def photo(path: Path, color="red", original: str | None = None, datetime_: str | None = None):
    """A small JPEG with optional EXIF dates, settled (mtime older than MIN_AGE_S)."""
    exif = Image.Exif()
    if original:
        exif.get_ifd(ExifTags.IFD.Exif)[ExifTags.Base.DateTimeOriginal] = original
    if datetime_:
        exif[ExifTags.Base.DateTime] = datetime_
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (40, 30), color).save(path, "JPEG", exif=exif)
    settle(path)
    return path


def settle(path: Path, mtime: float | None = None) -> None:
    mtime = time.time() - MIN_AGE_S - 60 if mtime is None else mtime
    os.utime(path, (mtime, mtime))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rows(conn: sqlite3.Connection) -> dict[str, str]:
    return dict(conn.execute("SELECT orig_relpath, id FROM images").fetchall())


def files(root: Path) -> list[str]:
    if not root.exists():
        return []
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())


@pytest.fixture
def inbox(config: Config) -> Path:
    return config.inboxes[0]


def test_new_photo_is_copied_by_exif_date_registered_and_removed(config, conn, inbox):
    src = photo(inbox / "IMG_1.jpg", original="2019:04:23 15:12:01")
    image_id, mtime = sha(src), src.stat().st_mtime_ns

    result = import_inbox(config, conn)

    assert result.imported == ["2019/04/IMG_1.jpg"]
    assert (result.duplicates, result.too_new, result.failed) == (0, 0, [])
    copy = config.originals_root / "2019/04/IMG_1.jpg"
    assert sha(copy) == image_id and copy.stat().st_mtime_ns == mtime  # bytes and date kept
    assert rows(conn) == {"2019/04/IMG_1.jpg": image_id}  # relpath relative to originals_root
    assert not src.exists()
    assert files(config.originals_root) == ["2019/04/IMG_1.jpg"]  # no temp file left


def test_date_falls_back_to_exif_datetime_then_mtime(config, conn, inbox):
    photo(inbox / "dt.jpg", color="blue", datetime_="2018:12:31 23:59:00")
    plain = photo(inbox / "plain.jpg", color="green")
    settle(plain, datetime(2017, 3, 5, 12, 0).timestamp())  # Syncthing keeps the phone's mtime

    result = import_inbox(config, conn)

    assert sorted(result.imported) == ["2017/03/plain.jpg", "2018/12/dt.jpg"]


def test_scan_follows_local_folder_rules(config, conn, inbox):
    photo(inbox / "a.jpg")
    photo(inbox / "Camera" / "b.JPG", color="blue")
    for name in [
        ".syncthing.c.jpg.tmp",  # Syncthing writes here, then renames
        "~syncthing~d.jpg.tmp",
        ".stfolder/x.jpg",
        "._a.jpg",
        "notes.txt",
        "clip.mp4",
    ]:
        (inbox / name).parent.mkdir(parents=True, exist_ok=True)
        (inbox / name).write_bytes(b"x")
        settle(inbox / name)

    result = import_inbox(config, conn)

    assert sorted(p.rsplit("/", 1)[1] for p in result.imported) == ["a.jpg", "b.JPG"]
    assert result.failed == []
    assert (inbox / ".syncthing.c.jpg.tmp").exists() and (inbox / "notes.txt").exists()


def test_recently_changed_file_is_skipped_and_kept(config, conn, inbox):
    fresh = photo(inbox / "fresh.jpg")
    settle(fresh, time.time() - 5)  # still being written
    photo(inbox / "old.jpg", color="blue")

    result = import_inbox(config, conn)

    assert result.too_new == 1 and len(result.imported) == 1
    assert fresh.exists() and "fresh.jpg" not in str(rows(conn))


def test_duplicate_is_counted_and_removed_from_the_inbox(config, conn, inbox):
    photo(inbox / "a.jpg", original="2019:04:23 15:12:01")
    import_inbox(config, conn)
    again = photo(inbox / "again" / "a-copy.jpg", original="2019:04:23 15:12:01")

    result = import_inbox(config, conn)

    assert (result.imported, result.duplicates, result.failed) == ([], 1, [])
    assert not again.exists()
    assert list(rows(conn)) == ["2019/04/a.jpg"]


def test_duplicate_of_a_missing_original_is_kept(config, conn, inbox):
    src = photo(inbox / "a.jpg")
    with conn:  # registered under a path whose file isn't in originals_root
        conn.execute(
            "INSERT INTO images (id, orig_relpath, created_at) VALUES (?, 'gone.jpg', 'now')",
            (sha(src),),
        )

    result = import_inbox(config, conn)

    assert result.duplicates == 0 and len(result.failed) == 1
    assert "gone.jpg" in result.failed[0][1]
    assert src.exists()


def test_name_clash_with_other_content_gets_hash_suffix(config, conn, inbox):
    photo(inbox / "IMG.jpg", original="2019:04:23 15:12:01")
    import_inbox(config, conn)
    first = config.originals_root / "2019/04/IMG.jpg"
    before = (sha(first), first.stat().st_mtime_ns)
    other = photo(inbox / "IMG.jpg", color="blue", original="2019:04:23 15:12:01")
    other_id = sha(other)

    result = import_inbox(config, conn)

    assert result.imported == [f"2019/04/IMG-{other_id[:8]}.jpg"]
    assert (sha(first), first.stat().st_mtime_ns) == before  # never overwritten
    assert rows(conn)[f"2019/04/IMG-{other_id[:8]}.jpg"] == other_id


def test_copy_left_by_an_interrupted_run_is_reused(config, conn, inbox):
    src = photo(inbox / "a.jpg", original="2019:04:23 15:12:01")
    target = config.originals_root / "2019/04/a.jpg"
    target.parent.mkdir(parents=True)
    target.write_bytes(src.read_bytes())  # copied, but the row was never inserted

    result = import_inbox(config, conn)

    assert result.imported == ["2019/04/a.jpg"]
    assert files(config.originals_root) == ["2019/04/a.jpg"]
    assert not src.exists()


def test_hash_mismatch_registers_nothing_and_keeps_the_inbox_file(config, conn, inbox):
    src = photo(inbox / "a.jpg", original="2019:04:23 15:12:01")

    def corrupt(source: Path, dest: Path) -> None:
        write_synced(source, dest)
        with dest.open("ab") as f:
            f.write(b"bit rot")

    result = import_inbox(config, conn, write_copy=corrupt)

    assert result.imported == [] and len(result.failed) == 1
    assert result.failed[0][0] == str(src) and "HashMismatch" in result.failed[0][1]
    assert rows(conn) == {}
    assert src.exists()
    assert files(config.originals_root) == []  # neither the final name nor the temp file


def test_one_failure_does_not_stop_the_others(config, conn, inbox):
    locked = photo(inbox / "locked.jpg")
    photo(inbox / "ok.jpg", color="blue")
    locked.chmod(0o000)
    try:
        result = import_inbox(config, conn)
    finally:
        locked.chmod(0o644)

    assert len(result.imported) == 1 and result.imported[0].endswith("/ok.jpg")
    assert [path for path, _ in result.failed] == [str(locked)]
    assert "PermissionError" in result.failed[0][1]
    assert locked.exists()


def test_rerun_with_nothing_new_does_nothing(config, conn, inbox):
    photo(inbox / "a.jpg")
    import_inbox(config, conn)
    snapshot = {p: (sha(config.originals_root / p)) for p in files(config.originals_root)}

    result = import_inbox(config, conn)

    assert (result.imported, result.duplicates, result.too_new, result.failed) == ([], 0, 0, [])
    assert {p: sha(config.originals_root / p) for p in files(config.originals_root)} == snapshot


def test_missing_inbox_is_created(config, conn):
    assert import_inbox(config, conn).imported == []
    assert config.inboxes[0].is_dir()


def test_cli_prints_counts_and_fails_on_errors(config_path, config, inbox):
    photo(inbox / "ok.jpg", original="2019:04:23 15:12:01")
    fresh = photo(inbox / "fresh.jpg", color="blue")
    settle(fresh, time.time())
    locked = photo(inbox / "locked.jpg", color="green")
    locked.chmod(0o000)
    try:
        result = CliRunner().invoke(app, ["--config", str(config_path), "import"])
    finally:
        locked.chmod(0o644)

    assert result.exit_code == 1
    lines = result.output.splitlines()
    assert lines[0] == "import: 1 imported, 0 duplicates, 1 skipped (too new), 1 failed"
    assert lines[1] == "  imported: 2019/04/ok.jpg"
    assert f"  failed: {locked}: PermissionError" in result.output
