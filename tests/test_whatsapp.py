"""The WhatsApp bot (P3): webhook, adapter, worker. The Graph API is an httpx.MockTransport
fake and the searcher uses fake retrievers: no network and no models."""

import hashlib
import hmac
import json
import logging
import sqlite3
import threading
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from imgsearch.chat.bot import (
    ERROR_REPLY,
    HELP,
    NEED_SEARCH,
    NO_DRIVE,
    NO_MORE,
    NO_RESULTS,
    NOT_FETCHABLE,
    REWRITE_SKIPPED,
    THUMBNAILS_ONLY,
    UNSUPPORTED,
    OriginalUnavailable,
    WhatsAppAdapter,
    caption_line,
)
from imgsearch.chat.service import LazySearcher, Worker
from imgsearch.chat.whatsapp import (
    DeliveryStatus,
    GraphClient,
    GraphError,
    Incoming,
    Secrets,
    delivery_statuses,
    incoming_messages,
    load_secrets,
    signature_ok,
)
from imgsearch.commands.serve import HOST, build_app
from imgsearch.config import Config
from imgsearch.interfaces import Candidate, ParsedQuery, Result
from imgsearch.search.hybrid import Searcher
from imgsearch.store.db import connect
from imgsearch.store.text import save_caption

SENDER = "919800000001"
STRANGER = "919800000009"
PHONE_ID = "1065403522"
QUERY = "kutte ki photo"
IDS = [f"img{n:02d}" for n in range(1, 13)]  # 12 images
MISSING_DRIVE = Path("/Volumes/imgsearch-test-no-such-drive")

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


class FakeRewriter:
    def __init__(self, error: str | None = None) -> None:
        self.error = error
        self.queries: list[str] = []

    def rewrite(self, query: str) -> ParsedQuery:
        self.queries.append(query)
        return ParsedQuery(english="photo of a dog", original=query, rewrite_error=self.error)


class FakeRetriever:
    def __init__(self, *image_ids: str) -> None:
        self.image_ids = image_ids

    def retrieve(self, q: ParsedQuery, k: int) -> list[Candidate]:
        return [
            Candidate(image_id=i, rank=r, score=1.0 / r, signal="vector")
            for r, i in enumerate(self.image_ids[:k], start=1)
        ]


def make_searcher(conn, config: Config, rewriter=None, image_ids=IDS) -> Searcher:
    return Searcher(
        conn, config.search, rewriter or FakeRewriter(), {"vector": FakeRetriever(*image_ids)}
    )


def unplugged(config: Config) -> Config:
    return config.model_copy(
        update={
            "originals_root": MISSING_DRIVE / "originals",
            "derivatives_root": MISSING_DRIVE / "derivatives",
        }
    )


# ---- fixtures ----


@pytest.fixture
def library(config: Config, conn: sqlite3.Connection) -> sqlite3.Connection:
    """12 images with a derivative, a thumbnail and an original file each."""
    for n, image_id in enumerate(IDS, start=1):
        with conn:
            conn.execute(
                "INSERT INTO images (id, orig_relpath, derivative_path, thumb_path, taken_at,"
                " created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    image_id,
                    f"2024/IMG_{n:04d}.HEIC",
                    f"{image_id}.jpg",
                    f"{image_id}.jpg",
                    f"2024-05-{n:02d}T10:00:00",
                    "2026-10-20T00:00:00+00:00",
                ),
            )
        for root, data in (
            (config.derivatives_root / f"{image_id}.jpg", derivative(image_id)),
            (config.thumbs_root / f"{image_id}.jpg", thumb(image_id)),
            (config.originals_root / f"2024/IMG_{n:04d}.HEIC", original(image_id)),
        ):
            root.parent.mkdir(parents=True, exist_ok=True)
            root.write_bytes(data)
    save_caption(conn, "img01", "A brown dog sleeping on a sofa.", "m")
    return conn


def derivative(image_id: str) -> bytes:
    return f"derivative bytes of {image_id}".encode()


def thumb(image_id: str) -> bytes:
    return f"thumbnail bytes of {image_id}".encode()


def original(image_id: str) -> bytes:
    return f"original bytes of {image_id}".encode()


@pytest.fixture
def graph() -> FakeGraph:
    return FakeGraph()


