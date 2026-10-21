# P1.8 eval: places from GPS and the place filter

Before and after P1.8. The 53 queries of P1.7, plus 4 new user-confirmed place queries (`u3-*`), 57 in all.
`docs/eval/baseline.md` (MVP) and `docs/eval/baseline-p1.7.md` stay as they were.

| | Before | After, filter on | After, filter off | After, default |
| --- | --- | --- | --- | --- |
| Run | `eval/runs/20261021-204543` | `eval/runs/20261021-205543` | `eval/runs/20261021-205732` (`--no-place-filter`) | `eval/runs/20261021-210129` |
| Git commit | `9c0fdfe` (main before the P1.8 merge, clean) | `810d7d6` (clean) | `810d7d6` (clean) | `062ee08` (clean; `search.place_filter: false`) |
| Queries | 53 (sha256 `157ca91f…`) | 57 (sha256 `63fbc7d6…`) | 57 | 57 |
| Library | 1,442 photos, 908 with GPS | same, plus `places` (v2): 908 resolved, 49 with a landmark, 908 `place` text rows | same | same |
| Models | SigLIP 2 base 384; `qwen2.5:7b` on Ollama 0.40.6 | same | same | same |
| Settings | `per_retriever_k` 10, `rrf_k` 60, weights 1.0, date filter on | same, place filter on | same, place filter off | same as "filter off" |
| Rewrite failures | 0 | 0 | 0 | 0 |

- The default run (filter off by config) gave the same result as the `--no-place-filter` run for every query and
  config, so the "filter off" column is the new default.
- The "before" numbers for the 4 new queries are the "filter off" run: they didn't exist at the before run.
- Cells are **hit@5 / recall@5 / MRR**.
- Place queries are the 6 old `type: place` queries plus the 4 `u3-*`. `hi-green-dupatta` ("…mahal ke garden…")
  is typed `scene`, but the acceptance list names it, so it is in the per-query table.

### Old 53 queries

| config | before | after, filter off | after, filter on |
| --- | --- | --- | --- |
| vector | 0.83 / 0.74 / 0.74 | 0.83 / 0.74 / 0.74 | 0.83 / 0.74 / 0.74 |
| vector+rw | 0.87 / 0.79 / 0.79 | 0.87 / 0.79 / 0.79 | 0.85 / 0.77 / 0.77 |
| text | 0.49 / 0.42 / 0.42 | 0.49 / 0.42 / 0.41 | 0.49 / 0.42 / 0.41 |
| text+rw | 0.66 / 0.54 / 0.55 | 0.66 / 0.54 / 0.54 | 0.66 / 0.54 / 0.54 |
| hybrid | 0.77 / 0.68 / 0.63 | 0.75 / 0.66 / 0.62 | 0.75 / 0.66 / 0.62 |
| hybrid+rw | 0.83 / 0.72 / 0.75 | 0.87 / 0.75 / 0.77 | 0.85 / 0.75 / 0.76 |
| hybrid+rw no captions | 0.81 / 0.66 / 0.66 | 0.81 / 0.66 / 0.63 | 0.79 / 0.66 / 0.62 |

### Place queries

| config | old 6 before | 10, filter off | 10, filter on | new 4, off | new 4, on |
| --- | --- | --- | --- | --- | --- |
| vector | 0.67 / 0.47 / 0.67 | 0.80 / 0.68 / 0.80 | 0.80 / 0.68 / 0.80 | 1.00 / 1.00 / 1.00 | 1.00 / 1.00 / 1.00 |
| vector+rw | 0.67 / 0.57 / 0.67 | 0.80 / 0.74 / 0.80 | 0.70 / 0.64 / 0.70 | 1.00 / 1.00 / 1.00 | 1.00 / 1.00 / 1.00 |
| text | 0.17 / 0.13 / 0.17 | 0.50 / 0.40 / 0.36 | 0.50 / 0.40 / 0.36 | 1.00 / 0.75 / 0.65 | 1.00 / 0.75 / 0.65 |
| text+rw | 0.33 / 0.17 / 0.22 | 0.60 / 0.50 / 0.47 | 0.60 / 0.50 / 0.47 | 1.00 / 0.95 / 0.88 | 1.00 / 0.95 / 0.88 |
| hybrid | 0.67 / 0.37 / 0.50 | 0.80 / 0.52 / 0.65 | 0.80 / 0.52 / 0.65 | 1.00 / 0.80 / 0.88 | 1.00 / 0.80 / 0.88 |
| hybrid+rw | 0.67 / 0.37 / 0.47 | 0.80 / 0.60 / 0.68 | 0.70 / 0.58 / 0.63 | 1.00 / 0.95 / 1.00 | 1.00 / 0.95 / 1.00 |
| hybrid+rw no captions | 0.67 / 0.30 / 0.50 | 0.80 / 0.60 / 0.75 | 0.70 / 0.58 / 0.70 | 1.00 / 0.90 / 1.00 | 1.00 / 0.90 / 1.00 |

### All 57 (after)

| config | filter off | filter on |
| --- | --- | --- |
| vector | 0.84 / 0.75 / 0.75 | 0.84 / 0.75 / 0.75 |
| vector+rw | 0.88 / 0.80 / 0.80 | 0.86 / 0.78 / 0.78 |
| text | 0.53 / 0.45 / 0.43 | 0.53 / 0.45 / 0.43 |
| text+rw | 0.68 / 0.57 / 0.57 | 0.68 / 0.57 / 0.57 |
| hybrid | 0.77 / 0.67 / 0.64 | 0.77 / 0.67 / 0.64 |
| hybrid+rw | 0.88 / 0.77 / 0.78 | 0.86 / 0.76 / 0.78 |
| hybrid+rw no captions | 0.82 / 0.68 / 0.66 | 0.81 / 0.67 / 0.65 |

