"""Issue #1209: the reusable workflow's documented `uses:` path, its install line, the
board page's docs link and README all named the old hankh95 repository. No tracked file
outside the changelog history names it any more; the repo lives at
Congruentsys/yurtle-kanban."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
OLD = "hankh95" + "/yurtle-kanban"  # split so this file never matches itself
# any form: https URL, `uses: OLD@v1` / `OLD/.github/…`, and `git@github.com:OLD` (r1)
NEEDLES = (OLD,)


def _is_history(path: str) -> bool:
    return path == "CHANGELOG.md" or path.startswith("changelog.d/")


def test_no_live_file_names_the_old_repo() -> None:
    if not (ROOT / ".git").exists():  # an unpacked sdist has no git index (r1)
        pytest.skip("not a git checkout")
    files = (
        subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True)
        .stdout.decode()
        .split("\0")
    )
    hits = []
    for rel in filter(None, files):
        if _is_history(rel):
            continue
        path = ROOT / rel
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if any(needle in line for needle in NEEDLES):
                hits.append(f"{rel}:{n}: {line.strip()}")
    assert not hits, "\n".join(hits)
