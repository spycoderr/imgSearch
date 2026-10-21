"""The HTTP side of the bot: a thin FastAPI app with the WhatsApp webhook and `/health`.

- `GET /webhook`: Meta's verification handshake (`hub.verify_token`, echo `hub.challenge`).
- `POST /webhook`: checks `X-Hub-Signature-256` (401 if wrong), logs status callbacks for
  messages we sent (sent, delivered, read, failed with error codes), drops senders not on
  the allowlist and repeated message ids, queues the rest and answers 200 at once (Meta
  retries slow or failed deliveries). The worker thread does the searching.
- `GET /health`: for the user on the Mac; 404 through the tunnel.

No other routes: FastAPI's docs and schema pages are off, and no files are served.
"""

import hmac
import json
import logging
from collections import OrderedDict
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import PlainTextResponse

from imgsearch.chat.service import Worker
from imgsearch.chat.whatsapp import (
    DeliveryStatus,
    Secrets,
    delivery_statuses,
    incoming_messages,
    log_id,
    signature_ok,
)

log = logging.getLogger(__name__)

TUNNEL_HEADER = "cf-connecting-ip"  # Cloudflare Tunnel adds it to every request it forwards


class SeenIds:
    """The last `size` message ids, so a redelivered message is handled once."""

    def __init__(self, size: int = 1000) -> None:
        self.size = size
        self.ids: OrderedDict[str, None] = OrderedDict()

    def first_time(self, message_id: str) -> bool:
        if message_id in self.ids:
            return False
        self.ids[message_id] = None
        if len(self.ids) > self.size:
            self.ids.popitem(last=False)
        return True


def log_status(status: DeliveryStatus) -> None:
    """Log a status callback: id, status and error codes and titles (Meta's generic text),
    never the recipient. A failure is a warning: it is the only sign a reply didn't arrive."""
    if status.errors:
        errors = "; ".join(f"error {code}: {title}" for code, title in status.errors)
        log.warning("message %s %s (%s)", log_id(status.message_id), status.status, errors)
    else:
        log.info("message %s %s", log_id(status.message_id), status.status)


def create_app(secrets: Secrets, worker: Worker, status: Callable[[], dict[str, Any]]) -> FastAPI:
    """The app; it starts the worker on startup and stops it (after its queue) on shutdown."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        worker.start()
        yield
        worker.stop()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    seen = SeenIds()

    @app.get("/webhook")
    def verify(
        mode: Annotated[str, Query(alias="hub.mode")] = "",
        token: Annotated[str, Query(alias="hub.verify_token")] = "",
        challenge: Annotated[str, Query(alias="hub.challenge")] = "",
    ) -> PlainTextResponse:
        if mode == "subscribe" and hmac.compare_digest(
            token.encode(), secrets.verify_token.encode()
        ):
            log.info("webhook verified")
            return PlainTextResponse(challenge)
        log.warning("webhook verification refused")
        raise HTTPException(status_code=403)

    @app.post("/webhook")
    async def receive(request: Request) -> Response:
        body = await request.body()
        if not signature_ok(secrets.app_secret, body, request.headers.get("x-hub-signature-256")):
            log.warning("webhook: bad or missing signature, %d bytes ignored", len(body))
            raise HTTPException(status_code=401)
        payload = json.loads(body)
        for status in delivery_statuses(payload, secrets.phone_number_id):
            log_status(status)
        not_allowed = repeated = 0
        for message in incoming_messages(payload, secrets.phone_number_id):
            if message.sender not in secrets.allowed_senders:
                not_allowed += 1
            elif not seen.first_time(message.message_id):
                repeated += 1
            else:
                worker.submit(message)
                log.info("message %s queued (%s)", log_id(message.message_id), message.kind)
        if not_allowed or repeated:
            log.info("webhook: %d from senders not allowed, %d repeated", not_allowed, repeated)
        return Response(status_code=200)

    @app.get("/health")
    def health(request: Request) -> dict[str, Any]:
        if TUNNEL_HEADER in request.headers:
            raise HTTPException(status_code=404)
        return {"status": "ok", **status()}

    return app
