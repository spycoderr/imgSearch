"""Telegram (plan D13's fallback, now the active chat app): a small Bot API client over httpx,
update filtering, and the long-polling loop. No webhook and no listening port.

The bot token is part of every Bot API URL, so nothing here logs a URL, and error text that
could hold one goes through `redact`. Logs never show message text, user or chat ids, names
or photos; they show update ids, counts and timings.
"""

import json
import logging
import mimetypes
import os
import threading
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from imgsearch.chat.env import read_env, split_list
from imgsearch.chat.service import Incoming
from imgsearch.config import DEFAULT_ENV_FILE, TelegramConfig

log = logging.getLogger(__name__)

SECRET_KEYS = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_USER_IDS")
RATE_LIMIT_TRIES = 3  # a request answered 429 is retried after `retry_after`, this many tries
MAX_BACKOFF_S = 60.0  # longest wait between getUpdates retries after network errors
ALBUM_MAX = 10  # sendMediaGroup takes 2-10 items


@dataclass(frozen=True)
class Secrets:
    """From .env. Kept out of repr, so a traceback or a log line can't show them."""

    token: str = field(repr=False)
    allowed_user_ids: frozenset[int] = field(repr=False)


def load_secrets(
    env_file: Path = DEFAULT_ENV_FILE, environ: Mapping[str, str] = os.environ
) -> Secrets:
    env = read_env(SECRET_KEYS, env_file, environ)
    ids = split_list(env["TELEGRAM_ALLOWED_USER_IDS"])
    if not ids or not all(i.isdigit() for i in ids):
        raise ValueError(
            "TELEGRAM_ALLOWED_USER_IDS must be numeric user ids, comma-separated (not"
            " usernames, which can change); find yours with `imgsearch telegram whoami`"
        )
    return Secrets(token=env["TELEGRAM_BOT_TOKEN"], allowed_user_ids=frozenset(map(int, ids)))


class TelegramError(Exception):
    """The Bot API answered `ok: false`."""

    def __init__(
        self, method: str, error_code: int, description: str, retry_after: int | None = None
    ) -> None:
        super().__init__(f"{method}: {error_code} {description}")
        self.error_code = error_code
        self.retry_after = retry_after  # seconds, when rate-limited (429)


@dataclass(frozen=True)
class Upload:
    """A file to send in a multipart request, under the form field `field`."""

    field: str
    path: Path
    filename: str
    mime: str


