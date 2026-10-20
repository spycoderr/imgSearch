# Personal Image Search: Design and MVP Plan

Oct 20, 2026 · @Nilesh

A local, single-user service on a Mac mini M4 that answers natural-language queries (English, Hindi, Hinglish) with the top 5 matching photos from a personal library, delivered later through WhatsApp. This doc holds the full target design and the reasoning behind it, plus the MVP (100 images, CLI query) split into agent-sized tasks.

## Goals and constraints

The system must turn a free-text query into the 5 most relevant photos from a personal library, run entirely on one Mac mini M4 (16GB RAM), and stay simple enough to maintain alone.

**Goals**

- Natural-language retrieval over \~100GB of personal photos (images only, no video).
- Queries in English, Hindi (Devanagari) and Hinglish (romanized Hindi).
- Find images by their text content (menus, receipts, signboards, screenshots); that text is predominantly English.
- Find photos of specific people (faces), added after the MVP.
- Delivered through WhatsApp (official Cloud API) after the MVP.
- Local models; fine-tuning is allowed but only after a baseline is measured.

**Constraints**

| Constraint | Value | Consequence |
| --- | --- | --- |
| Load | \~5 requests/hour, single user | No queueing, caching or fallbacks needed at query time; latency of several seconds is fine |
| Hardware | Mac mini M4, 16GB unified memory | Run heavy models one stage at a time; 4-bit VLMs of 3B-7B only |
| Originals | Never modified; will live on an external HDD | Models read derivatives only; search must work with the drive unplugged |
| Indexing time | Slow is acceptable (new images within 2-3 hours, bulk import can take days) | Batch/background pipeline, no real-time indexing |
| Privacy | Personal photos and faces | Everything local; no cloud model calls; FileVault on |
| Maintenance | One person, later broken into agent tasks | Few moving parts: one DB file, one Python service |

## Decision log

Each row records what was chosen, what else was considered, and why, so a later agent can check whether the reasoning still holds. IDs (D1...) are referenced elsewhere in this doc.

| ID | Decision | Alternatives considered | Why |
| --- | --- | --- | --- |
| D1 | One SQLite file with sqlite-vec (vectors) and FTS5 (text) | LanceDB, Qdrant, pgvector, Milvus | A single user with 100k images at most needs no server; one file is trivial to back up; vectors and text sit in the same transaction. Revisit only if vector search exceeds \~1s |
| D2 | Hybrid retrieval: vector + full-text, merged with Reciprocal Rank Fusion | Vector only; text only | Vectors catch scenes and concepts; FTS catches exact words in OCR text and captions. RRF needs no score calibration between the two |
| D3 | Image embeddings from a multilingual CLIP-family model (SigLIP 2 default; jina-clip-v2 as the swap candidate) | OpenCLIP, ColPali-style document models | Text-to-image search with Hindi support; runs on Apple GPU (mps). Final pick decided by the eval set, not by reputation |
| D4 | Hinglish handled by an LLM query rewrite into English JSON, not by the embedder | Rely on multilingual CLIP; transliterate with rules | CLIP-style models are weak on romanized Hindi. A rewrite step also extracts filters (people, dates, places) for later phases. Cost is \~1s at 5 requests/hour |
| D5 | Separate small text model for the rewrite | Reuse the captioning VLM | Keeps the rewrite fast and independent of the captioner; decided by the user. Memory is managed by unloading models between stages |
| D6 | OCR with Apple Vision (via ocrmac/pyobjc) | PaddleOCR, Surya, Tesseract, VLM-only OCR | Text in images is predominantly English, so Hindi OCR is out of scope. Vision is native, fast and needs no model download. Keep Surya/PaddleOCR as a fallback if recall on menus and screenshots is poor |
| D7 | VLM captions in the MVP (English only) | Skip captions; embeddings + OCR only | At 100 images captions cost minutes, and the MVP answers whether they justify 1-3 days of compute on 100GB. Decided by the user |
| D8 | Originals are read-only; models use a derivative (2048px long edge JPEG) and a 256px thumbnail stored on the Mac | Resize in place; work from originals | Protects originals; keeps search working with the HDD unplugged; 2048px preserves small text and faces. \~10-15GB for 40k photos |
| D9 | Image identity = SHA-256 of the original bytes; location = path relative to a configurable originals root | Absolute paths; filenames | Survives moving originals to the external drive and remounting under a different name. Also gives free exact-duplicate detection |
| D10 | Pipeline = idempotent stages with versioned status rows | One monolithic script | A new stage (faces) or a new model version backfills through the same queue without rewriting existing code |
| D11 | Pluggable interfaces for sources, models, and chat | Hard-wired implementations | Sync, WhatsApp and faces arrive after the MVP; interfaces are defined now so they plug in without refactors |
| D12 | Evaluate before fine-tuning: build a labelled query set and report recall@5 | Fine-tune up front | Most gains come from hybrid search, OCR and captions. The eval set tells us whether fine-tuning later is worth it |
| D13 | WhatsApp via the official Cloud API with the free test number; public webhook through Cloudflare Tunnel | Unofficial libraries (Baileys, whatsapp-web.js); Telegram | Official API has no ban risk and replies inside the 24h window are free. The chat layer is an adapter, so Telegram remains a cheap swap |
| D14 | Phone sync with Syncthing into a Mac inbox, then a mover job to the HDD | Google Photos API; Immich; direct-to-HDD sync | Google's Library API no longer lets apps read a whole library. An inbox on the Mac means an unplugged HDD never stalls syncing |
| D15 | Faces with InsightFace (SCRFD detector + ArcFace embeddings), clustered with HDBSCAN, named by the user once | Apple Vision faces, DeepFace, dlib | Strong open models that run locally via ONNX Runtime. Pretrained weights are non-commercial, acceptable for personal use |
| D16 | Clustering split into near-duplicate groups, time/GPS events, then semantic clusters | Only semantic clustering | Duplicates and events are cheap and improve results most; semantic clusters mainly help browsing |
| D17 | Bulk indexing in priority order: embeddings and EXIF first, OCR, faces, captions last | One image at a time through every stage | Search becomes usable within hours while the slow captioning continues in the background |

