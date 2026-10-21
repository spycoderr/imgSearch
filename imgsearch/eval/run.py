"""Run every ablation over the query set with one Searcher, and save a run folder."""

import json
import shutil
import sqlite3
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from imgsearch.config import PROJECT_ROOT, Config
from imgsearch.eval.metrics import query_metrics, summarize
from imgsearch.eval.queries import EvalQuery
from imgsearch.interfaces import ParsedQuery, QueryRewriter
from imgsearch.search.hybrid import SIGNALS, Searcher, open_searcher
from imgsearch.search.text import TextRetriever

NO_CAPTIONS = "text_no_captions"  # retriever key for full-text search without captions


@dataclass(frozen=True)
class Ablation:
    name: str
    signals: tuple[str, ...]
    rewrite: bool


ABLATIONS = (
    Ablation("vector", ("vector",), False),
    Ablation("vector+rw", ("vector",), True),
    Ablation("text", ("text",), False),
    Ablation("text+rw", ("text",), True),
    Ablation("hybrid", ("vector", "text"), False),
    Ablation("hybrid+rw", ("vector", "text"), True),
    Ablation("hybrid+rw no captions", ("vector", NO_CAPTIONS), True),
)
MAIN = "hybrid+rw"  # the configuration `imgsearch search` uses; shown in detail


class CachedRewriter:
    """Rewrites each distinct query once. Ollama at temperature 0 is deterministic within a
    version, so this only saves time and keeps every ablation on the same rewrite."""

    def __init__(self, rewriter: QueryRewriter) -> None:
        self.rewriter = rewriter
        self.cache: dict[str, ParsedQuery] = {}
        self.seconds: dict[str, float] = {}  # time of the one real call per query

    def rewrite(self, query: str) -> ParsedQuery:
        if query not in self.cache:
            start = time.perf_counter()
            self.cache[query] = self.rewriter.rewrite(query)
            self.seconds[query] = time.perf_counter() - start
        return self.cache[query]


def eval_searcher(config: Config, conn: sqlite3.Connection) -> tuple[Searcher, CachedRewriter]:
    """The real models, loaded once, plus a text retriever without captions (OCR, file names
    and places) for the no-captions run.
    Also returns the rewrite cache, which records each rewrite's time and result."""
    base = open_searcher(config, conn, signals=SIGNALS)  # all of them, not the search default
    no_captions = TextRetriever(conn, kinds=("ocr", "filename", "place"))
    retrievers = {**base.retrievers, NO_CAPTIONS: no_captions}
    rewriter = CachedRewriter(base.rewriter)
    return Searcher(conn, config.search, rewriter, retrievers), rewriter


def run_eval(
    searcher: Searcher,
    queries: list[EvalQuery],
    ablations: tuple[Ablation, ...] = ABLATIONS,
    progress: Callable[[EvalQuery, dict[str, dict]], None] | None = None,
    date_filter: bool = True,
    place_filter: bool | None = None,
) -> list[dict]:
    """One record per (query, ablation): what was searched, the top 5 and its metrics.
    `date_filter=False` ignores dates found in the queries (to measure the filter);
    `place_filter` switches the place filter on or off (None: the searcher's config)."""
    records = []
    for q in queries:
        done: dict[str, dict] = {}
        for ab in ablations:
            response = searcher.search(
                q.query,
                k=5,
                rewrite=ab.rewrite,
                signals=ab.signals,
                date_filter=date_filter,
                place_filter=place_filter,
            )
            ranked = [r.image_id for r in response.results]
            done[ab.name] = {
                "config": ab.name,
                "id": q.id,
                "query": q.query,
                "language": q.language,
                "type": q.type,
                "expected": list(q.expected_image_ids),
                "english": response.query.english,
                "rewrite_used": response.rewrite_used,
                "rewrite_error": response.query.rewrite_error,
                "date_range": response.query.date_range,  # as parsed by the rewrite
                "date_filter": response.date_filter,  # the range the results were limited to
                "date_fallback": response.date_fallback,
                "place": response.query.place,  # as parsed by the rewrite
                "place_resolved": (
                    response.query.place_filter.label if response.query.place_filter else None
                ),
                "place_filter": response.place_filter,  # the place the results were limited to
                "place_fallback": response.place_fallback,
                "results": [
                    {
                        "image_id": r.image_id,
                        "orig_relpath": r.orig_relpath,
                        "score": r.score,
                        "signals": {s: list(v) for s, v in r.signals.items()},
                    }
                    for r in response.results
                ],
                "retrieval_s": response.timings.retrieval_s,
                **query_metrics(ranked, q.expected_image_ids),
            }
        records.extend(done.values())
        if progress:
            progress(q, done)
    return records


def metrics_by_config(records: list[dict]) -> dict[str, dict]:
    names = list(dict.fromkeys(r["config"] for r in records))
    return {name: summarize(r for r in records if r["config"] == name) for name in names}


def git_state() -> dict[str, object]:
    """The commit the run used, and whether the working tree had uncommitted changes."""

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=PROJECT_ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()

    return {"commit": git("rev-parse", "HEAD"), "dirty": bool(git("status", "--porcelain"))}


def write_run(
    run_dir: Path, records: list[dict], meta: dict, config_path: Path, sheet_html: str
) -> None:
    """results.json, metrics.json, meta.json, config.yaml and contact_sheet.html."""
    run_dir.mkdir(parents=True)
    dump = {"results.json": records, "metrics.json": metrics_by_config(records), "meta.json": meta}
    for name, data in dump.items():
        (run_dir / name).write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    shutil.copyfile(config_path, run_dir / "config.yaml")
    (run_dir / "contact_sheet.html").write_text(sheet_html, encoding="utf-8")


def load_metrics(run_dir: Path) -> dict[str, dict]:
    return json.loads((run_dir / "metrics.json").read_text(encoding="utf-8"))
