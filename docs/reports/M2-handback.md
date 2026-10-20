# M2 hand-back reports

The brief reports the M2 agent sent back at the end of each phase, saved as sent. The formal
report is `docs/reports/M2.md`.

## Phase A (2026-10-20)

**Phase A done (M2).** The work is in `/Users/nileshgupta/Documents/Image Indexer/.worktrees/m2`, branch `m2`, commit `8a3989c`. Nothing has been merged into `main`, nothing was run against the real DB, and no README, benchmark, report or plan-doc edits are made yet (those are phase B). I'm waiting for go before starting phase B.

**What I built**
- **`imgsearch/models/siglip.py` — `SiglipEmbedder`:** implements `Embedder` via transformers `AutoModel`/`AutoProcessor`. Model name, dim and device come from `config.models.embedder`. Every vector is L2-normalized float32 through `normalize()`, which raises on a wrong length or a zero vector. The model loads when the object is created.
- **Text tokenization:** `padding="max_length"`, `max_length=64`, `truncation=True`, and the text is lowercased. I checked this against the sources, not memory:
  - The model card itself (online and cached) says nothing about tokenization.
  - The transformers SigLIP2 docs and the cached `modeling_siglip2.py` say pad/truncate to 64 and that the model was trained on lowercased text.
  - The checkpoint's `tokenizer_config.json` sets `model_max_length` to about 1e30, so `max_length` has to be passed explicitly.
  - The tokenizer does not lowercase by itself (`"A Dog"` and `"a dog"` give different ids), so the explicit `.lower()` is needed.
  - I left out the `"This is a photo of {label}."` template: the docs give it for zero-shot classification parity, not for retrieval queries.
- **`imgsearch/stages/embed.py` — `EmbedStage`:**
  - `name="embed"`, `requires=["ingest"]`, version from `config.stages`.
  - `run()` reads `derivatives_root / derivative_path`, never the original, one image per call.
  - `load()` builds the embedder; `unload()` drops it, runs `gc.collect()` and `torch.mps.empty_cache()`, and is safe without a load.
  - `save_embedding()` checks the length and writes `INSERT OR REPLACE` into `embeddings` keyed by `(image_id, model)`, using `sqlite_vec.serialize_float32`.
- **`imgsearch/search/vector.py` — `VectorRetriever(conn, embedder, embedder_config)`:** embeds `q.english` and ranks by `vec_distance_cosine` over rows `WHERE model = <configured name>`, ties broken by `image_id`. It returns `Candidate`s with rank 1 = best, score = 1 − distance (cosine similarity) and signal `"vector"`. A query vector of the wrong length raises.
- **`imgsearch search-vec "QUERY" [-k N]`:** appended at the end of `cli.py`, with `k` defaulting to `search.top_k`. It prints rank, score and `orig_relpath` through a small `candidate_lines()` helper. torch is imported inside the command so other commands stay fast.
- **Config:** one new key in my section, `models.embedder.text_max_length: 64`, plus the matching field in `EmbedderConfig`.

**Tests:** 73 pass (59 before, 14 new in `tests/test_embed.py` and `tests/test_vector.py`). `ruff check` and `ruff format --check` are clean. Tests use fake embedders, with no real model, no monkeypatching and no skips. They cover:
- normalization, and rejecting wrong lengths and zero vectors
- the stage's name, version and requires coming from config
- reading derivative paths only, and skipping images whose ingest isn't done
- a version bump replacing vectors without duplicating rows
- a wrong-length vector marking the image `failed`
- the exact `serialize_float32` blob
- cosine ranking, scores, filtering by model, `k`, and that `english` (not `original`) is embedded
- the `search-vec` output format

**Smoke check (3 derivatives, read-only from the main checkout; scratch DB in the scratchpad; model freed afterwards)**
- Model load takes about 4–5 s; the first 3 images embed in 1.5 s; peak memory (max RSS) is about 1.0 GB. Vectors are shape (768,), float32, with norm 1.0.
- Stage run 1 gave `{'done': 3, 'failed': 0}`; run 2 gave `{'done': 0, 'failed': 0}`. 3 rows are stored under `google/siglip2-base-patch16-384`, 3072 bytes each.
- `search-vec` put the right image first for all three queries:

