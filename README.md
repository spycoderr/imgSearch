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
| M4 | `caption` stage (Qwen-VL via MLX) | done |
| M5 | Hybrid search with RRF fusion, `imgsearch search` | done |
| M6 | Query rewriter for Hinglish/Hindi (Ollama) | done (wired into search by M5) |
| M7 | Eval harness: recall@5 report and HTML contact sheet | done (baseline in `docs/eval/baseline.md`) |

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
uv run imgsearch search "goa wali pizza"      # hybrid search, top 5 (M5; rewrite from M6)
```

`status` creates the database on first run. On a fresh DB it prints zeros for every configured stage; `not run` counts
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
uv run imgsearch search "dubai bazaar" --signals text --no-rewrite   # text search only
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

Text search (`--signals text`; `search-text` until M5) searches all of `text_fts` (OCR text now, captions once M4 runs), ranked by
bm25, one result per image. The query is used as given (no rewrite). It is split into words
(letters, digits and combining marks, so Devanagari works); each word is quoted, so
punctuation and FTS operators such as `AND`, `NEAR`, `-` or `*` are plain words. Common
English stopwords and photo words (`photo`, `picture`, `image`, `pic`, ...) are dropped, and
the rest are joined with `OR`. Matching is on whole words, with no stemming: `pizza` does not
match `pizzas`. Its score is `-bm25` (higher is better); `imgsearch search` prints its rank under
`text #N` (see "Search (M5)").

### Embeddings and vector search (M2)

```sh
uv run imgsearch run embed                       # embed every ingested image that needs it
uv run imgsearch search "a plate of food" --signals vector --no-rewrite   # vector search only
```

The `embed` stage needs `ingest` to be done. It reads each image's derivative (never the
original) and embeds it with SigLIP 2 (`models.embedder` in `config.yaml`: `name`, `dim`,
`device: mps`, `text_max_length: 64`) through transformers. The fixed-resolution checkpoint
resizes every image to 384×384 without cropping, so panoramas are squashed. Each vector is
L2-normalized float32 and stored in `embeddings` keyed by `(image_id, model)`, so a different
model name adds new rows and leaves the old ones alone. Re-running does nothing until
`stages.embed` changes; a re-run replaces the vectors. The model is loaded once per run and
freed at the end (including the MPS cache).

Vector search (`--signals vector`; `search-vec` until M5) embeds the query with the text tower (lowercased, padded/truncated to 64 tokens,
as SigLIP 2 was trained) and ranks the stored vectors for the configured model by cosine
distance (sqlite-vec `vec_distance_cosine`). Its score is cosine similarity (higher is better). SigLIP's
raw similarities are small (about 0.0–0.2 for a good match); only the order matters. The
command takes about 5 s, mostly loading torch and the model.

## Evaluation (M7)

```sh
uv run imgsearch eval-review                    # check eval/queries.yaml, write a review sheet
uv run imgsearch eval                           # run every configuration, write a run folder
uv run imgsearch eval-compare eval/runs/A eval/runs/B   # two runs' metrics side by side
```

- **The query set** is `eval/queries.yaml`: 41 queries confirmed by the user, written from
  looking at the photos. Each query is `{id, query, language, type, expected_image_ids,
  note}`. `language` is english, hinglish or hindi. `type` is scene, object, text, place
  or hard. Expected photos are image ids (SHA-256); a `# files:` comment lists the file
  names for people.
  - The loader rejects unknown ids, languages, types or fields, empty lists, and duplicate
    query ids.
  - `eval-review` writes `eval/runs/query_review.html`: each query with its expected
    thumbnails.
- **`imgsearch eval`** loads the models once and runs 7 configurations: vector, text and
  hybrid, each with and without the rewrite, plus hybrid with the rewrite but without
  captions (text search over OCR only).
  - Each query is rewritten once per run, and every rewrite-on configuration uses that
    rewrite.
  - It takes about 90 s for 41 queries.
- **Metrics,** per query, averaged overall, by language and by type:
  - hit@5: any expected photo in the top 5.
  - recall@5: expected photos found ÷ min(5, number expected).
  - MRR: 1 / rank of the first expected photo within the top 5.
- **Each run writes `eval/runs/<YYYYmmdd-HHMMSS>/`:** `results.json` (every query ×
  configuration, with the top 5 and per-signal ranks), `metrics.json`, `meta.json` (git
  commit and dirty flag, query-file hash, models, Ollama version, timings), a copy of
  `config.yaml`, and `contact_sheet.html`.
  - The contact sheet is one offline HTML file. It starts with the summary tables, then
    shows each query with its rewrite, the hybrid+rw top 5 (hits outlined in green, with
    fused score and per-signal ranks), and the expected photos (misses in red). The other
    configurations are folded underneath.
- **Privacy:** `eval/runs/` holds personal photos and is git-ignored. Never commit or share
  its contents. The committed numbers are in `docs/eval/baseline.md`.
