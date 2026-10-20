"""Read and write the stage_status table."""

import sqlite3
from datetime import UTC, datetime


def set_status(
    conn: sqlite3.Connection,
    image_id: str,
    stage: str,
    version: str,
    state: str,
    error: str | None = None,
) -> None:
    """Record a stage's state for one image. Commits."""
    now = datetime.now(UTC).isoformat(timespec="seconds")
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO stage_status"
            " (image_id, stage, version, state, error, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (image_id, stage, version, state, error, now),
        )


def status_counts(conn: sqlite3.Connection) -> dict[str, dict[str, int]]:
    """Return {stage: {state: count}} for every stage with at least one row."""
    counts: dict[str, dict[str, int]] = {}
    rows = conn.execute(
        "SELECT stage, state, COUNT(*) FROM stage_status GROUP BY stage, state ORDER BY stage"
    )
    for stage, state, n in rows:
        counts.setdefault(stage, {})[state] = n
    return counts
