# P3-T report: Telegram bot (replaces WhatsApp as the active chat adapter)

Phase A (code and tests): 2026-10-21, branch `p3` in `.worktrees/p3`, rebased on `main`
(`15991dc`, with P1.6.1 and P1.7 phase A). Phase B (real messages) waits for the user's go,
after P1.7 is reviewed.

## Summary

- **`imgsearch bot`** runs the chat app named by `chat.adapter`; it replaces
  `imgsearch serve`. The default is `telegram`; `whatsapp` still works, parked.
- **The Telegram bot uses long polling.** It calls `deleteWebhook` on start, then
  `getUpdates` with a 30 s wait and `offset` = last update id + 1. There is no webhook, no
  public URL and no listening port.
- **Only allowed users get replies:** private chats from numeric user ids in
  `TELEGRAM_ALLOWED_USER_IDS`. Anything else is counted by reason, with no reply.
- **`imgsearch telegram whoami`** prints the ids of people who messaged the bot, to the
  terminal only, without marking their messages read.
- **The search and command logic is shared, not copied.** It moved out of
  `WhatsAppAdapter` into `ChatBot` (`imgsearch/chat/bot.py`), which implements
  `ChatAdapter` for any `Messenger`.
  - `TelegramClient` and WhatsApp's `GraphClient` are the two messengers.
  - Behaviour is as in P3: `help` (and `/start`), `more`, `original N`, the pen-drive
    fallback, rewrite fallback, no results, a short error reply, quiet logs, and D21's
    lazy load and idle unload.
- **New on Telegram:**
  - results go as one album (`sendMediaGroup`) and `original N` by `sendDocument`
  - "typing…", "sending photo…" and "sending file…" statuses
  - network errors back off and retry; 429 waits `retry_after`; a wrong token stops the bot
  - the token is redacted from logged error text
- **P1.7 changes used:** captions show the search date (`taken_at`, else the file mtime).
  A date-range fallback ("no photos from X to Y") is explained in a text message.
- **Tests:** 451 pass (was 412 after the rebase), ruff clean, no new dependencies (httpx).
  The Bot API is faked with `httpx.MockTransport`.

  | File | Tests | Covers |
  | --- | ---: | --- |
  | `tests/test_telegram.py` | 28 | client, polling, whoami, `bot` dispatch |
  | `tests/test_chat_bot.py` | 29 | shared behaviour, once for both apps |
  | `tests/test_chat_service.py` | 3 | worker and idle unload |
  | `tests/test_whatsapp.py` | 25 | WhatsApp transport only (was 46: its behaviour tests moved to `test_chat_bot.py`) |

### Bot API facts used (checked in the current docs, 2026-10-21)

| Topic | What the docs say |
| --- | --- |
| `getUpdates` | `offset`, `limit` 1–100, `timeout` (0 = short polling), `allowed_updates`. An update is confirmed once getUpdates is called with a higher offset. It doesn't work while a webhook is set |
| `deleteWebhook` | `drop_pending_updates` optional (not used, so pending messages survive) |
| `sendMediaGroup` | 2–10 items; new files via `attach://<name>` in multipart/form-data. A single photo goes by `sendPhoto` |
| Captions | 0–1024 characters |
| Upload limits | multipart: 10 MB for photos, 50 MB for other files (`telegram.original_max_mb: 50`) |
| `sendChatAction` | the status lasts 5 s or until the bot sends a message. So "typing" is sent again after a cold model load |
| Errors | `{"ok": false, "error_code", "description", "parameters": {"retry_after"}}`; `retry_after` = seconds to wait on flood control |
| User ids | at most 52 significant bits (Python int) |

## Real-run results

Phase A: no real run, by the task rules (P1.7 is running its evals). No search, eval or bot
ran against the real DB, and no Bot API call was made.

The only check was the CLI error path, with the secrets blanked in the environment (no
network):
- `imgsearch telegram whoami` → "TELEGRAM_BOT_TOKEN not set: put it in .env", exit 1
- `imgsearch bot` → both keys named, exit 1

Phase B (criterion 16) to do:
- English, Hinglish, Hindi and a text-in-image query, then `more` and `original 1`
- a second account or a group gets no reply
- latency cold vs warm
- RSS back to baseline after the idle timeout
- `lsof` shows no listening socket

## Benchmark

Not a model task. Phase B records reply latency (cold and warm) and the bot process's RSS
before and after the idle unload.

