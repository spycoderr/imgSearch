"""P1.9: search loads only SigLIP's text tower. A tiny random SigLIP checkpoint and a tiny
SentencePiece model, both made in tmp_path, stand in for the real ones (no download)."""

from pathlib import Path

import numpy as np
import pytest
import sentencepiece
import torch
from transformers import SiglipConfig, SiglipModel

from imgsearch.config import EmbedderConfig
from imgsearch.models.siglip import SiglipTextEmbedder, normalize, token_ids

DIM = 32
MAX_LENGTH = 16
WORDS = "a white dog sits on the red carpet near the window with a boy and his kite at sunset"


@pytest.fixture(scope="module")
def checkpoint(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A random SigLIP (text and vision towers) saved like an HF checkpoint, plus a
    tokenizer.model with PAD 0 and EOS 1, as in SigLIP 2's."""
    folder = tmp_path_factory.mktemp("siglip")
    corpus = folder / "corpus.txt"
    corpus.write_text("\n".join([WORDS] * 20), encoding="utf-8")
    sentencepiece.SentencePieceTrainer.train(
        input=str(corpus),
        model_prefix=str(folder / "tokenizer"),
        vocab_size=32,
        pad_id=0,
        eos_id=1,
        bos_id=2,
        unk_id=3,
        minloglevel=2,
    )
    torch.manual_seed(0)
    tower = {"hidden_size": DIM, "intermediate_size": 37, "num_hidden_layers": 2}
    config = SiglipConfig(
        text_config={
            **tower,
            "vocab_size": 40,
            "num_attention_heads": 4,
            "max_position_embeddings": MAX_LENGTH,
            "projection_size": DIM,
        },
        vision_config={**tower, "num_attention_heads": 4, "image_size": 32, "patch_size": 8},
    )
    SiglipModel(config).eval().save_pretrained(folder)
    return folder


def embedder_config(checkpoint: Path, dtype: str = "float32") -> EmbedderConfig:
    return EmbedderConfig(
        name=str(checkpoint),
        dim=DIM,
        device="cpu",
        text_max_length=MAX_LENGTH,
        query_device="cpu",
        query_dtype=dtype,
    )


def full_model_vector(checkpoint: Path, ids: list[int]) -> np.ndarray:
    """What `SiglipEmbedder.embed_text` computes: the full model's text features."""
    model = SiglipModel.from_pretrained(checkpoint).eval()
    with torch.inference_mode():
        features = model.get_text_features(input_ids=torch.tensor([ids])).pooler_output
    return normalize(features.numpy(), DIM)[0]


def test_text_tower_vectors_match_the_full_model(checkpoint: Path) -> None:
    embedder = SiglipTextEmbedder(embedder_config(checkpoint))
    for text in ("White dog on the carpet", "boy with a kite at sunset " * 4):
        ids = token_ids(embedder.tokenizer, text, MAX_LENGTH)
        np.testing.assert_allclose(
            embedder.embed_text(text), full_model_vector(checkpoint, ids), atol=1e-6
        )


def test_no_vision_weights_are_loaded(checkpoint: Path) -> None:
    embedder = SiglipTextEmbedder(embedder_config(checkpoint))
    names = [name for name, _ in embedder.model.named_parameters()]
    assert names and not any("vision" in name for name in names)
    assert all(p.dtype == torch.float32 for p in embedder.model.parameters())


def test_float16_vectors_stay_within_tolerance(checkpoint: Path) -> None:
    full = SiglipTextEmbedder(embedder_config(checkpoint))
    half = SiglipTextEmbedder(embedder_config(checkpoint, "float16"))
    assert next(half.model.parameters()).dtype == torch.float16
    for text in ("white dog", "the red carpet near the window"):
        vec = half.embed_text(text)
        assert vec.dtype == np.float32
        assert float(vec @ full.embed_text(text)) >= 0.999


class FakeTokenizer:
    def encode(self, text: str) -> list[int]:
        return [ord(c) for c in text]

    def eos_id(self) -> int:
        return 1

    def pad_id(self) -> int:
        return 0


def test_token_ids_lowercase_then_eos_then_padding() -> None:
    assert token_ids(FakeTokenizer(), "AB", 5) == [97, 98, 1, 0, 0]


def test_token_ids_truncate_and_keep_eos_last() -> None:
    assert token_ids(FakeTokenizer(), "abcdefgh", 5) == [97, 98, 99, 100, 1]
    assert token_ids(FakeTokenizer(), "abcd", 5) == [97, 98, 99, 100, 1]
