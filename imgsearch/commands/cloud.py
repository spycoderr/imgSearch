"""Cloud originals (plan D19, P1.6): `upload`, `evict`, `fetch`, `restore-check`, `s3-key`."""

import sqlite3
from contextlib import closing
from typing import Annotated

import typer

from imgsearch.commands.common import need_drive
from imgsearch.config import Config
from imgsearch.interfaces import ObjectStore, StoreUnavailable
from imgsearch.store.cache import GB, cached_originals, cloud_counts, evict, fetch, open_cloud
from imgsearch.store.db import connect
from imgsearch.store.keychain import KeychainError, KeyMissing, create_key, read_key
from imgsearch.store.roots import is_available
from imgsearch.store.upload import (
    UploadResult,
    not_in_s3,
    restore_check,
    upload_db_copy,
    upload_originals,
)

MB = 1e6
PROGRESS_EVERY = 50

key_app = typer.Typer(no_args_is_help=True, help="Create or export the encryption key.")


def open_key_and_store(config: Config) -> tuple[bytes, ObjectStore]:
    """The key and the store, or exit 1 with the reason."""
    try:
        return open_cloud(config)
    except (KeyMissing, KeychainError, StoreUnavailable) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from None


def run_upload(config: Config, conn: sqlite3.Connection) -> UploadResult:
    """Upload the originals. A missing key, or S3 not configured or reachable, comes back
    as `error`; the DB copy is a separate step (`run_upload_db`)."""
    try:
        key, store = open_cloud(config)
    except (KeyMissing, KeychainError, StoreUnavailable) as e:
        return UploadResult(error=str(e))
    todo = len(not_in_s3(conn))

    def progress(done: int) -> None:
        if done % PROGRESS_EVERY == 0:
            typer.echo(f"  {done} of {todo} originals uploaded", err=True)

    return upload_originals(config, conn, store, key, progress)


def run_upload_db(config: Config, conn: sqlite3.Connection, result: UploadResult) -> None:
    """Upload the DB copy into `result`, unless the originals' upload couldn't reach S3."""
    if result.error is not None:
        return
    try:
        key, store = open_cloud(config)
    except (KeyMissing, KeychainError, StoreUnavailable) as e:
        result.db_error = str(e)
        return
    upload_db_copy(config, conn, store, key, result)


def upload_lines(result: UploadResult, pending: int) -> list[str]:
    left = f"{pending} originals not yet in S3"
    if result.error and not result.uploaded and not result.failed:
        return [f"upload skipped: {result.error}; {left}"]
    lines = [
        f"upload: {len(result.uploaded)} uploaded ({result.uploaded_bytes / MB:.1f} MB),"
        f" {len(result.failed)} failed; {left}"
    ]
    if result.error:
        lines.append(f"upload: stopped early: {result.error}")
    return lines


def db_copy_lines(result: UploadResult) -> list[str]:
    if result.db_error is not None:
        return [f"db copy: FAILED: {result.db_error}"]
    return [] if result.db is None else [f"db copy: {result.db}"]


def upload_command(ctx: typer.Context) -> None:
    """Encrypt and upload every original not yet in S3, then an encrypted copy of the DB."""
    config: Config = ctx.obj
    need_drive(config, "originals_root")
    with closing(connect(config)) as conn:
        result = run_upload(config, conn)
        run_upload_db(config, conn, result)
        pending = len(not_in_s3(conn))
    for line in upload_lines(result, pending) + db_copy_lines(result):
        typer.echo(line)
    for relpath, error in result.failed:
        typer.echo(f"  failed: {relpath}: {error}", err=True)
    if result.error or result.failed or result.db_error:
        raise typer.Exit(1)


