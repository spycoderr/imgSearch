"""The WhatsApp transport (P3, parked): secrets, signature, payloads, the Graph client and
the webhook end to end. The bot's behaviour is tested in test_chat_bot.py. The Graph API is
an httpx.MockTransport fake and the searcher uses fake retrievers: no network, no models."""

import hashlib
import hmac
import json
import logging
import threading
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from imgsearch.chat.bot import UNSUPPORTED
from imgsearch.chat.service import Incoming
from imgsearch.chat.whatsapp import (
    DeliveryStatus,
    GraphClient,
    GraphError,
    Secrets,
    delivery_statuses,
    incoming_messages,
    load_secrets,
    log_id,
    signature_ok,
)
from imgsearch.commands.bot import HOST, build_whatsapp_app
from imgsearch.config import Config
from imgsearch.search.hybrid import Searcher
from imgsearch.store.db import connect

SENDER = "919800000001"
STRANGER = "919800000009"
PHONE_ID = "1065403522"
QUERY = "kutte ki photo"

SECRETS = Secrets(
    token="test-token",
    phone_number_id=PHONE_ID,
    app_secret="test-app-secret",
    verify_token="test-verify-token",
    allowed_senders=frozenset({SENDER}),
)


# ---- fakes ----


class FakeGraph:
    """The Graph API: records uploads (multipart bodies by media id) and sent messages."""

    def __init__(self) -> None:
        self.media: dict[str, bytes] = {}
        self.messages: list[dict] = []
        self.requests: list[httpx.Request] = []
        self.fail_media = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        request.read()
        self.requests.append(request)
        if request.url.path.endswith("/media"):
            if self.fail_media:
                error = {"code": 131053, "message": "Media upload error", "fbtrace_id": "T1"}
                return httpx.Response(400, json={"error": error})
            media_id = f"media-{len(self.media) + 1}"
            self.media[media_id] = request.content
            return httpx.Response(200, json={"id": media_id})
        self.messages.append(json.loads(request.content))
        return httpx.Response(200, json={"messages": [{"id": "wamid.out"}]})

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))

    def texts(self) -> list[str]:
        return [m["text"]["body"] for m in self.messages if m["type"] == "text"]

    def images(self) -> list[tuple[bytes, str]]:
        """(uploaded multipart body, caption) per image message, in order."""
        return [
            (self.media[m["image"]["id"]], m["image"]["caption"])
            for m in self.messages
            if m["type"] == "image"
        ]


# ---- fixtures ----


@pytest.fixture
def graph() -> FakeGraph:
    return FakeGraph()


# ---- secrets, signature, payload ----


def test_load_secrets_from_env_file_with_environment_overrides(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "WHATSAPP_TOKEN=file-token\nWHATSAPP_PHONE_NUMBER_ID=123\nWHATSAPP_APP_SECRET=s\n"
        "WHATSAPP_VERIFY_TOKEN=v\nWHATSAPP_ALLOWED_SENDERS=+91 98000 00001, 919800000002,\n"
    )
    secrets = load_secrets(env_file, environ={"WHATSAPP_TOKEN": "env-token"})
    assert secrets.token == "env-token"
    assert secrets.phone_number_id == "123"
    assert secrets.allowed_senders == {"919800000001", "919800000002"}
    assert "env-token" not in repr(secrets) and "9198" not in repr(secrets)


def test_load_secrets_names_every_missing_key(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text("WHATSAPP_TOKEN=t\n")
    with pytest.raises(ValueError, match="WHATSAPP_PHONE_NUMBER_ID, WHATSAPP_APP_SECRET"):
        load_secrets(env_file, environ={})


def sign(body: bytes, secret: str = SECRETS.app_secret) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_log_id_hides_the_number_inside_a_wamid():
    wamid = "wamid.HBgLMTY1MDM4Nzk0MzkVAgASGBQzQTRBNjU5OUFFRTAzODEwMTQ0RgA="  # Meta's example
    assert log_id(wamid) == log_id(wamid) != log_id(wamid + "x")
    assert "16503879439" not in log_id(wamid) and "wamid" not in log_id(wamid)


def test_signature_check():
    body = b'{"object": "whatsapp_business_account"}'
    assert signature_ok(SECRETS.app_secret, body, sign(body))
    assert not signature_ok(SECRETS.app_secret, body + b" ", sign(body))
    assert not signature_ok(SECRETS.app_secret, body, sign(body, "other-secret"))
    assert not signature_ok(SECRETS.app_secret, body, sign(body).removeprefix("sha256="))
    assert not signature_ok(SECRETS.app_secret, body, None)


def payload(*messages: dict, phone_id: str = PHONE_ID) -> dict:
    value = {
        "messaging_product": "whatsapp",
        "metadata": {"display_phone_number": "15550783881", "phone_number_id": phone_id},
        "messages": list(messages),
    }
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "1022", "changes": [{"value": value, "field": "messages"}]}],
    }


def text_message(body: str, message_id: str = "wamid.1", sender: str = SENDER) -> dict:
    return {
        "from": sender,
        "id": message_id,
        "timestamp": "1749416383",
        "type": "text",
        "text": {"body": body},
    }


