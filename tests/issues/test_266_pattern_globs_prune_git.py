"""#266: pattern lookups (`rglob("FEAT-*.md")`) must never walk into `.git`.

On Python 3.11 `Path.rglob(pattern)` scans every directory, `.git/objects` included,
before matching, so it races with git repacking objects just like the #259 snapshots.
`glob_outside_git` is the drop-in replacement: same Paths (same order on 3.11), `.git`
pruned. rglob's order and its use of `os.scandir` vary across CPython versions (3.12
walks differently; 3.10 binds scandir at import), so order and the rglob-trips-the-fake
control are asserted only on 3.11; the set, no-duplicates and pruning checks run everywhere,
as does the #283 control that an unpruned scandir walk trips the fake.
"""

from __future__ import annotations

import fnmatch
import functools
import os
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from tests.issues._snapshot import glob_outside_git

PY311 = sys.version_info[:2] == (3, 11)
only_311 = pytest.mark.skipif(not PY311, reason="rglob order/scandir use is 3.11-specific")

PATTERNS = ["*.md", "FEAT-*.md", "FEAT-001*", "*", "_TEMPLATE.md", "*probe*", "nomatch-*"]


def _tree(root: Path) -> Path:
    """Items at several depths, dirs matching the pattern, hidden entries, a symlinked
    dir, and `.git` both at the root and nested (a sub-repo)."""
    for rel in (
        "FEAT-001-probe.md",
        "top.txt",
        "kanban-work/features/FEAT-002-b.md",
        "kanban-work/features/_TEMPLATE.md",
        "kanban-work/features/feat-003-lower.md",
        "kanban-work/bugs/BUG-001-x.md",
        ".claude/skills/a/SKILL.md",
        "FEAT-001-dir.md/inner.md",
        "custom/sub/FEAT-004-deep.md",
        "vendor/.git/objects/ab/FEAT-999.md",
        "vendor/keep.md",
    ):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rel)
    (root / ".git" / "objects" / "ab").mkdir(parents=True)
    (root / ".git" / "objects" / "ab" / "FEAT-000.md").write_text("object")
    (root / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (root / "link").symlink_to(root / "kanban-work", target_is_directory=True)
    return root


def _rglob_minus_git(root: Path, pattern: str) -> list[Path]:
    return [p for p in root.rglob(pattern) if ".git" not in p.relative_to(root).parts]


def _assert_same_as_rglob(got: list[Path], expected: list[Path]) -> None:
    """Same Paths as rglob-minus-.git on every version, no duplicates; same order on 3.11."""
    assert len(got) == len(set(got)), got
    assert sorted(got) == sorted(expected)
    if PY311:
        assert got == expected


def _deny_git_scans(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Make os.scandir raise (as a mid-walk repack would) for any path inside `.git`."""
    real = os.scandir
    scanned: list[str] = []

    def scandir(path: str | os.PathLike[str] = ".") -> os.ScandirIterator[os.DirEntry[str]]:
        scanned.append(os.fspath(path))
        if ".git" in Path(path).parts:
            raise FileNotFoundError(f"git repacked {path} mid-walk")
        return real(path)

    monkeypatch.setattr(os, "scandir", scandir)
    return scanned


@pytest.mark.parametrize("pattern", PATTERNS)
def test_same_paths_as_rglob_minus_git(tmp_path: Path, pattern: str) -> None:
    """Same Paths as rglob minus .git; same order on 3.11."""
    root = _tree(tmp_path)
    _assert_same_as_rglob(list(glob_outside_git(root, pattern)), _rglob_minus_git(root, pattern))


def test_non_vacuity_rglob_does_see_git(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    inside = [p for p in root.rglob("FEAT-*.md") if ".git" in p.relative_to(root).parts]
    assert len(inside) == 2, inside  # root .git and vendor/.git both hold a match


def test_matches_cover_nested_files_and_dirs(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    rel = {p.relative_to(root).as_posix() for p in glob_outside_git(root, "FEAT-*.md")}
    assert rel == {
        "FEAT-001-probe.md",
        "FEAT-001-dir.md",
        "kanban-work/features/FEAT-002-b.md",
        "custom/sub/FEAT-004-deep.md",
    }


@pytest.mark.parametrize("pattern", PATTERNS)
def test_never_enters_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pattern: str) -> None:
    root = _tree(tmp_path)
    expected = _rglob_minus_git(root, pattern)
    scanned = _deny_git_scans(monkeypatch)
    _assert_same_as_rglob(list(glob_outside_git(root, pattern)), expected)
    assert scanned, "non-vacuity: the fake scandir was used"
    assert not [s for s in scanned if ".git" in Path(s).parts], scanned


@only_311
def test_rglob_itself_trips_the_fake(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Control: the fake would catch an unpruned walk. 3.11 only: 3.10 binds scandir at
    import and 3.12 walks without it, so the patch never reaches their rglob."""
    root = _tree(tmp_path)
    _deny_git_scans(monkeypatch)
    with pytest.raises(FileNotFoundError, match="mid-walk"):
        list(root.rglob("FEAT-*.md"))


def _reraise(err: OSError) -> None:
    raise err


def _unpruned_walk(root: Path, pattern: str) -> Iterator[Path]:
    """os.walk (which calls os.scandir) with no pruning; onerror so it cannot skip .git."""
    for dirpath, dirs, names in os.walk(root, onerror=_reraise):
        for name in fnmatch.filter(dirs + names, pattern):
            yield Path(dirpath) / name


_UNPRUNED_GLOB = functools.partial(glob_outside_git, _prune=False)  # the real helper (#299)


@pytest.mark.parametrize("walker", [_UNPRUNED_GLOB, _unpruned_walk], ids=["glob", "os.walk"])
def test_unpruned_walk_trips_the_fake(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, walker: Callable[..., Iterator[Path]]
) -> None:
    """#283 control on every version: the fake reaches an unpruned os.scandir-based walk
    (glob_outside_git itself with `_prune=False`, #299), so test_never_enters_git passes
    because of the pruning, not because the fake is inert."""
    root = _tree(tmp_path)
    _deny_git_scans(monkeypatch)
    with pytest.raises(FileNotFoundError, match="mid-walk"):
        list(walker(root, "FEAT-*.md"))


def test_unscannable_non_git_dir_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _tree(tmp_path)
    real = os.scandir

    def scandir(path: str | os.PathLike[str] = ".") -> os.ScandirIterator[os.DirEntry[str]]:
        if Path(path).name == "sub":
            raise PermissionError(f"cannot scan {path}")
        return real(path)

    monkeypatch.setattr(os, "scandir", scandir)
    with pytest.raises(PermissionError, match="cannot scan"):
        list(glob_outside_git(root, "*.md"))


def test_missing_root_yields_nothing(tmp_path: Path) -> None:
    missing = tmp_path / "absent"
    assert list(glob_outside_git(missing, "*.md")) == list(missing.rglob("*.md")) == []


@pytest.mark.parametrize("pattern", ["features/*.md", "**/*.md", "**"])
def test_path_patterns_rejected(tmp_path: Path, pattern: str) -> None:
    with pytest.raises(ValueError, match="name pattern"):
        list(glob_outside_git(tmp_path, pattern))
