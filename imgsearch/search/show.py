"""Plain-text lines for a search response, as printed by `imgsearch search`."""

from imgsearch.search.hybrid import SearchResponse

CAPTION_CHARS = 100


def response_lines(response: SearchResponse) -> list[str]:
    """What was searched, one block per result, then the timings."""
    q = response.query
    if not response.rewrite_used:
        searched = f"searched: {q.english}  (rewrite off)"
    elif q.rewrite_error:
        searched = f"searched: {q.english}  (rewrite skipped: {q.rewrite_error})"
    elif q.english == q.original:
        searched = f"searched: {q.english}  (rewrite left it unchanged)"
    else:
        searched = f"searched: {q.english}  (rewritten from: {q.original})"
    lines = [searched, f"signals: {', '.join(response.signals)}"]
    if response.date_filter:
        start, end = response.date_filter
        lines.append(f"dates: {start} to {end}")
    elif response.date_fallback:
        start, end = q.date_range
        lines.append(f"dates: no photos from {start} to {end}; showing results from all dates")
    elif q.date_range:
        start, end = q.date_range
        lines.append(f"dates: {start} to {end} found but not used (date filter off)")

    for rank, r in enumerate(response.results, start=1):
        matched = ", ".join(f"{signal} #{r.signals[signal][0]}" for signal in sorted(r.signals))
        lines.append(f"{rank:>2}  {r.score:.4f}  {r.orig_relpath}  [{matched}]")
        lines.append(f"    {short(r.caption or '(no caption)')}")
    if not response.results:
        lines.append("no matches")

    t = response.timings
    lines.append(
        f"time: rewrite {t.rewrite_s:.2f}s, retrieval {t.retrieval_s:.2f}s, total {t.total_s:.2f}s"
    )
    return lines


def short(text: str) -> str:
    """`text` on one line, cut to about CAPTION_CHARS characters at a word boundary."""
    text = " ".join(text.split())
    if len(text) <= CAPTION_CHARS:
        return text
    return text[:CAPTION_CHARS].rsplit(" ", 1)[0] + " …"