class TelegramClient:
    """A `Messenger` for Telegram, plus the calls the polling loop needs.

    A 429 answer is retried after the `retry_after` Telegram gives, up to RATE_LIMIT_TRIES
    tries. `sleep` is there for tests.
    """

    def __init__(
        self,
        config: TelegramConfig,
        token: str,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.token = token
        self.base = f"{config.api_url}/bot{token}"
        self.timeout = config.timeout_s
        if client is None:
            # Binding to 0.0.0.0 makes every connection IPv4.
            local = "0.0.0.0" if config.ipv4_only else None
            client = httpx.Client(transport=httpx.HTTPTransport(local_address=local))
        self.client = client
        self.sleep = sleep

    def redact(self, text: str) -> str:
        return text.replace(self.token, "<token>")

    def call(
        self,
        method: str,
        data: Mapping[str, str | int] | None = None,
        uploads: Sequence[Upload] = (),
        timeout: float | None = None,
    ) -> Any:
        """Call a Bot API method; returns its `result` or raises TelegramError."""
        for attempt in range(1, RATE_LIMIT_TRIES + 1):
            with ExitStack() as stack:  # files are reopened on each try
                files = {
                    u.field: (u.filename, stack.enter_context(u.path.open("rb")), u.mime)
                    for u in uploads
                }
                response = self.client.post(
                    f"{self.base}/{method}",
                    data={k: str(v) for k, v in (data or {}).items()},
                    files=files or None,
                    timeout=timeout or self.timeout,
                )
            try:
                body = response.json()
            except ValueError:
                raise TelegramError(method, response.status_code, "not a JSON answer") from None
            if body.get("ok"):
                return body["result"]
            error = TelegramError(
                method,
                body.get("error_code", response.status_code),
                body.get("description", ""),
                body.get("parameters", {}).get("retry_after"),
            )
            if error.retry_after is None or attempt == RATE_LIMIT_TRIES:
                raise error
            log.warning("%s rate-limited; retrying in %ss", method, error.retry_after)
            self.sleep(error.retry_after)

    # ---- Messenger ----

    def send_text(self, to: str, text: str) -> None:
        self.call("sendMessage", {"chat_id": to, "text": text})

    def send_photos(self, to: str, photos: list[tuple[Path, str]]) -> None:
        """One album per 10 photos (`sendMediaGroup`); a single photo goes by `sendPhoto`."""
        for start in range(0, len(photos), ALBUM_MAX):
            chunk = photos[start : start + ALBUM_MAX]
            if len(chunk) == 1:
                path, caption = chunk[0]
                upload = Upload("photo", path, path.name, "image/jpeg")
                self.call("sendPhoto", {"chat_id": to, "caption": caption}, [upload])
                continue
            media = [
                {"type": "photo", "media": f"attach://photo{i}", "caption": caption}
                for i, (_, caption) in enumerate(chunk)
            ]
            uploads = [
                Upload(f"photo{i}", path, path.name, "image/jpeg")
                for i, (path, _) in enumerate(chunk)
            ]
            self.call("sendMediaGroup", {"chat_id": to, "media": json.dumps(media)}, uploads)

    def send_document(self, to: str, path: Path, filename: str, caption: str) -> None:
        mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        upload = Upload("document", path, filename, mime)
        self.call("sendDocument", {"chat_id": to, "caption": caption}, [upload])

    def show_activity(self, to: str, action: str) -> None:
        self.call("sendChatAction", {"chat_id": to, "action": action})

    # ---- receiving ----

    def get_updates(self, offset: int | None, timeout_s: int) -> list[dict[str, Any]]:
        """Long-poll for new messages. Passing `offset` confirms every earlier update."""
        data: dict[str, str | int] = {
            "timeout": timeout_s,
            "allowed_updates": json.dumps(["message"]),
        }
        if offset is not None:
            data["offset"] = offset
        return self.call("getUpdates", data, timeout=timeout_s + self.timeout)


def classify(update: Mapping[str, Any], allowed_user_ids: frozenset[int]) -> Incoming | str:
    """The message in `update` if it comes from an allowed user in a private chat with the
    bot, else why it is ignored (for counting; never the sender)."""
    message = update.get("message")
    if message is None:
        return "not a new message"
    if message["chat"]["type"] != "private":
        return "group or channel"
    user = message.get("from")
    if user is None or user["id"] not in allowed_user_ids:
        return "user not allowed"
    update_id = update["update_id"]
    text = message.get("text")
    return Incoming(
        message_id=str(update_id),
        log_id=f"u{update_id}",
        sender=str(message["chat"]["id"]),
        kind="text" if text is not None else "other",
        text=text,
    )


def run_polling(
    client: TelegramClient,
    allowed_user_ids: frozenset[int],
    submit: Callable[[Incoming], None],
    poll_timeout_s: int,
    stop: threading.Event,
) -> None:
    """Poll until `stop` is set (or KeyboardInterrupt), queueing allowed messages.

    Network errors and refused calls back off (1 s doubling to MAX_BACKOFF_S) and retry. A
    wrong token (401, 404) fails at once. On the way out, the last offset is confirmed so
    handled messages aren't delivered again after a restart.
    """
    client.call("deleteWebhook")  # getUpdates refuses to work while a webhook is set
    log.info("polling for messages")
    offset: int | None = None
    backoff = 1.0
    try:
        while not stop.is_set():
            try:
                updates = client.get_updates(offset, poll_timeout_s)
            except (httpx.TransportError, TelegramError) as e:
                if isinstance(e, TelegramError) and e.error_code in (401, 404):
                    raise  # the token is wrong: retrying can't help
                log.warning(
                    "getUpdates failed (%s: %s); retrying in %.0fs",
                    type(e).__name__,
                    client.redact(str(e)),
                    backoff,
                )
                client.sleep(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF_S)
                continue
            backoff = 1.0
            ignored: Counter[str] = Counter()
            for update in updates:
                offset = update["update_id"] + 1
                try:
                    item = classify(update, allowed_user_ids)
                except (KeyError, TypeError) as e:  # one odd update must not stop the bot
                    log.warning("update u%d unreadable: %s", update["update_id"], type(e).__name__)
                    continue
                if isinstance(item, str):
                    ignored[item] += 1
                else:
                    submit(item)
                    log.info("update %s queued (%s)", item.log_id, item.kind)
            if ignored:
                log.info("ignored: %s", ", ".join(f"{n} {why}" for why, n in ignored.items()))
    finally:
        if offset is not None:
            try:
                client.get_updates(offset, 0)
            except (httpx.TransportError, TelegramError) as e:
                log.warning("couldn't confirm the last updates: %s", type(e).__name__)


@dataclass(frozen=True)
class Sender:
    """Who sent a pending update, for `imgsearch telegram whoami` (printed, never logged)."""

    user_id: int
    name: str
    username: str | None
    chat_type: str


def pending_senders(client: TelegramClient) -> list[Sender]:
    """Senders of updates waiting for the bot. Calls getUpdates without an offset, so
    nothing is marked read and `imgsearch bot` still gets every message."""
    client.call("deleteWebhook")
    found: dict[int, Sender] = {}
    for update in client.call("getUpdates", {"timeout": 0}):
        message = update.get("message") or update.get("edited_message")
        if not message or "from" not in message:
            continue
        user = message["from"]
        found[user["id"]] = Sender(
            user["id"],
            " ".join(filter(None, (user.get("first_name"), user.get("last_name")))),
            user.get("username"),
            message["chat"]["type"],
        )
    return list(found.values())
