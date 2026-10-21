"""Command-line interface: `uv run imgsearch --help`.

The commands live in `imgsearch/commands/`; this module builds the app and registers them.
"""

from pathlib import Path
from typing import Annotated

import typer

from imgsearch.commands import cloud, evaluate, index, query
from imgsearch.commands.index import STAGES
from imgsearch.config import DEFAULT_CONFIG_PATH, load_config

__all__ = ["STAGES", "app"]

app = typer.Typer(no_args_is_help=True, add_completion=False)


@app.callback()
def main(
    ctx: typer.Context,
    config: Annotated[Path, typer.Option(help="Path to config.yaml.")] = DEFAULT_CONFIG_PATH,
) -> None:
    """Personal image search."""
    ctx.obj = load_config(config)
    ctx.meta["config_path"] = config  # `eval` copies the file into its run folder


# In the order `--help` lists them.
app.command("status")(index.status)
app.command("ingest")(index.ingest)
app.command("run")(index.run_command)
app.command("rewrite")(query.rewrite)
app.command("search")(query.search)
app.command("eval")(evaluate.eval_command)
app.command("eval-compare")(evaluate.eval_compare)
app.command("eval-review")(evaluate.eval_review)
app.command("import")(index.import_command)
app.command("update")(index.update_command)
app.command("verify")(index.verify_command)
app.command("upload")(cloud.upload_command)
app.command("evict")(cloud.evict_command)
app.command("fetch")(cloud.fetch_command)
app.command("restore-check")(cloud.restore_check_command)
app.add_typer(cloud.key_app, name="s3-key")
