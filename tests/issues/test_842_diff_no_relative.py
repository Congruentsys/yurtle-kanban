"""#842: git diffs the service reads must be repo-rooted even under ``diff.relative``.

A board whose ``.kanban/`` sits in a subdirectory (``sub/``) runs git with
``cwd=sub``. A user's ``git config diff.relative true`` then narrows ``git diff``
to ``sub/`` and prints paths relative to it:

1. ``_git_state``'s ``diff --cached -M --name-status -z HEAD`` prints a staged
   ``git mv``'s destination as ``research/papers/...`` instead of
   ``sub/research/papers/...``; it never matches the repo-rooted path, so a
   committed parent that was only renamed reads as "not committed" (untracked),
   not "has uncommitted edits". Consumer: ``create_item_and_push``'s parent check.
2. ``_commit_paths``'s ``diff --cached --quiet HEAD -- <paths>`` ignores a staged
   change outside ``sub/`` (an item whose board root is elsewhere in the same git
   work tree), answers "nothing changed", and the move is never committed.

Decided fix ([steer] on #842): ``--no-relative`` on both. Controls: the same
scenarios without ``diff.relative`` behave correctly today.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from yurtle_kanban import config as config_mod
from yurtle_kanban.config import KanbanConfig, PathConfig
from yurtle_kanban.models import WorkItemStatus, WorkItemType
from yurtle_kanban.service import KanbanService

SUB = "sub"
HDD_DIRS = ["research/experiments/", "research/papers/", "research/hypotheses/",
            "research/measures/"]
PAPER = f"{SUB}/research/papers/PAPER-130-A-paper.md"
MOVED = f"{SUB}/research/papers/PAPER-130-A-paper-moved.md"
PAPER_TEXT = (
    '---\nid: PAPER-130\ntitle: "A paper"\ntype: paper\nstatus: backlog\n---\n\n'
    "# PAPER-130\n\n```turtle\n@prefix paper: <https://nusy.dev/paper/> .\n"
    "<#PAPER-130> a paper:Paper .\n```\n"
)
FEAT = "work/FEAT-001-x.md"
FEAT_TEXT = (
    '---\nid: FEAT-001\ntitle: "x"\ntype: feature\nstatus: backlog\n'
    "priority: medium\ncreated: 2026-09-24\n---\n\n# FEAT-001: x\n"
)


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True,
    )
    return done.stdout


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    monkeypatch.setenv("YURTLE_AGENT", "tester")
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


def _repo(tmp_path: Path) -> Path:
    top = (tmp_path / "repo").resolve()
    top.mkdir()
    _git(top, "init", "-q", "-b", "main")
    _git(top, "config", "user.email", "t@t.com")
    _git(top, "config", "user.name", "T")
    return top


def _commit_all(top: Path, message: str) -> None:
    _git(top, "add", "-A")
    _git(top, "commit", "-q", "-m", message)


def _service(board: Path) -> KanbanService:
    config_mod._theme_cache.clear()
    svc = KanbanService(KanbanConfig.load(board / ".kanban" / "config.yaml"), board)
    svc.scan()
    return svc


# --- (1) _git_state: a staged rename of a committed parent ---------------------------


def _hdd_sub_board(tmp_path: Path) -> tuple[Path, KanbanService]:
    """An HDD board under `sub/` holding a committed PAPER-130, renamed by `git mv`."""
    top = _repo(tmp_path)
    sub = top / SUB
    for d in HDD_DIRS:
        (sub / d).mkdir(parents=True, exist_ok=True)
        (sub / d / ".gitkeep").write_text("")
    KanbanConfig(
        theme="hdd", paths=PathConfig(root="research/", scan_paths=HDD_DIRS)
    ).save(sub / ".kanban" / "config.yaml")
    (top / PAPER).write_text(PAPER_TEXT)
    _commit_all(top, "sub board with a paper")
    _git(top, "mv", PAPER, MOVED)
    svc = _service(sub)
    # the layout under test: the board's folder is below the git top level
    assert svc._git_toplevel().resolve() == top
    assert svc.repo_root.resolve() == sub.resolve()
    return top, svc


def _create_child(svc: KanbanService) -> dict:
    return svc.create_item_and_push(
        WorkItemType.HYPOTHESIS, "h", item_id="H130.1", parent="PAPER-130"
    )


def _assert_renamed_parent_is_uncommitted_edits(top: Path, svc: KanbanService) -> None:
    result = _create_child(svc)
    msg = result.get("message", "")
    assert result["success"] is False, result
    assert "uncommitted edits" in msg, msg
    assert "not committed" not in msg, msg
    assert svc._git_state(top / MOVED) == "changed", "a staged rename read as untracked"


def test_staged_rename_in_subdir_board_under_diff_relative(tmp_path: Path) -> None:
    top, svc = _hdd_sub_board(tmp_path)
    _git(top, "config", "diff.relative", "true")
    _assert_renamed_parent_is_uncommitted_edits(top, svc)


def test_control_staged_rename_in_subdir_board(tmp_path: Path) -> None:
    top, svc = _hdd_sub_board(tmp_path)
    _assert_renamed_parent_is_uncommitted_edits(top, svc)


# --- (2) _commit_paths: a staged change outside the board's folder -------------------


def _software_sub_board(tmp_path: Path) -> tuple[Path, KanbanService]:
    """A software board whose `.kanban/` is in `sub/` and whose items live in
    `work/` beside it: in the git work tree, outside the board's folder."""
    top = _repo(tmp_path)
    sub = top / SUB
    (sub / ".kanban").mkdir(parents=True)
    root = f"{top / 'work'}/"
    (sub / ".kanban" / "config.yaml").write_text(
        "kanban:\n  theme: software\n  paths:\n"
        f'    root: "{root}"\n    scan_paths:\n      - "{root}"\n    ignore: []\n'
    )
    (top / FEAT).parent.mkdir(parents=True)
    (top / FEAT).write_text(FEAT_TEXT)
    _commit_all(top, "sub board, items beside it")
    svc = _service(sub)
    assert svc._git_toplevel().resolve() == top
    assert "FEAT-001" in {i.id for i in svc.scan()}
    return top, svc


def _assert_move_is_committed(top: Path, svc: KanbanService) -> None:
    before = _git(top, "rev-parse", "HEAD").strip()
    svc.move_item("FEAT-001", WorkItemStatus.READY, commit=True, validate_workflow=False)
    after = _git(top, "rev-parse", "HEAD").strip()
    assert after != before, "the move was never committed"
    changed = _git(top, "diff-tree", "--no-commit-id", "-r", "--name-only", "HEAD").split()
    assert changed == [FEAT], changed
    assert _git(top, "status", "--porcelain", "--", FEAT) == "", "the move is left staged"


def test_move_outside_subdir_board_committed_under_diff_relative(tmp_path: Path) -> None:
    top, svc = _software_sub_board(tmp_path)
    _git(top, "config", "diff.relative", "true")
    _assert_move_is_committed(top, svc)


def test_control_move_outside_subdir_board_committed(tmp_path: Path) -> None:
    top, svc = _software_sub_board(tmp_path)
    _assert_move_is_committed(top, svc)
