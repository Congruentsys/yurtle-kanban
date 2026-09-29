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

import io
import json
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from click.testing import CliRunner, Result
from rich.console import Console

from yurtle_kanban import config as config_mod
from yurtle_kanban._click import refuse
from yurtle_kanban.cli import main
from yurtle_kanban.service import ControlState, KanbanService

SRC = Path(__file__).resolve().parents[2] / "src"
CLI = "from yurtle_kanban.cli import main; main()"

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


# --- r1 F1/F2: the stderr console follows stderr, not stdout's resolved state ---------


@pytest.mark.skipif(sys.platform == "win32", reason="needs a pty")
def test_stderr_file_gets_no_ansi_when_stdout_is_a_terminal(board: Path) -> None:
    """`list --board nosuch 2>err.log` in a terminal: stdout a TTY, stderr a file.
    The log holds the plain `Error:` line, no colour codes (as click's own is)."""
    import pty

    master, slave = pty.openpty()
    env = {**os.environ, "PYTHONPATH": str(SRC), "PYTHONDONTWRITEBYTECODE": "1"}
    env["TERM"] = "xterm-256color"
    for key in ("NO_COLOR", "FORCE_COLOR", "TTY_COMPATIBLE", "TTY_INTERACTIVE", "COLUMNS"):
        env.pop(key, None)
    err_log = board / "err.log"
    try:
        with err_log.open("wb") as err:
            proc = subprocess.run(
                [sys.executable, "-c", CLI, "list", "--board", "nosuch"],
                cwd=board,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=slave,
                stderr=err,
                timeout=120,
            )
    finally:
        os.close(slave)
        os.close(master)
    logged = err_log.read_bytes()
    assert proc.returncode == 1, logged
    assert logged == b"Error: Unknown board: nosuch\n", logged


def test_swapped_console_force_terminal_is_copied(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A caller's console forced to a terminal (tests swap one in) renders the
    refusal on stderr as a terminal too: its explicit settings are copied."""
    tty = Console(file=io.StringIO(), force_terminal=True, width=200)
    with pytest.raises(SystemExit) as exc:
        refuse("boom", console=tty)
    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "\x1b[" in captured.err, repr(captured.err)
    assert "Error: boom" in captured.err, repr(captured.err)


def test_default_console_on_non_tty_stderr_is_plain(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The auto-detecting module console (nothing set explicitly): stderr is
    rendered as stderr is — a captured, non-TTY stream gets no escapes."""
    with pytest.raises(SystemExit):
        refuse("boom", console=Console())
    captured = capsys.readouterr()
    assert captured.err == "Error: boom\n", repr(captured.err)


# --- r1 F3: a halted board's refusal (exit 8) through refuse() -----------------------


def _halt(monkeypatch: pytest.MonkeyPatch) -> None:
    state = ControlState(mode="halt", reason="freeze", by="Ops", at="2026-01-01")
    monkeypatch.setattr(KanbanService, "control_state", lambda self, **kw: state)


@pytest.mark.parametrize(
    "args", [["next"], ["list", "--pickable"]], ids=["next", "list-pickable"]
)
def test_halted_board_refusal_prints_on_stderr(
    board: Path, monkeypatch: pytest.MonkeyPatch, args: list[str]
) -> None:
    _halt(monkeypatch)
    result = _run(args)
    shown = _shown(result)
    assert result.exit_code == 8, shown
    assert "Error: board halted by Ops" in result.stderr, shown
    assert "board halted" not in result.stdout, shown


def test_control_halted_board_json_refusal_on_stdout(
    board: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _halt(monkeypatch)
    result = _run(["list", "--pickable", "--json"])
    shown = _shown(result)
    assert result.exit_code == 8, shown
    payload = json.loads(result.stdout)
    assert payload["success"] is False, shown
    assert payload["error"].startswith("board halted by Ops"), shown
