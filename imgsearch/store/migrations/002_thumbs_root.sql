-- P1.5-A storage layout (plan D18). Thumbnails move to their own root on the Mac
-- (config thumbs_root) and derivatives get a root of their own on the pen drive, so both
-- paths drop their leading folder: "thumbs/ab/<id>.jpg" -> "ab/<id>.jpg" (relative to
-- thumbs_root), "derivatives/ab/<id>.jpg" -> "ab/<id>.jpg" (relative to derivatives_root).

UPDATE images SET thumb_path = substr(thumb_path, length('thumbs/') + 1)
WHERE thumb_path LIKE 'thumbs/%';

UPDATE images SET derivative_path = substr(derivative_path, length('derivatives/') + 1)
WHERE derivative_path LIKE 'derivatives/%';
