# M6 hand-back reports

The brief reports M6 sent back to the coordinator, one dated section per report.

## Phase A (2026-10-20)

**Phase A done (M6, query rewriter).** I committed it as `3d2a506` on branch `m6` in `/Users/nileshgupta/Documents/Image Indexer/.worktrees/m6`. `uv run pytest` passes (75 tests, 16 of them new), and `ruff check` and `ruff format --check` are clean. I didn't touch `search()`, the retrievers, the DB or `data/`.

The 20 queries run well enough to wire into M5. English comes back unchanged 5 of 5 and no dates are invented, but about 8 of the 15 Hindi and Hinglish rewrites are wrong somewhere (listed under Weak spots).

**What was built**
- **`imgsearch/models/rewriter.py`:** `OllamaRewriter(config.models.rewriter, client=None)` with `rewrite(query) -> ParsedQuery`.
  - It POSTs to `{url}/api/chat` with the model name, the system prompt plus the user's query, `format` set to the pydantic model's JSON schema, `options.temperature: 0` and `stream: false`. I checked this shape against the current docs at docs.ollama.com/api/chat. The installed Ollama is 0.17.4.
  - The response is validated with pydantic: `english` must not be blank after stripping, `people` is a list of strings, `place` is a string or null, and `date_range` is `{start, end}` as ISO dates or null.
  - The code always sets `original` to the exact input. Any HTTP error, timeout, non-JSON reply or schema mismatch falls back to `ParsedQuery(english=query, original=query)` and logs `log.warning("query rewrite failed, ...")`.
  - `keep_alive` isn't sent, so Ollama's default applies.
- **`config.yaml` / `imgsearch/config.py`:** I added `timeout_s: 30` and `prompt` (a block string) under `models.rewriter` only. The prompt forbids inventing facts and tells the model to copy English queries word for word. It has four few-shot examples, none taken from the eval set.
- **`imgsearch rewrite "..."`:** appended at the end of `cli.py`. It prints the ParsedQuery as JSON and then `took X.XXs`.
- **`eval/rewrite_examples.yaml`:** 10 Hinglish, 5 Devanagari and 5 English queries, each with a hand-written `expect`.
- **`tests/test_rewriter.py`:** all tests use `httpx.MockTransport`, with no monkeypatching. They cover:
  - the request shape: model, URL, stream, temperature, format, messages and timeout;
  - parsing and trimming;
  - `original` overriding whatever the model returns;
  - 10 fallback cases, each asserting the warning is logged: connection error, timeout, HTTP 500, non-JSON body, missing message, non-JSON content, schema mismatch, missing field, bad date, blank `english`;
  - the prompt wording and the 10/5/5 mix in the examples file;
  - the CLI, pointed at a closed port so it exercises the fallback without calling Ollama.

**The 20 queries** (qwen2.5:3b, final prompt). Two full runs gave identical outputs.

