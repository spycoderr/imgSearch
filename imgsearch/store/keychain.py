"""The encryption key, kept in the macOS Keychain through the built-in `security` command.

The key is 32 random bytes, stored as 64 hex characters in a generic-password item named
by `s3.keychain_service` and `s3.keychain_account`. It is never passed as a command-line
argument (other processes can see those): `security` reads it from stdin. Nothing here
prints or logs it; only `imgsearch s3-key export` shows it, for the user to back up.
"""

import secrets
import subprocess
from collections.abc import Callable

from imgsearch.config import S3Config
from imgsearch.store.crypto import KEY_SIZE

NOT_FOUND = 44  # `security` exit status when the item doesn't exist (errSecItemNotFound)

Runner = Callable[..., subprocess.CompletedProcess]


class KeyMissing(Exception):
    pass


class KeychainError(Exception):
    pass


def read_key(s3: S3Config, run: Runner = subprocess.run) -> bytes:
    """The key from the Keychain. Raises KeyMissing if there is none.

    `run` stands in for subprocess.run in tests.
    """
    hex_key = _find(s3, run)
    if hex_key is None:
        raise KeyMissing(
            f"no encryption key in the Keychain ({s3.keychain_service});"
            " run `imgsearch s3-key create` once"
        )
    try:
        key = bytes.fromhex(hex_key)
    except ValueError:
        key = b""
    if len(key) != KEY_SIZE:
        raise KeychainError(f"Keychain item {s3.keychain_service} does not hold a 256-bit key")
    return key


def create_key(s3: S3Config, run: Runner = subprocess.run) -> None:
    """Make a random 256-bit key and store it. Refuses if the item already exists."""
    if _find(s3, run) is not None:
        raise KeychainError(
            f"a key already exists in the Keychain ({s3.keychain_service}); not replacing it:"
            " objects already uploaded need it"
        )
    hex_key = secrets.token_hex(KEY_SIZE)
    # `-w` last makes `security` prompt (twice) instead of taking the key as an argument.
    # A new session has no terminal, so the prompts read stdin. No `-U`: never overwrite.
    proc = run(
        [
            "security",
            "add-generic-password",
            "-s",
            s3.keychain_service,
            "-a",
            s3.keychain_account,
            "-w",
        ],  # fmt: skip
        input=f"{hex_key}\n{hex_key}\n",
        capture_output=True,
        text=True,
        start_new_session=True,
    )
    if proc.returncode != 0:
        raise KeychainError(f"security add-generic-password failed ({proc.returncode})")
    if _find(s3, run) != hex_key:
        raise KeychainError("the key read back from the Keychain differs from the one stored")


def _find(s3: S3Config, run: Runner) -> str | None:
    proc = run(
        [
            "security",
            "find-generic-password",
            "-s",
            s3.keychain_service,
            "-a",
            s3.keychain_account,
            "-w",
        ],  # fmt: skip
        capture_output=True,
        text=True,
    )
    if proc.returncode == NOT_FOUND:
        return None
    if proc.returncode != 0:
        # stderr never holds the key; stdout (which would) is not shown.
        raise KeychainError(
            f"security find-generic-password failed ({proc.returncode}): {proc.stderr.strip()}"
        )
    return proc.stdout.strip()
