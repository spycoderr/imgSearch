"""P1.7 file names and source folders: tokenising, the filename stage, import, backfills."""

import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path

import httpx
import pytest
from PIL import Image
from typer.testing import CliRunner

from imgsearch.cli import STAGES, app
from imgsearch.config import Config, env_value
from imgsearch.interfaces import Context, ParsedQuery
from imgsearch.search.text import TextRetriever
from imgsearch.sources.importer import import_inbox
from imgsearch.sources.syncthing import folder_id, synced_files
from imgsearch.stages.filename import FilenameStage, filename_words
from imgsearch.stages.runner import run_stage
from imgsearch.store.backfill import backfill_file_mtime, backfill_source_folders
from imgsearch.store.status import set_status
from imgsearch.store.text import save_caption, save_ocr

# ---- tokenising ----


@pytest.mark.parametrize(
    ("relpath", "words"),
    [
        ("2023/03/Signature Nilabh black_1 (1).jpg", ["signature", "nilabh", "black"]),
        ("Ultrasound report Kaushlya devi_1.jpg", ["ultrasound", "report", "kaushlya", "devi"]),
        (
            "Screenshots/Screenshot_20260327-171258_Gmail.png",
            ["screenshots", "screenshot", "gmail"],
        ),
        ("StarRail_Image_1700000000.jpg", ["starrail", "star", "rail", "image"]),
        ("WhatsApp Images/IMG-20260520-WA0034.jpg", ["whatsapp", "whats", "app", "images"]),
        ("IMG_20260915_221205788_HDR.jpg", []),
        ("PXL_20240101_120000.jpg", []),
        ("DSC00012.JPG", []),
        ("VID_20240101.jpg", []),
        ("PORTRAIT_20190101_BURST.jpg", ["burst"]),
        ("Adobe Scan Exports/Adobe Scan 12 Jan 2024.jpg", ["adobe", "scan", "exports", "jan"]),
        (
            "Wi-Fi_QR_code_Sarpotdar's_5GHz.jpg",
            ["wi", "fi", "qr", "code", "sarpotdar", "ghz", "hz"],
        ),
        ("QRCode.png", ["qrcode", "qr", "code"]),
        ("2024/02/शादी_1.jpg", ["शादी"]),
    ],
)
def test_filename_words(relpath: str, words: list[str]):
    assert filename_words(relpath) == words


# ---- the stage ----


@pytest.fixture
def ctx(config: Config, conn: sqlite3.Connection) -> Context:
    return Context(config=config, conn=conn)


def add(conn: sqlite3.Connection, image_id: str, orig: str, source: str | None = None) -> None:
    with conn:
        conn.execute(
            "INSERT INTO images (id, orig_relpath, created_at, source_relpath) VALUES (?, ?, ?, ?)",
            (image_id, orig, "2026-10-21T00:00:00+00:00", source),
        )


def fts_rows(conn: sqlite3.Connection, kind: str = "filename") -> dict[str, str]:
    return dict(conn.execute("SELECT image_id, text FROM text_fts WHERE kind = ?", (kind,)))


def test_stage_writes_one_filename_row_per_image(ctx: Context):
    add(ctx.conn, "sig", "2023/03/Signature Nilabh black_1 (1).jpg")
    add(ctx.conn, "shot", "2026/03/Screenshot_1_Gmail.png", "Screenshots/Screenshot_1_Gmail.png")
    save_ocr(ctx.conn, "shot", "Offer letter")  # other kinds are left alone

    assert run_stage(FilenameStage(ctx.config), ctx) == {"done": 2, "failed": 0}

    assert fts_rows(ctx.conn) == {
        "sig": "signature nilabh black",
        "shot": "screenshots screenshot gmail",  # the source folder comes from the inbox path
    }
    assert fts_rows(ctx.conn, "ocr") == {"shot": "Offer letter"}
    assert run_stage(FilenameStage(ctx.config), ctx) == {"done": 0, "failed": 0}
    assert len(fts_rows(ctx.conn)) == 2  # replaced, not duplicated


def test_stage_is_registered_after_ocr():
    assert STAGES["filename"] is FilenameStage
    assert list(STAGES).index("filename") > list(STAGES).index("ocr")


