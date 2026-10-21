import json
import sqlite3
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from imgsearch.cli import app
from imgsearch.config import Config, load_config
from imgsearch.eval.main import evaluate
from imgsearch.eval.queries import EvalQuery
from imgsearch.eval.run import (
    ABLATIONS,
    NO_CAPTIONS,
    CachedRewriter,
    load_metrics,
    metrics_by_config,
    run_eval,
)
from imgsearch.eval.sheets import contact_sheet
from imgsearch.interfaces import Candidate, ParsedQuery
from imgsearch.search.hybrid import Searcher
from imgsearch.search.text import TextRetriever
from imgsearch.store.text import save_caption, save_ocr


class FakeRewriter:
    def __init__(self):
        self.calls: list[str] = []

    def rewrite(self, query: str) -> ParsedQuery:
        self.calls.append(query)
        return ParsedQuery(english=f"en: {query}", original=query)


class FakeRetriever:
    def __init__(self, signal: str, *image_ids: str):
        self.signal, self.image_ids = signal, image_ids
        self.calls: list[ParsedQuery] = []

    def retrieve(self, q: ParsedQuery, k: int) -> list[Candidate]:
        self.calls.append(q)
        return [Candidate(i, r, 1.0 / r, self.signal) for r, i in enumerate(self.image_ids, 1)]


QUERIES = [
    EvalQuery("q-dog", "kutta", "hinglish", "object", ("dog",), ""),
    EvalQuery("q-sign", "welcome sign", "english", "text", ("sign", "lost"), ""),
]


@pytest.fixture
def library(conn: sqlite3.Connection, add_image, config: Config) -> sqlite3.Connection:
    for image_id in ("dog", "cat", "sign", "lost"):  # no retriever returns "lost"
        add_image(image_id)
    # One image with a real thumbnail file, to check embedding.
    thumb = config.thumbs_root / "dog.jpg"
    thumb.parent.mkdir(parents=True)
    thumb.write_bytes(b"\xff\xd8fake-jpeg")
    with conn:
        conn.execute("UPDATE images SET thumb_path = 'dog.jpg' WHERE id = 'dog'")
    return conn


def fake_build(retrievers: dict | None = None):
    rewriter = CachedRewriter(FakeRewriter())

    def build(config: Config, conn: sqlite3.Connection):
        parts = retrievers or {
            "vector": FakeRetriever("vector", "dog", "cat"),
            "text": FakeRetriever("text", "sign", "dog"),
            NO_CAPTIONS: FakeRetriever("text", "sign"),
        }
        return Searcher(conn, config.search, rewriter, parts), rewriter

    return build


def test_run_eval_covers_every_ablation(library, config: Config):
    vector = FakeRetriever("vector", "dog", "cat")
    text = FakeRetriever("text", "sign", "dog")
    ocr = FakeRetriever("text", "sign")
    searcher, rewriter = fake_build({"vector": vector, "text": text, NO_CAPTIONS: ocr})(
        config, library
    )
    records = run_eval(searcher, QUERIES)

    assert len(records) == len(QUERIES) * len(ABLATIONS)
    assert rewriter.rewriter.calls == ["kutta", "welcome sign"]  # each query rewritten once
    by = {(r["id"], r["config"]): r for r in records}
    assert by["q-dog", "vector"]["english"] == "kutta"  # rewrite off
    assert by["q-dog", "vector+rw"]["english"] == "en: kutta"
    assert by["q-dog", "hybrid+rw"]["results"][0]["image_id"] == "dog"  # v1 + t2 beats t1
    assert by["q-dog", "hybrid+rw"]["results"][0]["signals"] == {
        "vector": [1, 1.0],
        "text": [2, 0.5],
    }
    assert (by["q-dog", "text"]["hit@5"], by["q-dog", "text"]["rr"]) == (1.0, 0.5)
    # Without captions only the OCR retriever runs on the text side.
    assert [r["image_id"] for r in by["q-sign", "hybrid+rw no captions"]["results"]] == [
        "dog", "sign", "cat",
    ]  # fmt: skip
    assert len(ocr.calls) == len(QUERIES)
    assert by["q-sign", "text"]["recall@5"] == 0.5

    metrics = metrics_by_config(records)
    assert list(metrics) == [a.name for a in ABLATIONS]
    assert metrics["vector"]["overall"]["n"] == 2
    assert set(metrics["vector"]["language"]) == {"english", "hinglish"}


def write_queries(config: Config) -> None:
    raw = [
        {"id": q.id, "query": q.query, "language": q.language, "type": q.type,
         "expected_image_ids": list(q.expected_image_ids), "note": q.note}
        for q in QUERIES
    ]  # fmt: skip
    config.eval.queries.parent.mkdir(parents=True, exist_ok=True)
    config.eval.queries.write_text(yaml.safe_dump({"queries": raw}), encoding="utf-8")


def no_ollama(config_path: Path) -> Config:
    # Port 9 (discard) has no listener, so the Ollama version lookup gets no answer.
    text = config_path.read_text(encoding="utf-8")
    config_path.write_text(text.replace("http://localhost:11434", "http://127.0.0.1:9"))
    return load_config(config_path)


