"""P1.7 dates: the search date, filtering every retriever, the fallback and the switches."""

import json
import sqlite3
from pathlib import Path

import numpy as np
import pytest
from typer.testing import CliRunner

from imgsearch.cli import app
from imgsearch.config import Config, load_config
from imgsearch.eval.main import evaluate
from imgsearch.eval.queries import EvalQuery
from imgsearch.eval.run import CachedRewriter, run_eval
from imgsearch.interfaces import Candidate, ParsedQuery
from imgsearch.search.hybrid import Searcher
from imgsearch.search.show import response_lines
from imgsearch.search.text import TextRetriever
from imgsearch.search.vector import VectorRetriever
from imgsearch.stages.embed import save_embedding
from imgsearch.store.dates import SEARCH_DATE_SQL
from imgsearch.store.text import save_ocr

SEPT = ("2026-09-15", "2026-09-16")


@pytest.fixture
def dated(conn: sqlite3.Connection, add_image) -> sqlite3.Connection:
    """Five cake photos: dated by EXIF, by file date only, on the range's edges, and none."""
    dates = {
        "exif": ("2026-09-15T22:12:05", "2026-01-01T00:00:00"),  # taken_at wins
        "mtime": (None, "2026-09-16T23:59:59"),  # no EXIF: the file date counts
        "before": ("2026-09-14T23:59:59", None),
        "after": (None, "2026-09-17T00:00:00"),
        "undated": (None, None),
    }
    for image_id, (taken, mtime) in dates.items():
        add_image(image_id)
        with conn:
            conn.execute(
                "UPDATE images SET taken_at = ?, file_mtime = ? WHERE id = ?",
                (taken, mtime, image_id),
            )
        save_ocr(conn, image_id, "birthday cake")
    return conn


class FakeEmbedder:
    def embed_images(self, paths):
        raise AssertionError("search must not embed images")

    def embed_text(self, text: str) -> np.ndarray:
        return np.array([1.0, 0.0], dtype=np.float32)


class FakeRewriter:
    def __init__(self, date_range: tuple[str, str] | None = SEPT):
        self.date_range = date_range

    def rewrite(self, query: str) -> ParsedQuery:
        return ParsedQuery(english=query, original=query, date_range=self.date_range)


class RecordingRetriever:
    """Returns its ids regardless of dates, and records the date range it was given."""

    def __init__(self, *image_ids: str):
        self.image_ids = image_ids
        self.ranges: list[tuple[str, str] | None] = []

    def retrieve(self, q: ParsedQuery, k: int) -> list[Candidate]:
        self.ranges.append(q.date_range)
        return [Candidate(i, r, 1.0, "text") for r, i in enumerate(self.image_ids, 1)]


def ids(candidates) -> set[str]:
    return {c.image_id for c in candidates}


def test_search_date_is_taken_at_else_file_mtime(dated):
    rows = dated.execute(f"SELECT i.id, {SEARCH_DATE_SQL} FROM images i").fetchall()
    assert dict(rows) == {
        "exif": "2026-09-15T22:12:05",
        "mtime": "2026-09-16T23:59:59",
        "before": "2026-09-14T23:59:59",
        "after": "2026-09-17T00:00:00",
        "undated": None,
    }


def test_text_retriever_keeps_only_the_range_inclusive_days(dated):
    q = ParsedQuery(english="cake", original="cake")
    assert ids(TextRetriever(dated).retrieve(q, 10)) == {
        "exif",
        "mtime",
        "before",
        "after",
        "undated",
    }
    in_sept = ParsedQuery(english="cake", original="cake", date_range=SEPT)
    assert ids(TextRetriever(dated).retrieve(in_sept, 10)) == {"exif", "mtime"}


def test_vector_retriever_keeps_only_the_range(dated, config: Config):
    small = config.models.embedder.model_copy(update={"dim": 2})
    for image_id in ("exif", "mtime", "before", "after", "undated"):
        save_embedding(dated, image_id, small.name, np.array([1.0, 0.0]), small.dim)
    retriever = VectorRetriever(dated, FakeEmbedder(), small)
    q = ParsedQuery(english="cake", original="cake", date_range=SEPT)
    found = retriever.retrieve(q, 10)
    assert ids(found) == {"exif", "mtime"}
    assert [c.rank for c in found] == [1, 2]
    # The range is applied before the limit: k=1 still finds an image in the range.
    assert ids(retriever.retrieve(q, 1)) <= {"exif", "mtime"}


def test_searcher_filters_every_retriever(dated, config: Config):
    searcher = Searcher(dated, config.search, FakeRewriter(), {"text": TextRetriever(dated)})
    response = searcher.search("birthday cake")
    assert {r.image_id for r in response.results} == {"exif", "mtime"}
    assert response.date_filter == SEPT and not response.date_fallback
    assert response.query.date_range == SEPT
    assert "dates: 2026-09-15 to 2026-09-16" in response_lines(response)


def test_nothing_in_range_falls_back_to_all_dates(dated, config: Config):
    rewriter = FakeRewriter(("2020-01-01", "2020-01-31"))
    searcher = Searcher(dated, config.search, rewriter, {"text": TextRetriever(dated)})
    response = searcher.search("birthday cake")
    assert len(response.results) == 5
    assert response.date_fallback and response.date_filter is None
    assert response.query.date_range == ("2020-01-01", "2020-01-31")  # still shown
    assert (
        "dates: no photos from 2020-01-01 to 2020-01-31; showing results from all dates"
        in response_lines(response)
    )


