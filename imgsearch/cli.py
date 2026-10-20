"""Command-line interface: `uv run imgsearch --help`."""

import dataclasses
import json
import time
from contextlib import closing
from pathlib import Path
from typing import Annotated

import typer

from imgsearch.config import DEFAULT_CONFIG_PATH, Config, load_config
from imgsearch.interfaces import Context
from imgsearch.models.rewriter import OllamaRewriter
from imgsearch.search.hybrid import SIGNALS, open_searcher
from imgsearch.search.show import response_lines
from imgsearch.sources.local import LocalFolderSource
from imgsearch.stages.caption import CaptionStage
from imgsearch.stages.embed import EmbedStage
from imgsearch.stages.ingest import IngestStage, register_new
from imgsearch.stages.ocr import OcrStage
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
    stage = STAGES[stage_name](config)
    with closing(connect(config)) as conn:
        counts = run_stage(stage, Context(config=config, conn=conn))
        failures = conn.execute(
            "SELECT i.orig_relpath, s.error FROM stage_status s JOIN images i ON i.id = s.image_id"
            " WHERE s.stage = ? AND s.state = 'failed' ORDER BY i.orig_relpath",
            (stage.name,),
        ).fetchall()

    typer.echo(
        f"{stage.name}: {counts['done'] + counts['failed']} processed"
        f" ({counts['done']} done, {counts['failed']} failed)"
    )
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
        str, typer.Option(help="Comma-separated retrievers to use: vector, text or both.")
    ] = ",".join(SIGNALS),
) -> None:
    """Hybrid search: rewrite QUERY to English, run vector and full-text search, fuse with RRF."""
    config: Config = ctx.obj
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
