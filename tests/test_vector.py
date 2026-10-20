import sqlite3

import numpy as np
import pytest

from imgsearch.cli import candidate_lines
from imgsearch.config import Config, EmbedderConfig
from imgsearch.interfaces import ParsedQuery
from imgsearch.search.vector import VectorRetriever
from imgsearch.stages.embed import save_embedding


class FixedTextEmbedder:
    """Returns `vec` for any text and records the texts."""

    def __init__(self, vec: list[float]):
        self.vec = np.array(vec, dtype=np.float32)
        self.texts: list[str] = []

    def embed_images(self, paths):
        raise AssertionError("the retriever must not embed images")

    def embed_text(self, text: str) -> np.ndarray:
        self.texts.append(text)
        return self.vec


@pytest.fixture
def small(config: Config) -> EmbedderConfig:
    return config.models.embedder.model_copy(update={"dim": 2})


@pytest.fixture
def library(conn: sqlite3.Connection, add_image, small: EmbedderConfig) -> sqlite3.Connection:
    """Unit vectors at known angles from the query direction (1, 0)."""
    vectors = {"east": [1, 0], "northeast": [0.6, 0.8], "north": [0, 1], "west": [-1, 0]}
    for image_id, vec in vectors.items():
        add_image(image_id)
        save_embedding(conn, image_id, small.name, np.array(vec), small.dim)
    # Same images under another model: must be ignored.
    save_embedding(conn, "west", "other/model", np.array([1.0, 0.0]), 2)
    save_embedding(conn, "north", "other/model", np.array([1.0, 0.0]), 2)
    return conn


def query(text: str) -> ParsedQuery:
    return ParsedQuery(english=text, original="kuch aur")


def test_ranks_by_cosine_for_configured_model(library: sqlite3.Connection, small):
    embedder = FixedTextEmbedder([1.0, 0.0])
    got = VectorRetriever(library, embedder, small).retrieve(query("a dog"), k=10)

    assert embedder.texts == ["a dog"]  # english, not original
    assert [c.image_id for c in got] == ["east", "northeast", "north", "west"]
    assert [c.rank for c in got] == [1, 2, 3, 4]
    assert {c.signal for c in got} == {"vector"}
    np.testing.assert_allclose([c.score for c in got], [1.0, 0.6, 0.0, -1.0], atol=1e-6)


def test_k_limits_results(library: sqlite3.Connection, small):
    got = VectorRetriever(library, FixedTextEmbedder([0.0, 1.0]), small).retrieve(query("x"), k=2)
    assert [c.image_id for c in got] == ["north", "northeast"]


def test_wrong_query_length_raises(library: sqlite3.Connection, small):
    retriever = VectorRetriever(library, FixedTextEmbedder([1.0, 0.0, 0.0]), small)
    with pytest.raises(ValueError):
        retriever.retrieve(query("x"), k=5)


def test_candidate_lines_show_rank_score_and_relpath(library: sqlite3.Connection, small):
    got = VectorRetriever(library, FixedTextEmbedder([1.0, 0.0]), small).retrieve(query("x"), k=2)
    assert candidate_lines(library, got) == ["  1  1.0000  east.jpg", "  2  0.6000  northeast.jpg"]
