"""Splitting text into search words, shared by full-text search, the rewriter's guards and
place names."""

import unicodedata


def query_words(text: str) -> list[str]:
    """Split text into words: runs of letters, digits and combining marks (so Devanagari
    vowel signs stay inside their word). Everything else separates words. Duplicates are
    dropped, keeping first-seen order."""
    kept = "".join(c if unicodedata.category(c)[0] in "LNM" else " " for c in text)
    return list(dict.fromkeys(kept.split()))
