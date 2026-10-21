# P1.7 eval: file names, dates and weighted merging

Before and after P1.7 on the 53-query set: the 41 MVP queries and the 12 user-written `u2-*` queries on phone
photos. `docs/eval/baseline.md` stays as the MVP record (41 queries, 130 photos).

| | Before | After |
| --- | --- | --- |
| Run | `eval/runs/20261021-155500` | `eval/runs/20261021-171423` (default settings) |
| Date | 2026-10-21T15:55:00 | 2026-10-21T17:14:23 |
| Git commit | `74e9867` (main before P1.7, clean) | `4ece5a8` (P1.7 + weekday fix; dirty only by the two temporary weight configs, see below) |
| Library | 1,442 photos; ingest, embed, ocr done; captions 1,441 (130 MVP photos have useful captions) | same photos, plus `file_mtime` (1,433), source folders (1,312) and `filename` text (1,442) |
| Query set | `eval/queries.yaml`, sha256 `157ca91f048ae729…` | same |
| Models | SigLIP 2 base 384; `qwen2.5:7b` on Ollama 0.40.6 | same; rewriter prompt changed (dates) |
| Search settings | `per_retriever_k` 10, `rrf_k` 60, no weights, no date filter | same, `signal_weights` {vector 1.0, text 1.0}, date filter on |
| Rewrite failures | 0 | 0 |

Cells are **hit@5 / recall@5 / MRR**. "Old 41" are the MVP queries, "new 12" the `u2-*` queries.

## Overall: before

| config | all 53 | old 41 | new 12 |
| --- | --- | --- | --- |
| vector | 0.83 / 0.74 / 0.74 | 0.93 / 0.85 / 0.84 | 0.50 / 0.35 / 0.37 |
| vector+rw | 0.83 / 0.75 / 0.75 | 0.93 / 0.87 / 0.86 | 0.50 / 0.33 / 0.37 |
| text | 0.47 / 0.41 / 0.41 | 0.51 / 0.46 / 0.45 | 0.33 / 0.24 / 0.29 |
| text+rw | 0.64 / 0.52 / 0.52 | 0.71 / 0.59 / 0.59 | 0.42 / 0.28 / 0.29 |
| hybrid | 0.79 / 0.68 / 0.62 | 0.85 / 0.77 / 0.70 | 0.58 / 0.38 / 0.37 |
| hybrid+rw | 0.81 / 0.70 / 0.71 | 0.90 / 0.80 / 0.81 | 0.50 / 0.33 / 0.34 |
| hybrid+rw no captions | 0.75 / 0.62 / 0.61 | 0.88 / 0.74 / 0.71 | 0.33 / 0.20 / 0.25 |

## Overall: after (default settings: equal weights, date filter on)

| config | all 53 | old 41 | new 12 |
| --- | --- | --- | --- |
| vector | 0.83 / 0.74 / 0.74 | 0.93 / 0.85 / 0.84 | 0.50 / 0.35 / 0.37 |
| **vector+rw (default)** | **0.87 / 0.79 / 0.79** | **0.93 / 0.88 / 0.88** | **0.67 / 0.46 / 0.47** |
| text | 0.49 / 0.42 / 0.42 | 0.51 / 0.45 / 0.43 | 0.42 / 0.33 / 0.36 |
| text+rw | 0.66 / 0.54 / 0.55 | 0.71 / 0.58 / 0.58 | 0.50 / 0.41 / 0.44 |
| hybrid | 0.77 / 0.68 / 0.63 | 0.83 / 0.75 / 0.69 | 0.58 / 0.42 / 0.40 |
| hybrid+rw | 0.83 / 0.72 / 0.75 | 0.88 / 0.80 / 0.83 | 0.67 / 0.48 / 0.49 |
| hybrid+rw no captions | 0.81 / 0.66 / 0.66 | 0.90 / 0.75 / 0.74 | 0.50 / 0.37 / 0.40 |

"no captions" now searches OCR and file-name text (was OCR only).

## By language

| config | english (n=32) | hindi (n=8) | hinglish (n=13) |
| --- | --- | --- | --- |
| vector+rw before | 0.78 / 0.73 / 0.76 | 1.00 / 0.78 / 0.90 | 0.85 / 0.77 / 0.63 |
| vector+rw after | 0.84 / 0.77 / 0.80 | 1.00 / 0.86 / 1.00 | 0.85 / 0.77 / 0.63 |
| hybrid+rw before | 0.81 / 0.70 / 0.72 | 1.00 / 0.82 / 0.90 | 0.69 / 0.60 / 0.55 |
| hybrid+rw after | 0.88 / 0.76 / 0.78 | 1.00 / 0.85 / 1.00 | 0.62 / 0.56 / 0.54 |

