# P3 / P3-T review: chat bot (WhatsApp parked, Telegram live)

2026-10-21 · Supervisor review of the `p3` branch as merged into `main` (up to `3e2e8d5`), `docs/reports/P3.md`,
`docs/reports/P3-T.md`.

## Verdict

**Accepted and pushed.** The Telegram bot runs from `main` and answered the user's real queries. 486 tests and ruff
pass; the flaky thread tests were made deterministic before the merge (10/10 full runs, 20/20 chat runs under load).

## Checked independently

- The running `imgsearch bot` process has **no listening socket**; Tailscale Funnel has no config (off).
- No bucket name, AWS key, Meta token or Telegram token text in any commit (watcher scan on every commit).
- The bot token is redacted from anything the client logs; logs hold update ids and counts only.

## WhatsApp (P3) outcome

Code complete and kept behind `chat.adapter: whatsapp`. Meta keeps the business account locked (error 131031)
even after the business profile and a payment method; only business verification or Meta support is left.
Webhook security was verified live (403 bad verify token, 401 bad signature). Parked by user decision; plan D13
updated.

## Notes

| # | Item | Owner |
| --- | --- | --- |
| T1 | Not yet verified live (covered by tests): no reply to a second account; `original 1` and `more` past 10 after the fixes; memory dropping after the 5-minute idle | User, next time the bot is used |
| T2 | Latency: cold 21 s (SigLIP 6.6 s + cold rewrite 9.3 s), warm 5.5–8 s with 10 photos. Fine for ~5 requests/hour; a "searching…" chat action covers the wait | — |
| T3 | Memory while active ~7 GB in total (SigLIP 2.1 GB + Ollama 4.6 GB); unloads when idle (D21). Don't run captions or evals while the bot is busy | User |
| T4 | IPv6 to Telegram stalled on this network; the bot is IPv4-only (`telegram.ipv4_only`) | — |
| T5 | Small cleanup: set `HF_HUB_OFFLINE=1` for model loads (no Hub check per load) | P1.8 or later |
| T6 | Run the bot as a launchd service (template `docs/launchd/com.imgsearch.bot.plist`) so it survives reboots; restart it after every code or config change (next: after P1.8 merges) | User |
