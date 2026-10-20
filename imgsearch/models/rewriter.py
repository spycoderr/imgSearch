"""Query rewriter (D4, D5): a small Ollama text model turns a query into English JSON."""

import logging
from datetime import date

import httpx
from pydantic import BaseModel, ValidationError, field_validator

from imgsearch.config import RewriterConfig
from imgsearch.interfaces import ParsedQuery

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
            return ParsedQuery(english=query, original=query)
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
                    {"role": "system", "content": self.config.prompt},
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
