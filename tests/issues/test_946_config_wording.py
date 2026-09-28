"""Issue #946: config refusal wording, found in the PR #945 (#900) review.

Decided spec ([steer] on #946, bucket 1):

1. A v1 ``ignore`` refusal names where ``ignore`` actually sits, as ``_scan_list``'s
   ``scan_where`` does for ``scan_paths``:

   - ``kanban: {paths: {ignore: 5}}``  → ``in kanban.paths``
   - ``kanban: {ignore: 5}`` (beside ``paths:``, accepted since #482) → ``in kanban``
   - top-level ``paths: {ignore: 5}`` (no ``kanban:`` key) → ``in paths``

2. A dropped bad WIP limit's warning on a multi-board board ``a`` reads
   ``on board 'a'`` (the #900 shape), never ``config.yaml board 'a'``.

3. Controls: the #900 and #864 wording tests stay green.

Today's wording (red unless noted):

- kanban.paths.ignore: `ignore` in kanban.paths must be a list of glob patterns,
                       got int 5                                  (already green)
- kanban.ignore:       `ignore` in kanban.paths must be ...       (red)
- top-level paths:     `ignore` in kanban.paths must be ...       (red)
- WIP warning:         config.yaml board 'a' wip_limits.in_progress: WIP limit -1
                       is not a whole number, 0 or more; ignored  (red)
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from yurtle_kanban import config as config_mod
from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.models import InputRefused

V2 = 'version: "2.0"\n'
LOGGER = "yurtle-kanban"


@pytest.fixture(autouse=True)
def _fresh_theme_cache() -> None:
    config_mod._theme_cache.clear()


def _refusal(text: str, tmp_path: Path) -> str:
    with pytest.raises(InputRefused) as exc:
        KanbanConfig.from_text(text, tmp_path)
    return str(exc.value)


def _warnings(text: str, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> list[str]:
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        KanbanConfig.from_text(text, tmp_path)
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]


# --- 1. v1 `ignore` refusals name their real location ---------------------------


def test_kanban_paths_ignore_names_kanban_paths(tmp_path: Path) -> None:
    """Control: `ignore` under `kanban.paths` already says so."""
    msg = _refusal("kanban:\n  paths:\n    ignore: 5\n", tmp_path)
    assert "`ignore` in kanban.paths must be a list of glob patterns" in msg, msg
    assert "got int 5" in msg, msg


def test_kanban_ignore_beside_paths_names_kanban(tmp_path: Path) -> None:
    """`ignore` beside `paths:` under `kanban:` (#482) is in kanban, not kanban.paths."""
    msg = _refusal("kanban:\n  paths:\n    root: work/\n  ignore: 5\n", tmp_path)
    assert "`ignore` in kanban must be a list of glob patterns" in msg, msg
    assert "kanban.paths" not in msg, msg


def test_kanban_ignore_without_paths_names_kanban(tmp_path: Path) -> None:
    msg = _refusal("kanban:\n  ignore: 5\n", tmp_path)
    assert "`ignore` in kanban must be a list of glob patterns" in msg, msg
    assert "kanban.paths" not in msg, msg


def test_scan_paths_beside_paths_wording_matches(tmp_path: Path) -> None:
    """Control: the `scan_where` shape the ignore refusal mirrors."""
    msg = _refusal("kanban:\n  scan_paths: 5\n", tmp_path)
    assert "`scan_paths` in kanban must be a list of paths" in msg, msg


def test_top_level_paths_ignore_names_paths(tmp_path: Path) -> None:
    """No `kanban:` key: `ignore` sits in the top-level `paths`, not kanban.paths."""
    msg = _refusal("paths:\n  ignore: 5\n", tmp_path)
    assert "`ignore` in paths must be a list of glob patterns" in msg, msg
    assert "kanban" not in msg, msg


def test_kanban_paths_ignore_wins_over_kanban_ignore_location(tmp_path: Path) -> None:
    """Both set: `kanban.paths.ignore` wins (#482), so a bad one is named there."""
    msg = _refusal("kanban:\n  paths:\n    ignore: 5\n  ignore: ['a/**']\n", tmp_path)
    assert "`ignore` in kanban.paths must be a list of glob patterns" in msg, msg


# --- 2. dropped WIP limit warnings say `on board 'x'` -----------------------------


def _board_wip(wip: str, name: str = "a") -> str:
    return V2 + f"boards:\n  - name: {name}\n    path: work/\n    wip_limits:\n{wip}"


def test_dropped_wip_limit_warning_says_on_board(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    msgs = _warnings(_board_wip("      in_progress: -1\n"), tmp_path, caplog)
    wip = [m for m in msgs if "WIP limit" in m]
    assert len(wip) == 1, msgs
    assert "on board 'a'" in wip[0], wip[0]
    assert "config.yaml board" not in wip[0], wip[0]
    assert "wip_limits.in_progress" in wip[0], wip[0]


def test_dropped_per_type_wip_limit_warning_says_on_board(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    msgs = _warnings(_board_wip("      review:\n        bug: x\n"), tmp_path, caplog)
    wip = [m for m in msgs if "WIP limit" in m]
    assert len(wip) == 1, msgs
    assert "on board 'a'" in wip[0], wip[0]
    assert "config.yaml board" not in wip[0], wip[0]
    assert "wip_limits.review.bug" in wip[0], wip[0]


def test_no_config_yaml_board_wording_anywhere(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Two boards, both with a bad limit: neither warning keeps the old shape."""
    text = V2 + (
        "boards:\n"
        "  - name: a\n    path: a/\n    wip_limits:\n      in_progress: 1.5\n"
        "  - name: b\n    path: b/\n    wip_limits:\n      in_progress: text\n"
    )
    msgs = [m for m in _warnings(text, tmp_path, caplog) if "WIP limit" in m]
    assert len(msgs) == 2, msgs
    assert not [m for m in msgs if "config.yaml board" in m], msgs
    assert any("on board 'a'" in m for m in msgs), msgs
    assert any("on board 'b'" in m for m in msgs), msgs
