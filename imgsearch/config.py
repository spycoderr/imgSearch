"""Load config.yaml into typed settings."""

import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

import yaml
from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, field_validator

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"  # machine-specific paths; git-ignored
VOLUMES = Path("/Volumes")  # where macOS mounts external drives


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
    prompt: str  # system prompt for the rewrite LLM; `{today}` is replaced by today's date

    @field_validator("prompt")
    @classmethod
    def _one_today_placeholder(cls, value: str) -> str:
        if value.count("{today}") != 1:
            raise ValueError("the rewriter prompt must contain the placeholder {today} once")
        return value


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
    default_signals: list[Literal["vector", "text"]]  # used when a caller names none

    @field_validator("default_signals")
    @classmethod
    def _some_signals_once(cls, value: list[str]) -> list[str]:
        if not value or len(set(value)) != len(value):
            raise ValueError("default_signals must name vector and/or text, each once")
        return value


class EvalPaths(_Strict):
    queries: Path
    runs_dir: Path


class Config(_Strict):
    originals_root: Path  # pen drive (plan D18); only `imgsearch import` adds files here
    derivatives_root: Path  # pen drive: 2048px derivatives
    thumbs_root: Path  # on the Mac, so search shows previews with the pen drive unplugged
    db_path: Path  # on the Mac, never on a removable drive
    inboxes: list[Path]  # folders `imgsearch import` empties, e.g. the Syncthing folder
    models: ModelsConfig
    stages: dict[str, str]  # stage name -> version
    search: SearchConfig
    eval: EvalPaths

    @field_validator("db_path")
    @classmethod
    def _db_not_on_removable_drive(cls, value: Path) -> Path:
        # exFAT has no journal: a pulled drive could corrupt the DB (plan D18).
        if value.is_relative_to(VOLUMES):
            raise ValueError(f"db_path must be on the Mac, not under {VOLUMES}: {value}")
        return value


def load_config(path: Path = DEFAULT_CONFIG_PATH, env_file: Path = DEFAULT_ENV_FILE) -> Config:
    """Read a config file.

    In path fields, `${VAR}` is replaced from the environment, else from `env_file` (a
    missing file is fine), and an unset variable is an error. Then `~` is expanded, and
    relative paths resolve against the config file's folder.
    """
    path = path.resolve()
    env = {**dotenv_values(env_file), **os.environ}  # real environment variables win
    data = yaml.safe_load(path.read_text(encoding="utf-8"))

    def resolve(value: str) -> Path:
        return path.parent / Path(expand_vars(value, env)).expanduser()

    for key in ("originals_root", "derivatives_root", "thumbs_root", "db_path"):
        if key in data:  # a missing key is reported by validation below
            data[key] = resolve(data[key])
    if isinstance(data.get("inboxes"), list):
        data["inboxes"] = [resolve(p) for p in data["inboxes"]]
    for key in ("queries", "runs_dir"):
        if key in data.get("eval", {}):
            data["eval"][key] = resolve(data["eval"][key])
    return Config.model_validate(data)


def expand_vars(value: str, env: Mapping[str, str | None]) -> str:
    """Replace each `${VAR}` in `value`; raise naming the variable if it is unset or empty."""

    def lookup(match: re.Match[str]) -> str:
        name = match.group(1)
        if not env.get(name):
            raise ValueError(
                f"{name} is not set: put it in .env (see .env.example) or the environment"
            )
        return env[name]

    return re.sub(r"\$\{(\w+)\}", lookup, value)
