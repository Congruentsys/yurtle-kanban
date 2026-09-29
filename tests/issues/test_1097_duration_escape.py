"""Issue #1097: `_duration`'s `BadParameter` pass-through escapes its message
(#1091), but the only producer (`parse_duration`) quotes with `!r`, so nothing
pinned it. A patched producer raising raw control characters does."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from yurtle_kanban import cli
from yurtle_kanban.cli import main


def test_duration_refusal_escapes_raw_control_characters(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def parse(value: str) -> None:
        raise ValueError("bad \x1b[2J\nFORGED")

    monkeypatch.setattr(cli, "parse_duration", parse)
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(main, ["list", "--older-than", "1d"])
    assert result.exit_code == 2, result.output
    assert "\x1b" not in result.stderr, repr(result.stderr)
    assert "bad \\x1b[2J\\nFORGED" in result.stderr, repr(result.stderr)
    assert not any(ln.startswith("FORGED") for ln in result.stderr.splitlines())
