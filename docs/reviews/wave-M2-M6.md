# Parallel wave rules: M2, M3, M4, M6

2026-10-20 · Supervisor. Applies to the four agents building M2 (embeddings), M3 (OCR), M4 (captions)
and M6 (query rewriter) at the same time. M5 (hybrid search) and M7 (eval) come after this wave.

## Why two phases

Building and unit-testing can run in parallel. Real runs can't: the four models share 16 GB of
RAM, all real runs write the same `data/imgsearch.db`, and benchmarks run alongside another model
measure the wrong peak memory. So every task has:

- **Phase A, in parallel:** code, unit tests and a small smoke check, in your own git worktree.
  Then stop and report "Phase A done" with a short summary.
- **Phase B, one task at a time, when the user says go:** rebase on `main`, merge into `main`,
  real run on the 130 photos, benchmark, report, commit. The order is **M3, M2, M6, M4**: shortest
  first, and the long caption run last.

## Phase A rules

- Create your worktree inside the project: `git worktree add .worktrees/<task> -b <task>`
  (e.g. `.worktrees/m2`), and work only there. `.worktrees/` is git-ignored.
- `data/` isn't in your worktree. Don't create one, and don't run any stage against the real DB in
  phase A. Unit tests use `tmp_path`.
- Smoke check: you may read up to 3 files from the main checkout's `data/derivatives/` (read-only)
  to confirm your model loads and produces sane output. Keep it short; free the model afterwards.
- Unit tests must not load real models or call Ollama. Use small fake classes that satisfy the
  Protocol (or `httpx.MockTransport` for M6). No monkeypatching, no skipped tests. The real
  model is exercised by the phase B real run, which the report documents.

## Ownership (to keep merges trivial)

| Shared file | Rule |
| --- | --- |
| `config.yaml` and `imgsearch/config.py` | Add keys only inside your own section: M2 `models.embedder`, M3 a new `models.ocr`, M4 `models.captioner`, M6 `models.rewriter`. Don't reorder or reformat other sections |
| `imgsearch/cli.py` | Append only your own commands at the end of the file: M2 `search-vec`; M3 `search-text` and the generic `run` (below); M6 `rewrite`; M4 none |
| `README.md`, `docs/benchmarks.md`, plan doc "Changes during build" | Append your own section at the end; don't edit others' text |
| `imgsearch/search/` | M2 owns `vector.py`, M3 owns `text.py`. `TextRetriever` searches all of `text_fts` (OCR and captions), so M4 doesn't touch retrieval |
| Schema | No migrations are expected: M0 created every MVP table. If you think you need one, stop and report instead |

**Running stages:** M2, M3 and M4 each need a way to run their stage. Exactly one generic command,
`imgsearch run <stage>`, is added **by M3** (merged first), mapping stage names to their classes in
one small dict in `cli.py`. M2 and M4 add their entry to that dict when they rebase in phase B.
In phase A, M2 and M4 test their stage by calling `run_stage` directly in tests.

## Phase B checklist (each task, in turn)

1. In your worktree: `git rebase main`, resolve conflicts
   by keeping both sides' additions, run `uv run pytest` and `uv run ruff check .`.
2. Merge into `main` from the main checkout: `git -C "<project root>" merge --ff-only <task>`.
3. From the main checkout, run your stage on the real photos, then `imgsearch status`.
4. Benchmark, report `docs/reports/<task>.md` and acceptance check `docs/acceptance/<task>.md`.
   Commit on `main`.
5. Remove your worktree: `git worktree remove .worktrees/<task>` and delete the branch.
