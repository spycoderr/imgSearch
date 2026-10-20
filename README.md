# imgsearch

Local photo search for a personal library. Ask in English, Hindi (Devanagari) or Hinglish
(for example "mummy ke saath Goa wali pizza ki photo") and get the 5 best-matching photos.
Everything runs on one Mac mini M4 (16GB). No cloud model calls are made.

The full design, the decisions behind it and the task breakdown are in
[`Personal Image Search Design and MVP Plan.md`](Personal%20Image%20Search%20Design%20and%20MVP%20Plan.md).
That doc is the source of truth. This README covers how to set up and run the project.

## Status

**M1 done.** The package skeleton, config, database schema, stage runner,
`imgsearch status` and `imgsearch ingest` work. Photos can be ingested (hash, EXIF,
derivative, thumbnail) but not yet searched. Commands marked *(planned)* arrive with the task
named next to them.

| Task | What it adds | Status |
| --- | --- | --- |
| M0 | Scaffold, `config.yaml`, SQLite schema, stage framework, `imgsearch status` | done |
| M1 | Local folder source and `ingest` stage (hash, EXIF, derivative, thumbnail) | done |
| M2 | `embed` stage (SigLIP 2) and vector retriever | done |
| M3 | `ocr` stage (Apple Vision) and full-text search | done |
| M4 | `caption` stage (Qwen-VL via MLX) | not started |
| M5 | Hybrid search with RRF fusion, `imgsearch search` | not started |
| M6 | Query rewriter for Hinglish/Hindi (Ollama) | not started |
| M7 | Eval harness: recall@5 report and HTML contact sheet | not started |

M2, M3 and M4 can be built in parallel once M1 lands.

## How it works

```
 indexing:  originals (read-only) -> ingest -> embed -> ocr -> caption -> SQLite
 query:     text -> rewrite to English -> vector search + full-text search -> RRF -> top 5
```

- **One SQLite file** holds everything: image records, per-stage status, vectors
  (sqlite-vec) and OCR/caption text (FTS5).
- **Originals are never modified.** Models work from a JPEG derivative (at most 2048×1536
  pixels in area) and a 256px thumbnail stored on the Mac, so search keeps working when the originals' drive is unplugged.
- **An image's identity is the SHA-256 of its bytes.** Originals are stored as paths relative
  to `originals_root`, so the library can move to another drive by changing one config value.
- **Stages are idempotent.** Re-running a stage does nothing unless its `version` changes,
  so swapping a model re-runs only that stage.

## Requirements

