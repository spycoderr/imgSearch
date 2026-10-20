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
class ParsedQuery:
    """Output of the query rewriter. MVP search uses only `english` and `original`."""

    english: str
    original: str
    people: list[str] = field(default_factory=list)
    date_range: tuple[str, str] | None = None  # ISO 8601 start, end
    place: str | None = None


@dataclass(frozen=True)
class Candidate:
    """One image returned by one retriever."""

    image_id: str
    rank: int  # 1 = best
    score: float
    signal: str  # which retriever produced it, e.g. "vector" or "text"


@dataclass(frozen=True)
class Result:
    """One search result handed to a chat adapter."""

    image_id: str
    derivative_path: str | None
    thumb_path: str | None
    caption: str | None
    signals: dict[str, tuple[int, float]]  # signal -> (rank, score)


@dataclass(frozen=True)
class Context:
    """What a stage gets besides the image: settings and the open DB."""

    config: Config
    conn: sqlite3.Connection


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


class ChatAdapter(Protocol):
    def on_message(self, text: str, sender: str) -> None: ...
    def send_images(self, to: str, results: list[Result]) -> None: ...