## By type

| config | hard (n=7) | object (n=10) | place (n=6) | scene (n=17) | text (n=13) |
| --- | --- | --- | --- | --- | --- |
| vector+rw before | 0.57 / 0.37 / 0.46 | 1.00 / 0.95 / 0.84 | 0.83 / 0.67 / 0.72 | 0.88 / 0.83 / 0.85 | 0.77 / 0.71 / 0.71 |
| vector+rw after | 0.71 / 0.60 / 0.71 | 1.00 / 0.95 / 0.84 | 0.83 / 0.67 / 0.72 | 0.88 / 0.83 / 0.85 | 0.85 / 0.75 / 0.73 |
| hybrid+rw before | 0.57 / 0.37 / 0.46 | 0.80 / 0.80 / 0.75 | 0.83 / 0.45 / 0.51 | 0.88 / 0.84 / 0.85 | 0.85 / 0.72 / 0.71 |
| hybrid+rw after | 0.71 / 0.54 / 0.71 | 0.80 / 0.80 / 0.75 | 0.67 / 0.37 / 0.47 | 0.88 / 0.84 / 0.85 | 0.92 / 0.78 / 0.77 |

## Weights and the date filter (acceptance item 14)

Six runs at commit `4ece5a8`, same 53 queries. Weights change only the hybrid configurations; vector+rw is the
same in every run with the same filter setting. Text weights 0.5 and 0.25 were run from temporary copies of
`config.yaml` with only `signal_weights` changed (deleted afterwards; `meta.json` records the weights).

| text weight | date filter | run | vector+rw (all / old 41 / new 12, recall@5) | hybrid+rw | hybrid+rw no captions |
| --- | --- | --- | --- | --- | --- |
| 1.0 | on | `171423` | 0.79 / 0.88 / 0.46 | 0.72 / 0.80 / 0.48 | 0.66 / 0.75 / 0.37 |
| 1.0 | off | `171600` | 0.77 / 0.88 / 0.38 | 0.71 / 0.80 / 0.39 | 0.64 / 0.75 / 0.28 |
| 0.5 | on | `171738` | 0.79 / 0.88 / 0.46 | 0.75 / 0.84 / 0.46 | 0.79 / 0.87 / 0.50 |
| 0.5 | off | `171915` | 0.77 / 0.88 / 0.38 | 0.73 / 0.84 / 0.38 | 0.77 / 0.87 / 0.42 |
| 0.25 | on | `172052` | 0.79 / 0.88 / 0.46 | 0.74 / 0.84 / 0.42 | 0.79 / 0.87 / 0.50 |
| 0.25 | off | `172229` | 0.77 / 0.88 / 0.38 | 0.72 / 0.84 / 0.33 | 0.77 / 0.87 / 0.42 |
| before | none | `155500` | 0.75 / 0.87 / 0.33 | 0.70 / 0.80 / 0.33 | 0.62 / 0.74 / 0.20 |

The date filter changes one query, `u2-birthday` (0 → 1.00 recall with it on). Weights 0.5 and 0.25 help the
old 41 in hybrid (0.80 → 0.84) and cost the new 12.

**Default (item 15 rule):** none of the item-14 hybrid settings beats vector+rw (0.46) on the new 12 without
losing more than 0.02 on the old 41: equal weights reach 0.48 but drop the old 41 by 0.08; 0.5 and 0.25 don't
beat 0.46. The default stays **vector+rw, date filter on**. Not in the item-14 list, "hybrid+rw no captions"
at text weight 0.5 or 0.25 does pass the numbers (new 12 0.50, old 41 0.87, −0.01): the gain is half of one
query (`u2-signature`). Left to the user (see `docs/mvp-summary.md`, "P1.7 addendum").

**Caveat:** 53 queries on one library, 12 of them new. One query moves a new-12 average by up to 0.08 and an
old-41 average by up to 0.02. The weights were picked and measured on the same queries.

## The 12 new queries, before → after (vector+rw and hybrid+rw, default settings)

hit@5 / recall@5 / RR.