def test_incoming_messages():
    photo = {"from": SENDER, "id": "wamid.2", "timestamp": "1", "type": "image", "image": {}}
    found = incoming_messages(payload(text_message("hi"), photo), PHONE_ID)
    assert found == [
        Incoming("wamid.1", log_id("wamid.1"), SENDER, "text", "hi"),
        Incoming("wamid.2", log_id("wamid.2"), SENDER, "image", None),
    ]
    statuses = payload()
    del statuses["entry"][0]["changes"][0]["value"]["messages"]
    statuses["entry"][0]["changes"][0]["value"]["statuses"] = [{"id": "wamid.out"}]
    assert incoming_messages(statuses, PHONE_ID) == []
    assert incoming_messages(payload(text_message("hi"), phone_id="999"), PHONE_ID) == []


def status_payload(*statuses: dict, phone_id: str = PHONE_ID) -> dict:
    body = payload(phone_id=phone_id)
    value = body["entry"][0]["changes"][0]["value"]
    del value["messages"]
    value["statuses"] = list(statuses)
    return body


SENT = {"id": "wamid.out1", "status": "sent", "timestamp": "1", "recipient_id": SENDER}
FAILED = {
    "id": "wamid.out2",
    "status": "failed",
    "timestamp": "2",
    "recipient_id": SENDER,
    "errors": [
        {
            "code": 131049,
            "title": "This message was not delivered to maintain healthy ecosystem engagement.",
            "message": "This message was not delivered to maintain healthy ecosystem engagement.",
            "error_data": {"details": "In order to maintain a healthy ecosystem engagement."},
        }
    ],
}


def test_delivery_statuses():
    title = FAILED["errors"][0]["title"]
    assert delivery_statuses(status_payload(SENT, FAILED), PHONE_ID) == [
        DeliveryStatus("wamid.out1", "sent", ()),
        DeliveryStatus("wamid.out2", "failed", ((131049, title),)),
    ]
    assert delivery_statuses(status_payload(SENT, phone_id="999"), PHONE_ID) == []
    assert delivery_statuses(payload(text_message("hi")), PHONE_ID) == []


# ---- Graph client ----


def test_graph_client_uploads_then_sends_by_media_id(config: Config, graph, tmp_path: Path):
    photo = tmp_path / "a.jpg"
    photo.write_bytes(b"jpeg bytes")
    GraphClient(config.whatsapp, SECRETS, graph.client()).send_image(SENDER, photo, "1 · dog")

    upload, send = graph.requests
    base = f"{config.whatsapp.graph_url}/{config.whatsapp.api_version}/{PHONE_ID}"
    assert str(upload.url) == f"{base}/media"
    assert upload.headers["authorization"] == "Bearer test-token"
    assert b'name="messaging_product"\r\n\r\nwhatsapp' in upload.content
    assert b"Content-Type: image/jpeg\r\n\r\njpeg bytes" in upload.content
    assert str(send.url) == f"{base}/messages"
    assert send.headers["authorization"] == "Bearer test-token"
    assert json.loads(send.content) == {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": SENDER,
        "type": "image",
        "image": {"id": "media-1", "caption": "1 · dog"},
    }


def test_graph_client_sends_photos_one_message_each(config: Config, graph, tmp_path: Path):
    a, b = tmp_path / "a.jpg", tmp_path / "b.jpg"
    a.write_bytes(b"photo a")
    b.write_bytes(b"photo b")
    client = GraphClient(config.whatsapp, SECRETS, graph.client())
    client.show_activity(SENDER, "typing")  # no typing indicator in the Cloud API: no request
    client.send_photos(SENDER, [(a, "1"), (b, "2")])
    assert [(b"photo a" in body, b"photo b" in body, c) for body, c in graph.images()] == [
        (True, False, "1"),
        (False, True, "2"),
    ]


def test_graph_client_raises_graph_error(config: Config, graph, tmp_path: Path):
    photo = tmp_path / "a.jpg"
    photo.write_bytes(b"x")
    graph.fail_media = True
    client = GraphClient(config.whatsapp, SECRETS, graph.client())
    with pytest.raises(GraphError, match="400: code 131053"):
        client.send_image(SENDER, photo, "c")


def documents(graph: FakeGraph) -> list[tuple[bytes, dict]]:
    return [
        (graph.media[m["document"]["id"]], m["document"])
        for m in graph.messages
        if m["type"] == "document"
    ]


# ---- the HTTP layer, end to end ----


class Service:
    """build_whatsapp_app with fakes. The searcher waits for `gate`, so a test can see the
    webhook answer before the search runs."""

    def __init__(self, config: Config, graph: FakeGraph, make_searcher) -> None:
        self.gate = threading.Event()
        self.graph = graph
        self.searchers: list[Searcher] = []

        def build() -> Searcher:
            self.gate.wait(timeout=5)
            self.searchers.append(make_searcher(connect(config)))  # in the worker thread
            return self.searchers[-1]

        self.app = build_whatsapp_app(config, SECRETS, graph.client(), build, lambda i: Path())

    def queries(self) -> list[str]:
        return [q for s in self.searchers for q in s.rewriter.queries]

    def post(self, client: TestClient, body: dict, signature: str | None = None):
        raw = json.dumps(body).encode()
        headers = {"Content-Type": "application/json"}
        headers["X-Hub-Signature-256"] = signature or sign(raw)
        return client.post("/webhook", content=raw, headers=headers)


