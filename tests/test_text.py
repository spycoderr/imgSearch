import sqlite3

from imgsearch.store.text import save_caption, save_ocr


def fts_rows(conn: sqlite3.Connection) -> list[tuple[str, str, str]]:
    rows = conn.execute("SELECT image_id, kind, text FROM text_fts ORDER BY kind")
    return [tuple(r) for r in rows]


def matches(conn: sqlite3.Connection, word: str) -> list[str]:
    rows = conn.execute("SELECT image_id FROM text_fts WHERE text_fts MATCH ?", (word,))
    return [r[0] for r in rows]


def test_save_ocr_overwrite_keeps_fts_in_sync(conn: sqlite3.Connection, add_image):
    add_image("a")
    save_ocr(conn, "a", "paneer tikka")
    save_ocr(conn, "a", "masala dosa")

    assert conn.execute("SELECT text FROM ocr WHERE image_id = 'a'").fetchone()[0] == "masala dosa"
    assert fts_rows(conn) == [("a", "ocr", "masala dosa")]
    assert matches(conn, "dosa") == ["a"]
    assert matches(conn, "paneer") == []


def test_save_caption_overwrite_keeps_fts_in_sync(conn: sqlite3.Connection, add_image):
    add_image("a")
    save_caption(conn, "a", "a dog on a beach", "model-1")
    save_caption(conn, "a", "a cat on a sofa", "model-2")

    row = conn.execute("SELECT text, model FROM captions WHERE image_id = 'a'").fetchone()
    assert tuple(row) == ("a cat on a sofa", "model-2")
    assert fts_rows(conn) == [("a", "caption", "a cat on a sofa")]
    assert matches(conn, "dog") == []


def test_ocr_and_caption_rows_are_independent(conn: sqlite3.Connection, add_image):
    add_image("a")
    save_ocr(conn, "a", "menu")
    save_caption(conn, "a", "a restaurant menu", "m")
    save_ocr(conn, "a", "")  # replacing OCR must not touch the caption row

    assert fts_rows(conn) == [("a", "caption", "a restaurant menu"), ("a", "ocr", "")]


def test_empty_ocr_text_is_stored(conn: sqlite3.Connection, add_image):
    add_image("a")
    save_ocr(conn, "a", "")
    assert conn.execute("SELECT text FROM ocr WHERE image_id = 'a'").fetchone()[0] == ""
    assert fts_rows(conn) == [("a", "ocr", "")]
