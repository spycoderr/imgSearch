"""Query rewriter (D4, D5): a small Ollama text model turns a query into English JSON."""

import logging
import re
import unicodedata
from collections.abc import Collection
from datetime import date

import httpx
from pydantic import BaseModel, ValidationError, field_validator

from imgsearch.config import RewriterConfig
from imgsearch.interfaces import ParsedQuery
from imgsearch.search.text import query_words

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


def mentions_date(query: str, date_words: Collection[str]) -> bool:
    """True if the query names a date: a digit-based year or day/month (digits of any
    script count, २०१९), or one of `date_words` (lowercase words or phrases such as
    "sept", "kal", "last week"), matched as whole words in any case."""
    ascii_digits = "".join(
        str(unicodedata.decimal(c)) if unicodedata.decimal(c, None) is not None else c
        for c in query
    )
    if _DATE_IN_QUERY.search(ascii_digits):
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
        if output.date_range and not mentions_date(query, self.config.date_words):
            # The model invents dates, e.g. from "last Diwali" (review item W1).
            log.info("dropping date_range %s: the query names no date", output.date_range)
            output.date_range = None
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
                "options": {"temperature": 0},
                "stream": False,
            },
            timeout=self.config.timeout_s,
        )
        response.raise_for_status()
        content = _ChatResponse.model_validate_json(response.content).message.content
        return RewriteOutput.model_validate_json(content)


def system_prompt(prompt: str) -> str:
    """The configured prompt with its `{today}` placeholder set to today's ISO date."""
    return prompt.replace("{today}", date.today().isoformat())


def _reason(err: Exception) -> str:
    """A one-line reason for the CLI, e.g. "ConnectError: connection refused"."""
    first_line = (str(err).splitlines() or [""])[0]
    return f"{type(err).__name__}: {first_line}" if first_line else type(err).__name__
