# Benchmarks

Seconds per image and peak memory for each model stage, measured on the Mac mini M4
(16GB). Each model task fills in its own section, so parallel tasks don't edit the same lines.

## M1: ingest

No model; CPU only (Pillow). 130 JPEGs (96MB, mostly 2560x1920 and 2560x1440, from a 2016–2019
vivo phone), fresh DB, measured with `/usr/bin/time -l .venv/bin/imgsearch ingest`.

| Measure | Value |
| --- | --- |
| Total wall time (register + stage, incl. Python startup) | 5.6 s |
| Seconds per image | ~0.043 |
| Peak memory (max RSS) | 186 MB |
| Derivatives on disk (2048px, JPEG q90) | 74 MB (~0.58 MB per image) |
| Thumbnails on disk (256px, JPEG q85) | 1.6 MB (~13 KB per image) |

At this rate 40k photos take about 30 minutes of CPU, plus the time to read them from the
HDD. At ~0.58 MB per image the derivatives take ~23 GB for 40k photos, more than the plan's
10–15 GB estimate (D8); lowering the JPEG quality would shrink this.

## M1.1: ingest version 2

Same 130 photos, re-ingested at `stages.ingest: "2"`. Derivatives are now capped at 2048×1536
pixels in area (not a 2048 px long edge) and saved at JPEG quality 85 (was 90). Measured
with `/usr/bin/time -l .venv/bin/imgsearch ingest`.

| Measure | M1 | M1.1 |
| --- | --- | --- |
| Total wall time for 130 images | 5.6 s | 5.8 s |
| Seconds per image | ~0.043 | ~0.044 |
| Peak memory (max RSS) | 186 MB | 186 MB |
| Derivatives on disk | 74 MB | 69 MB |
| Derivative size per image | ~0.58 MB | ~0.53 MB |
| Projected derivatives for 40k photos | ~23 GB | ~21 GB |
| Thumbnails on disk | 1.6 MB | 1.6 MB |

The quality drop saves less than expected because 16:9 photos (47 of 130) are now
2364×1330 (3.1 MP) instead of 2048×1152 (2.4 MP).

## M2: embed

`google/siglip2-base-patch16-384` through transformers on `mps`, float32, one image per
call. All 130 derivatives (`stages.embed: "1"`), measured with
`/usr/bin/time -l .venv/bin/imgsearch run embed` on the Mac mini M4, with no other model
running. The breakdown (load, per image, query) comes from a separate timing script over the
same 130 derivatives and the same model.

| Measure | Value |
| --- | --- |
| Total wall time for 130 images (`run embed`, incl. Python startup and model load) | 14.6 s |
| Import torch + transformers | 1.7 s |
| Model load (from the HF cache) | 2.6 s |
| Seconds per image (decode + preprocess + forward), median / first / max | 0.060 / 0.150 / 0.150 |
| Image loop, 130 images | 7.8 s |
| Peak memory: max RSS / peak memory footprint (incl. MPS allocations) | 1.03 GB / 2.35 GB |
| MPS driver memory with the model loaded | 1.57 GB |
| Projected for 40k photos | ~40 min |
| Text query, `retrieve()` with model loaded: first / warm median / warm max | 179 ms / 9 ms / 11 ms |
| Vector SQL alone (130 rows, top 50) | 0.11 ms |
| `search-vec` command, whole (startup + load + query) | 4.7 s |

Per-image time includes JPEG decoding of derivatives up to 3.1 MP; the model sees 384×384.
At query time the embedder (about 1.6 GB on MPS) must be resident next to the rewrite LLM.

## M3: ocr

Apple Vision (`VNRecognizeTextRequest`, accurate level, `en-US`) through `ocrmac`, on the
CPU/Neural Engine; no model download. All 130 derivatives (`stages.ocr: "1"`), measured with
`/usr/bin/time -l .venv/bin/imgsearch run ocr` on the Mac mini M4, with no other model running.

| Measure | Value |
| --- | --- |
| Total wall time for 130 images (incl. Python startup) | 31.8 s (30.6 s user CPU) |
| Seconds per image | ~0.24 |
| Peak memory (max RSS) | 371 MB |
| Projected for 40k photos | ~2.7 h |
| `search-text` query (whole command, incl. startup) | 0.22 s |

Phase A timings for single images were 0.05 s (480×640) to 0.30 s (the 6203×507 panorama).
ocrmac re-encodes each image as PNG before handing it to Vision, which is part of the CPU time.