def evict_command(
    ctx: typer.Context,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Only list what would go.")] = False,
    cap_gb: Annotated[
        float | None, typer.Option(help="Cap in GB (default storage.cache_max_gb).")
    ] = None,
) -> None:
    """Remove least recently used originals from the pen drive until under the cap.

    Only originals with a verified S3 copy and a finished ingest are removed.
    """
    config: Config = ctx.obj
    need_drive(config, "originals_root")
    cap = config.storage.cache_max_gb if cap_gb is None else cap_gb
    store = None if dry_run else open_key_and_store(config)[1]
    with closing(connect(config)) as conn:
        result = evict(config, conn, store, cap, dry_run)
    verb = "would remove" if dry_run else "removed"
    typer.echo(
        f"evict{' (dry run)' if dry_run else ''}: {verb} {len(result.removed)} originals"
        f" ({sum(s for _, s in result.removed) / MB:.1f} MB); originals on the pen drive"
        f" {result.before / MB:.1f} MB -> {result.after / MB:.1f} MB, cap {result.cap / MB:.1f} MB"
    )
    for relpath, size in result.removed:
        typer.echo(f"  {verb}: {relpath} ({size / MB:.1f} MB)")
    if result.error is not None:
        typer.echo(f"evict: stopped early: {result.error}", err=True)
        raise typer.Exit(1)
    if result.after > result.cap:
        typer.echo(
            f"still over the cap: {result.not_safe} originals are not safe to evict yet"
            " (no verified S3 copy, or ingest not done)"
        )


def fetch_command(
    ctx: typer.Context,
    image: Annotated[str, typer.Argument(help="Image id or path relative to originals_root.")],
) -> None:
    """Print the original's path, first restoring it from S3 if it was evicted."""
    config: Config = ctx.obj
    need_drive(config, "originals_root")
    key, store = open_key_and_store(config)
    with closing(connect(config)) as conn:
        try:
            result = fetch(config, conn, store, key, image)
        except (LookupError, StoreUnavailable) as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(1) from None
    typer.echo(str(result.path))
    typer.echo(
        "downloaded from S3, decrypted and hash-checked"
        if result.downloaded
        else "already on the pen drive"
    )


def restore_check_command(
    ctx: typer.Context,
    sample: Annotated[int, typer.Option(help="Random originals to check.")] = 10,
) -> None:
    """Download, decrypt and hash-check random originals and the DB copy (writes nothing)."""
    config: Config = ctx.obj
    key, store = open_key_and_store(config)
    with closing(connect(config)) as conn:
        result = restore_check(config, conn, store, key, sample)
    for relpath in result.checked:
        typer.echo(f"  ok: {relpath}")
    for relpath, error in result.failed:
        typer.echo(f"  FAILED: {relpath}: {error}", err=True)
    typer.echo(f"originals: {len(result.checked)} of {result.sampled} match their image id")
    if result.error is not None:
        typer.echo(f"restore-check: stopped early: {result.error}", err=True)
        raise typer.Exit(1)
    typer.echo(f"db copy: {result.db}")
    if result.failed:
        raise typer.Exit(1)


def cloud_status_lines(config: Config, conn: sqlite3.Connection) -> list[str]:
    counts = cloud_counts(conn)
    lines = [f"s3: {counts.in_s3} in S3 (verified), {counts.not_uploaded} not yet uploaded"]
    cache = f"cache: {counts.evicted} evicted; originals on the pen drive"
    if is_available(config.originals_root):
        size = sum(s for _, s in cached_originals(config, conn))
        cache += f" {size / GB:.2f} GB of {config.storage.cache_max_gb:.2f} GB cap"
    else:
        cache += " unknown (not mounted)"
    return lines + [cache]


@key_app.command("create")
def key_create(ctx: typer.Context) -> None:
    """Make the 256-bit encryption key in the macOS Keychain (once; refuses if it exists)."""
    config: Config = ctx.obj
    try:
        create_key(config.s3)
    except KeychainError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from None
    typer.echo(
        f"created: encryption key stored in the Keychain ({config.s3.keychain_service}).\n"
        "Back it up now: run `uv run imgsearch s3-key export` in your own Terminal and save"
        " it in a password manager. Without it, the S3 copies can't be decrypted."
    )


@key_app.command("export")
def key_export(ctx: typer.Context) -> None:
    """Print the key so you can save it in a password manager. Run it in your own Terminal."""
    config: Config = ctx.obj
    try:
        key = read_key(config.s3)
    except (KeyMissing, KeychainError) as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from None
    typer.echo(key.hex())
    typer.echo(
        "Save this in a password manager, then clear the screen. Anyone with it and the"
        " bucket can read the photos; without it nobody can, including you.",
        err=True,
    )
