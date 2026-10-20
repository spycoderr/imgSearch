# imgsearch — personal image search

Local, single-user photo search on a Mac mini M4 (16GB). Photos are indexed into one SQLite
file (sqlite-vec for vectors, FTS5 for OCR text and captions). Queries in English, Hindi or
Hinglish go through a small-LLM rewrite to English, then hybrid vector + full-text retrieval
merged with RRF, returning the top 5. The MVP (tasks M0–M7) indexes 100 images and queries
from a CLI.

**Source of truth: `Personal Image Search Design and MVP Plan.md`.** Read its Decision log,
Interfaces, and "MVP tasks for agents" sections before starting any task.

## Commands

```sh
uv sync                     # install/refresh the env from uv.lock
uv run pytest               # run tests
uv run imgsearch ...        # the CLI (e.g. `uv run imgsearch status`)
uv add <pkg>                # add a dependency (`uv add --dev <pkg>` for dev tools)
uv run ruff format . && uv run ruff check --fix .
```

Never use `pip install`. Never run in Docker.

## Hard rules

- Python 3.12. Code lives under `imgsearch/` using the plan's layout
  (`sources/ stages/ models/ store/ search/ cli.py`, `eval/`, `tests/`).
- **Originals are read-only.** No task writes, moves, renames or chmods anything under
  `originals_root`. A hook (`.claude/hooks/protect_originals.py`) blocks this; don't work around it.
- Stages are idempotent and record `stage_status`. Re-running does nothing unless the stage
  `version` changes.
- No absolute paths or model names in code. Everything comes from `config.yaml`.
- Don't change an interface (plan, "Interfaces and extension seams") without recording the
  change and the reason in the plan doc.
- Each model task adds a benchmark (seconds per image, peak memory) to `docs/benchmarks.md`.
- Image identity is the SHA-256 of the original bytes; the DB stores only paths relative to
  `originals_root`.

## Mac-specific notes

- torch uses device `mps`. The captioning VLM runs on MLX (`mlx-vlm`). OCR uses Apple Vision
  via `ocrmac`. The query rewriter uses Ollama (it runs as a brew service).
- 16GB RAM: load only one heavy model at a time and unload it when its stage finishes.
- Models are already in the Hugging Face cache / Ollama. Names come from `config.yaml`.

## Tooling

- `.claude/settings.json` holds the permission allowlist and hooks. After a Write or Edit,
  a hook runs `ruff format` and `ruff check --fix` on `.py` files. Settings are in pyproject.toml
  (line length 100, rules E, F, I, UP, B).
- M2, M3 and M4 run in parallel. Keep changes inside your task's modules, and use
  `git worktree` if you need isolation.

## Definition of done (every task)

1. `uv run pytest` passes, including tests for your task's "Done when" criteria.
2. README updated with how to run your part.
3. Benchmark added to `docs/benchmarks.md` (model tasks).
4. Nothing written outside the project except the HF and Ollama caches; nothing
   written under `originals_root`.
