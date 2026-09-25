"""Tree snapshots that never walk into `.git` (#259).

`Path.rglob(...)` descends into `.git/objects` before any filter runs, and git may
repack or remove object directories concurrently, so the walk can raise
FileNotFoundError mid-way. Pruning `.git` from the walk itself avoids that.
"""

from __future__ import annotations

import fnmatch
import os
from collections.abc import Iterator
from pathlib import Path


def _raise(err: OSError) -> None:
    # os.walk skips a directory it cannot scan unless told otherwise; a snapshot that
    # silently omits a subtree would hide changes, so every non-.git scan error raises.
    raise err


def paths_outside_git(root: Path, suffix: str = "") -> Iterator[Path]:
    """Every path under `root` (files and dirs) whose name ends with `suffix`, outside
    any `.git` directory: `root.rglob(f"*{suffix}")` minus `.git`, without entering it."""
    for dirpath, dirs, names in os.walk(root, onerror=_raise):
        dirs[:] = [d for d in dirs if d != ".git"]
        for name in dirs + names:
            if name != ".git" and name.endswith(suffix):
                yield Path(dirpath) / name


def glob_outside_git(root: Path, pattern: str) -> Iterator[Path]:
    """`root.rglob(pattern)` minus `.git` and everything under it, without entering it (#266).

    Same Paths in the same order as Python 3.11's rglob for a simple name pattern: each
    directory's matches (files and dirs, in scandir order) before its subdirectories,
    which are visited depth-first; symlinked directories are not followed. Matching is
    `fnmatchcase` on the name, as PosixPath does. Path-containing or `**` patterns are
    rejected rather than half-emulated.
    """
    if "/" in pattern or os.sep in pattern or "**" in pattern:
        raise ValueError(f"glob_outside_git takes a name pattern, not {pattern!r}")
    if not root.is_dir():
        return
    with os.scandir(root) as it:
        entries = list(it)
    for entry in entries:
        if entry.name != ".git" and fnmatch.fnmatchcase(entry.name, pattern):
            yield root / entry.name
    for entry in entries:
        if entry.name != ".git" and entry.is_dir() and not entry.is_symlink():
            yield from glob_outside_git(root / entry.name, pattern)


def files_outside_git(repo: Path) -> dict[str, bytes]:
    """Every file under `repo` outside any `.git` directory, with its bytes."""
    return {
        str(p.relative_to(repo)): p.read_bytes()
        for p in paths_outside_git(repo)
        if p.is_file()
    }