def test_date_filter_off_ignores_the_range(dated, config: Config):
    retriever = RecordingRetriever("before", "exif")
    searcher = Searcher(dated, config.search, FakeRewriter(), {"text": retriever})
    off = searcher.search("birthday cake", date_filter=False)
    on = searcher.search("birthday cake")
    assert retriever.ranges == [None, SEPT]
    assert off.date_filter is None and not off.date_fallback
    assert on.date_filter == SEPT
    assert "dates: 2026-09-15 to 2026-09-16 found but not used (date filter off)" in (
        response_lines(off)
    )


def test_no_date_in_the_query_means_no_filter(dated, config: Config):
    retriever = RecordingRetriever("before")
    searcher = Searcher(dated, config.search, FakeRewriter(None), {"text": retriever})
    response = searcher.search("birthday cake")
    assert retriever.ranges == [None]
    assert response.date_filter is None and not response.date_fallback
    assert not any(line.startswith("dates:") for line in response_lines(response))


def test_searcher_uses_the_configured_weights(dated, config_path: Path):
    text = config_path.read_text(encoding="utf-8")
    config_path.write_text(text.replace("text: 1.0}", "text: 0.25}"), encoding="utf-8")
    config = load_config(config_path)
    assert config.search.signal_weights == {"vector": 1.0, "text": 0.25}
    retrievers = {
        "vector": _Fixed("vector", "exif", "mtime"),
        "text": _Fixed("text", "before"),
    }
    # Plain RRF would put "before" (text 1st) above "mtime" (vector 2nd).
    searcher = Searcher(dated, config.search, FakeRewriter(None), retrievers)
    assert [r.image_id for r in searcher.search("q", rewrite=False).results] == [
        "exif",
        "mtime",
        "before",
    ]


class _Fixed:
    def __init__(self, signal: str, *image_ids: str):
        self.signal, self.image_ids = signal, image_ids

    def retrieve(self, q: ParsedQuery, k: int) -> list[Candidate]:
        return [Candidate(i, r, 1.0, self.signal) for r, i in enumerate(self.image_ids, 1)]


@pytest.mark.parametrize("bad", ["{vector: 0}", "{vector: -1.0}", "{faces: 1.0}"])
def test_signal_weights_are_validated(config_path: Path, bad: str):
    text = config_path.read_text(encoding="utf-8")
    config_path.write_text(text.replace("{vector: 1.0, text: 1.0}", bad), encoding="utf-8")
    with pytest.raises(ValueError, match="signal_weights"):
        load_config(config_path)


# ---- the switches ----


def test_cli_search_accepts_no_date_filter(config_path: Path, dated):
    args = ["--config", str(config_path), "search", "cake", "--signals", "text", "--no-rewrite"]
    result = CliRunner().invoke(app, [*args, "--no-date-filter"])
    assert result.exit_code == 0, result.output
    assert "exif.jpg" in result.output


def eval_searcher(conn: sqlite3.Connection, config: Config):
    rewriter = CachedRewriter(FakeRewriter())
    retrievers = {
        "vector": TextRetriever(conn),
        "text": TextRetriever(conn),
        "text_no_captions": TextRetriever(conn, kinds=("ocr", "filename")),
    }
    return Searcher(conn, config.search, rewriter, retrievers), rewriter


CAKE = EvalQuery("q-cake", "birthday cake 15-16 sept", "english", "hard", ("before",), "")


def test_eval_records_the_parsed_range_and_can_switch_the_filter_off(dated, config: Config):
    searcher, _ = eval_searcher(dated, config)
    on = {r["config"]: r for r in run_eval(searcher, [CAKE])}
    off = {r["config"]: r for r in run_eval(searcher, [CAKE], date_filter=False)}

    assert on["text+rw"]["date_range"] == SEPT
    assert on["text+rw"]["date_filter"] == SEPT and not on["text+rw"]["date_fallback"]
    assert on["text+rw"]["hit@5"] == 0.0  # "before" is outside the range
    assert off["text+rw"]["date_range"] == SEPT and off["text+rw"]["date_filter"] is None
    assert off["text+rw"]["hit@5"] == 1.0
    assert on["text"]["date_range"] is None  # no rewrite, no date


def test_eval_meta_records_the_switch_and_weights(dated, config_path: Path):
    text = config_path.read_text(encoding="utf-8")
    config_path.write_text(text.replace("http://localhost:11434", "http://127.0.0.1:9"))
    config = load_config(config_path)  # port 9: no Ollama version lookup answers
    config.eval.queries.parent.mkdir(parents=True, exist_ok=True)
    config.eval.queries.write_text(
        "queries:\n  - {id: q-cake, query: cake, language: english, type: hard,"
        " expected_image_ids: [exif], note: ''}\n",
        encoding="utf-8",
    )
    run_dir = evaluate(
        config, config_path, dated, lambda _: None, build=eval_searcher_for, date_filter=False
    )
    meta = json.loads((run_dir / "meta.json").read_text())
    assert meta["date_filter"] is False
    assert meta["signal_weights"] == {"vector": 1.0, "text": 1.0}
    assert meta["per_retriever_k"] == config.search.per_retriever_k


def eval_searcher_for(config: Config, conn: sqlite3.Connection):
    return eval_searcher(conn, config)


def test_cli_eval_has_the_switch(config_path: Path):
    result = CliRunner().invoke(app, ["--config", str(config_path), "eval", "--help"])
    assert result.exit_code == 0 and "--no-date-filter" in result.output
