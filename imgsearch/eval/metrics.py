"""Retrieval metrics over one ranked list, and their means by group. Pure functions."""

from collections.abc import Iterable, Sequence
from statistics import mean

K = 5
METRICS = ("hit@5", "recall@5", "rr")


def hit_at_k(ranked: Sequence[str], expected: Iterable[str], k: int = K) -> float:
    """1.0 if any expected id is in the top `k`, else 0.0."""
    return 1.0 if set(ranked[:k]) & set(expected) else 0.0


def recall_at_k(ranked: Sequence[str], expected: Iterable[str], k: int = K) -> float:
    """Expected ids found in the top `k`, divided by min(k, number expected), so a query
    with more than `k` expected images can still score 1.0."""
    wanted = set(expected)
    if not wanted:
        raise ValueError("expected must not be empty")
    return len(set(ranked[:k]) & wanted) / min(k, len(wanted))


def reciprocal_rank(ranked: Sequence[str], expected: Iterable[str], k: int = K) -> float:
    """1 / rank of the first expected id in the top `k` (rank 1 = best), or 0.0 if none."""
    wanted = set(expected)
    for rank, image_id in enumerate(ranked[:k], start=1):
        if image_id in wanted:
            return 1.0 / rank
    return 0.0


def query_metrics(ranked: Sequence[str], expected: Iterable[str]) -> dict[str, float]:
    expected = list(expected)
    return {
        "hit@5": hit_at_k(ranked, expected),
        "recall@5": recall_at_k(ranked, expected),
        "rr": reciprocal_rank(ranked, expected),
    }


def summarize(rows: Iterable[dict], groups: Sequence[str] = ("language", "type")) -> dict:
    """Means of METRICS over `rows` (each has the metric keys and the group keys):
    {"overall": {...}, "language": {"english": {...}, ...}, "type": {...}}.
    Each mean dict also has "n", the number of queries in it."""
    rows = list(rows)
    out: dict = {"overall": _means(rows)}
    for group in groups:
        values = sorted({row[group] for row in rows})
        out[group] = {v: _means([r for r in rows if r[group] == v]) for v in values}
    return out


def _means(rows: list[dict]) -> dict[str, float]:
    if not rows:
        return {"n": 0, **{m: 0.0 for m in METRICS}}
    return {"n": len(rows), **{m: mean(row[m] for row in rows) for m in METRICS}}
