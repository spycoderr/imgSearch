# MVP eval baseline

The numbers later phases must beat. Produced by `uv run imgsearch eval`, with the
configurations described in `docs/reports/M7.md` and the README "Evaluation" section.

| | |
| --- | --- |
| Date | 2026-10-20 (2026-10-20T22:43:17) |
| Git commit | `b1d195eee5660669e82d05cd9d24b286b1fea6db` (working tree clean) |
| Query set | `eval/queries.yaml`, 41 queries confirmed by the user, sha256 `bf0f1dda54a8fad1…` |
| Mix | english 21, hinglish 12, hindi 8; scene 17, object 9, text 8, place 2, hard 5 |
| Library | 130 photos; all of ingest, embed, ocr, caption done |
| Models | embedder `google/siglip2-base-patch16-384`; rewriter `qwen2.5:7b` on Ollama 0.40.6; captions `mlx-community/Qwen3-VL-4B-Instruct-4bit` |
| Search settings | `top_k` 5, `per_retriever_k` 50, `rrf_k` 60 |
| Run | 7 configurations × 41 queries in 89 s; 0 rewrite failures |

Metrics, per query, averaged:

- **hit@5**: 1 if any expected photo is in the top 5.
- **recall@5**: expected photos found in the top 5 ÷ min(5, number expected).
- **MRR**: mean of 1 / rank of the first expected photo, counting only the top 5.

`+rw` means the query is rewritten to English first. "no captions" leaves captions out of
the text search, so it searches OCR text only.

## Overall

| config | n | hit@5 | recall@5 | MRR |
| --- | ---: | ---: | ---: | ---: |
| vector | 41 | 0.93 | 0.90 | 0.91 |
| vector+rw | 41 | 1.00 | 0.98 | 0.99 |
| text | 41 | 0.61 | 0.54 | 0.50 |
| text+rw | 41 | 0.88 | 0.76 | 0.76 |
| hybrid | 41 | 0.93 | 0.83 | 0.82 |
| hybrid+rw | 41 | 0.98 | 0.87 | 0.89 |
| hybrid+rw no captions | 41 | 0.98 | 0.85 | 0.78 |

## By language (hit@5 / recall@5 / MRR)

| config (hit / recall / MRR) | english (n=21) | hindi (n=8) | hinglish (n=12) |
| --- | --- | --- | --- |
| vector | 1.00 / 0.99 / 1.00 | 0.88 / 0.82 / 0.88 | 0.83 / 0.79 / 0.77 |
| vector+rw | 1.00 / 0.99 / 1.00 | 1.00 / 0.95 / 1.00 | 1.00 / 0.98 / 0.96 |
| text | 0.90 / 0.80 / 0.79 | 0.00 / 0.00 / 0.00 | 0.50 / 0.46 / 0.33 |
| text+rw | 0.90 / 0.80 / 0.79 | 1.00 / 0.78 / 0.88 | 0.75 / 0.67 / 0.63 |
| hybrid | 1.00 / 0.91 / 0.91 | 0.88 / 0.73 / 0.65 | 0.83 / 0.75 / 0.77 |
| hybrid+rw | 1.00 / 0.91 / 0.91 | 1.00 / 0.81 / 0.94 | 0.92 / 0.85 / 0.82 |
| hybrid+rw no captions | 1.00 / 0.88 / 0.81 | 1.00 / 0.88 / 0.84 | 0.92 / 0.78 / 0.68 |

## By type (hit@5 / recall@5 / MRR)

| config (hit / recall / MRR) | hard (n=5) | object (n=9) | place (n=2) | scene (n=17) | text (n=8) |
| --- | --- | --- | --- | --- | --- |
| vector | 0.80 / 0.72 / 0.80 | 1.00 / 1.00 / 1.00 | 1.00 / 0.75 / 0.60 | 0.88 / 0.88 / 0.88 | 1.00 / 0.97 / 1.00 |
| vector+rw | 1.00 / 0.92 / 1.00 | 1.00 / 1.00 / 1.00 | 1.00 / 1.00 / 0.75 | 1.00 / 0.99 / 1.00 | 1.00 / 0.97 / 1.00 |
| text | 0.20 / 0.16 / 0.10 | 0.67 / 0.57 / 0.53 | 0.50 / 0.50 / 0.12 | 0.53 / 0.47 / 0.49 | 1.00 / 0.91 / 0.84 |
| text+rw | 0.80 / 0.48 / 0.60 | 0.89 / 0.79 / 0.75 | 0.00 / 0.00 / 0.00 | 0.94 / 0.83 / 0.83 | 1.00 / 0.91 / 0.91 |
| hybrid | 0.80 / 0.64 / 0.67 | 1.00 / 0.98 / 1.00 | 1.00 / 0.45 / 0.23 | 0.88 / 0.79 / 0.77 | 1.00 / 0.94 / 0.94 |
| hybrid+rw | 0.80 / 0.56 / 0.70 | 1.00 / 0.98 / 0.94 | 1.00 / 0.70 / 0.29 | 1.00 / 0.90 / 0.96 | 1.00 / 0.94 / 0.94 |
| hybrid+rw no captions | 1.00 / 0.73 / 0.65 | 1.00 / 1.00 / 1.00 | 1.00 / 0.70 / 0.67 | 0.94 / 0.79 / 0.64 | 1.00 / 0.94 / 0.94 |

## Caveat

41 queries on 130 photos is a small set, and the queries mostly describe what the photos
look like. Treat differences of a few hundredths as noise: one query moves recall@5 by
about 0.02. A later phase should be compared with `imgsearch eval-compare <this run> <new
run>` on the same query set, or on a larger one alongside this one.
