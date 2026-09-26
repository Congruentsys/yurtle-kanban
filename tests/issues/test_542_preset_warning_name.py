"""#542 part 1: an unnamed board's preset warning names 'default', never None.

A multi-board board with no ``name:`` (absent, or a bare null) loads as
``default`` (``_or_default``). ``_theme_name`` was handed
``f" for board {data.get('name')!r}"``, so a bad ``preset:`` (an unknown theme
name, or an empty one) warned ``for board None``. The same label is used in the
ValueError for a non-string preset. Like #535's scan-path and WIP warnings, all of
them use the board's resolved name.

Control: a named board keeps its name.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from yurtle_kanban import config as config_mod
from yurtle_kanban.config import KanbanConfig

LOGGER = "yurtle-kanban"

UNNAMED = {"absent": "", "null": "name:"}
BAD_PRESET = {"unknown": "no-such-theme-542", "empty": '""'}


def _board(name_line: str, preset: str) -> str:
    """A one-board v2 config; ``name_line`` is the raw ``name:`` line or ``""``."""
    lines = [f"  - preset: {preset}", '    path: "a/"']
    if name_line:
        lines.insert(1, f"    {name_line}")
    return 'version: "2.0"\nboards:\n' + "\n".join(lines) + "\n"


def _write(tmp_path: Path, text: str) -> Path:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(text)
    config_mod._theme_cache.clear()
    return cfg


def _preset_warning(
    tmp_path: Path, text: str, caplog: pytest.LogCaptureFixture
) -> tuple[KanbanConfig, str]:
    cfg = _write(tmp_path, text)
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        config = KanbanConfig.load(cfg)
    hits = [
        r.getMessage()
        for r in caplog.records
        if r.name.startswith(LOGGER) and "`preset`" in r.getMessage()
    ]
    assert len(hits) == 1, [r.getMessage() for r in caplog.records]
    return config, hits[0]


@pytest.mark.parametrize("bad", sorted(BAD_PRESET))
@pytest.mark.parametrize("case", sorted(UNNAMED))
def test_unnamed_board_preset_warning_names_default(tmp_path, caplog, case, bad):
    config, msg = _preset_warning(tmp_path, _board(UNNAMED[case], BAD_PRESET[bad]), caplog)
    assert config.boards[0].name == "default"  # the name it loads as
    assert "None" not in msg, msg
    assert "board 'default'" in msg, msg


@pytest.mark.parametrize("case", sorted(UNNAMED))
def test_unnamed_board_non_string_preset_error_names_default(tmp_path, case):
    cfg = _write(tmp_path, _board(UNNAMED[case], "5"))
    with pytest.raises(ValueError) as info:
        KanbanConfig.load(cfg)
    msg = str(info.value)
    assert "None" not in msg, msg
    assert "board 'default'" in msg, msg


@pytest.mark.parametrize("bad", sorted(BAD_PRESET))
def test_control_named_board_preset_warning_keeps_name(tmp_path, caplog, bad):
    config, msg = _preset_warning(tmp_path, _board("name: alpha", BAD_PRESET[bad]), caplog)
    assert config.boards[0].name == "alpha"
    assert "board 'alpha'" in msg, msg