## Target architecture

The system is one Python service with two halves: an offline indexing pipeline that builds the searchable store, and an online query path that reads it. Items marked (later) are not in the MVP but have a reserved place.

```
 OFFLINE: INDEXING

 ImageSource ------> ingest ----> embed ----> ocr ----> caption ----> (later) faces ----> (later) cluster
  LocalFolder (MVP)   hash, EXIF   SigLIP 2    Apple     Qwen-VL        InsightFace        dup groups,
  Syncthing (later)   derivative              Vision    via MLX                           events, people
  WhatsApp (later)    + thumbnail
        |                |           |          |           |              |                  |
        v                v           v          v           v              v                  v
   originals         +-------------------------------------------------------------------------+
   (read-only;       |  SQLite: images, stage_status, embeddings (sqlite-vec),                  |
   HDD later)        |  ocr + captions (FTS5), faces, persons, events, dup_groups               |
                     +-------------------------------------------------------------------------+
                                                      ^
                                                      |
 ONLINE: QUERY                                        |
                                                      |
 ChatAdapter ---> search(query) ---> rewrite LLM ---> retrievers (vector, FTS) ---> RRF ---> filters/dedupe ---> top 5
  CLI (MVP)        core function      Hinglish -> EN     (later: people, date, place)        (later: rerank)
  WhatsApp (later)
```

**Layers and responsibilities**

| Layer | Responsibility | MVP implementation | Later |
| --- | --- | --- | --- |
| Source | Yields new image files and a reader for each | Local folder scan | Syncthing inbox mover, WhatsApp uploads |
| Storage | Originals (read-only), derivatives, DB | Originals and derivatives on the Mac | Originals on external HDD; derivatives stay on the Mac |
| Pipeline | Runs stages per image, resumable, one stage at a time across the library | ingest, embed, ocr, caption | faces, dup/event/semantic clustering |
| Models | Behind interfaces (`Embedder`, `OCREngine`, `Captioner`, `QueryRewriter`) | SigLIP 2, Apple Vision, Qwen-VL (MLX), small text LLM (Ollama) | Face model, reranker, fine-tuned variants |
| Store | Single SQLite file | tables for images, status, vectors, text | faces, persons, events, dup groups |
| Search | `search(query) -> list[Result]` | rewrite, vector + FTS, RRF | people/date/place filters, dedupe, rerank |
| Chat | Receives text, returns images | CLI | WhatsApp Cloud API adapter |
| Ops | Run and supervise | manual CLI | launchd service, Cloudflare Tunnel, backups |

