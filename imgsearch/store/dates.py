"""A photo's date for searching: EXIF `taken_at`, else the original file's mtime."""

from datetime import datetime

# The one definition of a photo's search date. Both columns are naive local ISO time, so
# the first 10 characters are the day (YYYY-MM-DD). Used with the `images` table as `i`.
SEARCH_DATE_SQL = "COALESCE(i.taken_at, i.file_mtime)"


def date_filter_sql(date_range: tuple[str, str] | None) -> tuple[str, tuple[str, ...]]:
    """An `AND image_id IN (...)` clause keeping images whose search date falls in
    `date_range` (inclusive ISO days), and its parameters. ("", ()) when there is no range.
    Images with no date at all never match a range."""
    if date_range is None:
        return "", ()
    sql = (
        f" AND image_id IN (SELECT i.id FROM images i"
        f" WHERE substr({SEARCH_DATE_SQL}, 1, 10) BETWEEN ? AND ?)"
    )
    return sql, date_range


def local_iso(timestamp: float) -> str:
    """A POSIX timestamp as naive local time, the format of `taken_at`."""
    return datetime.fromtimestamp(timestamp).isoformat(timespec="seconds")
