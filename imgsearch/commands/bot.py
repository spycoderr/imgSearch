"""`bot` (the chat bot, P3) and `telegram whoami`.

`imgsearch bot` runs the chat app named by `chat.adapter`:
- telegram: long polling from this Mac; no webhook and no listening port.
- whatsapp (parked): a webhook server on 127.0.0.1 that a tunnel makes public.
"""

import logging
import signal
import threading
import time
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

import httpx
import typer
from fastapi import FastAPI

from imgsearch.chat import telegram, whatsapp
from imgsearch.chat.bot import ChatBot, Messenger, OriginalUnavailable
from imgsearch.chat.env import read_env
from imgsearch.chat.service import Incoming, LazySearcher, Worker
from imgsearch.chat.webhook import create_app
from imgsearch.config import Config
from imgsearch.interfaces import StoreUnavailable
from imgsearch.search.hybrid import Searcher, open_searcher
from imgsearch.store.cache import fetch
from imgsearch.store.db import connect
from imgsearch.store.keychain import KeychainError, KeyMissing, read_key

HOST = "127.0.0.1"  # WhatsApp's webhook server: never all interfaces; only the tunnel reaches it

telegram_app = typer.Typer(no_args_is_help=True, help="Telegram helpers.")


def bot_command(ctx: typer.Context) -> None:
    """Run the chat bot named by chat.adapter in config.yaml (see README, "Chat bot")."""
    config: Config = ctx.obj
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    try:
        if config.chat.adapter == "telegram":
            secrets = telegram.load_secrets()
            signal.signal(signal.SIGTERM, signal.default_int_handler)  # launchd's stop = Ctrl-C
            run_telegram(config, secrets, *build_telegram(config, secrets), threading.Event())
        else:
            run_whatsapp(config, whatsapp.load_secrets())
    except ValueError as e:  # a secret is missing from .env
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from None


@telegram_app.command("whoami")
def whoami_command(ctx: typer.Context) -> None:
    """Print the user ids of people who messaged the bot, to put in TELEGRAM_ALLOWED_USER_IDS.

    Reads the waiting messages without marking them read. Prints here only; logs nothing.
    """
    config: Config = ctx.obj
    try:
        token = read_env(["TELEGRAM_BOT_TOKEN"])["TELEGRAM_BOT_TOKEN"]
    except ValueError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(1) from None
    quiet_http_logs()
    senders = telegram.pending_senders(telegram.TelegramClient(config.telegram, token))
    if not senders:
        typer.echo("No messages waiting. Send any message to the bot, then run this again.")
    for s in senders:
        username = f" (@{s.username})" if s.username else ""
        typer.echo(f"user id {s.user_id}: {s.name}{username}, {s.chat_type} chat")


def run_telegram(
    config: Config,
    secrets: telegram.Secrets,
    client: telegram.TelegramClient,
    worker: Worker,
    stop: threading.Event,
) -> None:
    """Poll until `stop` is set or Ctrl-C, then finish the queued messages."""
    worker.start()
    try:
        telegram.run_polling(
            client, secrets.allowed_user_ids, worker.submit, config.telegram.poll_timeout_s, stop
        )
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("stopping: finishing queued messages")
    finally:
        worker.stop()


def build_telegram(
    config: Config,
    secrets: telegram.Secrets,
    http: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
    build_searcher: Callable[[], Searcher] | None = None,
    fetch_original: Callable[[str], Path] | None = None,
) -> tuple[telegram.TelegramClient, Worker]:
    """The Bot API client and the worker running the bot. Tests pass fakes for the HTTP
    client, sleeps, the searcher and the S3 fetch."""
    client = telegram.TelegramClient(config.telegram, secrets.token, http, sleep)
    worker, _ = make_bot(
        config, client, config.telegram.original_max_mb, build_searcher, fetch_original
    )
    return client, worker


def run_whatsapp(config: Config, secrets: whatsapp.Secrets) -> None:
    import uvicorn  # only the WhatsApp bot needs a server

    app = build_whatsapp_app(config, secrets)
    typer.echo(f"listening on http://{HOST}:{config.whatsapp.port} (webhook path /webhook)")
    # No access log: the verification request carries the verify token in its query string.
    uvicorn.run(app, host=HOST, port=config.whatsapp.port, access_log=False, log_config=None)


def build_whatsapp_app(
    config: Config,
    secrets: whatsapp.Secrets,
    http: httpx.Client | None = None,
    build_searcher: Callable[[], Searcher] | None = None,
    fetch_original: Callable[[str], Path] | None = None,
) -> FastAPI:
    """The webhook app with its worker and bot. Tests pass fakes as for build_telegram."""
    client = whatsapp.GraphClient(config.whatsapp, secrets, http)
    worker, searcher = make_bot(
        config, client, config.whatsapp.original_max_mb, build_searcher, fetch_original
    )

    def status() -> dict[str, object]:
        return {"models_loaded": searcher.loaded, "queued": worker.queue.qsize()}

    return create_app(secrets, worker, status)


def make_bot(
    config: Config,
    messenger: Messenger,
    original_max_mb: float,
    build_searcher: Callable[[], Searcher] | None,
    fetch_original: Callable[[str], Path] | None,
) -> tuple[Worker, LazySearcher]:
    """The shared part: a ChatBot on a worker thread with a searcher that unloads when
    idle (D21). The worker isn't started."""
    quiet_http_logs()
    searcher = LazySearcher(build_searcher or (lambda: open_searcher(config, connect(config))))
    bot = ChatBot(
        config,
        messenger,
        searcher,
        fetch_original or (lambda image_id: restore(config, image_id)),
        original_max_mb,
    )

    def handle(message: Incoming) -> None:
        if message.kind == "text" and message.text is not None:
            bot.on_message(message.text, message.sender)
        else:
            bot.on_unsupported(message.sender)

    return Worker(handle, searcher.unload, config.chat.idle_unload_s), searcher


def quiet_http_logs() -> None:
    # httpx logs every request URL at INFO. Bot API URLs hold the token; Graph URLs the
    # phone-number id.
    logging.getLogger("httpx").setLevel(logging.WARNING)


def restore(config: Config, image_id: str) -> Path:
    """The P1.6 fetch: restore an evicted original from S3 and return its path."""
    from imgsearch.commands.cloud import open_store  # boto3 is slow to import

    try:
        key, store = read_key(config.s3), open_store(config)
        with closing(connect(config)) as conn:
            return fetch(config, conn, store, key, image_id).path
    except (KeyMissing, KeychainError, StoreUnavailable, LookupError) as e:
        raise OriginalUnavailable("can't restore the original from S3") from e
