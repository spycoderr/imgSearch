# M3 hand-back reports

The brief reports the M3 agent sent back at the end of each phase, saved as sent. The formal
report is `docs/reports/M3.md`.

## Phase A (2026-10-20)

Phase A done for M3. The work is in `.worktrees/m3`, committed on branch `m3` as `ef34369`. I haven't merged it into main or touched the real DB. All 98 tests pass (the earlier 59 plus 39 new), and `ruff check` and `ruff format --check` are clean. Phase B (rebase, merge, real run, benchmark, README, report) waits until you say go.

**What I built**
- **Config:** a new `models.ocr` section with `recognition_level: accurate` and `languages: ["en-US"]`. It's checked by an `OcrConfig` class in `imgsearch/config.py`, and I added one `ocr:` field to `ModelsConfig`.
- **OCR engine (`imgsearch/models/ocr.py`):** `AppleVisionOCR.read(path)` runs ocrmac's `text_from_image` on the image, and the lines come back in Vision's order, joined with `\n`. An image with no text gives `""`.
- **Stage (`imgsearch/stages/ocr.py`):** `OcrStage` requires `ingest`, reads its version from `config.stages["ocr"]`, reads only `derivatives_root / derivative_path`, and writes only through `save_ocr`. It takes an optional engine so tests can pass a fake; otherwise `load` builds `AppleVisionOCR`, and `unload` drops it.
- **Retriever (`imgsearch/search/text.py`):** `TextRetriever(conn).retrieve(q, k)` searches all of `text_fts`, so OCR text and captions both match.
  - **Safe queries:** words are runs of letters, digits and combining marks, so Devanagari vowel signs stay inside their words. Each word is double-quoted, duplicates are dropped, and the words are joined with `OR`. A query with no words returns `[]`.
  - **One query, not two:** the words of `q.english` and `q.original` go into a single MATCH. Each image appears once, at its best bm25 row.
  - **Score:** `score` is `-bm25`, so higher is better, and `signal` is `"text"`.
  - **Dedupe in Python:** `bm25()` can't be used under `GROUP BY` in SQLite (it raised "unable to use function bm25 in the requested context"), so rows are ordered by bm25 and the first row per image is kept.
  - **Snippets:** `best_snippet()` returns the matching text for display, with matched words in `[ ]`.
- **CLI (appended at the end of `cli.py`):**
  - `imgsearch run <stage>` uses `STAGES = {"ocr": OcrStage}`; each entry is built as `cls(config)`. Ingest isn't in the dict, because `imgsearch ingest` also registers new files. The command prints done and failed counts, lists each failure, exits 1 if any image failed, and exits 2 for an unknown stage.
  - `imgsearch search-text "..." [--k N]` prints rank, score and relpath, then a snippet line.
- **Tests:** `tests/test_ocr.py` and `tests/test_search_text.py`. They cover two generated lines read in order, a blank image stored as `""` and marked done, the derivative path being read, re-runs and version bumps, waiting for ingest, failures being recorded, the run command, both kinds of row, one candidate per image ranked by bm25, both query fields, 19 risky query strings (quotes, `-`, `:`, `*`, `(`, AND/OR/NOT/NEAR, Devanagari, emoji) that must not raise, and `search-text` output.

**Smoke check** (3 real derivatives from the main checkout, opened read-only; the main DB was opened `mode=ro` only to look up paths):
```
--- panorama IMG_20171115_132546 (6203×507)  0.30s, 0 lines
--- IMG_20180318_092837 (720×1280)           0.07s, 0 lines
--- IMG_20170813_111023 (480×640)            0.05s, 0 lines
max RSS MB 130.9
```
I looked at the panorama and IMG_20180318_092837: there is no text in either (a landscape with people, and a dog), so empty output is right. The two generated-text tests read their lines exactly.

**Flags**
1. **Vision errors look like "no text".** ocrmac's `text_from_image` returns `[]` when the Vision request fails, without raising. Such an image would be stored as `""` and marked `done`, not `failed`. The only fix I see is calling Vision through pyobjc directly instead of ocrmac. I've left ocrmac as specified; that's your call.
2. **Three tests call the real Apple Vision framework** (two-line order, blank image, and the `run ocr` end-to-end test). The wave rules say unit tests mustn't load real models, but acceptance item 2 asks for a test on a generated image. Vision is a system framework with no model download, and these tests take well under a second. Tell me if you'd rather they used the fake engine.
3. **No stemming.** FTS uses the default unicode61 tokenizer, so "pizza" won't match "pizzas". There's no change to the schema or tokenizer.
4. **Shared files I touched:** `config.yaml` (my section only), one `ocr:` field in `ModelsConfig`, and two import lines at the top of `cli.py` besides the appended commands.

## Phase B (2026-10-20)

M3 phase B is done. OCR ran on all 130 real photos with no failures, and at least 3 real text-in-image queries return the right photo in the top 3, which was the main acceptance test. All 100 tests pass and ruff is clean. The work is on `main` in three commits (`e8ca800` phase A, `527a0c7` the S3 stopword fix, `2edb697` the docs); the m3 branch merged fast-forward, and the worktree and branch are removed.

**Supervisor items**
- **S3:** queries now drop a fixed list of English stopwords plus photo words (photo, picture, image, pic and their plurals), in any case. A query made only of those words returns nothing. Two tests cover this.
- **S4:** the convention of passing a fake model to a stage for tests is recorded once in the plan's "Changes during build". M2 and M4 can point to it in their reports.
- **Q1:** the ocrmac gap is recorded in the plan and in the report. ocrmac returns no text instead of an error when Vision fails, so such a photo looks like a photo with no text. The baseline is 61 of 130 with empty text, so a jump in that number later would show the problem.

**Real run**
- `imgsearch run ocr` did 130 photos with 0 failed. A second run processed 0, and `status` shows ocr 130 done.
- 69 photos have text and 61 have none. The panorama `IMG_20171115_132546.jpg` gave no text, which is right: it's a landscape with no writing in it.
- Five real queries put the expected photo at rank 1: `skittles`, `dubai bazaar`, `Indian Institute of Technology`, `WASHINGTON hotels`, `Godavari`. A query with Devanagari and punctuation, `पनीर (dosa) -AND`, returns "no matches" with no error.
- Printed text (signs, screens, printed sheets) reads well. Handwritten notes, which are a lot of the text photos, come out garbled.

**Benchmark:** about 0.24 s per photo (31.8 s for all 130), peak memory 371 MB. That projects to about 2.7 h for 40k photos. A `search-text` query takes 0.22 s end to end.

**For the next tasks**
- M2 and M4 add their stage to the `STAGES` dict in `cli.py` when they rebase.
- The retriever already searches captions, so M4 only needs to write them through `save_caption`. Filler words like "text" or "visible" in captions aren't stopwords and will still match.

**Left as it is:** the README's Status paragraph still says "M1 done … not yet searched". That's shared text, so I only changed the M3 row of the status table.

Docs:
- `docs/reports/M3.md`
- `README.md` (new "OCR and text search (M3)" section)
- `docs/benchmarks.md` ("M3: ocr")
- `Personal Image Search Design and MVP Plan.md` (four M3 entries under "Changes during build")