## M4: caption

`mlx-community/Qwen3-VL-4B-Instruct-4bit` through mlx-vlm 0.7.9 on the GPU (MLX), greedy,
`max_tokens: 100`, images downscaled to at most 1024×1024 px in area. All 130 derivatives
(`stages.caption: "1"`), measured with `/usr/bin/time -l .venv/bin/imgsearch run caption` on
the Mac mini M4, with no other model running. Per-image times come from the gaps between
consecutive `stage_status.updated_at` stamps (1 s resolution). Load and MLX peak come from the
smoke and 10-image prompt trial runs (same model and settings, `mx.get_peak_memory()`).

| Measure | Value |
| --- | --- |
| Total wall time for 130 images (`run caption`, incl. Python startup and model load) | 755.5 s |
| Import mlx-vlm | ~3 s |
| Model load (from the HF cache) | 2.2–2.5 s |
| Seconds per image, mean / median / min / max | 5.8 / 6 / 3 / 7 |
| Seconds per image in the 10-image trial (0.1 s resolution) | 5.0–6.7 |
| MLX memory: model loaded / peak while captioning / after `unload()` | 3.10 GB / 3.96 GB / 0.00 GB |
| Peak memory: max RSS / peak memory footprint (incl. Metal allocations) | 3.88 GB / 5.84 GB |
| Caption length, median / max | 45.5 / 83 words |
| Projected for 40k photos | ~64 h (~2.7 days) |

Captioning is ~100× slower per image than embedding (0.06 s) and ~24× slower than OCR
(0.24 s). Each image costs up to ~1k image tokens of prefill plus up to 100 generated tokens.

## M6: query rewrite

`qwen2.5:7b` (Q4_K_M) served by Ollama 0.40.6 (brew service), temperature 0, JSON-schema
`format`, on the Mac mini M4 with no other model loaded. Run over the 20 queries in
`eval/rewrite_examples.yaml` after `ollama stop`; the first call includes loading the model.
Times are wall time around `OllamaRewriter.rewrite`, warm figures from a second pass over all
20 queries. Memory is from `ollama ps` and the RSS of the `llama-server` runner process.

| Measure | Value |
| --- | --- |
| First call (cold, includes model load) | 5.2–9.4 s (three cold starts: 5.2, 8.8, 9.4 s) |
| Warm, median / max over 20 queries | 1.67 / 1.97 s |
| Resident memory (`ollama ps`) | 4.6 GB, 100% GPU, context 4096 |
| `llama-server` runner RSS | 4.7 GB |
| Unloaded after | 5 min idle (Ollama default keep-alive) |

Candidates measured the same way during the model comparison (Q8):

| Model | Disk | Resident | Cold first call | Warm median / max |
| --- | --- | --- | --- | --- |
| `qwen2.5:3b` | 1.9 GB | 2.1 GB | 4.9 s | 0.74 / 1.34 s |
| `qwen2.5:7b` (chosen) | 4.7 GB | 4.6 GB | 8.8 s | 1.70 / 1.96 s |
| `gemma3:4b` | 3.4 GB | 2.2 GB | 6.6 s | 2.03 / 2.19 s |


## M5: search

Hybrid search (`open_searcher` once, then `Searcher.search` per query) on the Mac mini M4,
with no other model running. SigLIP 2 runs on MPS, and `qwen2.5:7b` on Ollama 0.40.6. Before
the first query, Ollama was emptied with `ollama stop`. Times are wall time inside
`search()`, over the 14 real queries in `docs/reports/M5.md`, all in one process. Memory is
from `/usr/bin/time -l .venv/bin/imgsearch search ...` (peak memory footprint) and
`ollama ps`.