**Memory budget (16GB)**

macOS and services take about 4-5GB. The bulk indexer loads one model at a time and unloads it before the next stage: SigLIP \~1-2GB, a 4-bit VLM \~3-6GB, Vision OCR and InsightFace are small. At query time only the embedder (for the query text) and the small rewrite LLM need to be resident. Benchmark on 200 images before sizing the VLM (D5, D7).

## Data model

One SQLite database. Rules: an image's identity is the SHA-256 of its original bytes (D9); stage results are keyed by `(image_id, stage, version)` (D10); nothing in the DB stores an absolute path to an original.

**MVP tables**

```sql
images(
  id TEXT PRIMARY KEY,          -- sha256 of original bytes
  orig_relpath TEXT NOT NULL,   -- relative to config.originals_root
  derivative_path TEXT,         -- 2048px JPEG on the Mac
  thumb_path TEXT,              -- 256px JPEG
  width INT, height INT,
  taken_at TEXT,                -- EXIF DateTimeOriginal, ISO 8601, nullable
  lat REAL, lon REAL,           -- EXIF GPS, nullable
  phash TEXT,                   -- perceptual hash (for later near-dup grouping)
  created_at TEXT NOT NULL
);

stage_status(
  image_id TEXT, stage TEXT, version TEXT,
  state TEXT,                   -- pending | running | done | failed
  error TEXT, updated_at TEXT,
  PRIMARY KEY (image_id, stage)
);

embeddings(image_id TEXT, model TEXT, vec BLOB);   -- sqlite-vec virtual table
ocr(image_id TEXT PRIMARY KEY, text TEXT);         -- mirrored into an FTS5 table
captions(image_id TEXT PRIMARY KEY, text TEXT, model TEXT);  -- mirrored into FTS5
```

**Reserved for later phases** (not created in the MVP, but nothing in the MVP may conflict with these names)

| Table | Purpose |
| --- | --- |
| `faces(id, image_id, bbox, embedding, person_id, det_score)` | One row per detected face |
| `persons(id, name)` and `person_aliases(person_id, alias)` | Named people; aliases such as "mummy", "mom", "ma" resolve to one person |
| `dup_groups(image_id, group_id, is_representative)` | Near-duplicate and burst grouping |
| `events(id, name, start, end, centroid_lat, centroid_lon)` and `image_events(image_id, event_id)` | Time and GPS clusters such as a trip |
| `semantic_clusters(image_id, cluster_id)` and `cluster_labels(cluster_id, label)` | HDBSCAN clusters over embeddings, labelled by an LLM |
| `sources(id, kind, config)` | Registered image sources |

**Derived-data policy.** Everything except `images` core fields and the user's face names can be rebuilt from the originals. Back up the DB file, but treat person names and aliases as the only irreplaceable human input after the originals.

## Interfaces and extension seams

These contracts are the reason later phases are cheap (D10, D11). The MVP implements each once; later work adds implementations or registers new stages, and does not change callers. Signatures are Python sketches, not final code.

