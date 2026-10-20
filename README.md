# imgsearch

Local photo search for a personal library. Ask in English, Hindi (Devanagari) or Hinglish
(for example "mummy ke saath Goa wali pizza ki photo") and get the 5 best-matching photos.
Everything runs on one Mac mini M4 (16GB). No cloud model calls are made.

The full design, the decisions behind it and the task breakdown are in
[`Personal Image Search Design and MVP Plan.md`](Personal%20Image%20Search%20Design%20and%20MVP%20Plan.md).
That doc is the source of truth. This README covers how to set up and run the project.

## Status

**M0 done.** The package skeleton, config, database schema, stage runner and
`imgsearch status` work. Nothing is indexed yet. Commands marked *(planned)* arrive with the
task named next to them.

| Task | What it adds | Status |
| --- | --- | --- |
| M0 | Scaffold, `config.yaml`, SQLite schema, stage framework, `imgsearch status` | done |
| M1 | Local folder source and `ingest` stage (hash, EXIF, derivative, thumbnail) | not started |
| M2 | `embed` stage (SigLIP 2) and vector retriever | not started |
| M3 | `ocr` stage (Apple Vision) and full-text search | not started |
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
- **Originals are never modified.** Models work from a 2048px JPEG derivative and a 256px
  thumbnail stored on the Mac, so search keeps working when the originals' drive is unplugged.
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
derivatives_root: data/derivatives  # 2048px JPEGs and 256px thumbnails
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
uv run imgsearch search-vec "a plate of food" # vector-only search (M2)
uv run imgsearch search "goa wali pizza"      # hybrid search, top 5 (M5; rewrite from M6)
```

`status` creates the database on first run. Only `status` exists so far; the others are
*(planned)*. On a fresh DB it prints zeros for every configured stage; `not run` counts
images with no row for that stage yet.

Each task adds a section here explaining how to run its part.

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

- **Writing a stage.** Implement the `Stage` protocol: `name`, `version` (read it from
  `config.stages`), `requires`, `load(ctx)`, `run(image, ctx)` and `unload()`. Run it with
  `run_stage(stage, Context(config, conn))`. The runner picks images whose row for the stage
  is missing, not `done`, or from another version, and whose `requires` stages are `done`.
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
