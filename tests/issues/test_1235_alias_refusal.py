"""Issue #1235: `list --pickable -a X` refused with "drop --status/--assignee" though the
user typed the deprecated `-a`. The refusal names the flags actually given."""

from __future__ import annotations

from click.testing import CliRunner

from yurtle_kanban.cli import main


def _refusal(args: list[str]) -> str:
    result = CliRunner().invoke(main, ["list", "--pickable", *args])
    assert result.exit_code == 2, result.output
    return result.stderr


def test_deprecated_a_is_named_in_the_refusal() -> None:
    err = _refusal(["-a", "x"])
    assert "drop -a" in err, err
    assert "--assignee" not in err.split("drop", 1)[1], err


def test_new_form_is_named_as_given() -> None:
    assert "drop --assignee" in _refusal(["--assignee", "x"])


def test_status_and_assignee_both_named() -> None:
    err = _refusal(["--status", "ready", "--assignee", "x"])
    assert "drop --status/--assignee" in err, err


def test_status_alone_is_named() -> None:
    err = _refusal(["--status", "ready"])
    assert "drop --status" in err and "--assignee" not in err.split("drop", 1)[1], err
