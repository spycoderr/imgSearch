"""The Telegram transport (P3-T): Bot API client, update filtering, long polling, whoami and
`imgsearch bot` dispatch. The Bot API is an httpx.MockTransport fake and the searcher uses
fake retrievers (conftest.py): no network, no models."""

import email
import email.policy
import json
import logging
import threading
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from imgsearch.chat.bot import ERROR_REPLY, HELP
from imgsearch.chat.service import Incoming
from imgsearch.chat.telegram import (
    RATE_LIMIT_TRIES,
    Secrets,
    TelegramClient,
    TelegramError,
    classify,
    load_secrets,
    pending_senders,
)
from imgsearch.cli import app
from imgsearch.commands.bot import build_telegram, run_telegram
from imgsearch.config import Config
from imgsearch.search.hybrid import Searcher
from imgsearch.store.db import connect

TOKEN = "123456:SECRET-test-token"
USER = 4242001  # the allowed user; in a private chat, chat id = user id
STRANGER = 4242009
GROUP = -100777
QUERY = "kutte ki photo"
SECRETS = Secrets(token=TOKEN, allowed_user_ids=frozenset({USER}))


# ---- a fake Bot API ----


def form(request: httpx.Request) -> tuple[dict[str, str], dict[str, tuple[str, bytes, str]]]:
    """A request's form fields, and its uploaded files as (filename, bytes, mime)."""
    content_type = request.headers.get("content-type", "")
    if not content_type.startswith("multipart/form-data"):
        return dict(httpx.QueryParams(request.content.decode())), {}
    raw = f"Content-Type: {content_type}\r\n\r\n".encode() + request.content
    message = email.message_from_bytes(raw, policy=email.policy.HTTP)
    fields, files = {}, {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        data = part.get_payload(decode=True)
        if part.get_filename():
            files[name] = (part.get_filename(), data, part.get_content_type())
        else:
            fields[name] = data.decode()
    return fields, files


class FakeBotAPI:
    """Answers Bot API calls and records them as (method, fields, files).

    `updates` holds the getUpdates answers in order: a list of updates, or an Exception to
    raise, or a dict to answer as is. When they run out, `stop` is set and [] is returned.
    `answers` maps a method to a list of dicts to answer with, in order.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str], dict]] = []
        self.updates: list = []
        self.answers: dict[str, list[dict]] = {}
        self.stop = threading.Event()

    def handler(self, request: httpx.Request) -> httpx.Response:
        request.read()
        prefix = f"/bot{TOKEN}/"
        assert request.url.path.startswith(prefix)
        method = request.url.path.removeprefix(prefix)
        fields, files = form(request)
        self.calls.append((method, fields, files))
        if self.answers.get(method):
            answer = self.answers[method].pop(0)
            return httpx.Response(answer.get("error_code", 200), json=answer)
        if method == "getUpdates":
            if not self.updates:
                self.stop.set()
                return httpx.Response(200, json={"ok": True, "result": []})
            item = self.updates.pop(0)
            if isinstance(item, Exception):
                raise item
            if isinstance(item, dict):
                return httpx.Response(item.get("error_code", 200), json=item)
            return httpx.Response(200, json={"ok": True, "result": item})
        return httpx.Response(200, json={"ok": True, "result": True})

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))

    def methods(self) -> list[str]:
        return [method for method, _, _ in self.calls]

    def sent(self, method: str) -> list[tuple[dict[str, str], dict]]:
        return [(fields, files) for m, fields, files in self.calls if m == method]


@pytest.fixture
def api() -> FakeBotAPI:
    return FakeBotAPI()


@pytest.fixture
def sleeps() -> list[float]:
    return []


@pytest.fixture
def client(config: Config, api: FakeBotAPI, sleeps: list[float]) -> TelegramClient:
    return TelegramClient(config.telegram, TOKEN, api.client(), sleeps.append)


def update(update_id: int, text: str | None = QUERY, user=USER, chat=USER, chat_type="private"):
    message = {
        "message_id": update_id * 10,
        "from": {"id": user, "is_bot": False, "first_name": "Test", "username": "tester"},
        "chat": {"id": chat, "type": chat_type},
        "date": 1761040000,
    }
    if text is not None:
        message["text"] = text
    else:
        message["photo"] = [{"file_id": "x", "width": 1, "height": 1}]
    return {"update_id": update_id, "message": message}


# ---- secrets ----


def test_load_secrets(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text(f"TELEGRAM_BOT_TOKEN={TOKEN}\nTELEGRAM_ALLOWED_USER_IDS=4242001, 77,\n")
    secrets = load_secrets(env_file, environ={})
    assert secrets.token == TOKEN and secrets.allowed_user_ids == {4242001, 77}
    assert TOKEN not in repr(secrets) and "4242001" not in repr(secrets)


@pytest.mark.parametrize("ids", ["@tester", "4242001,@tester", " , "])
def test_allowed_users_must_be_numeric_ids(tmp_path: Path, ids: str):
    env_file = tmp_path / ".env"
    env_file.write_text(f"TELEGRAM_BOT_TOKEN={TOKEN}\nTELEGRAM_ALLOWED_USER_IDS={ids}\n")
    with pytest.raises(ValueError, match="numeric user ids"):
        load_secrets(env_file, environ={})


def test_load_secrets_names_missing_keys(tmp_path: Path):
    with pytest.raises(ValueError, match="TELEGRAM_BOT_TOKEN, TELEGRAM_ALLOWED_USER_IDS"):
        load_secrets(tmp_path / "missing.env", environ={})


# ---- the client ----


def test_call_returns_the_result(client: TelegramClient, api: FakeBotAPI):
    assert client.call("sendMessage", {"chat_id": USER, "text": "hi"}) is True
    assert api.calls == [("sendMessage", {"chat_id": str(USER), "text": "hi"}, {})]


def test_rate_limit_waits_retry_after_then_retries(client, api, sleeps):
    too_many = {
        "ok": False,
        "error_code": 429,
        "description": "Too Many Requests: retry after 3",
        "parameters": {"retry_after": 3},
    }
    api.answers["sendMessage"] = [too_many]
    client.send_text(str(USER), "hi")
    assert api.methods() == ["sendMessage", "sendMessage"]
    assert sleeps == [3]

    api.answers["sendMessage"] = [too_many] * RATE_LIMIT_TRIES
    with pytest.raises(TelegramError, match="429") as e:
        client.send_text(str(USER), "hi")
    assert e.value.retry_after == 3


def test_refused_call_raises(client, api):
    api.answers["sendMessage"] = [
        {"ok": False, "error_code": 400, "description": "Bad Request: chat not found"}
    ]
    with pytest.raises(TelegramError, match="sendMessage: 400 Bad Request: chat not found"):
        client.send_text("1", "hi")


def test_redact_hides_the_token(client):
    assert client.redact(f"POST https://api.telegram.org/bot{TOKEN}/getUpdates") == (
        "POST https://api.telegram.org/bot<token>/getUpdates"
    )


def photos(tmp_path: Path, n: int) -> list[tuple[Path, str]]:
    found = []
    for i in range(1, n + 1):
        path = tmp_path / f"p{i}.jpg"
        path.write_bytes(f"jpeg {i}".encode())
        found.append((path, f"{i} · caption"))
    return found


def test_send_photos_as_one_album(client, api, tmp_path):
    client.send_photos(str(USER), photos(tmp_path, 5))
    [(fields, files)] = api.sent("sendMediaGroup")
    assert fields["chat_id"] == str(USER)
    assert json.loads(fields["media"]) == [
        {"type": "photo", "media": f"attach://photo{i}", "caption": f"{i + 1} · caption"}
        for i in range(5)
    ]
    assert {name: data for name, (_, data, _) in files.items()} == {
        f"photo{i}": f"jpeg {i + 1}".encode() for i in range(5)
    }
    assert all(mime == "image/jpeg" for _, _, mime in files.values())


def test_send_photos_splits_albums_of_ten_and_sends_a_single_photo_alone(client, api, tmp_path):
    client.send_photos(str(USER), photos(tmp_path, 11))
    assert api.methods() == ["sendMediaGroup", "sendPhoto"]
    assert len(json.loads(api.sent("sendMediaGroup")[0][0]["media"])) == 10
    fields, files = api.sent("sendPhoto")[0]
    assert fields["caption"] == "11 · caption" and files["photo"][1] == b"jpeg 11"


def test_send_document(client, api, tmp_path):
    original = tmp_path / "x.bin"
    original.write_bytes(b"original bytes")
    client.send_document(str(USER), original, "IMG_0001.HEIC", "1 · original")
    [(fields, files)] = api.sent("sendDocument")
    assert fields == {"chat_id": str(USER), "caption": "1 · original"}
    assert files["document"] == ("IMG_0001.HEIC", b"original bytes", "image/heic")


def test_show_activity(client, api):
    client.show_activity(str(USER), "upload_photo")
    assert api.calls == [("sendChatAction", {"chat_id": str(USER), "action": "upload_photo"}, {})]


# ---- update filtering ----


def test_classify():
    allowed = frozenset({USER})
    assert classify(update(5), allowed) == Incoming("5", "u5", str(USER), "text", QUERY)
    assert classify(update(6, text=None), allowed) == Incoming("6", "u6", str(USER), "other", None)
    assert classify(update(7, user=STRANGER, chat=STRANGER), allowed) == "user not allowed"
    group = update(8, chat=GROUP, chat_type="group")
    assert classify(group, allowed) == "group or channel"
    supergroup = update(9, chat=GROUP, chat_type="supergroup")
    assert classify(supergroup, allowed) == "group or channel"
    edited = {"update_id": 10, "edited_message": update(10)["message"]}
    assert classify(edited, allowed) == "not a new message"


# ---- the bot, end to end ----


def make_build(config: Config, make_searcher, gate: threading.Event | None = None):
    def build() -> Searcher:
        if gate is not None:
            gate.wait(timeout=5)
        return make_searcher(connect(config))  # in the worker thread

    return build


def run(
    config: Config,
    api: FakeBotAPI,
    sleeps: list[float],
    build_searcher: Callable[[], Searcher],
) -> None:
    client, worker = build_telegram(
        config, SECRETS, api.client(), sleeps.append, build_searcher, lambda i: Path()
    )
    run_telegram(config, SECRETS, client, worker, api.stop)


@pytest.fixture
def bot_run(config, chat_library, make_searcher, api, sleeps):
    def go(build_searcher=None) -> None:
        run(config, api, sleeps, build_searcher or make_build(config, make_searcher))

    return go


def test_polling_answers_an_allowed_user_with_an_album(bot_run, api, config, caplog):
    api.updates = [[update(100)]]
    with caplog.at_level(logging.DEBUG):
        bot_run()
    methods = api.methods()
    assert methods[0] == "deleteWebhook"  # long polling needs no webhook set
    assert methods.count("sendMediaGroup") == 1
    fields, files = api.sent("sendMediaGroup")[0]
    assert fields["chat_id"] == str(USER)
    assert [m["caption"] for m in json.loads(fields["media"])][0].startswith("1 · 2024-05-01")
    assert files["photo0"][1] == b"derivative bytes of img01"
    actions = [f["action"] for f, _ in api.sent("sendChatAction")]
    assert actions == ["typing", "typing", "upload_photo"]
    assert "update u100 queued (text)" in caplog.text


def test_offsets_confirm_handled_updates(bot_run, api, config):
    api.updates = [[update(100), update(101, text="help")], [update(102, text="help")]]
    bot_run()
    polls = [fields for fields, _ in api.sent("getUpdates")]
    assert "offset" not in polls[0]
    assert polls[1]["offset"] == "102"
    assert polls[2]["offset"] == "103"
    assert polls[-1] == {"timeout": "0", "allowed_updates": '["message"]', "offset": "103"}
    assert {p["timeout"] for p in polls[:-1]} == {str(config.telegram.poll_timeout_s)}


def test_others_get_no_reply_and_are_only_counted(bot_run, api, caplog):
    api.updates = [
        [
            update(200, user=STRANGER, chat=STRANGER),
            update(201, chat=GROUP, chat_type="group"),
            update(202, user=STRANGER, chat=GROUP, chat_type="supergroup"),
            {"update_id": 203, "edited_message": update(203)["message"]},
        ]
    ]
    with caplog.at_level(logging.DEBUG):
        bot_run()
    assert set(api.methods()) == {"deleteWebhook", "getUpdates"}  # nothing sent to anyone
    assert "ignored: 1 user not allowed, 2 group or channel, 1 not a new message" in caplog.text
    for private in (str(STRANGER), str(GROUP), str(USER), QUERY, "tester"):
        assert private not in caplog.text


def test_start_replies_with_help(bot_run, api):
    api.updates = [[update(300, text="/start")]]
    bot_run()
    assert [f["text"] for f, _ in api.sent("sendMessage")] == [HELP]


def test_one_bad_update_does_not_stop_the_loop(bot_run, api, caplog):
    broken = {"update_id": 400, "message": {"text": "no chat"}}
    api.updates = [[broken, update(401, text="help")]]
    with caplog.at_level(logging.WARNING):
        bot_run()
    assert "update u400 unreadable: KeyError" in caplog.text
    assert [f["text"] for f, _ in api.sent("sendMessage")] == [HELP]


def test_network_errors_and_refusals_back_off_and_retry(bot_run, api, sleeps, caplog):
    def down(message: str) -> httpx.ConnectError:
        return httpx.ConnectError(message, request=httpx.Request("POST", "https://x"))

    api.updates = [
        down(f"can't reach https://api.telegram.org/bot{TOKEN}/getUpdates"),
        down("still down"),
        {"ok": False, "error_code": 409, "description": "Conflict: another getUpdates"},
        [update(500, text="help")],
        down("down again"),
    ]
    with caplog.at_level(logging.DEBUG):
        bot_run()
    assert sleeps == [1.0, 2.0, 4.0, 1.0]  # doubling, reset after a good poll
    assert [f["text"] for f, _ in api.sent("sendMessage")] == [HELP]
    assert TOKEN not in caplog.text and "<token>" in caplog.text


def test_rate_limited_poll_waits_retry_after(bot_run, api, sleeps):
    api.updates = [
        {
            "ok": False,
            "error_code": 429,
            "description": "Too Many",
            "parameters": {"retry_after": 7},
        },
        [update(600, text="help")],
    ]
    bot_run()
    assert sleeps == [7]
    assert len(api.sent("sendMessage")) == 1


def test_wrong_token_fails_at_once(bot_run, api):
    api.updates = [{"ok": False, "error_code": 401, "description": "Unauthorized"}]
    with pytest.raises(TelegramError, match="401"):
        bot_run()


def test_a_crashed_search_replies_with_a_short_error(bot_run, api, caplog):
    def broken() -> Searcher:
        raise RuntimeError("model failed")

    api.updates = [[update(700)]]
    with caplog.at_level(logging.DEBUG):
        bot_run(broken)
    assert [f["text"] for f, _ in api.sent("sendMessage")] == [ERROR_REPLY]
    assert QUERY not in caplog.text


def test_logs_hold_no_token_text_ids_or_paths(bot_run, api, caplog):
    api.updates = [[update(800)], [update(801, text="original 1")]]
    with caplog.at_level(logging.DEBUG):
        bot_run()
    assert len(api.sent("sendDocument")) == 1
    assert "update u800 queued" in caplog.text and "message u801 handled" in caplog.text
    for private in (TOKEN, QUERY, str(USER), "tester", "IMG_0001", "img01"):
        assert private not in caplog.text


# ---- whoami ----


def test_pending_senders_reads_without_confirming(client, api):
    api.updates = [
        [
            update(900),
            update(901, user=STRANGER, chat=GROUP, chat_type="group"),
            update(902),  # the same user again
            {"update_id": 903, "my_chat_member": {}},
        ]
    ]
    senders = pending_senders(client)
    assert [(s.user_id, s.name, s.username, s.chat_type) for s in senders] == [
        (USER, "Test", "tester", "private"),
        (STRANGER, "Test", "tester", "group"),
    ]
    assert api.methods() == ["deleteWebhook", "getUpdates"]
    assert "offset" not in api.sent("getUpdates")[0][0]  # nothing is marked read


# ---- `imgsearch bot` picks the chat app from config ----


@pytest.mark.parametrize(
    ("adapter", "missing"),
    [("telegram", "TELEGRAM_BOT_TOKEN"), ("whatsapp", "WHATSAPP_TOKEN")],
)
def test_bot_runs_the_configured_adapter(config_path: Path, adapter: str, missing: str):
    text = config_path.read_text().replace("  adapter: telegram", f"  adapter: {adapter}")
    config_path.write_text(text)
    keys = ["TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_USER_IDS"]
    keys += ["WHATSAPP_TOKEN", "WHATSAPP_PHONE_NUMBER_ID", "WHATSAPP_APP_SECRET"]
    keys += ["WHATSAPP_VERIFY_TOKEN", "WHATSAPP_ALLOWED_SENDERS"]
    unset = dict.fromkeys(keys, "")  # empty counts as unset, and wins over .env
    result = CliRunner().invoke(app, ["--config", str(config_path), "bot"], env=unset)
    assert result.exit_code == 1
    assert missing in result.output


def test_config_names_a_known_adapter(config: Config):
    assert config.chat.adapter == "telegram"
    with pytest.raises(ValueError, match="telegram"):
        config.chat.model_validate({**config.chat.model_dump(), "adapter": "signal"})
