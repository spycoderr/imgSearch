-- P1.7 file names, source folders and dates.
-- source_relpath: the path the photo had inside its inbox (e.g. "Screenshots/Screenshot_1.png"),
--   recorded by `imgsearch import`; NULL for photos registered another way or not backfilled.
-- file_mtime: the original file's modification time, naive local time like taken_at
--   (YYYY-MM-DDTHH:MM:SS). A photo's date for search is taken_at, else file_mtime.
-- text_fts gains rows of kind 'filename' (written by store.text); no schema change needed.

ALTER TABLE images ADD COLUMN source_relpath TEXT;
ALTER TABLE images ADD COLUMN file_mtime TEXT;
