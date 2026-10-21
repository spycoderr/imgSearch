import sqlite3
from contextlib import closing
from pathlib import Path

import httpx
import numpy as np
import pytest
from typer.testing import CliRunner

from imgsearch.cli import app
from imgsearch.config import Config, load_config
from imgsearch.interfaces import Candidate, ParsedQuery
from imgsearch.models.rewriter import OllamaRewriter
from imgsearch.search.hybrid import Searcher, open_searcher
from imgsearch.search.show import response_lines, short
from imgsearch.search.text import TextRetriever
from imgsearch.search.vector import VectorRetriever
from imgsearch.stages.embed import save_embedding
from imgsearch.store.db import connect
from imgsearch.store.text import save_caption, save_ocr

QUERY = "kutte ki photo"
REWRITTEN = ParsedQuery(english="photo of a dog", original=QUERY)


class FakeRewriter:
    def __init__(self, parsed: ParsedQuery = REWRITTEN):
        self.parsed = parsed
        self.queries: list[str] = []

    def rewrite(self, query: str) -> ParsedQuery:
        self.queries.append(query)
        return self.parsed


class FakeRetriever:
    """Returns the given image ids ranked in order, and records each call."""

    def __init__(self, signal: str, *image_ids: str):
        self.signal = signal
        self.image_ids = image_ids
        self.calls: list[tuple[ParsedQuery, int]] = []

    def retrieve(self, q: ParsedQuery, k: int) -> list[Candidate]:
        self.calls.append((q, k))
        return [
            Candidate(image_id=i, rank=r, score=1.0 / r, signal=self.signal)
            for r, i in enumerate(self.image_ids[:k], start=1)
        ]


class FakeEmbedder:
    """Embeds any text as (1, 0) and records the texts."""

    def __init__(self):
        self.texts: list[str] = []

    def embed_images(self, paths):
        raise AssertionError("search must not embed images")

    def embed_text(self, text: str) -> np.ndarray:
        self.texts.append(text)
        return np.array([1.0, 0.0], dtype=np.float32)


@pytest.fixture
def images(conn: sqlite3.Connection, add_image) -> sqlite3.Connection:
    for image_id in ("dog", "cat", "sign", "menu"):
        add_image(image_id)
    save_caption(conn, "dog", "A brown dog sleeping on a sofa.", "m")
    save_caption(conn, "cat", "A cat on a window sill.", "m")
    save_ocr(conn, "sign", "स्वागत है\nWelcome")
    return conn


def fake_searcher(conn, config: Config, rewriter=None, **retrievers) -> Searcher:
    return Searcher(conn, config.search, rewriter or FakeRewriter(), retrievers)


def test_k_over_per_retriever_k_asks_each_retriever_for_k(images, config: Config):
    vector = FakeRetriever("vector", "dog", "cat", "sign", "menu")
    searcher = fake_searcher(images, config, FakeRewriter(), vector=vector)
    assert config.search.per_retriever_k == 10
    searcher.search(QUERY)  # default k: lists stay per_retriever_k long
    searcher.search(QUERY, k=20)
    assert [k for _, k in vector.calls] == [10, 20]


def test_query_flow(images, config: Config):
    rewriter = FakeRewriter()
    vector = FakeRetriever("vector", "cat", "dog", "menu")
    text = FakeRetriever("text", "dog", "sign")
    response = fake_searcher(images, config, rewriter, vector=vector, text=text).search(QUERY)

    assert rewriter.queries == [QUERY]
    per_k = config.search.per_retriever_k
    assert vector.calls == [(REWRITTEN, per_k)] and text.calls == [(REWRITTEN, per_k)]
    assert response.query == REWRITTEN and response.rewrite_used
    assert response.signals == ("vector", "text")
    assert [r.image_id for r in response.results] == ["dog", "cat", "sign", "menu"]

    dog = response.results[0]
    assert dog.signals == {"vector": (2, 0.5), "text": (1, 1.0)}
    assert dog.score == pytest.approx(1 / 62 + 1 / 61)
    assert (dog.orig_relpath, dog.caption) == ("dog.jpg", "A brown dog sleeping on a sofa.")
    assert response.results[2].caption is None  # sign has OCR text but no caption
    t = response.timings
    assert 0 <= t.rewrite_s <= t.total_s and 0 <= t.retrieval_s <= t.total_s


