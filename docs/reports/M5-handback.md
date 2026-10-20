# M5 hand-back

2026-10-20 · Sent to the user after the final commit.

## M5 (single phase)

M5 is done. Hybrid search works end to end on the 130 photos, and W1, W3 and W4 are closed.
The work is committed on `main` as `eb29796` and not pushed. 184 tests pass and ruff is
clean. Every item in `docs/acceptance/M5.md` passes. The optional real check with Ollama
stopped wasn't run; tests cover that case.

**What was built**
- **`rrf_fuse`** (`imgsearch/search/fusion.py`): score = Σ 1/(60 + rank). Ties go to the
  best single rank, then the image id.
- **`Searcher`** (`imgsearch/search/hybrid.py`):
  - `open_searcher(config, conn)` loads SigLIP and the rewriter once.
  - `searcher.search(query, k, rewrite=True, signals=None)` runs per query: rewrite, then
    vector on `english` and text on `english` + `original` (50 each), then RRF, then the
    top 5.
  - It returns the results plus the rewritten query and the timings.
- **`imgsearch search "..." [--k N] [--no-rewrite] [--signals vector,text]`** prints what
  was searched, then rank, fused score, path, matched signals with their ranks, and a short
  caption, then the timings.
- **Interface changes**, recorded in the plan:
  - `Result` gains `orig_relpath` and `score`.
  - `ParsedQuery` gains `rewrite_error`, so the CLI can say "rewrite skipped".
  - `search()` returns a `SearchResponse`.
- **W1:** `date_range` is dropped unless the query has a digit-based year or date. The
  prompt gets today's date through a `{today}` placeholder.
- **W4:** I **removed** `search-vec` and `search-text`. `search --signals vector|text
  --no-rewrite` does the same with the one printer.
- **W3:** the README note is in place.

**Real checks: 14 queries** (5 English, 3 Hinglish, 3 Devanagari, 3 text-in-image), all in
`docs/reports/M5.md`
- Every query has an expected photo at rank 1 in hybrid. I picked the queries after reading
  the captions, so this shows the system works; it doesn't measure recall.
- **Hybrid better than one signal:**
  - "The Road Not Taken": vector misses both poem pages; hybrid ranks one 1st.
  - "samundar mein masti karta bachcha": text finds nothing; hybrid ranks the photo 1st.
- **Hybrid worse:**
  - "safed kutta carpet pe baitha hua": vector has both carpet-dog photos at 1–2; hybrid
    moves one to 4, because "white dog" matches every dog caption.
  - "pigeons on the street": similar.
- No query was better than *both* single signals.

**Rewrite examples (W1):** all 20 M6 examples gave the same output as in M6.
- "kal wali shaadi ki photos": the model invented 2025-10-20, and the guard dropped it.
- But its English is still wrong ("previous year's wedding"). I left the prompt alone, per
  W2's "don't tune for one word".

**Speed and memory**
- Warm query: median 1.68 s, of which the rewrite is 1.64 s and retrieval 0.05 s.
- Cold first query: 9.2 s, while Ollama loads the model.
- SigLIP load: about 6 s per CLI call.
- Memory: 2.2 GB for the search process plus 4.6 GB in Ollama.

**Flags for M7**
- The rewriter changed 2 of 8 English queries slightly (added "a"; "Dubai" capitalised).
  This had no effect on results.
- The top 5 is always full of vector-tail filler when text matches little.
- Near-duplicates fill the top results.
- Include "kal wali shaadi" and जयपुर → Jodhpur in the eval.
