"""#535: board warnings use the board's resolved name; one wording for scan paths.

Follow-ups from the review of the #527 tests. Decided ([steer] on #535):

1. A multi-board board with no ``name:`` (absent, or a bare null) loads as
   ``default`` (``_or_default``). Its empty-``scan_paths``-entry warning and its
   WIP-limit warning (``config.yaml board …``) name ``'default'``, never ``None``.
2. The empty-entry warning reads ``\\`scan_paths\\` entry '' on board 'alpha'``;
   single-board says where in the same style,
   ``\\`scan_paths\\` entry '' in kanban.paths`` (or ``in kanban`` for the
   kanban-level fallback, #482).

Controls: named boards keep their names in both warnings.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from yurtle_kanban import config as config_mod
from yurtle_kanban.config import KanbanConfig

LOGGER = "yurtle-kanban"


def _board(name_line: str, scan: str = '["", "a/"]', wip: str | None = None) -> str:
    """A one-board v2 config; ``name_line`` is the raw ``name:`` line or ``""``."""
    lines = ["  - preset: software", '    path: "a/"', f"    scan_paths: {scan}"]
    if name_line:
        lines.insert(1, f"    {name_line}")
    if wip is not None:
        lines.append(f"    wip_limits: {wip}")
    return 'version: "2.0"\nboards:\n' + "\n".join(lines) + "\n"


def _single(scan_line: str) -> str:
    return f'kanban:\n  theme: software\n  paths:\n    root: "work/"\n    scan_paths: {scan_line}\n'


def _kanban_level(scan_line: str) -> str:
    return f'kanban:\n  theme: software\n  paths:\n    root: "work/"\n  scan_paths: {scan_line}\n'


def _load_logged(
    tmp_path: Path, text: str, caplog: pytest.LogCaptureFixture
) -> tuple[KanbanConfig, list[str]]:
    """The loaded config and every package WARNING message the load emitted."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(text)
    config_mod._theme_cache.clear()
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        config = KanbanConfig.load(cfg)
    msgs = [
        r.getMessage()
        for r in caplog.records
        if r.name.startswith(LOGGER) and r.levelno >= logging.WARNING
    ]
    return config, msgs


def _one(msgs: list[str], word: str) -> str:
    hits = [m for m in msgs if word in m]
    assert len(hits) == 1, msgs
    return hits[0]


UNNAMED = {"absent": "", "null": "name:"}
BAD_WIP = "{in_progress: x}"


# --- 1. an unnamed board is 'default' in its warnings ---------------------------------


@pytest.mark.parametrize("case", sorted(UNNAMED))
def test_unnamed_board_scan_warning_names_default(tmp_path, caplog, case):
    config, msgs = _load_logged(tmp_path, _board(UNNAMED[case]), caplog)
    assert config.boards[0].name == "default"  # the name it loads as
    assert config.boards[0].scan_paths == ["a/"]
    msg = _one(msgs, "scan_paths")
    assert "None" not in msg, msg
    assert "`scan_paths` entry '' on board 'default'" in msg, msg


@pytest.mark.parametrize("case", sorted(UNNAMED))
def test_unnamed_board_wip_warning_names_default(tmp_path, caplog, case):
    config, msgs = _load_logged(tmp_path, _board(UNNAMED[case], '["a/"]', BAD_WIP), caplog)
    assert config.boards[0].name == "default"
    msg = _one(msgs, "wip_limits")
    assert "None" not in msg, msg
    assert "board 'default'" in msg, msg


# --- 2. one wording ------------------------------------------------------------------


def test_named_board_scan_warning_wording(tmp_path, caplog):
    _, msgs = _load_logged(tmp_path, _board("name: alpha"), caplog)
    assert "`scan_paths` entry '' on board 'alpha'" in _one(msgs, "scan_paths"), msgs


def test_named_board_scan_warning_shows_entry(tmp_path, caplog):
    _, msgs = _load_logged(tmp_path, _board("name: alpha", '["   ", "a/"]'), caplog)
    assert "`scan_paths` entry '   ' on board 'alpha'" in _one(msgs, "scan_paths"), msgs


def test_kanban_paths_scan_warning_wording(tmp_path, caplog):
    _, msgs = _load_logged(tmp_path, _single('["", "a/"]'), caplog)
    assert "`scan_paths` entry '' in kanban.paths" in _one(msgs, "scan_paths"), msgs


def test_kanban_level_scan_warning_wording(tmp_path, caplog):
    _, msgs = _load_logged(tmp_path, _kanban_level('["", "a/"]'), caplog)
    msg = _one(msgs, "scan_paths")
    assert "`scan_paths` entry '' in kanban" in msg, msg
    assert "kanban.paths" not in msg, msg


# --- controls -------------------------------------------------------------------------


def test_control_named_board_wip_warning_keeps_name(tmp_path, caplog):
    config, msgs = _load_logged(tmp_path, _board("name: alpha", '["a/"]', BAD_WIP), caplog)
    assert config.boards[0].name == "alpha"
    assert "board 'alpha'" in _one(msgs, "wip_limits"), msgs


def test_control_named_board_loads_unchanged(tmp_path, caplog):
    config, msgs = _load_logged(tmp_path, _board("name: alpha", '["a/", "b/"]'), caplog)
    assert config.boards[0].name == "alpha"
    assert config.boards[0].scan_paths == ["a/", "b/"]
    assert msgs == [], msgs