```python
class ImageSource(Protocol):
    def scan(self) -> Iterable[SourceItem]: ...        # path/handle, size, mtime
    def open(self, item: SourceItem) -> BinaryIO: ...   # read-only

class Stage(Protocol):
    name: str
    version: str                                        # bump to force a re-run
    requires: list[str]                                 # e.g. ["ingest"]
    def run(self, image: ImageRecord, ctx: Context) -> None: ...

class Embedder(Protocol):
    def embed_images(self, paths: list[Path]) -> np.ndarray: ...
    def embed_text(self, text: str) -> np.ndarray: ...

class OCREngine(Protocol):
    def read(self, path: Path) -> str: ...

class Captioner(Protocol):
    def caption(self, path: Path) -> str: ...           # English; includes visible text

class QueryRewriter(Protocol):
    def rewrite(self, query: str) -> ParsedQuery: ...   # english, people, date_range, place

class Retriever(Protocol):
    def retrieve(self, q: ParsedQuery, k: int) -> list[Candidate]: ...

def search(query: str, k: int = 5) -> list[Result]: ...   # the one entry point for all chat adapters

class ChatAdapter(Protocol):                            # later: WhatsApp, Telegram
    def on_message(self, text: str, sender: str) -> None: ...
    def send_images(self, to: str, results: list[Result]) -> None: ...
```

**How each extension attaches**

| Later addition | What changes | What does not change |
| --- | --- | --- |
| Syncthing / phone sync | New `ImageSource` implementation plus a mover job (inbox to HDD) | Stages, DB, search |
| WhatsApp bot | New `ChatAdapter` plus a FastAPI webhook that calls `search()` | Search logic, DB |
| Faces | New `faces` stage, new tables, a `PeopleRetriever` or filter in `search()`, alias resolution in the rewriter | Existing stages; queue backfills old images |
| Near-dup / event / semantic clustering | New batch jobs writing the reserved tables; a dedupe post-processor in `search()` | Embeddings, OCR, captions |
| Reranker | New post-processor between fusion and the final top 5 | Retrievers |
| Swapping a model (SigLIP to jina-clip, new VLM) | New implementation behind the interface, new `version` string | Pipeline and search code; only that stage re-runs |
| Originals to external HDD | Change `originals_root` in config | DB rows, because paths are relative (D9) |

**Configuration.** One `config.yaml` holds `originals_root`, `derivatives_root`, `db_path`, model names and per-stage versions. No path or model name appears in code.

**Changes during build**

Deviations from the sketches above, with the reason. The code is the reference for exact signatures.

