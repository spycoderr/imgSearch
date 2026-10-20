"""Load config.yaml into typed settings."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"


class _Strict(BaseModel):
    # Unknown keys are errors, so a typo in config.yaml fails loudly.
    model_config = ConfigDict(extra="forbid", frozen=True)


class EmbedderConfig(_Strict):
    name: str
    dim: int
    device: str
    text_max_length: int


class CaptionerConfig(_Strict):
    name: str
    prompt: str
    max_tokens: int
    max_pixels: int  # images are downscaled to at most this area before captioning


class RewriterConfig(_Strict):
    name: str
    url: str
    timeout_s: float
    prompt: str  # system prompt for the rewrite LLM


class OcrConfig(_Strict):
    recognition_level: Literal["accurate", "fast"]
    languages: list[str]  # Vision language codes, e.g. "en-US"


class ModelsConfig(_Strict):
    embedder: EmbedderConfig
    captioner: CaptionerConfig
    rewriter: RewriterConfig
    ocr: OcrConfig


class SearchConfig(_Strict):
    top_k: int
    per_retriever_k: int
    rrf_k: int


class Config(_Strict):
    originals_root: Path
    derivatives_root: Path
    db_path: Path
    models: ModelsConfig
    stages: dict[str, str]  # stage name -> version
    search: SearchConfig


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> Config:
    """Read a config file. `~` is expanded; relative paths resolve against the file's folder."""
    path = path.resolve()
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    for key in ("originals_root", "derivatives_root", "db_path"):
        if key in data:  # a missing key is reported by validation below
            data[key] = path.parent / Path(data[key]).expanduser()
    return Config.model_validate(data)