| Measure | Value |
| --- | --- |
| `open_searcher`: import torch, load SigLIP, create rewriter | 5.95 s |
| First query, cold (Ollama loads the model) | 9.24 s: rewrite 8.71 s, retrieval 0.53 s |
| Warm query, median / max (13 queries) | 1.68 / 2.12 s |
| Warm rewrite, median / max | 1.64 / 2.08 s |
| Warm retrieval (vector + text + RRF + lookup), median / max | 0.046 / 0.058 s |
| `imgsearch search "..."` whole command, Ollama warm | 8.6–9.4 s |
| `imgsearch search "..." --signals text --no-rewrite` whole command | 0.41 s |
| Search process peak memory footprint | 2.20–2.23 GB |
| Ollama resident while loaded | 4.6 GB |
| Total while searching | ~6.8 GB (don't run alongside the 4–6 GB caption stage) |

## M7: eval

`imgsearch eval` on the 41 confirmed queries, 7 configurations, one process. Mac mini M4,
with no other model running. Ollama was emptied with `ollama stop` first.

| Measure | Value |
| --- | --- |
| Model load (SigLIP + rewriter client) | 7.0 s |
| Whole run (41 queries × 7 configurations, incl. load and contact sheet) | 89–90 s |
| Rewrite, median (each query once, cached for the other configurations) | 1.65 s |
| Eval process peak memory footprint (`/usr/bin/time -l`) | 2.24 GB |
| Ollama resident | 4.6 GB |
| Contact sheet size (130 thumbnails embedded once) | 2.2 MB |

## P1.9: memory while searching

The following measurements are preserved from upstream through `c797888`. They were
not rerun on this backport, which excludes upstream faces and near-duplicate features.

`imgsearch bench-memory`: the searcher as the bot builds it (`open_searcher` with the config's defaults), 10
fixed queries (`eval/memory-queries.txt`: 4 English, 4 Hinglish, 2 Hindi), rewriter unloaded from Ollama first.
Peak footprint = `proc_pid_rusage` lifetime max (includes Metal); Ollama = `/api/ps` size. Lock held, bot idle.

On the merged code (`7096e35`, 2026-11-11), DB copy of the main DB (14,871 photos):

| Measure | Before P1.9 (main `bb05d21`, 1,445 photos) | Text tower, CPU fp32 + `qwen2.5:7b` (default) | Text tower + `gemma3:4b` |
| --- | ---: | ---: | ---: |
| Searcher load | 2.5–4.2 s | 3.50 s | 3.20 s |
| First search, cold rewriter | 8.6–10.7 s | 10.09 s | 6.86 s |
| Warm search, median / max | 1.72 / 2.12 s | 1.55 / 2.03 s | 2.08 / 2.46 s |
| Search process peak footprint | 2.24 GB | **0.48 GB** | 0.48 GB |
| Ollama rewriter resident (ctx 4096) | 4.65 GB | 4.65 GB | 2.25 GB |
| **Total while searching** | **6.89 GB** | **5.13 GB** | **2.73 GB** |
| Footprint before load → after unload | 73 MB → 1.72 GB | 74 → 446 MB | 74 → 446 MB |
| Run record | `memory-20261024-211821.json`, `-211904.json` | `memory-20261111-234832.json` | `memory-20261111-235049.json` |

- A query embedding on the CPU takes ~0.02 s (9 ms on mps); the rest of a warm search is the rewrite.
- After the unload ~0.37 GB stays in the process: torch and transformers imports (~0.28 GB) and Python/SQLite.
  50 searches leave the same residue as 10 (+303 MB both, 1,445-photo copy), so nothing grows per search.
- Query-side variants (1,445-photo copy, `qwen2.5:7b`): text tower mps fp32 1.62 GB, mps fp16 2.10 GB, CPU fp16
  0.97 GB, CPU fp32 0.42 GB (the default). float16 copies the weights; mps keeps ~1 GB of Metal buffers after
  `empty_cache`.
- Rewriters (1,445-photo copy): `qwen2.5:3b` 2.09 GB in Ollama (warm search 0.77 s), `gemma3:4b` 2.25 GB,
  `qwen2.5:7b` 4.65 GB (4.56 GB at num_ctx 1536). Recall in `docs/eval/baseline-p1.9.md`.

### P1.9.1: `gemma3:4b` is the default (2026-11-12)

`imgsearch bench-memory` with the committed config (`gemma3:4b`, num_ctx 4096, prompt with the P1.9.1 sentence),
DB copy of 15,016 photos, lock held, bot idle (`memory-20261112-002310.json`): searcher load 3.21 s, first search
4.05 s (`gemma3:4b` was loaded in Ollama before the run, from the evals; P1.9 measured 6.86 s from an empty
Ollama), warm median / max 2.10 / 2.35 s, search process peak 0.48 GB, Ollama 2.25 GB, **total while
searching 2.73 GB**, footprint before load → after unload 74 → 445 MB. Same as P1.9's `gemma3:4b` column.
