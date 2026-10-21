"""Write OCR text, captions, file-name words and place names, keeping the text_fts index in sync.

Stages call these instead of touching text_fts directly.
"""

import sqlite3


def save_ocr(conn: sqlite3.Connection, image_id: str, text: str) -> None:
    """Store (or replace) an image's OCR text and its FTS row. Commits."""
    with conn:
        conn.execute("INSERT OR REPLACE INTO ocr (image_id, text) VALUES (?, ?)", (image_id, text))
        _replace_fts(conn, image_id, "ocr", text)


def save_caption(conn: sqlite3.Connection, image_id: str, text: str, model: str) -> None:
    """Store (or replace) an image's caption and its FTS row. Commits."""
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO captions (image_id, text, model) VALUES (?, ?, ?)",
            (image_id, text, model),
        )
        _replace_fts(conn, image_id, "caption", text)


def save_filename_text(conn: sqlite3.Connection, image_id: str, text: str) -> None:
    """Store (or replace) an image's `filename` FTS row: words from its file name and source
    folder. There is no base table; the words are rebuilt from `images`. Commits."""
    with conn:
        _replace_fts(conn, image_id, "filename", text)


def save_place_text(conn: sqlite3.Connection, image_id: str, text: str) -> None:
    """Replace an image's `place` FTS row: the names of where it was taken (P1.8). The base
    table is `photo_places`. Doesn't commit: `store.places.save_place` does."""
    _replace_fts(conn, image_id, "place", text)


def delete_place_text(conn: sqlite3.Connection, image_id: str) -> None:
    """Remove an image's `place` FTS row. Doesn't commit."""
    conn.execute("DELETE FROM text_fts WHERE image_id = ? AND kind = 'place'", (image_id,))


def _replace_fts(conn: sqlite3.Connection, image_id: str, kind: str, text: str) -> None:
    conn.execute("DELETE FROM text_fts WHERE image_id = ? AND kind = ?", (image_id, kind))
    conn.execute(
        "INSERT INTO text_fts (image_id, kind, text) VALUES (?, ?, ?)", (image_id, kind, text)
    )
