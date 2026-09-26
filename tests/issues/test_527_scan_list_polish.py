"""#527: follow-ups from the review of the #517 tests. Decided ([steer] on #527):

1. A whitespace-only scalar ``scan_paths`` (``'  '``) is treated as null: it loads
   as ``[]``, never ``['  ']`` (``Path('  ')`` is not a folder the user meant).
   Choice: "treated as null" means *silent*, like a bare ``scan_paths:`` and
   like the scalar ``""`` already is. The list case warns because a list entry is
   being dropped from a list the user wrote; a scalar that is only whitespace has
   no entry to drop, so it is simply empty.
2. The empty-entry warning names where the entry comes from (``kanban.paths``,
   the kanban-level ``scan_paths`` fallback, or the board by name) and shows the
   dropped entry as its ``repr`` (e.g. ``'   '``), so two boards with an empty
   entry give two distinguishable warnings.

Controls: ordinary lists produce no warning.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from yurtle_kanban import config as config_mod
from yurtle_kanban.config import KanbanConfig

LOGGER = "yurtle-kanban"


def _single(scan_line: str) -> str:
    return f'kanban:\n  theme: software\n  paths:\n    root: "work/"\n    scan_paths: {scan_line}\n'


def _kanban_level(scan_line: str) -> str:
    return f'kanban:\n  theme: software\n  paths:\n    root: "work/"\n  scan_paths: {scan_line}\n'


def _multi(alpha: str, beta: str) -> str:
    return (
        'version: "2.0"\nboards:\n'
        f'  - name: alpha\n    preset: software\n    path: "a/"\n    scan_paths: {alpha}\n'
        f'  - name: beta\n    preset: software\n    path: "b/"\n    scan_paths: {beta}\n'
        "default_board: alpha\n"
    )


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


def _scan_warnings(msgs: list[str]) -> list[str]:
    return [m for m in msgs if "scan_paths" in m]


# --- 1. a whitespace-only scalar is null ----------------------------------------------


@pytest.mark.parametrize("build", [_single, _kanban_level], ids=["paths", "kanban_level"])
@pytest.mark.parametrize("value", ['"  "', '"\\t"', '" \\n "'], ids=["spaces", "tab", "mixed"])
def test_whitespace_scalar_is_null(tmp_path, caplog, build, value):
    config, msgs = _load_logged(tmp_path, build(value), caplog)
    assert config.paths.scan_paths == [], config.paths.scan_paths
    assert _scan_warnings(msgs) == [], msgs


def test_multi_board_whitespace_scalar_is_null(tmp_path, caplog):
    config, msgs = _load_logged(tmp_path, _multi('"  "', '"b/"'), caplog)
    assert config.boards[0].scan_paths == []
    assert config.paths.scan_paths == ["b/"]  # the aggregate, too
    assert _scan_warnings(msgs) == [], msgs


def test_whitespace_scalar_not_in_work_paths(tmp_path, caplog):
    config, _ = _load_logged(tmp_path, _single('"  "'), caplog)
    assert Path("  ") not in config.get_work_paths()


# --- 2. the warning names its source and shows the entry ------------------------------


def test_paths_level_warning_names_kanban_paths_and_entry(tmp_path, caplog):
    config, msgs = _load_logged(tmp_path, _single('["   ", "a/"]'), caplog)
    assert config.paths.scan_paths == ["a/"]
    hits = _scan_warnings(msgs)
    assert len(hits) == 1, msgs
    assert "kanban.paths" in hits[0], hits[0]
    assert repr("   ") in hits[0], hits[0]


def test_kanban_level_warning_names_its_source_and_entry(tmp_path, caplog):
    config, msgs = _load_logged(tmp_path, _kanban_level('["   ", "a/"]'), caplog)
    assert config.paths.scan_paths == ["a/"]
    hits = _scan_warnings(msgs)
    assert len(hits) == 1, msgs
    # the kanban-level key, not `kanban.paths.scan_paths`: the user must find it
    assert "kanban" in hits[0] and "kanban.paths" not in hits[0], hits[0]
    assert repr("   ") in hits[0], hits[0]


def test_paths_and_kanban_level_warnings_differ(tmp_path, caplog):
    _, paths_msgs = _load_logged(tmp_path, _single('[""]'), caplog)
    _, level_msgs = _load_logged(tmp_path, _kanban_level('[""]'), caplog)
    assert len(_scan_warnings(paths_msgs)) == len(_scan_warnings(level_msgs)) == 1
    assert _scan_warnings(paths_msgs) != _scan_warnings(level_msgs), paths_msgs


def test_multi_board_warnings_name_each_board_and_entry(tmp_path, caplog):
    config, msgs = _load_logged(tmp_path, _multi('["   ", "a/"]', '["", "b/"]'), caplog)
    assert config.boards[0].scan_paths == ["a/"]
    assert config.boards[1].scan_paths == ["b/"]
    hits = _scan_warnings(msgs)
    assert len(hits) == 2, msgs
    alpha = [m for m in hits if "alpha" in m]
    beta = [m for m in hits if "beta" in m]
    assert len(alpha) == 1 and len(beta) == 1, hits
    assert "beta" not in alpha[0] and "alpha" not in beta[0], hits
    assert repr("   ") in alpha[0], alpha[0]
    assert repr("") in beta[0] and repr("   ") not in beta[0], beta[0]


def test_multi_board_same_entry_two_boards_distinguishable(tmp_path, caplog):
    _, msgs = _load_logged(tmp_path, _multi('["", "a/"]', '["", "b/"]'), caplog)
    hits = _scan_warnings(msgs)
    assert len(hits) == 2 and hits[0] != hits[1], hits


# --- controls -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        _single('["a/", "b/"]'),
        _kanban_level('["a/", "b/"]'),
        _multi('["a/"]', '["b/"]'),
        _single('"a/"'),
        _single(""),
        _single('""'),
    ],
    ids=["paths", "kanban_level", "multi", "string", "null", "empty_string"],
)
def test_control_no_warning(tmp_path, caplog, text):
    _, msgs = _load_logged(tmp_path, text, caplog)
    assert msgs == [], msgs
