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
| P1.5-A | Pen-drive storage layout, `imgsearch import` (inbox → originals), `update`, `verify` | done |
| P1.6 | Originals encrypted in S3 Glacier Instant Retrieval; pen drive as an LRU cache: `upload`, `evict`, `fetch` | done |
| P1.7 | File names and phone folders as search text, date filtering, weighted RRF, `backfill` | done (eval in `docs/eval/baseline-p1.7.md`) |

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
  | Query rewrite | `qwen2.5:7b` | Ollama |
  | OCR | Apple Vision (built into macOS) | none to download |

## Setup

```sh
uv sync                          # create .venv from uv.lock
uv run hf download google/siglip2-base-patch16-384
uv run hf download mlx-community/Qwen3-VL-4B-Instruct-4bit
ollama pull qwen2.5:7b
```

Then tell the project where the external drive's storage folder is:

```sh
cp .env.example .env    # then edit IMGSEARCH_STORAGE_ROOT, IMGSEARCH_S3_BUCKET, AWS_PROFILE
```

For the S3 copy of the originals, see "Originals in S3" below (one-time key setup).

Add dependencies with `uv add <pkg>` (or `uv add --dev <pkg>`), never `pip install`.

## Configuration

All paths, model names and stage versions live in `config.yaml` at the project root. Code
contains none of them. Relative paths resolve against the folder holding `config.yaml`.

```yaml
originals_root: "${IMGSEARCH_STORAGE_ROOT}/originals"      # pen drive
derivatives_root: "${IMGSEARCH_STORAGE_ROOT}/derivatives"  # pen drive
thumbs_root: data/thumbs       # Mac
db_path: data/imgsearch.db     # Mac; a path under /Volumes is rejected
inboxes: [data/inbox/phone, data/inbox/camera]   # Mac; Syncthing writes here
models: {embedder: ..., captioner: ..., rewriter: ..., ocr: ...}
stages: {ingest: "2", embed: "2", ocr: "1", caption: "1"}  # bump a version to re-run that stage
search: {top_k: 5, per_retriever_k: 10, rrf_k: 60, default_signals: [vector]}
s3: {bucket: "${IMGSEARCH_S3_BUCKET}", profile: "${AWS_PROFILE}", region: ap-south-1,
     storage_class: GLACIER_IR, originals_prefix: originals/, db_key: db/imgsearch.sqlite.enc, ...}
storage: {cache_max_gb: 20}   # pen-drive cap for originals; `imgsearch evict` enforces it
```

**Machine-specific paths live in `.env`**, not in `config.yaml`. `.env` is git-ignored;
`.env.example` shows its settings:

```sh
IMGSEARCH_STORAGE_ROOT=/Volumes/<drive name>/imgsearch
IMGSEARCH_S3_BUCKET=<bucket name>
AWS_PROFILE=<profile in ~/.aws/>
```

`${VAR}` in a path in `config.yaml` is filled from the environment first, then from `.env`.
An unset variable stops every command with `IMGSEARCH_STORAGE_ROOT is not set`. **To move
the storage (another drive, a renamed volume), edit `.env` only.** The originals-protection
hook reads the same `.env`.

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

- **The query set** is `eval/queries.yaml`: 53 queries confirmed by the user, written from
  looking at the photos (the 41 MVP queries plus 12 `u2-*` queries on phone photos). Each query is `{id, query, language, type, expected_image_ids,
  note}`. `language` is english, hinglish or hindi. `type` is scene, object, text, place
  or hard. Expected photos are image ids (SHA-256); a `# files:` comment lists the file
  names for people.
  - The loader rejects unknown ids, languages, types or fields, empty lists, and duplicate
    query ids.
  - `eval-review` writes `eval/runs/query_review.html`: each query with its expected
    thumbnails.
