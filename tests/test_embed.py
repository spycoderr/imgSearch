import sqlite3
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import sqlite_vec

from imgsearch.cli import STAGES
from imgsearch.config import Config, EmbedderConfig
from imgsearch.interfaces import Context
from imgsearch.models.siglip import normalize
from imgsearch.stages.embed import EmbedStage, save_embedding
from imgsearch.stages.runner import run_stage
from imgsearch.store.status import set_status


class FakeEmbedder:
    """Returns `fill` as every image vector (padded to `length`) and records the paths."""

    instances: list["FakeEmbedder"] = []

    def __init__(self, config: EmbedderConfig, fill: float = 1.0, length: int | None = None):
        self.config = config
        self.length = config.dim if length is None else length
        self.fill = fill
        self.paths: list[Path] = []
        FakeEmbedder.instances.append(self)

    def embed_images(self, paths: list[Path]) -> np.ndarray:
        self.paths.extend(paths)
        return np.full((len(paths), self.length), self.fill, dtype=np.float32)

    def embed_text(self, text: str) -> np.ndarray:
        raise AssertionError("the embed stage must not embed text")


@pytest.fixture
def ctx(config: Config, conn: sqlite3.Connection) -> Context:
    """Two ingested images and one not yet ingested."""
    with conn:
        for image_id, derivative in (("a", "derivatives/a.jpg"), ("b", "derivatives/b.jpg")):
            conn.execute(
                "INSERT INTO images (id, orig_relpath, derivative_path, created_at)"
                " VALUES (?, ?, ?, '2026-10-20T00:00:00+00:00')",
                (image_id, f"orig/{image_id}.jpg", derivative),
            )
        conn.execute(
            "INSERT INTO images (id, orig_relpath, created_at)"
            " VALUES ('c', 'orig/c.jpg', '2026-10-20T00:00:00+00:00')"
        )
    for image_id in ("a", "b"):
        set_status(conn, image_id, "ingest", config.stages["ingest"], "done")
    return Context(config=config, conn=conn)


def stored(conn: sqlite3.Connection) -> dict[tuple[str, str], np.ndarray]:
    rows = conn.execute("SELECT image_id, model, vec FROM embeddings").fetchall()
    return {(r[0], r[1]): np.frombuffer(r[2], dtype=np.float32) for r in rows}


def test_normalize_gives_unit_float32_rows():
    out = normalize(np.array([[3.0, 4.0], [0.0, 2.0]], dtype=np.float64), dim=2)
    assert out.dtype == np.float32
    np.testing.assert_allclose(out, [[0.6, 0.8], [0.0, 1.0]], rtol=1e-6)
    np.testing.assert_allclose(np.linalg.norm(out, axis=1), 1.0, rtol=1e-6)


@pytest.mark.parametrize("features", [np.ones((1, 3)), np.ones(2), np.zeros((1, 2))])
def test_normalize_rejects_wrong_length_or_zero(features: np.ndarray):
    with pytest.raises(ValueError):
        normalize(features, dim=2)


def test_stage_identity_comes_from_config(config: Config):
    stage = EmbedStage(config.model_copy(update={"stages": {**config.stages, "embed": "7"}}))
    assert stage.name == "embed"
    assert stage.version == "7"
    assert stage.requires == ["ingest"]


def test_embeds_derivatives_of_ingested_images_only(ctx: Context):
    FakeEmbedder.instances.clear()
    stage = EmbedStage(ctx.config, make_embedder=FakeEmbedder)
    assert run_stage(stage, ctx) == {"done": 2, "failed": 0}

    (fake,) = FakeEmbedder.instances
    assert fake.config == ctx.config.models.embedder
    root = ctx.config.derivatives_root
    assert fake.paths == [root / "derivatives/a.jpg", root / "derivatives/b.jpg"]
    assert not any(ctx.config.originals_root in p.parents for p in fake.paths)

    model = ctx.config.models.embedder
    vectors = stored(ctx.conn)
    assert set(vectors) == {("a", model.name), ("b", model.name)}  # "c" is not ingested
    assert all(v.shape == (model.dim,) for v in vectors.values())
    assert stage.embedder is None  # unloaded


def test_rerun_with_new_version_replaces_vectors(ctx: Context):
    run_stage(EmbedStage(ctx.config, make_embedder=FakeEmbedder), ctx)
    assert run_stage(EmbedStage(ctx.config, make_embedder=FakeEmbedder), ctx) == {
        "done": 0,
        "failed": 0,
    }

    bumped = ctx.config.model_copy(
        update={"stages": {**ctx.config.stages, "embed": ctx.config.stages["embed"] + "-bumped"}}
    )
    stage = EmbedStage(bumped, make_embedder=lambda c: FakeEmbedder(c, fill=2.0))
    assert run_stage(stage, Context(config=bumped, conn=ctx.conn)) == {"done": 2, "failed": 0}

    count = ctx.conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
    assert count == 2
    assert all(np.all(v == 2.0) for v in stored(ctx.conn).values())


def test_wrong_vector_length_fails_the_image(ctx: Context):
    stage = EmbedStage(ctx.config, make_embedder=lambda c: FakeEmbedder(c, length=c.dim - 1))
    assert run_stage(stage, ctx) == {"done": 0, "failed": 2}
    error = ctx.conn.execute(
        "SELECT error FROM stage_status WHERE image_id = 'a' AND stage = 'embed'"
    ).fetchone()[0]
    assert error.startswith("ValueError: expected a vector of length")
    assert stored(ctx.conn) == {}


def test_save_embedding_uses_sqlite_vec_float32(conn: sqlite3.Connection, add_image):
    add_image("a")
    vec = np.array([0.6, 0.8], dtype=np.float64)
    save_embedding(conn, "a", "m", vec, dim=2)
    blob = conn.execute("SELECT vec FROM embeddings").fetchone()[0]
    assert blob == sqlite_vec.serialize_float32([0.6, 0.8])
    with pytest.raises(ValueError):
        save_embedding(conn, "a", "m", vec, dim=3)


def test_unload_without_load_is_safe(config: Config):
    EmbedStage(config, make_embedder=FakeEmbedder).unload()


def test_cli_import_does_not_load_torch():
    # S1: EmbedStage is in the CLI's STAGES dict; `imgsearch status` must not pay for torch.
    code = (
        "import sys, imgsearch.cli;"
        " assert 'torch' not in sys.modules and 'transformers' not in sys.modules"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


def test_embed_is_runnable_from_cli():
    assert STAGES["embed"] is EmbedStage
