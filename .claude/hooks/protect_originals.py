#!/usr/bin/env python3
"""PreToolUse hook: block writes into the originals folder.

Originals are read-only (plan D8). The originals root is `originals_root` in
config.yaml at the project root, falling back to data/originals. `${VAR}` in it is
expanded from the environment, else from the project's .env (KEY=VALUE lines). If a
variable can't be resolved, both the unexpanded path and data/originals are protected,
and a block says why.

Exit 0 = allow (normal permission flow continues). Exit 2 = block; stderr is
shown to Claude as the reason. Standard library only: this runs outside the
project venv.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path

DEFAULT_ROOT = "data/originals"

# Commands that modify any path they are given.
WRITE_CMDS = {
    "rm",
    "rmdir",
    "unlink",
    "mv",
    "chmod",
    "chown",
    "chflags",
    "touch",
    "mkdir",
    "truncate",
    "ln",
    "shred",
    "xattr",
}
# Commands that write only to their destination (last argument or -t DIR).
COPY_CMDS = {"cp", "rsync", "install", "ditto", "scp"}
# Prefixes to skip to reach the real command.
WRAPPERS = {"sudo", "env", "command", "nohup", "time", "nice", "xargs", "exec"}


def project_dir() -> Path:
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent.parent


def read_originals_root(proj: Path) -> str:
    for cfg in (proj / "config.yaml", proj / "imgsearch" / "config.yaml"):
        if not cfg.is_file():
            continue
        try:
            text = cfg.read_text(encoding="utf-8")
        except OSError:
            continue
        m = re.search(r"^\s*originals_root\s*:\s*(.+?)\s*(?:#.*)?$", text, re.M)
        if m:
            value = m.group(1).strip().strip("'\"")
            if value:
                return value
    return DEFAULT_ROOT


def read_env_file(proj: Path) -> dict[str, str]:
    """KEY=VALUE lines of the project's .env; comments and blank lines skipped."""
    env: dict[str, str] = {}
    try:
        lines = (proj / ".env").read_text(encoding="utf-8").splitlines()
    except OSError:
        return env
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip().removeprefix("export ").strip()] = value.strip().strip("'\"")
    return env


def expand_vars(raw: str, env: dict[str, str]) -> tuple[str, list[str]]:
    """`raw` with ${VAR} replaced (environment first, then .env), and any unset names."""
    missing: list[str] = []

    def lookup(m: re.Match[str]) -> str:
        value = os.environ.get(m.group(1)) or env.get(m.group(1))
        if not value:
            missing.append(m.group(1))
            return m.group(0)
        return value

    return re.sub(r"\$\{(\w+)\}", lookup, raw), missing


def resolve(p: str, base: Path) -> Path:
    path = Path(os.path.expanduser(p))
    if not path.is_absolute():
        path = base / path
    return Path(os.path.realpath(path))


def is_inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def path_forms(root_raw: str, root_real: Path, proj: Path, cwd: Path) -> list[str]:
    """Every spelling of the originals root a command might use."""
    unresolved = Path(os.path.expanduser(root_raw))
    if not unresolved.is_absolute():
        unresolved = proj / unresolved
    absolutes = {str(root_real), os.path.normpath(str(unresolved))}
    forms = set(absolutes)
    for a in absolutes:
        for base in (cwd, proj):
            rel = os.path.relpath(a, base)
            if rel != ".":
                forms.add(rel)
                if not rel.startswith(".."):
                    forms.add("./" + rel)
        home = os.path.expanduser("~")
        if a.startswith(home + "/"):
            forms.add("~" + a[len(home) :])
    forms.add(root_raw.rstrip("/"))
    return sorted((f for f in forms if f and f != "."), key=len, reverse=True)


def mentions(token: str, forms: list[str]) -> bool:
    for f in forms:
        if re.search(r"(?:^|[=:\s'\"])" + re.escape(f) + r"(?:/|$|['\"\s])", token):
            return True
    return False


def split_segments(command: str) -> list[list[str]]:
    """Split on ; && || & newlines, then on |. Returns pipelines of segments."""
    pipelines = []
    for chunk in re.split(r"\|\||&&|;|\n|(?<![>&|])&(?![>&])", command):
        parts = [p.strip() for p in re.split(r"(?<![>|])\|(?!\|)", chunk) if p.strip()]
        if parts:
            pipelines.append(parts)
    return pipelines


def tokenize(segment: str) -> list[str]:
    try:
        return shlex.split(segment, posix=True)
    except ValueError:
        return segment.split()


def strip_wrappers(tokens: list[str]) -> list[str]:
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t in WRAPPERS or re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", t):
            i += 1
            # skip wrapper flags such as `xargs -0` or `sudo -u x`
            while i < len(tokens) and tokens[i].startswith("-"):
                i += 1
            continue
        break
    return tokens[i:]