## Changes from the plan

All are recorded in the plan; P3-T entries are in "Changes during build".

- **D13 rewritten:** Telegram (long polling) is active; WhatsApp is parked, with the reason
  (Meta's lock, 131031). The D21 row names `chat.idle_unload_s`. The P3 roadmap row is
  reworded.
- **`imgsearch serve` is replaced by `imgsearch bot`**, which also runs WhatsApp when
  `chat.adapter: whatsapp`. The launchd template is now `docs/launchd/com.imgsearch.bot.plist`
  (log `data/logs/bot.log`).
- **No separate `TelegramAdapter` class.** The acceptance list names one (item 13). The
  Telegram adapter is `ChatBot` + `TelegramClient`, the same `ChatBot` WhatsApp uses with
  `GraphClient`. A `TelegramAdapter` subclass would add nothing, so I didn't make one.
- **`Incoming`** moved to `chat/service.py` and gained `log_id`: Telegram `u<update id>`,
  WhatsApp a hash of the wamid. `WhatsAppAdapter` is gone; `GraphClient` gained
  `send_photos` (one message per photo) and a no-op `show_activity`.
- **Config:**
  - new `chat:` (`adapter`, plus `max_results` and `idle_unload_s` moved from `whatsapp:`)
  - new `telegram:` (`api_url`, `poll_timeout_s` 30, `timeout_s` 60, `original_max_mb` 50)
  - `.env.example` gains `TELEGRAM_BOT_TOKEN` and `TELEGRAM_ALLOWED_USER_IDS`
- **Secrets:** `imgsearch/chat/env.py` `read_env(keys)` reads several secrets at once and
  names every missing one. `config.env_value` (from P1.7) reads one.
- **Rebase:**
  - conflicts in `.env.example`, `config.yaml`, `imgsearch/config.py` and `imgsearch/cli.py`;
    both sides kept (Syncthing/backfill from main, the chat bot from `p3`)
  - the plan and README merged without conflicts
  - 412 tests passed after the rebase, before the P3-T changes

## Flags

### For phase B

- **`more` gives one extra page** (results 6–10), as in P3. A search returns at most
  `per_retriever_k` (10) results per retriever.
- **Album captions.** Telegram shows each photo's caption when that photo is opened, and
  none under the album as a whole. Phase B will show whether that reads well; the fallback
  is a short text list after the album.
- **Very wide panoramas.** Telegram rejects photos whose sides differ by more than 20:1
  (`PHOTO_INVALID_DIMENSIONS`), and one bad photo fails the whole album, which gives the
  short error reply. Derivatives keep the original's shape. If phase B hits this, send that
  one as a document.
- **HEIC originals** go by `sendDocument` as `image/heic`. Telegram accepts any file type
  as a document, so this should work; check it with `original 1`.
- **Redelivery after a crash.** On a normal stop (Ctrl-C, or SIGTERM from launchd) the last
  offset is confirmed. After a crash, the last batch is delivered again and answered twice.
- **`whoami`** calls `deleteWebhook` first (harmless: the bot never sets a webhook) and
  reads only messages, because polling sets `allowed_updates` to `["message"]`.

### For later tasks

- WhatsApp needs only Meta to unlock the account (business verification or support). Then
  set `chat.adapter: whatsapp`, put a permanent system-user token in `.env`, and follow the
  README's "WhatsApp (parked)" steps.

## Acceptance checklist status (`docs/acceptance/P3-T.md`)

| # | Criterion | Status |
| --- | --- | --- |
| 1 | Long polling, offset = last + 1, `deleteWebhook` on start; no webhook, URL or port | Done: `run_polling`; `test_polling_answers_an_allowed_user_with_an_album` (deleteWebhook first), `test_offsets_confirm_handled_updates`. No server in the Telegram path (uvicorn only in `run_whatsapp`); `lsof` in phase B |
| 2 | Secrets only in `.env`; placeholders; token never in a logged URL | Done: `load_secrets`; `Secrets` hides them from repr; httpx logger at WARNING; `redact`; `test_network_errors_and_refusals_back_off_and_retry` (token in an error → `<token>` in the log), `test_logs_hold_no_token_text_ids_or_paths` |
| 3 | Allowlist by numeric id; others and groups/channels ignored, counted, no reply | Done: `classify`; `test_classify`, `test_others_get_no_reply_and_are_only_counted`, `test_allowed_users_must_be_numeric_ids` |
| 4 | `telegram whoami` reads without marking read, prints to the terminal only | Done: `pending_senders` (no offset); `test_pending_senders_reads_without_confirming`; README step 3 |
| 5 | Logs: update ids, timings, counts only | Done: `test_logs_hold_no_token_text_ids_or_paths`, `test_logs_hold_no_text_chat_or_paths` |
| 6 | Network errors and 429 back off and retry; loop survives a bad update; a crashed search replies briefly | Done: `test_network_errors_and_refusals_back_off_and_retry`, `test_rate_limited_poll_waits_retry_after`, `test_rate_limit_waits_retry_after_then_retries`, `test_one_bad_update_does_not_stop_the_loop`, `test_a_crashed_search_replies_with_a_short_error`; a wrong token stops at once (`test_wrong_token_fails_at_once`) |
| 7 | Text → `search()` once with the CLI default → one album of derivatives with captions; default 5 | Done: `test_text_message_sends_top_results_as_one_album_of_derivatives`, `test_search_runs_once_with_the_cli_defaults`, `test_send_photos_as_one_album` |
| 8 | `original N` by `sendDocument`, only if on the drive or fetchable, size limit | Done: `test_original_*` (6 tests), `test_send_document` |
| 9 | `more`, `help`, `/start` (allowlisted only) | Done: `test_more_pages_through_the_last_search`, `test_help_and_start_do_not_load_the_models`, `test_start_replies_with_help` |
| 10 | Pen drive unplugged → thumbnails and a note | Done: `test_pen_drive_unplugged_sends_thumbnails`, `test_missing_derivative_falls_back_to_thumbnail` |
| 11 | Chat actions while searching and uploading | Done: `test_chat_actions_while_searching_and_sending`; end to end: typing, typing, upload_photo |
| 12 | D21: load on first search, unload after idle | Done with fakes: `test_lazy_searcher_loads_once_and_unloads`, `test_worker_calls_on_idle_after_the_timeout`, `test_unloaded_searcher_reopens_the_db`. RSS in phase B |
| 13 | Telegram adapter implementing `ChatAdapter`; httpx client; shared code factored, not copied | Done, with one difference: the adapter is `ChatBot` + `TelegramClient` (see "Changes from the plan") |
| 14 | `imgsearch bot` runs `chat.adapter`; WhatsApp behind `chat.adapter: whatsapp` | Done: `test_bot_runs_the_configured_adapter` (both values), `test_config_names_a_known_adapter`; WhatsApp tests pass through `build_whatsapp_app` |
| 15 | launchd template; README: BotFather, `.env`, whoami, launchd | Done: `docs/launchd/com.imgsearch.bot.plist` (`plutil -lint` OK); README "Chat bot (P3)" |
| 16 | Phase B real check | Pending (phase B) |
| 17 | Tests and ruff pass; no network; no new dependencies | Done: 451 passed, ruff clean; `pyproject.toml` unchanged |
| 18 | Plan updated (D13, D20/D21 refs); report; hand-back last; merged in phase B | Plan done (D13 cites D20's "nothing exposed"; D21 names `chat.idle_unload_s`); merge in phase B |

## Files changed

- **New:** `imgsearch/chat/telegram.py`, `imgsearch/chat/env.py`, `tests/test_telegram.py`,
  `tests/test_chat_bot.py`, `tests/test_chat_service.py`, `docs/reports/P3-T.md`.
- **Renamed:** `imgsearch/commands/serve.py` → `imgsearch/commands/bot.py` (now `bot`,
  `telegram whoami`, `make_bot`, `build_telegram`, `build_whatsapp_app`);
  `docs/launchd/com.imgsearch.serve.plist` → `com.imgsearch.bot.plist`.
- **Changed:**
  - `imgsearch/chat/bot.py` (`ChatBot`, `Messenger`)
  - `imgsearch/chat/service.py` (`Incoming`)
  - `imgsearch/chat/whatsapp.py`, `imgsearch/chat/webhook.py`
  - `imgsearch/config.py` (`ChatConfig`, `TelegramConfig`)
  - `imgsearch/cli.py`, `config.yaml`, `.env.example`
  - `tests/conftest.py` (shared chat fixtures), `tests/test_whatsapp.py`
  - `README.md`, `Personal Image Search Design and MVP Plan.md`
