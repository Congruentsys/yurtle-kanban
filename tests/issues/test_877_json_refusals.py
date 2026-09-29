"""Issue #877 — a ``--json`` command's refusal prints one JSON object on stdout.

Split from #847 (PR #843 review). Across the ``--json`` commands a refusal prints
plain text (``Unknown status: …``, ``Invalid <config>: …``, ``Error: …`` on stderr
from main's ``InputRefused`` handler, the pre-command ``argument N contains invalid
UTF-8`` check), or a JSON dict without ``success`` (``show``, ``states``), or #847's
``next-id`` dict without ``error``. A script parsing stdout gets nothing usable.

Decided ([steer] on #877, bucket 2), in ONE place in the CLI's error handling:
- with ``--json``, any refusal (``InputRefused``, a ``ValueError`` as the handler
  reports it today, a bad option value, an invalid config, or an argument refused
  before the command runs) prints exactly ONE JSON object on stdout —
  ``{"success": false, "error": "<message>"}``, extra keys allowed — and exits 1;
- ``next-id --json`` keeps #847's keys (``success``, ``id``, ``prefix``, ``number``,
  ``message``) and gains ``error``;
- without ``--json`` the output is unchanged.

The ``--json`` commands: list, show, states, boards, roadmap, history, metrics,
next-id, validate, query, hdd validate, hdd critical-path, experiment status.

Reds: one cheap refusal per command; an invalid config for every command; an
undecodable argument for every command (real subprocess, as #193's tests do); an
``InputRefused`` raised from a command body (main's handler, and the ``hdd``
group's). Controls: each command's success JSON, and the non-json refusals.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner, Result

from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import main
from yurtle_kanban.models import InputRefused
from yurtle_kanban.service import KanbanService

SRC = Path(__file__).resolve().parents[2] / "src"
BAD = b"A\xff"  # undecodable argv bytes -> "A\udcff" under surrogateescape
NEXT_ID_KEYS = {"success", "id", "prefix", "number", "message"}

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


@dataclass(frozen=True)
class Cmd:
    ok: list[str]  # a success path: prints its normal JSON, exit 0
    refused: list[str]  # a cheap refusal on the good board
    bad: list[str | bytes]  # the refusal's argv with an undecodable argument


CMDS: dict[str, Cmd] = {
    "list": Cmd(
        ["list", "--json"],
        ["list", "--status", "bogus", "--json"],
        ["list", "--status", BAD, "--json"],
    ),
    "show": Cmd(
        ["show", "FEAT-001", "--json"],
        ["show", "FEAT-999", "--json"],
        ["show", BAD, "--json"],
    ),
    "states": Cmd(
        ["states", "--json"],
        ["states", "--board", "nope", "--json"],
        ["states", "--board", BAD, "--json"],
    ),
    "boards": Cmd(
        ["boards", "--json"],
        [],  # no option to refuse: covered by the invalid-config case
        ["boards", "--json", BAD],
    ),
    "roadmap": Cmd(
        ["roadmap", "--json"],
        ["roadmap", "--type", "bogus", "--json"],
        ["roadmap", "--type", BAD, "--json"],
    ),
    "history": Cmd(
        ["history", "--json"],
        ["history", "--since", "notadate", "--json"],
        ["history", "--since", BAD, "--json"],
    ),
    "metrics": Cmd(
        ["metrics", "--json"],
        [],  # an unknown item id is not a refusal (exit 0): invalid-config case
        ["metrics", BAD, "--json"],
    ),
    "next-id": Cmd(
        ["next-id", "FEAT", "--no-sync", "--no-commit", "--json"],
        ["next-id", "1AB", "--no-sync", "--no-commit", "--json"],
        ["next-id", BAD, "--no-sync", "--no-commit", "--json"],
    ),
    "validate": Cmd(
        ["validate", "--json"],
        [],  # no option to refuse: invalid-config case
        ["validate", "--json", BAD],
    ),
    "query": Cmd(
        ["query", "hello", "--json", "--no-semantic"],
        ["query", "--json", "--no-semantic"],  # no query text
        ["query", BAD, "--json", "--no-semantic"],
    ),
    "hdd-validate": Cmd(
        ["hdd", "validate", "--json"],
        [],  # invalid-config case
        ["hdd", "validate", "--json", BAD],
    ),
    "hdd-critical-path": Cmd(
        ["hdd", "critical-path", "--json"],
        [],  # invalid-config case
        ["hdd", "critical-path", "--agent", BAD, "--json"],
    ),
    "experiment-status": Cmd(
        # this board has no experiment to succeed on: the success path is pinned
        # on an HDD board in test_905 (#905)
        [],
        ["experiment", "status", "EXPR-1", "--json"],  # unknown: a refusal (#905)
        ["experiment", "status", BAD, "--json"],
    ),
}

# extra per-command refusals on the good board, beyond CMDS[...].refused
EXTRA_REFUSALS: dict[str, list[str]] = {
    # `_refuse` (a ValueError from check_identity) prints `Error: …` today
    "list-assignee-empty": ["list", "--assignee", "", "--json"],
    # a SPARQL parse error prints to stderr, stdout empty
    "query-bad-sparql": ["query", "--sparql", "SELEC nonsense", "--json"],
}

REFUSALS = {
    **{name: c.refused for name, c in CMDS.items() if c.refused},
    **EXTRA_REFUSALS,
}


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_theme_cache() -> Iterator[None]:
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)


@pytest.fixture
def board(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A git repo with a software board holding FEAT-001; the cwd."""
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
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


