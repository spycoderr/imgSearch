"""What `imgsearch eval` and `imgsearch eval-review` do: run and save, or write the review sheet."""

import hashlib
import sqlite3
import statistics
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import httpx

from imgsearch.config import Config
from imgsearch.eval.html import relpaths, thumbs_css
from imgsearch.eval.metrics import METRICS
from imgsearch.eval.queries import counts, load_queries
from imgsearch.eval.run import (
    ABLATIONS,
    MAIN,
    CachedRewriter,
    eval_searcher,
    git_state,
    metrics_by_config,
    run_eval,
    write_run,
)
from imgsearch.eval.sheets import contact_sheet, review_sheet
from imgsearch.search.hybrid import Searcher


def evaluate(
    config: Config,
    config_path: Path,
    conn: sqlite3.Connection,
    echo: Callable[[str], None],
    build: Callable[[Config, sqlite3.Connection], tuple[Searcher, CachedRewriter]] = eval_searcher,
    date_filter: bool = True,
) -> Path:
    """Run every ablation on the query set and write `runs_dir/<timestamp>/`. Returns it.
    `build` makes the searcher; tests pass one with fakes (the M3 test-injection convention).
    `date_filter=False` ignores dates found in the queries."""
    queries = load_queries(config.eval.queries, conn)
    echo(counts(queries))
    started = datetime.now()
    t0 = time.perf_counter()
    searcher, rewriter = build(config, conn)
    load_s = time.perf_counter() - t0

    def progress(q, done: dict[str, dict]) -> None:
        main = done[MAIN]
        echo(f"{q.id:<24} " + "  ".join(f"{m} {main[m]:.2f}" for m in METRICS))

    records = run_eval(searcher, queries, ABLATIONS, progress, date_filter)
    elapsed_s = time.perf_counter() - t0
    meta = {
        "started": started.isoformat(timespec="seconds"),
        **git_state(),
        "queries": len(queries),
        "queries_sha256": hashlib.sha256(config.eval.queries.read_bytes()).hexdigest(),
        "ablations": [a.name for a in ABLATIONS],
        "date_filter": date_filter,
        "signal_weights": config.search.signal_weights,
        "per_retriever_k": config.search.per_retriever_k,
        "embedder": config.models.embedder.name,
        "rewriter": config.models.rewriter.name,
        "ollama_version": ollama_version(config.models.rewriter.url),
        "rewrite_failures": sum(1 for p in rewriter.cache.values() if p.rewrite_error),
        "rewrite_s_median": statistics.median(rewriter.seconds.values()),
        "load_s": load_s,
        "elapsed_s": elapsed_s,
    }
    ids = {i for r in records for i in r["expected"]} | {
        res["image_id"] for r in records for res in r["results"]
    }
    css = thumbs_css(conn, config.thumbs_root, ids)
    sheet = contact_sheet(records, metrics_by_config(records), meta, css, relpaths(conn))
    run_dir = config.eval.runs_dir / started.strftime("%Y%m%d-%H%M%S")
    write_run(run_dir, records, meta, config_path, sheet)
    return run_dir


def ollama_version(url: str) -> str | None:
    """The server's version, recorded because rewrites change across Ollama versions (M6).
    None if Ollama can't be reached; the rewrites then fell back and meta counts them."""
    try:
        response = httpx.get(f"{url.rstrip('/')}/api/version", timeout=5)
        response.raise_for_status()
    except httpx.HTTPError:
        return None
    return response.json().get("version")


def write_review(config: Config, conn: sqlite3.Connection) -> Path:
    """The query review sheet, written to `runs_dir/query_review.html`. Returns its path."""
    queries = load_queries(config.eval.queries, conn)
    ids = {i for q in queries for i in q.expected_image_ids}
    css = thumbs_css(conn, config.thumbs_root, ids)
    html = review_sheet(queries, css, relpaths(conn), counts(queries))
    path = config.eval.runs_dir / "query_review.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return path
