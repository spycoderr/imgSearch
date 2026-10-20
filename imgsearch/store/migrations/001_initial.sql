-- MVP schema. See the plan's "Data model" and "Changes during build".

CREATE TABLE images (
  id TEXT PRIMARY KEY,           -- sha256 of original bytes
  orig_relpath TEXT NOT NULL,    -- relative to config.originals_root
  derivative_path TEXT,          -- 2048px JPEG on the Mac
  thumb_path TEXT,               -- 256px JPEG
  width INT,
  height INT,
  taken_at TEXT,                 -- EXIF DateTimeOriginal, ISO 8601
  lat REAL,
  lon REAL,
  phash TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE stage_status (
  image_id TEXT NOT NULL REFERENCES images(id),
  stage TEXT NOT NULL,
  version TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('pending', 'running', 'done', 'failed')),
  error TEXT,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (image_id, stage)
);

-- Plain table, not a vec0 virtual table: sqlite-vec's distance functions scan it,
-- and a model swap can change the vector size without a schema change.
CREATE TABLE embeddings (
  image_id TEXT NOT NULL REFERENCES images(id),
  model TEXT NOT NULL,
  vec BLOB NOT NULL,             -- float32, as written by sqlite_vec.serialize_float32
  PRIMARY KEY (image_id, model)
);

CREATE TABLE ocr (
  image_id TEXT PRIMARY KEY REFERENCES images(id),
  text TEXT NOT NULL             -- empty string when the image has no text
);

CREATE TABLE captions (
  image_id TEXT PRIMARY KEY REFERENCES images(id),
  text TEXT NOT NULL,
  model TEXT NOT NULL
);

-- Full-text index over OCR text and captions. Written only by store.text.
CREATE VIRTUAL TABLE text_fts USING fts5 (
  image_id UNINDEXED,
  kind UNINDEXED,                -- 'ocr' or 'caption'
  text
);