def make_adapter(config, graph, searcher, fetch_original=None) -> WhatsAppAdapter:
    def no_fetch(image_id: str) -> Path:
        raise AssertionError("fetch_original must not be called")

    client = GraphClient(config.whatsapp, SECRETS, graph.client())
    return WhatsAppAdapter(
        config, client, LazySearcher(lambda: searcher), fetch_original or no_fetch
    )


@pytest.fixture
def adapter(config, library, graph) -> WhatsAppAdapter:
    return make_adapter(config, graph, make_searcher(library, config))


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
        Incoming("wamid.1", SENDER, "text", "hi"),
        Incoming("wamid.2", SENDER, "image", None),
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


def test_graph_client_raises_graph_error(config: Config, graph, tmp_path: Path):
    photo = tmp_path / "a.jpg"
    photo.write_bytes(b"x")
    graph.fail_media = True
    client = GraphClient(config.whatsapp, SECRETS, graph.client())
    with pytest.raises(GraphError, match="400: code 131053"):
        client.send_image(SENDER, photo, "c")


# ---- the adapter ----


def test_text_message_sends_top_results_as_derivative_images(adapter, graph, config):
    adapter.on_message(QUERY, SENDER)
    images = graph.images()
    assert len(images) == config.search.top_k == 5
    for (body, _), image_id in zip(images, IDS, strict=False):
        assert derivative(image_id) in body
        assert original(image_id) not in body
    assert [caption for _, caption in images] == [
        "1 · 2024-05-01 · A brown dog sleeping on a sofa.",
        "2 · 2024-05-02",
        "3 · 2024-05-03",
        "4 · 2024-05-04",
        "5 · 2024-05-05",
    ]
    assert graph.texts() == []
    assert all(m["to"] == SENDER for m in graph.messages)


def test_search_runs_once_with_the_cli_defaults(config, library, graph):
    rewriter = FakeRewriter()
    searcher = make_searcher(library, config, rewriter)
    calls = []
    search = searcher.search

    def recording_search(query, **kwargs):
        calls.append(kwargs)
        return search(query, **kwargs)

    searcher.search = recording_search
    make_adapter(config, graph, searcher).on_message(QUERY, SENDER)
    assert rewriter.queries == [QUERY]  # the rewrite is on, as in the CLI
    # No signals given: the searcher's own, built from search.default_signals.
    assert calls == [{"k": config.whatsapp.max_results}]


def test_more_pages_through_the_last_search(adapter, graph):
    adapter.on_message(QUERY, SENDER)
    adapter.on_message("More", SENDER)
    captions = [caption.split(" · ")[0] for _, caption in graph.images()]
    assert captions == [str(n) for n in range(1, 11)]
    assert derivative("img06") in graph.images()[5][0]
    # The fake retriever, like the real ones, returns at most search.per_retriever_k (10).
    adapter.on_message("more", SENDER)
    assert graph.texts() == [NO_MORE]


def test_more_and_original_need_a_search_first(adapter, graph):
    adapter.on_message("more", SENDER)
    adapter.on_message("original 1", SENDER)
    assert graph.texts() == [NEED_SEARCH, NEED_SEARCH]


def test_help_does_not_load_the_models(config, library, graph):
    adapter = make_adapter(config, graph, make_searcher(library, config))
    adapter.on_message(" HELP ", SENDER)
    assert graph.texts() == [HELP]
    assert adapter.searcher.loads == 0


def test_pen_drive_unplugged_sends_thumbnails(config, library, graph):
    adapter = make_adapter(unplugged(config), graph, make_searcher(library, config))
    adapter.on_message(QUERY, SENDER)
    assert graph.texts() == [THUMBNAILS_ONLY]
    assert graph.messages[0]["type"] == "text"  # the note comes first
    bodies = [body for body, _ in graph.images()]
    assert len(bodies) == 5
    assert all(thumb(i) in b for b, i in zip(bodies, IDS, strict=False))


def test_missing_derivative_falls_back_to_thumbnail(adapter, graph, config):
    (config.derivatives_root / "img01.jpg").unlink()
    adapter.on_message(QUERY, SENDER)
    assert thumb("img01") in graph.images()[0][0]
    assert derivative("img02") in graph.images()[1][0]


def test_rewrite_fallback_says_so_and_still_sends_results(config, library, graph):
    searcher = make_searcher(library, config, FakeRewriter(error="ConnectError: refused"))
    make_adapter(config, graph, searcher).on_message(QUERY, SENDER)
    assert graph.texts() == [REWRITE_SKIPPED]
    assert len(graph.images()) == 5


