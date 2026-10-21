"""`eval`, `eval-compare` and `eval-review` (M7)."""

from contextlib import closing
from pathlib import Path
from typing import Annotated

import typer

from imgsearch.config import Config
from imgsearch.eval.main import evaluate, write_review
from imgsearch.eval.queries import counts, load_queries
from imgsearch.eval.run import load_metrics
from imgsearch.eval.tables import all_tables, compare_table
from imgsearch.store.db import connect


def eval_command(ctx: typer.Context) -> None:
    """Run every ablation on eval.queries and write a run folder with a contact sheet."""
    config: Config = ctx.obj
    with closing(connect(config)) as conn:
        run_dir = evaluate(config, ctx.meta["config_path"], conn, typer.echo)
    typer.echo("")
    for line in all_tables(load_metrics(run_dir)):
        typer.echo(line)
    typer.echo(f"\nrun: {run_dir}\ncontact sheet: {run_dir / 'contact_sheet.html'}")


def eval_compare(
    run_a: Annotated[Path, typer.Argument(help="Earlier run folder.")],
    run_b: Annotated[Path, typer.Argument(help="Later run folder.")],
) -> None:
    """Print the overall metrics of two eval runs side by side, with the change."""
    for line in compare_table(load_metrics(run_a), load_metrics(run_b), "A", "B"):
        typer.echo(line)
    typer.echo(f"A = {run_a}\nB = {run_b}")


def eval_review(ctx: typer.Context) -> None:
    """Check eval.queries and write an HTML sheet of each query's expected photos."""
    config: Config = ctx.obj
    with closing(connect(config)) as conn:
        typer.echo(counts(load_queries(config.eval.queries, conn)))
        typer.echo(f"review sheet: {write_review(config, conn)}")
