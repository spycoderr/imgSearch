"""File-name stage: index the words in each photo's file name and source folder (no model).

Names carry meaning for documents and screenshots ("Signature Nilabh black_1 (1).jpg",
"Screenshot_20260327-171258_Gmail.png"), and the phone folder ("Screenshots", "WhatsApp
Images") is lost when originals are filed under YYYY/MM. The words go into `text_fts` as a
row of kind `filename`, so the text retriever finds them like OCR text and captions.
"""

import re
import unicodedata
from pathlib import PurePosixPath

from imgsearch.config import Config
from imgsearch.interfaces import Context, ImageRecord
from imgsearch.store.text import save_filename_text

# Camera and app prefixes that say nothing about the photo.
CAMERA_PREFIXES = frozenset({"img", "dsc", "pxl", "hdr", "wa", "vid", "portrait"})

# Letter↔digit boundaries ("WA0034", "5GHz"), then camelCase inside a run of letters:
# lower→Upper ("StarRail") and the end of a capital run before a capitalised word ("QRCode").
_DIGIT_BOUNDARY = re.compile(r"(?<=[^\W\d_])(?=\d)|(?<=\d)(?=[^\W\d_])")
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def filename_words(relpath: str) -> list[str]:
    """Lowercase search words from a relative path: its folders and its file name without
    the extension.

    The text is split on anything that isn't a letter, digit or combining mark, then on
    letter/digit and camelCase boundaries. A camelCase word is also kept whole
    ("WhatsApp" gives whatsapp, whats, app). Dropped: pure-digit tokens, single
    characters and CAMERA_PREFIXES. Duplicates are dropped, keeping first-seen order.
    """
    path = PurePosixPath(relpath)
    text = " ".join([*path.parent.parts, path.stem])
    kept = "".join(c if unicodedata.category(c)[0] in "LNM" else " " for c in text)
    words: list[str] = []
    for chunk in kept.split():
        for run in _DIGIT_BOUNDARY.split(chunk):
            parts = _CAMEL_BOUNDARY.split(run)
            if len(parts) > 1:
                words.append(run)
            words.extend(parts)
    keep = (
        w.lower()
        for w in words
        if not w.isdigit() and len(w) > 1 and w.lower() not in CAMERA_PREFIXES
    )
    return list(dict.fromkeys(keep))


class FilenameStage:
    """Writes one `filename` row per image: words from its inbox path (`source_relpath`,
    which keeps the phone folder) if known, else from `orig_relpath`."""

    name = "filename"
    requires: list[str] = []

    def __init__(self, config: Config) -> None:
        self.version = config.stages[self.name]

    def load(self, ctx: Context) -> None:
        pass  # no model

    def unload(self) -> None:
        pass

    def run(self, image: ImageRecord, ctx: Context) -> None:
        row = ctx.conn.execute(
            "SELECT source_relpath FROM images WHERE id = ?", (image.id,)
        ).fetchone()
        relpath = row["source_relpath"] or image.orig_relpath
        save_filename_text(ctx.conn, image.id, " ".join(filename_words(relpath)))