- macOS on Apple Silicon (built for a Mac mini M4, 16GB)
- [uv](https://docs.astral.sh/uv/) (Python 3.12 is pinned in `.python-version`)
- [Ollama](https://ollama.com/) running locally (`brew services start ollama`)
- Models, downloaded ahead of time:

  | Role | Model | Where it lives |
  | --- | --- | --- |
  | Image/text embeddings | `google/siglip2-base-patch16-384` | Hugging Face cache |
  | Captions | `mlx-community/Qwen3-VL-4B-Instruct-4bit` | Hugging Face cache |
  | Query rewrite | `qwen2.5:3b` | Ollama |
  | OCR | Apple Vision (built into macOS) | none to download |

## Setup

```sh
uv sync                          # create .venv from uv.lock
uv run hf download google/siglip2-base-patch16-384
uv run hf download mlx-community/Qwen3-VL-4B-Instruct-4bit
ollama pull qwen2.5:3b
```

Add dependencies with `uv add <pkg>` (or `uv add --dev <pkg>`), never `pip install`.

## Configuration

All paths, model names and stage versions live in `config.yaml` at the project root. Code
contains none of them. Relative paths resolve against the folder holding `config.yaml`.

```yaml
originals_root: data/originals      # read-only; later an external HDD mount
derivatives_root: data/derivatives  # derivative JPEGs and 256px thumbnails
db_path: data/imgsearch.db
models: {embedder: ..., captioner: ..., rewriter: ...}   # names, plus dim/device/url
stages: {ingest: "1", embed: "1", ocr: "1", caption: "1"}  # bump a version to re-run that stage
search: {top_k: 5, per_retriever_k: 50, rrf_k: 60}
```

Unknown keys are an error. Use another config file with `uv run imgsearch --config path/to/config.yaml ...`.
`data/` is git-ignored.

## Usage

```sh
uv run imgsearch status                       # image count, per-stage counts by state (M0)
uv run imgsearch ingest                       # register new photos, write derivatives (M1)
uv run imgsearch search-vec "a plate of food" # vector-only search (M2)
uv run imgsearch search "goa wali pizza"      # hybrid search, top 5 (M5; rewrite from M6)
```

`status` creates the database on first run. `status` and `ingest` exist so far; the others
are *(planned)*. On a fresh DB it prints zeros for every configured stage; `not run` counts
images with no row for that stage yet; `outdated` counts `done` rows from a stage version
other than the one in `config.yaml` (they re-run on the next run of that stage).

Each task adds a section here explaining how to run its part.

### Ingest

Put photos (`.jpg .jpeg .png .heic .heif`, any case, in any subfolder layout) under
`originals_root` (`data/originals` by default), then run:

```sh
uv run imgsearch ingest
```

It runs two steps:

1. **Register.** Scans `originals_root` recursively (hidden files such as `.DS_Store` and
   `._*` are skipped), hashes every file with SHA-256 and adds an `images` row for each new
   hash. Files with the same bytes as an already registered image count as duplicates and
   get no row; the first path seen is kept.
2. **Ingest stage.** For each registered image not yet ingested, opens the original
   read-only, applies EXIF orientation, and fills in `width`/`height` (after orientation),
   `taken_at` (EXIF DateTimeOriginal, else DateTime, as local time
   `YYYY-MM-DDTHH:MM:SS`; any UTC offset is dropped), `lat`/`lon` (EXIF GPS; missing,
   malformed, out-of-range or exactly (0, 0) GPS is stored as NULL) and `phash`. Transparent
   images (PNG screenshots, logos) are put on a white background.

What it creates, under `derivatives_root` (`data/derivatives` by default):

| File | What |
| --- | --- |
| `derivatives/<id[:2]>/<id>.jpg` | JPEG (quality 85), downscaled only above 2048×1536 pixels in area, keeping the aspect ratio; never upscaled. Panoramas and long screenshots stay readable. Models read this |
| `thumbs/<id[:2]>/<id>.jpg` | JPEG, long edge 256px |

`<id>` is the image's SHA-256. The DB stores these paths relative to `derivatives_root`, so
the full path is `config.derivatives_root / image.derivative_path`. Originals are never
written.

It prints counts for both steps: `new`, `already registered`, `duplicates` and `unreadable`
files, then `processed (done, failed)`. Running it again prints `0 new` and `0 processed`.
A file that can't be read, or an image that fails to decode, is listed with its error and
the command exits with status 1; the others still finish. Failed images are retried on the
next run. Bump `stages.ingest` in `config.yaml` to rebuild every derivative.

Each run re-hashes every file under `originals_root`. That takes well under a second for
the 100MB test set; for the full library on an HDD it means reading every byte each run, so
the P2 bulk index should skip files whose path, size and mtime are unchanged.

### OCR and text search (M3)

```sh
uv run imgsearch run ocr                  # OCR every ingested image that needs it
uv run imgsearch search-text "dubai bazaar" [--k 5]
```

`imgsearch run <stage>` is the generic way to run one stage; `run ocr` is the only stage it
knows so far (`STAGES` in `cli.py`; M2 and M4 add theirs). It prints
`processed (done, failed)`, lists each failure with its error and exits with status 1 if any
image failed. Running it again does nothing until `stages.ocr` changes in `config.yaml`.

The `ocr` stage needs `ingest` to be done. It reads each image's derivative (never the
original) with Apple Vision through `ocrmac`, using `models.ocr` in `config.yaml`
(`recognition_level: accurate`, `languages: ["en-US"]`). The recognised lines are stored
one per line in `ocr.text` and in `text_fts`. An image with no text stores `""` and is `done`.
Known gap: ocrmac returns no lines, rather than raising, when the Vision request itself
fails, so such an image is also stored as `""`.

`search-text` searches all of `text_fts` (OCR text now, captions once M4 runs), ranked by
bm25, one result per image. The query is used as given (no rewrite). It is split into words
(letters, digits and combining marks, so Devanagari works); each word is quoted, so
punctuation and FTS operators such as `AND`, `NEAR`, `-` or `*` are plain words. Common
English stopwords and photo words (`photo`, `picture`, `image`, `pic`, ...) are dropped, and
the rest are joined with `OR`. Matching is on whole words, with no stemming: `pizza` does not
match `pizzas`. Each result line shows rank, score (`-bm25`, higher is better) and the
original's path, then the matching text with the matched words in `[ ]`.

### Embeddings and vector search (M2)

```sh
uv run imgsearch run embed                       # embed every ingested image that needs it
uv run imgsearch search-vec "a plate of food" [--k 5]
```

The `embed` stage needs `ingest` to be done. It reads each image's derivative (never the
original) and embeds it with SigLIP 2 (`models.embedder` in `config.yaml`: `name`, `dim`,
`device: mps`, `text_max_length: 64`) through transformers. The fixed-resolution checkpoint
resizes every image to 384×384 without cropping, so panoramas are squashed. Each vector is
L2-normalized float32 and stored in `embeddings` keyed by `(image_id, model)`, so a different
model name adds new rows and leaves the old ones alone. Re-running does nothing until
`stages.embed` changes; a re-run replaces the vectors. The model is loaded once per run and
freed at the end (including the MPS cache).

