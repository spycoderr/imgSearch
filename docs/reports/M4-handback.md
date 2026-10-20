# M4 hand-back reports

The brief reports M4 sent back to the coordinator, one dated section per report.

## Phase A (2026-10-20)

Phase A done (M4, caption stage). The code, unit tests and smoke check are committed on branch `m4` as `a6298d1`, in the worktree `/Users/nileshgupta/Documents/Image Indexer/.worktrees/m4`. `uv run pytest` passes (75 tests, 16 of them new) and ruff check and format are clean. The main checkout is untouched, and I haven't started phase B.

**What was built**
- **`imgsearch/models/captioner.py` (`MlxCaptioner`)**: it uses the API of the installed mlx-vlm 0.7.9, checked in its source.
  - It loads the model with `load(name)` and builds the chat prompt once with `apply_chat_template(processor, model.config, prompt, num_images=1)`.
  - It calls `generate(..., image=[PIL image], max_tokens=..., temperature=0.0)`. Setting temperature to 0 matters: the checkpoint's `generation_config.json` has `do_sample: true` and temperature 0.7, and only an explicit temperature overrides that.
  - `unload()` drops the model and processor, then runs `gc.collect()` and `mx.clear_cache()`.
  - `fit_to_area` scales each image down to at most `max_pixels` before captioning. `clean_caption` removes only `<|...|>` tokens and a leading "Caption:".
- **`imgsearch/stages/caption.py` (`CaptionStage`)**: `requires=["ingest"]`, and the version comes from `config.stages`.
  - It captions `derivatives_root / derivative_path` and writes only through `save_caption` with the model name.
  - The constructor takes `load_captioner`, so tests can pass a fake.
  - mlx-vlm is imported inside the default loader because importing it takes about 3 s, and the CLI will import every stage.
- **`config.yaml` / `imgsearch/config.py`**: new keys `prompt`, `max_tokens: 200` and `max_pixels: 1048576`, only under `models.captioner`.
- **`tests/test_caption.py`**: covers resizing, the output cleanup, and the stage (end to end with a fake captioner, through `run_stage`). No real model is loaded.
- **`README.md`**: a "Captions (M4)" section appended at the end. I didn't touch `cli.py`; the `run` dict entry gets added in phase B.

**Decision for you: the size limit is an area cap, not a long edge.** I capped images at about 1 MP instead of a 1024 px long edge. With a long-edge cap, the 6203×507 panorama would shrink to 1024×83 and any text in it would be unreadable. With the area cap it becomes 3581×292, and the image still costs at most about 1k tokens, the same budget as 1024×1024. This follows the reasoning of M1.1's R5.

**Smoke check (3 derivatives, read-only, final prompt)**

| Image | Time | MLX peak | Caption |
| --- | --- | --- | --- |
| Panorama `IMG_20171115_132546` | 6.0 s | 3.94 GB | "The photo shows a panoramic view of a rugged, arid landscape with rocky hills and sparse vegetation, overlooking a valley with a body of water and distant mountains under a bright sky. Three people stand on a rocky outcrop in the foreground, posing for the shot." |
| `IMG_20180121_130605` (2364×1330) | 7.3 s | 3.96 GB | "A fluffy white dog wearing a red patterned vest lies on a red and beige ornate rug, looking up at the camera. Behind it, a dark wooden table leg is visible. No readable text is present." |
| `IMG_20170122_125432` (1536×2048) | 7.8 s | 3.96 GB | "Two young men in formal school uniforms pose for a photo in a classroom setting, with one wearing a maroon blazer and a matching cap, and the other in a grey blazer with a red sweater. In the background, other students are visible near a chalkboard. The text on the left man's blazer reads "46"." |

- **Load:** 2.2–2.5 s; 3.10 GB of MLX memory is in use after loading.
- **After `unload()`:** MLX active and cache memory are both 0.00 GB.
- **Whole process:** peak memory (max RSS) was 2.46 GB.
- **Projection:** at about 7 s per image, 40k photos would take about 78 hours. This is a rough number from 3 images; phase B measures it properly.

**Flags for phase B**
- **The prompt isn't fully obeyed.** My first prompt led the model to add "There is no visible text in the image." This kind of filler would make queries containing "text" or "visible" match every caption. I tightened the prompt and re-ran the same 3 derivatives. Two captions are now clean, but the dog photo still ends with "No readable text is present.", and two captions run to 3 sentences instead of 1–2. I didn't strip this filler in code, because acceptance item 5 only allows removing chat-template artifacts. Before the full run it's worth tuning the prompt on a few more images or lowering `max_tokens`. Alternatively, you or the supervisor could allow stripping a fixed "no text" sentence.
- **No text-heavy photo was tested.** The 130 photos contain no screenshots or PNGs, so verbatim text capture was only seen on the "46" badge.
- **Two runs on the same 3 derivatives.** The prompt fix meant running the smoke check twice on the same images, so the 3-file limit held.
- **`uv` warning.** Commands print a warning that the main checkout's `VIRTUAL_ENV` is ignored; the worktree uses its own `.venv`. It's harmless.

The smoke script is `/private/tmp/claude-501/-Users-nileshgupta-Documents-Image-Indexer/ecf7fb3d-6ec9-43a4-8480-8a4432d7d2ee/scratchpad/smoke_m4.py`.

## Phase B (2026-10-20)

M4 phase B is done and committed on `main`. The worktree `.worktrees/m4` and the `m4` branch are removed.

- **Real run:** `imgsearch run caption` on the 130 photos, from the main checkout. 130 done, 0 failed; a second run processes 0. There are 130 `text_fts` rows of kind `caption`, and `search-text` finds the beach, candy-list and dog photos through their captions.
- **Benchmark:**
  - 755.5 s in total, about 5.8 s per image (median 6, max 7); model load 2.2–2.5 s.
  - Memory: MLX peak 3.96 GB, max RSS 3.88 GB, peak memory footprint 5.84 GB, and 0.00 GB of MLX memory after `unload()`.
  - Projection: about 64 h for 40k photos.
- **Q4:** `max_tokens` is now 100.
  - I tried two prompts on 10 varied images. I kept the phase A prompt (filler in 4 of 10, text copied line by line). The other one had filler in 3 of 10 but paraphrased the text instead of copying it.
  - Nothing is stripped in code. Filler over the full run is 45 of 130 (35%).
- **Other flags:**
  - On 2 of 130 photos, greedy decoding got stuck repeating a phrase until `max_tokens` cut it off.
  - Copied text is sometimes wrong ("Rit Kat").
  - 37 descriptions have 3 sentences.
  - Correction to my phase A hand-back: the set does have text-heavy photos (photographed documents).
- **Recommendation:** caption everything, last and in the background (~64 h). Captions add scene words for the 61 of 130 photos with no OCR text. Skip them if the M7 eval shows no gain in recall@5.
- **Docs:**
  - Report: `docs/reports/M4.md`.
  - Benchmark: `docs/benchmarks.md` ("M4: caption").
  - Three M4 entries in the plan's "Changes during build" (area cap Q3, settings and decoding Q4, S4 injection).
  - README section and status row.
- **Tests:** 134 pass, and ruff is clean.