- **M0 – layout.** `config.yaml`, `tests/`, `eval/` and `docs/` sit at the project root, not inside `imgsearch/`. The originals-protection hook reads `config.yaml` from the root, and tests/eval are not part of the installed package. Relative paths in `config.yaml` resolve against the folder holding it.
- **M0 – one interfaces module.** All protocols and shared dataclasses live in `imgsearch/interfaces.py`. `Context` (not defined in the sketch) holds `config` and the open DB connection `conn`. `Result.signals` is `dict[signal, (rank, score)]`.
- **M0 – `Stage` gains `load(ctx)` and `unload()`.** A stage loads its model once and frees it at the end, so only one heavy model is in memory (16GB limit). `run(image, ctx)` stays per image. The runner skips `load` when there is nothing to do and always calls `unload`, including when `load` or an image fails, so `unload` must cope with a partly loaded stage.
- **M0 – stage versions live in `config.yaml` (`stages:`).** Stage implementations should read their `version` from `config.stages[name]`.
- **M0 – runner re-run rule.** An image is picked when its stage row is missing, in any state other than `done` (failed, or `running` left by a crash), or has a different version. `requires` checks only that the upstream stage is `done`, not its version.
- **M0 – `embeddings` is a plain table** `(image_id, model, vec BLOB, PRIMARY KEY(image_id, model))`, not a sqlite-vec virtual table. sqlite-vec's distance functions scan it, which is fast enough at this library size, and a model swap can change the vector size without a schema change. Revisit with D1 if vector search gets slow.
- **M0 – one FTS5 table** `text_fts(image_id UNINDEXED, kind UNINDEXED, text)` with `kind` = `ocr` or `caption`. Stages write through `store.text.save_ocr` / `save_caption`, which update the base row and the FTS row in one transaction; no stage touches `text_fts` directly.
- **M0 – `text_fts` deletes scan the table.** Replacing a row deletes by `image_id` and `kind`, which are `UNINDEXED` columns, so each delete scans the whole FTS table. Fine at MVP size; revisit in P2 (e.g. key FTS rows by rowid).
- **M0 – schema constraints.** The plan's columns are kept as listed, plus foreign keys to `images(id)`, `NOT NULL` on keys and text columns, and a `CHECK` on `stage_status.state`. Schema changes are numbered `.sql` files in `imgsearch/store/migrations/`, tracked with `PRAGMA user_version` and applied when the DB is opened.
- **M1 – ingest is two steps.** `register_new(source, root, conn)` hashes every file from the `ImageSource` and inserts an `images` row (id, `orig_relpath`, `created_at`) for each new hash. `IngestStage` is then a normal `Stage` run by `run_stage`; it fills in the remaining columns. The runner only works on existing `images` rows, so registration can't be a stage. `imgsearch ingest` runs both. Unreadable files are reported by register; undecodable images are recorded `failed` by the runner.
- **M1 – derivative paths are relative to `derivatives_root`.** `derivative_path` = `derivatives/<id[:2]>/<id>.jpg` and `thumb_path` = `thumbs/<id[:2]>/<id>.jpg`. Like `orig_relpath`, this keeps absolute paths out of the DB, so the derivatives folder can move with one config change. Readers join them with `config.derivatives_root`.
- **M1 – `width`/`height` are the original's size after EXIF orientation**, not the derivative's. `taken_at` falls back to EXIF `DateTime` when `DateTimeOriginal` is missing, and includes the UTC offset when `OffsetTimeOriginal` is set. `phash` is computed from the oriented 2048px derivative.
- **M1 – every run re-hashes every original.** Fine at MVP size (the 100MB test set hashes in well under a second). For P2, skip files whose path, size and mtime match a registered image, especially once originals are on the HDD.
- **M1.1 – derivatives are capped by pixel area, not long edge (changes D8).** A derivative is downscaled only when it is larger than 2048×1536 (≈3.1 MP), keeping the aspect ratio, and is never upscaled. 4:3 photos still come out at 2048×1536. With a 2048 px long-edge cap, the 6656×544 panorama shrank to 167 px tall, too small for OCR, and tall phone screenshots shrank too. It is now 6203×507. 16:9 photos come out at 2364×1330, not 2048×1152. Derivative JPEG quality is 85 (was 90). Disk use is 0.53 MB per image, about 21 GB for 40k photos (D8 estimated 10–15 GB). Stage version `ingest` is `"2"`.
- **M1.1 – `taken_at` is always naive local time** (`YYYY-MM-DDTHH:MM:SS`). Any `OffsetTimeOriginal` is dropped; this replaces the M1 rule of appending the offset. Mixing offset-aware and naive strings would break date sorting and range filters, and users ask by local time ("Diwali evening").

## Query flow

A query takes five steps; each is a separate function so it can be tested alone.

1. **Receive.** The adapter passes raw text (for example "mummy ke saath Goa wali pizza ki photo") to `search()`.
2. **Rewrite (D4).** A small local text LLM returns JSON: `{english: "photo of mom with pizza in Goa", original: "...", people: ["mummy"], date_range: null, place: "Goa"}`. In the MVP only `english` and `original` are used; the other fields are parsed and ignored so later filters need no schema change. The prompt must forbid inventing facts and must return the original text unchanged if the query is already English.
3. **Retrieve.** Vector retriever: embed `english` with the text tower and take the nearest 50 image vectors. Text retriever: FTS5 match over OCR text and captions using both `english` and `original`, so Devanagari or exact words still match. Later retrievers (people, date, place) add candidates or filters.
4. **Fuse (D2).** Reciprocal Rank Fusion with k=60 over the retriever rankings. Later: drop near-duplicates (keep one per `dup_groups` group) and optionally rerank the top 20.
5. **Return.** The top 5 results carry image id, derivative path, caption, and per-signal ranks and scores (so the eval harness and later debugging can see why an image matched). The adapter formats them: CLI prints paths; WhatsApp sends resized images with short captions and offers "send original" if the HDD is mounted.

Expected latency on the M4 is a few seconds, dominated by the rewrite LLM. At \~5 requests/hour no caching is needed.

## Extension roadmap

Phases after the MVP, in the order I recommend, with the reasoning for the order. Each phase should be specified as its own task set when started. This section preserves the larger design so those specs can be checked against it.

