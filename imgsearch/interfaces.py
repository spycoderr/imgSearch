"""Shared types and interfaces from the plan's "Interfaces and extension seams" section.

Only protocols and data types live here; implementations go in sources/, stages/,
models/ and search/.
"""

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Protocol

import numpy as np

from imgsearch.config import Config

# ---- Data types ----


@dataclass(frozen=True)
class SourceItem:
    """One image file offered by an ImageSource."""

    path: Path
    size: int
    mtime: float


@dataclass(frozen=True)
class ImageRecord:
    """One row of the `images` table."""

    id: str  # sha256 of the original bytes
    orig_relpath: str  # relative to config.originals_root
    derivative_path: str | None
    thumb_path: str | None
    width: int | None
    height: int | None
    taken_at: str | None  # ISO 8601
    lat: float | None
    lon: float | None
    phash: str | None
    created_at: str


@dataclass(frozen=True)
class PlaceFilter:
    """The places in the library a query's `place` resolved to (P1.8)."""

    label: str  # the place as the query named it, e.g. "Jaipur"
    geonameids: tuple[int, ...]  # GeoNames ids of the matching places (any level)
    names: tuple[str, ...]  # names searched in OCR text, captions and file names


@dataclass(frozen=True)
class ParsedQuery:
    """Output of the query rewriter, plus the place filter `Searcher` resolves from `place`.
    Retrievers must honour `date_range` and `place_filter` (P1.7, P1.8)."""

    english: str
    original: str
    people: list[str] = field(default_factory=list)
    date_range: tuple[str, str] | None = None  # ISO 8601 start, end
    place: str | None = None
    rewrite_error: str | None = None  # set when the rewrite failed and the query is used as given
    place_filter: PlaceFilter | None = None  # set by Searcher, not by the rewriter


@dataclass(frozen=True)
class Candidate:
    """One image returned by one retriever."""

    image_id: str
    rank: int  # 1 = best
    score: float
    signal: str  # which retriever produced it, e.g. "vector" or "text"


@dataclass(frozen=True)
class Result:
    """One search result handed to a chat adapter. Paths are relative, as stored in the DB."""

    image_id: str
    orig_relpath: str  # relative to config.originals_root
    derivative_path: str | None  # relative to config.derivatives_root
    thumb_path: str | None
    caption: str | None
    score: float  # fused RRF score, higher is better
    signals: dict[str, tuple[int, float]]  # signal -> (rank, score) in that retriever
    date: str | None = None  # search date: EXIF taken_at, else the file's mtime (P1.7)
    place: str | None = None  # where it was taken, e.g. "Amer, Jaipur, Rajasthan" (P1.8)


@dataclass(frozen=True)
class Context:
    """What a stage gets besides the image: settings and the open DB."""

    config: Config
    conn: sqlite3.Connection


@dataclass(frozen=True)
class ObjectInfo:
    """What an ObjectStore reports about one stored object version."""

    size: int
    version_id: str
    sha256: str  # hex SHA-256 of the stored bytes, as checked by the store
    metadata: dict[str, str] = field(default_factory=dict)


class StoreUnavailable(Exception):
    """The object store can't be reached (offline, no credentials). Nothing was stored."""


# ---- Protocols ----


class ImageSource(Protocol):
    def scan(self) -> Iterable[SourceItem]: ...
    def open(self, item: SourceItem) -> BinaryIO: ...  # read-only


class Stage(Protocol):
    name: str
    version: str  # bump to force a re-run
    requires: list[str]  # stages that must be done first, e.g. ["ingest"]

    def load(self, ctx: Context) -> None: ...  # load models once, before the first image
    def run(self, image: ImageRecord, ctx: Context) -> None: ...
    def unload(self) -> None: ...  # free memory; also called if load or run failed


class Embedder(Protocol):
    def embed_images(self, paths: list[Path]) -> np.ndarray: ...
    def embed_text(self, text: str) -> np.ndarray: ...


class OCREngine(Protocol):
    def read(self, path: Path) -> str: ...


class Captioner(Protocol):
    def caption(self, path: Path) -> str: ...  # English; includes visible text


class QueryRewriter(Protocol):
    def rewrite(self, query: str) -> ParsedQuery: ...


class Retriever(Protocol):
    def retrieve(self, q: ParsedQuery, k: int) -> list[Candidate]: ...


class ObjectStore(Protocol):
    """Versioned object storage (P1.6: S3). Nothing ever deletes; a put to a used key adds
    a new version. Methods raise StoreUnavailable when the store can't be reached."""

    def put(
        self, key: str, data: bytes, sha256: str, metadata: dict[str, str] | None = None
    ) -> str: ...  # returns the new version id; rejected if `data` doesn't hash to `sha256`
    def get(self, key: str, version_id: str | None = None) -> bytes: ...
    def head(
        self, key: str, version_id: str | None = None
    ) -> ObjectInfo | None: ...  # None: absent


class ChatAdapter(Protocol):
    def on_message(self, text: str, sender: str) -> None: ...
    def send_images(self, to: str, results: list[Result]) -> None: ...
