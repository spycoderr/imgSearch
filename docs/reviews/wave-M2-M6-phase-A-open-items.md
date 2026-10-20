# Wave M2/M3/M4/M6 — phase A open items

Questions and flags raised by the phase A reports, collected 2026-10-20. Decisions go in the
"Decision" lines; agents act on them in phase B.

Status: phase A reviewed by the supervisor (all four branches: tests and ruff pass, no hacks
found). Decisions are at the end. Phase B order: **M3 → M2 → M4 → M6**.

| Task | Branch / commit | Tests |
| --- | --- | --- |
| M2 | `m2` @ `8a3989c` | 73 pass (14 new) |
| M3 | `m3` @ `ef34369` | 98 pass (39 new) |
| M4 | `m4` @ `a6298d1` | 75 pass (16 new) |
| M6 | `m6` @ `3d2a506` | 75 pass (16 new) |

## Questions needing a decision

### Q1 (M3) — Vision failures recorded as "no text"
ocrmac's `text_from_image` returns `[]` when the Vision request fails, without raising. Such an
image is stored as `""` and marked `done`, not `failed`. The only fix is calling Vision through
pyobjc directly instead of ocrmac.
- Recommendation: keep ocrmac for the MVP; record as a known gap.
- Decision: see "Supervisor decisions" at the end.

### Q2 (M3) — Tests that call real Apple Vision
Three tests (two-line order, blank image, `run ocr` end-to-end) use the real Vision framework.
The wave rules say unit tests mustn't load real models; acceptance item 2 asks for a test on a
generated image. Vision is a system framework, no download, under 1 s.
- Recommendation: keep them.
- Decision: see "Supervisor decisions" at the end.

### Q3 (M4) — Image size limit: area cap instead of long edge
M4 caps images at ~1 MP (`max_pixels: 1048576`) instead of a 1024 px long edge. A long-edge cap
shrinks the 6203×507 panorama to 1024×83 (text unreadable); the area cap gives 3581×292 at the
same token budget (~1k tokens). Follows M1.1 R5.
- Recommendation: accept.
- Decision: see "Supervisor decisions" at the end.

### Q4 (M4) — Caption filler ("No readable text is present.")
The model adds filler about missing text, which would make queries containing "text"/"visible"
match many captions. After tightening the prompt, 1 of 3 smoke captions still has it, and 2 of 3
run to 3 sentences instead of 1–2. Acceptance item 5 only allows stripping chat-template artifacts.
- Options: (a) tune the prompt / lower `max_tokens` on more images in phase B;
  (b) allow stripping a fixed "no text" sentence in code (needs acceptance change).
- Recommendation: (a).
- Decision: see "Supervisor decisions" at the end.

### Q5 (M4) — README edited in phase A
M4 appended a "Captions (M4)" README section in phase A; M2 treats README as phase B work.
Check against the wave rules; may cause a merge conflict.
- Decision: see "Supervisor decisions" at the end.

### Q6 (all) — Phase B order
M2 and M3 both added imports at the top of `cli.py`; M3, M4 and M2 each add to the `run` stage
dict. Merging one at a time avoids most conflicts.
- Recommendation: M3 first (it owns `run`), then M2, then M4; M6 any time.
- Decision: see "Supervisor decisions" at the end.

### Q7 (M6) — `original` left out of the schema sent to the model
The JSON schema in `format` omits `original`, since code always sets it to the exact input and
asking the model to echo it wastes tokens. Acceptance criterion 3 can be read as wanting the
pydantic model to include `original`. Small change either way.
- Recommendation: keep it out of the model-facing schema; note it in the M6 report.
- Decision: see "Supervisor decisions" at the end.

### Q8 (M6) — Hindi/Hinglish rewrite quality
English came back unchanged 5 of 5 (criterion 8 needs ≥4 of 5) and no dates were invented, but
about 8 of 15 Hindi/Hinglish rewrites are wrong somewhere:
- #12 बिल्ली (cat) → name "Billie", खिड़की (window) → "balcony"
- #10 adds "and others"; #9 drops "diye" (oil lamps); #14 treats golgappe as a place;
  #3 "grandma's hand's potato flatbread on top of it" is clumsy