def test_no_results(config, library, graph):
    make_adapter(config, graph, make_searcher(library, config, image_ids=[])).on_message(
        QUERY, SENDER
    )
    assert graph.texts() == [NO_RESULTS]
    assert graph.images() == []


def test_search_error_gives_a_short_reply_not_a_traceback(config, library, graph, caplog):
    def broken_build():
        raise RuntimeError(f"model failed on {QUERY}")

    client = GraphClient(config.whatsapp, SECRETS, graph.client())
    adapter = WhatsAppAdapter(config, client, LazySearcher(broken_build), lambda i: Path())
    with caplog.at_level(logging.DEBUG):
        adapter.on_message(QUERY, SENDER)
    assert graph.texts() == [ERROR_REPLY]
    assert "RuntimeError" in caplog.text
    assert QUERY not in caplog.text  # the exception's message isn't logged


def test_graph_error_while_sending_gives_the_short_reply(adapter, graph):
    graph.fail_media = True
    adapter.on_message(QUERY, SENDER)
    assert graph.texts() == [ERROR_REPLY]


def test_unsupported_message(adapter, graph):
    adapter.on_unsupported(SENDER)
    assert graph.texts() == [UNSUPPORTED]


def documents(graph: FakeGraph) -> list[tuple[bytes, dict]]:
    return [
        (graph.media[m["document"]["id"]], m["document"])
        for m in graph.messages
        if m["type"] == "document"
    ]


def test_original_sends_the_file_as_a_document(adapter, graph):
    adapter.on_message(QUERY, SENDER)
    adapter.on_message("original 2", SENDER)
    [(body, document)] = documents(graph)
    assert original("img02") in body
    assert document["filename"] == "IMG_0002.HEIC"
    assert document["caption"] == "2 · original"


def test_original_number_must_be_among_the_shown_results(adapter, graph):
    adapter.on_message(QUERY, SENDER)
    adapter.on_message("original 6", SENDER)  # only 1-5 shown so far
    adapter.on_message("original 0", SENDER)
    assert graph.texts() == ["Choose N from 1 to 5: original N."] * 2
    assert documents(graph) == []


def test_original_with_the_pen_drive_unplugged(config, library, graph):
    adapter = make_adapter(unplugged(config), graph, make_searcher(library, config))
    adapter.on_message(QUERY, SENDER)
    adapter.on_message("original 1", SENDER)
    assert graph.texts()[-1] == NO_DRIVE
    assert documents(graph) == []


def test_evicted_original_is_fetched_then_sent(config, library, graph):
    evicted = config.originals_root / "2024/IMG_0001.HEIC"
    evicted.unlink()
    fetched = []

    def fetch_original(image_id: str) -> Path:
        fetched.append(image_id)
        evicted.write_bytes(original(image_id))  # what the P1.6 fetch does
        return evicted

    adapter = make_adapter(config, graph, make_searcher(library, config), fetch_original)
    adapter.on_message(QUERY, SENDER)
    adapter.on_message("original 1", SENDER)
    assert fetched == ["img01"]
    assert original("img01") in documents(graph)[0][0]


def test_original_not_fetchable(config, library, graph):
    (config.originals_root / "2024/IMG_0001.HEIC").unlink()

    def fetch_original(image_id: str) -> Path:
        raise OriginalUnavailable("S3 offline")

    adapter = make_adapter(config, graph, make_searcher(library, config), fetch_original)
    adapter.on_message(QUERY, SENDER)
    adapter.on_message("original 1", SENDER)
    assert graph.texts() == [NOT_FETCHABLE]
    assert documents(graph) == []


def test_original_over_the_size_limit_is_refused(config, library, graph):
    small = config.model_copy(
        update={"whatsapp": config.whatsapp.model_copy(update={"original_max_mb": 0.000001})}
    )
    adapter = make_adapter(small, graph, make_searcher(library, config))
    adapter.on_message(QUERY, SENDER)
    adapter.on_message("original 1", SENDER)
    assert "over the 0 MB limit" in graph.texts()[-1]
    assert documents(graph) == []


def test_caption_line_without_date_or_caption():
    r = Result("x", "a.jpg", None, None, None, 0.1, {})
    assert caption_line(3, None, r) == "3"


# ---- searcher lifetime and worker (D21) ----


