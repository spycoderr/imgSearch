"""The run's contact sheet and the query review sheet, as self-contained HTML."""

from html import escape

from imgsearch.eval.html import md_table, page, tile
from imgsearch.eval.queries import EvalQuery
from imgsearch.eval.run import MAIN
from imgsearch.eval.tables import group_table, overall_table

SHORT = {"vector": "v", "text": "t"}


def contact_sheet(
    records: list[dict], metrics: dict[str, dict], meta: dict, css: str, paths: dict[str, str]
) -> str:
    """Summary tables, then per query: the rewrite, MAIN's top 5 (hits marked), the expected
    images (misses marked), each configuration's metrics, and the other top 5s folded."""
    commit = f"{meta['commit'][:10]}{' (uncommitted changes)' if meta['dirty'] else ''}"
    parts = [
        f"<h1>Eval run {escape(meta['started'])}</h1>",
        f"<p class='muted'>commit {escape(commit)} · {meta['queries']} queries · "
        f"{len(metrics)} configurations · {meta['elapsed_s']:.0f} s. "
        f"Cells are hit@5 / recall@5 / MRR. Details below use <b>{MAIN}</b>.</p>",
    ]
    parts += ["<h2>Overall</h2>", md_table(overall_table(metrics))]
    for group in ("language", "type"):
        parts += [f"<h2>By {group}</h2>", md_table(group_table(metrics, group))]

    by_query: dict[str, dict[str, dict]] = {}
    for r in records:
        by_query.setdefault(r["id"], {})[r["config"]] = r
    parts.append(_index(by_query))
    for qid, runs in by_query.items():
        parts.append(_query_block(qid, runs, paths))
    return page(f"Eval {meta['started']}", css, "\n".join(parts))


def _index(by_query: dict[str, dict[str, dict]]) -> str:
    rows = []
    for qid, runs in by_query.items():
        r = runs.get(MAIN) or next(iter(runs.values()))
        mark = "hit" if r["hit@5"] else "miss"
        rows.append(
            f"<tr><td><a href='#{escape(qid)}'>{escape(qid)}</a></td><td>{escape(r['query'])}</td>"
            f"<td class='{mark}'>{r['hit@5']:.0f} / {r['recall@5']:.2f} / {r['rr']:.2f}</td></tr>"
        )
    head = f"<tr><th>id</th><th>query</th><th>{MAIN}</th></tr>"
    return f"<h2>Queries</h2><table>{head}{''.join(rows)}</table>"


def _query_block(qid: str, runs: dict[str, dict], paths: dict[str, str]) -> str:
    main = runs.get(MAIN) or next(iter(runs.values()))
    expected = set(main["expected"])
    rewritten = next((r for r in runs.values() if r["rewrite_used"]), main)
    if rewritten["rewrite_error"]:
        rewrite = f"<span class='miss'>rewrite skipped: {escape(rewritten['rewrite_error'])}</span>"
    else:
        rewrite = f"rewrite: <b>{escape(rewritten['english'])}</b>"
    if rewritten.get("date_range"):  # runs before P1.7 have no date fields
        start, end = rewritten["date_range"]
        used = (
            "fell back to all dates"
            if rewritten["date_fallback"]
            else ("filtered" if rewritten["date_filter"] else "not used")
        )
        rewrite += f" · dates {escape(start)} to {escape(end)} ({used})"
    scores = "".join(
        f"<tr><td>{escape(name)}</td><td class='{'hit' if r['hit@5'] else 'miss'}'>"
        f"{r['hit@5']:.0f} / {r['recall@5']:.2f} / {r['rr']:.2f}</td></tr>"
        for name, r in runs.items()
    )
    found = {res["image_id"] for res in main["results"]}
    expected_tiles = [
        tile(
            i,
            escape(paths.get(i, i[:12])) + ("" if i in found else " · <b class='miss'>missed</b>"),
            "hit" if i in found else "miss",
        )  # fmt: skip
        for i in main["expected"]
    ]
    others = "".join(
        f"<p class='muted'>{escape(name)}</p>"
        f"<div class='row small'>{_top5(r, expected, paths)}</div>"
        for name, r in runs.items()
        if name != main["config"]
    )
    return (
        f"<div class='query' id='{escape(qid)}'><h2>{escape(qid)}: {escape(main['query'])}</h2>"
        f"<div class='muted'>{main['language']} · {main['type']}</div><div>{rewrite}</div>"
        f"<p>{main['config']} top 5:</p><div class='row'>{_top5(main, expected, paths)}</div>"
        f"<p>expected ({len(expected)}):</p><div class='row small'>{''.join(expected_tiles)}</div>"
        f"<details><summary>all configurations</summary><table>{scores}</table>{others}</details>"
        "</div>"
    )


def _top5(record: dict, expected: set[str], paths: dict[str, str]) -> str:
    tiles = []
    for rank, res in enumerate(record["results"], start=1):
        signals = " ".join(
            f"{SHORT.get(s, s)}{rank_score[0]}" for s, rank_score in sorted(res["signals"].items())
        )
        label = (
            f"#{rank} · {res['score']:.4f} · {escape(signals)}<br>"
            f"<span class='muted'>{escape(paths.get(res['image_id'], res['orig_relpath']))}</span>"
        )
        tiles.append(tile(res["image_id"], label, "hit" if res["image_id"] in expected else ""))
    return "".join(tiles) or "<span class='muted'>no results</span>"


def review_sheet(queries: list[EvalQuery], css: str, paths: dict[str, str], summary: str) -> str:
    """Each query with its expected thumbnails and file names, for the user to confirm."""
    blocks = [
        "<h1>Eval query review</h1>",
        f"<p class='muted'>{escape(summary)}. Check that each query's expected photos are right "
        "and complete (every photo of a burst counts); edit <code>eval/queries.yaml</code>.</p>",
    ]
    for q in queries:
        tiles = "".join(tile(i, escape(paths[i])) for i in q.expected_image_ids)
        note = f"<div class='muted'>note: {escape(q.note)}</div>" if q.note else ""
        blocks.append(
            f"<div class='query' id='{escape(q.id)}'><h2>{escape(q.id)}: {escape(q.query)}</h2>"
            f"<div class='muted'>{q.language} · {q.type} · {len(q.expected_image_ids)} expected"
            f"</div>{note}<div class='row small'>{tiles}</div></div>"
        )
    return page("Eval query review", css, "\n".join(blocks))