def test_version_bump_reruns_only_the_filename_stage(config_path: Path, conn, ctx: Context):
    add(conn, "a", "Signature_a.jpg")
    for stage in ("ingest", "embed", "ocr"):
        set_status(conn, "a", stage, ctx.config.stages[stage], "done")
    run_stage(FilenameStage(ctx.config), ctx)

    text = config_path.read_text(encoding="utf-8")
    config_path.write_text(text.replace('filename: "1"', 'filename: "2"'), encoding="utf-8")
    result = CliRunner().invoke(app, ["--config", str(config_path), "run", "filename"])

    assert result.exit_code == 0, result.output
    assert result.output.strip() == "filename: 1 processed (1 done, 0 failed)"
    versions = dict(conn.execute("SELECT stage, version FROM stage_status"))
    assert versions["filename"] == "2"
    assert {s: versions[s] for s in ("ingest", "embed", "ocr")} == {
        s: ctx.config.stages[s] for s in ("ingest", "embed", "ocr")
    }


def test_text_retriever_finds_file_names_with_ocr_and_captions(ctx: Context):
    add(ctx.conn, "sig", "Signature Nilabh black_1 (1).jpg")
    add(ctx.conn, "doc", "IMG_1.jpg")
    add(ctx.conn, "cap", "IMG_2.jpg")
    save_ocr(ctx.conn, "doc", "Nilabh's notes")
    save_caption(ctx.conn, "cap", "A signature on paper.", "m")
    run_stage(FilenameStage(ctx.config), ctx)
    q = ParsedQuery(english="Signature from Nilabh", original="Signature from Nilabh")

    assert {c.image_id for c in TextRetriever(ctx.conn).retrieve(q, 10)} == {"sig", "doc", "cap"}
    no_captions = TextRetriever(ctx.conn, kinds=("ocr", "filename"))
    assert {c.image_id for c in no_captions.retrieve(q, 10)} == {"sig", "doc"}
    assert TextRetriever(ctx.conn).retrieve(q, 10)[0].image_id == "sig"  # both words match


# ---- import records the inbox path and the file's date ----


def test_import_records_inbox_path_and_mtime(config: Config, conn):
    src = config.inboxes[0] / "Screenshots" / "Screenshot_20260327-171258_Gmail.png"
    src.parent.mkdir(parents=True)
    Image.new("RGB", (8, 8), "red").save(src, "PNG")  # no EXIF date
    mtime = datetime(2026, 3, 27, 17, 13, 0).timestamp()  # older than the import's 60 s
    os.utime(src, (mtime, mtime))

    result = import_inbox(config, conn)

    assert result.imported == ["2026/03/Screenshot_20260327-171258_Gmail.png"]
    row = conn.execute("SELECT source_relpath, file_mtime, taken_at FROM images").fetchone()
    assert tuple(row) == (
        "Screenshots/Screenshot_20260327-171258_Gmail.png",
        "2026-03-27T17:13:00",
        None,
    )
    run_stage(FilenameStage(config), Context(config=config, conn=conn))
    assert list(fts_rows(conn).values()) == ["screenshots screenshot gmail"]


# ---- backfills ----


def original(config: Config, relpath: str, data: bytes = b"x", mtime: float | None = None):
    path = config.originals_root / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def evict(conn: sqlite3.Connection, image_id: str) -> None:
    with conn:
        conn.execute(
            "INSERT INTO original_cache (image_id, last_access, evicted_at) VALUES (?, ?, ?)",
            (image_id, "2026-10-21T00:00:00", "2026-10-21T00:00:00"),
        )


def test_backfill_file_mtime(config: Config, conn):
    when = datetime(2024, 2, 3, 10, 20, 30).timestamp()
    add(conn, "on", "2024/02/a.jpg")
    original(config, "2024/02/a.jpg", mtime=when)
    add(conn, "gone", "2024/02/b.jpg")
    add(conn, "evicted", "2024/02/c.jpg")
    evict(conn, "evicted")
    add(conn, "set", "2024/02/d.jpg")
    original(config, "2024/02/d.jpg", mtime=when)
    with conn:
        conn.execute("UPDATE images SET file_mtime = '2020-01-01T00:00:00' WHERE id = 'set'")

    result = backfill_file_mtime(config, conn)

    assert (result.updated, result.evicted, result.missing) == (1, 1, ["2024/02/b.jpg"])
    mtimes = dict(conn.execute("SELECT id, file_mtime FROM images"))
    assert mtimes == {
        "on": "2024-02-03T10:20:30",
        "gone": None,
        "evicted": None,
        "set": "2020-01-01T00:00:00",  # already set: left alone
    }
    assert backfill_file_mtime(config, conn).updated == 0


def test_backfill_mtime_cli(config_path: Path, config: Config, conn):
    add(conn, "on", "a.jpg")
    original(config, "a.jpg")
    result = CliRunner().invoke(app, ["--config", str(config_path), "backfill", "mtime"])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == (
        "file_mtime: 1 set, 0 skipped (evicted), 0 skipped (original missing)"
    )


