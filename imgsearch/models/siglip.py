"""SigLIP 2 image and text embeddings via transformers, on the configured torch device."""

from pathlib import Path

import numpy as np
import torch
from PIL import Image
from transformers import AutoModel, AutoProcessor

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
        self.processor = AutoProcessor.from_pretrained(config.name)
        self.model = AutoModel.from_pretrained(config.name).to(self.device).eval()

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