- **Results:** the MVP baseline is in `docs/eval/baseline.md`, and what it means is in
  `docs/mvp-summary.md`. On this set vector+rw is best (recall@5 0.98, against 0.87 for
  hybrid+rw).
- Re-run `imgsearch eval` after any model, prompt, config or Ollama change. Compare with
  `eval-compare`. Don't run it during a caption run (memory, see "Search (M5)").

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

```sh
uv run imgsearch run caption              # caption every ingested image that needs it
uv run imgsearch search "kit kat"         # captions are searched together with OCR text
```

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
- It takes about 6 s per image and about 4 GB of MLX memory on the Mac mini M4 (see
  `docs/benchmarks.md`), so it runs last and alone. Captions sometimes end with filler such
  as "No readable text is present."; the M4 report gives the rate.

## Query rewrite (M6)

```sh
uv run imgsearch rewrite "mummy ke saath Goa wali pizza ki photo"
```

`OllamaRewriter` (`imgsearch/models/rewriter.py`) turns an English, Hindi or Hinglish query
into a `ParsedQuery`: `english`, `original`, `people`, `date_range` and `place`. `rewrite`
prints it as JSON, followed by the time taken. `imgsearch search` calls it first (M5).

- It needs Ollama 0.40.6 or later (`brew services start ollama`) and the model pulled:
  `ollama pull qwen2.5:7b`.
- Settings live in `config.yaml` under `models.rewriter`: `name` (the Ollama model), `url`,
  `timeout_s` and `prompt` (the system prompt, with four few-shot examples).
- It calls Ollama's `/api/chat` with `stream: false` and temperature 0, and passes the output
  model's JSON schema as `format`, so the reply is constrained to that schema. The reply is
  validated with pydantic. `original` is always the exact input, set by code.
- Any failure (Ollama down, timeout, HTTP error, invalid JSON, schema mismatch, empty
  `english`) logs a warning and returns the query unchanged as both `english` and `original`.
- Ollama keeps the model loaded for its default 5 minutes after the last call (about 4.6 GB).
  A cold call takes about 5–9 s, a warm one about 1.7 s (see `docs/benchmarks.md`).
- `eval/rewrite_examples.yaml` holds 21 example queries (11 Hinglish, 5 Devanagari,
  5 English; M5 added "kal wali shaadi ki photos") with hand-written expected rewrites; `docs/reports/M6.md` shows the outputs.
- `date_range` is kept only if the query has a digit-based year or date (`2019`, `15/08`,
  `२०१९`); a date the model infers from "kal" or "last Diwali" is dropped (M5, review W1).
  The prompt's `{today}` placeholder is filled with today's date.

## Search (M5)

```sh
uv run imgsearch search "mummy ke saath Goa wali pizza ki photo"
uv run imgsearch search "dubai bazaar" --k 10
uv run imgsearch search "a white dog" --no-rewrite            # skip the rewrite LLM
uv run imgsearch search "dubai bazaar" --signals text          # one retriever only
```

The query is rewritten to English (M6), then the vector retriever searches with `english`
and the full-text retriever with `english` and the original text. Each returns up to
`search.per_retriever_k` (50) images. The two rankings are merged with Reciprocal Rank
Fusion: score = Σ 1 / (`search.rrf_k` + rank), with `rrf_k` = 60. Ties go to the better
single rank, then the image id. The top `--k` (default `search.top_k`, 5) are printed:

```
searched: white dog sitting on the carpet  (rewritten from: safed kutta carpet pe baitha hua)
signals: vector, text
 1  0.0320  IMG_20180121_130605.jpg  [text #4, vector #1]
    A fluffy white dog wearing a red patterned vest lies on a red and beige ornate rug, looking up at …
...
time: rewrite 1.78s, retrieval 0.11s, total 1.89s
model load: 5.88s
```

Each result shows the rank, the fused score, the original's path (relative to
`originals_root`), and each retriever that found it with its rank there. Then the first
~100 characters of the caption. If Ollama is down or the reply is invalid, the query is
searched as given, and the first line says `rewrite skipped: <reason>`.

- `--no-rewrite` searches the query as given. `--signals vector` or `--signals text` uses
  one retriever. `--signals text --no-rewrite` loads no model and needs no Ollama
  (about 0.4 s).
- A full search loads SigLIP (about 6 s per command), then takes about 1.7 s warm, almost
  all of it the rewrite. The first query after 5 idle minutes waits 5–9 s for Ollama to
  load the model.
- In code, build once and reuse: `searcher = open_searcher(config, conn)`, then
  `searcher.search(query, k=None, rewrite=True, signals=None)` returns a `SearchResponse`.
  That holds the `ParsedQuery` searched, the `Result`s and the timings
  (`imgsearch/search/hybrid.py`).
- **Memory: don't search while a caption run is going.** Search holds about 2.2 GB
  (SigLIP) plus 4.6 GB in Ollama, and `imgsearch run caption` needs 4–6 GB. Together
  that is too much for 16 GB.
