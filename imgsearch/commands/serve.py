"""`serve`: the WhatsApp bot (P3). A webhook server on 127.0.0.1; the tunnel makes it public."""

import logging
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

import httpx
import typer
from fastapi import FastAPI

from imgsearch.chat.bot import OriginalUnavailable, WhatsAppAdapter
from imgsearch.chat.service import LazySearcher, Worker
from imgsearch.chat.webhook import create_app
from imgsearch.chat.whatsapp import GraphClient, Incoming, Secrets, load_secrets
from imgsearch.config import Config
from imgsearch.interfaces import StoreUnavailable
from imgsearch.search.hybrid import Searcher, open_searcher
from imgsearch.store.cache import fetch
from imgsearch.store.db import connect
from imgsearch.store.keychain import KeychainError, KeyMissing, read_key

HOST = "127.0.0.1"  # never all interfaces: only the tunnel reaches the server from outside


def serve_command(ctx: typer.Context) -> None:
    """Run the WhatsApp bot's webhook server on 127.0.0.1 (see README, "WhatsApp bot")."""
    import uvicorn  # only this command needs the server

    config: Config = ctx.obj
    try:
        secrets = load_secrets()
    except ValueError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from None
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    app = build_app(config, secrets)
    typer.echo(f"listening on http://{HOST}:{config.whatsapp.port} (webhook path /webhook)")
    # No access log: the verification request carries the verify token in its query string.
    uvicorn.run(app, host=HOST, port=config.whatsapp.port, access_log=False, log_config=None)


def build_app(
    config: Config,
    secrets: Secrets,
    http: httpx.Client | None = None,
    build_searcher: Callable[[], Searcher] | None = None,
    fetch_original: Callable[[str], Path] | None = None,
) -> FastAPI:
    """The webhook app with its worker and adapter. Tests pass fakes for the Graph API's
    HTTP client, the searcher and the S3 fetch."""
    # httpx logs every request URL at INFO, and the Graph URLs hold the phone-number id.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    searcher = LazySearcher(build_searcher or (lambda: open_searcher(config, connect(config))))
    client = GraphClient(config.whatsapp, secrets, http)
    adapter = WhatsAppAdapter(
        config, client, searcher, fetch_original or (lambda image_id: restore(config, image_id))
    )

    def handle(message: Incoming) -> None:
        if message.kind == "text" and message.text is not None:
            adapter.on_message(message.text, message.sender)
        else:
            adapter.on_unsupported(message.sender)

    worker = Worker(handle, searcher.unload, config.whatsapp.idle_unload_s)

    def status() -> dict[str, object]:
        return {"models_loaded": searcher.loaded, "queued": worker.queue.qsize()}

    return create_app(secrets, worker, status)


def restore(config: Config, image_id: str) -> Path:
    """The P1.6 fetch: restore an evicted original from S3 and return its path."""
    from imgsearch.commands.cloud import open_store  # boto3 is slow to import

    try:
        key, store = read_key(config.s3), open_store(config)
        with closing(connect(config)) as conn:
            return fetch(config, conn, store, key, image_id).path
    except (KeyMissing, KeychainError, StoreUnavailable, LookupError) as e:
        raise OriginalUnavailable("can't restore the original from S3") from e
