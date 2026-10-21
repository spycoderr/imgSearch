-- P1.6 cloud originals (plan D19). One row per original with an S3 copy, and the
-- pen-drive cache state of each original. Neither table touches `images`.

CREATE TABLE s3_copies (
  image_id TEXT PRIMARY KEY REFERENCES images(id),
  object_key TEXT NOT NULL,          -- originals/<image id>
  version_id TEXT NOT NULL,          -- the S3 version holding this upload
  encrypted_sha256 TEXT NOT NULL,    -- hex SHA-256 of the encrypted object
  size INTEGER NOT NULL,             -- bytes of the encrypted object
  orig_mtime_ns INTEGER NOT NULL,    -- the original's mtime, restored by `imgsearch fetch`
  uploaded_at TEXT NOT NULL,
  verified_at TEXT                   -- set once S3 reports the same size and checksum
);

-- No row means the original is on the pen drive and was never accessed through `fetch`
-- (its LRU time is then images.created_at).
CREATE TABLE original_cache (
  image_id TEXT PRIMARY KEY REFERENCES images(id),
  last_access TEXT NOT NULL,         -- ISO 8601 UTC
  evicted_at TEXT                    -- set when `imgsearch evict` removed it; NULL when on the drive
);
