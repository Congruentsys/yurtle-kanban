"""Issue #1090, review r1 F1: `list --priority` with an unknown value printed its
red refusal lines on stdout (a `for` loop of prints, then `sys.exit(1)` in a
sibling `if`). The AST sweep in test_1090_red_refusals.py sees that shape since
#1099 (its function-wide rule); this file is the behavioural pin.

Expected: one refusal through `refuse()`: every unknown value's line on stderr,
nothing on stdout, exit 1; under `--json` one object naming every value."""

from __future__ import annotations

from pathlib import Path

from tests.issues import test_1090_red_refusals as base

# the sibling file's fixtures, used here by name
board = base.board
_clean_theme_cache = base._clean_theme_cache


def test_list_unknown_priority_on_stderr(board: Path) -> None:
    result = base._run(["list", "--priority", "bogus,high,nope"])
    base._assert_on_stderr(result, 1, "Unknown priority: bogus", "Unknown priority: nope")


def test_list_unknown_priority_json_is_one_object(board: Path) -> None:
    result = base._run(["list", "--priority", "bogus,nope", "--json"])
    base._assert_json_refusal(result, 1, "Unknown priority: bogus")
    base._assert_json_refusal(result, 1, "Unknown priority: nope")
