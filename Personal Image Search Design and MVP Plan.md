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
| D18 | Storage layout (revised 2026-10-20): the **pen drive** (64 GB, exFAT, unencrypted by the user's choice; its path is `IMGSEARCH_STORAGE_ROOT` in the git-ignored `.env`) holds the originals cache and staging area plus the 2048px derivatives; the **Mac** keeps the SQLite DB, the 256px thumbnails and the Syncthing inbox | Everything on the Mac; a 2 TB disk first; encrypted disk image on the pen drive | The Mac has ~32 GB free. Search reads only the DB (vectors and text), so it works with the drive unplugged; thumbnails stay on the Mac so results still show previews. SQLite never lives on the USB stick (exFAT has no journaling; a pulled drive could corrupt it). Commands that need the drive fail loudly when it isn't mounted. Always eject before unplugging |
| D19 | Originals' primary home is **S3 Glacier Instant Retrieval**, encrypted on the Mac before upload, with bucket versioning and MFA on the account (revised 2026-10-20). The pen drive keeps a least-recently-used cache of originals and evicts only originals whose S3 copy is verified. Deep Archive in a second region is an optional extra copy | Deep Archive as primary (12–48 h per fetch); a local disk as primary; AWS-hosted search | Search never reads originals, so cloud storage adds no search latency. Instant Retrieval returns an original in ~1–2 s (home internet) for ~$0.40/month per 100 GB, so 'send the original' stays usable; Deep Archive would make it next-day. 100 GB never has to fit locally: Takeout can be processed one zip at a time. Storage leaves the Mac (encrypted); models still run only on the Mac. Losing AWS access is the residual risk: versioning + MFA, and the optional Deep Archive copy |
| D20 | Phone uploads with Syncthing (Android, Syncthing-Fork), home Wi-Fi only, LAN discovery only (no global discovery, relays or NAT traversal) (added 2026-10-20) | FTP/SFTP with an auto-upload app; uploads over mobile data | Automatic, resumable and encrypted in transit; nothing exposed to the internet. Phone folder is send-only, Mac folder receive-only, so the Mac can never change or delete photos on the phone. Refines D14 |
| D21 | Models are loaded only while needed: `imgsearch search` loads SigLIP per command and exits; Ollama unloads the rewriter after `keep_alive` (default 5 min) of no queries; the P3 long-running service must unload SigLIP after an idle timeout too (added 2026-10-20) | Keep every model resident | The user wants the Mac's RAM free most of the time. Cost is a cold start (~5–9 s) on the first query after idle |

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
- **M3 – test injection for stages (applies to every model stage).** A stage that uses a model takes an optional constructor argument that supplies the model (an `OCREngine`, or a callable that builds the embedder or captioner); when it is omitted, `load(ctx)` builds the real one from `config`. Tests pass a small fake that satisfies the protocol, so unit tests need no real model and no monkeypatching. `imgsearch run <stage>` always builds stages as `cls(config)`, i.e. with the real model. M2 (`EmbedStage`) and M4 (`CaptionStage`) follow the same convention.
- **M3 – `imgsearch run <stage>`.** One generic command runs any stage listed in the `STAGES` dict in `cli.py` (name -> class, built as `cls(config)`). Ingest is not listed, since `imgsearch ingest` must also register new files first.
- **M3 – OCR settings in `models.ocr`** (`recognition_level`, `languages`), although Vision has no model name. Known gap (accepted for the MVP): ocrmac's `text_from_image` returns no lines, without raising, when the Vision request fails, so such an image is stored as `""` and marked `done`. The M3 report records the empty-text count (61 of 130) so a later jump is visible. Fixing it means calling Vision through pyobjc directly.
- **M3 – `TextRetriever` runs one FTS query** over the words of both `english` and `original` (not one query per field), deduplicated by image keeping its best bm25 row. `Candidate.score` is `-bm25`, so higher is better. User text is split into words (Unicode letters, digits and combining marks), each word is quoted and the words are joined with `OR`. A fixed list of English stopwords and photo words (`photo`, `picture`, `image`, `pic`, plurals) is dropped first, because captions ("This photo shows...") would otherwise match almost any query; a query of only such words returns nothing. No stemming (default `unicode61` tokenizer).
- **M2 – SigLIP 2 text settings in `models.embedder`.** New key `text_max_length: 64`. Query text is lowercased and tokenized with `padding="max_length"`, `max_length=64`, `truncation=True`. The model card is silent on this; the source is the transformers SigLIP2 docs and modeling code. The checkpoint's tokenizer has `model_max_length` ≈ 1e30 and does not lowercase, so both must be done explicitly. The `"This is a photo of {label}."` template from the docs is not used: it is for zero-shot classification, not retrieval.
- **M2 – one image per `embed_images` call, no batching.** As decided in M0 (`run(image, ctx)` is per image). At 0.06 s per image, 40k photos take about 40 minutes, so batching isn't needed for P2.
- **M2 – `EmbedStage(config, make_embedder=...)`** follows the M3 test-injection convention. The default builder imports `SiglipEmbedder` (torch, transformers) lazily, and `unload()` imports torch lazily, so the CLI (`imgsearch status`, which lists `EmbedStage` in `STAGES`) doesn't pay seconds of import time.
- **M2 – `VectorRetriever(conn, embedder, embedder_config)`.** `Candidate.score` is cosine similarity (1 − `vec_distance_cosine`), so higher is better; ties are broken by `image_id`. Only rows whose `model` equals `models.embedder.name` are ranked, so vectors from a previous model are ignored.
- **M2 – stage version `embed` is `"2"`.** The real run was at `"1"`; it was bumped once to check that only the embed stage re-runs (acceptance item 10). Vectors are unchanged.
- **M4 – caption images are capped by pixel area, not long edge.** Before captioning, each derivative is downscaled to at most `models.captioner.max_pixels` = 1024×1024 ≈ 1 MP, keeping the aspect ratio (never upscaled). Qwen3-VL uses about one token per 32×32 px, so this caps a prompt at about 1k image tokens. A 1024 px long-edge cap would shrink the 6203×507 panorama to 1024×83; the area cap gives 3581×292 at the same token budget. Same reasoning as M1.1 (R5).
- **M4 – caption settings in `models.captioner`:** `prompt`, `max_tokens: 100` and `max_pixels`. Decoding is greedy (`temperature=0.0` passed explicitly, because the checkpoint's `generation_config.json` samples at 0.7). Only chat-template tokens (`<|...|>`) and a leading "Caption:" are stripped from the output; filler such as "No readable text is present." is kept (supervisor decision Q4) and its rate is reported in `docs/reports/M4.md`.
- **M4 – `CaptionStage(config, load_captioner=...)`** follows the M3 test-injection convention. The default loader imports mlx-vlm (≈3 s) lazily, so the CLI doesn't pay for it. `MlxCaptioner.unload()` drops the model, runs `gc.collect()` and `mx.clear_cache()`.
- **M6 – rewriter model is `qwen2.5:7b`, not `qwen2.5:3b` (D5).** With the same prompt, the 20 example queries scored 15 correct / 4 minor / 1 wrong on `qwen2.5:7b`, 11 / 8 / 1 on `gemma3:4b` and 9 / 6 / 5 on `qwen2.5:3b` (hand-scored; table in `docs/reports/M6.md`). The 7b model is warm in about 1.7 s, within the 3 s limit set by the supervisor, but holds about 4.6 GB while loaded (the 3b held 2.1 GB). Query time then needs the embedder plus about 4.6 GB, so the bot must not query while the caption stage (about 4–6 GB) runs.
- **M6 – Ollama upgraded from 0.17.4 to 0.40.6** (`brew upgrade ollama`, approved by the user), because 0.17.4 refuses to pull `gemma3:4b` ("requires a newer version of Ollama"). The `/api/chat` request shape is unchanged. The same model and prompt give different outputs on the new version: 13 of the 20 `qwen2.5:3b` rewrites changed (all 5 English queries stayed the same). Outputs are repeatable within one Ollama version, not across versions.
- **M6 – rewriter settings in `models.rewriter`:** `timeout_s` and `prompt` (the system prompt) join `name` and `url`. The request is `POST {url}/api/chat` with `stream: false`, `options.temperature: 0` and `format` set to the JSON schema of the pydantic output model; `keep_alive` is not sent, so Ollama's default applies.
- **M6 – `original` is not in the schema sent to the model** (supervisor decision Q7). Code always sets `ParsedQuery.original` to the exact input, so asking the model to echo it would only cost tokens and invite changes. The model-facing schema has `english`, `people`, `place` and `date_range` (`{start, end}` ISO dates, converted to the `(start, end)` tuple of `ParsedQuery`).
- **M6 – `OllamaRewriter(config, client=None)`** takes an optional `httpx.Client`, so tests pass one with `httpx.MockTransport`. Same idea as the stages' test-injection convention.
- **M5 – `search()` is `Searcher.search`, built once.** `open_searcher(config, conn, signals=("vector", "text"), rewrite=True)` loads the embedder and creates the rewriter once, building only what `signals` and `rewrite` need. Then `searcher.search(query, k=None, rewrite=True, signals=None)` runs per query. It returns a `SearchResponse` (the `ParsedQuery` that was searched, `rewrite_used`, the signals used, `results: list[Result]`, and rewrite/retrieval/total timings), not a bare `list[Result]`. Reasons: models must load once per process, because the M7 eval calls search hundreds of times. Callers (CLI, eval, later the bot) also need to show what was searched. `rewrite` and `signals` are plain parameters for M7's ablations. Code is in `imgsearch/search/hybrid.py`. `Searcher(conn, search_config, rewriter, retrievers)` takes its parts directly, so tests pass fakes.
- **M5 – `Result` gains `orig_relpath` and `score`.** `score` is the fused RRF score. `orig_relpath` is for printing and for a later "send original". `derivative_path` and `thumb_path` stay relative to `derivatives_root`, as stored.
- **M5 – `ParsedQuery` gains `rewrite_error: str | None`.** The rewriter still never raises. On fallback it sets a one-line reason (e.g. `ConnectError: connection refused`), so the CLI can say the rewrite was skipped, and the eval can tell a failed rewrite from an English query passed through unchanged.
- **M5 – rewriter date guard (review W1).** `date_range` is set to None in code unless the query contains a year from 1900 to 2099, or a numeric day/month (`15/08`, `3-4-21`, `15.8.19`). Digits of any script count. The model invents dates for relative words ("kal" became 2025-10-20; "pichle hafte" became the past week). The prompt has one `{today}` placeholder, required by `RewriterConfig` and filled with today's ISO date in code, so relative dates can be resolved later. Dates with a month name ("15 August") aren't recognised yet.
- **M5 – RRF ties.** `rrf_fuse(rankings, k)` sums each image's terms in rank order. It compares scores rounded to 12 decimal places, then the best single rank, then `image_id`.
- **M5 – `search-vec` and `search-text` removed (review W4).** `imgsearch search --signals vector|text --no-rewrite` replaces them, with one printer (`imgsearch/search/show.py`). `best_snippet` was removed with them.
- **M7 – layout: eval code in `imgsearch/eval/`, eval data in `eval/`.** The plan's sketch puts `run_eval.py` and `contact_sheet.py` in `eval/`. They are package modules instead (`queries.py` loader, `metrics.py`, `run.py`, `tables.py`, `html.py`, `sheets.py`, `main.py`), run with `imgsearch eval`, `imgsearch eval-compare A B` and `imgsearch eval-review`. That way they import `imgsearch` without `sys.path` edits, and the tests cover them. `eval/` keeps `queries.yaml`, `rewrite_examples.yaml` and the git-ignored `runs/`. New config section `eval: {queries, runs_dir}`; its paths resolve against the config file's folder.
- **M7 – query file fields.** Each query is `{id, query, language, type, expected_image_ids, note}`. `language` is one of english, hinglish or hindi. `type` is one of scene, object, text, place or hard. The loader rejects unknown fields and values, unknown or duplicate image ids, empty lists and duplicate query ids.
- **M7 – metrics.** hit@5; recall@5 = expected found in the top 5 ÷ min(5, number expected), so bursts with more than 5 photos can reach 1.0; and reciprocal rank of the first expected image within the top 5 (0 if none). Each is reported overall, by language and by type.
- **M7 – ablations.** vector, text and hybrid, each with and without the rewrite, plus "hybrid+rw no captions", which uses a `TextRetriever(conn, kinds=("ocr",))`. `TextRetriever` gained `kinds` (default: all). bm25's statistics still come from the whole FTS table. One `Searcher` serves every configuration. Its rewriter is wrapped in a per-run cache (`CachedRewriter`), so each query is rewritten once and every rewrite-on configuration sees the same rewrite.
- **M7 – run folder** `eval/runs/<YYYYmmdd-HHMMSS>/` holds `results.json` (one record per query and configuration), `metrics.json`, `meta.json` (commit, dirty flag, query-file hash, model names, Ollama version, timings, rewrite failures), a copy of `config.yaml`, and `contact_sheet.html`. The contact sheet embeds each thumbnail once, as a base64 CSS class, so it works offline and stays a few MB. Runs contain personal photos and are never committed.
- **M7 – quiet model loading (review N1).** `SiglipEmbedder` sets transformers' logging to errors only and turns off its progress bar. `imgsearch/models/__init__.py` raises the one torch logger that prints "Redirects are currently not supported in MacOs" to ERROR. Errors still show.
- **M7.1 – default search signals come from config: `search.default_signals: [vector]`.** `open_searcher(config, conn, signals=None)` and `imgsearch search` without `--signals` use it, so every adapter (CLI, later WhatsApp) shares one default. `--signals vector,text` runs hybrid. Reason: on the M7 baseline, vector + rewrite (recall@5 0.98) beat hybrid + rewrite (0.87, or 0.93 at `per_retriever_k` 10) and was never worse on any query (`docs/mvp-summary.md`, "Decisions after the MVP"). This changes the plan's query flow step 3: the text retriever now runs only when asked. The eval builds every retriever explicitly (`open_searcher(..., signals=SIGNALS)`), so its configurations are unchanged.
- **M7.1 – `search.per_retriever_k` is 10** (was 50, the plan's "nearest 50"). With 130 photos, a 50-deep list covers 38% of the library, so RRF rewards weak matches that appear in both lists. 10 was best for every hybrid configuration in the M7 experiment. Vector-only results don't depend on it. Re-measure on the P2 library.
- **M7.1 – captions stay a stage, run last in P2.** In the P2 bulk run, ingest → embed → ocr come first and captions run afterwards in the background (D17). Search doesn't need them.
- **P1.5-A – storage roots (D18).** Config gains `thumbs_root` (Mac) and `inboxes` (a list, Mac). `originals_root` and `derivatives_root` are `${IMGSEARCH_STORAGE_ROOT}/originals` and `/derivatives`; the variable comes from the environment, else from the git-ignored `.env` in the project root (`.env.example` is committed), so no committed file holds a machine path. `${VAR}` is expanded in path fields only, and an unset variable is an error naming it. `db_path` under `/Volumes/` is rejected. The originals-protection hook expands the same variable; if it can't, it protects the unexpanded path and `data/originals` and says why.
- **P1.5-A – derivative and thumbnail paths (changes the M1 entry).** `derivative_path` is relative to `derivatives_root` and `thumb_path` to `thumbs_root`, both `<id[:2]>/<id>.jpg` (the leading `derivatives/` and `thumbs/` folders are gone, since each now has its own root). Migration `002_thumbs_root.sql` rewrote the existing rows. `Result.thumb_path` is now relative to `thumbs_root`.
- **P1.5-A – pen-drive check.** `imgsearch/store/roots.py`: a root under `/Volumes/<name>` is available only if `/Volumes/<name>` is a mount point. `import`, `update`, `ingest`, `verify` and `run <stage>` check before doing anything and exit 1 with "pen drive not mounted: …". `search` and `status` don't need the drive; `search` says full-size images are unavailable.
- **P1.5-A – `imgsearch import`** (`imgsearch/sources/importer.py`) is the mover job of the "Syncthing / phone sync" seam. It scans each inbox with `LocalFolderSource` (no new `ImageSource`), skips files changed in the last 60 s, and registers rows itself, so `update` doesn't re-scan `originals_root` (D-b). Two additions to the acceptance rules: a duplicate is removed from the inbox only if its registered original exists (else it's a failure and stays), and a target that already holds the same bytes (left by an interrupted run) is reused. The copy keeps the source mtime. `import_inbox(config, conn, write_copy=...)` takes the copy function for tests (S4 convention).
- **P1.5-A – `imgsearch update`** = import, then `IngestStage`, `EmbedStage`, `OcrStage` through `run_stage`. No captions. `imgsearch ingest` (scan all of `originals_root`) still exists.
- **P1.5-A – `imgsearch verify`** checks the original by SHA-256 against the id. Derivatives and thumbnails have no stored hash, so it checks that they exist and decode fully. The one-off move of the 130 derivatives and thumbnails was checked by hash against the old copies (`docs/reports/P1.5-A.md`).
- **P1.6 – `cli.py` split into command modules (review A5).** `imgsearch/commands/` holds `common.py`, `index.py`, `query.py`, `evaluate.py` and `cloud.py`; `cli.py` only registers them. Behaviour unchanged.
- **P1.6 – `ObjectStore` protocol (new seam).** `interfaces.py` gains `ObjectStore` (`put(key, data, sha256, metadata) -> version id`, `get(key, version_id)`, `head(key, version_id) -> ObjectInfo | None`), `ObjectInfo` and `StoreUnavailable`. Nothing deletes: the IAM user can't, and a put to a used key adds a version. `S3ObjectStore` (boto3) is the one implementation; tests use an in-memory fake. Network and credential errors become `StoreUnavailable`, so callers can say "offline".
- **P1.6 – `in_s3` is a table, not a column.** Migration 003 adds `s3_copies` (image id, object key, version id, encrypted SHA-256, size, `orig_mtime_ns`, uploaded_at, verified_at) and `original_cache` (image id, last_access, evicted_at). `images` is unchanged. `orig_mtime_ns` lets `fetch` restore the file's mtime. A copy counts as in S3 only once `verified_at` is set, after `head` of the exact version returns the same size and S3-computed SHA-256.
- **P1.6 – encryption format.** AES-256-GCM (`cryptography`), each blob `b"IMGS"` + format version byte + 12-byte random nonce, then ciphertext and tag; the header is authenticated data. The 256-bit key is in the macOS Keychain (`security`, service `s3.keychain_service`), made once by `imgsearch s3-key create`; `s3-key export` is for the user's own Terminal.
- **P1.6 – S3 settings.** Bucket and profile come from `.env` (`IMGSEARCH_S3_BUCKET`, `AWS_PROFILE`); `config.yaml` `s3:` holds `storage_class: GLACIER_IR`, `originals_prefix`, `db_key` and the Keychain names; `storage.cache_max_gb` is the pen-drive cap (20 GB). Objects are single `PutObject` calls (no multipart) with a SHA-256 checksum, fine for photos; P2 videos may need multipart.
- **P1.6 – the DB copy.** `imgsearch upload` ends by uploading the DB (SQLite backup API into memory, encrypted) to `s3.db_key`, with the plaintext SHA-256 as object metadata; it is skipped when unchanged since the latest version.
- **P1.6 – `update` = import → upload → ingest → embed → ocr.** If upload can't run, the rest still runs and the summary prints how many originals are not yet in S3.
- **P1.6 – eviction and fetch.** `imgsearch evict [--dry-run] [--cap-gb N]` removes least-recently-used originals (by `original_cache.last_access`, else `images.created_at`) until under the cap, only if the S3 copy is verified (re-checked by `head` just before) and ingest is done. `imgsearch fetch <id|relpath>` downloads the recorded version, checks both hashes, writes via a temp file and rename (never over an existing file), restores the mtime and updates `last_access`. `verify` skips evicted originals but still checks their derivative and thumbnail. `imgsearch restore-check --sample N` downloads and checks without writing.

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
| P1.5 | Getting photos in. **A:** `imgsearch import` (inbox → pen-drive originals, verify, clear the inbox) and `imgsearch update` (import → ingest → embed → ocr), with the D18 layout. **B:** Google Takeout import, reading its JSON dates/GPS | The user wants intake before the bulk run; P2 needs a way to bring 100 GB in | Moved forward from P4. Phone uploads via Syncthing (D20) or any app that drops files in the inbox. Captions stay a separate, non-blocking background job (decided after M7) |
| P1.6 | Cloud originals (D19): encrypt + upload new originals to S3 Glacier Instant Retrieval, verify, record `in_s3`; LRU eviction on the pen drive above a size cap; fetch-on-demand for 'send original' and re-ingest | Must exist before the full Takeout import, which can't fit on the pen drive | Client-side encryption; objects named by image id, so S3 never sees file names; a restore check against the DB; credentials outside the repo; optional Deep Archive copy |
| P2 | Bulk index of the full 100GB archive | Real data exposes scale, memory and quality problems early, and needs no new interfaces | Resumable priority queue (D17); pillow-heif for HEIC; benchmark 200 images first; embeddings + EXIF first, then OCR, then captions. Estimate 1-3 days of background compute for captions; measure before committing |
| P3 | WhatsApp bot (official Cloud API) | The user-facing interface; depends only on `search()` | Meta developer app, free test number (up to 5 registered recipients), system-user token (the default token expires in \~24h), webhook signature check and sender allowlist, Cloudflare Tunnel for a public URL. Send resized images, not originals. Adapter is thin so Telegram can replace it |
| P4 | Originals moved from the pen drive to a 2 TB external disk (Syncthing and the mover moved to P1.5) | Operational layer; the DB design already tolerates it (D8, D9, D14) | Copy, verify, change `originals_root`. Format the disk APFS (Encrypted). The S3 copy (P1.6) is the second copy |
| P4b | Reformat the pen drive to APFS (added 2026-10-21; deferred by the user) | exFAT makes macOS write a hidden `._` file per file (for its `com.apple.provenance` attribute) in 128 KiB clusters: measured +0.45 MB per photo, ~18 GB at 40k photos. APFS also adds journaling and optional encryption | Only after P1.6 has a verified S3 copy of every original, or after copying the storage folder off the drive: the drive holds the only Mac-side copy of imported photos. Then format, copy back, update `IMGSEARCH_STORAGE_ROOT` if the volume name changes, run `imgsearch verify` |
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
