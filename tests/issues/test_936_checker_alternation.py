"""Issue #936: the doc/skill command checker (tests/test_skill_commands_execute.py).

1. `|`-alternatives inside a synopsis's brackets are split, each flag checked:
   `[--status <s>|--all]` checks `--all`; `[--a|--b]` is two flags, not one.
2. A backslash-quoted heredoc terminator (`cat <<\\EOF`) is a heredoc: its body is
   data, never checked as commands.
"""

from __future__ import annotations

from tests.test_skill_commands_execute import _block_commands, _flags, _parse, _rejection


def test_alternation_after_a_value_is_checked() -> None:
    words = _parse("yurtle-kanban list [--status <s>|--all]")
    assert "--all" in _flags(words), words
    assert _rejection(*words) is None, _rejection(*words)


def test_alternation_of_two_flags_is_two_flags() -> None:
    words = _parse("yurtle-kanban list [--json|--bogus]")
    assert {"--json", "--bogus"} <= set(_flags(words)), words
    problem = _rejection(*words)
    assert problem is not None and "--bogus" in problem, problem


def test_control_real_alternatives_are_accepted() -> None:
    words = _parse("yurtle-kanban list [--status <s>|--json]")
    assert _rejection(*words) is None, _rejection(*words)


def test_control_a_pipe_outside_brackets_is_untouched() -> None:
    assert _parse("yurtle-kanban list --json | head") == ("list", "--json", "|", "head")


def test_backslash_quoted_heredoc_body_is_skipped() -> None:
    lines = ["cat <<\\EOF", "yurtle-kanban --bogus", "EOF", "yurtle-kanban list"]
    commands = [text for _, text in _block_commands(lines, 1)]
    assert not any("--bogus" in c for c in commands), commands
    assert "yurtle-kanban list" in commands, commands
