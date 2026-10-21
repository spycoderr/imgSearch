# P3-T hand-back reports

The brief reports the P3-T agent sent back at the end of each phase, saved as sent. The
formal report is `docs/reports/P3-T.md`.

## Phase A (2026-10-21)

**Phase A done (P3-T).** The Telegram bot is built and tested on branch `p3`
(`.worktrees/p3`, commit `d6284bd`), rebased on `main` with P1.6.1 and P1.7 phase A. Not
merged. Nothing ran against the real DB and no Telegram call was made.

- **`imgsearch bot`** runs the app named by `chat.adapter`; the default is `telegram`.
- **Long polling, no webhook:** no public URL or open port.
- **Only allowed users get replies:** private chats from the numeric ids in
  `TELEGRAM_ALLOWED_USER_IDS`. Groups, channels and other users are only counted.
- **`imgsearch telegram whoami`** shows your id without marking messages read.
- **Same behaviour as the WhatsApp bot, now shared code:** `ChatBot` with a `Messenger`
  per app.
  - Results go as one album; `original N` as a document (50 MB limit); `more`;
    `help`/`/start`.
  - Thumbnails when the pen drive is out; "typing…"/"sending photo…" while it works.
  - SigLIP unloads after 5 idle minutes.
- **Robust and quiet:** network errors back off and retry, rate limits wait as Telegram
  asks, and a wrong token stops with an error. Logs hold update ids and counts only; the
  token is redacted.
- **WhatsApp is parked** behind `chat.adapter: whatsapp`, with the Meta lock recorded in
  plan D13.
- **Checks:** 451 tests pass (39 new, with the Bot API faked); ruff clean; no new
  dependencies.
- **Docs:** README "Chat bot (P3)" (BotFather steps, `.env` keys, `whoami`, launchd, the
  privacy note), launchd template `docs/launchd/com.imgsearch.bot.plist`, plan, and
  `docs/reports/P3-T.md`.

**One difference from the checklist:** there's no separate `TelegramAdapter` class. The
Telegram adapter is the shared `ChatBot` with `TelegramClient`, the same way WhatsApp uses
`GraphClient`.

**For phase B (after P1.7 is reviewed):**
1. Create the bot with @BotFather (`/newbot`; `/setjoingroups` → Disable; `/setprivacy`
   → Enable) and put `TELEGRAM_BOT_TOKEN` in `.env`.
2. Message the bot from your phone.
3. Then I merge `p3` into `main`, run `whoami` with you, and do the real check: the
   queries, `more`, `original 1`, a second account, latency and RAM.

## Before the phase B merge: deterministic thread tests (2026-10-21)

**Fixed: no bot test waits on the clock any more.**
- The worker's loop is now `Worker.step()`, which the tests call directly. An empty queue
  counts as idle at once.
- The WhatsApp webhook test waits on a gate with no timeout, reopened in a `finally`.
- An unused timed gate in the Telegram tests is gone.
- **Results:** 453 tests. 10 full-suite runs in a row: 10/10 passed. 20 runs of the chat
  tests with all 10 cores busy: 20/20 passed.

## Phase B (2026-10-21)

**P3-T is done, with three items unverified: the Telegram bot is live on `main`, and your
own queries worked ("it works fine").** `p3` was rebased on `main` (with P1.7) and
fast-forwarded into `main`; not pushed. The bot is running from the main checkout.

**What the real check showed**
- **No listening port:** the bot only makes outbound HTTPS calls to Telegram and talks to
  the local Ollama.
- **20 messages, 0 errors, 0 rate limits (429).** We were far below Telegram's ~1 message
  per second per chat.
- **Latency:** cold 21.4 s (SigLIP load 6.6 s + cold Ollama rewrite 9.3 s); warm 5.5–8 s,
  10 photos included.
- **Memory:** 70 MB idle; 2.1 GB with SigLIP loaded, and about 7 GB in all with Ollama
  (free memory 80% → about 30%).

**Fixed during the check** (all tested and committed)
- **Slow replies (35–115 s):** IPv6 connections to Telegram stalled on this Mac. The bot
  now uses IPv4 only (`telegram.ipv4_only`); warm replies take 6–8 s since.
- **A count in the message:** "10 photos of …" / "top 3 …" sends that many (up to 20), and
  `more` pages by the same number.
- **`more` past 10:** searches now fetch deeper when more is wanted, so `more` reaches 20.
  The CLI and eval results are unchanged.
- **`original 1` was searched as a query:** it now also accepts "Original 1.", "original1",
  "orig 1" and "original #1".
- **P1.7 review N3:** when a date matches nothing, the bot says so before showing photos
  from all dates.

**Not verified on the live bot**
1. The no-reply check from a second Telegram account.
2. `original 1` and `more` past 10 after the fixes.
3. The memory drop after the 5-minute idle timeout (the bot was restarted for each fix;
   each stop freed the 2.1 GB).

All three are covered by tests.

**Worth knowing**
- **Stopping the bot:** a bot started in the background ignores Ctrl-C. Use
  `kill <pid>`; it finishes the reply in progress first. Run only one bot at a time.
- **Location:** 908 of 988 phone camera photos have GPS, but search doesn't use it yet. I
  propose a small offline place-name task (coordinates → city, state, country as searchable
  text), so "Jaipur ka kila" works without captions.
- **Possible small cleanup:** stop the Hugging Face Hub check at each model load
  (`HF_HUB_OFFLINE=1`).

**Running now:** `imgsearch bot` (stop: `kill $(pgrep -f "bin/imgsearch bot")`). Report:
`docs/reports/P3-T.md`.