def test_top_k_default_and_override(images, config: Config):
    vector = FakeRetriever("vector", "cat", "dog", "menu", "sign")
    searcher = fake_searcher(images, config, vector=vector)
    assert len(searcher.search(QUERY).results) == min(4, config.search.top_k)
    assert [r.image_id for r in searcher.search(QUERY, k=2).results] == ["cat", "dog"]


def test_rewrite_off(images, config: Config):
    rewriter = FakeRewriter()
    vector = FakeRetriever("vector", "dog")
    response = fake_searcher(images, config, rewriter, vector=vector).search(QUERY, rewrite=False)
    assert rewriter.queries == []
    assert vector.calls[0][0] == ParsedQuery(english=QUERY, original=QUERY)
    assert not response.rewrite_used


def test_signals_switch(images, config: Config):
    vector = FakeRetriever("vector", "cat")
    text = FakeRetriever("text", "dog")
    searcher = fake_searcher(images, config, vector=vector, text=text)

    response = searcher.search(QUERY, signals=["text"])
    assert vector.calls == [] and len(text.calls) == 1
    assert response.signals == ("text",)
    assert [r.image_id for r in response.results] == ["dog"]

    with pytest.raises(ValueError, match="signals"):
        searcher.search(QUERY, signals=["faces"])
    with pytest.raises(ValueError, match="signals"):
        searcher.search(QUERY, signals=[])


def test_no_rewriter_needs_rewrite_off(images, config: Config):
    searcher = Searcher(images, config.search, None, {"text": FakeRetriever("text", "dog")})
    with pytest.raises(ValueError, match="rewrite=False"):
        searcher.search(QUERY)
    assert searcher.search(QUERY, rewrite=False).results[0].image_id == "dog"


def test_no_matches(images, config: Config):
    response = fake_searcher(images, config, text=FakeRetriever("text")).search(QUERY)
    assert response.results == []
    assert "no matches" in response_lines(response)


def test_real_retrievers_get_english_and_original(images, config: Config):
    small = config.models.embedder.model_copy(update={"dim": 2})
    for image_id, vec in {"dog": [0.6, 0.8], "cat": [1.0, 0.0]}.items():
        save_embedding(images, image_id, small.name, np.array(vec), small.dim)
    embedder = FakeEmbedder()
    parsed = ParsedQuery(english="a dog", original="स्वागत कुत्ता")
    searcher = fake_searcher(
        images,
        config,
        FakeRewriter(parsed),
        vector=VectorRetriever(images, embedder, small),
        text=TextRetriever(images),
    )
    response = searcher.search("स्वागत कुत्ता")

    assert embedder.texts == ["a dog"]  # the vector side embeds english only
    signals = {r.image_id: r.signals for r in response.results}
    assert signals["cat"].keys() == {"vector"}
    assert signals["dog"].keys() == {"vector", "text"}  # "dog" from english
    assert signals["sign"].keys() == {"text"}  # स्वागत from original
    assert [r.image_id for r in response.results] == ["dog", "cat", "sign"]


def test_rewriter_fallback_when_ollama_fails(images, config: Config):
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client = httpx.Client(transport=httpx.MockTransport(refuse))
    rewriter = OllamaRewriter(config.models.rewriter, client=client)
    searcher = Searcher(images, config.search, rewriter, {"text": TextRetriever(images)})
    response = searcher.search("dog")

    assert response.query.english == "dog" and response.rewrite_used
    assert [r.image_id for r in response.results] == ["dog"]
    assert response_lines(response)[0] == (
        "searched: dog  (rewrite skipped: ConnectError: connection refused)"
    )


def test_open_searcher_builds_only_what_is_asked(images, config: Config):
    searcher = open_searcher(config, images, signals=["text"], rewrite=False)
    assert list(searcher.retrievers) == ["text"] and searcher.rewriter is None
    with pytest.raises(ValueError, match="signals"):
        open_searcher(config, images, signals=["faces"])


