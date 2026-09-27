"""#584: kanban commits are scoped to their own paths, and a failed commit is an error.

Every kanban commit path used to ``git add <file>`` and then run a bare
``git commit -m``, which commits the WHOLE index: a file the user had staged went
into the kanban commit (and, with ``--push``, to the remote). A refused commit
(a pre-commit hook exiting 1) was only logged, so the command reported success
with nothing committed.

Commit paths covered (every ``git commit`` in src/):
  service._git_commit            <- move, comment, rank, update_item
  service.create_item_and_push   <- create --push (and the hdd ``<type> create --push``)
  service.allocate_next_id       <- next-id
  hdd_commands.hdd_registry      <- hdd registry --push
  hdd_commands.experiment_run    <- experiment run --push

Expected:
1. An unrelated STAGED file is never committed or pushed; it is still staged after.
2. An unrelated modified-but-UNSTAGED file is untouched and not committed.
3. A refusing pre-commit hook: non-zero exit (no traceback), a message carrying
   the hook's own output, no new commit, nothing pushed, the unrelated staged
   file still staged. Choice for the item edit: it is LEFT in the working tree
   (not reverted) -- the user fixes the hook and commits it; reverting would
   silently lose the change.
4. Control: a normal move commits exactly the item file with the usual message.
"""

from __future__ import annotations

import stat
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import main
from yurtle_kanban.config import KanbanConfig, PathConfig
from yurtle_kanban.service import KanbanService

HOOK_SAYS = "hook says no 584"
ITEM = "work/features/FEAT-001-thing.md"
LOCK = ".kanban/_ID_ALLOCATIONS.json"

SOFTWARE_YAML = (
    "kanban:\n  theme: software\n  paths:\n    root: \"work/\"\n"
    "    scan_paths:\n      - \"work/\"\n    ignore: []\n"
)


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True,
    )
    return done.stdout


def _head(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD").strip()


def _remote_head(remote: Path) -> str:
    return _git(remote, "rev-parse", "main").strip()


def _committed_since(repo: Path, base: str) -> set[str]:
    """Every path touched by the commits in base..HEAD."""
    out = _git(repo, "log", "--name-only", "--format=", f"{base}..HEAD")
    return {line for line in out.splitlines() if line.strip()}


def _staged(repo: Path) -> set[str]:
    return set(_git(repo, "diff", "--cached", "--name-only").split())


def _unstaged(repo: Path) -> set[str]:
    return set(_git(repo, "diff", "--name-only").split())


def _item(path: Path, item_id: str, title: str = "thing") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nid: {item_id}\ntitle: \"{title}\"\ntype: feature\nstatus: backlog\n"
        f"priority: medium\ncreated: 2026-09-24\n---\n\n# {item_id}: {title}\n"
    )


