"""Full-text retriever over `text_fts` (OCR text, captions, file-name words, place names),
ranked by bm25."""

import sqlite3

from imgsearch.interfaces import Candidate, ParsedQuery
from imgsearch.search.filters import filter_sql
from imgsearch.search.words import query_words

SIGNAL = "text"
KINDS = ("ocr", "caption", "filename", "place")  # the `kind` values in text_fts

# Dropped from queries (any case): they match almost every caption ("This photo shows a..."),
# so they would add noise to every fused result.
STOPWORDS = frozenset(
    "a an the and or of in on at to for with from by is are was were be been this that these"
    " those it its as me my our your his her their them we you he she they i there here some"
    " any all what which who show shows showing find"
    " photo photos picture pictures image images pic pics".split()
)


def match_expression(*texts: str) -> str:
    """An FTS5 MATCH string that matches any word of `texts` except `STOPWORDS`, or "" if
    no word is left.

    Each word is double-quoted, so FTS5 operators typed by the user (AND, NEAR, -, :, *)
    are plain words. Words can't contain `"`, since it is a separator in `query_words`.
    """
    words = [w for w in query_words(" ".join(texts)) if w.lower() not in STOPWORDS]
    return " OR ".join(f'"{w}"' for w in words)


class TextRetriever:
    """`Retriever` over all of `text_fts`, so OCR text, captions and file-name words match.

    One query covers the words of both `q.english` and `q.original`. Each image appears
    once, at its best-scoring row. `Candidate.score` is `-bm25`, so higher is better.
    When `q.date_range` or `q.place_filter` is set, only images within them are returned.
    `kinds` limits the rows searched, e.g. `("ocr", "filename")` to leave captions out
    (the M7 ablation); the default searches every kind.
    """

    def __init__(self, conn: sqlite3.Connection, kinds: tuple[str, ...] = KINDS) -> None:
        unknown = [k for k in kinds if k not in KINDS]
        if not kinds or unknown:
            raise ValueError(f"kinds must be some of {KINDS}, got {kinds}")
        self.conn = conn
        self.kinds = kinds

    def retrieve(self, q: ParsedQuery, k: int) -> list[Candidate]:
        expression = match_expression(q.english, q.original)
        if not expression:
            return []
        # bm25() can't be used under GROUP BY, so keep each image's first (best) row here.
        marks = ",".join("?" * len(self.kinds))
        in_range, range_params = filter_sql(q)
        rows = self.conn.execute(
            "SELECT image_id, bm25(text_fts) FROM text_fts WHERE text_fts MATCH ?"
            f" AND kind IN ({marks}){in_range} ORDER BY bm25(text_fts), image_id",
            (expression, *self.kinds, *range_params),
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
