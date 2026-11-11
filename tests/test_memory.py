"""P1.9: the memory benchmark (`imgsearch bench-memory`)."""

import json
import sqlite3
from pathlib import Path

import httpx
import numpy as np
import pytest
from typer.testing import CliRunner

from imgsearch.config import PROJECT_ROOT, Config
from imgsearch.eval.memory import (
    compare_vectors,
    measure,
    ollama_models,
    process_memory,
    read_queries,
)

URL = "http://ollama.test"


def test_process_memory_sees_an_allocation() -> None:
    before = process_memory()
    block = bytearray(64 * 1024 * 1024)
    block[::4096] = b"x" * len(block[::4096])  # touch every page so it is resident
    after = process_memory()
    assert before.rss > 0
    assert after.footprint - before.footprint > 50e6
    assert after.peak_footprint >= after.footprint
    del block


def ollama_fake(calls: list[tuple[str, dict | None]], loaded: list[dict]) -> httpx.Client:
    """Answers /api/ps with `loaded` and records every request."""

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        calls.append((request.url.path, body))
        if request.url.path == "/api/ps":
            return httpx.Response(200, json={"models": loaded})
        return httpx.Response(200, json={"done": True})

    return httpx.Client(transport=httpx.MockTransport(handle))


def test_read_queries_skips_comments_and_blank_lines(tmp_path: Path) -> None:
    path = tmp_path / "q.txt"
    path.write_text("# fixed set\nwhite dog\n\n  सफेद कुत्ता  \n", encoding="utf-8")
    assert read_queries(path) == ["white dog", "सफेद कुत्ता"]


def test_ollama_models_keeps_size_and_context() -> None:
    loaded = [{"name": "m:7b", "size": 4_600_000_000, "size_vram": 1, "context_length": 4096}]
    models = ollama_models(ollama_fake([], [{**loaded[0], "digest": "x"}]), URL)
    assert models == loaded


def test_measure_unloads_the_rewriter_first_then_loads_searches_and_unloads(
    chat_library: sqlite3.Connection, make_searcher
) -> None:
    calls: list[tuple[str, dict | None]] = []
    loaded = [{"name": "m:7b", "size": 4_600_000_000, "size_vram": 1, "context_length": 2048}]
    built = []

    def build():
        built.append(make_searcher(chat_library))
        return built[-1]

    report = measure(build, ["a", "b", "c"], 7, ollama_fake(calls, loaded), URL, "m:7b")

    assert calls[:2] == [("/api/ps", None), ("/api/generate", {"model": "m:7b", "keep_alive": 0})]
    assert len(built) == 1
    assert built[0].rewriter.queries == ["a", "b", "c", "a", "b", "c", "a"]
    assert (report.searches, len(report.warm_s)) == (7, 6)
    assert report.rewriter_bytes == 4_600_000_000
    assert report.loaded.peak_footprint >= report.before.footprint
    with pytest.raises(sqlite3.ProgrammingError):  # the unload closed the searcher's DB
        chat_library.execute("SELECT 1")
    assert any("Total while searching" in line for line in report.lines())


def test_bench_memory_command_is_registered(config: Config, config_path: Path) -> None:
    from imgsearch.cli import app

    result = CliRunner().invoke(app, ["--config", str(config_path), "bench-memory", "--help"])
    assert result.exit_code == 0
    assert config.eval.memory_queries.name == "memory-queries.txt"
    assert len(read_queries(PROJECT_ROOT / "eval" / "memory-queries.txt")) == 10


class FixedEmbedder:
    def __init__(self, vectors: dict[str, list[float]]) -> None:
        self.vectors = vectors

    def embed_text(self, text: str) -> np.ndarray:
        vec = np.array(self.vectors[text], dtype=np.float32)
        return vec / np.linalg.norm(vec)


def test_compare_vectors_reports_the_worst_text() -> None:
    a = FixedEmbedder({"dog": [1, 0], "cat": [0, 1]})
    b = FixedEmbedder({"dog": [1, 0], "cat": [0.1, 1]})
    cosine, diff = compare_vectors(a, b, ["dog", "cat"])
    assert cosine == pytest.approx(1 / np.sqrt(1.01))
    assert diff == pytest.approx(0.1 / np.sqrt(1.01), rel=1e-5)
