"""Markdown tables of eval metrics, for the terminal and the reports."""

from imgsearch.eval.metrics import METRICS

HEADS = {"hit@5": "hit@5", "recall@5": "recall@5", "rr": "MRR"}


def overall_table(metrics: dict[str, dict]) -> list[str]:
    """One row per configuration: n, hit@5, recall@5, MRR."""
    lines = ["| config | n | " + " | ".join(HEADS[m] for m in METRICS) + " |"]
    lines.append("| --- | ---: | " + " | ".join("---:" for _ in METRICS) + " |")
    for name, m in metrics.items():
        o = m["overall"]
        lines.append(f"| {name} | {o['n']} | " + " | ".join(f"{o[k]:.2f}" for k in METRICS) + " |")
    return lines


def group_table(metrics: dict[str, dict], group: str) -> list[str]:
    """Rows are configurations, columns the group's values (with n); each cell is
    hit@5 / recall@5 / MRR."""
    values = sorted({v for m in metrics.values() for v in m[group]})
    first = next(iter(metrics.values()))[group]
    heads = [f"{v} (n={first[v]['n']})" if v in first else v for v in values]
    lines = [f"| config ({'hit / recall / MRR'}) | " + " | ".join(heads) + " |"]
    lines.append("| --- | " + " | ".join("---" for _ in values) + " |")
    for name, m in metrics.items():
        cells = [_cell(m[group].get(v)) for v in values]
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    return lines


def all_tables(metrics: dict[str, dict]) -> list[str]:
    lines = ["Overall", "", *overall_table(metrics)]
    for group in ("language", "type"):
        lines += ["", f"By {group}", "", *group_table(metrics, group)]
    return lines


def compare_table(a: dict[str, dict], b: dict[str, dict], label_a: str, label_b: str) -> list[str]:
    """Overall metrics of two runs side by side, with B - A, for configurations in both."""
    heads = [f"{HEADS[m]} {label}" for m in METRICS for label in (label_a, label_b, "Δ")]
    lines = ["| config | " + " | ".join(heads) + " |"]
    lines.append("| --- | " + " | ".join("---:" for _ in heads) + " |")
    for name in [n for n in a if n in b]:
        oa, ob = a[name]["overall"], b[name]["overall"]
        cells = []
        for m in METRICS:
            cells += [f"{oa[m]:.2f}", f"{ob[m]:.2f}", f"{ob[m] - oa[m]:+.2f}"]
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    only = sorted(set(a) ^ set(b))
    if only:
        lines.append(f"(in one run only: {', '.join(only)})")
    return lines


def _cell(m: dict | None) -> str:
    if m is None:
        return "–"
    return " / ".join(f"{m[k]:.2f}" for k in METRICS)
