"""Check every image's files: the original by hash, the derivative and thumbnail by decoding.

The original's SHA-256 must equal the image id. Derivatives and thumbnails have no stored
hash, so each must exist and decode as a complete image.
"""

import sqlite3
from dataclasses import dataclass, field

from PIL import Image

from imgsearch.config import Config
from imgsearch.sources.importer import sha256_file


@dataclass
class VerifyResult:
    images: int = 0
    ok: int = 0
    problems: list[tuple[str, str]] = field(default_factory=list)  # (orig_relpath, problem)


def verify(config: Config, conn: sqlite3.Connection) -> VerifyResult:
    result = VerifyResult()
    rows = conn.execute(
        "SELECT id, orig_relpath, derivative_path, thumb_path FROM images ORDER BY orig_relpath"
    ).fetchall()
    for row in rows:
        result.images += 1
        problems = _check(config, row)
        result.problems += [(row["orig_relpath"], p) for p in problems]
        if not problems:
            result.ok += 1
    return result


def _check(config: Config, row: sqlite3.Row) -> list[str]:
    problems = []
    original = config.originals_root / row["orig_relpath"]
    if not original.is_file():
        problems.append(f"original missing: {original}")
    elif sha256_file(original) != row["id"]:
        problems.append(f"original changed (sha256 differs from the id): {original}")
    for kind, root, relpath in (
        ("derivative", config.derivatives_root, row["derivative_path"]),
        ("thumbnail", config.thumbs_root, row["thumb_path"]),
    ):
        if relpath is None:
            problems.append(f"no {kind} yet (run `imgsearch update`)")
            continue
        path = root / relpath
        if not path.is_file():
            problems.append(f"{kind} missing: {path}")
            continue
        try:
            with Image.open(path) as img:
                img.load()
        except OSError as e:  # truncated or not an image
            problems.append(f"{kind} unreadable: {path}: {e}")
    return problems
