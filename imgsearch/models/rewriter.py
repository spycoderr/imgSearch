"""Query rewriter (D4, D5): a small Ollama text model turns a query into English JSON."""

import logging
import re
import unicodedata
from collections.abc import Collection, Mapping
from datetime import date, timedelta
from itertools import chain

import httpx
from pydantic import BaseModel, ValidationError, field_validator

from imgsearch.config import WEEKDAYS, RewriterConfig
from imgsearch.interfaces import ParsedQuery
from imgsearch.search.words import query_words

log = logging.getLogger(__name__)


class DateRange(BaseModel):
    start: date
    end: date


# What the LLM must return; its JSON schema is sent as Ollama's `format`. `original` is not
# asked for: code sets it to the exact input. (No docstring: it would land in the schema.)
class RewriteOutput(BaseModel):
    english: str
    people: list[str]
    place: str | None
    date_range: DateRange | None

    @field_validator("english")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("english is empty")
        return value


class _Message(BaseModel):
    content: str


class _ChatResponse(BaseModel):
    message: _Message


OUTPUT_SCHEMA = RewriteOutput.model_json_schema()

# A year 1900-2099, or a numeric day and month such as 15/08, 15-08-2019 or 15.8.19.
_DATE_IN_QUERY = re.compile(r"(?<!\d)(?:(?:19|20)\d\d|\d{1,2}[./-]\d{1,2})(?!\d)")


def all_date_words(config: RewriterConfig) -> list[str]:
    """`date_words` plus every weekday word."""
    return [*config.date_words, *chain(*config.weekdays.values())]


def weekday_date(
    query: str, weekdays: Mapping[str, Collection[str]], today: date | None = None
) -> date | None:
    """The day a query means by a weekday ("on saturday", "shanivar ko"): the most recent
    one before today. None if the query names no weekday, several, or also has a digit
    date ("saturday 15/08"), which the model's range then covers."""
    words = f" {' '.join(query_words(unicodedata.normalize('NFC', query).lower()))} "
    named = {day for day, names in weekdays.items() if any(f" {n} " in words for n in names)}
    if len(named) != 1 or _DATE_IN_QUERY.search(_ascii_digits(query)):
        return None
    today = today or date.today()
    back = (today.weekday() - WEEKDAYS.index(named.pop())) % 7 or 7
    return today - timedelta(days=back)


def _ascii_digits(query: str) -> str:
    return "".join(
        str(unicodedata.decimal(c)) if unicodedata.decimal(c, None) is not None else c
        for c in query
    )


def mentions_date(query: str, date_words: Collection[str]) -> bool:
    """True if the query names a date: a digit-based year or day/month (digits of any
    script count, २०१९), or one of `date_words` (lowercase words or phrases such as
    "sept", "kal", "last week"), matched as whole words in any case."""
    if _DATE_IN_QUERY.search(_ascii_digits(query)):
        return True
    words = f" {' '.join(query_words(unicodedata.normalize('NFC', query).lower()))} "
    return any(f" {phrase} " in words for phrase in date_words)


class OllamaRewriter:
    """`QueryRewriter` backed by Ollama's /api/chat with a JSON-schema `format`."""

    def __init__(self, config: RewriterConfig, client: httpx.Client | None = None) -> None:
        self.config = config
        self.client = client or httpx.Client()

    def rewrite(self, query: str) -> ParsedQuery:
        try:
            output = self._ask(query)
        except (httpx.HTTPError, ValidationError) as err:
            log.warning("query rewrite failed, using the query as given: %s", err)
            return ParsedQuery(english=query, original=query, rewrite_error=_reason(err))
        if output.date_range and not mentions_date(query, all_date_words(self.config)):
            # The model invents dates, e.g. from "last Diwali" (review item W1).
            log.info("dropping date_range %s: the query names no date", output.date_range)
            output.date_range = None
        if day := weekday_date(query, self.config.weekdays):
            output.date_range = DateRange(start=day, end=day)
        return ParsedQuery(
            english=output.english,
            original=query,
            people=[p for p in (s.strip() for s in output.people) if p],
            date_range=(
                (output.date_range.start.isoformat(), output.date_range.end.isoformat())
                if output.date_range
                else None
            ),
            place=(output.place or "").strip() or None,
        )

    def _ask(self, query: str) -> RewriteOutput:
        response = self.client.post(
            f"{self.config.url.rstrip('/')}/api/chat",
            json={
                "model": self.config.name,
                "messages": [
                    {"role": "system", "content": system_prompt(self.config.prompt)},
                    {"role": "user", "content": query},
                ],
                "format": OUTPUT_SCHEMA,
                "options": {"temperature": 0, "num_ctx": self.config.num_ctx},
                "stream": False,
            },
            timeout=self.config.timeout_s,
        )
        response.raise_for_status()
        content = _ChatResponse.model_validate_json(response.content).message.content
        return RewriteOutput.model_validate_json(content)


def system_prompt(prompt: str, today: date | None = None) -> str:
    """The configured prompt with `{today}` set to today's weekday and ISO date, followed by
    the seven days before it, yesterday labelled. With the list qwen2.5:7b gets "kal",
    "aaj", "last week" and "last month" right; weekdays it still gets wrong, so
    `weekday_date` overrides those."""
    today = today or date.today()
    days = [_day(today - timedelta(days=n)) for n in range(1, 8)]
    days[0] += " (yesterday, kal)"
    return prompt.replace("{today}", f"{_day(today)}. The seven days before it: {', '.join(days)}")


def _day(day: date) -> str:
    return f"{day:%A} {day.isoformat()}"


def _reason(err: Exception) -> str:
    """A one-line reason for the CLI, e.g. "ConnectError: connection refused"."""
    first_line = (str(err).splitlines() or [""])[0]
    return f"{type(err).__name__}: {first_line}" if first_line else type(err).__name__
