# P3 hand-back reports

The brief reports the P3 agent sent back at the end of each phase, saved as sent. The formal
report is `docs/reports/P3.md`.

## Phase A (2026-10-21)

**Phase A done (P3).** The work is on branch `p3` in `.worktrees/p3`, commit `7cc9de0`. Nothing
is merged into `main`. Nothing ran against the real DB, no models loaded, and the real Graph
API was never called.

- **`imgsearch serve`** runs a FastAPI webhook server on 127.0.0.1:8765. It does Meta's
  verification handshake and checks the HMAC signature of every POST (401 if wrong). It drops
  senders not on the allowlist (logged by count) and repeated message ids, queues each message
  and answers 200 at once.
- **One worker thread** runs the searches. It loads SigLIP on the first search and unloads it
  after 5 idle minutes (D21).
- **Replies** use the CLI's search defaults. The top 5 come back as derivative images
  (thumbnails if the pen drive is out), uploaded to the media endpoint and sent by media id,
  each captioned "rank · date · caption".
- **Commands:** `help`, `more`, and `original N` (sent as a document, fetched from S3 if
  evicted, 100 MB limit). A failed rewrite, no results or an error each get a one-line reply.
- **Logs** hold message ids, counts and timings only.
- **Checks:** 43 new tests (Graph API faked with `httpx.MockTransport`); 335 pass; ruff clean.
  A smoke run of `serve` with fake secrets confirmed it listens on localhost only, verifies,
  returns 401 to an unsigned POST and shuts down cleanly.
- **Docs:** README "WhatsApp bot (P3)" (Meta setup, the tunnel, `.env` keys, launchd),
  `docs/launchd/com.imgsearch.serve.plist` (not installed), plan "Changes during build", and
  `docs/reports/P3.md`.

**Flags**
- `more` gives only one extra page (results 6–10), because a search returns at most
  `per_retriever_k` (10) results. Changing that means touching `Searcher`, which P1.7 is
  also editing.
- WhatsApp doesn't list HEIC as a document type, so check `original N` on a HEIC in phase B.
- Duplicate detection is in memory, so a message Meta redelivers after a restart gets
  answered again.
- `config.py` and `cli.py` will have small merge conflicts with P1.6.1.

**Phase B needs from you:** the Meta app with a system-user token, the app secret, the phone
number id, your number as a test recipient, the five `WHATSAPP_*` keys in `.env`, a tunnel
(a quick `cloudflared tunnel --url` is fine to start), and no caption run.
