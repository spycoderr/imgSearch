"""Vector retriever: nearest image embeddings to the query's English text."""

import sqlite3

import numpy as np
import sqlite_vec

from imgsearch.config import EmbedderConfig
from imgsearch.interfaces import Candidate, Embedder, ParsedQuery
from imgsearch.search.filters import filter_sql

SIGNAL = "vector"


class VectorRetriever:
    """Implements `Retriever`. Ranks only vectors stored under the configured model name,
    and only images within `q.date_range` and `q.place_filter` when they are set.

    Score is cosine similarity (1 - cosine distance); rank 1 is the most similar.
    """

    def __init__(
        self, conn: sqlite3.Connection, embedder: Embedder, config: EmbedderConfig
    ) -> None:
        self.conn = conn
        self.embedder = embedder
        self.model = config.name
        self.dim = config.dim

    def retrieve(self, q: ParsedQuery, k: int) -> list[Candidate]:
        vec = self.embedder.embed_text(q.english)
        if vec.shape != (self.dim,):
            raise ValueError(f"expected a query vector of length {self.dim}, got {vec.shape}")
        in_range, range_params = filter_sql(q)
        rows = self.conn.execute(
            "SELECT image_id, vec_distance_cosine(vec, ?) AS distance FROM embeddings"
            f" WHERE model = ?{in_range} ORDER BY distance, image_id LIMIT ?",
            (
                sqlite_vec.serialize_float32(vec.astype(np.float32).tolist()),
                self.model,
                *range_params,
                k,
            ),
        ).fetchall()
        return [
            Candidate(image_id=row[0], rank=rank, score=1.0 - row[1], signal=SIGNAL)
            for rank, row in enumerate(rows, start=1)
        ]
