"""Load config.yaml into typed settings."""

import os
import re
import unicodedata
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


WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
Weekday = Literal["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


class RewriterConfig(_Strict):
    name: str
    url: str
    timeout_s: float
    prompt: str  # system prompt for the rewrite LLM; `{today}` is replaced by today's date
    # Words and phrases that name a date (month, weekday, "kal", "last week", ...). The
    # model's date_range is kept only if the query has a digit date or one of these.
    date_words: list[str]
    weekdays: dict[Weekday, list[str]]  # the words for each day, in any language

    @field_validator("prompt")
    @classmethod
    def _one_today_placeholder(cls, value: str) -> str:
        if value.count("{today}") != 1:
            raise ValueError("the rewriter prompt must contain the placeholder {today} once")
        return value

    @field_validator("date_words")
    @classmethod
    def _lowercase_words(cls, value: list[str]) -> list[str]:
        return _date_words(value, "date_words")

    @field_validator("weekdays")
    @classmethod
    def _all_weekdays(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        if set(value) != set(WEEKDAYS):
            raise ValueError("weekdays must list words for each of monday ... sunday")
        return {day: _date_words(words, "weekdays") for day, words in value.items()}


def _date_words(value: list[str], field: str) -> list[str]:
    # NFC, so a nukta letter typed either way (फ़ or फ + ़) compares equal.
    words = [unicodedata.normalize("NFC", " ".join(w.lower().split())) for w in value]
    if not all(words):
        raise ValueError(f"{field} must not contain empty entries")
    return words


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
    # RRF weight per signal: score = sum of weight / (rrf_k + rank). A missing signal is 1.0.
    signal_weights: dict[Literal["vector", "text"], float]

    @field_validator("default_signals")
    @classmethod
    def _some_signals_once(cls, value: list[str]) -> list[str]:
        if not value or len(set(value)) != len(value):
            raise ValueError("default_signals must name vector and/or text, each once")
        return value

    @field_validator("signal_weights")
    @classmethod
    def _positive_weights(cls, value: dict[str, float]) -> dict[str, float]:
        if any(w <= 0 for w in value.values()):
            raise ValueError("signal_weights must be positive")
        return value


class EvalPaths(_Strict):
    queries: Path
    runs_dir: Path


class S3Config(_Strict):
    # From IMGSEARCH_S3_BUCKET and AWS_PROFILE (a profile in ~/.aws/) in .env. None when the
    # variable is unset: only the commands that use S3 need them (review R5).
    bucket: str | None
    profile: str | None
    region: str
    storage_class: str  # e.g. GLACIER_IR
    originals_prefix: str  # object key = prefix + image id
    db_key: str  # object key of the encrypted DB copy
    keychain_service: str  # macOS Keychain item that holds the encryption key
    keychain_account: str


class SyncthingConfig(_Strict):
    url: str  # local REST API, read only (P1.7 folder backfill); key: SYNCTHING_API_KEY


class StorageConfig(_Strict):
    cache_max_gb: float  # `imgsearch evict` keeps the originals on the pen drive under this


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
    s3: S3Config
    storage: StorageConfig
    syncthing: SyncthingConfig

    @field_validator("db_path")
    @classmethod
    def _db_not_on_removable_drive(cls, value: Path) -> Path:
        # exFAT has no journal: a pulled drive could corrupt the DB (plan D18).
        if value.is_relative_to(VOLUMES):
            raise ValueError(f"db_path must be on the Mac, not under {VOLUMES}: {value}")
        return value


def load_config(path: Path = DEFAULT_CONFIG_PATH, env_file: Path = DEFAULT_ENV_FILE) -> Config:
    """Read a config file.

    In path fields and in `s3.bucket` and `s3.profile`, `${VAR}` is replaced from the
    environment, else from `env_file` (a missing file is fine). An unset variable is an
    error in a path, and makes `s3.bucket` or `s3.profile` None. Then `~` is expanded in
    paths, and relative paths resolve against the config file's folder.
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
    for key in ("bucket", "profile"):  # account-specific names live in .env, not the repo
        if key in data.get("s3", {}):
            try:
                data["s3"][key] = expand_vars(data["s3"][key], env)
            except UnsetVariable:
                data["s3"][key] = None
    return Config.model_validate(data)


class UnsetVariable(ValueError):
    pass


def env_value(name: str, env_file: Path = DEFAULT_ENV_FILE) -> str:
    """A secret such as an API key, from the environment, else from `env_file`. Raises
    naming the variable if it is unset."""
    return expand_vars(f"${{{name}}}", {**dotenv_values(env_file), **os.environ})


def expand_vars(value: str, env: Mapping[str, str | None]) -> str:
    """Replace each `${VAR}` in `value`; raise naming the variable if it is unset or empty."""

    def lookup(match: re.Match[str]) -> str:
        name = match.group(1)
        if not env.get(name):
            raise UnsetVariable(
                f"{name} is not set: put it in .env (see .env.example) or the environment"
            )
        return env[name]

    return re.sub(r"\$\{(\w+)\}", lookup, value)
