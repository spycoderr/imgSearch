"""Encrypt originals and the DB copy before they leave the Mac (plan D19, P1.6).

AES-256-GCM. Each blob is a 17-byte header, then the ciphertext with its 16-byte tag:

    b"IMGS" | format version (1 byte) | nonce (12 random bytes)

The header is authenticated as associated data, so it can't be changed without the
decryption failing. A new format gets a new version byte; old blobs stay readable.
"""

import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"IMGS"
FORMAT_VERSION = 1
NONCE_SIZE = 12
HEADER_SIZE = len(MAGIC) + 1 + NONCE_SIZE
TAG_SIZE = 16
KEY_SIZE = 32  # AES-256


class DecryptError(Exception):
    pass


def encrypt(key: bytes, plaintext: bytes) -> bytes:
    nonce = os.urandom(NONCE_SIZE)  # fresh per object; random 96-bit nonces are safe here
    header = MAGIC + bytes([FORMAT_VERSION]) + nonce
    return header + AESGCM(key).encrypt(nonce, plaintext, header)


def decrypt(key: bytes, blob: bytes) -> bytes:
    """Return the plaintext, or raise DecryptError if the blob is malformed or was changed."""
    if len(blob) < HEADER_SIZE + TAG_SIZE or blob[: len(MAGIC)] != MAGIC:
        raise DecryptError("not an imgsearch encrypted blob")
    version = blob[len(MAGIC)]
    if version != FORMAT_VERSION:
        raise DecryptError(f"unknown encryption format version {version}")
    header = blob[:HEADER_SIZE]
    nonce = header[len(MAGIC) + 1 :]
    try:
        return AESGCM(key).decrypt(nonce, blob[HEADER_SIZE:], header)
    except InvalidTag:
        raise DecryptError(
            "authentication failed: the blob was changed, or the key is wrong"
        ) from None
