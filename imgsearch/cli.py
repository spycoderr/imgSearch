"""Command-line interface: `uv run imgsearch --help`."""

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Annotated

import typer

from imgsearch.config import DEFAULT_CONFIG_PATH, Config, load_config
from imgsearch.interfaces import Candidate, Context, ParsedQuery
from imgsearch.search.text import TextRetriever, best_snippet
from imgsearch.search.vector import VectorRetriever
from imgsearch.sources.local import LocalFolderSource
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


# ---- M3: generic stage runner and text search ----

# Stages runnable with `imgsearch run <name>`. Each is built as `cls(config)`.
# Ingest is not listed: `imgsearch ingest` also registers new files first.
STAGES = {
    "ocr": OcrStage,
    "embed": EmbedStage,
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


@app.command("search-text")
def search_text(
    ctx: typer.Context,
    query: Annotated[str, typer.Argument(help="Words to find in OCR text and captions.")],
    k: Annotated[int | None, typer.Option(help="Results to show (default search.top_k).")] = None,
) -> None:
    """Full-text search over OCR text and captions. The query is used as given (no rewrite)."""
    config: Config = ctx.obj
    q = ParsedQuery(english=query, original=query)
    with closing(connect(config)) as conn:
        candidates = TextRetriever(conn).retrieve(q, k or config.search.top_k)
        for c in candidates:
            relpath = conn.execute(
                "SELECT orig_relpath FROM images WHERE id = ?", (c.image_id,)
            ).fetchone()[0]
            typer.echo(f"{c.rank:>2}  {c.score:8.4f}  {relpath}")
            typer.echo(f"    {best_snippet(conn, c.image_id, q)}")
    if not candidates:
        typer.echo("no matches")


# ---- M2: vector search ----


@app.command("search-vec")
def search_vec(
    ctx: typer.Context,
    query: str,
    k: Annotated[int | None, typer.Option(help="Results to show (default: search.top_k).")] = None,
) -> None:
    """Vector-only search: embed QUERY as English text and print the nearest images."""
    from imgsearch.models.siglip import SiglipEmbedder  # loads torch; only this command needs it

    config: Config = ctx.obj
    embedder = SiglipEmbedder(config.models.embedder)
    with closing(connect(config)) as conn:
        retriever = VectorRetriever(conn, embedder, config.models.embedder)
        candidates = retriever.retrieve(
            ParsedQuery(english=query, original=query), k or config.search.top_k
        )
        for line in candidate_lines(conn, candidates):
            typer.echo(line)


def candidate_lines(conn: sqlite3.Connection, candidates: list[Candidate]) -> list[str]:
    """One line per candidate: rank, score and the original's relative path."""
    lines = []
    for c in candidates:
        row = conn.execute("SELECT orig_relpath FROM images WHERE id = ?", (c.image_id,)).fetchone()
        lines.append(f"{c.rank:>3}  {c.score:.4f}  {row['orig_relpath']}")
    return lines
