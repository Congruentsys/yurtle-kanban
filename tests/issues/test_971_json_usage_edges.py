"""Issue #971 — ``--json`` usage-error edges left by #929 (PR #966 review).

1. ``argv_requests_json`` walks a cluster of short options (``-vn``) as one
   unknown token, so when the cluster's LAST letter takes a value and nothing
   follows it in the token, the next ``--json`` is counted as a request although
   click reads it as that option's value: ``query -vn --json abc`` gives ``-n``
   the value ``--json``, yet the usage error comes out as JSON. Such pairs today:
   ``query`` (``-v`` + ``-n``) and ``move`` (``-f`` + ``-e``).
2. A command with no ``--json`` option answers ``--json`` with JSON:
   ``move X done --json`` -> ``{"success": false, "error": "No such option
   '--json'."}``, exit 2. [steer] bucket 2: that IS the #877/#929 contract; it
   keeps the JSON (a changelog line, no special case). Pinned here.
3. A usage error in the ROOT's own options (``yurtle-kanban --bogus list
   --json``) is raised in ``_Main.parse_args`` before ``Group.invoke``, so it
   stays click's plain usage text instead of one JSON object.

Decided ([steer] on #971): parts 1 and 3 are fixed, part 2 is kept.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.issues import test_929_json_usage_errors as t929
from tests.issues.test_929_json_usage_errors import _is_json, _json_object, _run
from yurtle_kanban._click import argv_requests_json
from yurtle_kanban.cli import main

# #929's fixtures: its software board (FEAT-001) as the cwd, a clean theme cache
board = t929.board
_clean_theme_cache = t929._clean_theme_cache

# ---------------------------------------------------------------------------
# 1. A short-option cluster ending in a value-taking option eats the next arg
# ---------------------------------------------------------------------------

CLUSTER_EATS_JSON: dict[str, list[str]] = {
    # -v flag + -n (--top, takes a value): -n's value is `--json`
    "query-vn": ["query", "-vn", "--json", "abc"],
    # -f flag + -e (--export-board, takes a value): -e's value is `--json`
    "move-fe": ["move", "FEAT-001", "done", "-fe", "--json"],
}


@pytest.mark.parametrize("name", list(CLUSTER_EATS_JSON))
def test_cluster_value_json_is_not_a_json_request(name: str) -> None:
    args = CLUSTER_EATS_JSON[name]
    assert argv_requests_json(args, main) is False, args


def test_cluster_value_json_usage_error_is_plain_text(board: Path) -> None:
    """`query -vn --json abc`: -n's value `--json` is not an integer, a usage
    error with no --json request, so click's plain usage text, exit 2."""
    result = _run(["query", "-vn", "--json", "abc"])
    shown = (
        f"exit {result.exit_code}\n--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )
    assert result.exit_code == 2, shown
    assert not _is_json(result.stdout), shown
    assert not result.stdout.strip().startswith("{"), shown
    assert "Usage:" in result.output and "Error:" in result.output, shown
    assert "--json" in result.output, shown  # the bad value click names


# Controls: a real --json after a COMPLETE cluster is still a request
CLUSTER_THEN_JSON: dict[str, tuple[list[str], str]] = {
    # -n's value is the next arg `0`; then a real --json
    "query-vn-sep-value": (["query", "--no-semantic", "-vn", "0", "--json"], "0"),
    # -n's value is attached in the token (`0`); then a real --json
    "query-vn-attached-value": (["query", "--no-semantic", "-vn0", "--json"], "0"),
    # -n takes the rest of the token (`v`) as its value; then a real --json
    "query-nv": (["query", "--no-semantic", "-nv", "--json"], "'v'"),
    # separate short options, no cluster
    "query-v-n-separate": (["query", "--no-semantic", "-v", "-n", "0", "--json"], "0"),
}


@pytest.mark.parametrize("name", list(CLUSTER_THEN_JSON))
def test_control_json_after_complete_cluster_is_a_request(name: str) -> None:
    args, _ = CLUSTER_THEN_JSON[name]
    assert argv_requests_json(args, main) is True, args


@pytest.mark.parametrize("name", list(CLUSTER_THEN_JSON))
def test_control_json_after_complete_cluster_usage_error_is_json(board: Path, name: str) -> None:
    args, fragment = CLUSTER_THEN_JSON[name]
    result = _run(args)
    obj = _json_object(result.exit_code, result.stdout, result.stderr, exit_code=2)
    assert "--top" in obj["error"] and fragment in obj["error"], obj


# ---------------------------------------------------------------------------
# 2. [steer] pin: a command with no --json option answers --json with JSON
# ---------------------------------------------------------------------------


def test_command_without_json_option_answers_json(board: Path) -> None:
    result = _run(["move", "EXP-1", "done", "--json"])
    obj = _json_object(result.exit_code, result.stdout, result.stderr, exit_code=2)
    assert "--json" in obj["error"], obj
    assert not obj["error"].startswith("Usage:"), obj


# ---------------------------------------------------------------------------
# 3. A usage error in the root's own options, under --json
# ---------------------------------------------------------------------------


def test_root_option_usage_error_under_json_is_one_json_object(board: Path) -> None:
    result = _run(["--bogus", "list", "--json"])
    obj = _json_object(result.exit_code, result.stdout, result.stderr, exit_code=2)
    assert "--bogus" in obj["error"], obj
    assert not obj["error"].startswith("Usage:"), obj


def test_control_root_option_usage_error_without_json_unchanged(board: Path) -> None:
    result = _run(["--bogus", "list"])
    assert result.exit_code == 2, result.output
    assert not _is_json(result.stdout), result.stdout
    assert "Usage:" in result.output and "Error:" in result.output, result.output
    assert "--bogus" in result.output, result.output
