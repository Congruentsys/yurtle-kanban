"""Issue #1086 — the remaining refusals bypass ``refuse()`` and print on stdout.

Found while fixing #1080 (PR #1084): text-mode refusals through the shared
``refuse()`` print on stderr, but ``move``'s own ``Error: Item not found`` and
``Unknown status`` lines were ``console.print`` + ``sys.exit(1)``, so they still
printed on stdout. The addendum adds ``_print_outcome``'s ``Error:`` line (a
non-zero claim/bounce/control sync outcome) and ``move``'s ``BoardHalted`` refusal
(exit 8).

Expected: every text-mode refusal goes through ``refuse()`` — one stream (stderr),
one wording, a JSON refusal under ``--json`` — each with its exit code.

Reds: the four refusals above print on stdout today. None of ``move`` / ``claim``
takes ``--json``, so the JSON half is pinned on ``_print_outcome`` itself under a
context that asked for JSON: one object on stdout, its exit code kept.

The static sweep's rule (pinned here): in ``cli.py``, ``hdd_commands.py`` and
``epic_commands.py``, no ``console.print`` whose first argument's literal text
starts with ``[red]Error`` (plain or f-string, leading spaces ignored) is
IMMEDIATELY followed, as the next statement of its block, by an exit
(``sys.exit(...)`` or ``raise SystemExit(...)``). Such a pair is an ``Error:``
refusal written by hand; it goes through ``refuse()`` instead. Red lines of other
wording followed by an exit (``Unknown type``, ``Failed: ...``) are outside this
issue's rule; ``move``'s ``Unknown status`` is pinned by its behavioural test.
"""

from __future__ import annotations

import ast
import json
import subprocess
from collections.abc import Iterator
from pathlib import Path

import click
import pytest
from click.testing import CliRunner, Result

from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import _print_outcome, main
from yurtle_kanban.service import ControlState, KanbanService
from yurtle_kanban.sync import Outcome

PKG = Path(__file__).resolve().parents[2] / "src" / "yurtle_kanban"
SWEPT = ("cli.py", "hdd_commands.py", "epic_commands.py")

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
id: {id}
title: "Hello"
type: feature
status: {status}
priority: medium
created: 2026-01-01
---

# {id}: Hello
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
    """A git repo (no remote) with FEAT-001 in backlog and FEAT-002 ready; the cwd."""
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    monkeypatch.delenv("YURTLE_AGENT", raising=False)
    repo = tmp_path / "repo"
    (repo / ".kanban").mkdir(parents=True)
    (repo / ".kanban" / "config.yaml").write_text(CONFIG)
    features = repo / "kanban-work" / "features"
    features.mkdir(parents=True)
    (features / "FEAT-001-hello.md").write_text(ITEM.format(id="FEAT-001", status="backlog"))
    (features / "FEAT-002-hello.md").write_text(ITEM.format(id="FEAT-002", status="ready"))
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


def _halt(monkeypatch: pytest.MonkeyPatch) -> None:
    state = ControlState(mode="halt", reason="freeze", by="Ops", at="2026-01-01")
    monkeypatch.setattr(KanbanService, "control_state", lambda self, **kw: state)


def _assert_on_stderr(result: Result, code: int, *lines: str) -> None:
    shown = _shown(result)
    assert result.exit_code == code, shown
    for line in lines:
        assert line in result.stderr, shown
        assert line not in result.stdout, shown
    assert result.stdout.strip() == "", shown


# --- move's own refusals ------------------------------------------------------------


def test_move_item_not_found_on_stderr(board: Path) -> None:
    result = _run(["move", "NOSUCH-1", "done", "--agent", "Tester"])
    _assert_on_stderr(result, 1, "Error: Item not found: NOSUCH-1")


def test_move_unknown_status_on_stderr(board: Path) -> None:
    """Worded as `list --status`'s refusal (#1080), with the valid names kept."""
    result = _run(["move", "FEAT-001", "bogus", "--agent", "Tester"])
    _assert_on_stderr(result, 1, "Unknown status: bogus", "Valid statuses:")


