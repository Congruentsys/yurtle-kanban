"""Issue #972: two doc-checker edges (tests/test_skill_commands_execute.py).

1. `<<'\\EOF'`: bash's terminator is literally `\\EOF` (quoting keeps the backslash),
   so the body runs to that line, not to `EOF`.
2. `|` in an OUTER synopsis bracket (`[--a [--x]|--b]`) separates alternatives too.
"""

from __future__ import annotations

from tests.test_skill_commands_execute import _block_commands, _flags, _parse, _rejection


def test_quoted_backslash_terminator_is_kept_literally() -> None:
    lines = ["cat <<'\\EOF'", "EOF", "yurtle-kanban --bogus", "\\EOF", "yurtle-kanban list"]
    commands = [text for _, text in _block_commands(lines, 1)]
    assert not any("--bogus" in c for c in commands), commands
    assert "yurtle-kanban list" in commands, commands


def test_pipe_in_an_outer_bracket_splits() -> None:
    words = _parse("yurtle-kanban list [--json [--status <s>]|--bogus]")
    assert {"--json", "--status", "--bogus"} <= set(_flags(words)), words
    problem = _rejection(*words)
    assert problem is not None and "--bogus" in problem, problem