- **`imgsearch eval`** loads the models once and runs 7 configurations: vector, text and
  hybrid, each with and without the rewrite, plus hybrid with the rewrite but without
  captions (text search over OCR and file names).
  - `imgsearch eval --no-date-filter` runs them with the date filter off, to measure it.
    `results.json` records each query's parsed `date_range` and whether the filter was used
    or fell back; `meta.json` records the filter setting, `signal_weights` and
    `per_retriever_k`.
  - Each query is rewritten once per run, and every rewrite-on configuration uses that
    rewrite.
  - It takes about 95 s for 53 queries.
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
  `docs/mvp-summary.md`. On the MVP set vector+rw was best (recall@5 0.98, against 0.87 for
  hybrid+rw). The P1.7 before/after on 53 queries is in `docs/eval/baseline-p1.7.md`:
  vector+rw 0.79 (old 41: 0.88, new 12: 0.46), hybrid+rw 0.72.
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
- **Dates (P1.7).** The prompt's `{today}` placeholder is filled with today's weekday and
  date and the seven days before it, yesterday marked "(yesterday, kal)", so the model can
  resolve "15-16 sept", "kal", "aaj", "pichle hafte" or "pichle mahine". A month or day without
  a year means the most recent one.
  - **Weekdays are worked out in code**, not by the model (it got them wrong): a query naming
    one weekday ("on saturday", "shanivar", "मंगलवार") gets the most recent such day before
    today. The words are in `models.rewriter.weekdays`.
  - **Date guard (review W1, widened):** `date_range` is kept only if the query names a date:
    a digit year or day/month (`2019`, `15/08`, `२०१९`), or a word from
    `models.rewriter.date_words` (months, "yesterday", "kal", "aaj", "pichle", "last week",
    Hindi forms…) or a weekday word, matched as whole words. A date the model infers from
    "last Diwali" is still dropped.

## Search (M5)

```sh
uv run imgsearch search "mummy ke saath Goa wali pizza ki photo"   # default: vector + rewrite
uv run imgsearch search "Jaipur ka kila" --signals vector,text      # hybrid
uv run imgsearch search "dubai bazaar" --k 10
uv run imgsearch search "a white dog" --no-rewrite            # skip the rewrite LLM
uv run imgsearch search "dubai bazaar" --signals text          # full-text only
```

The query is rewritten to English (M6). Then each chosen retriever searches:
- The vector retriever searches with `english`.
- The full-text retriever searches with `english` and the original text.

**By default only the vector retriever runs.** That is `search.default_signals: [vector]`
in `config.yaml`, used by every caller that names no signals. It was chosen because vector +
rewrite beat hybrid on the M7 eval (recall@5 0.98 against 0.87–0.93; see
`docs/mvp-summary.md`). `--signals vector,text` runs hybrid. That still helps when a caption
or OCR text names something the image alone doesn't show, e.g. "Jaipur ka kila" ranks the
Amber Fort panorama 1st in hybrid and 6th with vector alone.

Each retriever returns up to `search.per_retriever_k` (10) images. The rankings are merged
with weighted Reciprocal Rank Fusion: score = Σ weight / (`search.rrf_k` + rank), with
`rrf_k` = 60 and each signal's weight from `search.signal_weights` (both 1.0, i.e. plain RRF;
P1.7 tried text at 0.5 and 0.25, see `docs/eval/baseline-p1.7.md`). With one retriever, that
keeps its order. Ties go to the better single rank, then the image id.
The top `--k` (default `search.top_k`, 5) are printed. An example in hybrid mode:

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

- **Dates (P1.7).** When the rewrite finds a date, every retriever keeps only photos from
  those days (inclusive). A photo's date is its EXIF `taken_at`, else its file date
  (`file_mtime`). A line under `signals:` says `dates: 2026-09-15 to 2026-09-16`. If no photo
  matches, search shows results from all dates and says `dates: no photos from … ; showing
  results from all dates`. `--no-date-filter` turns the filter off.
- **File names (P1.7).** Full-text search also matches words from each photo's file name and
  phone folder ("Screenshots", "Adobe Scan Exports", "Signature Nilabh black_1"), so
  `--signals vector,text` finds documents by name. See "File names, dates and backfills".
- `--no-rewrite` searches the query as given. `--signals text` uses full-text search only;
  `--signals vector,text` uses both. `--signals text --no-rewrite` loads no model and needs no Ollama
  (about 0.4 s).
