#!/usr/bin/env python3
"""PostToolUse hook: run `ruff format` and `ruff check --fix` on edited .py files.

Never blocks or fails the edit: every outcome exits 0. Remaining lint issues
show up when ruff is run directly (`uv run ruff check`).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def main() -> None:
    try:
        data = json.load(sys.stdin)
        tool_input = data.get("tool_input") or {}
        path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
        if not path.endswith(".py"):
            return
        proj = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path(__file__).resolve().parents[2])
        target = Path(os.path.realpath(path))
        if proj.resolve() not in target.parents or not target.is_file():
            return  # only format files inside this project
        for args in (["format"], ["check", "--fix", "--quiet"]):
            subprocess.run(
                ["uv", "run", "--quiet", "ruff", *args, str(target)],
                cwd=proj,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=60,
                check=False,
            )
    except Exception:
        pass


if __name__ == "__main__":
    main()
    sys.exit(0)
