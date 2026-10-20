"""Load config.yaml into typed settings."""

from pathlib import Path

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


class CaptionerConfig(_Strict):
    name: str


class RewriterConfig(_Strict):
    name: str
    url: str


class ModelsConfig(_Strict):
    embedder: EmbedderConfig
    captioner: CaptionerConfig
    rewriter: RewriterConfig


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
    """Read a config file. Relative paths resolve against the file's folder."""
    path = path.resolve()
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    for key in ("originals_root", "derivatives_root", "db_path"):
        data[key] = path.parent / data[key]
    return Config.model_validate(data)
