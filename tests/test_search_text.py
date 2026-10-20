import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from imgsearch.cli import app
from imgsearch.interfaces import ParsedQuery
from imgsearch.search.text import TextRetriever, best_snippet, match_expression, query_words
from imgsearch.store.text import save_caption, save_ocr


def q(english: str, original: str | None = None) -> ParsedQuery:
    return ParsedQuery(english=english, original=english if original is None else original)


def ids(conn: sqlite3.Connection, query: ParsedQuery, k: int = 10) -> list[str]:
    return [c.image_id for c in TextRetriever(conn).retrieve(query, k)]


@pytest.fixture
def library(conn: sqlite3.Connection, add_image) -> sqlite3.Connection:
    """menu: OCR only; dog: caption only; both: OCR and caption; sign: Devanagari OCR."""
    for image_id in ("menu", "dog", "both", "sign", "blank"):
        add_image(image_id)
    save_ocr(conn, "menu", "MENU\nPaneer Tikka 250\nMasala Dosa 120")
    save_caption(conn, "dog", "a brown dog sleeping on a sofa", "m")
    save_ocr(conn, "both", "Paneer special")
    save_caption(conn, "both", "a plate of paneer tikka with onions, paneer", "m")
    save_ocr(conn, "sign", "स्वागत है\nWelcome")
    save_ocr(conn, "blank", "")
    return conn


def test_query_words():
    assert query_words('paneer-tikka "x" a:b (c* NEAR') == [
        "paneer",
        "tikka",
        "x",
        "a",
        "b",
        "c",
        "NEAR",
    ]
    assert query_words("पनीर टिक्का, पनीर") == ["पनीर", "टिक्का"]  # vowel signs stay
    assert query_words("?!. -- ") == []


def test_match_expression_quotes_each_word():
    assert match_expression("chai AND coffee", "chai") == '"chai" OR "AND" OR "coffee"'
    assert match_expression("...", "") == ""


def test_searches_ocr_and_captions(library):
    assert ids(library, q("dosa")) == ["menu"]  # OCR row
    assert ids(library, q("sofa")) == ["dog"]  # caption row


def test_one_candidate_per_image_ranked_by_bm25(library):
    candidates = TextRetriever(library).retrieve(q("paneer tikka"), 10)
    # "both" matches in OCR and caption but appears once.
    assert sorted(c.image_id for c in candidates) == ["both", "menu"]
    assert [c.rank for c in candidates] == [1, 2]
    assert all(c.signal == "text" for c in candidates)
    assert candidates[0].score >= candidates[1].score

    expected = library.execute(
        "SELECT image_id FROM text_fts WHERE text_fts MATCH ? ORDER BY bm25(text_fts) LIMIT 1",
        (match_expression("paneer tikka"),),
    ).fetchone()[0]
    assert candidates[0].image_id == expected


def test_k_limits_results(library):
    assert len(TextRetriever(library).retrieve(q("paneer"), 1)) == 1


def test_no_match_and_empty_query(library):
    assert ids(library, q("zebra")) == []
    assert ids(library, q("")) == []
    assert ids(library, q("?!")) == []


def test_searches_english_and_original(library):
    # The rewrite gives English; the Devanagari original still matches the sign's OCR.
    assert sorted(ids(library, q(english="sofa", original="स्वागत"))) == ["dog", "sign"]
    assert ids(library, q(english="sofa", original="sofa")) == ["dog"]
    assert ids(library, q(english="", original="स्वागत")) == ["sign"]
    # Same image matched by both fields appears once.
    assert ids(library, q(english="welcome", original="स्वागत है")) == ["sign"]


@pytest.mark.parametrize(
    "text",
    [
        'dosa"',
        '"dosa',
        "masala-dosa",
        "-dosa",
        "menu:dosa",
        "dosa*",
        "(dosa",
        "dosa)",
        "paneer AND",
        "AND",
        "OR",
        "NOT dosa",
        "NEAR(dosa paneer)",
        "dosa NEAR paneer",
        "^dosa",
        "{text}: dosa",
        "मसाला डोसा",
        'मेनू: "डोसा" - (*)',
        "dosa 🍛 !!!",
    ],
)
def test_user_text_never_raises(library, text):
    found = ids(library, q(text))
    if "dosa" in text.lower():
        assert "menu" in found


def test_best_snippet(library):
    assert (
        best_snippet(library, "menu", q("dosa")) == "ocr: MENU Paneer Tikka 250 Masala [Dosa] 120"
    )
    assert best_snippet(library, "dog", q("dosa")) == ""


def test_cli_search_text(config_path: Path, library):
    result = CliRunner().invoke(app, ["--config", str(config_path), "search-text", "masala dosa"])
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0].split()[0] == "1"
    assert lines[0].split()[2] == "menu.jpg"
    assert "[Masala] [Dosa]" in lines[1]


def test_cli_search_text_no_match(config_path: Path, library):
    result = CliRunner().invoke(app, ["--config", str(config_path), "search-text", "?! :"])
    assert result.exit_code == 0, result.output
    assert result.output.strip() == "no matches"
