"""Issue #1239: a value given through a deprecated 2.x alias (#1230) and then
refused was refused under the NEW flag's name: `create feature t -a ''` printed
`Error: --assign is empty`, though the user typed `-a`. Every refusal reached
through an alias names the spelling actually given (as #1235 did for
`list --pickable`); the new forms keep their own names.

The positional `comment ID TEXT` has no flag: its refusal names `TEXT`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from yurtle_kanban import cli
from yurtle_kanban.cli import main
from yurtle_kanban.config import KanbanConfig, PathConfig


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, check=True)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A git repo with a software-theme board and one item, FEAT-001."""
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.email", "test@test.com")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "config", "commit.gpgsign", "false")
    (tmp_path / ".kanban").mkdir()
    (tmp_path / "kanban-work" / "features").mkdir(parents=True)
    KanbanConfig(
        theme="software",
        paths=PathConfig(root="kanban-work/", scan_paths=["kanban-work/features/"]),
    ).save(tmp_path / ".kanban" / "config.yaml")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "init")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("YURTLE_AGENT", raising=False)
    monkeypatch.setattr(cli.console, "_width", 10_000)
    r = CliRunner().invoke(main, ["create", "feature", "probe"])
    assert r.exit_code == 0, r.output
    return tmp_path


def _error(args: list[str]) -> str:
    """The `Error:` line of a refused command (the deprecation line, which names
    the new form by design, is left out)."""
    r: Result = CliRunner().invoke(main, args)
    assert r.exit_code != 0, r.output
    errors = [line for line in r.stderr.splitlines() if line.startswith("Error:")]
    assert len(errors) == 1, r.stderr
    return errors[0]


@pytest.mark.parametrize(
    "args, named, not_named",
    [pytest.param(*c, id=" ".join(c[0])) for c in [
        (["create", "feature", "t", "-a", ""], "-a", "--assign"),
        (["create", "feature", "t", "--assignee", ""], "--assignee", "--assign "),
        (["create", "feature", "t", "-a", "x\ty"], "-a", "--assign"),
        (["create", "feature", "t", "-d", ""], "-d", "--body"),
        (["create", "feature", "t", "--description", " "], "--description", "--body"),
        (["move", "FEAT-001", "in_progress", "-a", "", "--agent", "Test", "--no-commit"],
         "-a", "--assign"),
        (["next", "-a", ""], "-a", "--agent"),
        (["next", "--assignee", ""], "--assignee", "--agent"),
        (["comment", "FEAT-001", "--body", "x", "-a", ""], "-a", "--agent"),
        (["comment", "FEAT-001", "--body", "x", "--author", ""], "--author", "--agent"),
        (["comment", "FEAT-001", ""], "TEXT", "--body"),
        (["list", "-a", ""], "-a", "--assignee"),
    ]],
)
def test_refusal_through_an_alias_names_the_alias(
    repo: Path, args: list[str], named: str, not_named: str
) -> None:
    err = _error(args)
    assert err.startswith(f"Error: {named} "), err
    assert not_named not in err, err


@pytest.mark.parametrize(
    "args, named",
    [pytest.param(*c, id=" ".join(c[0])) for c in [
        (["create", "feature", "t", "--assign", ""], "--assign"),
        (["create", "feature", "t", "--body", ""], "--body"),
        (["move", "FEAT-001", "in_progress", "--assign", "", "--agent", "Test", "--no-commit"],
         "--assign"),
        (["next", "--agent", ""], "--agent"),
        (["comment", "FEAT-001", "--body", "x", "--agent", ""], "--agent"),
        (["comment", "FEAT-001", "--body", ""], "--body"),
        (["list", "--assignee", ""], "--assignee"),
    ]],
)
def test_new_forms_keep_their_own_names(repo: Path, args: list[str], named: str) -> None:
    assert _error(args).startswith(f"Error: {named} ")


def test_pickable_status_with_deprecated_a_names_both() -> None:
    r = CliRunner().invoke(main, ["list", "--pickable", "--status", "ready", "-a", "x"])
    assert r.exit_code == 2, r.output
    assert "drop --status/-a" in r.stderr, r.stderr