| Phase | Adds | Why this order | Design notes |
| --- | --- | --- | --- |
| P1 (MVP) | Index 100 images, CLI query, eval harness | Proves retrieval quality and the Hinglish path before any infrastructure is built | See the MVP sections |
| P2 | Bulk index of the full 100GB archive | Real data exposes scale, memory and quality problems early, and needs no new interfaces | Resumable priority queue (D17); pillow-heif for HEIC; benchmark 200 images first; embeddings + EXIF first, then OCR, then captions. Estimate 1-3 days of background compute for captions; measure before committing |
| P3 | WhatsApp bot (official Cloud API) | The user-facing interface; depends only on `search()` | Meta developer app, free test number (up to 5 registered recipients), system-user token (the default token expires in \~24h), webhook signature check and sender allowlist, Cloudflare Tunnel for a public URL. Send resized images, not originals. Adapter is thin so Telegram can replace it |
| P4 | Originals on external HDD plus Syncthing from the phone camera folder | Operational layer; the DB design already tolerates it (D8, D9, D14) | Syncthing writes to an inbox on the Mac; a mover job hashes, copies to the HDD, verifies, then removes from the inbox. If the drive is unmounted the mover waits; search is unaffected. Plan a second copy of the originals, since one HDD is a single point of failure |
| P5 | Faces | Highest-value feature after text/scene search, but it needs a naming workflow and tuning | `faces` stage with InsightFace buffalo\_l via ONNX Runtime; store bbox, 512-d embedding and crop; HDBSCAN over embeddings; new faces assigned to the nearest named person by centroid, else to "unknown"; the user names clusters through the bot (it sends sample crops, the user replies "Mom"), with a small local page for merge/split; alias table maps "mummy/mom/ma" to one person; the rewriter emits `people`, and a retriever filters by person. Keep face data local and the disk encrypted |
| P6 | Clustering: near-duplicates, then events, then semantic clusters | Near-dups and events are cheap and improve top-5 quality; semantic clusters mostly help browsing | Near-dup: pHash plus embedding similarity above a tight threshold; keep the sharpest as representative; search collapses groups. Events: sort by time, split on large gaps, subdivide by GPS (DBSCAN); an LLM names each event from captions. Semantic: UMAP then HDBSCAN, labelled by an LLM from sampled captions. All are batch jobs run after indexing and updated incrementally |
| P7 | Reranker and fine-tuning | Only worth doing against a measured baseline (D12) | Rerank the top 20 with a cross-encoder over caption/OCR text or a VLM judge. Fine-tune only if the eval shows a clear gap: LoRA on the embedder's text tower or on the reranker, using the user's own query-image pairs collected from the eval set and bot feedback (thumbs up/down) |

**Not planned, but the design allows it:** video (add a keyframe-extraction source and treat frames as images), additional languages for OCR (swap the `OCREngine`), multi-user access (the chat adapter would need identity and per-user libraries, which the current schema does not provide).

## MVP scope, test set and eval

The MVP indexes 100 hand-picked images and answers queries from a CLI, measured by recall@5 against a labelled query set.

**In scope:** local-folder source, ingest (hash, EXIF, derivative, thumbnail), embeddings, OCR, English captions, query rewrite, hybrid search, CLI, eval harness with an HTML contact sheet.

**Out of scope:** phone sync, WhatsApp, external HDD, faces, any clustering, reranking, fine-tuning, FastAPI server. Interfaces for them are defined (see Interfaces) but not implemented.

**Test set (100 images, chosen by the user)**

| Category | Count | Purpose |
| --- | --- | --- |
| Text-heavy (menus, receipts, signboards, screenshots, English) | \~15 | Tests OCR and FTS |
| Near-duplicates and bursts | \~10 | Shows the duplicate problem addressed in P6 |
| Images with people | \~15 | Held for the faces phase |
| Varied scenes: food, travel, pets, documents, indoor, night | \~60 | Tests embeddings and captions |

**Query set (30-40 queries)** stored in `eval/queries.yaml` as `{query, language, expected_image_ids}`. Mix: plain English, Hindi in Devanagari, Hinglish, and text-in-image queries (for example a dish name that appears on a menu). Use image ids (hashes), not filenames.

