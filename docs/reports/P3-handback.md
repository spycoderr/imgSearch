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

## Phase B, part 1 (2026-10-21)

**Part 1 stopped: two blockers on the Meta side, and Funnel is down.** Branch `p3` (rebased on
`main` at `315bfb6`), last code commit `2fdaa05`. Not merged. No search ran, no DB was
created and no models loaded.

**Why the phone gets nothing: not known yet, because Meta's status callbacks never reach us.**
1. **Our app isn't subscribed to your WhatsApp Business Account** (`1650890083124255`).
   Its `subscribed_apps` lists only Meta's internal "WA DevX Webhook Events 1P App". So
   message and status webhooks never come to our URL, even though the dashboard webhook
   verified (13:07:27). Fix: `POST /1650890083124255/subscribed_apps` with our token.
   You approved it, but by then the token had expired.
2. **The token was the temporary one** (type `USER`, expired 13:30 IST). Every call now gets
   **error 190/463**, "Session has expired". A permanent system-user token is needed.
3. Once both are fixed, a `failed` callback names the cause. Likely candidates: 131047
   (24 h window), 131026 (undeliverable), 130497 (country restriction).

**Done**
- Rebased on `main` (plan conflict resolved, both sides kept). Status callbacks (sent,
  delivered, read, failed, with code and title) are logged without the recipient.
- Found and fixed a leak: WhatsApp message ids contain the phone number (base64), so logs now
  carry a short hash (`m-…`). 350 tests pass; ruff clean.
- `.env` symlinked; the server runs from the worktree with `WHATSAPP_ALLOWED_SENDERS=0`
  overriding `.env`, so a text from you is only counted (no search, no second DB, no SigLIP
  during the caption run).
- Tailscale Funnel exposes only `/webhook`. Public DNS took ~15 min to appear, which is why
  Meta's first verify failed. After that, the public path gave: wrong token 403, right token
  200, unsigned or bad signature POST 401, `/health` 404.
- README: Tailscale Funnel, the DNS delay, and setup step 7 (`subscribed_apps`). Plan "Changes
  during build" and `docs/reports/P3.md` updated.

**Problem: Funnel stopped answering after ~14:00.** Both relays now fail the TLS handshake,
and your phone on mobile data can't connect either. Restarting Funnel didn't help, and
nothing reaches the Mac. Next: quit and reopen the Tailscale app, then re-check from the
phone. Cloudflare Tunnel is the fallback.

**Still running**
- Server: `imgsearch serve` from `.worktrees/p3`, log `.worktrees/p3/data/logs/serve.log`.
  Stop it with `pkill -INT -f "imgsearch serve"`.
- Funnel on `/webhook`. Stop it with `tailscale funnel --https=443 off`.

**Needed from you:**
1. Restart the Tailscale app and check Funnel from the phone.
2. A permanent system-user token in `.env`.
3. Then I subscribe the app to the WABA, restart `serve`, send one test text and read the
   callback.

## Phase B, part 1 continued (2026-10-21, 15:55)

**Callbacks now reach the webhook, and they say why nothing arrives: Meta has blocked your
business account from sending.** Our code and setup are fine.

- I subscribed our app to the WhatsApp Business Account (it now lists `img-search`),
  restarted `serve` on the new token, and sent one test text at 15:51:32. It was accepted,
  and 8 s later the callback came back: **failed, error 131031 "Business Account locked"**.
- The Health Status API gives the reasons. The phone number and the app can send; the
  account and the business are BLOCKED:
  - **131000:** the business profile is incomplete; Legal Name, Country and Website are
    required "before integrity checks can proceed".
  - **141006:** a problem with the payment method (blocks business-initiated messages).
  - **141010:** the business hasn't passed business verification.
- **Fix, cheapest first; I re-test after each:**
  1. Meta Business Suite → Settings → Business info: fill in Legal Name, Country and Website.
  2. If still blocked: add a payment method in WhatsApp Manager.
  3. If still blocked: business verification (needs documents; takes days).
- **The new token is temporary again** (type `USER`, expires today 17:30 IST). Create it as
  a **system user** in Business Settings → Users → System users → Generate token. It should
  show type `SYSTEM_USER` and no expiry.
- **Funnel:** after your Tailscale restart one relay works (`.153`), the other (`.217`) still
  fails the TLS handshake. Meta retries, so deliveries arrive, possibly late.
- **Still running:** `serve` (stop: `pkill -INT -f "imgsearch serve"`) and Funnel on
  `/webhook` (stop: `tailscale funnel --https=443 off`). Not merged; no search ran.

## Phase B, part 1: re-test after the business profile (2026-10-21, 16:05)

**Still blocked, but one step closer.** After you filled in the business profile, the
business went from BLOCKED to LIMITED (only verification is missing). The WhatsApp Business
Account is still BLOCKED by the payment-method error (141006). A test text at 16:02:14 failed
again with 131031 "Business Account locked".

**Next, free test first:** send any message (e.g. "hi") from your phone to the test number.
The server only counts it (no search). I then send one reply inside the 24 h window. Meta
says 141006 blocks only business-initiated messages, and the bot only replies, so this shows
whether the bot works without a payment method. If it still fails, add a payment method in
WhatsApp Manager → Payment settings and I re-test. The token still expires at 17:30 IST.

## Phase B, part 1: service-window test (2026-10-21, 16:05)

**Replies are blocked too.** Your "hi" reached the bot at 16:03:02, so inbound works. My reply
at 16:03:16, inside the 24 h window, failed with 131031 "Business Account locked". The
account-level block stops replies as well, not just messages the business starts.

**Next:** add a payment method in WhatsApp Manager → Payment settings (for the business that
owns this WhatsApp account), then tell me and I re-test. If it still fails, business
verification is the last block. The token expires at 17:30 IST.

## Phase B, part 1: re-test after the payment method (2026-10-21, 16:15)

**Still locked.** The payment method cleared the account's block: health status now says
the WhatsApp Business Account, phone number and app can all send, and only "business not
verified" (141010) remains (overall LIMITED). But replies inside the open 24 h window at
16:08 and 16:11 both failed with 131031 "Business Account locked".

**Left to do (both on Meta's side):**
1. Business verification: Business settings → Security centre → Start verification
   (documents for the legal name; takes days).
2. A Meta support ticket asking to unlock, quoting error 131031 and WABA id
   1650890083124255. A lock can remain from an integrity flag the health check doesn't show.

Nothing in our code needs to change: inbound messages arrive, and every send is accepted
and gets a callback within ~5 s. When a reply is delivered, part 2 (search) can start.

## Phase B, part 1: last retry, server stopped (2026-10-21, 16:20)

**Still locked.** The retry at 16:19 failed with 131031 "Business Account locked"; the
health status was unchanged (only business verification is missing). As you asked, I stopped
`imgsearch serve`. Tailscale Funnel is still on, forwarding `/webhook` to nothing; turn it
off with `tailscale funnel --https=443 off`.

Next: business verification and/or a Meta support ticket for the unlock (error 131031, WABA
1650890083124255), plus a permanent system-user token. Then restart `serve` and send one
test message.