- `people`/`place` sometimes wrong ("hamara" as a person; sofa, park as places) — MVP search
  ignores both fields.

A third prompt with Hindi noun hints fixed #12 but invented a Diwali date and changed English
#17, and was tuned to the eval words, so M6 reverted to the second prompt.
- Options: (a) accept for MVP and revisit in M7 eval (the original query is also searched by
  the text retriever); (b) try a larger Ollama model; (c) more prompt work in phase B.
- Recommendation: (a).
- Decision: see "Supervisor decisions" at the end.

## Flags (no decision needed, record in reports)

### M2
- Text tokenization: model card is silent; settings taken from transformers SigLIP2 docs and
  modeling code — `padding="max_length"`, `max_length=64`, `truncation=True`, and explicit
  lowercasing (tokenizer doesn't lowercase). The `"This is a photo of {label}."` template was
  left out (it's for zero-shot classification).
- New config key `models.embedder.text_max_length: 64`.
- `EmbedStage` takes an optional `make_embedder` callable for test fakes — a constructor
  parameter beyond the plan's interfaces; to be recorded in the M2 report / plan.
- Fixed-resolution checkpoint squashes every derivative to 384×384; the 6203×507 panorama is
  heavily distorted (as flagged in M1.1).
- Raw cosine scores are small (0.0–0.18), normal for SigLIP; only order matters for RRF.
- Harmless load warning: `bos_token_id`/`eos_token_id` (49406/49407) out of vocab range in the
  checkpoint config.
- Smoke: load 4–5 s, 3 images in 1.5 s, peak RSS ~1.0 GB; re-run did nothing; correct top hit
  for all 3 test queries.

### M3
- No stemming: default unicode61 tokenizer, so "pizza" doesn't match "pizzas". No schema change.
- `bm25()` can't be used under `GROUP BY`; dedupe to one row per image is done in Python.
- `OcrStage` takes an optional engine for test fakes (same pattern as M2/M4).
- Shared files touched: `config.yaml` (`models.ocr` only), one `ocr:` field in `ModelsConfig`,
  two import lines at the top of `cli.py`.
- Smoke: 3 derivatives, 0.05–0.30 s each, peak RSS ~131 MB, all returned no text (checked: none
  in the photos).

### M4
- Temperature must be passed as 0.0 explicitly: the checkpoint's `generation_config.json` has
  `do_sample: true`, temperature 0.7.
- `CaptionStage` takes `load_captioner` for test fakes; mlx-vlm is imported lazily (~3 s import).
- Verbatim text capture barely tested: no text-heavy photos (screenshots, PNGs) in the set; only
  a "46" badge was captured.
- Smoke: 6–8 s per image, MLX peak ~3.96 GB, process max RSS 2.46 GB, MLX memory 0.00 GB after
  `unload()`. Rough projection: ~78 h for 40k photos.
- `uv` warns that the main checkout's `VIRTUAL_ENV` is ignored in worktrees; harmless.

### Cross-task
- M2, M3 and M4 all added an optional constructor argument to inject test fakes. Consider
  recording this once in the plan's Interfaces / Changes during build.
- Each worktree has its own git-ignored `.venv`.

### M6
- Request shape checked against docs.ollama.com/api/chat; installed Ollama 0.17.4. POST
  `{url}/api/chat` with `format` = pydantic JSON schema, `options.temperature: 0`,
  `stream: false`. `keep_alive` not sent (Ollama default: unload 5 min after last call).
- New config keys under `models.rewriter` only: `timeout_s: 30` and `prompt` (block string, four
  few-shot examples, none from the eval set). Prompt in config is required by acceptance item 1.
- Fallback covers 10 cases (connection error, timeout, HTTP 500, non-JSON body, missing message,
  non-JSON content, schema mismatch, missing field, bad date, blank `english`), each logging a
  warning. All tests use `httpx.MockTransport`.
- `date_range` null for all 20 queries; two full runs gave identical outputs.
- Timing (smoke only, other agents may have been loading models): ~18 s cold load, 1.5–2.4 s
  first call with model loaded, ~1.0 s median / 1.4 s max warm. qwen2.5:3b resident at 2.3 GB.
  Proper benchmark in phase B.
- Phase B items left: README, `docs/benchmarks.md`, plan "Changes during build", `docs/reports/M6.md`,
  rebase and merge.

## Supervisor decisions

2026-10-20 · Supervisor reviewed all four branches: code read in full, tests and ruff re-run in
each worktree (M2 73, M3 98, M4 75, M6 75 pass). Every item below is part of that task's phase B.

### Answers to Q1–Q8

| Q | Decision |
| --- | --- |
| Q1 (M3) | **Accept.** Keep ocrmac. Record as a known gap in the M3 report and plan. The report states how many images have empty OCR text, so a jump in that number is visible later |
| Q2 (M3) | **Accept.** Apple Vision is a system framework (no download, < 1 s, little memory), so the "no real models in unit tests" rule's reason doesn't apply |
| Q3 (M4) | **Accept** the ~1 MP area cap. Record it under "Changes during build" next to M1.1 R5 |
| Q4 (M4) | **Option (a), no stripping in code.** Also lower `max_tokens` to about 100 (shorter output, faster). Try the prompt on 10 varied images before the full run. Report the filler rate after the run; any rate is acceptable for the MVP if it's reported |
| Q5 (M4) | **Accept.** The wave rules allow appending your own README section; resolve any conflict by keeping both sections |
| Q6 (all) | **Order M3 → M2 → M4 → M6.** M6 goes last because its phase B now includes a model comparison (Q8); it can download candidate models while M4's caption run is going, since downloads don't use the shared RAM |
| Q7 (M6) | **Accept.** Note it in the M6 report |
| Q8 (M6) | **Modified (b): compare models before accepting.** Hinglish is the plan's core risk, and 8 of 15 wrong is too high to defer without trying the cheap fix. With the current prompt unchanged (no per-model tuning), run the 20 examples on `qwen2.5:3b` and two candidates: `qwen2.5:7b` and `gemma3:4b` (confirm the tags exist in the Ollama library first). Score each output against `expect` by hand (correct / minor issue / wrong) and report a table. Pick the most correct model whose warm median latency is ≤ 3 s, set it in config, and `ollama rm` the losers. Check free disk space before pulling. If none is clearly better, keep `qwen2.5:3b` and record that the M7 eval decides |

### Supervisor items (new)

| # | Task | Item |
| --- | --- | --- |
| S1 | M2 | `imgsearch/stages/embed.py` imports `torch` (and through `SiglipEmbedder`, transformers) at module level. Once `EmbedStage` is in the `run` dict in `cli.py`, every CLI command, including `imgsearch status`, pays that import (seconds). Import lazily, as M4 does: build `SiglipEmbedder` in a small default loader function, and import `torch` inside `unload()` |
| S2 | M2 | The comment in `siglip.py` says the model card requires lowercasing, but the phase A note says the model card is silent and the source is the transformers SigLIP2 docs. Make the comment name the right source |
| S3 | M3 | `match_expression` ORs every word, including "the", "of", "a", "photo". Once captions exist ("This photo shows..."), almost any query matches almost every caption, and the text retriever adds noise to every fused result. Drop a small fixed list of English stopwords plus photo words (`photo`, `photos`, `picture`, `pictures`, `image`, `images`, `pic`, `pics`) from the query. A query made only of stopwords returns []. Add tests |
| S4 | M3 | Record the test-injection convention once in the plan's "Changes during build": stages accept an optional constructor argument that builds their model, so tests can pass a fake. M2 and M4 then just reference it in their reports |
| S5 | all | `candidate_lines` (M2) and the print loop in `search-text` (M3) look up `orig_relpath` the same way. Leave both; M5 replaces them with one result printer |
