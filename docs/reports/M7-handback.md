# M7 hand-back

2026-10-20 · Messages sent to the user, one section per phase. Written after the final commit.

## Phase A

Phase A of M7 is done. The eval runs end to end, and I've stopped for you to confirm the
query set before Phase B. Committed on `main` as `68538d7`, not pushed. 215 tests pass and
ruff is clean.

**Files to open** (both stay in the git-ignored `eval/runs/`):
- Query review sheet: `eval/runs/query_review.html`
- Contact sheet: `eval/runs/20261020-213657/contact_sheet.html`

**Initial run.** It covered 42 queries in 7 configurations and took 90 s. The task text
said 36, but the file also has your 6 queries, so I ran all 42.

| config | hit@5 | recall@5 | MRR |
| --- | ---: | ---: | ---: |
| vector | 0.93 | 0.90 | 0.89 |
| vector + rewrite | 1.00 | 0.97 | 0.95 |
| text | 0.60 | 0.54 | 0.49 |
| text + rewrite | 0.86 | 0.75 | 0.74 |
| hybrid | 0.90 | 0.82 | 0.80 |
| hybrid + rewrite (what `search` uses) | 0.95 | 0.86 | 0.87 |
| hybrid + rewrite, no captions | 0.93 | 0.83 | 0.75 |

**What stands out**
- **Vector + rewrite beats hybrid.** Each retriever returns 50 of only 130 photos, so a photo
  found by both scores higher than one ranked 1st by only one of them. Generic caption words
  then decide the order.
- **The rewrite helps every retriever on Hindi and Hinglish.** Vector recall goes from 0.82
  to 0.93 on Hindi and from 0.79 to 0.97 on Hinglish.
- **Some rewrites are wrong:** कबूतरों का झुंड became "flock of parrots", जयपुर became
  "Jayaipur", and "hare dupatte mein aurat…" came out garbled.

**Queries I think need your call:**
1. `en-txt-polsci`: add `IMG_20190506_184214`, which shows the same Political Science
   questions.
2. `dv-jaipur-fort` and `hi-green-dupatta`: should `IMG_20161102_135155` be in one or both?
3. `user-car`: keep the quilted-seat selfie, or drop the query?
4. 42 queries is above the acceptance file's 30–40.

## Phase B

M7 is done, and with it the MVP. The baseline is committed: 41 confirmed queries at
`b1d195e`. On this set, **vector + rewrite is the best configuration (recall@5 0.98), ahead
of hybrid + rewrite (0.87), which `imgsearch search` uses now**. Committed on `main` up to
`995d226`, not pushed. 215 tests pass and ruff is clean.

**Your confirmation** (commit `b1d195e`) was applied as given:
- `user-car` dropped.
- `IMG_20161102_135155` added to `dv-jaipur-fort` and `hi-green-dupatta`.
- `IMG_20190506_184214` added to `en-txt-polsci`.

**Baseline** (`docs/eval/baseline.md`):

| config | hit@5 | recall@5 | MRR |
| --- | ---: | ---: | ---: |
| vector + rewrite | 1.00 | 0.98 | 0.99 |
| hybrid + rewrite | 0.98 | 0.87 | 0.89 |
| hybrid + rewrite, no captions | 0.98 | 0.85 | 0.78 |
| text + rewrite | 0.88 | 0.76 | 0.76 |

- Hybrid + rewrite is better than vector + rewrite on **no** query and worse on 8.
- The rewrite lifts vector hit@5 to 1.00 on Hindi (from 0.88) and on Hinglish (from 0.83).

**`per_retriever_k` experiment:**
- Hybrid + rewrite recall@5 is 0.87 at 50, 0.90 at 20 and 0.93 at 10. A smaller value helps
  hybrid every time, but it never reaches vector + rewrite.
- `config.yaml` is back at 50. 41 queries on 130 photos can overfit, and 0.06 is only 2–3
  queries.

**Decisions for you** (proposals in `docs/mvp-summary.md`; I changed nothing):
1. **Search default:** I recommend vector + rewrite. Keep hybrid available, and add 5–10
   small- or exact-text queries before trusting either choice. The current set leans
   toward visual descriptions.
2. **`per_retriever_k`:** if hybrid stays the default, set it to 10.
3. **Captions:** this eval doesn't justify the ~64 h run. For P2: ingest → embed → OCR
   (~3.5 h for 40k photos), with captions optional and last.

**Other findings**
- **Memory:** search uses about 6.8 GB and captioning about 5.8 GB, so they fit in 16 GB
  one at a time.
- **Disk is the tighter limit:** ~21 GB of derivatives for 40k photos, against 32 GiB free
  now.
- **Main failures:**
  - The hybrid "found by both" bonus (e.g. `hi-kal-shaadi`, `user-wedding-dress`).
  - Wrong rewrites (`dv-pigeons`, `dv-jaipur-fort`).
  - Handwriting (`dv-handwriting`).
  - Near-duplicate bursts.
  - Place names without GPS (`user-rajasthan-trip`).
- **N1:** model loading no longer prints warnings or a progress bar.