@pytest.fixture
def service(config, chat_library, make_searcher, graph) -> Service:
    return Service(config, graph, make_searcher)


def test_webhook_answers_at_once_and_searches_in_the_background(service, graph):
    with TestClient(service.app) as client:
        response = service.post(client, payload(text_message(QUERY)))
        assert response.status_code == 200
        assert graph.messages == []  # the search is still waiting
        service.gate.set()
    # Leaving the client stops the worker after it has handled the queue.
    assert service.queries() == [QUERY]
    assert len(graph.images()) == 5


def test_repeated_delivery_is_handled_once(service, graph):
    service.gate.set()
    with TestClient(service.app) as client:
        body = payload(text_message(QUERY, message_id="wamid.same"))
        assert service.post(client, body).status_code == 200
        assert service.post(client, body).status_code == 200
    assert service.queries() == [QUERY]
    assert len(graph.images()) == 5


def test_sender_not_allowed_gets_no_reply(service, graph, caplog):
    service.gate.set()
    with caplog.at_level(logging.INFO), TestClient(service.app) as client:
        response = service.post(client, payload(text_message(QUERY, sender=STRANGER)))
        assert response.status_code == 200
    assert graph.requests == []
    assert service.queries() == []
    assert "1 from senders not allowed" in caplog.text
    assert STRANGER not in caplog.text


@pytest.mark.parametrize("signature", ["sha256=" + "0" * 64, "nonsense"])
def test_bad_signature_is_refused_and_not_processed(service, graph, signature):
    service.gate.set()
    with TestClient(service.app) as client:
        response = service.post(client, payload(text_message(QUERY)), signature)
        assert response.status_code == 401
        missing = client.post("/webhook", content=b"{}")
        assert missing.status_code == 401
    assert graph.requests == []
    assert service.queries() == []


def test_non_text_message_gets_a_short_reply(service, graph):
    service.gate.set()
    photo = {"from": SENDER, "id": "wamid.p", "timestamp": "1", "type": "image", "image": {}}
    with TestClient(service.app) as client:
        service.post(client, payload(photo))
    assert graph.texts() == [UNSUPPORTED]


def test_webhook_verification(service):
    with TestClient(service.app) as client:
        params = {"hub.mode": "subscribe", "hub.challenge": "1158201444"}
        ok = client.get("/webhook", params={**params, "hub.verify_token": "test-verify-token"})
        assert ok.status_code == 200 and ok.text == "1158201444"
        bad = client.get("/webhook", params={**params, "hub.verify_token": "wrong"})
        assert bad.status_code == 403
        assert client.get("/webhook").status_code == 403


def test_health_on_localhost_only(service):
    with TestClient(service.app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "models_loaded": False, "queued": 0}
        via_tunnel = client.get("/health", headers={"CF-Connecting-IP": "203.0.113.5"})
        assert via_tunnel.status_code == 404


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json", "/", "/originals/x.jpg"])
def test_no_other_routes(service, path):
    with TestClient(service.app) as client:
        assert client.get(path).status_code == 404


def test_server_binds_localhost_only():
    assert HOST == "127.0.0.1"


def test_logs_hold_no_message_text_or_numbers(service, graph, caplog):
    service.gate.set()
    with caplog.at_level(logging.DEBUG), TestClient(service.app) as client:
        service.post(client, payload(text_message(QUERY, message_id="wamid.log")))
        service.post(client, payload(text_message("original 1", message_id="wamid.log2")))
    assert len(graph.images()) == 5 and len(documents(graph)) == 1
    assert log_id("wamid.log") in caplog.text  # hashed ids and counts are fine
    assert "wamid" not in caplog.text
    for secret in (QUERY, SENDER, PHONE_ID, "IMG_0001", "test-token", "test-app-secret"):
        assert secret not in caplog.text


def test_status_callbacks_are_logged_without_the_recipient(service, graph, caplog):
    service.gate.set()
    with caplog.at_level(logging.INFO), TestClient(service.app) as client:
        response = service.post(client, status_payload(SENT, FAILED))
        assert response.status_code == 200
    assert f"message {log_id('wamid.out1')} sent" in caplog.text
    assert (
        f"message {log_id('wamid.out2')} failed (error 131049: This message was not delivered"
        in caplog.text
    )
    assert "wamid" not in caplog.text  # ids hold the phone number; only hashes are logged
    [failed] = [r for r in caplog.records if log_id("wamid.out2") in r.getMessage()]
    assert failed.levelno == logging.WARNING
    assert SENDER not in caplog.text
    assert graph.requests == [] and service.queries() == []
