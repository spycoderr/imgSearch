"""Caption images with a vision-language model on MLX (mlx-vlm).

The mlx-vlm calls follow the installed 0.7.9 source: `load(repo) -> (model, processor)`,
`apply_chat_template(processor, model.config, prompt, num_images=1)` and
`generate(model, processor, prompt, image=[...], max_tokens=..., temperature=...)`.
"""

import gc
import math
import re
from pathlib import Path

import mlx.core as mx
from mlx_vlm import apply_chat_template, generate, load
from PIL import Image

from imgsearch.config import CaptionerConfig

# Chat-template tokens such as <|im_end|> or <|endoftext|>.
_SPECIAL_TOKEN = re.compile(r"<\|[^|<>]*\|>")
_LABEL = re.compile(r"^\s*caption\s*:\s*", re.IGNORECASE)


class MlxCaptioner:
    """`Captioner` backed by mlx-vlm. Loads the model on construction; call `unload()`."""

    def __init__(self, config: CaptionerConfig) -> None:
        self._config = config
        self._model, self._processor = load(config.name)
        self._prompt = apply_chat_template(
            self._processor, self._model.config, config.prompt, num_images=1
        )

    def caption(self, path: Path) -> str:
        with Image.open(path) as img:
            image = fit_to_area(img.convert("RGB"), self._config.max_pixels)
        result = generate(
            self._model,
            self._processor,
            self._prompt,
            image=[image],  # mlx-vlm's process_image accepts PIL images as well as paths
            max_tokens=self._config.max_tokens,
            temperature=0.0,  # greedy; the checkpoint's own default samples at 0.7
        )
        return clean_caption(result.text)

    def unload(self) -> None:
        """Drop the model and return MLX's cached buffers to the system."""
        self._model = None
        self._processor = None
        gc.collect()  # model modules hold reference cycles; free them before clearing
        mx.clear_cache()


def fit_to_area(image: Image.Image, max_pixels: int) -> Image.Image:
    """Downscale `image` to at most `max_pixels` in area, keeping the aspect ratio.

    Smaller images are returned unchanged.
    """
    width, height = image.size
    if width * height <= max_pixels:
        return image
    scale = math.sqrt(max_pixels / (width * height))
    size = (max(1, math.floor(width * scale)), max(1, math.floor(height * scale)))
    return image.resize(size, Image.Resampling.LANCZOS)


def clean_caption(text: str) -> str:
    """Remove chat-template artifacts: special tokens and a leading "Caption:" label."""
    text = _SPECIAL_TOKEN.sub("", text).strip()
    return _LABEL.sub("", text).strip()
