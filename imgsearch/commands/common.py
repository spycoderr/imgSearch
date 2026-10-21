"""Helpers shared by the command modules."""

import sqlite3

import typer

from imgsearch.config import Config
from imgsearch.store.roots import DriveNotMounted, require_mounted


def need_drive(config: Config, *keys: str) -> None:
    """Exit with a clear message if a root these commands need is on an unmounted drive."""
    try:
        require_mounted(config, *keys)
    except DriveNotMounted as e:
        typer.echo(f"{e}. Plug it in and try again.", err=True)
        raise typer.Exit(1) from None


def stage_line(name: str, counts: dict[str, int]) -> str:
    total = counts["done"] + counts["failed"]
    return f"{name}: {total} processed ({counts['done']} done, {counts['failed']} failed)"


def stage_failures(conn: sqlite3.Connection, stage_name: str) -> list[tuple[str, str]]:
    return conn.execute(
        "SELECT i.orig_relpath, s.error FROM stage_status s JOIN images i ON i.id = s.image_id"
        " WHERE s.stage = ? AND s.state = 'failed' ORDER BY i.orig_relpath",
        (stage_name,),
    ).fetchall()
