# P3-T report: Telegram bot (replaces WhatsApp as the active chat adapter)

- **Phase A (code and tests):** 2026-10-21, branch `p3` in `.worktrees/p3`, rebased on
  `main` (`15991dc`, with P1.6.1 and P1.7 phase A).
- **Phase B (real messages):** 2026-10-21, after P1.7's review. `p3` was rebased on `main`
  (`bb9eed6`) and fast-forwarded into `main`; the work is on `main`, not pushed. The bot runs
  on Telegram from the main checkout.

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

### Phase B: the real check (2026-10-21, 17:36–18:08 IST)

**Setup**
- Rebased `p3` on `main` (`bb9eed6`). Conflicts in the plan and README were both-sided
  additions, kept from both sides. One follow-up fix: a duplicated README heading and the
  old P3 status row.
- The suite passed 3× (467, then 468 after the N3 change); ruff clean. Fast-forward merge
  into `main` (`954136c`); 468 pass on `main`.
- `imgsearch telegram whoami` found the user's id; it went into `.env` (only that line
  changed). One pending "hi" was marked read before the start, so the first search would be
  a true cold start.
- No caption run; DB 1442 photos with all stages done (captions 1441); pen drive mounted;
  Ollama 0.40.6.

**Transport (criterion 1).** `lsof` on the bot process: **no listening socket**. Its only
connections are outbound HTTPS to Telegram (`2001:67c:4e8:f004::9`, after the IPv4 switch
`149.154.166.110`) and `127.0.0.1:11434` (Ollama).

**Queries.** The user tested from the allowlisted phone with their own queries, not the five
suggested ones ("I used some other queries for testing"). Texts aren't logged, so they
aren't listed here. The user's verdict: "it works fine", including a 10-photo request ("the
last query was for 10"). Which languages and query types were covered isn't recorded. 20 messages, 19 searches,
**0 errors, 0 rate limits (429)**.

**Latency.** Wall = queued → handled, as the user felt it; work = time in the handler (after
any wait in the queue).

| Update | Wall s | Work s | Rewrite s | Search s | Note |
| --- | ---: | ---: | ---: | ---: | --- |
| u…417 | 84.4 | 84.4 | 11.59 | 0.48 | cold (SigLIP 5.7 s, Ollama cold); album upload ~65 s (IPv6 stall) |
| u…418 | 6.3 | 6.3 | 1.81 | 0.22 | warm |
| u…419 | 5.9 | 5.9 | 1.64 | 0.21 | warm |
| u…420/421/422 | 5.7 / 9.3 / 13.4 | 5.7 / 3.6 / 4.2 | ~1.65 | ≤0.26 | 3 messages at once; handled in order |
| u…423 | 6.9 | 6.9 | 1.95 | 0.23 | warm |
| u…424 | 35.3 | 35.3 | 1.52 | 0.27 | upload ~33 s (IPv6 stall) |
| u…425 | 5.5 | 5.5 | 1.50 | 0.24 | warm |
| u…426 | 61.0 | 61.0 | 1.21 | 0.23 | upload ~59 s (IPv6 stall) |
| u…427 | 115.0 | 115.0 | 1.74 | 0.21 | upload ~113 s (IPv6 stall) |
| u…428 | 5.9 | 5.9 | 1.19 | 0.25 | warm |
| u…429 | 84.8 | 84.8 | 1.61 | 0.22 | IPv6 stall; finished during the restart |
| **u…430** | **21.4** | 21.4 | 9.31 | 0.46 | **cold on IPv4**: SigLIP 6.6 s + Ollama cold 9.3 s + upload 3.8 s |
| u…431 | 6.5 | 6.5 | 1.31 | 0.17 | warm, IPv4 |
| u…432 | 8.1 | 8.1 | 1.56 | 0.24 | warm, IPv4, **10 photos** (upload ~6 s) |
| u…433 | 0.4 | 0.4 | – | – | `more` after 10 → "No more photos" (old 10-result cap) |
| u…434 | 5.8 | 5.8 | 1.75 | 0.27 | `original 1` as typed was **searched as a query** (bug, fixed below) |
| u…435, u…436 | 7.5, 5.9 | | ~1.4 | ~0.22 | warm, IPv4 |

- **Cold** (both models unloaded): 21.4 s on IPv4 (SigLIP load 6.6 s, rewrite 9.3 s, search
  0.5 s, upload 3.8 s).
- **Warm:** 5.5–8.1 s, of which rewrite ~1.2–2.0 s, search ~0.2 s, and the rest the album
  upload.
- **IPv6 stalls:** before the IPv4 switch, 5 of 13 replies took 35–115 s, all in the album
  upload. A direct `getMe` test: IPv4 connected 3/3 in 0.6–0.9 s; IPv6 1 of 3 hung until
  curl's 30 s timeout. After the switch: 7 replies, none over 8.1 s except the cold start.

**Memory** (`top` MEM = physical footprint, which counts MPS memory; sampled every 15 s).

| Moment | Bot process | System free |
| --- | ---: | ---: |
| Before start (Ollama unloaded) | – | 80% |
| Bot started, idle (no models) | 70 MB | 80–81% |
| During searches (SigLIP + Ollama `qwen2.5:7b`) | **2.1 GB** (peak 2105 MB) | 23–34% |
| New bot after restart, idle | 69–70 MB | 39–48% (Ollama unloading on its own) |

- Ollama's runner shows only ~250 MB in `top` (its Metal memory isn't counted there). The
  free-memory column shows its real cost: about 7 GB for search in total, as the MVP
  summary measured.
