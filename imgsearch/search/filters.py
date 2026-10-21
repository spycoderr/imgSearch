"""The filters every retriever applies in SQL, before its `k` limit: the query's dates
(P1.7) and its place (P1.8). Both must hold."""

from imgsearch.interfaces import ParsedQuery
from imgsearch.store.dates import date_filter_sql
from imgsearch.store.places import place_filter_sql


def filter_sql(q: ParsedQuery) -> tuple[str, tuple]:
    """`AND image_id IN (...)` clauses for `q.date_range` and `q.place_filter`, and their
    parameters; ("", ()) when the query has neither."""
    dates, date_params = date_filter_sql(q.date_range)
    place, place_params = place_filter_sql(q.place_filter)
    return dates + place, (*date_params, *place_params)
