"""Issue #1000: config messages name `kanban.` only when the file has a `kanban:` key.

A v1 config may put `paths:` (and `ignore:` / `scan_paths:` beside it) at the top
level. #946 made the list refusals say where they sit ("at the top level",
"in paths"); the "must be a mapping" refusal and the "is ignored because" warning
still said `kanban.` there.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.models import InputRefused


def _load(text: str, tmp_path: Path) -> KanbanConfig:
    return KanbanConfig.from_text(text, tmp_path)


def test_top_level_ignore_refusal_says_at_the_top_level(tmp_path: Path) -> None:
    """#946's wording for a list beside a top-level `paths:`, pinned (#998 review)."""
    with pytest.raises(InputRefused) as exc:
        _load("paths:\n  root: work/\nignore: 5\n", tmp_path)
    assert "at the top level" in str(exc.value), str(exc.value)
    assert "kanban" not in str(exc.value), str(exc.value)


def test_top_level_paths_not_a_mapping_names_paths(tmp_path: Path) -> None:
    with pytest.raises(InputRefused) as exc:
        _load("paths: 5\n", tmp_path)
    assert str(exc.value).startswith("paths must be a mapping"), str(exc.value)


def test_kanban_paths_not_a_mapping_still_names_kanban(tmp_path: Path) -> None:
    with pytest.raises(InputRefused) as exc:
        _load("kanban:\n  paths: 5\n", tmp_path)
    assert str(exc.value).startswith("kanban.paths must be a mapping"), str(exc.value)


def test_top_level_shadowed_key_warning_names_no_kanban(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        _load("paths:\n  ignore: ['a/**']\nignore: ['b/**']\n", tmp_path)
    said = [r.getMessage() for r in caplog.records if "is ignored because" in r.getMessage()]
    assert said == ["config: `ignore` is ignored because `paths.ignore` is set"], said


def test_kanban_shadowed_key_warning_still_names_kanban(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        _load("kanban:\n  paths:\n    ignore: ['a/**']\n  ignore: ['b/**']\n", tmp_path)
    said = [r.getMessage() for r in caplog.records if "is ignored because" in r.getMessage()]
    assert said == [
        "config: `kanban.ignore` is ignored because `kanban.paths.ignore` is set"
    ], said