`search-vec` embeds the query with the text tower (lowercased, padded/truncated to 64 tokens,
as SigLIP 2 was trained) and ranks the stored vectors for the configured model by cosine
distance (sqlite-vec `vec_distance_cosine`). Each line shows rank, score (cosine similarity,
higher is better) and the original's path. The query is used as given (no rewrite). SigLIP's
raw similarities are small (about 0.0–0.2 for a good match); only the order matters. The
command takes about 5 s, mostly loading torch and the model.

## Evaluation *(planned, M7)*

`eval/queries.yaml` holds 30–40 labelled queries (`{query, language, expected_image_ids}`)
over a hand-picked set of 100 images. The eval reports recall@5 overall, by language and
by query type, plus ablations (vector only, text only, hybrid, with and without the rewrite).
It also writes an HTML contact sheet per run. Per-stage speed and memory benchmarks
are kept in `docs/benchmarks.md`.

## Development

```sh
uv run pytest                                    # tests
uv run ruff format . && uv run ruff check --fix .
```

- Package code goes under `imgsearch/`; `config.yaml`, `tests/`, `eval/` and `docs/` sit at the root.

  | Module | What it holds |
  | --- | --- |
  | `imgsearch/config.py` | `load_config()` and the pydantic `Config` model |
  | `imgsearch/interfaces.py` | All protocols (`Stage`, `Embedder`, ...) and shared dataclasses |
  | `imgsearch/store/db.py` | `connect(config)`: opens the DB, loads sqlite-vec, applies migrations |
  | `imgsearch/store/migrations/` | Numbered `.sql` files, tracked with `PRAGMA user_version`. Add a new file; never edit an applied one |
  | `imgsearch/store/text.py` | `save_ocr()` / `save_caption()`: the only writers of OCR text, captions and `text_fts` |
  | `imgsearch/store/status.py` | `set_status()`, `status_counts()` for `stage_status` |
  | `imgsearch/stages/runner.py` | `run_stage(stage, ctx)`: runs one stage over every pending image |
  | `imgsearch/sources/local.py` | `LocalFolderSource`: yields image files under a folder, opens them read-only |
  | `imgsearch/stages/ingest.py` | `register_new()` (hash, add `images` rows) and `IngestStage` (EXIF, pHash, derivative, thumbnail) |

- **Writing a stage.** Implement the `Stage` protocol: `name`, `version` (read it from
  `config.stages`), `requires`, `load(ctx)`, `run(image, ctx)` and `unload()`. Run it with
  `run_stage(stage, Context(config, conn))`. The runner picks images whose row for the stage
  is missing, not `done`, or from another version, and whose `requires` stages are `done`.
  Bumping an upstream stage's version does not re-run downstream stages; bump those too.
  A failing image is recorded as `failed` with the error text, and the others continue.
  `unload()` is always called, even if `load()` failed.
- Tests use a temp config and DB per test (fixtures in `tests/conftest.py`).
- `CLAUDE.md` lists the rules every coding agent follows. `.claude/settings.json` sets up:
  - a hook that blocks writes into `originals_root`
  - a hook that runs ruff on every edited `.py` file
  - a permission allowlist for common commands
- A task is done when tests pass, this README is updated, and nothing has been written
  outside the project except the Hugging Face and Ollama caches.

## Roadmap after the MVP

Bulk indexing of the full ~100GB archive, a WhatsApp bot (official Cloud API), originals on
an external HDD with Syncthing phone sync, face search, near-duplicate and event clustering,
and finally reranking and fine-tuning against the measured baseline. See the plan's
"Extension roadmap" for the order and reasoning.

## Captions (M4)

The `caption` stage describes each derivative with a vision-language model on MLX
(`MlxCaptioner` in `imgsearch/models/captioner.py`, via `mlx-vlm`) and stores the text with
`store.text.save_caption`, so it lands in `captions` and in `text_fts` (kind `caption`).
The text retriever already searches it.

- Settings live in `config.yaml` under `models.captioner`: `name` (the MLX model), `prompt`,
  `max_tokens`, and `max_pixels`. Each derivative is downscaled to at most `max_pixels` in
  area (aspect kept) before captioning, which caps the image at about 1k prompt tokens.
- Decoding is greedy (temperature 0). Only chat-template tokens and a leading "Caption:"
  label are stripped from the output.
- The stage requires `ingest`, loads the model once and frees MLX memory in `unload()`.
- Bump `stages.caption` to re-caption everything (e.g. after changing the prompt or model).
