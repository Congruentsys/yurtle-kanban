"""Issue #1080 — text-mode refusals print on stderr; ``--json`` refusals stay on stdout.

Found while fixing #1077: with one wording (``Error: Unknown board: X``) everywhere,
the stream still differed — ``blocked`` refused through the ``Group`` handler's
``ClickException`` (stderr), while ``list``, ``states`` and ``export`` printed
through ``_refuse()`` (stdout). A script capturing stderr for errors saw one and
missed the others.

Decided ([steer] on #1080, bucket 1): text-mode refusals go to **stderr**, as
click's own errors and the ``Group`` handler's ``Error:`` lines already do. With
``--json`` a refusal stays ONE JSON object on stdout (#877).

Reds: refusals through the shared ``refuse()`` / ``_refuse()`` (#962) in text mode
print their line on stderr, stdout empty of it, exit code unchanged. Controls: the
refusals already on stderr (``blocked``, ``next-id``), and the same commands with
``--json`` printing exactly one JSON object on stdout.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import main

CONFIG = """\
kanban:
  theme: software
  paths:
    root: kanban-work/
    scan_paths:
    - "kanban-work/"
    ignore:
      - "**/_TEMPLATE*"
"""

ITEM = """\
---
id: FEAT-001
title: "Hello"
type: feature
status: backlog
priority: medium
created: 2026-01-01
---

# FEAT-001: Hello
"""


@pytest.fixture(autouse=True)
def _clean_theme_cache() -> Iterator[None]:
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)


@pytest.fixture
def board(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A git repo with a software board holding FEAT-001; the cwd; no agent set."""
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    monkeypatch.delenv("YURTLE_AGENT", raising=False)
    repo = tmp_path / "repo"
    (repo / ".kanban").mkdir(parents=True)
    (repo / ".kanban" / "config.yaml").write_text(CONFIG)
    features = repo / "kanban-work" / "features"
    features.mkdir(parents=True)
    (features / "FEAT-001-hello.md").write_text(ITEM)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    monkeypatch.chdir(repo)
    return repo


def _run(args: list[str]) -> Result:
    # click 8.2+: result.stdout and result.stderr are captured separately
    return CliRunner().invoke(main, args)


def _shown(result: Result) -> str:
    return (
        f"exit {result.exit_code}\n--- stdout ---\n{result.stdout}"
        f"\n--- stderr ---\n{result.stderr}"
    )


# (argv, the refusal line's text) — each through refuse()/_refuse() today
TEXT_REFUSALS: dict[str, tuple[list[str], str]] = {
    "list-unknown-board": (["list", "--board", "nosuch"], "Error: Unknown board: nosuch"),
    "states-unknown-board": (["states", "--board", "nosuch"], "Error: Unknown board: nosuch"),
    "export-unknown-board": (
        ["export", "-f", "markdown", "--board", "nosuch"],
        "Error: Unknown board: nosuch",
    ),
    "list-unknown-status": (["list", "--status", "bogus"], "Unknown status: bogus"),
    "list-empty-assignee": (["list", "--assignee", ""], "Error: --assignee is empty"),
    "claim-no-actor": (["claim", "FEAT-001"], "Error: No actor"),
    "move-empty-assign": (
        ["move", "FEAT-001", "done", "--assign", ""],
        "Error: --assign is empty",
    ),
    "experiment-status-unknown": (
        ["experiment", "status", "EXPR-1"],
        "Error: Experiment not found: EXPR-1",
    ),
}

# already on stderr (the Group handler's ClickException path): controls
STDERR_ALREADY: dict[str, tuple[list[str], str]] = {
    "blocked-unknown-board": (["blocked", "--board", "nosuch"], "Error: Unknown board: nosuch"),
    "next-id-bad-prefix": (
        ["next-id", "1AB", "--no-sync", "--no-commit"],
        "Error: '1AB' is not an ID prefix",
    ),
}

JSON_REFUSALS: dict[str, list[str]] = {
    "list-unknown-board": ["list", "--board", "nosuch", "--json"],
    "states-unknown-board": ["states", "--board", "nosuch", "--json"],
    "blocked-unknown-board": ["blocked", "--board", "nosuch", "--json"],
    "list-unknown-status": ["list", "--status", "bogus", "--json"],
    "list-empty-assignee": ["list", "--assignee", "", "--json"],
    "next-id-bad-prefix": ["next-id", "1AB", "--no-sync", "--no-commit", "--json"],
    "experiment-status-unknown": ["experiment", "status", "EXPR-1", "--json"],
}


@pytest.mark.parametrize("name", sorted(TEXT_REFUSALS))
def test_text_refusal_prints_on_stderr(board: Path, name: str) -> None:
    args, line = TEXT_REFUSALS[name]
    result = _run(args)
    shown = _shown(result)
    assert result.exit_code == 1, shown
    assert line in result.stderr, shown
    assert line not in result.stdout, shown
    assert result.stdout.strip() == "", shown


@pytest.mark.parametrize("name", sorted(STDERR_ALREADY))
def test_control_group_handler_refusal_on_stderr(board: Path, name: str) -> None:
    args, line = STDERR_ALREADY[name]
    result = _run(args)
    shown = _shown(result)
    assert result.exit_code == 1, shown
    assert line in result.stderr, shown
    assert line not in result.stdout, shown


@pytest.mark.parametrize("name", sorted(JSON_REFUSALS))
def test_control_json_refusal_is_one_object_on_stdout(board: Path, name: str) -> None:
    result = _run(JSON_REFUSALS[name])
    shown = _shown(result)
    assert result.exit_code == 1, shown
    payload = json.loads(result.stdout)  # the whole of stdout: exactly one object
    assert isinstance(payload, dict), shown
    assert payload["success"] is False, shown
    assert payload.get("error"), shown
    assert "Error:" not in result.stderr, shown
