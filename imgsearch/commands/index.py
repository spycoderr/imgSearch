"""Indexing commands: `status`, `ingest`, `run`, `import`, `update`, `verify`."""

from contextlib import closing
from typing import Annotated

import typer

from imgsearch.commands.cloud import (
    cloud_status_lines,
    db_copy_lines,
    run_upload,
    run_upload_db,
    upload_lines,
)
from imgsearch.commands.common import need_drive, stage_failures, stage_line
from imgsearch.config import Config
from imgsearch.interfaces import Context
from imgsearch.sources.importer import ImportResult, import_inbox
from imgsearch.sources.local import LocalFolderSource
from imgsearch.stages.caption import CaptionStage
from imgsearch.stages.embed import EmbedStage
from imgsearch.stages.filename import FilenameStage
from imgsearch.stages.ingest import IngestStage, register_new
from imgsearch.stages.ocr import OcrStage
from imgsearch.stages.runner import run_stage
from imgsearch.stages.update import UPDATE_STAGES, update
from imgsearch.store.db import connect
from imgsearch.store.roots import is_available
from imgsearch.store.status import status_counts
from imgsearch.store.verify import verify

STATES = ["done", "failed", "running", "pending", "outdated"]

# Stages runnable with `imgsearch run <name>`. Each is built as `cls(config)`.
# Ingest is not listed: `imgsearch ingest` also registers new files first.
STAGES = {
    "ocr": OcrStage,
    "embed": EmbedStage,
    "caption": CaptionStage,
    "filename": FilenameStage,
}


def status(ctx: typer.Context) -> None:
    """Show image count and per-stage counts by state."""
    config: Config = ctx.obj
    conn = connect(config)
    total = conn.execute("SELECT COUNT(*) FROM images").fetchone()[0]
    counts = status_counts(conn, config.stages)
    cloud = cloud_status_lines(config, conn)
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
    drive = "mounted" if is_available(config.originals_root) else "NOT MOUNTED"
    typer.echo(f"pen drive: {drive} (originals: {config.originals_root})")
    for line in cloud:
        typer.echo(line)


def ingest(ctx: typer.Context) -> None:
    """Register new photos under originals_root, then read EXIF and write derivatives."""
    config: Config = ctx.obj
    need_drive(config, "originals_root", "derivatives_root")
    with closing(connect(config)) as conn:
        found = register_new(LocalFolderSource(config.originals_root), config.originals_root, conn)
        stage = IngestStage(config)
        counts = run_stage(stage, Context(config=config, conn=conn))
        failures = stage_failures(conn, stage.name)

    typer.echo(
        f"register: {found.new} new, {found.known} already registered,"
        f" {found.duplicate} duplicates, {len(found.unreadable)} unreadable"
    )
    for relpath, error in found.unreadable:
        typer.echo(f"  unreadable: {relpath}: {error}", err=True)
    typer.echo(stage_line(stage.name, counts))
    for relpath, error in failures:
        typer.echo(f"  failed: {relpath}: {error}", err=True)
    if failures or found.unreadable:
        raise typer.Exit(1)


def run_command(
    ctx: typer.Context,
    stage_name: Annotated[str, typer.Argument(metavar="STAGE", help="Stage to run.")],
) -> None:
    """Run one stage on every image that needs it, then report done and failed counts."""
    config: Config = ctx.obj
    if stage_name not in STAGES:
        raise typer.BadParameter(
            f"unknown stage {stage_name!r}; choose from: {', '.join(STAGES)}",
            param_hint="STAGE",
        )
    need_drive(config, "derivatives_root")  # every stage reads the derivatives
    stage = STAGES[stage_name](config)
    with closing(connect(config)) as conn:
        counts = run_stage(stage, Context(config=config, conn=conn))
        failures = stage_failures(conn, stage.name)

    typer.echo(stage_line(stage.name, counts))
    for relpath, error in failures:
        typer.echo(f"  failed: {relpath}: {error}", err=True)
    if failures:
        raise typer.Exit(1)


def import_lines(result: ImportResult) -> list[str]:
    lines = [
        f"import: {len(result.imported)} imported, {result.duplicates} duplicates,"
        f" {result.too_new} skipped (too new), {len(result.failed)} failed"
    ]
    lines += [f"  imported: {relpath}" for relpath in result.imported]
    return lines


def import_command(ctx: typer.Context) -> None:
    """Move new photos from the inbox folders into originals_root (no indexing)."""
    config: Config = ctx.obj
    need_drive(config, "originals_root")
    with closing(connect(config)) as conn:
        result = import_inbox(config, conn)
    for line in import_lines(result):
        typer.echo(line)
    for path, error in result.failed:
        typer.echo(f"  failed: {path}: {error}", err=True)
    if result.failed:
        raise typer.Exit(1)


def update_command(ctx: typer.Context) -> None:
    """Import the inboxes, upload new originals to S3, run ingest, embed and ocr, then
    upload the DB copy.

    Indexing still runs if the upload can't (offline). Captions are not run.
    """
    config: Config = ctx.obj
    need_drive(config, "originals_root", "derivatives_root")
    stages = [cls(config) for cls in UPDATE_STAGES]
    with closing(connect(config)) as conn:
        result = update(
            config,
            conn,
            stages,
            lambda: run_upload(config, conn),
            lambda uploaded: run_upload_db(config, conn, uploaded),
        )
        failures = {stage.name: stage_failures(conn, stage.name) for stage in stages}

    for line in import_lines(result.imported):
        typer.echo(line)
    for path, error in result.imported.failed:
        typer.echo(f"  failed: {path}: {error}", err=True)
    for line in upload_lines(result.uploaded, result.not_in_s3):
        typer.echo(line)
    for relpath, error in result.uploaded.failed:
        typer.echo(f"  failed: {relpath}: {error}", err=True)
    for name, stage_counts in result.stages.items():
        typer.echo(stage_line(name, stage_counts))
        for relpath, error in failures[name]:
            typer.echo(f"  failed: {relpath}: {error}", err=True)
    for line in db_copy_lines(result.uploaded):  # uploaded last, after indexing
        typer.echo(line)
    # Offline is not an error here: the originals are uploaded on a later run.
    failed = result.imported.failed or result.uploaded.failed or result.uploaded.db_error
    if failed or any(failures.values()):
        raise typer.Exit(1)


def verify_command(ctx: typer.Context) -> None:
    """Check every image: original by SHA-256, derivative and thumbnail exist and decode."""
    config: Config = ctx.obj
    need_drive(config, "originals_root", "derivatives_root")
    with closing(connect(config)) as conn:
        result = verify(config, conn)
    for relpath, problem in result.problems:
        typer.echo(f"  {relpath}: {problem}", err=True)
    evicted = f" ({result.evicted} originals evicted to S3, not checked)" if result.evicted else ""
    typer.echo(f"verify: {result.ok} of {result.images} images ok{evicted}")
    if result.problems:
        raise typer.Exit(1)