**Metrics and reports**

- Recall@5 overall and by language and by query type.
- Ablations: vector only, text only, hybrid; with and without the rewrite step.
- Per-query HTML contact sheet showing the top 5, the expected images, and each result's per-signal rank, so misses can be inspected by eye.
- A benchmark note: seconds per image per stage and peak memory for each model.

**MVP exit criteria.** The eval runs end to end and produces the baseline numbers; Hinglish queries retrieve sensible results through the rewrite step; and the benchmark shows whether captions and the chosen model sizes fit in 16GB and are affordable for the full 100GB archive. These numbers become the baseline that later phases must beat.

## MVP tasks for agents

Eight tasks. M2, M3 and M4 are independent once M1 lands and can run in parallel. Every task must leave the repo passing its tests and update `README` with how to run its part.

**Conventions for every task**

- Python 3.12, `uv` for environments, `pytest` for tests, `typer` for the CLI, `pydantic` settings for config. Code under `imgsearch/` as laid out below.
- Read the Decision log and Interfaces sections before starting; do not change an interface without recording the change and reason in this doc.
- Originals are read-only: no task may write to `originals_root`.
- Each stage is idempotent and records `stage_status`; re-running does nothing unless the stage `version` changes.
- No absolute paths or model names in code; use `config.yaml`.
- Each model task includes a short benchmark (seconds per image, peak memory) in `docs/benchmarks.md`.

```
imgsearch/
  config.yaml
  sources/    stages/    models/    store/    search/    cli.py
  eval/       queries.yaml   run_eval.py   contact_sheet.py
  tests/
```

**Task overview**

| Task | Title | Depends on | Output |
| --- | --- | --- | --- |
| M0 | Scaffold, config, schema, stage framework | none | Runnable skeleton, empty DB, `cli status` |
| M1 | Local source and ingest stage | M0 | Hashes, EXIF, derivatives, thumbnails for the 100 images |
| M2 | Embedding stage and vector retriever | M1 | Text query returns nearest images |
| M3 | OCR stage and FTS | M1 | Text-in-image query matches |
| M4 | Caption stage | M1 | English captions in FTS |
| M5 | Hybrid search, fusion, `search()` and CLI | M2, M3 (M4 adds captions to FTS) | Top 5 with per-signal scores |
| M6 | Query rewriter | M5 | Hinglish and Hindi queries work end to end |
| M7 | Eval harness and contact sheet | M2 to start; M5, M6 for full ablations | Baseline recall@5 report |

### M0: Scaffold, config, schema, stage framework

Build the package layout, `config.yaml` loader, SQLite schema (MVP tables from Data model, with sqlite-vec and FTS5 loaded), the `Stage` protocol, a runner that executes one stage across all pending images and records status, and `cli status` showing counts per stage and state.

- Done when: `imgsearch status` runs on an empty DB; a dummy stage can run twice and the second run does nothing; schema migrations are versioned.

### M1: Local source and ingest stage

Implement `LocalFolderSource` and the `ingest` stage: SHA-256, dedupe by hash, EXIF date and GPS, perceptual hash, 2048px long-edge JPEG derivative and 256px thumbnail (HEIC supported), EXIF orientation applied. Originals opened read-only.

- Done when: the 100 test images are ingested; running again adds nothing; a duplicate file produces one row; originals are byte-identical afterwards (checked by test).

### M2: Embedding stage and vector retriever

Implement `Embedder` for SigLIP 2 on `mps`, the `embed` stage (batching, model unloaded at the end), the sqlite-vec table, and `VectorRetriever`. Keep the model name and dimension in config and in the `embeddings.model` column.

- Done when: `imgsearch search-vec "a plate of food"` returns plausible images; swapping the model in config and bumping the version re-embeds only that stage; benchmark recorded.

### M3: OCR stage and FTS

Implement `OCREngine` using Apple Vision (via `ocrmac` or `pyobjc`), the `ocr` stage, the FTS5 mirror, and `TextRetriever` over OCR text. English only (D6).

- Done when: querying a dish name or word that appears on one of the menu or screenshot images returns that image; empty-text images store an empty string, not an error; benchmark recorded.

