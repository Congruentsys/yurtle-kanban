"""Issue #391 — an empty (or emptied) ``columns`` section; whole-number float fields.

Follow-ups from the review of PR #389 (#378). Observed today, via a custom
``.kanban/themes/mytheme.yaml``:

- ``columns: {}`` written out, or a ``columns`` section emptied by #378's non-str key
  drop or #363's non-mapping entry drop, keeps the ``columns`` key, so
  ``_get_columns_from_theme`` builds ZERO columns instead of the defaults and seeded
  items vanish from ``board``; #365's fall-through doesn't fire because the key exists.
- ``columns.<id>.wip_limit: 3.0`` / ``order: 2.0`` are dropped with #378's warning.

Decided behaviour ([steer] bucket-2 on the issue):

1. An empty ``columns`` section counts as absent: ``board`` prints what it prints for the
   same theme with no ``columns`` key (the default columns, the item visible). One extra
   warning when it was emptied by drops; an explicit ``{}`` may warn at most once.
2. A whole-number float ``wip_limit`` / ``order`` is coerced to the int, silently. A
   non-whole float (``1.5``) is still dropped with the #378 warning; ``True`` too.
3. Controls: the built-ins load unchanged; non-empty columns are unchanged; the #365
   fall-through still works, and a theme whose only content was ``columns: {}`` falls
   through to the built-in.
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
    SINGLE_CFG,
    THEME,
    THEME_FILE,
    _clean_theme_cache,  # noqa: F401  (autouse fixture)
    _dump,
    _field_theme,
    _hits,
    _invoke,
    _load,
    _no_crash,
    _normalized,
    _repo,
    _same_as_absent,
    _seed,
    _warnings,
    warnings_log,  # noqa: F401  (fixture)
)
from yurtle_kanban import config as config_mod
from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.service import KanbanService

# a theme that still has content without its columns (so it never falls through)
BASE: dict[str, Any] = {
    "theme": {"name": "acme"},
    "item_types": {
        "task": {"id_prefix": "TASK", "path": "work/tasks/"},
        "bug": {"id_prefix": "BUG", "path": "work/bugs/"},
    },
}

# ways a `columns` section is empty, or ends up empty once #363/#378 drop its content
EMPTY_COLUMNS: dict[str, Any] = {
    "explicit": {},
    "non_str_key": {5: {"name": "Five", "order": 5}},
    "non_mapping_entry": {"todo": "not a mapping"},
}
EMPTIED_BY_DROPS = ("non_str_key", "non_mapping_entry")


def _theme(columns: Any = ABSENT) -> dict[Any, Any]:
    theme = copy.deepcopy(BASE)
    if columns is not ABSENT:
        theme["columns"] = copy.deepcopy(columns)
    return theme


def _cols(repo: Path) -> list[tuple[Any, ...]]:
    """The columns the single-board service builds for ``repo``."""
    config_mod._theme_cache.clear()
    service = KanbanService(KanbanConfig.load(repo / ".kanban" / "config.yaml"), repo)
    return [(c.id, c.name, c.order, c.wip_limit) for c in service._get_columns_from_theme()]


def _columns_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    """Warnings naming the theme file and ``columns`` in any spelling."""
    return [m for m in _warnings(caplog) if THEME_FILE in m and "columns" in m]


# ---------------------------------------------------------------------------
# 1. An empty columns section counts as absent
# ---------------------------------------------------------------------------


class TestEmptyColumnsAtLoad:
    @pytest.mark.parametrize("shape", list(EMPTY_COLUMNS))
    def test_empty_columns_loads_as_absent(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        shape: str,
        warnings_log: pytest.LogCaptureFixture,  # noqa: F811
    ) -> None:
        repo = _repo(tmp_path / "repo", SINGLE_CFG, _dump(_theme(EMPTY_COLUMNS[shape])))
        ref = _repo(tmp_path / "ref0", SINGLE_CFG, _dump(_theme()))
        loaded = _load(repo, monkeypatch)
        assert loaded is not None
        rest = {k: v for k, v in loaded.items() if k != "columns"}
        assert rest == _theme(), loaded
        if "columns" in loaded:  # loose: kept, but then it must behave as if absent
            assert _cols(repo) == _cols(ref), loaded
            assert _cols(repo), "the default columns, not zero columns"

    @pytest.mark.parametrize("shape", EMPTIED_BY_DROPS)
    def test_emptied_by_drops_one_extra_warning(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        shape: str,
        warnings_log: pytest.LogCaptureFixture,  # noqa: F811
    ) -> None:
        repo = _repo(tmp_path / "repo", None, _dump(_theme(EMPTY_COLUMNS[shape])))
        _load(repo, monkeypatch)
        config_mod._load_builtin_theme(THEME, repo)  # cached: said once
        # the #363/#378 drop warning, plus one saying `columns` is now empty
        assert len(_columns_warnings(warnings_log)) == 2, _warnings(warnings_log)
        assert len(_warnings(warnings_log)) == 2, _warnings(warnings_log)

    def test_explicit_empty_columns_warns_at_most_once(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        warnings_log: pytest.LogCaptureFixture,  # noqa: F811
    ) -> None:
        repo = _repo(tmp_path / "repo", None, _dump(_theme({})))
        _load(repo, monkeypatch)
        config_mod._load_builtin_theme(THEME, repo)
        assert len(_warnings(warnings_log)) <= 1, _warnings(warnings_log)


class TestEmptyColumnsBoard:
    @pytest.mark.parametrize("cfg", CONFIGS)
    @pytest.mark.parametrize("shape", list(EMPTY_COLUMNS))
    def test_board_empty_columns_as_absent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shape: str, cfg: str
    ) -> None:
        bad = _theme(EMPTY_COLUMNS[shape])
        _same_as_absent(tmp_path, monkeypatch, cfg, bad, _theme(), ["board"])

    @pytest.mark.parametrize("cfg", CONFIGS)
    @pytest.mark.parametrize("shape", list(EMPTY_COLUMNS))
    def test_board_empty_columns_shows_seeded_item(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shape: str, cfg: str
    ) -> None:
        repo = _repo(tmp_path / "repo", cfg, _dump(_theme(EMPTY_COLUMNS[shape])))
        _seed(repo)
        result = _invoke(repo, monkeypatch, ["board"])
        _no_crash(result)
        assert result.exit_code == 0, result.output
        for name in ("Backlog", "In Progress"):  # the default columns
            assert name in result.output, result.output
        assert "TASK-001" in result.output, result.output


# ---------------------------------------------------------------------------
# 2. Whole-number floats are coerced; other bad values still dropped
# ---------------------------------------------------------------------------


class TestWholeFloats:
    @pytest.mark.parametrize(("fld", "value", "want"), [("wip_limit", 3.0, 3), ("order", 2.0, 2)])
    def test_whole_float_coerced_silently(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        fld: str,
        value: float,
        want: int,
        warnings_log: pytest.LogCaptureFixture,  # noqa: F811
    ) -> None:
        repo = _repo(tmp_path / "repo", None, _dump(_field_theme("columns", fld, value)))
        loaded = _load(repo, monkeypatch)
        assert loaded == _field_theme("columns", fld, want), loaded
        got = loaded["columns"]["in_progress"][fld]
        assert type(got) is int and got == want, repr(got)
        assert not _warnings(warnings_log), _warnings(warnings_log)

    @pytest.mark.parametrize("cfg", CONFIGS)
    def test_board_whole_float_wip_limit_as_int(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cfg: str
    ) -> None:
        got_theme = _field_theme("columns", "wip_limit", 3.0)
        want_theme = _field_theme("columns", "wip_limit", 3)
        _same_as_absent(tmp_path, monkeypatch, cfg, got_theme, want_theme, ["board"])
        repo = tmp_path / "bad"
        result = _invoke(repo, monkeypatch, ["board"])
        assert "2/3" in _normalized(result, repo), result.output

    @pytest.mark.parametrize("cfg", CONFIGS)
    def test_board_whole_float_order_as_int(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cfg: str
    ) -> None:
        got_theme = _field_theme("columns", "order", 2.0)
        want_theme = _field_theme("columns", "order", 2)
        _same_as_absent(tmp_path, monkeypatch, cfg, got_theme, want_theme, ["board"])

    @pytest.mark.parametrize("fld", ["wip_limit", "order"])
    @pytest.mark.parametrize("value", [1.5, True], ids=["non_whole_float", "bool"])
    def test_control_non_whole_float_and_bool_still_dropped(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        fld: str,
        value: Any,
        warnings_log: pytest.LogCaptureFixture,  # noqa: F811
    ) -> None:
        repo = _repo(tmp_path / "repo", None, _dump(_field_theme("columns", fld, value)))
        assert _load(repo, monkeypatch) == _field_theme("columns", fld, ABSENT)
        dotted = f"columns.in_progress.{fld}"
        assert len(_hits(warnings_log, dotted)) == 1, _warnings(warnings_log)
        assert len(_warnings(warnings_log)) == 1, _warnings(warnings_log)


# ---------------------------------------------------------------------------
# 3. Controls
# ---------------------------------------------------------------------------


def _builtin_file(name: str) -> Path:
    for d in config_mod._theme_dirs(None):
        if (d / f"{name}.yaml").exists():
            return d / f"{name}.yaml"
    raise AssertionError(f"no built-in {name}.yaml")


class TestControls:
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

    def test_non_empty_columns_unchanged(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        warnings_log: pytest.LogCaptureFixture,  # noqa: F811
    ) -> None:
        columns = {
            "backlog": {"name": "Backlog", "order": 1},
            "in_progress": {"name": "In Progress", "order": 2, "wip_limit": 3},
        }
        theme = _theme(columns)
        repo = _repo(tmp_path / "repo", None, _dump(theme))
        assert _load(repo, monkeypatch) == theme
        assert not _warnings(warnings_log), _warnings(warnings_log)

    @pytest.mark.parametrize("cfg", CONFIGS)
    def test_control_board_no_columns_key_shows_defaults(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cfg: str
    ) -> None:
        repo = _repo(tmp_path / "repo", cfg, _dump(_theme()))
        _seed(repo)
        result = _invoke(repo, monkeypatch, ["board"])
        _no_crash(result)
        assert result.exit_code == 0, result.output
        assert "TASK-001" in result.output, result.output

    def test_theme_left_empty_falls_through_to_builtin(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # #365, unchanged
        builtin = config_mod._load_builtin_theme("software", None)
        config_mod._theme_cache.clear()
        repo = tmp_path / "repo"
        themes = repo / ".kanban" / "themes"
        themes.mkdir(parents=True)
        (themes / "software.yaml").write_text(_dump({"columns": 5, "item_types": [1]}))
        monkeypatch.chdir(repo)
        assert config_mod._load_builtin_theme("software", repo) == builtin

    @pytest.mark.parametrize("shape", list(EMPTY_COLUMNS))
    def test_theme_only_empty_columns_falls_through_to_builtin(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shape: str
    ) -> None:
        builtin = config_mod._load_builtin_theme("software", None)
        assert builtin is not None
        config_mod._theme_cache.clear()
        repo = tmp_path / "repo"
        themes = repo / ".kanban" / "themes"
        themes.mkdir(parents=True)
        (themes / "software.yaml").write_text(_dump({"columns": EMPTY_COLUMNS[shape]}))
        monkeypatch.chdir(repo)
        assert config_mod._load_builtin_theme("software", repo) == builtin
