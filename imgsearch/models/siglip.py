"""SigLIP 2 image and text embeddings via transformers, on the configured torch device."""

from pathlib import Path

import numpy as np
import sentencepiece
import torch
import transformers
from PIL import Image
from transformers import AutoModel, AutoProcessor, SiglipTextModel
from transformers.utils import cached_file

from imgsearch.config import EmbedderConfig


class SiglipEmbedder:
    """Implements `Embedder`. Returns L2-normalized float32 rows of length `config.dim`.

    The model loads when the object is created; drop the object (and empty the device
    cache) to free it.
    """

    def __init__(self, config: EmbedderConfig) -> None:
        self.dim = config.dim
        self.device = config.device
        self.text_max_length = config.text_max_length
        _quiet_transformers()
        # From the HF cache only, with no Hub check per load (P3-T review T5).
        self.processor = AutoProcessor.from_pretrained(config.name, local_files_only=True)
        model = AutoModel.from_pretrained(config.name, local_files_only=True)
        self.model = model.to(self.device).eval()

    def embed_images(self, paths: list[Path]) -> np.ndarray:
        images = []
        for path in paths:
            with Image.open(path) as img:
                images.append(img.convert("RGB"))
        inputs = self.processor(images=images, return_tensors="pt").to(self.device)
        with torch.inference_mode():
            features = self.model.get_image_features(**inputs)
        return normalize(_tensor(features).float().cpu().numpy(), self.dim)

    def embed_text(self, text: str) -> np.ndarray:
        # Per the transformers SigLIP2 docs (the model card is silent): the model was trained on
        # lowercased text padded/truncated to 64 tokens. The tokenizer doesn't lowercase.
        inputs = self.processor(
            text=[text.lower()],
            padding="max_length",
            max_length=self.text_max_length,
            truncation=True,
            return_tensors="pt",
        ).to(self.device)
        with torch.inference_mode():
            features = self.model.get_text_features(**inputs)
        return normalize(_tensor(features).float().cpu().numpy(), self.dim)[0]


class SiglipTextEmbedder:
    """Implements `TextEmbedder` for search (P1.9): only SigLIP's text tower, on
    `config.query_device` in `config.query_dtype`. Its vectors match
    `SiglipEmbedder.embed_text` (the full model's).

    On the CPU in float32 (the checkpoint's own dtype) the weights stay memory-mapped from the
    HF cache: clean file pages, which add almost nothing to the process's memory footprint.
    The tokenizer is the checkpoint's SentencePiece model (a few MB); the HF tokenizer object
    builds the 256k-token vocabulary in Python and costs ~0.4 GB (P1.9 report).
    """

    def __init__(self, config: EmbedderConfig) -> None:
        self.dim = config.dim
        self.device = config.query_device
        self.text_max_length = config.text_max_length
        _quiet_transformers()
        path = cached_file(config.name, "tokenizer.model", local_files_only=True)
        self.tokenizer = sentencepiece.SentencePieceProcessor(model_file=path)
        model = SiglipTextModel.from_pretrained(
            config.name, local_files_only=True, dtype=getattr(torch, config.query_dtype)
        )
        self.model = model.to(self.device).eval()

    def embed_text(self, text: str) -> np.ndarray:
        ids = token_ids(self.tokenizer, text, self.text_max_length)
        with torch.inference_mode():
            output = self.model(input_ids=torch.tensor([ids], device=self.device))
        return normalize(output.pooler_output.float().cpu().numpy(), self.dim)[0]


def token_ids(
    tokenizer: sentencepiece.SentencePieceProcessor, text: str, max_length: int
) -> list[int]:
    """The ids the checkpoint's HF tokenizer gives for `SiglipEmbedder.embed_text` (its config:
    add_eos_token, no BOS, right padding): the lowercased text's pieces cut to max_length - 1,
    then EOS, then PAD up to max_length. One difference: the HF tokenizer reads "<pad>" or
    "<eos>" typed in a query as special tokens; here they are plain text."""
    ids = tokenizer.encode(text.lower())[: max_length - 1] + [tokenizer.eos_id()]
    return ids + [tokenizer.pad_id()] * (max_length - len(ids))


def _quiet_transformers() -> None:
    # Quiet load noise (review N1): the "Loading weights" bar, and warnings such as the
    # checkpoint's out-of-vocab bos/eos ids (harmless, see the M2 report), or the vision
    # weights the text tower skips. Errors still show.
    transformers.logging.set_verbosity_error()
    transformers.logging.disable_progress_bar()


def _tensor(features: object) -> torch.Tensor:
    # transformers 5 returns a model output whose pooler_output holds the projected features.
    if isinstance(features, torch.Tensor):
        return features
    return features.pooler_output


def normalize(features: np.ndarray, dim: int) -> np.ndarray:
    """L2-normalize each row as float32. Raises if rows aren't `dim` long or have zero norm."""
    if features.ndim != 2 or features.shape[1] != dim:
        raise ValueError(f"expected vectors of length {dim}, got shape {features.shape}")
    norms = np.linalg.norm(features, axis=1, keepdims=True)
    if not np.all(norms > 0):
        raise ValueError("cannot normalize a zero vector")
    return (features / norms).astype(np.float32)