- A full search loads SigLIP (about 6 s per command), then takes about 1.7 s warm, almost
  all of it the rewrite. The first query after 5 idle minutes waits 5–9 s for Ollama to
  load the model.
- In code, build once and reuse: `searcher = open_searcher(config, conn)` (the config's
  default signals, or pass `signals=[...]`), then
  `searcher.search(query, k=None, rewrite=True, signals=None)` returns a `SearchResponse`.
  That holds the `ParsedQuery` searched, the `Result`s and the timings
  (`imgsearch/search/hybrid.py`).
- **Memory: don't search while a caption run is going.** Search holds about 2.2 GB
  (SigLIP) plus 4.6 GB in Ollama, and `imgsearch run caption` needs 4–6 GB. Together
  that is too much for 16 GB.

## Storage on the pen drive, import and update (P1.5-A)

The layout follows plan D18:

| What | Where | Config key |
| --- | --- | --- |
| Originals | pen drive, `$IMGSEARCH_STORAGE_ROOT/originals/YYYY/MM/<name>` | `originals_root` |
| Derivatives (2048px JPEGs the models read) | pen drive, `$IMGSEARCH_STORAGE_ROOT/derivatives/<id[:2]>/<id>.jpg` | `derivatives_root` |
| Thumbnails (256px) | Mac, `data/thumbs/<id[:2]>/<id>.jpg` | `thumbs_root` |
| SQLite DB | Mac, `data/imgsearch.db` | `db_path` |
| Inboxes (Syncthing, receive-only) | Mac, `data/inbox/phone`, `data/inbox/camera` | `inboxes` |

The DB stores `derivative_path` relative to `derivatives_root` and `thumb_path` relative to
`thumbs_root` (migration `002_thumbs_root.sql` moved the older `derivatives/…` and
`thumbs/…` paths over).

**Search works with the pen drive unplugged.** Vectors and text are in the DB and the
thumbnails are on the Mac. `imgsearch search` then adds "pen drive not mounted: full-size
images are unavailable". `imgsearch status` shows whether the drive is mounted.

**Commands that need the drive check it first.** `import`, `update`, `ingest`, `verify` and
`run <stage>` stop with `pen drive not mounted: /Volumes/<name> (...)` and exit 1 if the
volume isn't mounted. **Always eject the drive before unplugging it** (Finder, or
`diskutil eject "/Volumes/<name>"`).

```sh
uv run imgsearch import    # inbox -> originals_root only
uv run imgsearch update    # import, then ingest, embed, ocr, filename (no captions)
uv run imgsearch verify    # check every image's files
```

**`import`** goes through every image file in each inbox (same rules as ingest: image
suffixes, hidden files and Syncthing's `.syncthing.*.tmp` skipped):

1. Files changed in the last 60 s are skipped (`skipped (too new)`); the next run takes them.
2. The file is hashed. If the hash is already registered, it's a duplicate: counted and
   deleted from the inbox (only if the registered original is in place; otherwise it's a
   failure and the file stays).
3. New photos are copied to `originals_root/YYYY/MM/<original name>`, dated by EXIF
   DateTimeOriginal, else DateTime, else the file's mtime (Syncthing keeps the phone's).
   A name taken by a different photo gets `-<first 8 hex of the hash>`. Nothing is ever
   overwritten.
4. The copy is written to a hidden temp name in the target folder, fsynced, re-hashed and
   compared, then renamed. Only then is the `images` row inserted, and only after that
   commit is the inbox file deleted.

A failure on one file is listed with its path and doesn't stop the others; the inbox file
is kept, and the command exits 1. Running it again with nothing new does nothing. `import`
is the only code that adds files under `originals_root`.

**`update`** runs `import`, then the `ingest`, `embed`, `ocr` and `filename` stages through the runner,
and prints each step's counts. Captions are a separate, slow background job:
`uv run imgsearch run caption`.

**`verify`** checks every DB row: the original's SHA-256 must equal the image id, and the
derivative and thumbnail must exist and decode. It prints `verify: N of M images ok` and
lists each problem.

**Syncthing.** The Mac folders are receive-only, so deleting an imported photo from the
inbox never touches the phone. Syncthing then lists those files as "locally changed".
**Don't press "Revert Local Changes"**: that downloads them all again.