@pytest.fixture
def bad_config(board: Path) -> Path:
    """The same board with a config that isn't a mapping: every command refuses."""
    (board / ".kanban" / "config.yaml").write_text("- not a mapping\n")
    return board


def _run(args: list[str]) -> Result:
    return CliRunner().invoke(main, args)


def _run_bytes(repo: Path, args: list[str | bytes]) -> tuple[int, str, str]:
    """The real CLI in a subprocess, argv as raw bytes (see #193's tests: a
    surrogate in-process can poison cli.py's module-level Console)."""
    env = {**os.environ, "PYTHONPATH": str(SRC), "PYTHONDONTWRITEBYTECODE": "1"}
    env.pop("PYTHONIOENCODING", None)
    argv = [a if isinstance(a, bytes) else a.encode("utf-8") for a in args]
    proc = subprocess.run(
        [
            os.fsencode(sys.executable),
            b"-c",
            b"from yurtle_kanban.cli import main; main()",
            *argv,
        ],
        cwd=repo,
        env=env,
        capture_output=True,
        timeout=120,
    )
    return (
        proc.returncode,
        proc.stdout.decode("utf-8", "replace"),
        proc.stderr.decode("utf-8", "replace"),
    )


def _assert_json_refusal(code: int, stdout: str, stderr: str) -> dict[str, Any]:
    """Exit 1 and stdout is exactly one JSON object with success false and an error."""
    shown = f"exit {code}\n--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}"
    assert code == 1, shown
    try:
        obj = json.loads(stdout)
    except json.JSONDecodeError as e:
        pytest.fail(f"--json refusal: stdout is not one JSON object ({e})\n{shown}")
    assert isinstance(obj, dict), shown
    assert obj.get("success") is False, shown
    error = obj.get("error")
    assert isinstance(error, str) and error.strip(), shown
    assert not stdout.lstrip().startswith("Error:"), shown
    return obj


def _assert_result_refusal(result: Result) -> dict[str, Any]:
    return _assert_json_refusal(result.exit_code, result.stdout, result.stderr)


# ---------------------------------------------------------------------------
# Reds: every --json refusal is one JSON object on stdout
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("args", list(REFUSALS.values()), ids=list(REFUSALS))
def test_json_refusal_is_one_json_object(board: Path, args: list[str]) -> None:
    _assert_result_refusal(_run(args))


@pytest.mark.parametrize("name", list(CMDS))
def test_json_invalid_config_is_one_json_object(bad_config: Path, name: str) -> None:
    obj = _assert_result_refusal(_run(CMDS[name].ok or CMDS[name].refused))
    assert "mapping" in obj["error"], obj


@pytest.mark.parametrize("name", list(CMDS))
def test_json_undecodable_argument_is_one_json_object(board: Path, name: str) -> None:
    """The pre-command argv check (#193/#239) refuses before the command body."""
    code, out, err = _run_bytes(board, CMDS[name].bad)
    obj = _assert_json_refusal(code, out, err)
    assert "invalid UTF-8" in obj["error"], obj