### M4: Caption stage

Implement `Captioner` using a 4-bit Qwen-VL class model via `mlx-vlm`. The prompt asks for a one-to-two sentence English description plus any visible text verbatim. Store text and model name; mirror into FTS5; extend `TextRetriever` to search captions as well. Model is unloaded after the stage.

- Done when: all 100 images have captions; peak memory stays within the 16GB budget alongside a running desktop; benchmark recorded with a recommendation on whether captions are affordable for 100GB.

### M5: Hybrid search, fusion, `search()` and CLI

Implement `rrf_fuse` (k=60), `search(query, k=5)` returning `Result` objects with image id, paths, caption and per-signal ranks and scores, and `imgsearch search "..."`. At this stage the query is used as given (no rewrite).

- Done when: a CLI query prints the top 5 with the signal that matched each; unit tests cover fusion with fixed rankings and ties.

### M6: Query rewriter

Implement `QueryRewriter` with a separate small text model served by Ollama (D5). Output is validated JSON: `english`, `original`, `people`, `date_range`, `place`. If the model fails or returns invalid JSON, fall back to the original text. `search()` uses `english` for the vector retriever and both `english` and `original` for FTS.

- Done when: at least 10 Hinglish and 5 Devanagari example queries produce sensible English; English queries pass through unchanged; invalid output falls back safely (tested with a stub model).

### M7: Eval harness and contact sheet

Implement `run_eval.py` over `eval/queries.yaml` (the user supplies the 30-40 queries and expected images, or the agent drafts them for the user to confirm), recall@5 overall and by language and query type, ablations (vector only, text only, hybrid, with and without rewrite), and `contact_sheet.py` producing an HTML page per run showing results, expected images and per-signal ranks.

- Done when: one command produces the report and contact sheet; results are stored with the git commit and config used so runs can be compared later.

## Risks, open questions and facts to verify

Model names, library behaviour and platform policies in this doc come from general knowledge as of October 2026 and were not re-checked against current documentation. Verify the items marked below before the task that depends on them.

**Risks**

| Risk | Impact | Mitigation |
| --- | --- | --- |
| 16GB memory limit with a 4-bit VLM plus other services | Captioning slow or crashes | Run stages sequentially; benchmark in M4 before the bulk run; choose VLM size from the benchmark |
| Romanized Hinglish retrieves poorly | Core use case fails | Rewrite step (D4), evaluated with and without in M7 |
| Caption cost for 100GB is too high | Bulk index takes too long | Caption only text-bearing images or skip captions; decide from the M4 benchmark |
| Single external HDD fails | Loss of the only copy of the photos | Plan a second copy of the originals before P4; the index is rebuildable, the photos are not |
| Face recognition quality on family photos | Wrong people in results | Named-person review loop; conservative assignment threshold; "unknown" bucket |
| Face data is sensitive | Privacy | Local only, FileVault, no cloud calls, no face data in logs |
| WhatsApp test-number setup friction | Delays P3 | Keep the chat layer an adapter; Telegram is the fallback |

**Facts to verify before building**

- Apple Vision OCR quality on the user's real English menus and screenshots (M3), and the `ocrmac` package's current maintenance status.
- Which SigLIP 2 and jina-clip-v2 variants run well on `mps`, and their Hindi query quality (M2, M7).
- The best current Qwen-VL class model that `mlx-vlm` supports at 4-bit, and its memory use (M4).
- Current WhatsApp Cloud API rules: test-number recipient limit, 24-hour service window pricing, and permanent token setup (P3).
- Current Google Photos API access limits, if Google Photos is ever considered as a source (D14).
- InsightFace pretrained-model licence terms, which are non-commercial (P5).

**Open questions**

1. Which 100 images and which 30-40 queries form the eval set (user to supply; the agent can draft queries for confirmation).
2. Whether to caption only text-bearing images in the bulk run, pending the M4 benchmark.
3. Where face naming should live: the WhatsApp bot, a local page, or both (decide at P5).
4. SSD instead of HDD for originals if budget allows; the design works with either.
5. Whether a second copy of the originals will exist before the HDD migration.
