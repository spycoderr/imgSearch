"""`rewrite` and `search`."""

import dataclasses
import json
import time
from contextlib import closing
from typing import Annotated

import typer

from imgsearch.config import Config
from imgsearch.models.rewriter import OllamaRewriter
from imgsearch.search.hybrid import SIGNALS, open_searcher
from imgsearch.search.show import response_lines
from imgsearch.store.db import connect
from imgsearch.store.roots import is_available

NO_ORIGINALS = "pen drive not mounted: full-size images are unavailable"


def rewrite(ctx: typer.Context, query: str) -> None:
    """Rewrite a query into English JSON with the rewrite LLM (falls back to the query as given)."""
    config: Config = ctx.obj
    start = time.perf_counter()
    parsed = OllamaRewriter(config.models.rewriter).rewrite(query)
    elapsed = time.perf_counter() - start
    fields = dataclasses.asdict(parsed)
    del fields["place_filter"]  # set by search, not by the rewrite
    typer.echo(json.dumps(fields, ensure_ascii=False, indent=2))
    typer.echo(f"took {elapsed:.2f}s")


def search(
    ctx: typer.Context,
    query: str,
    k: Annotated[int | None, typer.Option(help="Results to show (default search.top_k).")] = None,
    no_rewrite: Annotated[
        bool, typer.Option("--no-rewrite", help="Search the query as given.")
    ] = False,
    signals: Annotated[
        str | None,
        typer.Option(
            help="Comma-separated retrievers: vector, text, or vector,text for hybrid"
            " (default: search.default_signals in config.yaml)."
        ),
    ] = None,
    no_date_filter: Annotated[
        bool,
        typer.Option("--no-date-filter", help="Ignore a date found in the query."),
    ] = False,
    place_filter: Annotated[
        bool | None,
        typer.Option(
            "--place-filter/--no-place-filter",
            help="Limit results to a place found in the query (default: search.place_filter).",
        ),
    ] = None,
) -> None:
    """Search: rewrite QUERY to English, run the chosen retrievers, fuse them with RRF.

    A date in the query ("15 sept", "kal", "last week") limits the results to photos from
    then (EXIF date, else file date), unless nothing matches or --no-date-filter is given.
    A place in the query ("Goa", "India Gate", "जयपुर") that some photo was taken at limits
    them to photos from there or whose text names it, unless nothing matches or
    --no-place-filter is given."""
    config: Config = ctx.obj
    names = None
    if signals is not None:
        names = [s.strip() for s in signals.split(",") if s.strip()]
        if not names or any(s not in SIGNALS for s in names):
            raise typer.BadParameter(f"choose from: {', '.join(SIGNALS)}", param_hint="--signals")
    with closing(connect(config)) as conn:
        start = time.perf_counter()
        searcher = open_searcher(config, conn, signals=names, rewrite=not no_rewrite)
        loaded = time.perf_counter() - start
        response = searcher.search(
            query,
            k=k,
            rewrite=not no_rewrite,
            date_filter=not no_date_filter,
            place_filter=place_filter,
        )
    for line in response_lines(response):
        typer.echo(line)
    typer.echo(f"model load: {loaded:.2f}s")
    if not is_available(config.originals_root):
        typer.echo(f"note: {NO_ORIGINALS} (results come from the index on the Mac)")