def test_lazy_searcher_loads_once_and_unloads(config, library):
    built = []

    def build() -> Searcher:
        built.append(1)
        return make_searcher(connect(config), config)

    lazy = LazySearcher(build)
    assert not lazy.loaded
    first = lazy.get()
    assert lazy.get() is first and lazy.loads == 1
    lazy.unload()
    assert not lazy.loaded
    with pytest.raises(sqlite3.ProgrammingError):  # its DB connection was closed
        first.conn.execute("SELECT 1")
    lazy.unload()  # nothing loaded: no-op
    assert lazy.get() is not first and lazy.loads == 2


def test_worker_calls_on_idle_after_the_timeout():
    handled, idle = [], threading.Event()
    worker = Worker(lambda m: handled.append(m.message_id), idle.set, idle_s=0.01)
    worker.start()
    worker.submit(Incoming("wamid.1", SENDER, "text", "x"))
    assert idle.wait(timeout=5)
    worker.stop()
    assert handled == ["wamid.1"]


def test_worker_survives_a_failing_message():
    handled = []

    def handle(m: Incoming) -> None:
        if m.message_id == "bad":
            raise RuntimeError("boom")
        handled.append(m.message_id)

    worker = Worker(handle, lambda: None, idle_s=60)
    worker.start()
    worker.submit(Incoming("bad", SENDER, "text", "x"))
    worker.submit(Incoming("good", SENDER, "text", "x"))
    worker.stop()  # finishes the queue first
    assert handled == ["good"]


# ---- the HTTP layer, end to end ----


class Service:
    """build_app with fakes. The searcher waits for `gate`, so a test can see the webhook
    answer before the search runs."""

    def __init__(self, config: Config, graph: FakeGraph) -> None:
        self.gate = threading.Event()
        self.rewriter = FakeRewriter()
        self.graph = graph

        def build() -> Searcher:
            self.gate.wait(timeout=5)
            return make_searcher(connect(config), config, self.rewriter)  # in the worker thread

        self.app = build_app(config, SECRETS, graph.client(), build, lambda i: Path())

    def post(self, client: TestClient, body: dict, signature: str | None = None):
        raw = json.dumps(body).encode()
        headers = {"Content-Type": "application/json"}
        headers["X-Hub-Signature-256"] = signature or sign(raw)
        return client.post("/webhook", content=raw, headers=headers)


@pytest.fixture
def service(config, library, graph) -> Service:
    return Service(config, graph)


def test_webhook_answers_at_once_and_searches_in_the_background(service, graph):
    with TestClient(service.app) as client:
        response = service.post(client, payload(text_message(QUERY)))
        assert response.status_code == 200
        assert graph.messages == []  # the search is still waiting
        service.gate.set()
    # Leaving the client stops the worker after it has handled the queue.
    assert service.rewriter.queries == [QUERY]
    assert len(graph.images()) == 5


def test_repeated_delivery_is_handled_once(service, graph):
    service.gate.set()
    with TestClient(service.app) as client:
        body = payload(text_message(QUERY, message_id="wamid.same"))
        assert service.post(client, body).status_code == 200
        assert service.post(client, body).status_code == 200
    assert service.rewriter.queries == [QUERY]
    assert len(graph.images()) == 5


def test_sender_not_allowed_gets_no_reply(service, graph, caplog):
    service.gate.set()
    with caplog.at_level(logging.INFO), TestClient(service.app) as client:
        response = service.post(client, payload(text_message(QUERY, sender=STRANGER)))
        assert response.status_code == 200
    assert graph.requests == []
    assert service.rewriter.queries == []
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
    assert service.rewriter.queries == []


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
    assert "wamid.log" in caplog.text  # ids and counts are fine
    for secret in (QUERY, SENDER, PHONE_ID, "IMG_0001", "test-token", "test-app-secret"):
        assert secret not in caplog.text


def test_status_callbacks_are_logged_without_the_recipient(service, graph, caplog):
    service.gate.set()
    with caplog.at_level(logging.INFO), TestClient(service.app) as client:
        response = service.post(client, status_payload(SENT, FAILED))
        assert response.status_code == 200
    assert "message wamid.out1 sent" in caplog.text
    assert "message wamid.out2 failed (error 131049: This message was not delivered" in caplog.text
    [failed] = [r for r in caplog.records if "wamid.out2" in r.getMessage()]
    assert failed.levelno == logging.WARNING
    assert SENDER not in caplog.text
    assert graph.requests == [] and service.rewriter.queries == []