- **Not observed: the drop after the 5-minute idle timeout.** The bot was restarted
  three times during the check (IPv4, `more`/`original` fixes), and the user's messages
  were never 5 minutes apart while a bot had models loaded. Each "searcher unloaded" in the
  log came from a stop, and each stop took the process from 2.1 GB to gone. The idle unload
  itself is covered by tests only (`test_worker_handles_messages_in_order_then_goes_idle`,
  `test_lazy_searcher_loads_once_and_unloads`, `test_unloaded_searcher_reopens_the_db`).

**Not done in the real check**
- **A second account or a group gets no reply:** not tested; the user had no second
  account to hand. Covered by tests (`test_others_get_no_reply_and_are_only_counted`,
  `test_classify`). The log shows 0 ignored updates, so nobody else messaged the bot.
- **`original 1` and `more` past 10 after the fixes:** the fixed bot started at 18:07 and
  had no messages before the check ended at 18:08. Both are covered by tests.

**Findings and fixes during phase B** (all committed on `main`)
1. **P1.7 review N3 (date fallback).** The reply now says "No photos matched the dates X to
   Y, so these are from all dates." before the album. Tests:
   `test_date_fallback_says_no_photos_matched_the_dates`, `test_no_date_note_when_the_dates_matched`.
2. **IPv6 stalls → `telegram.ipv4_only: true`.** The client binds to 0.0.0.0.
3. **A count in the message** (asked for by the user): "10 photos …" / "top 3 …" sets the
   number sent, and `more` pages by it. 14 tests.
4. **`more` stopped at 10:** a search returned at most `per_retriever_k` (10). Now each
   retriever fetches `max(per_retriever_k, k)` (`Searcher.search`), so `more` reaches
   `chat.max_results` (20). Calls with k ≤ 10 (CLI default, eval) are unchanged.
5. **`original 1` was searched as a query:** only exact "original N" matched. Now also a
   trailing ".!?", "original1", "orig 1" and "original #1". 6 tests. The exact text typed
   isn't known (texts aren't logged); a keyboard full stop or capital is the likely cause.
6. **Restart pitfall:** a bot started with `nohup … &` from a script ignores SIGINT
   (verified: Python sees `SIG_IGN` for SIGINT at start), so `kill -INT` did nothing. A
   second bot then started, and the two polled at once (8 × 409 Conflict over 2 minutes).
   `kill` (SIGTERM, handled like Ctrl-C) stopped the old one cleanly. The first SIGTERM took
   ~70 s to act, because the main thread was stuck in a stalled IPv6 call; on IPv4 the next
   restart took under 1 s. README "Running it" now says to stop it with `kill <pid>`.

**Rate limits.** Telegram's FAQ: at most about 1 message per second per chat, short bursts
allowed. The bot sends one album per query plus chat actions; the busiest moment was 3
albums in ~13 s. No 429s.

**Location (asked by the user).** 908 of the 988 phone camera photos (`source_relpath` at
the folder root) have GPS. Screenshots and app folders have none, and the first 130 test
photos have none. Search doesn't use GPS yet: there's no reverse geocoding, so place
queries ("Jaipur ka kila") match only through captions and the image itself. Proposed as
its own task (see Flags).

## Benchmark

Not a model task; latency and memory are in "Real-run results" above.

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

### Status after phase B

- **`more` past 10:** fixed (`Searcher.search` fetches `max(per_retriever_k, k)`); up to
  `chat.max_results` (20). Not yet tried on the live bot.
- **`original N`:** the live "original 1" was searched as a query; variants are accepted
  now. Not yet tried on the live bot, so HEIC as a document (`image/heic`) is still
  unconfirmed.
- **Album captions:** no complaint from the user; the text-list fallback isn't needed.
- **Very wide panoramas** (Telegram rejects photos with sides over 20:1, failing the whole
  album): not hit.
- **Redelivery after a crash:** unchanged. A normal stop confirms the offset; a crash
  replays the last batch.
- **Only one bot at a time.** A second `imgsearch bot` makes both get 409 Conflict until one
  stops (seen in phase B). launchd's `KeepAlive` runs one copy; when running it by hand,
  stop the old one with `kill <pid>` first.
- **Ollama's cold rewrite is the biggest part of a cold start** (9.3–11.6 s, against
  SigLIP's 5.7–6.6 s). It unloads on its own 5-minute `keep_alive` (D21), so a query after
  a pause of more than 5 minutes takes about 15 s longer.

