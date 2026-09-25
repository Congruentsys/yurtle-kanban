"""#259: tree-snapshot helpers must never walk into `.git`.

git may repack or remove `.git/objects/*` directories while a test walks the tree, so a
walk that merely *filters* `.git` entries (rglob) can still raise FileNotFoundError. The
instrumented `os.scandir` below raises exactly that for any `.git` directory, so a
helper passes only if it prunes `.git` before descending.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests import test_init  # module import: a bare Test* name would be re-collected here
from tests.issues._snapshot import files_outside_git


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A tree with a nested .git/objects, and scandir that dies inside any .git."""
    (tmp_path / ".git" / "objects" / "ab").mkdir(parents=True)
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "x.md").write_text("c")
    (tmp_path / "work" / "sub").mkdir(parents=True)
    (tmp_path / "work" / "sub" / "a.md").write_bytes(b"A")
    (tmp_path / "top.txt").write_bytes(b"T")

    real = os.scandir

    def scandir(path: str | os.PathLike[str] = ".") -> os.ScandirIterator[os.DirEntry[str]]:
        if ".git" in Path(path).parts:
            raise FileNotFoundError(f"git repacked {path} mid-walk")
        return real(path)

    monkeypatch.setattr(os, "scandir", scandir)
    return tmp_path


def test_files_outside_git_never_enters_git(repo: Path) -> None:
    assert files_outside_git(repo) == {
        str(Path(".claude", "x.md")): b"c",
        "top.txt": b"T",
        str(Path("work", "sub", "a.md")): b"A",
    }


def test_init_created_paths_never_enters_git(repo: Path) -> None:
    assert test_init.TestInitExplicitPathScaffolding._created_paths(repo) == [
        "top.txt", "work", "work/sub", "work/sub/a.md",
    ]
