"""Read-only access to Syncthing's REST API: the files it knows in an inbox folder.

Used once, to backfill the phone folders of photos imported before P1.7. Only GET
requests are sent: nothing here changes Syncthing's config, its folders or its files.
"""

from collections.abc import Iterator
from pathlib import Path

import httpx

FILE = "FILE_INFO_TYPE_FILE"
DIRECTORY = "FILE_INFO_TYPE_DIRECTORY"
SYMLINK = "FILE_INFO_TYPE_SYMLINK"  # not a photo; skipped


def folder_id(client: httpx.Client, inbox: Path) -> str:
    """The id of the Syncthing folder whose path is `inbox`."""
    response = client.get("/rest/config/folders")
    response.raise_for_status()
    for folder in response.json():
        if Path(folder["path"]).expanduser().resolve() == inbox.resolve():
            return folder["id"]
    raise LookupError(f"no Syncthing folder has the path {inbox}")


def synced_files(client: httpx.Client, folder: str) -> list[tuple[str, int]]:
    """(relative path, size) of every file in the folder's global index (`db/browse`).

    The global index lists the files the phone has, including the ones `imgsearch import`
    has since removed from the Mac's receive-only copy.
    """
    response = client.get("/rest/db/browse", params={"folder": folder})
    response.raise_for_status()
    return list(_walk(response.json(), ""))


def _walk(entries: list[dict], prefix: str) -> Iterator[tuple[str, int]]:
    if not isinstance(entries, list):
        raise ValueError(f"unexpected db/browse format: {type(entries).__name__}, not a list")
    for entry in entries:
        path = f"{prefix}{entry['name']}"
        if entry["type"] == FILE:
            yield path, int(entry["size"])
        elif entry["type"] == DIRECTORY:
            yield from _walk(entry.get("children") or [], f"{path}/")
        elif entry["type"] != SYMLINK:
            raise ValueError(f"unexpected db/browse entry type {entry['type']!r} at {path}")
