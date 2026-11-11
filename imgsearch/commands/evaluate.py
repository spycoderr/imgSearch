"""`eval`, `eval-compare` and `eval-review` (M7); `bench-memory` and `check-query-vectors`
(P1.9)."""

import json
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Annotated

import httpx
import typer

from imgsearch.config import Config
from imgsearch.eval.main import evaluate, ollama_version, write_review
from imgsearch.eval.memory import compare_vectors, measure, read_queries
from imgsearch.eval.queries import counts, load_queries
from imgsearch.eval.run import git_state, load_metrics
from imgsearch.eval.tables import all_tables, compare_table
from imgsearch.search.hybrid import open_searcher
from imgsearch.store.db import connect


def eval_command(
    ctx: typer.Context,
    no_date_filter: Annotated[
        bool,
        typer.Option("--no-date-filter", help="Ignore dates found in the queries."),
    ] = False,
    place_filter: Annotated[
        bool | None,
        typer.Option(
            "--place-filter/--no-place-filter",
            help="Limit results to places found in the queries (default: search.place_filter).",
        ),
    ] = None,
) -> None:
    """Run every ablation on eval.queries and write a run folder with a contact sheet."""
    config: Config = ctx.obj
    with closing(connect(config)) as conn:
        run_dir = evaluate(
            config,
            ctx.meta["config_path"],
            conn,
            typer.echo,
            date_filter=not no_date_filter,
            place_filter=place_filter,
        )
    typer.echo("")
    for line in all_tables(load_metrics(run_dir)):
        typer.echo(line)
    typer.echo(f"\nrun: {run_dir}\ncontact sheet: {run_dir / 'contact_sheet.html'}")


def eval_compare(
    run_a: Annotated[Path, typer.Argument(help="Earlier run folder.")],
    run_b: Annotated[Path, typer.Argument(help="Later run folder.")],
) -> None:
    """Print the overall metrics of two eval runs side by side, with the change."""
    for line in compare_table(load_metrics(run_a), load_metrics(run_b), "A", "B"):
        typer.echo(line)
    typer.echo(f"A = {run_a}\nB = {run_b}")


def eval_review(ctx: typer.Context) -> None:
    """Check eval.queries and write an HTML sheet of each query's expected photos."""
    config: Config = ctx.obj
    with closing(connect(config)) as conn:
        typer.echo(counts(load_queries(config.eval.queries, conn)))
        typer.echo(f"review sheet: {write_review(config, conn)}")


def bench_memory(
    ctx: typer.Context,
    searches: Annotated[
        int | None,
        typer.Option(min=1, help="Searches to run, cycling the queries (default: one each)."),
    ] = None,
) -> None:
    """Measure memory and latency while searching, as the bot searches (P1.9).

    Unloads the rewriter from Ollama, loads the searcher with the config's defaults, runs the
    queries in eval.memory_queries, unloads it, and prints peak footprint, Ollama's resident
    memory, cold and warm latency, and memory before loading and after unloading. Writes
    eval/runs/memory-<time>.json. Run nothing else heavy meanwhile."""
    config: Config = ctx.obj
    queries = read_queries(config.eval.memory_queries)
    rewriter = config.models.rewriter
    started = datetime.now()
    with httpx.Client(timeout=rewriter.timeout_s) as client:
        report = measure(
            lambda: open_searcher(config, connect(config)),
            queries,
            searches or len(queries),
            client,
            rewriter.url,
            rewriter.name,
        )
    for line in report.lines():
        typer.echo(line)
    path = config.eval.runs_dir / f"memory-{started:%Y%m%d-%H%M%S}.json"
    meta = {
        "started": started.isoformat(timespec="seconds"),
        **git_state(),
        "embedder": config.models.embedder.model_dump(),
        "rewriter": rewriter.name,
        "num_ctx": rewriter.num_ctx,
        "ollama_version": ollama_version(rewriter.url),
        "default_signals": config.search.default_signals,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({**meta, **report.to_json()}, ensure_ascii=False, indent=1))
    typer.echo(f"\nsaved: {path}")


def check_query_vectors(
    ctx: typer.Context,
    run: Annotated[
        Path | None,
        typer.Option(help="Also embed the English rewrites in this eval run folder."),
    ] = None,
) -> None:
    """Compare search's query vectors with the full SigLIP model's (the one that embedded the
    images) on every eval query: lowest cosine and largest difference (P1.9). Also counts the
    queries whose token ids differ from the HF tokenizer's. Loads both models (~2.5 GB)."""
    from imgsearch.models.siglip import SiglipEmbedder, SiglipTextEmbedder, token_ids

    config: Config = ctx.obj
    with closing(connect(config)) as conn:
        texts = [q.query for q in load_queries(config.eval.queries, conn)]
    if run is not None:
        records = json.loads((run / "results.json").read_text(encoding="utf-8"))
        texts += [r["english"] for r in records]
    texts = list(dict.fromkeys(texts))
    settings = config.models.embedder
    full = SiglipEmbedder(settings)
    query = SiglipTextEmbedder(settings)
    differ = sum(
        full.processor(
            text=[t.lower()],
            padding="max_length",
            max_length=settings.text_max_length,
            truncation=True,
        )["input_ids"][0]
        != token_ids(query.tokenizer, t, settings.text_max_length)
        for t in texts
    )
    cosine, diff = compare_vectors(full, query, texts)
    typer.echo(
        f"{len(texts)} texts; query side {settings.query_device} {settings.query_dtype}"
        f" vs full model {settings.device} float32"
    )
    typer.echo(f"lowest cosine {cosine:.7f}; largest abs difference {diff:.2e}")
    typer.echo(f"token ids differing from the HF tokenizer's: {differ}")