@pytest.mark.parametrize(
    ("args", "method"),
    [
        pytest.param(["list", "--json"], "get_items", id="main-list"),
        pytest.param(
            ["hdd", "critical-path", "--json"], "get_critical_path", id="hdd-critical-path"
        ),
    ],
)
def test_json_input_refused_in_command_body(
    board: Path, monkeypatch: pytest.MonkeyPatch, args: list[str], method: str
) -> None:
    """An InputRefused from a command body: `_click.Group.invoke`'s handler (#666)."""

    def refuse(self: KanbanService, *a: object, **kw: object) -> None:
        raise InputRefused("refused for #877")

    monkeypatch.setattr(KanbanService, method, refuse)
    obj = _assert_result_refusal(_run(args))
    assert "refused for #877" in obj["error"], obj


def test_next_id_json_refusal_keeps_847_keys_and_gains_error(board: Path) -> None:
    result = _run(CMDS["next-id"].refused)
    obj = _assert_result_refusal(result)
    assert NEXT_ID_KEYS <= obj.keys(), obj
    assert obj["id"] is None and obj["number"] is None, obj
    assert obj["prefix"] == "1AB", obj


# ---------------------------------------------------------------------------
# Controls: success JSON and non-json refusals are unchanged
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", list(CMDS))
def test_control_json_success_unchanged(board: Path, name: str) -> None:
    if not CMDS[name].ok:
        pytest.skip(f"{name}: success path pinned elsewhere (test_905)")
    result = _run(CMDS[name].ok)
    shown = f"{result.stdout}\n--- stderr ---\n{result.stderr}"
    assert result.exit_code == 0, shown
    obj = json.loads(result.stdout)
    if isinstance(obj, dict):
        assert "error" not in obj, shown
        assert obj.get("success") is not False, shown


def test_control_next_id_json_success_keys(board: Path) -> None:
    result = _run(CMDS["next-id"].ok)
    obj = json.loads(result.stdout)
    assert result.exit_code == 0, result.stdout
    assert obj["success"] is True and obj["id"] == "FEAT-002", obj
    assert NEXT_ID_KEYS <= obj.keys(), obj


def test_control_non_json_refused_value_prints_error(board: Path) -> None:
    result = _run(["list", "--assignee", ""])
    assert result.exit_code == 1, result.output
    # refusals print on stderr (#1080)
    assert "Error: --assignee is empty" in result.stderr, result.output


def test_control_non_json_input_refused_prints_error(
    board: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(self: KanbanService, *a: object, **kw: object) -> None:
        raise InputRefused("refused for #877")

    monkeypatch.setattr(KanbanService, "get_items", refuse)
    result = _run(["list"])
    assert result.exit_code == 1, result.output
    assert "Error: refused for #877" in result.stderr, result.output
    assert not result.stdout.strip().startswith("{"), result.output


@pytest.mark.parametrize(
    ("args", "text"),
    [
        pytest.param(["list", "--status", "bogus"], "Unknown status: bogus", id="list"),
        pytest.param(["show", "FEAT-999"], "Item not found: FEAT-999", id="show"),
        pytest.param(["states", "--board", "nope"], "Unknown board: nope", id="states"),
        pytest.param(
            ["next-id", "1AB", "--no-sync", "--no-commit"], "is not an ID prefix", id="next-id"
        ),
    ],
)
def test_control_non_json_refusal_is_plain_text(board: Path, args: list[str], text: str) -> None:
    result = _run(args)
    assert result.exit_code == 1, result.output
    assert text in result.output, result.output
    assert not result.stdout.strip().startswith("{"), result.output


def test_control_non_json_invalid_config_is_plain_text(bad_config: Path) -> None:
    result = _run(["list"])
    assert result.exit_code == 1, result.output
    assert "Invalid " in result.stderr and "mapping" in result.stderr, result.output


def test_control_non_json_undecodable_argument_is_plain_text(board: Path) -> None:
    code, out, err = _run_bytes(board, ["show", BAD])
    assert code == 1, out + err
    assert "invalid UTF-8" in out + err, out + err
    assert not out.strip().startswith("{"), out
