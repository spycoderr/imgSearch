"""The search entry point: rewrite, retrieve with each signal, fuse with RRF, top k."""

import sqlite3
import time
from collections.abc import Collection
from dataclasses import dataclass, replace

from imgsearch.config import Config, SearchConfig
from imgsearch.interfaces import ParsedQuery, QueryRewriter, Result, Retriever
from imgsearch.models.rewriter import OllamaRewriter
from imgsearch.search import text, vector
from imgsearch.search.fusion import Fused, rrf_fuse

SIGNALS = (vector.SIGNAL, text.SIGNAL)


@dataclass(frozen=True)
class Timings:
    rewrite_s: float
    retrieval_s: float  # retrievers, fusion and the result lookup
    total_s: float


@dataclass(frozen=True)
class SearchResponse:
    """The results plus what was searched, so callers can show or log it."""

    query: ParsedQuery  # `english` went to the vector retriever, both texts to full-text
    rewrite_used: bool  # False when the rewrite was switched off
    signals: tuple[str, ...]  # the retrievers that ran
    results: list[Result]
    timings: Timings
    # The date range the results were filtered to (`query.date_range`), or None when the
    # query names no date, the filter was off, or nothing matched in the range.
    date_filter: tuple[str, str] | None = None
    # True when the range matched nothing and the results are from all dates instead.
    date_fallback: bool = False


class Searcher:
    """Built once (models load once), then `search()` is called for each query.

    `retrievers` maps a signal name to its retriever. `rewriter` may be None when every
    call passes `rewrite=False`.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        config: SearchConfig,
        rewriter: QueryRewriter | None,
        retrievers: dict[str, Retriever],
    ) -> None:
        self.conn = conn
        self.config = config
        self.rewriter = rewriter
        self.retrievers = retrievers

    def search(
        self,
        query: str,
        k: int | None = None,
        rewrite: bool = True,
        signals: Collection[str] | None = None,
        date_filter: bool = True,
    ) -> SearchResponse:
        """Top `k` (default `search.top_k`) for `query`. `rewrite` and `signals` are the
        switches for ablations; `signals=None` uses every retriever this searcher has.

        When the rewrite finds a date range, every retriever keeps only images dated in
        it. If none are, the search runs again without the range and says so
        (`date_fallback`). `date_filter=False` ignores the range."""
        names = tuple(self.retrievers) if signals is None else tuple(signals)
        unknown = [s for s in names if s not in self.retrievers]
        if not names or unknown:
            raise ValueError(f"signals must be some of {list(self.retrievers)}, got {names}")
        if rewrite and self.rewriter is None:
            raise ValueError("this searcher has no rewriter; pass rewrite=False")

        start = time.perf_counter()
        q = self.rewriter.rewrite(query) if rewrite else ParsedQuery(english=query, original=query)
        rewritten = time.perf_counter()

        k = k or self.config.top_k
        # Each retriever's list is per_retriever_k long, or k when more results are asked for
        # (the chat bot's `more`), so a search can return k results.
        depth = max(self.config.per_retriever_k, k)
        date_range = q.date_range if date_filter else None
        fused = self._fuse(replace(q, date_range=date_range), names, depth)
        fallback = not fused and date_range is not None
        if fallback:
            date_range = None
            fused = self._fuse(replace(q, date_range=None), names, depth)
        fused = fused[:k]
        rows = image_rows(self.conn, [f.image_id for f in fused])
        results = [
            Result(
                image_id=f.image_id,
                orig_relpath=rows[f.image_id]["orig_relpath"],
                derivative_path=rows[f.image_id]["derivative_path"],
                thumb_path=rows[f.image_id]["thumb_path"],
                caption=rows[f.image_id]["caption"],
                score=f.score,
                signals=f.signals,
            )
            for f in fused
        ]
        end = time.perf_counter()
        return SearchResponse(
            query=q,
            rewrite_used=rewrite,
            signals=names,
            results=results,
            timings=Timings(rewritten - start, end - rewritten, end - start),
            date_filter=date_range,
            date_fallback=fallback,
        )

    def _fuse(self, q: ParsedQuery, names: tuple[str, ...], depth: int) -> list[Fused]:
        rankings = [self.retrievers[name].retrieve(q, depth) for name in names]
        return rrf_fuse(rankings, self.config.rrf_k, self.config.signal_weights)


def image_rows(conn: sqlite3.Connection, image_ids: list[str]) -> dict[str, sqlite3.Row]:
    """Paths and caption for each image id."""
    marks = ",".join("?" * len(image_ids))
    rows = conn.execute(
        "SELECT i.id, i.orig_relpath, i.derivative_path, i.thumb_path, c.text AS caption"
        f" FROM images i LEFT JOIN captions c ON c.image_id = i.id WHERE i.id IN ({marks})",
        image_ids,
    )
    return {row["id"]: row for row in rows}


def open_searcher(
    config: Config,
    conn: sqlite3.Connection,
    signals: Collection[str] | None = None,
    rewrite: bool = True,
) -> Searcher:
    """A `Searcher` with the real models, building only what `signals` and `rewrite` need
    (so a text-only search loads no embedder and needs no Ollama). `signals=None` means
    `search.default_signals` from config, so every adapter gets the same default."""
    if signals is None:
        signals = config.search.default_signals
    unknown = [s for s in signals if s not in SIGNALS]
    if not signals or unknown:
        raise ValueError(f"signals must be some of {list(SIGNALS)}, got {list(signals)}")
    retrievers: dict[str, Retriever] = {}
    for name in SIGNALS:  # keeps a fixed order, whatever order `signals` has
        if name not in signals:
            continue
        if name == vector.SIGNAL:
            from imgsearch.models.siglip import SiglipEmbedder  # loads torch: import only here

            embedder = SiglipEmbedder(config.models.embedder)
            retrievers[name] = vector.VectorRetriever(conn, embedder, config.models.embedder)
        else:
            retrievers[name] = text.TextRetriever(conn)
    rewriter = OllamaRewriter(config.models.rewriter) if rewrite else None
    return Searcher(conn, config.search, rewriter, retrievers)
