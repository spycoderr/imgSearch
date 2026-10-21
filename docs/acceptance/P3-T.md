# P3-T acceptance criteria: Telegram bot (replaces WhatsApp as the active chat adapter)

2026-10-21. WhatsApp is parked: the Meta business account stays locked (error 131031) after the business profile
and a payment method; only business verification or Meta support is left (`docs/reports/P3.md`). The plan's
fallback (D13) is Telegram. Reuse P3's work on branch `p3`: `ChatAdapter`, the lazy `Searcher` with idle unload
(D21), commands (`help`, `more`, `original N`), the pen-drive fallback, quiet logs. Only the transport changes.

## Transport and security

| # | Criterion | How to verify |
| --- | --- | --- |
| 1 | **Long polling** with the Bot API (`getUpdates`, a long-poll timeout, `offset` = last update id + 1). No webhook, no public URL, no listening port. On start it calls `deleteWebhook` so polling works | Read code; `lsof` shows no listening socket |
| 2 | Secrets only in `.env`: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_USER_IDS` (numeric ids, not usernames, which can change). `.env.example` placeholders; nothing in commits or logs. The token is never part of a logged URL | `git grep`; read logs |
| 3 | **Allowlist by numeric user id**: anything from other users or any group/channel is ignored (counted, not logged by content or id), with no reply | Tests |
| 4 | A one-off `imgsearch telegram whoami`: reads pending updates **without marking them read** and prints sender ids **to the user's terminal only**, so the user can find their own id | Test; README |
| 5 | Logs hold update ids, timings and counts only: no message text, user ids, names or images | Read code |
| 6 | Network errors and Telegram 429 (rate limit, `retry_after`) back off and retry; the loop never dies on one bad update; a crash in one search replies with a short error text | Tests with `httpx.MockTransport` |

## Behaviour (same as P3, Telegram-shaped)

| # | Criterion | How to verify |
| --- | --- | --- |
| 7 | A text message runs `search()` once with the CLI default and replies with the top results as **one album** (`sendMediaGroup`) of resized derivatives, each with a short caption (rank, date, short description); default 5 from config | Tests |
| 8 | `original N` sends the full original as a **document** (`sendDocument`) only if it's on the drive or fetchable (P1.6 fetch), refusing over Telegram's size limit with a clear message | Tests |
| 9 | `more` sends the next 5 for the last query; `help` lists commands; `/start` replies with help (for allowlisted users only) | Tests |
| 10 | Pen drive unplugged: falls back to thumbnails and says so | Test |
| 11 | "Typing…"/"uploading photo" chat action while searching, so slow cold starts don't look dead | Test |
| 12 | D21: SigLIP loads on the first search and unloads after the idle timeout | Test with fakes; RSS observed in phase B |

## Code, running, docs

| # | Criterion | How to verify |
| --- | --- | --- |
| 13 | A `TelegramAdapter` implementing `ChatAdapter`, a small Bot API client over `httpx` (no new Telegram library), shared search/command code factored out of the WhatsApp adapter, not copied | Read code |
| 14 | `imgsearch bot` runs the active adapter from config (`chat.adapter: telegram`); WhatsApp code stays, unused, behind `chat.adapter: whatsapp` | Test |
| 15 | launchd plist template for `imgsearch bot` (not installed by the agent), README steps: BotFather (`/newbot`; `/setjoingroups` → Disable; `/setprivacy` → Enable), `.env` keys, `whoami`, launchd | Read |
| 16 | Phase B real check from the user's phone: English, Hinglish, Hindi and a text-in-image query, `more`, `original 1`; a second Telegram account (or a group) gets no reply; cold vs warm latency recorded; RAM back to baseline after the idle timeout | Report |
| 17 | Tests and ruff pass; no real network in tests; no new dependencies | Run |
| 18 | Plan updated: D13 (Telegram active, WhatsApp parked with reason), D20/D21 refs; report `docs/reports/P3-T.md`, hand-back last; branch `p3` merged in phase B; commit on `main`, not pushed | Read |

## Privacy note for the README

Bot chats are Telegram cloud chats (not end-to-end encrypted): the photos the bot sends are stored on Telegram's
servers, like WhatsApp sends would be on Meta's. Only resized derivatives are sent unless the user asks for
`original N`.