| # | Lang | Input | english | people / place |
|---|---|---|---|---|
| 1 | hinglish | mummy ke saath Goa wali pizza ki photo | having a photo of pizza with mom in Goa | [mummy] / Goa |
| 2 | hinglish | papa ka birthday cake | dad's birthday cake | [papa] / – |
| 3 | hinglish | dadi ke haath ka aloo paratha | grandma's hand's potato flatbread on top of it | [dadi] / – |
| 4 | hinglish | bhai ki shaadi mein dance | dancing at the wedding of the brother | [bhai] / – |
| 5 | hinglish | Manali trip pe barf mein khelte hue | playing in the snow during the Manali trip | [] / Manali |
| 6 | hinglish | hamara kutta sofa pe soya hua | our dog fell asleep on the couch | [hamara] / sofa |
| 7 | hinglish | bijli ka bill wali photo | electricity bill's photo | [] / – |
| 8 | hinglish | aadhaar card ki photo | photo of aadhaar card | [] / – |
| 9 | hinglish | Diwali pe diye aur rangoli | colored Diwali with rangolis | [] / – |
| 10 | hinglish | station pe train ka intezaar karte hue Rahul | Rahul and others waiting at the station for the train | [Rahul] / – |
| 11 | hindi | माँ के साथ मंदिर की फोटो | with mom at the temple | [माँ] / – |
| 12 | hindi | बिल्ली खिड़की पर बैठी है | Billie sitting on the balcony | [बिल्ली] / – |
| 13 | hindi | जयपुर में परिवार की तस्वीर | photos of the family in Jaipur | [] / Jaipur |
| 14 | hindi | गोलगप्पे खाते हुए बच्चे | children eating while in Golgappa | [] / – |
| 15 | hindi | पासपोर्ट का पहला पन्ना | first page of passport | [] / – |
| 16 | english | dog playing in the park | dog playing in the park (unchanged) | [] / park |
| 17 | english | restaurant menu with prices | unchanged | [] / – |
| 18 | english | family dinner at home | unchanged | [] / – |
| 19 | english | boarding pass for the Delhi flight | unchanged | [] / Delhi |
| 20 | english | sunset from the hotel balcony in Goa | unchanged | [] / Goa |

`date_range` was null for all 20. **English came back unchanged in 5 of 5** (criterion 8 needs at least 4 of 5).

**Weak spots for the reviewer (a limit of the 3B model):**
- Rewrites that invent or mistranslate:
  - #12 reads बिल्ली (cat) as the name "Billie" and खिड़की (window) as "balcony".
  - #10 adds "and others".
  - #9 drops "diye" (oil lamps).
  - #14 treats golgappe as a place.
  - #3 is clumsy.
- `people` and `place` are sometimes wrong: "hamara" tagged as a person, and sofa and park tagged as places. MVP search ignores both fields.
- A third prompt version with Hindi noun hints fixed #12, but it invented a Diwali date and changed English query #17. Its hints were also tuned to the eval words, so I went back to the second version.

