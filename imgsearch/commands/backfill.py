"""`backfill mtime` and `backfill folders`: fill P1.7's new columns for older photos."""

from contextlib import closing
from typing import Annotated

import httpx
import typer

from imgsearch.commands.common import need_drive
from imgsearch.config import Config, env_value
from imgsearch.sources.syncthing import folder_id, synced_files
from imgsearch.store.backfill import backfill_file_mtime, backfill_source_folders
from imgsearch.store.db import connect

backfill_app = typer.Typer(no_args_is_help=True, help="Fill new columns for older photos.")


def syncthing_client(config: Config) -> httpx.Client:
    return httpx.Client(
        base_url=config.syncthing.url,
        headers={"X-API-Key": env_value("SYNCTHING_API_KEY")},
        timeout=60,
    )


@backfill_app.command("mtime")
def mtime_command(ctx: typer.Context) -> None:
    """Record each original's file date (images.file_mtime) where it is missing."""
    config: Config = ctx.obj
    need_drive(config, "originals_root")
    with closing(connect(config)) as conn:
        result = backfill_file_mtime(config, conn)
    typer.echo(
        f"file_mtime: {result.updated} set, {result.evicted} skipped (evicted),"
        f" {len(result.missing)} skipped (original missing)"
    )
    for relpath in result.missing:
        typer.echo(f"  missing: {relpath}", err=True)


@backfill_app.command("folders")
def folders_command(
    ctx: typer.Context,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Report the matches without writing them.")
    ] = False,
) -> None:
    """Record which phone folder each older photo came from, read from Syncthing's index.

    Matches by file name and size; ambiguous matches are skipped. Read only: it never
    changes Syncthing's config or folders.
    """
    config: Config = ctx.obj
    need_drive(config, "originals_root")
    with syncthing_client(config) as client:
        synced = []
        for inbox in config.inboxes:
            synced += synced_files(client, folder_id(client, inbox))
    with closing(connect(config)) as conn:
        result = backfill_source_folders(config, conn, synced, dry_run=dry_run)
    typer.echo(
        f"source folders{' (dry run, nothing written)' if dry_run else ''}:"
        f" {len(result.updated)} matched, {result.ambiguous} ambiguous (skipped),"
        f" {result.unmatched} not in Syncthing, {result.evicted} skipped (evicted),"
        f" {len(result.missing)} skipped (original missing); {len(synced)} synced files"
    )