**Disk use on exFAT.** The pen drive uses 128 KiB clusters, and macOS writes a hidden
`._<name>` file (one cluster) next to every file it creates there. Expect each original or
derivative to cost its size rounded up, plus about 128 KiB.

## Originals in S3, pen drive as a cache (P1.6)

Plan D19: the originals' primary home is **S3 Glacier Instant Retrieval**, encrypted on the
Mac before upload. The pen drive keeps a least-recently-used cache of them. Search never
reads originals, so evicted photos are still found; `fetch` brings one back in a second or two.

**Setup (already done for this Mac).** A private bucket in `ap-south-1` with versioning,
Block Public Access and a lifecycle rule that expires old versions after 90 days. An IAM user
whose key can only put, get and head objects in that bucket (`s3:PutObject`, `s3:GetObject`
and `s3:GetObjectVersion`; the last is needed because upload, `fetch` and `restore-check` read
an exact version, and without it those requests return 403): it can't delete objects or change
versioning, so the code never deletes from S3, and re-uploading a key adds a new version. The
key is in `~/.aws/` under the profile named by `AWS_PROFILE`; the bucket name is
`IMGSEARCH_S3_BUCKET`. Both are in `.env`, so no committed file names them. Only the commands
that use S3 (`upload`, `evict`, `fetch`, `restore-check`, and the upload steps of `update`)
need them; without them those commands say `S3 not configured` and exit 1, `update` prints
`upload skipped: S3 not configured` and still indexes, and everything else (`status`,
`verify`, `search`, `evict --dry-run`, ...) works as usual.

**The encryption key.** AES-256-GCM, one random 256-bit key, kept in the macOS Keychain
(item `imgsearch-s3`, from `s3.keychain_service`), read through the built-in `security` command.

```sh
uv run imgsearch s3-key create   # once; refuses if a key already exists
uv run imgsearch s3-key export   # run in your own Terminal, then save it in a password manager
```

> **Back up the key.** A lost key means the S3 copies can never be decrypted: the backups
> become useless. After `create`, run `export` yourself, store the 64 hex characters in a
> password manager, and clear the terminal. The key is never written to the repo, `.env`
> or a log. To restore on another Mac, add it back with
> `security add-generic-password -s imgsearch-s3 -a encryption-key -w` (it prompts).

**Commands**

```sh
uv run imgsearch upload               # every original not yet in S3, then an encrypted DB copy
uv run imgsearch update               # import -> upload -> ingest -> embed -> ocr
uv run imgsearch evict --dry-run      # list what would leave the pen drive
uv run imgsearch evict                # remove LRU originals until under storage.cache_max_gb
uv run imgsearch evict --cap-gb 3.5   # ... or under another cap
uv run imgsearch fetch <image id or path under originals_root>   # prints the local path
uv run imgsearch restore-check --sample 10   # download + decrypt + hash-check; writes nothing
uv run imgsearch status               # adds "s3:" and "cache:" lines
```

- **Objects.** An original is stored as `originals/<image id>` (its SHA-256), so S3 never sees
  file names or dates. Each object is `IMGS` + format version + a fresh 12-byte nonce, then the
  AES-GCM ciphertext and tag. The upload sends a SHA-256 of the encrypted bytes, so S3 rejects
  a corrupted upload. A copy counts as verified (`s3_copies.verified_at`) only after S3
  reports the same size and checksum for the exact version written; the version id and the
  encrypted hash are stored.
- **`upload`** skips originals with a verified copy, so a second run uploads nothing. One
  failure is listed and doesn't stop the others. It also uploads `db/imgsearch.sqlite.enc`, a
  copy made with SQLite's online backup API, encrypted the same way, but only when the DB has
  changed since the last copy (bucket versioning keeps the older copies for 90 days). Without
  the DB, the objects can't be mapped back to file paths. Offline, it prints
  `upload skipped: S3 unreachable: ...`, changes nothing and exits 1. A failed DB copy is
  printed as `db copy: FAILED: ...` and exits 1.
