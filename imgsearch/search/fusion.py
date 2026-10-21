"""Reciprocal Rank Fusion (D2) over the rankings of several retrievers."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from imgsearch.interfaces import Candidate


@dataclass(frozen=True)
class Fused:
    """One image after fusion: its RRF score and its (rank, score) in each ranking it is in."""

    image_id: str
    score: float
    signals: dict[str, tuple[int, float]]


def rrf_fuse(
    rankings: Iterable[list[Candidate]], k: int, weights: Mapping[str, float] | None = None
) -> list[Fused]:
    """Fuse rankings with score = sum of weight / (k + rank) over the rankings an image is in.

    `weights` maps a signal (`Candidate.signal`) to its weight; a signal not in it weighs
    1.0. Best first. Equal scores are ordered by the best single rank, then by `image_id`.
    Each ranking holds one retriever's candidates; `Candidate.rank` is used (1 = best).
    """
    weights = weights or {}
    signals: dict[str, dict[str, tuple[int, float]]] = {}
    for ranking in rankings:
        for c in ranking:
            signals.setdefault(c.image_id, {})[c.signal] = (c.rank, c.score)

    fused = []
    for image_id, by_signal in signals.items():
        # Summing the terms in a fixed order makes equal sets of terms give bit-identical
        # scores.
        terms = sorted(weights.get(s, 1.0) / (k + rank) for s, (rank, _) in by_signal.items())
        best_rank = min(rank for rank, _ in by_signal.values())
        fused.append((Fused(image_id, sum(terms), by_signal), best_rank))
    # Rounding lets sums that are equal on paper but differ in the last bit count as a tie.
    fused.sort(key=lambda item: (-round(item[0].score, 12), item[1], item[0].image_id))
    return [f for f, _ in fused]
