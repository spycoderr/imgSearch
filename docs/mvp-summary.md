# MVP summary

2026-10-20 · Answers to the plan's MVP exit criteria, from the M7 baseline
(`docs/eval/baseline.md`, run `20261020-224317`, commit `b1d195e`) and the per-stage
benchmarks (`docs/benchmarks.md`). Metrics are hit@5 / recall@5 / MRR, averaged over the
41 confirmed queries.

**Caveat for every number below:** 41 queries on 130 photos is a small set, and anything
tuned on it can overfit. One query moves recall@5 by about 0.02. The queries also lean
toward **visual descriptions** ("white dog sitting on a carpet", "feet on a weighing
scale"), which is exactly what the image embedder matches. Only 8 of 41 are text-in-image
queries, where full-text search should help most.

## 1. Does the eval run end to end?

Yes. `uv run imgsearch eval` loads the models once and runs 7 configurations × 41 queries
in 89 s (0 rewrite failures). It writes `eval/runs/<timestamp>/`: per-query results,
metrics, commit, config copy and an offline contact sheet. `imgsearch eval-compare A B`
compares two runs. `imgsearch eval-review` re-checks the query file.

## 2. Do Hinglish and Hindi queries work through the rewrite?

Yes. The rewrite helps every retriever on Hindi and Hinglish and changes nothing on
English:

| config | english (21) | hindi (8) | hinglish (12) |
| --- | --- | --- | --- |
| vector | 1.00 / 0.99 / 1.00 | 0.88 / 0.82 / 0.88 | 0.83 / 0.79 / 0.77 |
| vector+rw | 1.00 / 0.99 / 1.00 | **1.00 / 0.95 / 1.00** | **1.00 / 0.98 / 0.96** |
| text | 0.90 / 0.80 / 0.79 | 0.00 / 0.00 / 0.00 | 0.50 / 0.46 / 0.33 |
| text+rw | 0.90 / 0.80 / 0.79 | 1.00 / 0.78 / 0.88 | 0.75 / 0.67 / 0.63 |
| hybrid | 1.00 / 0.91 / 0.91 | 0.88 / 0.73 / 0.65 | 0.83 / 0.75 / 0.77 |
| hybrid+rw | 1.00 / 0.91 / 0.91 | 1.00 / 0.81 / 0.94 | 0.92 / 0.85 / 0.82 |

- **Vector:** the rewrite lifts it to 1.00 hit@5 on every language. Even without the rewrite,
  SigLIP 2 finds 7 of 8 Devanagari queries.
- **Text:** without the rewrite, full-text search can't match Hindi at all (captions are
  English, and OCR is English-only).
- **The rewrite never lowered a query's vector recall.** But 4 rewrites were wrong, and the
  vector side mostly rescued them (see the failure patterns).
- **Cost:** about 1.6 s per query warm, and 5–9 s after Ollama has been idle for 5 minutes.

## 3. Should `imgsearch search` default to vector+rw or hybrid+rw?

**On this query set, vector+rw.**

| config | hit@5 | recall@5 | MRR |
| --- | ---: | ---: | ---: |
| vector+rw | **1.00** | **0.98** | **0.99** |
| hybrid+rw (current default, `per_retriever_k` 50) | 0.98 | 0.87 | 0.89 |
| hybrid+rw, `per_retriever_k` 10 | 1.00 | 0.93 | 0.91 |

- **Hybrid+rw is never better than vector+rw on any of the 41 queries**, neither in recall@5
  nor in reciprocal rank. It is worse on 8: `en-txt-polsci`, `hi-scooty`, `hi-kal-shaadi`,
  `dv-jaipur-fort`, `dv-hills`, `user-wedding-dress`, `user-pet-dog`, `user-rajasthan-trip`.
- **Even on text-in-image queries**, vector+rw scores 1.00 / 0.97 / 1.00 and hybrid+rw
  1.00 / 0.94 / 0.94. SigLIP reads large text such as "Dubai Bazaar" and "Indian Institute of
  Technology Delhi" well enough.

The caveat matters here. The set has few queries that only full-text search can answer:
small print, a word on a receipt, a name in a document. A real library of 40k photos with
screenshots, bills and documents will have more. So I don't recommend removing hybrid.

