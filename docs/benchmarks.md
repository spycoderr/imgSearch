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
HDD. At ~0.6 MB per image the derivatives take ~24 GB for 40k photos, more than the plan's
10–15 GB estimate (D8); lowering the JPEG quality would shrink this.

## M2: embed

Not measured yet.

## M3: ocr

Not measured yet.

## M4: caption

Not measured yet.
