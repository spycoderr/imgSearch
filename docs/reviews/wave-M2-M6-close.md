# Wave M2/M3/M4/M6 — close-out review

2026-10-20 · Supervisor. All four tasks are merged on `main` (last commit `c31cc4d`), worktrees and
branches removed, 150 tests and ruff pass. `imgsearch status`: ingest, embed, ocr and caption all
130/130 done.

## Per task

| Task | Verdict | Supervisor check | Key numbers |
| --- | --- | --- | --- |
| M3 OCR + text search | Accepted | "dubai bazaar" ranks the two sign photos 1–2; stopword-only query returns nothing; bad stage name gives a clear error | 0.24 s/img; 69 of 130 have text; handwriting OCR is poor |
| M2 embeddings + vector search | Accepted | S1 verified: `import imgsearch.cli` loads neither torch nor transformers; `status` 0.25 s. "a dog" top hit opened: a dog | 0.06 s/img (~40 min / 40k); "a plate of food" 1 of 3 |
| M4 captions | Accepted | Read sampled captions: specific and mostly accurate (names "Amber Fort in Jaipur"; copies a school sheet's text). Confirmed 2 looping captions and 45 with "no readable text" filler | 5.8 s/img (~64 h / 40k); MLX peak 3.96 GB |
| M6 query rewriter | Accepted | Ollama upgrade 0.17.4 → 0.40.6 was approved by the user before it ran. Spot check below | qwen2.5:7b chosen: 15/4/1 vs 3b 9/6/5 and gemma3:4b 11/8/1; warm ~1.7 s; 4.6 GB resident |

## Open items for M5

| # | Item | Fix |
| --- | --- | --- |
| W1 | The rewriter invents dates for relative words. Supervisor test: "kal wali shaadi ki photos" → "photos from the previous year's wedding", `date_range` 2022. Breaks the plan's "must forbid inventing facts" rule | In `OllamaRewriter`, accept `date_range` only if the query contains a digit-based year or date; otherwise set it to None. Add today's date to the system prompt so "kal", "pichle hafte" and "last Diwali" can be resolved later. Add the query to `eval/rewrite_examples.yaml`. Re-check the 20 examples |
| W2 | जयपुर → "Jodhpur" every run. Captions name real places (the Amber Fort caption says "Jaipur"), so this costs a hit | Don't tune the prompt for one word. Let M7 measure it; if place errors are common, try a transliteration hint in the prompt then |
| W3 | Search needs the embedder (~1 GB) and the rewriter (~4.6 GB) loaded at once; captioning needs ~4–6 GB | M5 doesn't run search during a caption run. Note it in the README |
| W4 | `search-vec` (M2) and `search-text` (M3) each print results their own way (S5) | M5 replaces both printers with one result printer used by `search` |

## Carried to M7

- Captions: filler in 35%, loops in 2 of 130. M7 recall@5 with and without captions decides whether the
  ~64 h bulk caption run is worth it (M4's recommendation: caption everything last, in the background).
- Near-duplicate bursts fill the top results (IIT sign, shop list, beach). This is the P6 baseline.
- No stemming in FTS ("pizza" ≠ "pizzas").
- Ollama upgrades change rewriter outputs (13 of 20 changed on 3b). Re-run `eval/rewrite_examples.yaml`
  after any upgrade.

## Process notes

- An agent wrote its handback before its final commit (M2). The handback is now required to be written
  last, after the final commit (CLAUDE.md).
- M6 stopped mid-rebase at a usage limit and resumed cleanly. Resuming the same session kept the model
  comparison results, which weren't saved to disk yet. Agents should write key results to their
  report as soon as they have them, not only at the end.
