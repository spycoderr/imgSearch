"""Load and validate the labelled query set (`eval/queries.yaml`)."""

import sqlite3
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import yaml

LANGUAGES = ("english", "hinglish", "hindi")
TYPES = ("scene", "object", "text", "place", "hard")
FIELDS = {"id", "query", "language", "type", "expected_image_ids", "note"}


@dataclass(frozen=True)
class EvalQuery:
    id: str
    query: str
    language: str
    type: str
    expected_image_ids: tuple[str, ...]
    note: str


class QuerySetError(ValueError):
    """The query file is malformed; the message names the query and the problem."""


def load_queries(path: Path, conn: sqlite3.Connection) -> list[EvalQuery]:
    """Read the query file and check every query. Image ids must exist in `images`."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("queries"), list):
        raise QuerySetError(f"{path}: expected a top-level 'queries' list")
    if not data["queries"]:
        raise QuerySetError(f"{path}: the query list is empty")
    known = {row[0] for row in conn.execute("SELECT id FROM images")}
    queries = [parse_query(i, raw, known) for i, raw in enumerate(data["queries"], start=1)]
    dupes = [qid for qid, n in Counter(q.id for q in queries).items() if n > 1]
    if dupes:
        raise QuerySetError(f"duplicate query ids: {', '.join(sorted(dupes))}")
    return queries


def parse_query(position: int, raw: object, known_ids: set[str]) -> EvalQuery:
    """One query entry, checked. `position` (1-based) names it in errors before its id is known."""
    if not isinstance(raw, dict):
        raise QuerySetError(f"query #{position}: expected a mapping")
    name = f"query {raw.get('id', f'#{position}')!r}"
    missing, extra = FIELDS - raw.keys(), raw.keys() - FIELDS
    if missing or extra:
        raise QuerySetError(f"{name}: missing {sorted(missing)}, unknown {sorted(extra)}")
    for key in ("id", "query"):
        if not isinstance(raw[key], str) or not raw[key].strip():
            raise QuerySetError(f"{name}: {key} must be a non-empty string")
    if raw["language"] not in LANGUAGES:
        raise QuerySetError(f"{name}: language {raw['language']!r} not in {LANGUAGES}")
    if raw["type"] not in TYPES:
        raise QuerySetError(f"{name}: type {raw['type']!r} not in {TYPES}")
    expected = raw["expected_image_ids"]
    if not isinstance(expected, list) or not expected:
        raise QuerySetError(f"{name}: expected_image_ids must be a non-empty list")
    unknown = [i for i in expected if i not in known_ids]
    if unknown:
        raise QuerySetError(f"{name}: unknown image ids: {', '.join(map(str, unknown))}")
    if len(set(expected)) != len(expected):
        raise QuerySetError(f"{name}: expected_image_ids has duplicates")
    return EvalQuery(
        id=raw["id"],
        query=raw["query"],
        language=raw["language"],
        type=raw["type"],
        expected_image_ids=tuple(expected),
        note=raw["note"] or "",
    )


def counts(queries: list[EvalQuery]) -> str:
    """One line: total, then counts by language and by type."""
    by_lang = Counter(q.language for q in queries)
    by_type = Counter(q.type for q in queries)
    langs = ", ".join(f"{lang} {by_lang[lang]}" for lang in LANGUAGES)
    types = ", ".join(f"{t} {by_type[t]}" for t in TYPES)
    return f"{len(queries)} queries; {langs}; {types}"
