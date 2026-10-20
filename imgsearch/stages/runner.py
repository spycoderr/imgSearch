"""Run one stage across every image that needs it, recording stage_status."""

import sqlite3

from imgsearch.interfaces import Context, ImageRecord, Stage
from imgsearch.store.status import set_status

_IMAGE_COLUMNS = (
    "i.id, i.orig_relpath, i.derivative_path, i.thumb_path, i.width, i.height,"
    " i.taken_at, i.lat, i.lon, i.phash, i.created_at"
)


def pending_images(conn: sqlite3.Connection, stage: Stage) -> list[ImageRecord]:
    """Images whose `stage` row is missing, not done, or from another version,
    and whose required stages are all done."""
    requires_sql = "".join(
        " AND EXISTS (SELECT 1 FROM stage_status r"
        " WHERE r.image_id = i.id AND r.stage = ? AND r.state = 'done')"
        for _ in stage.requires
    )
    sql = (
        f"SELECT {_IMAGE_COLUMNS} FROM images i"
        " LEFT JOIN stage_status s ON s.image_id = i.id AND s.stage = ?"
        " WHERE (s.image_id IS NULL OR s.state != 'done' OR s.version != ?)"
        f"{requires_sql}"
        " ORDER BY i.id"
    )
    rows = conn.execute(sql, (stage.name, stage.version, *stage.requires))
    return [ImageRecord(**dict(row)) for row in rows]


def run_stage(stage: Stage, ctx: Context) -> dict[str, int]:
    """Run `stage` on every pending image. Returns {"done": n, "failed": m}.

    One image failing is recorded and does not stop the others. The stage is loaded
    only if there is work, and always unloaded afterwards. A failed image may keep
    partial results (e.g. a committed save_ocr); the next run overwrites them.
    """
    counts = {"done": 0, "failed": 0}
    images = pending_images(ctx.conn, stage)
    if not images:
        return counts

    try:
        stage.load(ctx)
        for image in images:
            set_status(ctx.conn, image.id, stage.name, stage.version, "running")
            try:
                stage.run(image, ctx)
            except Exception as e:  # the plan requires per-image failures to be recorded
                error = f"{type(e).__name__}: {e}"
                set_status(ctx.conn, image.id, stage.name, stage.version, "failed", error)
                counts["failed"] += 1
            else:
                set_status(ctx.conn, image.id, stage.name, stage.version, "done")
                counts["done"] += 1
    finally:
        stage.unload()
    return counts