| Query | 1st | 2nd | 3rd |
| --- | --- | --- | --- |
| "two people in a shopping mall" | mall.jpg 0.1248 | handwritten.jpg 0.0113 | textbook.jpg −0.0039 |
| "handwritten notes" | handwritten.jpg 0.1311 | textbook.jpg 0.0949 | mall.jpg 0.0127 |
| "a maths textbook page" | textbook.jpg 0.1770 | handwritten.jpg 0.1060 | mall.jpg 0.0004 |

  Raw cosine scores are small (about 0.0–0.18). That is normal for SigLIP, which applies a learned logit scale on top; only the order matters for RRF.

**Notes for the user / phase B**
- **Test hook:** `EmbedStage` takes an optional `make_embedder` callable (default `SiglipEmbedder`) so tests can pass a fake. It's the smallest way to test without monkeypatching, but it is a constructor parameter beyond the plan's interfaces; I'll record it in the report.
- **Imports in `cli.py`:** besides appending at the end, I added `sqlite3`, `Candidate` and `ParsedQuery` to its imports at the top. A trivial rebase conflict with M3 is possible there.
- **Warning on load:** transformers prints that the model config's `bos_token_id`/`eos_token_id` (49406/49407) are out of vocabulary range. This comes from the checkpoint's config and is harmless for embedding (the text ids end in eos id 1).
- **Aspect ratio:** this is the fixed-resolution checkpoint, so every derivative is squashed to 384×384. The 6203×507 panorama will be heavily distorted, as M1.1 flagged.
- **Phase B:** I'll add an `embed` entry to M3's `run` stage dict after rebasing.
- **Worktree environment:** `uv run` created a separate `.venv` inside the worktree; it is git-ignored.

## Phase B (2026-10-20)

**M2 phase B done.** M2 is merged into `main` (fast-forward to `67ed8fd`, then a docs commit on top). The worktree and the `m2` branch are removed. Formal report: `docs/reports/M2.md`.

- **Supervisor items:**
  - S1 done: `EmbedStage`'s default builder `load_siglip` and `unload()` import torch/transformers lazily. `import imgsearch.cli` loads neither, checked by a subprocess test.
  - S2 done: the tokenization comment now names the transformers SigLIP2 docs.
  - S4 convention followed (`make_embedder`).
  - S5 left as decided.
  - `embed` added to `STAGES`, so `imgsearch run embed` works.
- **Real run (130 photos):** 130 done, 0 failed; a second run processes 0. 130 vectors under `google/siglip2-base-patch16-384`, 3072 bytes each. Bumping `stages.embed` 1 → 2 re-ran only embed: ingest and ocr rows unchanged, still 130 vectors. Config stays at `"2"`.
- **Benchmark:**
  - 14.6 s for the whole run (1.7 s import, 2.6 s model load, 0.060 s per image median), about 40 min for 40k photos.
  - Peak memory: max RSS 1.03 GB, footprint 2.35 GB incl. MPS.
  - Query: 9 ms warm; the whole `search-vec` command takes 4.7 s.
- **Queries, top 3 checked by eye:**
  - "a dog": 3 of 3 correct.
  - "a handwritten notebook page": 3 of 3 correct.
  - "people at a beach": 2 of 3 are beach shots, but only the 1st has a person in it.
  - "a plate of food": only the 1st is right (a thali); the 2nd is a handwritten chocolate list, the 3rd a man in a restaurant.
  - "a building at night": the buildings come back, but all in daylight.
- **Flags:**
  - Near-duplicate bursts fill adjacent ranks (P6, and M7 should know).
  - The CLI option is `--k`, not `-k` as my phase A note said.
  - One test hard-coded `embed: "2"` and broke after the bump; it now bumps relative to config.
- **Checks:** 116 tests and ruff pass. All 13 acceptance items pass.
