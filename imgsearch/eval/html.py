"""Shared parts of the self-contained HTML sheets: page shell and embedded thumbnails.

Each thumbnail is embedded once, as a CSS class with a base64 background, and reused by
every tile that shows it, so the file stays small and opens offline.
"""

import base64
import sqlite3
from html import escape
from pathlib import Path

STYLE = """
:root { --bg: #fff; --fg: #1d1d1f; --muted: #6e6e73; --line: #d2d2d7; --hit: #1a7f37;
  --miss: #cf222e; --card: #f5f5f7; }
@media (prefers-color-scheme: dark) { :root { --bg: #161618; --fg: #f5f5f7;
  --muted: #a1a1a6; --line: #3a3a3c; --hit: #3fb950; --miss: #f85149; --card: #232326; } }
body { background: var(--bg); color: var(--fg); margin: 0 auto; max-width: 1200px;
  padding: 16px; font: 14px/1.45 -apple-system, system-ui, sans-serif; }
h1 { font-size: 22px; } h2 { font-size: 17px; margin: 0 0 4px; }
table { border-collapse: collapse; margin: 8px 0 20px; font-variant-numeric: tabular-nums; }
th, td { border: 1px solid var(--line); padding: 3px 8px; text-align: left; }
a { color: inherit; }
.muted { color: var(--muted); } .hit { color: var(--hit); } .miss { color: var(--miss); }
.query { background: var(--card); border-radius: 10px; padding: 12px 14px; margin: 14px 0; }
.row { display: flex; flex-wrap: wrap; gap: 10px; margin: 6px 0; }
.tile { width: 150px; font-size: 12px; overflow-wrap: anywhere; }
.img { width: 150px; height: 150px; background: center / contain no-repeat;
  border: 3px solid transparent; border-radius: 6px; background-color: var(--bg); }
.tile.is-hit .img { border-color: var(--hit); } .tile.is-miss .img { border-color: var(--miss); }
.small .tile { width: 90px; } .small .img { width: 90px; height: 90px; }
details summary { cursor: pointer; color: var(--muted); }
"""


def thumb_class(image_id: str) -> str:
    return "t" + image_id[:16]


def thumbs_css(conn: sqlite3.Connection, derivatives_root: Path, image_ids: set[str]) -> str:
    """One CSS rule per image, with its 256 px thumbnail as a base64 background."""
    rules = []
    for image_id in sorted(image_ids):
        row = conn.execute("SELECT thumb_path FROM images WHERE id = ?", (image_id,)).fetchone()
        if row is None or row["thumb_path"] is None:
            continue  # the tile then shows its caption text without a picture
        data = base64.b64encode((derivatives_root / row["thumb_path"]).read_bytes()).decode()
        rules.append(
            f".{thumb_class(image_id)}{{background-image:url(data:image/jpeg;base64,{data})}}"
        )
    return "\n".join(rules)


def relpaths(conn: sqlite3.Connection) -> dict[str, str]:
    return {
        row["id"]: row["orig_relpath"]
        for row in conn.execute("SELECT id, orig_relpath FROM images")
    }


def tile(image_id: str, label: str, state: str = "") -> str:
    """A thumbnail with a caption underneath. `state` is "hit", "miss" or ""."""
    cls = f"tile is-{state}" if state else "tile"
    return (
        f'<div class="{cls}"><div class="img {thumb_class(image_id)}"></div>'
        f"<div>{label}</div></div>"
    )


def page(title: str, css: str, body: str) -> str:
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{escape(title)}</title><style>{STYLE}\n{css}</style></head>"
        f"<body>{body}</body></html>"
    )


def md_table(lines: list[str]) -> str:
    """A markdown table (as made by `imgsearch.eval.tables`) as an HTML table."""
    rows = [line.strip("|").split("|") for line in lines if line.startswith("|")]
    rows = [r for r in rows if not all(set(c.strip()) <= set("-:") for c in r)]
    if not rows:
        return ""
    head = "".join(f"<th>{escape(c.strip())}</th>" for c in rows[0])
    body = "".join(
        "<tr>" + "".join(f"<td>{escape(c.strip())}</td>" for c in r) + "</tr>" for r in rows[1:]
    )
    return f"<table><tr>{head}</tr>{body}</table>"