def test_evaluate_writes_a_run_folder(library, config_path: Path):
    config = no_ollama(config_path)
    write_queries(config)
    lines: list[str] = []
    run_dir = evaluate(config, config_path, library, lines.append, build=fake_build())

    assert run_dir.parent == config.eval.runs_dir
    names = {p.name for p in run_dir.iterdir()}
    assert names == {"results.json", "metrics.json", "meta.json", "config.yaml",
                     "contact_sheet.html"}  # fmt: skip
    meta = json.loads((run_dir / "meta.json").read_text())
    assert len(meta["commit"]) == 40 and isinstance(meta["dirty"], bool)
    assert meta["queries"] == 2 and meta["ollama_version"] is None
    assert meta["rewrite_failures"] == 0
    assert (run_dir / "config.yaml").read_text() == config_path.read_text()
    assert len(json.loads((run_dir / "results.json").read_text())) == 2 * len(ABLATIONS)
    assert load_metrics(run_dir)["hybrid+rw"]["overall"]["n"] == 2
    assert lines[0].startswith("2 queries;") and lines[1].startswith("q-dog ")

    html = (run_dir / "contact_sheet.html").read_text()
    assert html.count("data:image/jpeg;base64,") == 1  # dog's thumbnail, embedded once
    assert "<h2>Overall</h2>" in html and "By language" in html
    assert "q-sign: welcome sign" in html and "rewrite: <b>en: welcome sign</b>" in html
    assert "lost.jpg · <b class='miss'>missed</b>" in html  # expected, never returned
    assert "<script" not in html and "http" not in html.split("<body>")[1]  # works offline


def test_contact_sheet_shows_a_skipped_rewrite(library, config: Config):
    record = {
        "config": "hybrid+rw", "id": "q", "query": "kutta", "language": "hinglish",
        "type": "object", "expected": ["dog"], "english": "kutta", "rewrite_used": True,
        "rewrite_error": "ConnectError: refused", "results": [], "hit@5": 0.0,
        "recall@5": 0.0, "rr": 0.0, "retrieval_s": 0.0,
    }  # fmt: skip
    meta = {"started": "t", "commit": "c" * 40, "dirty": False, "queries": 1, "elapsed_s": 1}
    html = contact_sheet([record], metrics_by_config([record]), meta, "", {"dog": "dog.jpg"})
    assert "rewrite skipped: ConnectError: refused" in html
    assert "no results" in html and "dog.jpg · <b class='miss'>missed</b>" in html


def test_cli_eval_review(library, config_path: Path):
    write_queries(load_config(config_path))
    result = CliRunner().invoke(app, ["--config", str(config_path), "eval-review"])
    assert result.exit_code == 0, result.output
    assert result.output.startswith("2 queries; english 1, hinglish 1, hindi 0;")
    path = Path(result.output.split("review sheet: ")[1].strip())
    html = path.read_text()
    assert "q-dog: kutta" in html and "sign.jpg" in html and "data:image/jpeg" in html


def test_cli_eval_review_reports_a_bad_query_set(library, config_path: Path):
    config = load_config(config_path)
    write_queries(config)
    config.eval.queries.write_text(config.eval.queries.read_text().replace("sign", "nope"))
    result = CliRunner().invoke(app, ["--config", str(config_path), "eval-review"])
    assert result.exit_code == 1
    assert "unknown image ids: nope" in str(result.exception)


def test_cli_eval_compare(tmp_path: Path, config_path: Path):
    def run(name: str, recall: float) -> Path:
        overall = {"n": 2, "hit@5": 1.0, "recall@5": recall, "rr": 0.5}
        folder = tmp_path / name
        folder.mkdir()
        metrics = {"hybrid+rw": {"overall": overall}, name: {"overall": overall}}
        (folder / "metrics.json").write_text(json.dumps(metrics))
        return folder

    a, b = run("a", 0.5), run("b", 0.75)
    result = CliRunner().invoke(app, ["--config", str(config_path), "eval-compare", str(a), str(b)])
    assert result.exit_code == 0, result.output
    row = next(line for line in result.output.splitlines() if line.startswith("| hybrid+rw"))
    assert "| 0.50 | 0.75 | +0.25 |" in row
    assert "(in one run only: a, b)" in result.output


def test_text_retriever_kinds(conn: sqlite3.Connection, add_image):
    for image_id in ("ocr", "cap"):
        add_image(image_id)
    save_ocr(conn, "ocr", "pizza menu")
    save_caption(conn, "cap", "a pizza on a plate", "m")
    q = ParsedQuery(english="pizza", original="pizza")
    assert {c.image_id for c in TextRetriever(conn).retrieve(q, 10)} == {"ocr", "cap"}
    assert [c.image_id for c in TextRetriever(conn, kinds=("ocr",)).retrieve(q, 10)] == ["ocr"]
    with pytest.raises(ValueError, match="kinds"):
        TextRetriever(conn, kinds=("faces",))
