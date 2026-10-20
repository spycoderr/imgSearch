"""LocalFolderSource: image files under one folder, opened read-only."""

from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO

from imgsearch.interfaces import SourceItem

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".heic", ".heif"}


class LocalFolderSource:
    def __init__(self, root: Path) -> None:
        self.root = root

    def scan(self) -> Iterator[SourceItem]:
        """Yield image files under `root`, recursively, sorted by path.

        Hidden files and folders (names starting with "."), such as macOS `._*` files,
        are skipped.
        """
        if not self.root.is_dir():
            raise FileNotFoundError(f"originals folder not found: {self.root}")
        for path in sorted(self.root.rglob("*")):
            if any(part.startswith(".") for part in path.relative_to(self.root).parts):
                continue
            if path.suffix.lower() not in IMAGE_SUFFIXES or not path.is_file():
                continue
            stat = path.stat()
            yield SourceItem(path=path, size=stat.st_size, mtime=stat.st_mtime)

    def open(self, item: SourceItem) -> BinaryIO:
        return item.path.open("rb")
