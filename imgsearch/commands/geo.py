"""`imgsearch geo download`: fetch the GeoNames files for the `places` stage (P1.8)."""

from typing import Annotated

import httpx
import typer

from imgsearch.config import Config
from imgsearch.sources.geonames import MANIFEST, download

geo_app = typer.Typer(no_args_is_help=True, help="Offline place data (GeoNames, CC BY 4.0).")


@geo_app.command("download")
def download_command(
    ctx: typer.Context,
    force: Annotated[
        bool, typer.Option("--force", help="Download again even if the files are there.")
    ] = False,
) -> None:
    """Download the country dumps in places.countries plus the admin and country name files
    into places.dir, once. Sizes and SHA-256 go into MANIFEST.tsv there."""
    config: Config = ctx.obj
    with httpx.Client(timeout=60, follow_redirects=True) as client:
        results = download(config.places, client, force=force)
    for r in results:
        state = "already there" if r.skipped else "downloaded"
        typer.echo(f"{r.name}: {state}, {r.size / 1e6:.1f} MB, sha256 {r.sha256}")
    typer.echo(f"manifest: {config.places.dir / MANIFEST}")
