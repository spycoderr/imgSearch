"""Full-text retriever over `text_fts` (OCR text and captions), ranked by bm25."""

import sqlite3
import unicodedata

from imgsearch.interfaces import Candidate, ParsedQuery

SIGNAL = "text"


def query_words(text: str) -> list[str]:
    """Split text into words: runs of letters, digits and combining marks (so Devanagari
    vowel signs stay inside their word). Everything else separates words. Duplicates are
    dropped, keeping first-seen order."""
    kept = "".join(c if unicodedata.category(c)[0] in "LNM" else " " for c in text)
    return list(dict.fromkeys(kept.split()))


def match_expression(*texts: str) -> str:
    """An FTS5 MATCH string that matches any word of `texts`, or "" if there are none.

    Each word is double-quoted, so FTS5 operators typed by the user (AND, NEAR, -, :, *)
    are plain words. Words can't contain `"`, since it is a separator in `query_words`.
    """
    words = query_words(" ".join(texts))
    return " OR ".join(f'"{w}"' for w in words)


class TextRetriever:
    """`Retriever` over all of `text_fts`, so OCR text and captions both match.

    One query covers the words of both `q.english` and `q.original`. Each image appears
    once, at its best-scoring row. `Candidate.score` is `-bm25`, so higher is better.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def retrieve(self, q: ParsedQuery, k: int) -> list[Candidate]:
        expression = match_expression(q.english, q.original)
        if not expression:
            return []
        # bm25() can't be used under GROUP BY, so keep each image's first (best) row here.
        rows = self.conn.execute(
            "SELECT image_id, bm25(text_fts) FROM text_fts WHERE text_fts MATCH ?"
            " ORDER BY bm25(text_fts), image_id",
            (expression,),
        )
        best: dict[str, float] = {}
        for image_id, bm25 in rows:
            if image_id not in best:
                best[image_id] = -bm25
                if len(best) == k:
                    break
        return [
            Candidate(image_id=image_id, rank=rank, score=score, signal=SIGNAL)
            for rank, (image_id, score) in enumerate(best.items(), start=1)
        ]


def best_snippet(conn: sqlite3.Connection, image_id: str, q: ParsedQuery) -> str:
    """The matching text around the query words in an image's best row, for display.
    Matched words are wrapped in [ ]. Returns "" when nothing matches."""
    expression = match_expression(q.english, q.original)
    if not expression:
        return ""
    row = conn.execute(
        "SELECT kind, snippet(text_fts, 2, '[', ']', '…', 12) FROM text_fts"
        " WHERE text_fts MATCH ? AND image_id = ? ORDER BY bm25(text_fts) LIMIT 1",
        (expression, image_id),
    ).fetchone()
    if row is None:
        return ""
    kind, snippet = row
    return f"{kind}: " + " ".join(snippet.split())
