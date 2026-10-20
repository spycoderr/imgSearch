"""Check that storage roots on a removable drive are there before using them.

A root under /Volumes/<name> lives on an external drive. When the drive is unplugged,
/Volumes/<name> is not a mount point, and writing there would put files on the Mac's
disk (or fail), so commands that need the drive check it first.
"""

import os
from pathlib import Path

from imgsearch.config import VOLUMES, Config


class DriveNotMounted(Exception):
    pass


def mount_point(path: Path) -> Path | None:
    """The /Volumes/<name> folder `path` lives on, or None for a path on the Mac's disk."""
    if not path.is_relative_to(VOLUMES) or path == VOLUMES:
        return None
    return VOLUMES / path.relative_to(VOLUMES).parts[0]


def is_available(path: Path) -> bool:
    mount = mount_point(path)
    return mount is None or os.path.ismount(mount)


def require_mounted(config: Config, *keys: str) -> None:
    """Raise DriveNotMounted if any of the named config roots is on an unmounted drive."""
    for key in keys:
        path: Path = getattr(config, key)
        if not is_available(path):
            raise DriveNotMounted(f"pen drive not mounted: {mount_point(path)} ({key}: {path})")
