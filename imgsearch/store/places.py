"""Photo places (P1.8): save what the `places` stage found, resolve a query's place name to
the places in the library, and filter search by it. Search reads only the DB.
"""

import re
import sqlite3
import unicodedata

from imgsearch.interfaces import PlaceFilter
from imgsearch.search.words import query_words
from imgsearch.sources.geonames import Feature, Place
from imgsearch.store.text import delete_place_text, save_place_text

MIN_KEY_CHARS = 3  # shorter alternate names ("IN", "UP") would match stray words
LEVELS = ("landmark_id", "city_id", "district_id", "state_id", "country_id")
# A place as the rewriter wrote it can name several ("Amer Fort, Jaipur", "Goa and Pune").
_PARTS = re.compile(r",|/|&|\band\b|\bor\b", re.IGNORECASE)


def place_key(name: str) -> str:
    """The lookup form of a place name: NFC, casefolded, words joined by one space, without
    a leading "the" ("The India Gate" → "india gate"; "जयपुर" stays as it is)."""
    words = query_words(unicodedata.normalize("NFC", name).casefold())
    if len(words) > 1 and words[0] == "the":
        words = words[1:]
    return " ".join(words)


def save_place(conn: sqlite3.Connection, image_id: str, place: Place) -> None:
    """Store (or replace) a photo's place, its places' names and its `place` FTS row.
    Commits."""
    with conn:
        for feature in place.features():
            _save_feature(conn, feature)

        def gid(f: Feature | None) -> int | None:
            return f.geonameid if f else None

        conn.execute(
            "INSERT OR REPLACE INTO photo_places (image_id, city_id, city_km, district_id,"
            " state_id, country_id, landmark_id, landmark_km) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                image_id,
                gid(place.city),
                place.city_km,
                gid(place.district),
                gid(place.state),
                gid(place.country),
                gid(place.landmark),
                place.landmark_km,
            ),
        )
        names = {f.name.casefold(): f.name for f in place.features()}  # "New Delhi" once
        save_place_text(conn, image_id, " ".join(names.values()))


def delete_place(conn: sqlite3.Connection, image_id: str) -> None:
    """Remove a photo's place and its `place` FTS row (a photo without GPS). Commits."""
    with conn:
        conn.execute("DELETE FROM photo_places WHERE image_id = ?", (image_id,))
        delete_place_text(conn, image_id)


def _save_feature(conn: sqlite3.Connection, feature: Feature) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO places (geonameid, name) VALUES (?, ?)",
        (feature.geonameid, feature.name),
    )
    keys = {place_key(n) for n in (feature.name, *feature.names)}
    conn.executemany(
        "INSERT OR IGNORE INTO place_names (key, geonameid) VALUES (?, ?)",
        [(k, feature.geonameid) for k in keys if len(k) >= MIN_KEY_CHARS and not k.isdigit()],
    )


def resolve_place(conn: sqlite3.Connection, place: str | None) -> PlaceFilter | None:
    """The places in the library that `place` names, or None if it names none ("park",
    "sofa", "home"). Tries the whole name, then each part of a list ("Amer Fort, Jaipur")
    in order. Matches any name GeoNames knows (Bangalore/Bengaluru, जयपुर), but only
    places some photo is in."""
    if not place:
        return None
    for text in [place, *_PARTS.split(place)]:
        key = place_key(text)
        if len(key) < MIN_KEY_CHARS:
            continue
        rows = conn.execute(
            "SELECT DISTINCT n.geonameid, p.name FROM place_names n"
            " JOIN places p ON p.geonameid = n.geonameid WHERE n.key = ? AND EXISTS ("
            "SELECT 1 FROM photo_places pp WHERE n.geonameid IN"
            f" ({', '.join('pp.' + level for level in LEVELS)})) ORDER BY n.geonameid",
            (key,),
        ).fetchall()
        if rows:
            names = dict.fromkeys([text.strip(), *(row["name"] for row in rows)])
            return PlaceFilter(
                label=text.strip(),
                geonameids=tuple(row["geonameid"] for row in rows),
                names=tuple(names),
            )
    return None


def place_filter_sql(place: PlaceFilter | None) -> tuple[str, tuple]:
    """An `AND image_id IN (...)` clause keeping photos taken at `place` (at any level: a
    state matches all its cities) or whose OCR text, caption or file name contains one of
    its names, with its parameters. ("", ()) when there is no place."""
    if place is None:
        return "", ()
    marks = ",".join("?" * len(place.geonameids))
    by_gps = " OR ".join(f"{level} IN ({marks})" for level in LEVELS)
    phrases = [" ".join(query_words(name)) for name in place.names]
    expression = " OR ".join(f'"{p}"' for p in dict.fromkeys(phrases) if p)
    sql = (
        f" AND image_id IN (SELECT image_id FROM photo_places WHERE {by_gps}"
        # Not the `place` rows: they hold GPS places, matched by id above ("New Delhi" in a
        # Noida photo's place row must not match "Delhi").
        " UNION SELECT image_id FROM text_fts WHERE text_fts MATCH ? AND kind != 'place')"
    )
    return sql, (*place.geonameids * len(LEVELS), expression)


def place_labels(conn: sqlite3.Connection, image_ids: list[str]) -> dict[str, str]:
    """A short place line per photo that has one, e.g. "Amber Fort, Amer, Jaipur,
    Rajasthan": landmark, city, district and state, repeats left out."""
    marks = ",".join("?" * len(image_ids))
    joins = "".join(
        f" LEFT JOIN places {alias} ON {alias}.geonameid = pp.{level}"
        for alias, level in zip("lcds", LEVELS, strict=False)
    )
    rows = conn.execute(
        f"SELECT pp.image_id, l.name, c.name, d.name, s.name FROM photo_places pp{joins}"
        f" WHERE pp.image_id IN ({marks})",
        image_ids,
    )
    labels = {}
    for image_id, *names in rows:
        parts = {n.casefold(): n for n in names if n}
        if parts:
            labels[image_id] = ", ".join(parts.values())
    return labels


def place_counts(conn: sqlite3.Connection) -> tuple[int, int, int]:
    """(photos with GPS, photos with a place, photos with a landmark)."""
    row = conn.execute(
        "SELECT (SELECT COUNT(*) FROM images WHERE lat IS NOT NULL AND lon IS NOT NULL),"
        " (SELECT COUNT(*) FROM photo_places WHERE COALESCE(city_id, district_id, state_id,"
        " country_id, landmark_id) IS NOT NULL),"
        " (SELECT COUNT(*) FROM photo_places WHERE landmark_id IS NOT NULL)"
    ).fetchone()
    return row[0], row[1], row[2]
