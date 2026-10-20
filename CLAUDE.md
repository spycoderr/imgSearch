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

- Python 3.12. Package code lives under `imgsearch/` (`sources/ stages/ models/ store/
  search/ cli.py`). `config.yaml`, `tests/`, `eval/` and `docs/` sit at the project root.
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

## Code style: keep it simple

- Prefer plain functions and the standard library. Use classes only where the plan's
  interfaces need them (`imgsearch/interfaces.py`).
- Build only what the current task needs. No speculative options, plugin registries,
  factories or abstraction layers beyond the plan's interfaces.
- Plain `sqlite3` with SQL strings; no ORM. Schema changes go in a new numbered file in
  `imgsearch/store/migrations/`; never edit an applied one.
- No hacks: no `sys.path` edits, monkeypatching, broad `except Exception: pass`,
  sleeps/retries to hide problems, skipped tests, or lint suppressions without a one-line reason.
- Errors fail loudly. Catch exceptions only where the plan requires it (the stage runner
  records failures; the query rewriter falls back).
- If something only works with a workaround, stop and report it instead of hacking around it.
- Small modules, clear names, type hints. Comments only where the reason isn't obvious.

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
5. Checked against `docs/acceptance/<task>.md`, if one exists.
6. Report written to `docs/reports/<task>.md` (e.g. `M2.md`) and committed with the work;
   hand-back `docs/reports/<task>-handback.md` written and committed last.

## Task reports (`docs/reports/`)

- **Before starting:** read every existing report in `docs/reports/` and every supervisor
  review in `docs/reviews/`. Earlier tasks record flags for later ones there (e.g. panoramas,
  file paths, disk size). Open review items addressed to your task are part of your scope.
- **When finishing:** write `docs/reports/<task>.md` with these sections: Summary, Real-run
  results, Benchmark (model tasks), Changes from the plan, Flags (including "For <later
  tasks>"), Acceptance checklist status, Files changed. State facts and numbers, not opinions.
- Never edit another task's report. To correct or follow up on one, add a dated note under
  "Follow-ups" in your own report.
- Write key results (benchmark numbers, model comparisons, query checks) into your report as
  soon as you have them, so they survive if the session stops.
- **Hand-back last:** after the final commit, write `docs/reports/<task>-handback.md`: the short
  message you send back to the user, one section per phase, saved as sent. Commit it. Its
  appearance signals the task is complete, so it must come after everything else.