**Proposal (the user decides):**
1. Make vector+rw the default for `imgsearch search`, and keep `--signals vector,text`
   available.
2. Add 5–10 queries for small or exact text (receipts, labels, document words) to
   `eval/queries.yaml` before P2. Re-check vector vs hybrid on the bigger library.

This is a one-line default change in `cli.py`. I haven't made it.

### `per_retriever_k` experiment (config change only)

Hybrid configurations re-run with `search.per_retriever_k` at 10 and 20, besides the
baseline's 50. Vector-only and text-only results are the same at every value, because
their top 5 doesn't depend on the list length.

| config | per_retriever_k | overall | english | hindi | hinglish |
| --- | ---: | --- | --- | --- | --- |
| vector+rw | (any) | 1.00 / 0.98 / 0.99 | 1.00 / 0.99 / 1.00 | 1.00 / 0.95 / 1.00 | 1.00 / 0.98 / 0.96 |
| hybrid | 10 | 0.95 / 0.84 / 0.84 | 1.00 / 0.90 / 0.92 | 0.88 / 0.73 / 0.69 | 0.92 / 0.81 / 0.79 |
| hybrid | 20 | 0.95 / 0.86 / 0.84 | 1.00 / 0.92 / 0.92 | 0.88 / 0.73 / 0.69 | 0.92 / 0.83 / 0.79 |
| hybrid | 50 | 0.93 / 0.83 / 0.82 | 1.00 / 0.91 / 0.91 | 0.88 / 0.73 / 0.65 | 0.83 / 0.75 / 0.77 |
| hybrid+rw | 10 | 1.00 / 0.93 / 0.91 | 1.00 / 0.91 / 0.92 | 1.00 / 0.90 / 0.92 | 1.00 / 0.98 / 0.90 |
| hybrid+rw | 20 | 1.00 / 0.90 / 0.91 | 1.00 / 0.91 / 0.92 | 1.00 / 0.88 / 0.94 | 1.00 / 0.91 / 0.86 |
| hybrid+rw | 50 | 0.98 / 0.87 / 0.89 | 1.00 / 0.91 / 0.91 | 1.00 / 0.81 / 0.94 | 0.92 / 0.85 / 0.82 |
| hybrid+rw no captions | 10 | 1.00 / 0.92 / 0.86 | 1.00 / 0.91 / 0.86 | 1.00 / 0.90 / 0.92 | 1.00 / 0.94 / 0.82 |
| hybrid+rw no captions | 20 | 1.00 / 0.88 / 0.83 | 1.00 / 0.88 / 0.83 | 1.00 / 0.90 / 0.91 | 1.00 / 0.85 / 0.78 |
| hybrid+rw no captions | 50 | 0.98 / 0.85 / 0.78 | 1.00 / 0.88 / 0.81 | 1.00 / 0.88 / 0.84 | 0.92 / 0.78 / 0.68 |

Runs: `20261020-224502` (10), `20261020-224622` (20), `20261020-224317` (50).

- **Smaller is better for every hybrid configuration**, and the gain comes from Hindi and
  Hinglish. A shorter list stops a photo from collecting the "found by both" bonus from
  a weak match. For example, vector 15 + text 4 (0.029) outranks vector 1 alone (0.016).
  With 130 photos, a 50-deep list covers 38% of the library.
- **Still below vector+rw at every value.**
- **`config.yaml` is back to 50.** The gain is consistent, but it is 2–3 queries' worth on a
  small set, and the right value depends on library size: 10 of 130 is not 10 of 40,000.
  **Proposal:** if hybrid stays the default, set `per_retriever_k: 10` for now, and re-measure
  on the P2 library.

## 4. Are captions worth ~64 h for 40k photos?

**Not on this evidence.**

| config | hit@5 | recall@5 | MRR |
| --- | ---: | ---: | ---: |
| hybrid+rw (with captions) | 0.98 | 0.87 | 0.89 |
| hybrid+rw no captions | 0.98 | 0.85 | 0.78 |
| vector+rw (uses no text at all) | 1.00 | 0.98 | 0.99 |

- Inside hybrid, captions add 0.02 recall@5 and 0.11 MRR at `per_retriever_k` 50, and 0.01
  and 0.05 at 10.
