import json
import logging
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
import yaml
from typer.testing import CliRunner

from imgsearch.cli import app
from imgsearch.config import Config, RewriterConfig
from imgsearch.interfaces import ParsedQuery
from imgsearch.models.rewriter import OUTPUT_SCHEMA, OllamaRewriter

QUERY = "mummy ke saath Goa wali pizza ki photo"
GOOD = {
    "english": "photo of mom with pizza in Goa",
    "people": ["mummy"],
    "place": "Goa",
    "date_range": None,
}


def chat_reply(content: str) -> httpx.Response:
    return httpx.Response(200, json={"message": {"role": "assistant", "content": content}})


def make_rewriter(
    config: Config, handler: Callable[[httpx.Request], httpx.Response]
) -> OllamaRewriter:
    return OllamaRewriter(
        config.models.rewriter, client=httpx.Client(transport=httpx.MockTransport(handler))
    )


def test_request_shape(config: Config):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return chat_reply(json.dumps(GOOD))

    make_rewriter(config, handler).rewrite(QUERY)

    cfg: RewriterConfig = config.models.rewriter
    (request,) = seen
    assert request.method == "POST"
    assert str(request.url) == f"{cfg.url}/api/chat"
    body = json.loads(request.content)
    assert body["model"] == cfg.name
    assert body["stream"] is False
    assert body["options"] == {"temperature": 0}
    assert body["format"] == OUTPUT_SCHEMA
    assert body["messages"] == [
        {"role": "system", "content": cfg.prompt},
        {"role": "user", "content": QUERY},
    ]
    assert request.extensions["timeout"]["read"] == cfg.timeout_s


def test_parses_response(config: Config):
    reply = {
        **GOOD,
        "people": [" mummy ", ""],
        "place": " ",
        "date_range": {"start": "2019-01-01", "end": "2019-12-31"},
    }
    parsed = make_rewriter(config, lambda _: chat_reply(json.dumps(reply))).rewrite(QUERY)
    assert parsed == ParsedQuery(
        english="photo of mom with pizza in Goa",
        original=QUERY,
        people=["mummy"],
        date_range=("2019-01-01", "2019-12-31"),
        place=None,
    )


def test_original_is_the_input_not_the_model_output(config: Config):
    reply = {**GOOD, "original": "something else"}
    parsed = make_rewriter(config, lambda _: chat_reply(json.dumps(reply))).rewrite(QUERY)
    assert parsed.original == QUERY
    assert parsed.english == GOOD["english"]


def _raise(error: Exception) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        raise error

    return handler


FAILURES = {
    "connection error": _raise(httpx.ConnectError("connection refused")),
    "timeout": _raise(httpx.ReadTimeout("timed out")),
    "http 500": lambda _: httpx.Response(500, text="model not found"),
    "body not json": lambda _: httpx.Response(200, text="<html>"),
    "no message": lambda _: httpx.Response(200, json={"done": True}),
    "content not json": lambda _: chat_reply("Sure! The English is: photo of mom"),
    "schema mismatch": lambda _: chat_reply(json.dumps({**GOOD, "people": "mummy"})),
    "missing field": lambda _: chat_reply(json.dumps({"english": "photo of mom"})),
    "bad date": lambda _: chat_reply(
        json.dumps({**GOOD, "date_range": {"start": "last year", "end": "now"}})
    ),
    "empty english": lambda _: chat_reply(json.dumps({**GOOD, "english": "  "})),
}


@pytest.mark.parametrize("handler", FAILURES.values(), ids=FAILURES.keys())
def test_failure_falls_back_with_warning(config: Config, handler, caplog: pytest.LogCaptureFixture):
    with caplog.at_level(logging.WARNING, logger="imgsearch.models.rewriter"):
        parsed = make_rewriter(config, handler).rewrite(QUERY)
    assert parsed == ParsedQuery(english=QUERY, original=QUERY)
    assert "query rewrite failed" in caplog.text


def test_prompt_forbids_inventing_and_keeps_english(config: Config):
    prompt = config.models.rewriter.prompt
    assert "Never add people, places, dates, objects" in prompt
    assert "If the query is already in English, copy it exactly" in prompt


def test_examples_file_has_the_planned_mix():
    path = Path(__file__).resolve().parent.parent / "eval" / "rewrite_examples.yaml"
    queries = yaml.safe_load(path.read_text(encoding="utf-8"))["queries"]
    langs = [q["lang"] for q in queries]
    assert (langs.count("hinglish"), langs.count("hindi"), langs.count("english")) == (10, 5, 5)
    for q in queries:
        if q["lang"] == "english":
            assert q["expect"] == q["query"]


def test_cli_prints_json_and_time(config_path: Path):
    # Port 9 (discard) has no listener, so this exercises the fallback without Ollama.
    text = config_path.read_text(encoding="utf-8")
    config_path.write_text(text.replace("http://localhost:11434", "http://127.0.0.1:9"))
    result = CliRunner().invoke(app, ["--config", str(config_path), "rewrite", "kutta"])
    assert result.exit_code == 0, result.output
    json_part, _, timing = result.output.rpartition("}")
    assert json.loads(json_part + "}") == {
        "english": "kutta",
        "original": "kutta",
        "people": [],
        "date_range": None,
        "place": None,
    }
    assert timing.strip().startswith("took ")