### Per place query (recall@5)

| id | query | vector+rw before | off | on | hybrid+rw before | off | on |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `u2-india-gate` | images from India Gate outing | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |
| `u2-jaipur-coworkers` | Images from jaipur trip with my coworkers | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |
| `u2-book-fair` | images from delhi book fair (pustak mela) | 0.60 | 0.60 | 0.60 | 0.40 | 0.40 | 0.60 |
| `user-rajasthan-trip` | give me images of trip to rajasthan | 1.00 | 1.00 | 0.00 | 0.40 | 0.40 | 0.00 |
| `dv-jaipur-fort` | जयपुर का किला | 0.80 | 0.80 | 0.80 | 0.40 | 0.40 | 0.40 |
| `hi-green-dupatta` | hare dupatte mein aurat mahal ke garden ke saamne | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |
| `u3-goa-trip` | goa trip ki photos | — | 1.00 | 1.00 | — | 1.00 | 1.00 |
| `u3-mussoorie-night` | Mussoorie mall road at night | — | 1.00 | 1.00 | — | 1.00 | 1.00 |
| `u3-pune-trip` | pune trip photos | — | 1.00 | 1.00 | — | 0.80 | 0.80 |
| `u3-lotus-temple` | Lotus Temple visit | — | 1.00 | 1.00 | — | 1.00 | 1.00 |

## Rule 14: the place filter is off by default

The rule: keep the filter on only if place queries improve and the old queries don't drop by more than 0.02
recall@5. Default config, vector+rw:

| | filter off | filter on |
| --- | --- | --- |
| 10 place queries | 0.74 | 0.64 |
| old 53 | 0.79 | 0.77 |

Place queries got worse, so **`search.place_filter: false`**. `--place-filter` turns it on for one search or eval.

- **Why the filter loses.** `user-rajasthan-trip` ("give me images of trip to rajasthan") falls 1.00 → 0.00:
  - Its expected photos are the 2016 MVP trip, which has no GPS. Its captions say "Jaipur" and "Amber Fort",
    not "Rajasthan".
  - The filter keeps only photos whose GPS is in Rajasthan or whose text says "Rajasthan". That is the 2023
    Chittorgarh/Jaisalmer trip, the expected set of `u2-jaipur-coworkers`, so it is a real Rajasthan trip, just
    not this query's.
  - 534 of the 1,442 photos have no GPS (the MVP photos, most screenshots), so a hard filter drops them unless
    their text names the place.
- **Why nothing gains.** The four new queries already score 1.00 with vector+rw and no filter: SigLIP plus the
  rewrite find Goa beaches, the Mussoorie sign, Lotus Temple and the Pune trip photos on their own.
- **Where filtering did change results:**
  - `en-txt-iit` ("Delhi" resolved; still 1.00).
  - `u2-book-fair` hybrid+rw 0.40 → 0.60.
  - `user-rajasthan-trip` 1.00 → 0.00.
  - The others with a resolved place were already at 1.00.
- **Unchanged without the filter.** vector+rw (the default) is identical to the before run on all 53 old queries,
  with the same rewrites.
- **Text configs moved, from the new `place` rows and the bm25 shift:**
  - Place rows: `u2-india-gate` text 0.80 → 1.00 (its GPS place says "India Gate"); `u2-book-fair` no-captions
    0.40 → 0.60.
  - bm25 shift from 908 more short rows: `hi-laptop` hybrid+rw 0 → 1.00, `hi-iit-boy` hybrid 1.00 → 0,
    `user-rajasthan-trip` hybrid 0.60 → 0.40.
  - Net: hybrid+rw on the old 53 0.72 → 0.75; hybrid 0.68 → 0.66.

Caveat: 10 place queries, one library. A single query moves the place average by 0.10.

## The place each query's rewrite named (vector+rw)

| id | rewriter `place` | resolved |
| --- | --- | --- |
| `u2-india-gate` | India Gate | India Gate (landmark) |
| `u2-book-fair` | Delhi | Delhi (state; also the "Delhi" city row) |
| `user-rajasthan-trip` | Rajasthan | Rajasthan (state) |
| `u2-jaipur-coworkers` | jaipur | no: no photo's GPS is in Jaipur (the 2023 trip was Bhilwara, Chittorgarh, Jaisalmer) |
| `dv-jaipur-fort` | जयपुर | no: same reason |
| `en-txt-iit` | Delhi | Delhi |
| `u3-goa-trip` | Goa | Goa (state; also Mormugao, whose alternate names include "Goa") |
| `u3-mussoorie-night` | Mussoorie | Mussoorie (city) |
| `u3-pune-trip` | Pune | Pune (city) and Pune Division (district, alternate name "Pune") |
| `u3-lotus-temple` | Lotus Temple | Lotus Temple (landmark) |

## The 4 new queries (user-confirmed 2026-10-21)

| id | query | expected | chosen by eye from |
| --- | --- | ---: | --- |
| `u3-goa-trip` | goa trip ki photos | 67 | all photos in Goa 10–12 Sept 2022 (convocation, beaches, Chapora Fort, Anjuna); not the 9 Sept Delhi airport |
| `u3-mussoorie-night` | Mussoorie mall road at night | 23 | 6–7 June 2023 after dark: the lit sign, the railing and the dinner after |
| `u3-pune-trip` | pune trip photos | 68 | 10–13 June 2026: Pune (house garden, Aga Khan Palace, the mall, home) and Lonavla; not Ellora or screenshots |
| `u3-lotus-temple` | Lotus Temple visit | 17 | 12 Nov 2022, 12:21–12:58 at the temple; not Kalkaji Mandir that morning |

All four are easy for vector+rw (1.00). They mainly guard against a place change breaking trip queries.
