"""Command-line interface: `uv run imgsearch --help`."""

import dataclasses
import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Annotated

import typer

from imgsearch.config import DEFAULT_CONFIG_PATH, Config, load_config
from imgsearch.eval.main import evaluate, write_review
from imgsearch.eval.queries import counts, load_queries
from imgsearch.eval.run import load_metrics
from imgsearch.eval.tables import all_tables, compare_table
from imgsearch.interfaces import Context
from imgsearch.models.rewriter import OllamaRewriter
from imgsearch.search.hybrid import SIGNALS, open_searcher
from imgsearch.search.show import response_lines
from imgsearch.sources.importer import ImportResult, import_inbox
from imgsearch.sources.local import LocalFolderSource
from imgsearch.stages.caption import CaptionStage
from imgsearch.stages.embed import EmbedStage
from imgsearch.stages.ingest import IngestStage, register_new
from imgsearch.stages.ocr import OcrStage
from imgsearch.stages.runner import run_stage
from imgsearch.stages.update import UPDATE_STAGES, update
from imgsearch.store.db import connect
from imgsearch.store.roots import DriveNotMounted, is_available, require_mounted
from imgsearch.store.status import status_counts
from imgsearch.store.verify import verify

app = typer.Typer(no_args_is_help=True, add_completion=False)

STATES = ["done", "failed", "running", "pending", "outdated"]
NO_ORIGINALS = "pen drive not mounted: full-size images are unavailable"


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


@app.callback()
def main(
    ctx: typer.Context,
    config: Annotated[Path, typer.Option(help="Path to config.yaml.")] = DEFAULT_CONFIG_PATH,
) -> None:
    """Personal image search."""
    ctx.obj = load_config(config)
    ctx.meta["config_path"] = config  # `eval` copies the file into its run folder


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
    drive = "mounted" if is_available(config.originals_root) else "NOT MOUNTED"
    typer.echo(f"pen drive: {drive} (originals: {config.originals_root})")


@app.command()
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


# ---- M3: generic stage runner ----

# Stages runnable with `imgsearch run <name>`. Each is built as `cls(config)`.
# Ingest is not listed: `imgsearch ingest` also registers new files first.
STAGES = {
    "ocr": OcrStage,
    "embed": EmbedStage,
    "caption": CaptionStage,
}


@app.command("run")
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


@app.command()
def rewrite(ctx: typer.Context, query: str) -> None:
    """Rewrite a query into English JSON with the rewrite LLM (falls back to the query as given)."""
    config: Config = ctx.obj
    start = time.perf_counter()
    parsed = OllamaRewriter(config.models.rewriter).rewrite(query)
    elapsed = time.perf_counter() - start
    typer.echo(json.dumps(dataclasses.asdict(parsed), ensure_ascii=False, indent=2))
    typer.echo(f"took {elapsed:.2f}s")


# ---- M5: hybrid search ----


@app.command()
def search(
    ctx: typer.Context,
    query: str,
    k: Annotated[int | None, typer.Option(help="Results to show (default search.top_k).")] = None,
    no_rewrite: Annotated[
        bool, typer.Option("--no-rewrite", help="Search the query as given.")
    ] = False,
    signals: Annotated[
        str | None,
        typer.Option(
            help="Comma-separated retrievers: vector, text, or vector,text for hybrid"
            " (default: search.default_signals in config.yaml)."
        ),
    ] = None,
) -> None:
    """Search: rewrite QUERY to English, run the chosen retrievers, fuse them with RRF."""
    config: Config = ctx.obj
    names = None
    if signals is not None:
        names = [s.strip() for s in signals.split(",") if s.strip()]
        if not names or any(s not in SIGNALS for s in names):
            raise typer.BadParameter(f"choose from: {', '.join(SIGNALS)}", param_hint="--signals")
    with closing(connect(config)) as conn:
        start = time.perf_counter()
        searcher = open_searcher(config, conn, signals=names, rewrite=not no_rewrite)
        loaded = time.perf_counter() - start
        response = searcher.search(query, k=k, rewrite=not no_rewrite)
    for line in response_lines(response):
        typer.echo(line)
    typer.echo(f"model load: {loaded:.2f}s")
    if not is_available(config.originals_root):
        typer.echo(f"note: {NO_ORIGINALS} (results come from the index on the Mac)")


# ---- M7: evaluation ----


@app.command("eval")
def eval_command(ctx: typer.Context) -> None:
    """Run every ablation on eval.queries and write a run folder with a contact sheet."""
    config: Config = ctx.obj
    with closing(connect(config)) as conn:
        run_dir = evaluate(config, ctx.meta["config_path"], conn, typer.echo)
    typer.echo("")
    for line in all_tables(load_metrics(run_dir)):
        typer.echo(line)
    typer.echo(f"\nrun: {run_dir}\ncontact sheet: {run_dir / 'contact_sheet.html'}")


@app.command("eval-compare")
def eval_compare(
    run_a: Annotated[Path, typer.Argument(help="Earlier run folder.")],
    run_b: Annotated[Path, typer.Argument(help="Later run folder.")],
) -> None:
    """Print the overall metrics of two eval runs side by side, with the change."""
    for line in compare_table(load_metrics(run_a), load_metrics(run_b), "A", "B"):
        typer.echo(line)
    typer.echo(f"A = {run_a}\nB = {run_b}")


@app.command("eval-review")
def eval_review(ctx: typer.Context) -> None:
    """Check eval.queries and write an HTML sheet of each query's expected photos."""
    config: Config = ctx.obj
    with closing(connect(config)) as conn:
        typer.echo(counts(load_queries(config.eval.queries, conn)))
        typer.echo(f"review sheet: {write_review(config, conn)}")


# ---- P1.5-A: import from the inbox, update, verify ----


def import_lines(result: ImportResult) -> list[str]:
    lines = [
        f"import: {len(result.imported)} imported, {result.duplicates} duplicates,"
        f" {result.too_new} skipped (too new), {len(result.failed)} failed"
    ]
    lines += [f"  imported: {relpath}" for relpath in result.imported]
    return lines


@app.command("import")
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


@app.command("update")
def update_command(ctx: typer.Context) -> None:
    """Import the inboxes, then run ingest, embed and ocr. Captions are not run."""
    config: Config = ctx.obj
    need_drive(config, "originals_root", "derivatives_root")
    stages = [cls(config) for cls in UPDATE_STAGES]
    with closing(connect(config)) as conn:
        result = update(config, conn, stages)
        failures = {stage.name: stage_failures(conn, stage.name) for stage in stages}

    for line in import_lines(result.imported):
        typer.echo(line)
    for path, error in result.imported.failed:
        typer.echo(f"  failed: {path}: {error}", err=True)
    for name, stage_counts in result.stages.items():
        typer.echo(stage_line(name, stage_counts))
        for relpath, error in failures[name]:
            typer.echo(f"  failed: {relpath}: {error}", err=True)
    if result.imported.failed or any(failures.values()):
        raise typer.Exit(1)


@app.command("verify")
def verify_command(ctx: typer.Context) -> None:
    """Check every image: original by SHA-256, derivative and thumbnail exist and decode."""
    config: Config = ctx.obj
    need_drive(config, "originals_root", "derivatives_root")
    with closing(connect(config)) as conn:
        result = verify(config, conn)
    for relpath, problem in result.problems:
        typer.echo(f"  {relpath}: {problem}", err=True)
    typer.echo(f"verify: {result.ok} of {result.images} images ok")
    if result.problems:
        raise typer.Exit(1)