def _init(repo: Path, write_config) -> Path:
    """A repo with one pushed commit and a bare ``origin``; returns the remote."""
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@t.com")
    _git(repo, "config", "user.name", "T")
    (repo / ".kanban").mkdir(exist_ok=True)
    write_config(repo)
    (repo / "tracked.txt").write_text("original\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    remote = repo.parent / "origin.git"
    _git(repo.parent, "init", "--bare", "-b", "main", str(remote))
    _git(repo, "remote", "add", "origin", str(remote))
    _git(repo, "push", "-u", "origin", "main")
    return remote


def _write_software(repo: Path) -> None:
    (repo / ".kanban" / "config.yaml").write_text(SOFTWARE_YAML)
    _item(repo / ITEM, "FEAT-001")


def _write_hdd(repo: Path) -> None:
    for sub in ("ideas", "literature", "papers", "hypotheses", "experiments", "measures"):
        (repo / "research" / sub).mkdir(parents=True, exist_ok=True)
        (repo / "research" / sub / ".gitkeep").write_text("")
    KanbanConfig(
        theme="hdd",
        paths=PathConfig(
            root="research/",
            scan_paths=[f"research/{s}/" for s in (
                "ideas", "literature", "papers", "hypotheses", "experiments", "measures",
            )],
        ),
    ).save(repo / ".kanban" / "config.yaml")


def _stage_other(repo: Path) -> None:
    (repo / "other.txt").write_text("user's own work, not for the kanban commit\n")
    _git(repo, "add", "other.txt")


def _refusing_hook(repo: Path) -> None:
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text(f"#!/bin/sh\necho '{HOOK_SAYS}' >&2\nexit 1\n")
    hook.chmod(hook.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _run(repo: Path, monkeypatch, args: list[str]) -> Result:
    config_mod._theme_cache.clear()
    monkeypatch.chdir(repo)
    return CliRunner().invoke(main, args)


def _service(repo: Path) -> KanbanService:
    config_mod._theme_cache.clear()
    svc = KanbanService(KanbanConfig.load(repo / ".kanban" / "config.yaml"), repo)
    svc.scan()
    return svc


@pytest.fixture(autouse=True)
def _clear_theme_cache():
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


@pytest.fixture
def sw(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    remote = _init(repo, _write_software)
    return repo, remote


@pytest.fixture
def hdd(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    """HDD repo holding a committed PAPER-130 paper."""
    repo = tmp_path / "repo"
    remote = _init(repo, _write_hdd)
    result = _run(repo, monkeypatch, ["paper", "create", "130", "A paper"])
    assert result.exit_code == 0, result.output
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "hdd items")
    _git(repo, "push")
    return repo, remote


# Commands that commit, per repo kind. Values: (argv, paths the commit may touch).
SW_CMDS: dict[str, tuple[list[str], str]] = {
    "move": (["move", "FEAT-001", "in_progress", "--force"], "work/"),
    "comment": (["comment", "FEAT-001", "--body", "hello 584"], "work/"),
    "rank": (["rank", "FEAT-001", "3"], "work/"),
    "create-push": (["create", "feature", "New thing", "--push"], "work/"),
    "next-id": (["next-id", "FEAT"], LOCK),
}
HDD_CMDS: dict[str, tuple[list[str], str]] = {
    "registry-push": (["hdd", "registry", "--push"], "research/"),
    "experiment-run-push": (
        ["experiment", "run", "EXPR-130", "--being", "b-v1", "--push"], "research/",
    ),
    "hypothesis-create-paper-push": (
        ["hypothesis", "create", "Linked hyp", "--paper", "130", "--push"], "research/",
    ),
}


def _own(path: str, prefix: str) -> bool:
    return path.startswith(prefix) or path == LOCK


def _check_staged_not_swept(repo: Path, remote: Path, base: str, prefix: str) -> None:
    touched = _committed_since(repo, base)
    assert touched, "control: the command committed nothing"
    assert "other.txt" not in touched, f"unrelated staged file swept into commit: {touched}"
    stray = {p for p in touched if not _own(p, prefix)}
    assert not stray, f"commit touched paths outside the item's own: {stray}"
    assert "other.txt" in _staged(repo), "unrelated staged file no longer staged"
    pushed = _git(remote, "ls-tree", "-r", "--name-only", "main").split()
    assert "other.txt" not in pushed, "unrelated staged file pushed to the remote"


# --- 1. an unrelated staged file is not swept into the kanban commit ----------


@pytest.mark.parametrize("name", list(SW_CMDS))
def test_staged_file_not_swept(sw, monkeypatch, name):
    repo, remote = sw
    args, prefix = SW_CMDS[name]
    _stage_other(repo)
    base = _head(repo)
    result = _run(repo, monkeypatch, args)
    assert result.exit_code == 0, result.output
    _check_staged_not_swept(repo, remote, base, prefix)


@pytest.mark.parametrize("name", list(HDD_CMDS))
def test_staged_file_not_swept_hdd(hdd, monkeypatch, name):
    repo, remote = hdd
    args, prefix = HDD_CMDS[name]
    _stage_other(repo)
    base = _head(repo)
    result = _run(repo, monkeypatch, args)
    assert result.exit_code == 0, result.output
    _check_staged_not_swept(repo, remote, base, prefix)


def test_staged_file_not_swept_by_update_item(sw):
    repo, remote = sw
    _stage_other(repo)
    base = _head(repo)
    _service(repo).update_item("FEAT-001", title="Renamed 584")
    _check_staged_not_swept(repo, remote, base, "work/")


# --- 2. an unrelated modified-but-unstaged file is untouched -------------------


@pytest.mark.parametrize("name", list(SW_CMDS))
def test_unstaged_change_untouched(sw, monkeypatch, name):
    repo, _remote = sw
    args, _prefix = SW_CMDS[name]
    (repo / "tracked.txt").write_text("edited, not staged\n")
    base = _head(repo)
    result = _run(repo, monkeypatch, args)
    assert result.exit_code == 0, result.output
    assert "tracked.txt" not in _committed_since(repo, base)
    assert "tracked.txt" in _unstaged(repo)
    assert "tracked.txt" not in _staged(repo)
    assert (repo / "tracked.txt").read_text() == "edited, not staged\n"


# --- 3. a refusing pre-commit hook is an error ---------------------------------


def _check_refused(repo: Path, remote: Path, result: Result, base: str, rbase: str) -> None:
    assert result.exit_code != 0, f"refused commit reported success:\n{result.output}"
    assert result.exception is None or isinstance(result.exception, SystemExit), (
        f"traceback instead of a message: {result.exception!r}"
    )
    assert HOOK_SAYS in result.output, (
        f"message does not say why the commit failed (hook output missing):\n{result.output}"
    )
    assert _head(repo) == base, "a commit was created despite the hook"
    assert _remote_head(remote) == rbase, "something was pushed despite the hook"
    assert "other.txt" in _staged(repo), "unrelated staged file unstaged by the failure"


@pytest.mark.parametrize("name", list(SW_CMDS))
def test_hook_refusal_is_an_error(sw, monkeypatch, name):
    repo, remote = sw
    args, _prefix = SW_CMDS[name]
    _stage_other(repo)
    _refusing_hook(repo)
    base, rbase = _head(repo), _remote_head(remote)
    result = _run(repo, monkeypatch, args)
    _check_refused(repo, remote, result, base, rbase)


@pytest.mark.parametrize("name", ["registry-push", "experiment-run-push"])
def test_hook_refusal_is_an_error_hdd(hdd, monkeypatch, name):
    repo, remote = hdd
    args, _prefix = HDD_CMDS[name]
    _stage_other(repo)
    _refusing_hook(repo)
    base, rbase = _head(repo), _remote_head(remote)
    result = _run(repo, monkeypatch, args)
    _check_refused(repo, remote, result, base, rbase)


@pytest.mark.parametrize(
    ("name", "expected"),
    [("move", "status: in_progress"), ("comment", "hello 584"), ("rank", "priority_rank: 3")],
)
def test_hook_refusal_leaves_item_edit_in_place(sw, monkeypatch, name, expected):
    """Choice: the edit is kept in the working tree (not reverted), uncommitted."""
    repo, _remote = sw
    _refusing_hook(repo)
    result = _run(repo, monkeypatch, SW_CMDS[name][0])
    assert result.exit_code != 0, result.output
    assert expected in (repo / ITEM).read_text()


def test_hook_refusal_update_item_raises(sw):
    repo, _remote = sw
    _refusing_hook(repo)
    base = _head(repo)
    with pytest.raises(Exception, match=HOOK_SAYS):
        _service(repo).update_item("FEAT-001", title="Renamed 584")
    assert _head(repo) == base


# --- 4. control: a normal move commits exactly the item file -------------------


def test_control_move_commits_only_item(sw, monkeypatch):
    repo, _remote = sw
    base = _head(repo)
    result = _run(repo, monkeypatch, ["move", "FEAT-001", "in_progress", "--force"])
    assert result.exit_code == 0, result.output
    assert _git(repo, "rev-list", "--count", f"{base}..HEAD").strip() == "1"
    assert _committed_since(repo, base) == {ITEM}
    msg = _git(repo, "log", "-1", "--format=%s").strip()
    assert msg == "Move FEAT-001 to in_progress (forced)", msg
    assert not _staged(repo) and not _unstaged(repo)