def test_move_on_halted_board_on_stderr_exit_8(
    board: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _halt(monkeypatch)
    result = _run(["move", "FEAT-002", "in_progress", "--agent", "Tester"])
    _assert_on_stderr(result, 8, "Error: Can't move FEAT-002", "board halted by Ops")


# --- _print_outcome: a non-zero sync outcome ----------------------------------------


def test_claim_refused_outcome_on_stderr(board: Path) -> None:
    """A backlog item can't be claimed: `refused`, exit 1, through `_print_outcome`."""
    result = _run(["claim", "FEAT-001", "--agent", "Tester"])
    _assert_on_stderr(result, 1, "Error: Can't claim FEAT-001")


def test_claim_on_halted_board_on_stderr_exit_8(board: Path) -> None:
    """No remote: claim reads the halt from the working tree's control.yaml (#582)."""
    (board / ".kanban" / "control.yaml").write_text(
        "mode: halt\nreason: freeze\nby: Ops\nat: '2026-01-01'\n"
    )
    _git(board, "add", "-A")
    _git(board, "commit", "-q", "-m", "halt")
    result = _run(["claim", "FEAT-002", "--agent", "Tester"])
    _assert_on_stderr(result, 8, "Error: Can't claim FEAT-002", "board halted by Ops")


def test_control_claim_success_line_stays_on_stdout(board: Path) -> None:
    """`_print_outcome`'s success line is output, not a refusal: stdout."""
    result = _run(["claim", "FEAT-002", "--agent", "Tester"])
    shown = _shown(result)
    assert result.exit_code == 0, shown
    assert "FEAT-002" in result.stdout, shown
    assert "Error" not in result.stderr, shown


@pytest.mark.parametrize(
    ("outcome", "code"),
    [
        (Outcome(kind="refused", message="nope"), 1),
        (Outcome(kind="refused", message="board halted by Ops", halted=True), 8),
    ],
    ids=["refused", "halted"],
)
def test_print_outcome_json_refusal_is_one_object_on_stdout(
    capsys: pytest.CaptureFixture[str], outcome: Outcome, code: int
) -> None:
    """Under a command that asked for `--json`, a non-zero outcome is one JSON
    object on stdout, with its exit code (#877)."""
    ctx = click.Context(click.Command("x"))
    ctx.params["as_json"] = True
    with ctx, pytest.raises(SystemExit) as exc:
        _print_outcome(outcome)
    assert exc.value.code == code
    captured = capsys.readouterr()
    payload = json.loads(captured.out)  # the whole of stdout: exactly one object
    assert payload["success"] is False, captured
    assert payload["error"] == outcome.message, captured
    assert "Error:" not in captured.err, captured


# --- static sweep -------------------------------------------------------------------


def _literal_start(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr) and node.values:
        first = node.values[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            return first.value
    return None


def _is_red_print(stmt: ast.stmt) -> bool:
    if not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)):
        return False
    call = stmt.value
    func = call.func
    if not (
        isinstance(func, ast.Attribute)
        and func.attr == "print"
        and isinstance(func.value, ast.Name)
        and func.value.id == "console"
        and call.args
    ):
        return False
    text = _literal_start(call.args[0])
    return text is not None and text.lstrip().startswith("[red]Error")


def _is_exit(stmt: ast.stmt) -> bool:
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
        func = stmt.value.func
        return (
            isinstance(func, ast.Attribute)
            and func.attr == "exit"
            and isinstance(func.value, ast.Name)
            and func.value.id == "sys"
        )
    if isinstance(stmt, ast.Raise) and stmt.exc is not None:
        exc = stmt.exc.func if isinstance(stmt.exc, ast.Call) else stmt.exc
        return isinstance(exc, ast.Name) and exc.id == "SystemExit"
    return False


def _hand_written_refusals(path: Path) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
        for field in ("body", "orelse", "finalbody"):
            block = getattr(node, field, None)
            if not isinstance(block, list) or not block or not isinstance(block[0], ast.stmt):
                continue
            for printed, then in zip(block, block[1:]):
                if _is_red_print(printed) and _is_exit(then):
                    found.append(f"{path.name}:{printed.lineno}")
    return found


def test_no_hand_written_refusal_remains() -> None:
    found = [hit for name in SWEPT for hit in _hand_written_refusals(PKG / name)]
    assert found == [], "`[red]Error` line + exit outside refuse(): " + ", ".join(found)
