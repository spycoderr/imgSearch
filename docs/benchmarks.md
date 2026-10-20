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

Not measured yet.

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

Not measured yet.
