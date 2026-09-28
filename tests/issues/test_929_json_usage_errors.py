"""Issue #929 — ``--json`` contract gaps left by #877 (PR #927 review).

1. A click usage error under ``--json`` (a bad option value, an unknown option, a
   missing argument, an unknown subcommand) prints click's plain usage text and
   exits 2, so a script parsing stdout gets nothing usable.
2. ``argv_requests_json`` counts a ``--json`` that is the VALUE of the preceding
   value-taking option (``list --assignee --json``) as a JSON request. That only
   matters on the undecodable-argument path (``_Main.parse_args``), which refuses
   before click has parsed the command's options.

Decided ([steer] on #929, bucket 2):
- under ``--json`` a usage error prints exactly ONE JSON object on stdout,
  ``{"success": false, "error": <click's message>}``, and the exit code STAYS 2;
- without ``--json`` nothing changes (exit 2, click's usage text);
- ``argv_requests_json`` stops counting a ``--json`` that is the value of the
  preceding value-taking option.

Note: ``history`` has no ``--limit`` option, so the issue's
``history --json --limit abc`` is an unknown-option error; the bad-value case is
``query --json --top abc`` (``--top`` is an ``IntRange``).

Item 2 goes through the real path, not a unit test of ``argv_requests_json``: its
signature takes only ``args`` and cannot know which options take a value without
the command, so the fix may change it. The observable contract is what the
undecodable-argument refusal prints.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner, Result

from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import main

SRC = Path(__file__).resolve().parents[2] / "src"
BAD = b"A\xff"  # undecodable argv bytes -> "A\udcff" under surrogateescape

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

# name -> (argv under --json, a fragment of click's message the error must carry)
USAGE_ERRORS: dict[str, tuple[list[str], str]] = {
    # `history` has no --limit: click's "No such option '--limit'"
    "history-unknown-limit": (["history", "--json", "--limit", "abc"], "--limit"),
    "list-unknown-option": (["list", "--json", "--bogus"], "--bogus"),
    # a bad option value: --top is an IntRange
    "query-bad-value": (["query", "--json", "--no-semantic", "--top", "abc"], "abc"),
    "show-missing-argument": (["show", "--json"], "ITEM_ID"),
    "hdd-unknown-subcommand": (["hdd", "bogus", "--json"], "bogus"),
}


def _without_json(args: list[str]) -> list[str]:
    return [a for a in args if a != "--json"]


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


def _run(args: list[str]) -> Result:
    return CliRunner().invoke(main, args)


def _run_bytes(repo: Path, args: list[str | bytes]) -> tuple[int, str, str]:
    """The real CLI in a subprocess, argv as raw bytes (as #193's / #877's tests)."""
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


def _json_object(code: int, stdout: str, stderr: str, exit_code: int) -> dict[str, Any]:
    """`exit_code`, and stdout is exactly one JSON object: success false, an error."""
    shown = f"exit {code}\n--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}"
    assert code == exit_code, shown
    try:
        obj = json.loads(stdout)
    except json.JSONDecodeError as e:
        pytest.fail(f"--json: stdout is not one JSON object ({e})\n{shown}")
    assert isinstance(obj, dict), shown
    assert obj.get("success") is False, shown
    error = obj.get("error")
    assert isinstance(error, str) and error.strip(), shown
    return obj


def _is_json(text: str) -> bool:
    try:
        json.loads(text)
    except json.JSONDecodeError:
        return False
    return True


# ---------------------------------------------------------------------------
# 1. A usage error under --json is one JSON object on stdout, exit 2
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", list(USAGE_ERRORS))
def test_json_usage_error_is_one_json_object_exit_2(board: Path, name: str) -> None:
    args, fragment = USAGE_ERRORS[name]
    result = _run(args)
    obj = _json_object(result.exit_code, result.stdout, result.stderr, exit_code=2)
    assert fragment in obj["error"], obj
    # click's message, not its usage banner
    assert not obj["error"].startswith("Usage:"), obj


# ---------------------------------------------------------------------------
# 2. Controls: without --json a usage error is unchanged
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", list(USAGE_ERRORS))
def test_control_non_json_usage_error_unchanged(board: Path, name: str) -> None:
    args, fragment = USAGE_ERRORS[name]
    result = _run(_without_json(args))
    assert result.exit_code == 2, result.output
    assert not _is_json(result.stdout), result.stdout
    assert "Usage:" in result.output and "Error:" in result.output, result.output
    assert fragment in result.output, result.output


# ---------------------------------------------------------------------------
# 3. argv_requests_json: a `--json` that is an option's VALUE is no JSON request
#    (via the undecodable-argument refusal in `_Main.parse_args`)
# ---------------------------------------------------------------------------


def test_json_as_option_value_is_not_a_json_request(board: Path) -> None:
    """`--assignee --json`: `--json` is --assignee's value, so the undecodable
    `--status` value is refused as plain text, exit 1 (#193), not as JSON."""
    code, out, err = _run_bytes(board, ["list", "--assignee", "--json", "--status", BAD])
    shown = f"exit {code}\n--- stdout ---\n{out}\n--- stderr ---\n{err}"
    assert code == 1, shown
    assert "invalid UTF-8" in out + err, shown
    assert not _is_json(out), shown
    assert not out.strip().startswith("{"), shown


@pytest.mark.parametrize(
    "args",
    [
        pytest.param(["list", "--json", "--status", BAD], id="bare-json"),
        pytest.param(
            ["list", "--assignee", "x", "--json", "--status", BAD], id="json-after-valued-option"
        ),
    ],
)
def test_control_real_json_flag_is_a_json_request(
    board: Path, args: list[str | bytes]
) -> None:
    code, out, err = _run_bytes(board, args)
    obj = _json_object(code, out, err, exit_code=1)
    assert "invalid UTF-8" in obj["error"], obj