### For later tasks

- **Place names from GPS (proposed task).** 908 of the 988 phone camera photos have
  lat/lon, but nothing turns them into places, so "Jaipur ka kila" relies on captions. A
  `place` stage could reverse-geocode each photo offline (for example GeoNames cities and
  admin regions, no network) to city, state and country. It would write them as a `place`
  text kind (searchable like file names), and later feed a filter from the rewrite's
  `place` field. This needs a dataset choice and probably one dependency, so it wants its
  own acceptance list.
- **Hugging Face checks the Hub at every model load.** At SigLIP load the log shows 5 GET
  requests to `huggingface.co` (model metadata) under the logger `httpx2`, although the
  model is in the cache. They carry no user data, but they add network time and noise.
  Fix: `HF_HUB_OFFLINE=1` for the bot (in the launchd plist or `.env`), and quieting
  `httpx2`.
- **Hybrid with k over 10** fuses deeper lists, so with `default_signals: [vector, text]`
  the bot's top 5 (it asks for k = 20) could differ slightly from `imgsearch search`'s.
  The current default is vector only, which is unaffected.
- WhatsApp needs only Meta to unlock the account (business verification or support). Then
  set `chat.adapter: whatsapp`, put a permanent system-user token in `.env`, and follow the
  README's "WhatsApp (parked)" steps.

## Acceptance checklist status (`docs/acceptance/P3-T.md`)

| # | Criterion | Status |
| --- | --- | --- |
| 1 | Long polling, offset = last + 1, `deleteWebhook` on start; no webhook, URL or port | Done: code and tests (phase A); real run: `lsof` shows no listening socket, only outbound HTTPS to Telegram and the local Ollama |
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
| 12 | D21: load on first search, unload after idle | Done in code and tests. Real run: 70 MB idle → 2.1 GB with SigLIP loaded; every unload seen came from a stop (2.1 GB → process gone). The drop after the 5-minute idle timeout itself wasn't observed (bot restarted 3 times for fixes) |
| 13 | Telegram adapter implementing `ChatAdapter`; httpx client; shared code factored, not copied | Done, with one difference: the adapter is `ChatBot` + `TelegramClient` (see "Changes from the plan") |
| 14 | `imgsearch bot` runs `chat.adapter`; WhatsApp behind `chat.adapter: whatsapp` | Done: `test_bot_runs_the_configured_adapter` (both values), `test_config_names_a_known_adapter`; WhatsApp tests pass through `build_whatsapp_app` |
| 15 | launchd template; README: BotFather, `.env`, whoami, launchd | Done: `docs/launchd/com.imgsearch.bot.plist` (`plutil -lint` OK); README "Chat bot (P3)" |
| 16 | Phase B real check | Mostly done: the user's own queries (texts not logged; languages and types not recorded) worked by the user's check ("it works fine"), including a 10-photo request; cold 21.4 s vs warm 5.5–8.1 s; 0 errors. Not done: the second account; `original 1` and `more` past 10 on the fixed bot (fixed and tested after real-run bugs); the idle-timeout memory drop |
| 17 | Tests and ruff pass; no network; no new dependencies | Done: 451 passed, ruff clean; `pyproject.toml` unchanged |
| 18 | Plan updated (D13, D20/D21 refs); report; hand-back last; merged in phase B | Done: plan D13, D21 and P3-T entries in "Changes during build" (phase A and B); report; merged into `main` (fast-forward), not pushed; hand-back last |

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

## Follow-ups

### 2026-10-21: thread tests made deterministic (before the phase B merge)

The supervisor saw the suite fail once in 13 runs on `p3` (it passed on re-runs), most
likely the thread tests that waited with `timeout=5` under CPU load.

- **`Worker.step()`** (`imgsearch/chat/service.py`) handles the next message, or calls
  `on_idle` if none comes within `idle_s`, and returns False after `stop`. The thread runs
  it in a loop. The worker tests call it directly with `idle_s=0`: an empty queue raises
  `queue.Empty` at once, without sleeping. One test keeps a real thread but depends only on
  `stop()` draining the queue and joining. (`test_chat_service.py`: 5 tests, was 3.)
- **WhatsApp webhook test** (`test_whatsapp.py`): the searcher's gate starts open and is
  waited on with no timeout. The one test that closes it, to see the 200 before the search
  runs, reopens it in a `finally`, so it can neither time out early nor hang. Six
  `gate.set()` lines that were no longer needed are gone.
- **`test_telegram.py`:** removed an unused `gate` parameter (the dead `timeout=5`).
- No bot test now waits on the clock. Other clock uses in the suite are outside the bot,
  with wide margins (the import tests' 60 s settle window; one rewriter test that compares
  against today's date).
- **Results:** 453 tests.
  - 10 consecutive full-suite runs: 10/10 passed (load average about 2).
  - 20 runs of the four chat test files with 10 CPU spinners on the 10 cores: 20/20
    passed (load average up to 12).
