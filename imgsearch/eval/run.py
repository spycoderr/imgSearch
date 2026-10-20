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
from imgsearch.search.hybrid import Searcher, open_searcher
from imgsearch.search.text import TextRetriever

OCR_ONLY = "text_ocr"  # retriever key for full-text search without captions


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
    Ablation("hybrid+rw no captions", ("vector", OCR_ONLY), True),
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
    """The real models, loaded once, plus an OCR-only text retriever for the no-captions run.
    Also returns the rewrite cache, which records each rewrite's time and result."""
    base = open_searcher(config, conn)
    retrievers = {**base.retrievers, OCR_ONLY: TextRetriever(conn, kinds=("ocr",))}
    rewriter = CachedRewriter(base.rewriter)
    return Searcher(conn, config.search, rewriter, retrievers), rewriter


def run_eval(
    searcher: Searcher,
    queries: list[EvalQuery],
    ablations: tuple[Ablation, ...] = ABLATIONS,
    progress: Callable[[EvalQuery, dict[str, dict]], None] | None = None,
) -> list[dict]:
    """One record per (query, ablation): what was searched, the top 5 and its metrics."""
    records = []
    for q in queries:
        done: dict[str, dict] = {}
        for ab in ablations:
            response = searcher.search(q.query, k=5, rewrite=ab.rewrite, signals=ab.signals)
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