| query | vector+rw before | vector+rw after | hybrid+rw before | hybrid+rw after | why |
| --- | --- | --- | --- | --- | --- |
| u2-birthday | 0 / 0.00 / 0.00 | 1 / 1.00 / 1.00 | 0 / 0.00 / 0.00 | 1 / 1.00 / 1.00 | Date filter: "15-16 sept" → 2026-09-15..16 keeps only that night's photos |
| u2-bbq-nation | 1 / 0.40 / 1.00 | 1 / 0.40 / 1.00 | 1 / 0.40 / 1.00 | 1 / 0.40 / 1.00 | "on saturday" → 2026-10-17; no photos that day, so search fell back to all dates (the photos are from Sat 10-10) |
| u2-networking-notes | 0 / 0.00 / 0.00 | 0 / 0.00 / 0.00 | 1 / 0.60 / 0.50 | 1 / 0.40 / 0.50 | Hybrid: 1,442 new short `filename` rows change bm25's statistics; one ChatGPT notes image fell from text #3 to below the top 5 |
| u2-sortly-offer | 0 / 0.00 / 0.00 | 1 / 0.50 / 0.25 | 1 / 0.50 / 0.25 | 1 / 0.50 / 0.50 | The new prompt rewrites "of sortly" as "shortly"; that reorders the vector list (luck, not a P1.7 feature) |
| u2-profile-photo | 0 / 0.00 / 0.00 | 0 / 0.00 / 0.00 | 0 / 0.00 / 0.00 | 0 / 0.00 / 0.00 | Unchanged: no name, folder or date names "profile photo" |
| u2-purple-saree | 1 / 0.50 / 0.20 | 1 / 0.50 / 0.20 | 0 / 0.00 / 0.00 | 0 / 0.00 / 0.00 | Unchanged |
| u2-jaipur-coworkers | 0 / 0.00 / 0.00 | 0 / 0.00 / 0.00 | 0 / 0.00 / 0.00 | 0 / 0.00 / 0.00 | Unchanged: camera names, no captions, no GPS |
| u2-signature | 1 / 0.50 / 0.20 | 1 / 0.50 / 0.20 | 0 / 0.00 / 0.00 | 1 / 1.00 / 0.50 | Hybrid: file names "Signature Nilabh New_1", "Signature Nilabh black_1" are text #1 and #2 |
| u2-hsr-su | 0 / 0.00 / 0.00 | 0 / 0.00 / 0.00 | 0 / 0.00 / 0.00 | 0 / 0.00 / 0.00 | Unchanged: `StarRail_Image_*` names give "star rail", but the query says "HSR simulated universe" |
| u2-india-gate | 1 / 1.00 / 1.00 | 1 / 1.00 / 1.00 | 1 / 1.00 / 1.00 | 1 / 1.00 / 1.00 | Unchanged |
| u2-book-fair | 1 / 0.60 / 1.00 | 1 / 0.60 / 1.00 | 1 / 0.40 / 0.33 | 1 / 0.40 / 0.33 | Unchanged at equal weights (0.60 at text weight 0.5) |
| u2-ultrasound | 1 / 1.00 / 1.00 | 1 / 1.00 / 1.00 | 1 / 1.00 / 1.00 | 1 / 1.00 / 1.00 | Unchanged (already found by OCR) |

Old-41 changes: `dv-jaipur-fort` 0.20 → 0.80 (vector+rw) because the new prompt writes "Jaypur" not "Jayapur"
(rewrite noise); `hi-iit-boy` 0.50 → 0.00 (hybrid+rw) because bm25's statistics moved a near-duplicate into the
top 5. No other old query changed in vector+rw or hybrid+rw.

## Parsed date ranges

The eval records the rewriter's `date_range` per query. Three of the 53 name a date; all were parsed
correctly in every after-run (today was Wednesday 2026-10-21):

| query | parsed range | with the filter on |
| --- | --- | --- |
| `u2-birthday` "Give me the birthday images from 15-16 sept" | 2026-09-15 to 2026-09-16 | filtered |
| `u2-bbq-nation` "… barbecue nation outing on saturday" | 2026-10-17 | no photos that day; fell back to all dates |
| `hi-kal-shaadi` "kal wali shaadi ki photos" | 2026-10-20 | filtered to yesterday's 4 photos; the expected 2018 wedding photos were already missed before (recall 0.00 in every run) |

Superseded runs: `20261021-165814` to `20261021-170712` (commit `15991dc`, before the weekday fix) parsed "on
saturday" as 2026-10-21 (today), and the filter then dropped `u2-bbq-nation` to 0. That led to the fix in
`4ece5a8` (weekdays worked out in code). Apart from that query, only `u2-book-fair` differs: the changed
prompt (it now lists the past week) made the rewrite drop "(pustak mela)", which moves its hybrid ranks.
