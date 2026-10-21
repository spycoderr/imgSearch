"""P1.6 cloud originals: encryption, Keychain, upload, update, evict, fetch, restore check.

S3 is the in-memory `store` fixture; the Keychain is a fake `security` runner.
"""

import hashlib
import os
import sqlite3
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from imgsearch.cli import app
from imgsearch.config import Config
from imgsearch.stages.update import update
from imgsearch.store.cache import evict, fetch
from imgsearch.store.crypto import HEADER_SIZE, MAGIC, DecryptError, decrypt, encrypt
from imgsearch.store.keychain import KeychainError, KeyMissing, create_key, read_key
from imgsearch.store.status import set_status
from imgsearch.store.upload import UploadResult, restore_check, upload
from imgsearch.store.verify import verify

KEY = bytes(range(32))  # a test key; never a real one
MTIME_NS = 1_600_000_000_123_456_789


def add_original(
    config: Config, conn: sqlite3.Connection, name: str, size: int = 1000, ingested: bool = True
) -> str:
    """Write a random original under originals_root and register it. Returns its id.

    Rows get increasing created_at in call order, so the first added is the least recently
    used.
    """
    path = config.originals_root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    data = os.urandom(size)
    path.write_bytes(data)
    os.utime(path, ns=(MTIME_NS, MTIME_NS))
    image_id = hashlib.sha256(data).hexdigest()
    n = conn.execute("SELECT COUNT(*) FROM images").fetchone()[0]
    with conn:
        conn.execute(
            "INSERT INTO images (id, orig_relpath, created_at) VALUES (?, ?, ?)",
            (image_id, name, f"2026-01-01T00:00:{n:02d}+00:00"),
        )
    if ingested:
        set_status(conn, image_id, "ingest", config.stages["ingest"], "done")
    return image_id


