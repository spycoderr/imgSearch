"""Command-line interface: `uv run imgsearch --help`."""

from contextlib import closing
from pathlib import Path
from typing import Annotated

import typer

from imgsearch.config import DEFAULT_CONFIG_PATH, Config, load_config
from imgsearch.interfaces import Context
from imgsearch.sources.local import LocalFolderSource
from imgsearch.stages.ingest import IngestStage, register_new
from imgsearch.stages.runner import run_stage
from imgsearch.store.db import connect
from imgsearch.store.status import status_counts

app = typer.Typer(no_args_is_help=True, add_completion=False)

STATES = ["done", "failed", "running", "pending", "outdated"]


@app.callback()
def main(
    ctx: typer.Context,
    config: Annotated[Path, typer.Option(help="Path to config.yaml.")] = DEFAULT_CONFIG_PATH,
) -> None:
    """Personal image search."""
    ctx.obj = load_config(config)


@app.command()
def status(ctx: typer.Context) -> None:
    """Show image count and per-stage counts by state."""
    config: Config = ctx.obj
    conn = connect(config)
    total = conn.execute("SELECT COUNT(*) FROM images").fetchone()[0]
    counts = status_counts(conn, config.stages)
    conn.close()

    typer.echo(f"db: {config.db_path}")
    typer.echo(f"images: {total}")
    # Configured stages first, then any other stage that has rows.
    stages = list(config.stages) + [s for s in counts if s not in config.stages]
    typer.echo(f"{'stage':<10}" + "".join(f"{s:>9}" for s in [*STATES, "not run"]))
    for stage in stages:
        by_state = counts.get(stage, {})
        not_run = total - sum(by_state.values())
        cells = [by_state.get(s, 0) for s in STATES] + [not_run]
        typer.echo(f"{stage:<10}" + "".join(f"{n:>9}" for n in cells))


@app.command()
def ingest(ctx: typer.Context) -> None:
    """Register new photos under originals_root, then read EXIF and write derivatives."""
    config: Config = ctx.obj
    with closing(connect(config)) as conn:
        found = register_new(LocalFolderSource(config.originals_root), config.originals_root, conn)
        stage = IngestStage(config)
        counts = run_stage(stage, Context(config=config, conn=conn))
        failures = conn.execute(
            "SELECT i.orig_relpath, s.error FROM stage_status s JOIN images i ON i.id = s.image_id"
            " WHERE s.stage = ? AND s.state = 'failed' ORDER BY i.orig_relpath",
            (stage.name,),
        ).fetchall()

    typer.echo(
        f"register: {found.new} new, {found.known} already registered,"
        f" {found.duplicate} duplicates, {len(found.unreadable)} unreadable"
    )
    for relpath, error in found.unreadable:
        typer.echo(f"  unreadable: {relpath}: {error}", err=True)
    typer.echo(
        f"ingest: {counts['done'] + counts['failed']} processed"
        f" ({counts['done']} done, {counts['failed']} failed)"
    )
    for relpath, error in failures:
        typer.echo(f"  failed: {relpath}: {error}", err=True)
    if failures or found.unreadable:
        raise typer.Exit(1)
