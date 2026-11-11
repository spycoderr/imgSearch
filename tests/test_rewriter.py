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
from imgsearch.config import Config, RewriterConfig, load_config
from imgsearch.interfaces import ParsedQuery
from imgsearch.models.rewriter import (
    OUTPUT_SCHEMA,
    OllamaRewriter,
    all_date_words,
    mentions_date,
    system_prompt,
    weekday_date,
)

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
    assert body["options"] == {"temperature": 0, "num_ctx": cfg.num_ctx}  # num_ctx: P1.9
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
    [
        "chacha ki gaay 2019",
        "शादी २०१९",
        "photos from 15/08",
        "bday 3-4-21",
        "trip 1998",
        "Give me the birthday images from 15-16 sept",
        "birthday in Sept",
        "Give me images from barbecue nation outing on saturday",
        "kal wali shaadi ki photos",
        "pichle mahine ki trip",
        "pichle hafte",
        "photos from last week",
        "YESTERDAY at the park",
        "पिछले ह\u095e\u094dते की फोटो",  # फ़ as one code point (U+095E)
        "पिछले हफ\u093c\u094dते की फोटो",  # फ + combining nukta (U+093C)
        "कल की शादी",
    ],
)
def test_mentions_date(config: Config, query: str):
    assert mentions_date(query, all_date_words(config.models.rewriter))


@pytest.mark.parametrize(
    "query",
    [
        "purple saree ki images",
        "last Diwali",
        "room 12345",
        "5 dogs",
        "the last photo of grandma",
        "kala kutta",  # "kal" only as a whole word
        "septic tank",
        "",
    ],
)
def test_mentions_no_date(config: Config, query: str):
    assert not mentions_date(query, all_date_words(config.models.rewriter))


def test_date_words_are_matched_as_whole_phrases():
    assert mentions_date("photos from last week", ["last week"])
    assert not mentions_date("last photo of the week", ["last week"])


DATED = {**GOOD, "date_range": {"start": "2025-10-19", "end": "2025-10-19"}}


def test_invented_date_is_dropped(config: Config):
    rewriter = make_rewriter(config, lambda _: chat_reply(json.dumps(DATED)))
    parsed = rewriter.rewrite("last Diwali wali shaadi ki photos")
    assert parsed.date_range is None
    assert parsed.english == GOOD["english"]  # the rest of the rewrite is kept
    assert parsed.rewrite_error is None


def test_named_relative_date_is_kept(config: Config):
    rewriter = make_rewriter(config, lambda _: chat_reply(json.dumps(DATED)))
    parsed = rewriter.rewrite("kal wali shaadi ki photos")
    assert parsed.date_range == ("2025-10-19", "2025-10-19")


def test_date_words_come_from_config(config_path: Path):
    text = config_path.read_text(encoding="utf-8")
    config_path.write_text(text.replace("date_words: [", "date_words: [Diwali, "), "utf-8")
    words = load_config(config_path).models.rewriter.date_words
    assert "diwali" in words  # lowercased
    assert mentions_date("last Diwali", words)


def test_stated_date_is_kept(config: Config):
    rewriter = make_rewriter(config, lambda _: chat_reply(json.dumps(DATED)))
    assert rewriter.rewrite("shaadi 19/10/2025").date_range == ("2025-10-19", "2025-10-19")


def test_prompt_has_todays_date(config: Config):
    assert config.models.rewriter.prompt.count("{today}") == 1
    prompt = system_prompt(config.models.rewriter.prompt)
    assert "{today}" not in prompt
    assert f"Today's date is {date.today():%A} {date.today().isoformat()}." in prompt


def test_prompt_lists_the_last_seven_days_with_weekdays(config: Config):
    prompt = system_prompt(config.models.rewriter.prompt, today=date(2026, 10, 21))
    assert (
        "Today's date is Wednesday 2026-10-21. The seven days before it: Tuesday 2026-10-20"
        " (yesterday, kal), Monday 2026-10-19, Sunday 2026-10-18, Saturday 2026-10-17,"
        " Friday 2026-10-16, Thursday 2026-10-15, Wednesday 2026-10-14." in prompt
    )


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


WEDNESDAY = date(2026, 10, 21)


@pytest.mark.parametrize(
    ("query", "day"),
    [
        ("Give me images from barbecue nation outing on saturday", date(2026, 10, 17)),
        ("photos from last Friday", date(2026, 10, 16)),
        ("shanivar ki party", date(2026, 10, 17)),
        ("मंगलवार की फोटो", date(2026, 10, 20)),
        ("on wednesday", date(2026, 10, 14)),  # today's weekday means a week ago
        ("monday", date(2026, 10, 19)),
    ],
)
def test_weekday_is_the_most_recent_one_before_today(config: Config, query: str, day: date):
    assert weekday_date(query, config.models.rewriter.weekdays, WEDNESDAY) == day


@pytest.mark.parametrize(
    "query",
    ["birthday photos", "saturday 15/08", "monday or tuesday", "sunset at the beach"],
)
def test_no_weekday_or_ambiguous_leaves_the_range_to_the_model(config: Config, query: str):
    assert weekday_date(query, config.models.rewriter.weekdays, WEDNESDAY) is None


def test_weekday_overrides_the_models_range(config: Config):
    wrong = {**DATED, "date_range": {"start": "2026-10-21", "end": "2026-10-21"}}
    rewriter = make_rewriter(config, lambda _: chat_reply(json.dumps(wrong)))
    parsed = rewriter.rewrite("barbecue nation outing on saturday")
    assert parsed.date_range is not None
    day = date.fromisoformat(parsed.date_range[0])
    assert parsed.date_range[0] == parsed.date_range[1]
    assert day.weekday() == 5 and 1 <= (date.today() - day).days <= 7


@pytest.mark.parametrize("missing", ["sunday", None])
def test_weekdays_config_needs_all_seven_days(config: Config, missing: str | None):
    data = config.models.rewriter.model_dump()
    weekdays = {d: w for d, w in data["weekdays"].items() if d != missing}
    if missing is None:
        weekdays["funday"] = ["funday"]
    with pytest.raises(ValueError, match="weekdays"):
        RewriterConfig.model_validate({**data, "weekdays": weekdays})