- But vector+rw, which uses neither captions nor OCR, beats both.
- Captions also cause some hybrid misses: generic words ("traditional attire", "parked
  cars", "road") pull in wrong photos (`user-wedding-dress`, `hi-scooty`, `hi-kal-shaadi`).
- Captioning costs 5.8 s per image, about 64 h for 40k, against about 40 min for
  embeddings and 2.7 h for OCR.

**Recommendation:** for the P2 bulk run, do ingest → embed → ocr first; search works after
those. Run captions last, in the background, only if a later eval with more text-heavy
queries shows they pay off. This matches D17 and M4's recommendation. The 130 captions
already made stay in the DB.

## 5. Do the models fit in 16 GB?

Yes, if heavy stages run one at a time.

| Process | Peak memory |
| --- | --- |
| Search or eval process (SigLIP on MPS) | 2.24 GB footprint |
| Ollama with `qwen2.5:7b` | 4.6 GB resident (unloads after 5 idle min) |
| **Search total** | **~6.8 GB** |
| Caption stage (Qwen3-VL 4B, MLX) | 5.84 GB footprint (3.96 GB MLX peak) |
| Embed stage / OCR stage | 2.35 GB / 0.37 GB |

- Search plus a caption run would be about 12.6 GB on top of macOS and other apps. That's
  too close, so don't search during a caption run (README).
- **Disk is the tighter limit.** Derivatives are about 0.53 MB per photo, so ~21 GB for
  40k photos, and the Mac has 32 GiB free now. Check free space before the P2 bulk run.

## 6. Settings to run

The current `config.yaml` is what produced the baseline:

| Setting | Value |
| --- | --- |
| Embedder | `google/siglip2-base-patch16-384` on `mps`, text padded to 64 tokens |
| Rewriter | `qwen2.5:7b` on Ollama 0.40.6, temperature 0; re-run `eval/rewrite_examples.yaml` and `imgsearch eval` after any Ollama upgrade (outputs change across versions) |
| OCR | Apple Vision, `accurate`, `en-US` |
| Captioner | `mlx-community/Qwen3-VL-4B-Instruct-4bit`, `max_tokens` 100, 1 MP cap (optional in P2, see 4) |
| Search | `top_k` 5, `rrf_k` 60, `per_retriever_k` 50. Proposed: vector+rw default (3), or `per_retriever_k` 10 if hybrid stays |
| P2 stage order | ingest → embed → ocr → (caption last, in the background, if kept) |

## 7. Main failure patterns

| Pattern | Example query ids | What happens |
| --- | --- | --- |
| **RRF "found by both" bonus with generic caption words** | `hi-kal-shaadi`, `user-wedding-dress`, `hi-scooty`, `user-rajasthan-trip`, `user-pet-dog`, `dv-hills` | Vector ranks the right photos 1–3; hybrid pushes them out. Weak matches that appear in both lists win: "wedding"/"traditional attire", "road", "trip". This is the main reason hybrid < vector here |
| **Wrong rewrites** | `dv-pigeons` (कबूतर → "parrots"), `dv-jaipur-fort` ("Jayaipur's fort"), `hi-green-dupatta` ("hare dupatta" left untranslated), `hi-kal-shaadi` ("previous year's wedding") | The vector side mostly survives: `dv-pigeons` 1.00, `dv-jaipur-fort` 1.00 on vector+rw. Full-text search doesn't (`dv-jaipur-fort` text+rw 0.20), because the misspelt place name never matches the captions' "Jaipur" |
| **Handwriting** | `dv-handwriting` (best 0.60), `en-txt-polsci` (best 0.75) | OCR reads handwriting poorly, and partly framed pages lose their heading. Vector finds "paper with writing" but can't tell which page |
| **Near-duplicate bursts** | `en-txt-iit` (6 shots), `en-geo-homework` (7), `en-blazers` (4) | They score well (recall@5 counts up to 5), but the user gets five near-identical photos. This is the P6 dup-group work |
| **Devanagari without the rewrite** | every `dv-*` on `text` (0.00) | OCR is English-only and captions are English, so the original Hindi text never matches. With the rewrite, the English captions match (`dv-hindi-page` text+rw 1.00, found through its caption "text in a non-Latin script"), and vector finds it either way |
| **Place names without GPS** | `user-rajasthan-trip`, `dv-jaipur-fort` | No photo has GPS. Places come only from captions ("Jaipur", "Amber Fort"), and "Rajasthan" appears in none, so text+rw scores 0.00. Trip queries need dates or GPS (later phases) |

## Status

The MVP exit criteria are met. The eval runs end to end and produces a committed baseline.
Hinglish and Hindi work through the rewrite (vector+rw hit@5 1.00 on both). The benchmarks
show the models fit in 16 GB when run one at a time. Embeddings and OCR are affordable for
the full archive (~3.5 h for 40k photos). Captions (~64 h) aren't justified by this eval.

Open decisions for the user:
- The search default (vector+rw or hybrid+rw).
- `per_retriever_k`, if hybrid stays.
- Captions in P2.
- More text-heavy eval queries.

## Decisions after the MVP

2026-10-20 · Decided by the user after the M7 review (`docs/reviews/M7.md`), applied in M7.1.
`docs/eval/baseline.md` stays unchanged as the record.

1. **Default search is vector + rewrite. Hybrid stays an option.**
   - New config key `search.default_signals: [vector]`, so every adapter (the CLI now,
     WhatsApp later) gets the same default from one place.
   - `imgsearch search "..."` uses it when no `--signals` is given.
     `--signals vector,text` still runs hybrid, and `--signals text` runs text only.
   - The eval's configurations are unchanged: they name their signals explicitly.
2. **`search.per_retriever_k` is 10** (was 50), for the hybrid mode. Vector-only results don't
   depend on it.
   - Re-run after the change (`eval/runs/20261020-230042`, commit `365d411`):
     - vector+rw: 1.00 / 0.98 / 0.99, same as the baseline.
     - hybrid+rw: 1.00 / 0.93 / 0.91, same as the k = 10 experiment. Every metric in every
       group matches.
   - Re-measure on the P2 library: 10 of 130 photos is a different cut from 10 of 40,000.
3. **Captions: keep the stage.** In P2, run it last and in the background, after ingest,
   embed and OCR. Search works without captions, and the default (vector + rewrite) doesn't
   use them. Captions still feed the hybrid mode's text search, which is where place names
   like "Amber Fort in Jaipur" come from (see `docs/reports/M7.1.md`).

## P1.7 addendum: search default after file names and dates

2026-10-21 · Applied the rule in `docs/acceptance/P1.7.md` item 15. Numbers are in `docs/eval/baseline-p1.7.md`
(53 queries: the 41 MVP queries plus 12 user-written `u2-*` queries on 1,442 photos).

1. **Default stays vector + rewrite, now with the date filter on.**
   - Rule: hybrid becomes the default only if it beats vector+rw on the new 12 without dropping the old 41 by
     more than 0.02 recall@5.
   - vector+rw after P1.7: recall@5 0.88 on the old 41, 0.46 on the new 12 (before: 0.87 and 0.33).
   - hybrid+rw, equal weights: 0.80 / 0.48. Better on the new 12, but 0.08 worse on the old 41.
   - hybrid+rw, text weight 0.5: 0.84 / 0.46; text weight 0.25: 0.84 / 0.42. Neither beats 0.46.
   - So `search.default_signals` stays `[vector]` and `search.signal_weights` stays equal (1.0, 1.0).
2. **Date filter on.** It changes one query (`u2-birthday`, 0 → 1.00) and none for the worse. When nothing
   falls in the range, search shows all dates and says so (`u2-bbq-nation`). `--no-date-filter` turns it off.
3. **Open for the user: hybrid without captions.** Text search over OCR and file names only (no captions),
   merged with vector at text weight 0.5, scored 0.87 / 0.50 (old 41 −0.01, new 12 +0.04). That passes the
   rule's numbers, but it isn't one of the settings the rule names, the gain is half of one query
   (`u2-signature`, found by its file name), and making it a default would need a new setting (text kinds per
   signal). Worth re-checking on the P2 library, where captions will be rare.

**Caveat:** 53 queries on one library. One new query moves the new-12 average by up to 0.08, and the
weights were tuned and measured on the same queries. File names help hybrid mostly (signatures, scans); the
default vector search gains from the dates and the rewrite only.