def segment_hits(segment: str, forms: list[str], in_originals: bool) -> str | None:
    """Return a reason if this segment writes into the originals root."""
    # Redirections: > file, >> file, &> file (ignore 2>&1 style fd dups).
    for m in re.finditer(r"(?:\d|&)?>>?\|?\s*([^\s&|;]+)", segment):
        target = m.group(1).strip("'\"")
        if not target or target.startswith("&"):
            continue
        if mentions(target, forms) or (in_originals and not target.startswith("/")):
            return f"redirects output into '{target}'"

    tokens = strip_wrappers(tokenize(segment))
    if not tokens:
        return None
    cmd = os.path.basename(tokens[0])
    args = tokens[1:]
    hit_args = [a for a in args if mentions(a, forms)]
    touches = bool(hit_args) or in_originals

    # Raw-text check too: an unquoted path with a space splits into two tokens.
    if cmd in WRITE_CMDS and (touches or mentions(segment, forms)):
        return f"'{cmd}' would modify the originals"
    if cmd == "tee" and touches:
        return "'tee' would write into the originals"
    if (
        cmd in ("sed", "gsed", "perl")
        and touches
        and any(
            a == "-i" or a.startswith("-i") or (a.startswith("-") and "i" in a and cmd == "perl")
            for a in args
        )
    ):
        return f"'{cmd} -i' would edit files in place in the originals"
    if cmd in COPY_CMDS:
        non_opts = [a for a in args if not a.startswith("-")]
        dest = None
        if "-t" in args and args.index("-t") + 1 < len(args):
            dest = args[args.index("-t") + 1]
        elif len(non_opts) >= 2:
            dest = non_opts[-1]
        if dest and (mentions(dest, forms) or (in_originals and not dest.startswith("/"))):
            return f"'{cmd}' destination '{dest}' is inside the originals"
    if cmd == "dd" and any(a.startswith("of=") and mentions(a, forms) for a in args):
        return "'dd of=' would write into the originals"
    if (
        cmd == "find"
        and touches
        and any(a in ("-delete", "-exec", "-execdir", "-ok") for a in args)
    ):
        # -exec is only a problem if it runs a write command
        if "-delete" in args:
            return "'find -delete' would delete originals"
        for i, a in enumerate(args):
            if a in ("-exec", "-execdir", "-ok") and i + 1 < len(args):
                if os.path.basename(args[i + 1]) in WRITE_CMDS | {"sed", "tee"}:
                    return f"'find {a} {args[i + 1]}' would modify originals"
    return None


def check_bash(command: str, forms: list[str], cwd: Path, root: Path) -> str | None:
    # Best effort only: shell is too flexible to parse fully (variables, eval,
    # python -c with open(..., "w"), etc. are not caught). This guards against
    # accidents, not a determined bypass. Reads and listings are always allowed.
    in_originals = is_inside(Path(os.path.realpath(cwd)), root)
    for pipeline in split_segments(command):
        pipe_mentions = in_originals
        for segment in pipeline:
            tokens = strip_wrappers(tokenize(segment))
            if tokens and tokens[0] == "cd":
                target = tokens[1] if len(tokens) > 1 else "~"
                in_originals = is_inside(resolve(target, cwd), root)
                pipe_mentions = pipe_mentions or in_originals
                continue
            reason = segment_hits(segment, forms, in_originals)
            if reason:
                return reason
            # `find data/originals | xargs rm` style: path earlier, writer later
            raw = tokenize(segment)
            if pipe_mentions and raw and raw[0] == "xargs":
                inner = strip_wrappers(raw)
                if inner and os.path.basename(inner[0]) in WRITE_CMDS:
                    return f"'xargs {inner[0]}' is fed paths from the originals"
            if mentions(segment, forms):
                pipe_mentions = True
    return None


def block(reason: str, root: Path, note: str = "") -> None:
    print(
        f"Blocked by protect_originals hook: {reason}.\n"
        f"The originals folder ({root}) is read-only (plan decision D8). "
        "Write derivatives, thumbnails and test fixtures elsewhere "
        f"(e.g. derivatives_root or a pytest tmp_path).{note}",
        file=sys.stderr,
    )
    sys.exit(2)


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)  # not our input; don't get in the way

    tool = data.get("tool_name", "")
    tool_input = data.get("tool_input") or {}
    proj = project_dir()
    cwd = Path(data.get("cwd") or os.getcwd())
    configured = read_originals_root(proj)
    expanded, missing = expand_vars(configured, read_env_file(proj))
    roots_raw = [expanded]
    note = ""
    if missing:
        # Never guess: protect the unexpanded path and the default, and say why.
        roots_raw = [configured, DEFAULT_ROOT]
        note = (
            f"\nNote: {', '.join(missing)} is not set (environment or .env), so originals_root"
            f" '{configured}' can't be resolved; the hook protects it as written and"
            f" '{DEFAULT_ROOT}'."
        )

    for root_raw in roots_raw:
        root = resolve(root_raw, proj)
        if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
            target = tool_input.get("file_path") or tool_input.get("notebook_path")
            if target and is_inside(resolve(target, cwd), root):
                block(f"{tool} targets '{target}', which is inside the originals", root, note)
        elif tool == "Bash":
            command = tool_input.get("command") or ""
            reason = check_bash(command, path_forms(root_raw, root, proj, cwd), cwd, root)
            if reason:
                block(reason, root, note)
    sys.exit(0)


if __name__ == "__main__":
    main()
