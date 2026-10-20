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