def copies(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    return {r["image_id"]: r for r in conn.execute("SELECT * FROM s3_copies")}


# ---- encryption ----


def test_encrypt_round_trip_with_versioned_header_and_fresh_nonce():
    a, b = encrypt(KEY, b"photo bytes"), encrypt(KEY, b"photo bytes")
    assert a[:4] == MAGIC and a[4] == 1
    assert a[5:HEADER_SIZE] != b[5:HEADER_SIZE]  # a new random nonce per object
    assert b"photo bytes" not in a
    assert decrypt(KEY, a) == decrypt(KEY, b) == b"photo bytes"


@pytest.mark.parametrize("position", [4, 10, HEADER_SIZE + 2, -1])  # version, nonce, body, tag
def test_any_changed_byte_fails_decryption(position: int):
    blob = bytearray(encrypt(KEY, b"photo bytes"))
    blob[position] ^= 1
    with pytest.raises(DecryptError):
        decrypt(KEY, bytes(blob))


def test_wrong_key_or_junk_fails_decryption():
    with pytest.raises(DecryptError):
        decrypt(bytes(32), encrypt(KEY, b"photo bytes"))
    with pytest.raises(DecryptError, match="not an imgsearch"):
        decrypt(KEY, b"junk")


# ---- Keychain (fake `security`) ----


class FakeSecurity:
    """Stands in for subprocess.run(["security", ...]) with one in-memory item store."""

    def __init__(self) -> None:
        self.items: dict[tuple[str, str], str] = {}
        self.argv: list[list[str]] = []

    def __call__(self, argv: list[str], input: str | None = None, **kwargs):
        self.argv.append(argv)
        opts = dict(zip(argv[2::2], argv[3::2], strict=False))
        item = (opts["-s"], opts["-a"])
        if argv[1] == "find-generic-password":
            if item not in self.items:
                return subprocess.CompletedProcess(argv, 44, "", "not found")
            return subprocess.CompletedProcess(argv, 0, self.items[item] + "\n", "")
        assert argv[1] == "add-generic-password" and argv[-1] == "-w"
        assert kwargs.get("start_new_session"), "must not prompt on a terminal"
        first, second = input.splitlines()
        assert first == second
        self.items[item] = first
        return subprocess.CompletedProcess(argv, 0, "", "")


def test_key_is_created_once_via_stdin_and_read_back(config: Config):
    security = FakeSecurity()
    with pytest.raises(KeyMissing, match="s3-key create"):
        read_key(config.s3, run=security)

    create_key(config.s3, run=security)
    key = read_key(config.s3, run=security)
    assert len(key) == 32
    assert all(key.hex() not in arg for argv in security.argv for arg in argv)

    with pytest.raises(KeychainError, match="already exists"):
        create_key(config.s3, run=security)
    assert read_key(config.s3, run=security) == key


def test_a_keychain_item_that_is_not_a_key_is_an_error(config: Config):
    security = FakeSecurity()
    security.items[(config.s3.keychain_service, config.s3.keychain_account)] = "abc"
    with pytest.raises(KeychainError, match="256-bit"):
        read_key(config.s3, run=security)


# ---- upload ----


def test_upload_encrypts_names_by_id_records_and_verifies(config: Config, conn, store):
    ids = [add_original(config, conn, f"2024/0{i}/IMG_{i}.jpg") for i in (1, 2, 3)]

    result = upload(config, conn, store, KEY)

    assert result.error is None and result.failed == []
    assert len(result.uploaded) == 3
    rows = copies(conn)
    for image_id in ids:
        key = f"originals/{image_id}"
        version, blob, _ = store.versions[key][-1]
        assert hashlib.sha256(decrypt(KEY, blob)).hexdigest() == image_id
        row = rows[image_id]
        assert (row["object_key"], row["version_id"], row["size"]) == (key, version, len(blob))
        assert row["encrypted_sha256"] == hashlib.sha256(blob).hexdigest()
        assert row["verified_at"] is not None and row["orig_mtime_ns"] == MTIME_NS
    assert not any("IMG_" in k or "2024" in k for k in store.versions)  # no names or dates

    db_version, db_blob, _ = store.versions[config.s3.db_key][-1]
    with sqlite3.connect(":memory:") as copy:
        copy.deserialize(decrypt(KEY, db_blob))
        assert copy.execute("SELECT COUNT(*) FROM s3_copies").fetchone()[0] == 3
    assert result.db.startswith("uploaded")


def test_upload_again_does_nothing(config: Config, conn, store):
    add_original(config, conn, "a.jpg")
    upload(config, conn, store, KEY)
    puts = store.puts

    again = upload(config, conn, store, KEY)

    assert again.uploaded == [] and again.failed == []
    assert again.db == "unchanged since the last copy"
    assert store.puts == puts


def test_one_failed_upload_does_not_stop_the_others(config: Config, conn, store):
    bad = add_original(config, conn, "a.jpg")
    good = add_original(config, conn, "b.jpg")
    changed = add_original(config, conn, "c.jpg")
    (config.originals_root / "c.jpg").write_bytes(b"edited")
    store.failing_keys.add(f"originals/{bad}")

    result = upload(config, conn, store, KEY)

    assert result.uploaded == ["b.jpg"]
    assert [relpath for relpath, _ in result.failed] == ["a.jpg", "c.jpg"]
    assert "original changed" in result.failed[1][1]
    assert set(copies(conn)) == {good}
    assert f"originals/{changed}" not in store.versions


def test_offline_upload_says_so_and_changes_nothing(config: Config, conn, store):
    add_original(config, conn, "a.jpg")
    store.offline = True

    result = upload(config, conn, store, KEY)

    assert result.error == "S3 unreachable: test is offline"
    assert result.uploaded == [] and result.db is None
    assert copies(conn) == {} and store.versions == {}


def test_a_changed_db_is_uploaded_as_a_new_version(config: Config, conn, store):
    add_original(config, conn, "a.jpg")
    upload(config, conn, store, KEY)
    add_original(config, conn, "b.jpg")
    upload(config, conn, store, KEY)
    assert len(store.versions[config.s3.db_key]) == 2


# ---- update ----


def test_update_uploads_after_import_and_keeps_indexing_when_offline(config: Config, conn, store):
    add_original(config, conn, "a.jpg", ingested=False)
    add_original(config, conn, "b.jpg", ingested=False)
    store.offline = True
    calls: list[str] = []

    class RecordingStage:
        name, version, requires = "ingest", config.stages["ingest"], []

        def load(self, ctx):
            calls.append("load")

        def run(self, image, ctx):
            calls.append(image.orig_relpath)

        def unload(self):
            pass

    def upload_step() -> UploadResult:
        calls.append("upload")
        return upload(config, conn, store, KEY)

    result = update(config, conn, [RecordingStage()], upload_step)

    assert calls[:2] == ["upload", "load"] and sorted(calls[2:]) == ["a.jpg", "b.jpg"]
    assert result.uploaded.error == "S3 unreachable: test is offline"
    assert result.not_in_s3 == 2
    assert result.stages["ingest"] == {"done": 2, "failed": 0}

    store.offline = False
    assert update(config, conn, [], upload_step).not_in_s3 == 0


def test_update_cli_without_a_key_still_indexes(config_path: Path, config: Config, conn):
    add_original(config, conn, "a.jpg", ingested=False)
    result = CliRunner().invoke(app, ["--config", str(config_path), "update"])
    # The original isn't a decodable image, so ingest fails it; the point is that it ran.
    assert "upload: not run: no encryption key" in result.output
    assert "1 originals not yet in S3" in result.output
    assert "ingest: 1 processed" in result.output


# ---- evict ----


def test_evict_dry_run_lists_least_recently_used_and_removes_nothing(config: Config, conn, store):
    names = [f"{c}.jpg" for c in "abcd"]
    for name in names:
        add_original(config, conn, name, size=1000)
    upload(config, conn, store, KEY)

    result = evict(config, conn, None, cap_gb=2500 / 1e9, dry_run=True)

    assert [r for r, _ in result.removed] == ["a.jpg", "b.jpg"]
    assert (result.before, result.after) == (4000, 2000)
    assert all((config.originals_root / n).exists() for n in names)
    assert conn.execute("SELECT COUNT(*) FROM original_cache").fetchone()[0] == 0


def test_evict_removes_only_safe_originals_and_keeps_rows(config: Config, conn, store):
    not_ingested = add_original(config, conn, "a.jpg", ingested=False)
    add_original(config, conn, "b.jpg")
    upload(config, conn, store, KEY)
    not_uploaded = add_original(config, conn, "c.jpg")
    add_original(config, conn, "d.jpg")
    store.failing_keys.add(f"originals/{not_uploaded}")
    upload(config, conn, store, KEY)
    images_before = conn.execute("SELECT * FROM images ORDER BY id").fetchall()

    result = evict(config, conn, store, cap_gb=0)

    assert [r for r, _ in result.removed] == ["b.jpg", "d.jpg"]
    assert result.not_safe == 2 and result.after == 2000
    assert sorted(p.name for p in config.originals_root.iterdir()) == ["a.jpg", "c.jpg"]
    assert conn.execute("SELECT * FROM images ORDER BY id").fetchall() == images_before
    evicted = conn.execute("SELECT COUNT(*) FROM original_cache WHERE evicted_at IS NOT NULL")
    assert evicted.fetchone()[0] == 2
    assert not_ingested in copies(conn)  # uploaded, but kept: its ingest isn't done


def test_evict_rechecks_s3_and_keeps_the_file_if_the_copy_differs(config: Config, conn, store):
    image_id = add_original(config, conn, "a.jpg")
    upload(config, conn, store, KEY)
    store.replace_bytes(f"originals/{image_id}", b"damaged")

    with pytest.raises(RuntimeError, match="not evicting"):
        evict(config, conn, store, cap_gb=0)
    assert (config.originals_root / "a.jpg").exists()


# ---- fetch ----


def test_fetch_restores_an_evicted_original_exactly(config: Config, conn, store):
    image_id = add_original(config, conn, "2024/05/a.jpg")
    upload(config, conn, store, KEY)
    evict(config, conn, store, cap_gb=0)
    path = config.originals_root / "2024/05/a.jpg"
    assert not path.exists()

    result = fetch(config, conn, store, KEY, "2024/05/a.jpg")

    assert result.downloaded and result.path == path
    assert hashlib.sha256(path.read_bytes()).hexdigest() == image_id
    assert path.stat().st_mtime_ns == MTIME_NS
    assert [p.name for p in path.parent.iterdir()] == ["a.jpg"]  # no temp file left
    row = conn.execute("SELECT * FROM original_cache WHERE image_id = ?", (image_id,)).fetchone()
    assert row["evicted_at"] is None

    again = fetch(config, conn, store, KEY, image_id)  # by id; already there
    assert not again.downloaded


def test_fetch_fails_loudly_on_a_tampered_object_and_writes_nothing(config: Config, conn, store):
    image_id = add_original(config, conn, "a.jpg")
    upload(config, conn, store, KEY)
    evict(config, conn, store, cap_gb=0)
    name = f"originals/{image_id}"

    store.replace_bytes(name, b"x" + store.versions[name][-1][1][1:])
    with pytest.raises(ValueError, match="recorded hash"):
        fetch(config, conn, store, KEY, image_id)

    # Even if the recorded hash matched, the decryption would catch it.
    with sqlite3.connect(config.db_path) as raw:
        blob = store.versions[name][-1][1]
        raw.execute(
            "UPDATE s3_copies SET encrypted_sha256 = ? WHERE image_id = ?",
            (hashlib.sha256(blob).hexdigest(), image_id),
        )
    raw.close()
    with pytest.raises(DecryptError):
        fetch(config, conn, store, KEY, image_id)
    assert list(config.originals_root.iterdir()) == []


def test_fetched_original_becomes_most_recently_used(config: Config, conn, store):
    a = add_original(config, conn, "a.jpg")
    add_original(config, conn, "b.jpg")
    upload(config, conn, store, KEY)
    fetch(config, conn, store, KEY, a)  # on the drive: only refreshes its access time

    result = evict(config, conn, None, cap_gb=1000 / 1e9, dry_run=True)
    assert [r for r, _ in result.removed] == ["b.jpg"]


def test_fetch_unknown_image_is_an_error(config: Config, conn, store):
    with pytest.raises(LookupError, match="no image"):
        fetch(config, conn, store, KEY, "nope.jpg")


# ---- restore check and status ----


def test_restore_check_reads_back_originals_and_the_db(config: Config, conn, store):
    ids = [add_original(config, conn, f"{c}.jpg") for c in "abc"]
    upload(config, conn, store, KEY)

    result = restore_check(config, conn, store, KEY, sample=10)
    assert sorted(result.checked) == ["a.jpg", "b.jpg", "c.jpg"] and result.failed == []
    assert "integrity_check ok, 3 images, 3 S3 copies" in result.db

    store.replace_bytes(f"originals/{ids[0]}", b"damaged")
    assert [r for r, _ in restore_check(config, conn, store, KEY, 10).failed] == ["a.jpg"]


def test_status_shows_s3_and_cache_numbers(config_path: Path, config: Config, conn, store):
    add_original(config, conn, "a.jpg", size=2_000_000)
    add_original(config, conn, "b.jpg", size=1_000_000)
    store.failing_keys.add(f"originals/{add_original(config, conn, 'c.jpg', size=500_000)}")
    upload(config, conn, store, KEY)
    evict(config, conn, store, cap_gb=0.002)

    output = CliRunner().invoke(app, ["--config", str(config_path), "status"]).output
    assert "s3: 2 in S3 (verified), 1 not yet uploaded" in output
    assert "cache: 1 evicted; originals on the pen drive 0.00 GB of 20.00 GB cap" in output


def test_migration_adds_the_s3_tables(conn):
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(s3_copies)")}
    assert columns == {
        "image_id", "object_key", "version_id", "encrypted_sha256", "size",
        "orig_mtime_ns", "uploaded_at", "verified_at",
    }  # fmt: skip
    cache = {r["name"] for r in conn.execute("PRAGMA table_info(original_cache)")}
    assert cache == {"image_id", "last_access", "evicted_at"}


def test_verify_skips_evicted_originals_but_checks_the_rest(
    config_path: Path, config: Config, conn, store
):
    a = add_original(config, conn, "a.jpg")
    add_original(config, conn, "b.jpg")
    upload(config, conn, store, KEY)
    evict(config, conn, store, cap_gb=1000 / 1e9)  # removes a.jpg

    result = verify(config, conn)
    assert result.evicted == 1
    problems = [p for _, p in result.problems]
    assert not any("original" in p for p in problems)  # a.jpg is evicted, b.jpg matches
    assert all(p.startswith("no derivative") or p.startswith("no thumbnail") for p in problems)

    fetch(config, conn, store, KEY, a)
    assert verify(config, conn).evicted == 0
