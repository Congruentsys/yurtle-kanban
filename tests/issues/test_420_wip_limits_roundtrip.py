"""Issue #420 — saving config.yaml rewrites the CLEANED wip_limits; emptied per-type map.

Follow-ups from the review of PR #418 (#411). Observed today:

- ``BoardConfig.from_dict`` keeps only the cleaned ``wip_limits`` and ``to_dict``
  serializes them, so any config save (e.g. ``board-add``) rewrites config.yaml with the
  user's bad values erased: ``dev`` with ``wip_limits: {in_progress: "3", review:
  {chore: -1}, ready: 4}`` is saved as ``{review: {}, ready: 4}``.
- A per-type map whose entries are all dropped becomes ``{}``, so ``type_wip_limits``
  is ``{}`` and ``wip_limit`` is None: the theme's limit no longer applies.
- ``board-add --wip-limit in_progress:-1`` is accepted and written.
- whole-decimal ``4.0`` in wip-policy.md was pinned only for ``wip:ColumnLimit``.

Decided behaviour ([steer] on #420):

1. Saving keeps the raw values: ``to_dict`` returns exactly what the user wrote; the
   runtime (``get_board``) still uses the cleaned limits (#411).
2. A per-type map emptied by dropping means no override (the theme limit applies). An
   explicit ``{}`` written by the user keeps today's meaning (pinned below).
3. ``board-add --wip-limit col:N`` rejects N that is not a whole number >= 0 with a
   one-line error, exit 1, and no config change.
4. ``wip:TypeLimit`` with ``wip:limit 4.0`` in wip-policy.md is read as 4.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.issues.test_378_theme_field_shapes import (
    THEME,
    _clean_theme_cache,  # noqa: F401  (autouse fixture)
    _dump,
    _invoke,
    _no_crash,
    _repo,
    _warnings,
    warnings_log,  # noqa: F401  (fixture)
)
from tests.issues.test_411_per_type_zero_board_limits import _policy, _type_limit
from yurtle_kanban import config as config_mod
from yurtle_kanban.config import BoardConfig, KanbanConfig
from yurtle_kanban.models import Column
from yurtle_kanban.service import KanbanService

THEME_IN_PROGRESS = 3
THEME_REVIEW = 2
THEME_DICT: dict[str, Any] = {
    "theme": {"name": "acme"},
    "columns": {
        "backlog": {"name": "Backlog", "order": 1},
        "ready": {"name": "Ready", "order": 2},
        "in_progress": {"name": "In Progress", "order": 3, "wip_limit": THEME_IN_PROGRESS},
        "review": {"name": "Review", "order": 4, "wip_limit": THEME_REVIEW},
        "done": {"name": "Done", "order": 5},
    },
}

# the issue's repro: every bad value must survive a save verbatim
REPRO: dict[str, Any] = {"in_progress": "3", "review": {"chore": -1}, "ready": 4}

# one raw shape per bad (or non-canonical) value #411 cleans at runtime
RAW_SHAPES = {
    "str": {"in_progress": "3"},
    "neg": {"in_progress": -1},
    "float": {"in_progress": 1.5},
    "whole_float": {"in_progress": 3.0},
    "list": {"in_progress": [1]},
    "bool": {"in_progress": True},
    "type_neg": {"review": {"chore": -1}},
    "type_mixed": {"review": {"chore": "x", "task": 1, "_default": -2}},
    "repro": REPRO,
}
RAW_CASES = [pytest.param(v, id=k) for k, v in RAW_SHAPES.items()]

# valid configs: saving must leave them unchanged
VALID_SHAPES = {
    "ints": {"in_progress": 2, "review": 0},
    "per_type": {"review": {"task": 1, "chore": None, "_default": 3}},
    "null_column": {"in_progress": None, "ready": 4},
}
VALID_CASES = [pytest.param(v, id=k) for k, v in VALID_SHAPES.items()]


def _cfg(wip_limits: Any = ...) -> str:
    board: dict[str, Any] = {"name": "dev", "preset": THEME, "path": "work/"}
    if wip_limits is not ...:
        board["wip_limits"] = wip_limits
    return yaml.safe_dump({"version": "2.0", "boards": [board]}, sort_keys=False)


def _make(tmp_path: Path, wip_limits: Any = ...) -> Path:
    return _repo(tmp_path / "repo", _cfg(wip_limits), _dump(THEME_DICT))


def _config_path(repo: Path) -> Path:
    return repo / ".kanban" / "config.yaml"


def _saved_board(repo: Path, name: str = "dev") -> dict[str, Any]:
    data = yaml.safe_load(_config_path(repo).read_text())
    return next(b for b in data["boards"] if b["name"] == name)


def _service(repo: Path, monkeypatch: pytest.MonkeyPatch) -> KanbanService:
    monkeypatch.chdir(repo)
    config_mod._theme_cache.clear()
    return KanbanService(KanbanConfig.load(_config_path(repo)), repo)


def _column(repo: Path, monkeypatch: pytest.MonkeyPatch, col_id: str) -> Column:
    board = _service(repo, monkeypatch).get_board()
    return next(c for c in board.columns if c.id == col_id)


def _assert_raw(saved: Any, raw: Any) -> None:
    """``saved`` equals ``raw`` value for value AND type (``3.0`` is not ``3``)."""
    assert saved == raw, saved
    if isinstance(raw, dict):
        for key, value in raw.items():
            _assert_raw(saved[key], value)
    else:
        assert type(saved) is type(raw), (saved, raw)


def _board_add(repo: Path, monkeypatch: pytest.MonkeyPatch, *extra: str) -> Any:
    return _invoke(
        repo,
        monkeypatch,
        ["board-add", "other", "--preset", "software", "--path", "other/", *extra],
    )


# ---------------------------------------------------------------------------
# 1. Saving keeps the raw values
# ---------------------------------------------------------------------------


class TestSaveKeepsRaw:
    def test_board_add_keeps_repro_verbatim(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _make(tmp_path, copy.deepcopy(REPRO))
        result = _board_add(repo, monkeypatch)
        _no_crash(result)
        assert result.exit_code == 0, result.output
        saved = _saved_board(repo)["wip_limits"]
        assert saved == REPRO, saved
        # "3" is still the string "3", not dropped nor coerced
        assert saved["in_progress"] == "3" and type(saved["in_progress"]) is str
        assert saved["review"] == {"chore": -1}

    @pytest.mark.parametrize("raw", RAW_CASES)
    def test_board_add_keeps_raw_shapes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raw: dict[str, Any]
    ) -> None:
        repo = _make(tmp_path, copy.deepcopy(raw))
        result = _board_add(repo, monkeypatch)
        _no_crash(result)
        assert result.exit_code == 0, result.output
        _assert_raw(_saved_board(repo)["wip_limits"], raw)

    @pytest.mark.parametrize("raw", RAW_CASES)
    def test_load_save_roundtrip_keeps_raw(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raw: dict[str, Any]
    ) -> None:
        repo = _make(tmp_path, copy.deepcopy(raw))
        monkeypatch.chdir(repo)
        KanbanConfig.load(_config_path(repo)).save(_config_path(repo))
        _assert_raw(_saved_board(repo)["wip_limits"], raw)

    @pytest.mark.parametrize("raw", RAW_CASES)
    def test_to_dict_returns_raw(self, raw: dict[str, Any]) -> None:
        data = {"name": "dev", "preset": "software", "path": "work/"}
        data["wip_limits"] = copy.deepcopy(raw)
        _assert_raw(BoardConfig.from_dict(data).to_dict()["wip_limits"], raw)

    def test_runtime_still_uses_cleaned_after_save(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _make(tmp_path, copy.deepcopy(REPRO))
        _no_crash(_board_add(repo, monkeypatch))
        # bad values are ignored at runtime, as #411 decided
        ip = _column(repo, monkeypatch, "in_progress")
        assert ip.wip_limit == THEME_IN_PROGRESS and ip.type_wip_limits is None
        assert _column(repo, monkeypatch, "ready").wip_limit == 4
        review = _column(repo, monkeypatch, "review")
        assert review.wip_limit == THEME_REVIEW and not review.type_wip_limits

    # controls ---------------------------------------------------------------

    @pytest.mark.parametrize("raw", VALID_CASES)
    def test_control_valid_config_roundtrips(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raw: dict[str, Any]
    ) -> None:
        repo = _make(tmp_path, copy.deepcopy(raw))
        result = _board_add(repo, monkeypatch)
        _no_crash(result)
        assert result.exit_code == 0, result.output
        _assert_raw(_saved_board(repo)["wip_limits"], raw)

    def test_control_null_board_roundtrips(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _make(tmp_path, None)
        _no_crash(_board_add(repo, monkeypatch))
        saved = _saved_board(repo)
        assert "wip_limits" in saved and saved["wip_limits"] is None

    def test_control_no_wip_limits_stays_absent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _make(tmp_path)
        _no_crash(_board_add(repo, monkeypatch))
        assert "wip_limits" not in _saved_board(repo)


# ---------------------------------------------------------------------------
# 2. A per-type map emptied by dropping means no override
# ---------------------------------------------------------------------------


class TestEmptiedPerTypeMap:
    @pytest.mark.parametrize(
        "per_type",
        [
            pytest.param({"chore": -1}, id="neg"),
            pytest.param({"chore": "3"}, id="str"),
            pytest.param({"chore": 1.5, "_default": -1}, id="all_bad"),
        ],
    )
    def test_emptied_map_keeps_theme_limit(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        per_type: dict[str, Any],
    ) -> None:
        repo = _make(tmp_path, {"review": per_type})
        col = _column(repo, monkeypatch, "review")
        assert col.wip_limit == THEME_REVIEW and type(col.wip_limit) is int
        assert col.type_wip_limits is None

    def test_emptied_map_other_column_override_still_applies(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _make(tmp_path, {"review": {"chore": -1}, "in_progress": 5})
        assert _column(repo, monkeypatch, "review").wip_limit == THEME_REVIEW
        assert _column(repo, monkeypatch, "in_progress").wip_limit == 5

    # pins / controls ------------------------------------------------------

    def test_pin_explicit_empty_map_keeps_todays_meaning(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # today an explicit `review: {}` clears the column limit: per-type mode with
        # no types listed, i.e. no limit on the column
        repo = _make(tmp_path, {"review": {}})
        col = _column(repo, monkeypatch, "review")
        assert col.wip_limit is None
        assert col.type_wip_limits == {}

    def test_control_partly_dropped_map_keeps_valid_entries(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _make(tmp_path, {"review": {"chore": -1, "task": 1}})
        col = _column(repo, monkeypatch, "review")
        assert col.type_wip_limits == {"task": 1}
        assert col.wip_limit is None


# ---------------------------------------------------------------------------
# 3. board-add --wip-limit rejects bad limits
# ---------------------------------------------------------------------------


class TestBoardAddWipLimit:
    @pytest.mark.parametrize("value", ["-1", "-5", "abc", "1.5"])
    def test_bad_limit_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        repo = _make(tmp_path, {"in_progress": 2})
        before = _config_path(repo).read_text()
        result = _invoke(
            repo,
            monkeypatch,
            ["board-add", "x", "--preset", "software", "--path", "x/",
             "--wip-limit", f"in_progress:{value}"],
        )
        _no_crash(result)
        assert result.exit_code == 1, result.output
        lines = [ln for ln in result.output.splitlines() if ln.strip()]
        assert len(lines) == 1, result.output
        assert value in lines[0], result.output
        assert _config_path(repo).read_text() == before

    # controls ---------------------------------------------------------------

    @pytest.mark.parametrize("value", [0, 3])
    def test_control_valid_limit_accepted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: int
    ) -> None:
        repo = _make(tmp_path, {"in_progress": 2})
        result = _invoke(
            repo,
            monkeypatch,
            ["board-add", "x", "--preset", "software", "--path", "x/",
             "--wip-limit", f"in_progress:{value}"],
        )
        _no_crash(result)
        assert result.exit_code == 0, result.output
        assert _saved_board(repo, "x")["wip_limits"] == {"in_progress": value}


# ---------------------------------------------------------------------------
# 4. wip-policy.md: whole decimal for wip:TypeLimit
# ---------------------------------------------------------------------------


class TestPolicyTypeLimitDecimal:
    def test_whole_decimal_type_limit_is_int(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        warnings_log: pytest.LogCaptureFixture,  # noqa: F811
    ) -> None:
        repo = _repo(
            tmp_path / "repo",
            yaml.safe_dump(
                {"version": "2.0",
                 "boards": [{"name": "devboard", "preset": THEME, "path": "work/"}]},
                sort_keys=False,
            ),
            _dump(THEME_DICT),
        )
        _policy(repo, _type_limit("task", "4.0"))
        col = _column(repo, monkeypatch, "in_progress")
        assert col.type_wip_limits == {"task": 4}
        assert type(col.type_wip_limits["task"]) is int
        assert not _warnings(warnings_log), _warnings(warnings_log)
