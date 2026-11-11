# P1.9 eval: rewriter models and search memory

The 57 labelled queries (sha256 `63fbc7d6…`: English 35, Hindi 8, Hinglish 14). Cells are
**hit@5 / recall@5 / MRR** for `vector+rw`, the default search (SigLIP 2 vectors after the rewrite). Ollama
0.40.6, temperature 0. The search side is the same in every run: the text tower's vectors equal the full model's
(`check-query-vectors`: lowest cosine 0.9999998), so only the rewriter differs between rows of one table.

## Confirmation on the merged code (2026-11-11)

`p1.9` at `7096e35` (main with P6.1, P5-B1 and P5-B2 merged in); "dirty" in the run records is only the untracked
variant config. DB: `sqlite3 .backup` of the main DB taken at 23:48 (14,871 photos, migration 8, integrity ok,
2,105 near-duplicate groups), in the worktree. Defaults: dedupe on, people filter off, person boost 0.002, date
filter on, place filter off, `per_retriever_k` 10, `num_ctx` 4096. Lock `data/heavy.lock` held for every run.

| rewriter | run | overall | English | Hindi | Hinglish | Hindi + Hinglish recall@5 | rewrite median |
| --- | --- | --- | --- | --- | --- | ---: | ---: |
| `qwen2.5:7b` (default) | `20261111-234901` | 0.79 / 0.681 / 0.67 | 0.77 / 0.664 / 0.71 | 1.00 / 0.808 / 0.78 | 0.71 / 0.650 / 0.49 | 0.708 | 1.40 s |
| `gemma3:4b` | `20261111-235119` | 0.81 / 0.683 / 0.69 | 0.77 / 0.653 / 0.71 | 0.88 / 0.733 / 0.75 | 0.86 / 0.729 / 0.62 | 0.730 | 1.99 s |
| Δ recall@5 | | +0.002 | −0.011 | **−0.075** | +0.079 | +0.022 | |

- A repeat of the `gemma3:4b` run (`20261111-235409`) gave the same numbers to the third decimal.
- **Rule 6 (acceptance item 6):** overall holds (+0.002); Hinglish +0.08; **Hindi −0.075, more than the 0.02
  allowed** when Hindi is taken on its own. With Hindi and Hinglish together (22 queries), +0.02.
- The Hindi drop is one query out of 8: `dv-beach-boy` (समुद्र किनारे लड़का) 1.00 → 0.00 ("boy at the seaside" →
  "a boy on the seashore"). `dv-jaipur-fort` gains (0.40 → 0.80: "Jaypur's fort" → "Jaipur Fort").
- Hinglish gains: `hi-iit-boy` 0 → 1.00, `hi-green-dupatta` 0 → 0.50 (qwen read "aurat mahal" as "women's hostel").
- English changes: `gemma3:4b` often leaves an English query as typed. `user-wedding-dress` 0 → 0.50,
  `user-rajasthan-trip` 0.80 → 0.40, `u2-sortly-offer` 0.50 → 0 (qwen's "shortly" for "sortly" happened to
  help), `u3-goa-trip` (Hinglish, left as typed) 0.60 → 0.20.
- Recall is lower than on the 1,445-photo copy below for both models: 14,871 photos give each query more
  competitors.

## Candidates on the 1,445-photo copy (2026-10-24/25)

DB copy of the main DB at 21:57 on 2026-10-24 (1,445 photos), before P6.1's dedupe, P5's faces and the OnePlus
batches. `qwen2.5:7b` at num_ctx 4096 unless noted.

| run | code | rewriter | overall | English | Hindi | Hinglish | rewrite median |
| --- | --- | --- | --- | --- | --- | --- | ---: |
| `20261024-212214` | main `b4b2717` (full model on mps) | `qwen2.5:7b` | 0.88 / 0.80 / 0.80 | 0.86 / 0.79 / 0.81 | 1.00 / 0.86 / 1.00 | 0.86 / 0.79 / 0.66 | 1.53 s |
| `20261024-215953` | text tower, CPU fp32 | `qwen2.5:7b` | 0.88 / 0.80 / 0.80 | 0.86 / 0.79 / 0.81 | 1.00 / 0.86 / 1.00 | 0.86 / 0.79 / 0.66 | |
| `20261024-233301` | text tower | `qwen2.5:3b` | 0.79 / 0.72 / 0.73 | 0.83 / 0.76 / 0.79 | 0.62 / 0.53 / 0.62 | 0.79 / 0.73 / 0.64 | 0.68 s |
| `20261024-233427` | text tower | `gemma3:4b` | 0.89 / 0.82 / 0.83 | 0.86 / 0.79 / 0.82 | 1.00 / 0.86 / 0.94 | 0.93 / 0.88 / 0.78 | 2.03 s |
| `20261024-233706` | text tower | `qwen2.5:7b`, num_ctx 1536 | 0.88 / 0.80 / 0.80 | 0.86 / 0.79 / 0.81 | 1.00 / 0.86 / 1.00 | 0.86 / 0.79 / 0.66 | |
| `20261025-032253` | text tower | `gemma3:4b`, num_ctx 1536 | 0.88 / 0.81 / 0.81 | 0.83 / 0.77 / 0.79 | 1.00 / 0.86 / 0.94 | 0.93 / 0.88 / 0.78 | |

- `qwen2.5:3b` fails rule 6 (Hindi −0.33). `gemma3:4b` passed rule 6 on this copy; on the full library (above)
  its Hindi recall drops by one query.
- The rewrite request is 793–809 prompt tokens + 23–64 output tokens (at most 872) for all three models. num_ctx
  1536 changes no recall@5 for `qwen2.5:7b` and saves 0.09 GB; for `gemma3:4b` it saves nothing.
