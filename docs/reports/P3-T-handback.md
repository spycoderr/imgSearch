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
