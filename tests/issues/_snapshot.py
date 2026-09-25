"""Tree snapshots that never walk into `.git` (#259).

`Path.rglob("*")` descends into `.git/objects` before any filter runs, and git may
repack or remove object directories concurrently, so the walk can raise
FileNotFoundError mid-way. Pruning `.git` from the walk itself avoids that.
"""

from __future__ import annotations

import os
from pathlib import Path


def files_outside_git(repo: Path) -> dict[str, bytes]:
    """Every file under `repo` outside any `.git` directory, with its bytes."""
    files: dict[str, bytes] = {}
    for dirpath, dirs, names in os.walk(repo):
        dirs[:] = [d for d in dirs if d != ".git"]
        for name in names:
            p = Path(dirpath) / name
            if name != ".git" and p.is_file():
                files[str(p.relative_to(repo))] = p.read_bytes()
    return files
