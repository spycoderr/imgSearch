"""Secrets for the chat apps: from the environment, else from the git-ignored .env."""

import os
from collections.abc import Mapping, Sequence
from pathlib import Path

from dotenv import dotenv_values

from imgsearch.config import DEFAULT_ENV_FILE


def read_env(
    keys: Sequence[str],
    env_file: Path = DEFAULT_ENV_FILE,
    environ: Mapping[str, str] = os.environ,
) -> dict[str, str]:
    """The values of `keys`. Raises ValueError naming every one that is unset or empty."""
    env = {**dotenv_values(env_file), **environ}  # real environment variables win
    missing = [key for key in keys if not env.get(key)]
    if missing:
        them = "it" if len(missing) == 1 else "them"
        raise ValueError(f"{', '.join(missing)} not set: put {them} in .env (see .env.example)")
    return {key: env[key] for key in keys}


def split_list(value: str) -> list[str]:
    """A comma-separated .env value as a list, blanks dropped."""
    return [item.strip() for item in value.split(",") if item.strip()]
