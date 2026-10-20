"""Embed stage: one image vector per derivative, stored in `embeddings` under the model name."""

import gc
import sqlite3
from collections.abc import Callable

import numpy as np
import sqlite_vec

from imgsearch.config import Config, EmbedderConfig
from imgsearch.interfaces import Context, Embedder, ImageRecord


def load_siglip(config: EmbedderConfig) -> Embedder:
    """Build the real embedder. Imported here so the CLI doesn't load torch for every command."""
    from imgsearch.models.siglip import SiglipEmbedder

    return SiglipEmbedder(config)


class EmbedStage:
    """Embed each image's derivative (never the original) with the configured embedder.

    `make_embedder` builds the embedder in `load()`; tests pass a fake (the plan's test-injection
    convention, "Changes during build", M3).
    """

    name = "embed"
    requires = ["ingest"]

    def __init__(
        self,
        config: Config,
        make_embedder: Callable[[EmbedderConfig], Embedder] = load_siglip,
    ) -> None:
        self.version = config.stages[self.name]
        self.make_embedder = make_embedder
        self.embedder: Embedder | None = None

    def load(self, ctx: Context) -> None:
        self.embedder = self.make_embedder(ctx.config.models.embedder)

    def unload(self) -> None:
        # Also called after a failed load, when there may be no embedder.
        import torch  # lazy: see load_siglip

        self.embedder = None
        gc.collect()
        if torch.backends.mps.is_available():
            torch.mps.empty_cache()

    def run(self, image: ImageRecord, ctx: Context) -> None:
        if self.embedder is None:
            raise RuntimeError("EmbedStage.run called before load()")
        if image.derivative_path is None:
            raise ValueError(f"image {image.id} has no derivative")
        model = ctx.config.models.embedder
        vectors = self.embedder.embed_images([ctx.config.derivatives_root / image.derivative_path])
        save_embedding(ctx.conn, image.id, model.name, vectors[0], model.dim)


def save_embedding(
    conn: sqlite3.Connection, image_id: str, model: str, vec: np.ndarray, dim: int
) -> None:
    """Store (or replace) the vector for (image_id, model). Commits. Raises on a wrong length."""
    if vec.shape != (dim,):
        raise ValueError(f"expected a vector of length {dim}, got shape {vec.shape}")
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO embeddings (image_id, model, vec) VALUES (?, ?, ?)",
            (image_id, model, sqlite_vec.serialize_float32(vec.astype(np.float32).tolist())),
        )
