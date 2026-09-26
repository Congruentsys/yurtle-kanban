"""#511: `_repo_relative` with a RELATIVE path (follow-up from the review of #508).

What is asserted: a relative path is made absolute against the current working
directory before it is compared with the repo root, so a bare `..` (or anything
normalising to it) with cwd = the repo is OUTSIDE the repo (None), and a relative
path with cwd elsewhere is judged by where it lands from that cwd.
Controls: `.` and `x/y` with cwd = the repo stay inside, unchanged; `x/../y` is `y`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.service import KanbanService


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo = (tmp_path / "repo").resolve()
    (repo / "x" / "y").mkdir(parents=True)
    (repo / "sub").mkdir()
    (tmp_path / "elsewhere").mkdir()
    return repo


def _service(repo: Path) -> KanbanService:
    return KanbanService(KanbanConfig(), repo)


@pytest.mark.parametrize("rel", ["..", "../x", "x/../..", "../repo/.."])
def test_relative_dotdot_outside_cwd_repo(repo, monkeypatch, rel):
    monkeypatch.chdir(repo)
    svc = _service(repo)
    got = svc._repo_relative(Path(rel))
    assert got is None, f"cwd=repo: {rel!r} -> {got!r}, expected outside (None)"
    assert svc._repo_relative(Path(rel), repo) is None, f"explicit root: {rel!r}"


def test_relative_dotdot_outside_git(repo, monkeypatch):
    monkeypatch.chdir(repo)
    svc = _service(repo)
    svc._git_top = repo  # no git needed: the toplevel is the repo
    assert svc._outside_git(Path("..")) is True


# --- relative paths are made absolute against the cwd -----------------------------


def test_relative_from_cwd_elsewhere_is_outside(repo, monkeypatch):
    monkeypatch.chdir(repo.parent / "elsewhere")
    svc = _service(repo)
    assert svc._repo_relative(Path("x/y")) is None, "elsewhere/x/y taken as repo/x/y"
    assert svc._repo_relative(Path(".")) is None


def test_relative_from_cwd_subdir(repo, monkeypatch):
    monkeypatch.chdir(repo / "sub")
    svc = _service(repo)
    assert svc._repo_relative(Path("z.md")) == Path("sub/z.md")
    assert svc._repo_relative(Path("../x/y")) == Path("x/y")
    assert svc._repo_relative(Path("..")) == Path(".")
    assert svc._repo_relative(Path("../..")) is None


def test_relative_from_cwd_outside_back_into_repo(repo, monkeypatch):
    monkeypatch.chdir(repo.parent / "elsewhere")
    svc = _service(repo)
    assert svc._repo_relative(Path("../repo/x")) == Path("x")


# --- controls ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rel", "want"),
    [(".", "."), ("x/y", "x/y"), ("x/../x/y", "x/y"), ("x/..", "."), ("new/f.md", "new/f.md")],
)
def test_control_relative_inside_cwd_repo(repo, monkeypatch, rel, want):
    monkeypatch.chdir(repo)
    svc = _service(repo)
    assert svc._repo_relative(Path(rel)) == Path(want)