**Timing (smoke only):** about 18 s on a true cold load, 1.5–2.4 s for the first call with the model already loaded, and about 1.0 s median and 1.4 s max warm. `ollama ps` shows qwen2.5:3b resident at 2.3 GB, 100% GPU, unloading 5 minutes after the last call (Ollama's default). Other wave agents may have been loading models at the same time, so the proper benchmark is in phase B.

**Decision for you:**
- **Prompt in `config.yaml`:** the prompt lives there because acceptance criterion 1 requires it.
- **`original` and the model's schema:** the schema sent to the model leaves out `original`, because code sets it and asking the model to echo the query wastes tokens. If the reviewer wants the pydantic model to list `original` too, as criterion 3 reads, it's a small change.
- **Phase B:** I left the README section, the `docs/benchmarks.md` entry, the plan's "Changes during build" note and `docs/reports/M6.md` for phase B, along with the rebase and merge. I haven't started phase B and am waiting for your go.

## Phase B part 1 (2026-10-20)

**Phase B part 1 is done except for one candidate:** `qwen2.5:7b` is pulled, but `gemma3:4b` won't download on the installed Ollama 0.17.4. No model was loaded into RAM (`ollama ps` is empty) and no queries were run. I haven't rebased or merged.

- **Disk space:** 34 GiB free before, 30 GiB after.
- **Tags exist:** the Ollama registry has both `library/qwen2.5:7b` and `library/gemma3:4b` (manifest request returns HTTP 200).
- **`qwen2.5:7b`:** pulled, 4.7 GB (ID 845dbda0ea48).
- **`gemma3:4b`:** not pulled. The pull fails with "412: The model you are attempting to pull requires a newer version of Ollama." brew lists stable 0.40.6, against our 0.17.4.
- **Installed now:** `qwen2.5:7b` 4.7 GB, `qwen2.5:3b` 1.9 GB, and `mistral:latest` 4.4 GB, which was already there and isn't part of this task.

**Decision for you:** how to handle `gemma3:4b`. Upgrading means running `brew upgrade ollama` and restarting the brew service. That changes the system outside the project, so I didn't do it without the user's OK. It wouldn't affect M4's MLX run, which doesn't use Ollama. Options:
- **(a)** The user approves the upgrade. I upgrade, pull `gemma3:4b`, and then re-check that the `/api/chat` request shape still matches the docs and the tests.
- **(b)** Compare only `qwen2.5:3b` against `qwen2.5:7b`, and record in the report why `gemma3:4b` was dropped.
- **(c)** Pick another candidate that works on 0.17.4.

I'll wait for M4 to finish and for your answer before part 2. I'll write `docs/reports/M6-handback.md` (Phase A, Phase B part 1, Phase B part 2) in part 2, as you asked.

### Upgrade outcome (option (a), approved by the user)

This wasn't sent as a separate report: the session hit a usage limit right after the pull, and M4 finished in the meantime. So the outcome is recorded here and repeated in part 2.

- `brew upgrade ollama`: 0.17.4 → 0.40.6. `brew services restart ollama` worked, and `/api/version` reports `0.40.6`.
- `ollama pull gemma3:4b` succeeded: 3.4 GB (ID 68ceab7e468a).
- `qwen2.5:3b`, `qwen2.5:7b` and `mistral:latest` were still listed after the upgrade.
- Disk free after the pull: 26 GiB.
- No model was loaded and no queries were run during the upgrade.

## Phase B part 2 (2026-10-20)

**M6 is done and merged into `main`.** `qwen2.5:7b` is now the rewriter model, and `uv run pytest` passes (150 tests).

**Model comparison** (20 queries, prompt unchanged, Ollama 0.40.6, hand-scored against `expect`):

| Model | Correct / minor / wrong | English unchanged | Warm median / max | Cold | Resident |
| --- | --- | --- | --- | --- | --- |
| qwen2.5:3b | 9 / 6 / 5 | 5 of 5 | 0.74 / 1.34 s | 4.9 s | 2.1 GB |
| **qwen2.5:7b** | **15 / 4 / 1** | 5 of 5 | 1.67 / 1.97 s | 5.2–9.4 s | 4.6 GB |
| gemma3:4b | 11 / 8 / 1 | 5 of 5 | 2.03 / 2.19 s | 6.6 s | 2.2 GB |

- **qwen2.5:7b fixes the phase A problems:** cat/window, diyas, "and others", golgappa and the paratha. Its one wrong output is #13: जयपुर (Jaipur) becomes "Jodhpur", the same on every run.
- **Its minor issues are all in `people` and `place`,** which MVP search ignores.
- **The per-query scores** are in `docs/reports/M6.md`.

**Done:**
- `ollama rm` removed `qwen2.5:3b` and `gemma3:4b`; `mistral:latest` was left alone. 31 GiB of disk is free.
- The `/api/chat` request shape is unchanged on 0.40.6; I re-checked it against the docs, and the tests pass.
- I rebased onto `d343618`, resolving the conflicts in `config.yaml` (the `ocr` section placed after the rewriter's `prompt`) and in `cli.py` (both sides' imports kept). The tests and ruff passed, then I merged fast-forward.
- I wrote the README section and the `docs/benchmarks.md` entry.
- I added five plan entries under "Changes during build": the model choice, the Ollama upgrade, the config keys, Q7 and the client injection.
- I wrote `docs/reports/M6.md` and this file. All 12 acceptance items pass.
- I committed on `main` and removed the worktree and the `m6` branch.

**Flags:**
- **The Ollama upgrade changed outputs.** It changed 13 of the 20 `qwen2.5:3b` outputs from phase A (English was unchanged), so re-check the examples after any future upgrade.
- **The rewriter needs more memory.** It now holds about 4.6 GB while loaded, so M5 shouldn't search while captions are running.