- **`update`** uploads new originals right after the import, and the DB copy at the end, so
  the copy includes the run's index. If the upload can't run or fails (offline, no key, S3 not
  configured, an S3 error), indexing still runs, the summary says how many originals are not
  yet in S3, and the next run uploads them. A failed DB copy makes `update` exit 1.
- **`import`** treats a photo already registered as a duplicate even when its original was
  evicted, as long as it has a verified S3 copy: the inbox file is removed and nothing is
  restored.
- **Re-ingesting** (an ingest version bump) fetches each evicted original back first, through
  `fetch`; the next `evict` applies the cap again.
- **`evict`** removes originals from the pen drive, least recently used first (last `fetch`,
  else the time it was registered), until the rest are under the cap. It removes one only if
  it has a verified S3 copy (re-checked with S3 just before) and its ingest is done. It never
  touches the `images` row: search, thumbnails and derivatives are unaffected. Sizes are file
  sizes; exFAT adds about 8% on disk. If S3 becomes unreachable mid-run, it lists what it
  removed, prints `evict: stopped early: ...` and exits 1.
- **`fetch`** returns the local path. If the original was evicted, it downloads the recorded
  version, checks the encrypted hash, decrypts, checks that the SHA-256 equals the image id,
  and writes it back to the same path (temp file, fsync, rename; never over an existing file),
  with its original mtime. A mismatch fails loudly and writes nothing. Offline, it prints
  `S3 unreachable: ...` and exits 1.
- **`verify`** skips the hash check for evicted originals (it still checks their derivative
  and thumbnail) and says how many it skipped; `restore-check` covers the S3 side. It reports
  a missing or undecryptable DB copy as a failure (after the originals' results); offline, it
  prints what it checked, then `restore-check: stopped early: ...`, and exits 1.

## File names, dates and backfills (P1.7)

**File names as text.** The `filename` stage (no model, part of `update` and
`imgsearch run filename`) writes one full-text row per photo of kind `filename`:

- The words come from the photo's inbox path, e.g. `Screenshots/Screenshot_20260327-171258_Gmail.png`
  gives `screenshots screenshot gmail`. If the inbox path isn't known, they come from the
  original's path under `originals_root`.
- The text is split on anything that isn't a letter or digit, at letter/digit boundaries and
  at camelCase (`WhatsApp` gives `whatsapp whats app`). Pure numbers, single characters and
  the camera prefixes `img dsc pxl hdr wa vid portrait` are dropped.
- Camera names (`IMG_20230501_123456.jpg`) give no words; that's expected.
- The text retriever searches these rows with OCR and captions. The eval's "no captions"
  setting keeps them.

**What `import` records** (migration `004_source_and_mtime.sql`): `images.source_relpath`, the
path inside the inbox (keeps the phone folder, which `YYYY/MM/` filing loses), and
`images.file_mtime`, the file's modification time as naive local time.

**Backfills for photos imported before P1.7** (both need the pen drive; run once):

```sh
uv run imgsearch backfill mtime              # file_mtime from the originals on the pen drive
uv run imgsearch backfill folders --dry-run  # match against Syncthing's index, write nothing
uv run imgsearch backfill folders            # record source_relpath and rewrite filename text
```

- `backfill mtime` fills only empty `file_mtime`. Evicted originals are skipped and counted:
  `imgsearch fetch` them first if they matter. Fetch restores the original mtime.
- `backfill folders` asks Syncthing's REST API (`syncthing.url` in `config.yaml`) for each
  inbox folder's global index (`/rest/db/browse`). Each photo is matched by file name and size.
  - Only a unique match is used. Ambiguous ones, ones not in Syncthing, and evicted ones are
    counted and skipped.
  - It sends only GET requests and never changes Syncthing's config or folders.
  - It needs the Mac's Syncthing API key as `SYNCTHING_API_KEY` in `.env`. Copy it from the
    Mac's Syncthing GUI → Actions → Settings → General, not from the phone app, whose key is
    different.

**Dates.** A photo's date for search is `taken_at` (EXIF), else `file_mtime`; the one SQL
expression is in `imgsearch/store/dates.py`. See "Search (M5)" and "Query rewrite (M6)" for
how a query's date becomes a filter.

