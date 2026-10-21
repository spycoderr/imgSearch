"""WhatsApp Cloud API (plan D13): secrets, the webhook signature and payload, a Graph client.

Nothing here logs message text, phone numbers or images.
"""

import hashlib
import hmac
import logging
import mimetypes
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from dotenv import dotenv_values

from imgsearch.config import DEFAULT_ENV_FILE, WhatsAppConfig

log = logging.getLogger(__name__)

SECRET_KEYS = (
    "WHATSAPP_TOKEN",
    "WHATSAPP_PHONE_NUMBER_ID",
    "WHATSAPP_APP_SECRET",
    "WHATSAPP_VERIFY_TOKEN",
    "WHATSAPP_ALLOWED_SENDERS",
)


@dataclass(frozen=True)
class Secrets:
    """From .env. Kept out of repr, so a traceback or a log line can't show them."""

    token: str = field(repr=False)
    phone_number_id: str = field(repr=False)
    app_secret: str = field(repr=False)
    verify_token: str = field(repr=False)
    allowed_senders: frozenset[str] = field(repr=False)  # digits only, with country code


def load_secrets(
    env_file: Path = DEFAULT_ENV_FILE, environ: Mapping[str, str] = os.environ
) -> Secrets:
    """Read the WhatsApp secrets from the environment, else from `env_file`."""
    env = {**dotenv_values(env_file), **environ}
    missing = [key for key in SECRET_KEYS if not env.get(key)]
    if missing:
        raise ValueError(f"{', '.join(missing)} not set: put them in .env (see .env.example)")
    senders = frozenset(digits(n) for n in env["WHATSAPP_ALLOWED_SENDERS"].split(",") if digits(n))
    if not senders:
        raise ValueError("WHATSAPP_ALLOWED_SENDERS has no phone numbers")
    return Secrets(
        token=env["WHATSAPP_TOKEN"],
        phone_number_id=env["WHATSAPP_PHONE_NUMBER_ID"],
        app_secret=env["WHATSAPP_APP_SECRET"],
        verify_token=env["WHATSAPP_VERIFY_TOKEN"],
        allowed_senders=senders,
    )


def digits(number: str) -> str:
    """A phone number as WhatsApp sends it in `from`: digits only ("+91 98..." -> "9198...")."""
    return "".join(c for c in number if c.isdigit())


def signature_ok(app_secret: str, body: bytes, header: str | None) -> bool:
    """Check `X-Hub-Signature-256`: "sha256=" + hex HMAC-SHA256 of the raw body."""
    if header is None or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.encode(), header.removeprefix("sha256=").encode())


@dataclass(frozen=True)
class Incoming:
    """One message from a webhook payload."""

    message_id: str
    sender: str = field(repr=False)  # the sender's WhatsApp id: phone number digits
    kind: str  # "text", "image", "audio", ...
    text: str | None = field(repr=False)  # the body of a text message


def incoming_messages(payload: Mapping[str, Any], phone_number_id: str) -> list[Incoming]:
    """The messages in a webhook payload sent to our number."""
    found = []
    for value in _values(payload, phone_number_id):
        for m in value.get("messages", []):
            text = m["text"]["body"] if m["type"] == "text" else None
            found.append(Incoming(m["id"], m["from"], m["type"], text))
    return found


@dataclass(frozen=True)
class DeliveryStatus:
    """A status callback for a message we sent. `recipient_id` (a phone number) is not kept."""

    message_id: str
    status: str  # sent, delivered, read, played or failed
    errors: tuple[tuple[int, str], ...]  # (code, title) for each error; empty unless failed


def delivery_statuses(payload: Mapping[str, Any], phone_number_id: str) -> list[DeliveryStatus]:
    """The status callbacks in a webhook payload for our number."""
    found = []
    for value in _values(payload, phone_number_id):
        for s in value.get("statuses", []):
            errors = tuple((e["code"], e.get("title", "")) for e in s.get("errors", []))
            found.append(DeliveryStatus(s["id"], s["status"], errors))
    return found


def _values(payload: Mapping[str, Any], phone_number_id: str) -> list[Mapping[str, Any]]:
    """Each `messages` change's value addressed to our phone number id."""
    return [
        change["value"]
        for entry in payload.get("entry", [])
        for change in entry.get("changes", [])
        if change.get("field") == "messages"
        and change["value"].get("metadata", {}).get("phone_number_id") == phone_number_id
    ]


def log_id(message_id: str) -> str:
    """A short stand-in for a message id in logs. A `wamid` is base64 that includes the
    other party's phone number, so it is never logged as is."""
    return "m-" + hashlib.sha256(message_id.encode()).hexdigest()[:10]


class GraphError(Exception):
    """The Graph API refused a request."""


class GraphClient:
    """Uploads media and sends messages from our business number."""

    def __init__(
        self, config: WhatsAppConfig, secrets: Secrets, client: httpx.Client | None = None
    ) -> None:
        self.base = f"{config.graph_url}/{config.api_version}/{secrets.phone_number_id}"
        self.client = client or httpx.Client(timeout=config.timeout_s)
        self.headers = {"Authorization": f"Bearer {secrets.token}"}

    def send_text(self, to: str, text: str) -> None:
        self._send(to, "text", {"body": text})

    def send_image(self, to: str, path: Path, caption: str) -> None:
        """Upload a JPEG and send it by media id (no public link to the photo)."""
        media_id = self.upload(path, "image/jpeg")
        self._send(to, "image", {"id": media_id, "caption": caption})

    def send_document(self, to: str, path: Path, filename: str, caption: str) -> None:
        mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        media_id = self.upload(path, mime)
        self._send(to, "document", {"id": media_id, "filename": filename, "caption": caption})

    def upload(self, path: Path, mime: str) -> str:
        """Upload a file to the media endpoint; returns its media id."""
        with path.open("rb") as f:
            response = self.client.post(
                f"{self.base}/media",
                headers=self.headers,
                data={"messaging_product": "whatsapp", "type": mime},
                files={"file": (path.name, f, mime)},
            )
        return _checked(response)["id"]

    def _send(self, to: str, kind: str, body: dict[str, str]) -> None:
        message = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": kind,
            kind: body,
        }
        _checked(self.client.post(f"{self.base}/messages", headers=self.headers, json=message))


def _checked(response: httpx.Response) -> dict[str, Any]:
    if response.is_success:
        return response.json()
    try:
        error = response.json().get("error", {})
    except ValueError:  # not JSON, e.g. a proxy error page
        error = {}
    raise GraphError(
        f"Graph API {response.status_code}: code {error.get('code')}, {error.get('message')}"
        f" (fbtrace_id {error.get('fbtrace_id')})"
    )