def test_response_lines(images, config: Config):
    long_caption = "A brown dog " + "and more words " * 10
    save_caption(images, "dog", long_caption, "m")
    searcher = fake_searcher(
        images,
        config,
        vector=FakeRetriever("vector", "cat", "dog"),
        text=FakeRetriever("text", "dog"),
    )
    lines = response_lines(searcher.search(QUERY))
    assert lines[0] == f"searched: photo of a dog  (rewritten from: {QUERY})"
    assert lines[1] == "signals: vector, text"
    assert lines[2] == f" 1  {1 / 61 + 1 / 62:.4f}  dog.jpg  [text #1, vector #2]"
    assert lines[3] == f"    {short(long_caption)}"
    assert lines[3].endswith(" …") and len(lines[3]) < 110
    assert lines[4] == f" 2  {1 / 61:.4f}  cat.jpg  [vector #1]"
    assert lines[5] == "    A cat on a window sill."
    assert lines[6].startswith("time: rewrite ") and "retrieval" in lines[6]

    english = fake_searcher(
        images, config, FakeRewriter(ParsedQuery("dog", "dog")), text=FakeRetriever("text")
    )
    assert response_lines(english.search("dog"))[0] == "searched: dog  (rewrite left it unchanged)"
    off = response_lines(english.search("dog", rewrite=False))
    assert off[0] == "searched: dog  (rewrite off)"


def run_cli(config_path: Path, *args: str):
    return CliRunner().invoke(app, ["--config", str(config_path), "search", *args])


def test_cli_search_text_only(config_path: Path, images):
    result = run_cli(config_path, "welcome", "--signals", "text", "--no-rewrite", "--k", "3")
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0] == "searched: welcome  (rewrite off)"
    assert lines[1] == "signals: text"
    assert lines[2].split() == ["1", f"{1 / 61:.4f}", "sign.jpg", "[text", "#1]"]
    assert lines[3].strip() == "(no caption)"
    assert lines[4].startswith("time: rewrite 0.00s, retrieval ")
    assert lines[5].startswith("model load: ")


def test_cli_says_rewrite_was_skipped(config_path: Path, images):
    # Port 9 (discard) has no listener, so the rewrite fails without Ollama.
    text = config_path.read_text(encoding="utf-8")
    config_path.write_text(text.replace("http://localhost:11434", "http://127.0.0.1:9"))
    result = run_cli(config_path, "sofa", "--signals", "text")
    assert result.exit_code == 0, result.output
    assert "searched: sofa  (rewrite skipped: ConnectError" in result.output
    assert "dog.jpg" in result.output


def test_cli_rejects_unknown_signals(config_path: Path, images):
    result = run_cli(config_path, "dog", "--signals", "vector,faces")
    assert result.exit_code == 2
    assert "--signals" in result.output


def set_default_signals(config_path: Path, signals: str) -> Config:
    text = config_path.read_text(encoding="utf-8")
    assert "  default_signals: [vector]\n" in text
    config_path.write_text(
        text.replace("  default_signals: [vector]\n", f"  default_signals: {signals}\n")
    )
    return load_config(config_path)


def test_project_default_is_vector_with_k_10(config: Config):
    assert config.search.default_signals == ["vector"]
    assert config.search.per_retriever_k == 10


@pytest.mark.parametrize("bad", ["[]", "[vector, vector]", "[faces]"])
def test_default_signals_are_validated(config_path: Path, bad: str):
    with pytest.raises(ValueError, match="default_signals"):
        set_default_signals(config_path, bad)


def test_open_searcher_uses_the_config_default(config_path: Path):
    config = set_default_signals(config_path, "[text]")  # text: no model to load
    with closing(connect(config)) as conn:
        assert list(open_searcher(config, conn, rewrite=False).retrievers) == ["text"]


def test_cli_uses_the_config_default(config_path: Path, images):
    set_default_signals(config_path, "[text]")
    result = run_cli(config_path, "welcome", "--no-rewrite")
    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[1] == "signals: text"


def test_cli_signals_override_the_default(config_path: Path, images):
    # The project default is vector; --signals text must win (and so load no model).
    result = run_cli(config_path, "welcome", "--no-rewrite", "--signals", "text")
    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[1] == "signals: text"
    assert "sign.jpg" in result.output