def test_backfill_source_folders(config: Config, conn):
    add(conn, "shot", "2026/03/Screenshot_1_Gmail.png")
    original(config, "2026/03/Screenshot_1_Gmail.png", b"12345")
    add(conn, "twice", "2025/01/IMG_2.jpg")
    original(config, "2025/01/IMG_2.jpg", b"123")
    add(conn, "none", "2025/01/IMG_3.jpg")
    original(config, "2025/01/IMG_3.jpg", b"1")
    add(conn, "size", "2025/01/IMG_4.jpg")
    original(config, "2025/01/IMG_4.jpg", b"1")
    add(conn, "evicted", "2025/01/IMG_5.jpg")
    evict(conn, "evicted")
    add(conn, "known", "2025/01/IMG_6.jpg", "Camera/IMG_6.jpg")
    synced = [
        ("Screenshots/Screenshot_1_Gmail.png", 5),
        ("WhatsApp/IMG_2.jpg", 3),
        ("Camera/IMG_2.jpg", 3),  # same name and size twice: ambiguous
        ("Camera/IMG_4.jpg", 99),  # same name, other size: no match
        ("Camera/IMG_5.jpg", 1),
    ]

    dry = backfill_source_folders(config, conn, synced, dry_run=True)
    assert dry.updated == [("2026/03/Screenshot_1_Gmail.png", "Screenshots/Screenshot_1_Gmail.png")]
    assert conn.execute("SELECT COUNT(source_relpath) FROM images").fetchone()[0] == 1

    result = backfill_source_folders(config, conn, synced)
    assert result.updated == dry.updated
    assert (result.ambiguous, result.unmatched, result.evicted) == (1, 2, 1)
    sources = dict(conn.execute("SELECT id, source_relpath FROM images"))
    assert sources["shot"] == "Screenshots/Screenshot_1_Gmail.png"
    assert sources["known"] == "Camera/IMG_6.jpg"
    assert sources["twice"] is None
    # The folder words are searchable at once, without re-running the stage.
    assert fts_rows(conn) == {"shot": "screenshots screenshot gmail"}


# ---- Syncthing, read only ----


def syncthing(config: Config, requests: list[httpx.Request]) -> httpx.Client:
    browse = [
        {"name": "IMG_1.jpg", "type": "FILE_INFO_TYPE_FILE", "size": 10, "modTime": "x"},
        {
            "name": "Screenshots",
            "type": "FILE_INFO_TYPE_DIRECTORY",
            "modTime": "x",
            "children": [
                {"name": "Shot.png", "type": "FILE_INFO_TYPE_FILE", "size": 20, "modTime": "x"},
                {"name": "link", "type": "FILE_INFO_TYPE_SYMLINK", "size": 0, "modTime": "x"},
                {"name": "Empty", "type": "FILE_INFO_TYPE_DIRECTORY", "modTime": "x"},
            ],
        },
    ]
    folders = [
        {"id": "other", "path": "/somewhere/else"},
        {"id": "phone-id", "path": str(config.inboxes[0]) + "/"},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/rest/config/folders":
            return httpx.Response(200, json=folders)
        if request.url.path == "/rest/db/browse":
            assert request.url.params["folder"] == "phone-id"
            return httpx.Response(200, content=json.dumps(browse))
        return httpx.Response(404)

    return httpx.Client(base_url="http://syncthing.test", transport=httpx.MockTransport(handler))


def test_synced_files_reads_the_global_index(config: Config):
    requests: list[httpx.Request] = []
    client = syncthing(config, requests)
    folder = folder_id(client, config.inboxes[0])
    assert folder == "phone-id"
    assert synced_files(client, folder) == [("IMG_1.jpg", 10), ("Screenshots/Shot.png", 20)]
    assert {r.method for r in requests} == {"GET"}  # read only


def test_unknown_inbox_and_unexpected_format_fail_loudly(config: Config, tmp_path: Path):
    client = syncthing(config, [])
    with pytest.raises(LookupError, match="no Syncthing folder"):
        folder_id(client, tmp_path / "not-an-inbox")

    old_format = httpx.MockTransport(lambda _: httpx.Response(200, json={"a.jpg": ["x", 1]}))
    with pytest.raises(ValueError, match="db/browse format"):
        synced_files(httpx.Client(base_url="http://s.test", transport=old_format), "f")


def test_env_value_reads_the_env_file_and_names_a_missing_key(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text("IMGSEARCH_TEST_KEY=abc\n", encoding="utf-8")
    assert env_value("IMGSEARCH_TEST_KEY", env_file) == "abc"
    with pytest.raises(ValueError, match="IMGSEARCH_TEST_MISSING is not set"):
        env_value("IMGSEARCH_TEST_MISSING", env_file)
