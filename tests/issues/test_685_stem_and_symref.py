"""#685: a dotted suffix after an id's number still holds the id (only a paper-scoped
`H1.2` doesn't), and a guessed default branch is never recorded as origin/HEAD."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.service import KanbanService


@pytest.mark.parametrize(
    ("stem", "key", "holds"),
    [
        ("EXP-003.v2", ("EXP-", 3), True),
        ("EXP-003.v2-notes", ("EXP-", 3), True),
        ("EXP-003-Title", ("EXP-", 3), True),
        ("H1.2-Title", ("H", 1), False),
        ("H130.2-x", ("H130.", 2), True),
        ("EXP-0031", ("EXP-", 3), False),
    ],
)
def test_stem_holds(stem, key, holds):
    assert KanbanService._stem_holds(stem, key) is holds


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout


def _world(tmp_path: Path) -> Path:
    """A clone whose remote advertises no HEAD symref (HEAD names an unborn
    branch), with no local origin/HEAD, but a `main` branch that exists."""
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(remote))
    seed = tmp_path / "seed"
    _git(tmp_path, "init", "-q", "-b", "main", str(seed))
    (seed / "README.md").write_text("seed\n")
    _git(seed, "add", ".")
    _git(seed, "commit", "-q", "-m", "seed")
    _git(seed, "remote", "add", "origin", str(remote))
    _git(seed, "push", "-q", "origin", "main")
    _git(remote, "symbolic-ref", "HEAD", "refs/heads/gone")  # no symref advertised
    local = tmp_path / "local"
    _git(tmp_path, "init", "-q", "-b", "main", str(local))
    _git(local, "remote", "add", "origin", str(remote))
    (local / ".kanban").mkdir()
    (local / ".kanban" / "config.yaml").write_text(
        "kanban:\n  theme: software\n  paths:\n    root: work/\n"
    )
    return local


def test_guessed_default_is_not_recorded_as_origin_head(tmp_path):
    local = _world(tmp_path)
    svc = KanbanService(KanbanConfig.load(local / ".kanban" / "config.yaml"), local)
    branch = svc._default_branch()
    assert branch == "main"  # the guess
    assert svc._fetch_default(branch).returncode == 0
    head = subprocess.run(
        ["git", "symbolic-ref", "-q", "refs/remotes/origin/HEAD"],
        cwd=local, capture_output=True, text=True,
    )
    assert head.returncode != 0, head.stdout  # still unset: a guess isn't recorded


def test_known_default_is_still_recorded(tmp_path):
    local = _world(tmp_path)
    _git(tmp_path / "remote.git", "symbolic-ref", "HEAD", "refs/heads/main")
    svc = KanbanService(KanbanConfig.load(local / ".kanban" / "config.yaml"), local)
    assert svc._fetch_default(svc._default_branch()).returncode == 0
    assert _git(local, "symbolic-ref", "refs/remotes/origin/HEAD").strip() == (
        "refs/remotes/origin/main"
    )
