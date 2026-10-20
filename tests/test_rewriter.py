import json
import logging
from collections.abc import Callable
from datetime import date
from pathlib import Path

import httpx
import pytest
import yaml
from typer.testing import CliRunner

from imgsearch.cli import app
from imgsearch.config import Config, RewriterConfig
from imgsearch.interfaces import ParsedQuery
from imgsearch.models.rewriter import OUTPUT_SCHEMA, OllamaRewriter, mentions_date, system_prompt

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
        {"role": "system", "content": system_prompt(cfg.prompt)},
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
    query = QUERY + " 2019"
    parsed = make_rewriter(config, lambda _: chat_reply(json.dumps(reply))).rewrite(query)
    assert parsed == ParsedQuery(
        english="photo of mom with pizza in Goa",
        original=query,
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
    assert (parsed.english, parsed.original, parsed.date_range) == (QUERY, QUERY, None)
    assert parsed.rewrite_error  # so search can say the rewrite was skipped
    assert "query rewrite failed" in caplog.text


def test_reason_names_the_error(config: Config):
    handler = FAILURES["connection error"]
    parsed = make_rewriter(config, handler).rewrite(QUERY)
    assert parsed.rewrite_error == "ConnectError: connection refused"


@pytest.mark.parametrize(
    "query",
    ["chacha ki gaay 2019", "शादी २०१९", "photos from 15/08", "bday 3-4-21", "trip 1998"],
)
def test_mentions_date(query: str):
    assert mentions_date(query)


@pytest.mark.parametrize(
    "query",
    ["kal wali shaadi ki photos", "last Diwali", "pichle hafte", "room 12345", "5 dogs", ""],
)
def test_mentions_no_date(query: str):
    assert not mentions_date(query)


DATED = {**GOOD, "date_range": {"start": "2025-10-19", "end": "2025-10-19"}}


def test_invented_date_is_dropped(config: Config):
    rewriter = make_rewriter(config, lambda _: chat_reply(json.dumps(DATED)))
    parsed = rewriter.rewrite("kal wali shaadi ki photos")
    assert parsed.date_range is None
    assert parsed.english == GOOD["english"]  # the rest of the rewrite is kept
    assert parsed.rewrite_error is None


def test_stated_date_is_kept(config: Config):
    rewriter = make_rewriter(config, lambda _: chat_reply(json.dumps(DATED)))
    assert rewriter.rewrite("shaadi 19/10/2025").date_range == ("2025-10-19", "2025-10-19")


def test_prompt_has_todays_date(config: Config):
    assert config.models.rewriter.prompt.count("{today}") == 1
    prompt = system_prompt(config.models.rewriter.prompt)
    assert "{today}" not in prompt
    assert f"Today's date is {date.today().isoformat()}." in prompt


def test_prompt_without_placeholder_is_rejected(config: Config):
    data = config.models.rewriter.model_dump()
    with pytest.raises(ValueError, match="today"):
        RewriterConfig.model_validate({**data, "prompt": "no placeholder"})


def test_prompt_forbids_inventing_and_keeps_english(config: Config):
    prompt = config.models.rewriter.prompt
    assert "Never add people, places, dates, objects" in prompt
    assert "If the query is already in English, copy it exactly" in prompt


def test_examples_file_has_the_planned_mix():
    path = Path(__file__).resolve().parent.parent / "eval" / "rewrite_examples.yaml"
    queries = yaml.safe_load(path.read_text(encoding="utf-8"))["queries"]
    langs = [q["lang"] for q in queries]
    assert (langs.count("hinglish"), langs.count("hindi"), langs.count("english")) == (11, 5, 5)
    assert "kal wali shaadi ki photos" in [q["query"] for q in queries]  # W1
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
    parsed = json.loads(json_part + "}")
    assert parsed.pop("rewrite_error").startswith("ConnectError")
    assert parsed == {
        "english": "kutta",
        "original": "kutta",
        "people": [],
        "date_range": None,
        "place": None,
    }
    assert timing.strip().startswith("took ")
