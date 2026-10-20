import sqlite3
from pathlib import Path

import pytest
import yaml

from imgsearch.config import DEFAULT_CONFIG_PATH, load_config
from imgsearch.eval.queries import QuerySetError, counts, load_queries


def query(**overrides) -> dict:
    base = {
        "id": "en-dog",
        "query": "a white dog",
        "language": "english",
        "type": "object",
        "expected_image_ids": ["dog1", "dog2"],
        "note": "",
    }
    return {**base, **overrides}


@pytest.fixture
def library(conn: sqlite3.Connection, add_image) -> sqlite3.Connection:
    for image_id in ("dog1", "dog2", "sign"):
        add_image(image_id)
    return conn


def write(tmp_path: Path, queries: list) -> Path:
    path = tmp_path / "queries.yaml"
    path.write_text(yaml.safe_dump({"queries": queries}, allow_unicode=True), encoding="utf-8")
    return path


def test_loads_valid_queries(tmp_path: Path, library):
    queries = [query(), query(id="dv-sign", query="बोर्ड", language="hindi", type="text",
                              expected_image_ids=["sign"], note=None)]  # fmt: skip
    loaded = load_queries(write(tmp_path, queries), library)
    assert [q.id for q in loaded] == ["en-dog", "dv-sign"]
    assert loaded[0].expected_image_ids == ("dog1", "dog2")
    assert loaded[1].note == ""
    assert counts(loaded).startswith("2 queries; english 1, hinglish 0, hindi 1;")


@pytest.mark.parametrize(
    ("bad", "message"),
    [
        (query(expected_image_ids=["dog1", "nope"]), "unknown image ids: nope"),
        (query(language="french"), "language 'french'"),
        (query(type="vibes"), "type 'vibes'"),
        (query(expected_image_ids=[]), "must be a non-empty list"),
        (query(expected_image_ids=["dog1", "dog1"]), "duplicates"),
        (query(query="  "), "query must be a non-empty string"),
        ({k: v for k, v in query().items() if k != "note"}, "missing ['note']"),
        (query(lang="english"), "unknown ['lang']"),
        ("just a string", "query #1: expected a mapping"),
    ],
)
def test_rejects_bad_queries(tmp_path: Path, library, bad, message):
    with pytest.raises(QuerySetError, match="en-dog|#1") as err:
        load_queries(write(tmp_path, [bad]), library)
    assert message in str(err.value)


def test_rejects_duplicate_ids(tmp_path: Path, library):
    with pytest.raises(QuerySetError, match="duplicate query ids: en-dog"):
        load_queries(write(tmp_path, [query(), query(query="dog again")]), library)


@pytest.mark.parametrize("content", ["queries: []\n", "- just a list\n", "{}\n"])
def test_rejects_bad_file_shape(tmp_path: Path, library, content):
    path = tmp_path / "queries.yaml"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(QuerySetError):
        load_queries(path, library)


def test_project_query_set_has_the_planned_mix():
    # Checks shape and mix only; image ids are checked against the real DB by `eval-review`.
    config = load_config(DEFAULT_CONFIG_PATH)
    raw = yaml.safe_load(config.eval.queries.read_text(encoding="utf-8"))["queries"]
    langs = [q["language"] for q in raw]
    types = [q["type"] for q in raw]
    assert langs.count("english") >= 12 and langs.count("hinglish") >= 10
    assert langs.count("hindi") >= 6
    assert types.count("text") >= 6 and types.count("hard") >= 4
    for needed in ("जयपुर का किला", "kal wali shaadi ki photos"):
        assert needed in [q["query"] for q in raw]
