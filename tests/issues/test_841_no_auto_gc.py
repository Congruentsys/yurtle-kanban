"""Issue #841: no background git gc in the suite's repos.

`test_666_rendered_wording` failed at random with FileNotFoundError under
`.git/objects`: a detached `gc --auto` pruned loose objects while the test walked
the repo. The suite's global gitconfig (tests/conftest.py) turns auto-gc and
auto-maintenance off, and `_files` never walks `.git`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from tests.issues.test_644_comments_followups import _files


def _config(repo: Path, key: str) -> str:
    return subprocess.run(
        ["git", "config", "--get", key], cwd=repo, capture_output=True, text=True,
        stdin=subprocess.DEVNULL,
    ).stdout.strip()


def test_suite_repos_never_auto_gc(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, stdin=subprocess.DEVNULL)
    assert _config(tmp_path, "gc.auto") == "0"
    assert _config(tmp_path, "gc.autoDetach") == "false"
    assert _config(tmp_path, "maintenance.auto") == "false"


def test_files_never_walks_git(tmp_path: Path) -> None:
    """A `.git` that can't be listed can't fail `_files` (it isn't entered)."""
    (tmp_path / "a.md").write_text("x")
    git = tmp_path / ".git" / "objects" / "e5"
    git.mkdir(parents=True)
    (git / "loose").write_text("x")
    git.chmod(0)
    try:
        assert _files(tmp_path) == {tmp_path / "a.md"}
    finally:
        git.chmod(0o755)
