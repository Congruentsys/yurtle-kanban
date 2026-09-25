"""Issue #402 — negative / zero ``wip_limit``; single- vs multi-board default columns.

Follow-ups from the reviews of PR #398 (#391) and PR #400 (#393). Observed today, via a
custom ``.kanban/themes/mytheme.yaml``:

- ``columns.<id>.wip_limit: -1`` (or ``-1.0``) is accepted; ``board`` then shows the
  column as ``(0/-1)`` in red and lists it under "WIP Limit Violations" even when empty.
- ``wip_limit: 0`` is falsy in ``board.py``, i.e. "no limit".
- A theme with no ``columns`` gets different defaults per code path: single-board
  (``_get_columns_from_theme``) limits ready 5, in_progress 3, review 2; multi-board
  (``_get_columns_from_preset``) limits only in_progress 3.

Decided behaviour ([steer] bucket-2 on the issue):

1. A negative ``wip_limit`` is dropped at load with ONE warning naming the file and
   ``columns.<id>.wip_limit``; ``board`` shows the column as with the limit absent.
2. ``wip_limit: 0`` is kept and means "no limit": no ``x/0`` and no WIP violation.
3. The multi-board default columns match the single-board ones (ready 5, in_progress 3,
   review 2); a board-level ``wip_limits`` override still wins on top.
4. Controls: positive limits and the built-in themes are unchanged.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.issues.test_378_theme_field_shapes import (
    ABSENT,
    BUILTIN_NAMES,
    CONFIGS,
    MULTI_CFG,
    SINGLE_CFG,
    THEME,
    _clean_theme_cache,  # noqa: F401  (autouse fixture)
    _dump,
    _field_theme,
    _hits,
    _invoke,
    _item,
    _load,
    _no_crash,
    _normalized,
    _repo,
    _same_as_absent,
    _seed,
    _warnings,
    warnings_log,  # noqa: F401  (fixture)
)
from tests.issues.test_391_empty_columns_whole_floats import BASE, _builtin_file
from yurtle_kanban import config as config_mod
from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.service import KanbanService

NEGATIVE = {"int": -1, "float": -1.0, "int_big": -5}
NEG_CASES = [pytest.param(v, id=k) for k, v in NEGATIVE.items()]
DOTTED = "columns.in_progress.wip_limit"

# the single-board defaults, which the multi-board path must now match
DEFAULT_COLUMNS = [
    ("backlog", "Backlog", 1, None),
    ("ready", "Ready", 2, 5),
    ("in_progress", "In Progress", 3, 3),
    ("review", "Review", 4, 2),
    ("done", "Done", 5, None),
]

MULTI_OVERRIDE_CFG = MULTI_CFG + "    wip_limits:\n      in_progress: 4\n"


def _service(repo: Path) -> KanbanService:
    config_mod._theme_cache.clear()
    return KanbanService(KanbanConfig.load(repo / ".kanban" / "config.yaml"), repo)


def _shape(columns: list[Any]) -> list[tuple[Any, ...]]:
    return [(c.id, c.name, c.order, c.wip_limit) for c in columns]


def _board_cols(repo: Path, monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, ...]]:
    """The columns ``get_board()`` builds for ``repo`` (either config shape)."""
    monkeypatch.chdir(repo)
    return _shape(_service(repo).get_board().columns)


def _flat(result, repo: Path) -> str:
    return _normalized(result, repo)


# ---------------------------------------------------------------------------
# 1. Negative wip_limit — dropped at load, board as absent
# ---------------------------------------------------------------------------


class TestNegativeWipLimit:
    @pytest.mark.parametrize("value", NEG_CASES)
    def test_negative_dropped_once_with_warning(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        value: Any,
        warnings_log: pytest.LogCaptureFixture,  # noqa: F811
    ) -> None:
        repo = _repo(tmp_path / "repo", None, _dump(_field_theme("columns", "wip_limit", value)))
        expected = _field_theme("columns", "wip_limit", ABSENT)
        assert _load(repo, monkeypatch) == expected
        assert config_mod._load_builtin_theme(THEME, repo) == expected  # cached
        assert len(_hits(warnings_log, DOTTED)) == 1, _warnings(warnings_log)
        assert len(_warnings(warnings_log)) == 1, _warnings(warnings_log)

    @pytest.mark.parametrize("cfg", CONFIGS)
    @pytest.mark.parametrize("value", NEG_CASES)
    def test_board_negative_as_absent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: Any, cfg: str
    ) -> None:
        bad = _field_theme("columns", "wip_limit", value)
        ref = _field_theme("columns", "wip_limit", ABSENT)
        _same_as_absent(tmp_path, monkeypatch, cfg, bad, ref, ["board"])

    @pytest.mark.parametrize("cfg", CONFIGS)
    @pytest.mark.parametrize("value", NEG_CASES)
    def test_board_negative_not_over_limit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: Any, cfg: str
    ) -> None:
        repo = _repo(tmp_path / "repo", cfg, _dump(_field_theme("columns", "wip_limit", value)))
        _seed(repo)
        result = _invoke(repo, monkeypatch, ["board"])
        _no_crash(result)
        assert result.exit_code == 0, result.output
        out = _flat(result, repo)
        assert "/-1" not in out and "/-5" not in out, result.output
        assert "Violations" not in out, result.output


# ---------------------------------------------------------------------------
# 2. wip_limit: 0 — kept, means "no limit"
# ---------------------------------------------------------------------------


class TestZeroWipLimit:
    def test_zero_kept_without_warning(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        warnings_log: pytest.LogCaptureFixture,  # noqa: F811
    ) -> None:
        theme = _field_theme("columns", "wip_limit", 0)
        repo = _repo(tmp_path / "repo", None, _dump(theme))
        assert _load(repo, monkeypatch) == theme
        assert not _warnings(warnings_log), _warnings(warnings_log)

    @pytest.mark.parametrize("cfg", CONFIGS)
    def test_board_zero_shows_no_limit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cfg: str
    ) -> None:
        repo = _repo(tmp_path / "repo", cfg, _dump(_field_theme("columns", "wip_limit", 0)))
        _seed(repo)  # two items in in_progress: over a limit of 0, were 0 a limit
        result = _invoke(repo, monkeypatch, ["board"])
        _no_crash(result)
        assert result.exit_code == 0, result.output
        out = _flat(result, repo)
        assert "/0)" not in out and "2/0" not in out, result.output
        assert "In Progress (2)" in out, result.output

    @pytest.mark.parametrize("cfg", CONFIGS)
    def test_board_zero_not_a_violation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cfg: str
    ) -> None:
        repo = _repo(tmp_path / "repo", cfg, _dump(_field_theme("columns", "wip_limit", 0)))
        _seed(repo)
        result = _invoke(repo, monkeypatch, ["board"])
        _no_crash(result)
        assert result.exit_code == 0, result.output
        assert "Violations" not in _flat(result, repo), result.output

    @pytest.mark.parametrize("cfg", CONFIGS)
    def test_board_zero_same_as_absent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cfg: str
    ) -> None:
        bad = _field_theme("columns", "wip_limit", 0)
        ref = _field_theme("columns", "wip_limit", ABSENT)
        _same_as_absent(tmp_path, monkeypatch, cfg, bad, ref, ["board"])


# ---------------------------------------------------------------------------
# 3. Default columns — multi-board matches single-board
# ---------------------------------------------------------------------------


class TestDefaultColumns:
    def test_single_board_defaults_pinned(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _repo(tmp_path / "repo", SINGLE_CFG, _dump(BASE))
        assert _board_cols(repo, monkeypatch) == DEFAULT_COLUMNS

    def test_preset_defaults_match_theme_defaults(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        single = _repo(tmp_path / "single", SINGLE_CFG, _dump(BASE))
        multi = _repo(tmp_path / "multi", MULTI_CFG, _dump(BASE))
        monkeypatch.chdir(single)
        from_theme = _shape(_service(single)._get_columns_from_theme())
        monkeypatch.chdir(multi)
        from_preset = _shape(_service(multi)._get_columns_from_preset(THEME))
        assert from_preset == from_theme == DEFAULT_COLUMNS

    def test_multi_board_defaults_match_single(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _repo(tmp_path / "repo", MULTI_CFG, _dump(BASE))
        assert _board_cols(repo, monkeypatch) == DEFAULT_COLUMNS

    @pytest.mark.parametrize("cfg", CONFIGS)
    def test_board_output_shows_default_limits(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cfg: str
    ) -> None:
        repo = _repo(tmp_path / "repo", cfg, _dump(BASE))
        result = _invoke(repo, monkeypatch, ["board"])
        _no_crash(result)
        assert result.exit_code == 0, result.output
        out = _flat(result, repo)
        # "In Progress (0/3)" wraps in the 80-column table; its marker alone is kept
        for marker in ("Ready (0/5)", "(0/3)", "Review (0/2)"):
            assert marker in out, result.output

    def test_board_override_still_wins(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _repo(tmp_path / "repo", MULTI_OVERRIDE_CFG, _dump(BASE))
        want = [(i, n, o, 4 if i == "in_progress" else w) for i, n, o, w in DEFAULT_COLUMNS]
        assert _board_cols(repo, monkeypatch) == want

    def test_board_override_still_wins_output(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _repo(tmp_path / "repo", MULTI_OVERRIDE_CFG, _dump(BASE))
        _item(repo, "TASK-001", "task", "in_progress")
        result = _invoke(repo, monkeypatch, ["board"])
        _no_crash(result)
        assert result.exit_code == 0, result.output
        out = _flat(result, repo)
        assert "(1/4)" in out, result.output
        assert "Ready (0/5)" in out and "Review (0/2)" in out, result.output

    def test_control_override_on_theme_columns(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # a theme that defines its columns: the override applies, the rest unchanged
        theme = copy.deepcopy(BASE)
        theme["columns"] = {
            "backlog": {"name": "Backlog", "order": 1},
            "in_progress": {"name": "In Progress", "order": 2, "wip_limit": 2},
            "review": {"name": "Review", "order": 3, "wip_limit": 1},
        }
        repo = _repo(tmp_path / "repo", MULTI_OVERRIDE_CFG, _dump(theme))
        assert _board_cols(repo, monkeypatch) == [
            ("backlog", "Backlog", 1, None),
            ("in_progress", "In Progress", 2, 4),
            ("review", "Review", 3, 1),
        ]


# ---------------------------------------------------------------------------
# 4. Controls — positive limits and built-in themes unchanged
# ---------------------------------------------------------------------------


class TestControls:
    @pytest.mark.parametrize("value", [1, 3, 10])
    def test_positive_kept_without_warning(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        value: int,
        warnings_log: pytest.LogCaptureFixture,  # noqa: F811
    ) -> None:
        theme = _field_theme("columns", "wip_limit", value)
        repo = _repo(tmp_path / "repo", None, _dump(theme))
        assert _load(repo, monkeypatch) == theme
        assert not _warnings(warnings_log), _warnings(warnings_log)

    @pytest.mark.parametrize("cfg", CONFIGS)
    def test_board_positive_limit_shown(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cfg: str
    ) -> None:
        repo = _repo(tmp_path / "repo", cfg, _dump(_field_theme("columns", "wip_limit", 3)))
        _seed(repo)
        result = _invoke(repo, monkeypatch, ["board"])
        _no_crash(result)
        assert result.exit_code == 0, result.output
        assert "In Progress (2/3)" in _flat(result, repo), result.output

    @pytest.mark.parametrize("cfg", CONFIGS)
    def test_board_positive_limit_exceeded_is_a_violation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cfg: str
    ) -> None:
        repo = _repo(tmp_path / "repo", cfg, _dump(_field_theme("columns", "wip_limit", 1)))
        _seed(repo)
        result = _invoke(repo, monkeypatch, ["board"])
        _no_crash(result)
        assert result.exit_code == 0, result.output
        out = _flat(result, repo)
        assert "In Progress (2/1)" in out, result.output
        assert "Violations" in out, result.output

    @pytest.mark.parametrize("name", BUILTIN_NAMES)
    def test_builtin_themes_load_unchanged(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        name: str,
        warnings_log: pytest.LogCaptureFixture,  # noqa: F811
    ) -> None:
        monkeypatch.chdir(tmp_path)
        raw = yaml.safe_load(_builtin_file(name).read_text())
        assert config_mod._load_builtin_theme(name, None) == raw
        assert not _warnings(warnings_log), _warnings(warnings_log)

    @pytest.mark.parametrize("name", BUILTIN_NAMES)
    def test_builtin_columns_same_on_both_paths(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
    ) -> None:
        single = _repo(
            tmp_path / "single", f"kanban:\n  theme: {name}\n  paths:\n    root: work/\n"
        )
        multi = _repo(
            tmp_path / "multi",
            f'version: "2.0"\nboards:\n  - name: devboard\n    preset: {name}\n    path: work/\n',
        )
        raw = yaml.safe_load(_builtin_file(name).read_text())
        want = sorted(
            (
                (i, d.get("name", i.title()), d.get("order", 0), d.get("wip_limit"))
                for i, d in raw["columns"].items()
            ),
            key=lambda c: c[2],
        )
        assert _board_cols(single, monkeypatch) == want
        assert _board_cols(multi, monkeypatch) == want
