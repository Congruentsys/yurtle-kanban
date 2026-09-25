"""Issue #428 — the #420 raw-wip sentinel: lost on copy; code changes to limits dropped.

Follow-ups from the review of PR #424 (#420). Observed today:

- ``_NO_RAW`` is a bare ``object()``: ``copy.deepcopy`` / pickle make a NEW object, so a
  copied code-built ``BoardConfig`` serializes that object as its ``wip_limits`` and
  ``yaml.safe_dump`` fails (``save`` writes a ``!!python/object`` tag).
- ``to_dict`` always writes ``raw_wip_limits`` when a board was loaded, so a limit
  changed in code after loading (assignment or ``dataclasses.replace``) is dropped on
  save.

Decided behaviour ([steer] on #428):

1. The sentinel survives ``copy.deepcopy`` and pickle: a copied code-built board saves
   its ``wip_limits``, and a deepcopied ``KanbanConfig`` saves a valid config.yaml.
2. ``to_dict`` writes the raw limits only while cleaning them (quietly) still equals the
   current ``wip_limits``; after a change in code the current value is written (G1).
   Unchanged, the raw value is still saved verbatim (#420).
3. Saving emits none of the #411 bad-limit warnings: only the load warns.
"""

from __future__ import annotations

import copy
import dataclasses
import pickle
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.issues.test_378_theme_field_shapes import (
    _clean_theme_cache,  # noqa: F401  (autouse fixture)
    _no_crash,
    _warnings,
    warnings_log,  # noqa: F401  (fixture)
)
from tests.issues.test_420_wip_limits_roundtrip import (
    _assert_raw,
    _board_add,
    _config_path,
    _make,
    _saved_board,
)
from yurtle_kanban.config import BoardConfig, KanbanConfig

# a loaded board: "3" is dropped at runtime (#411) but kept raw for saving (#420)
RAW: dict[str, Any] = {"in_progress": "3", "ready": 4}
CHANGED: dict[str, Any] = {"in_progress": 7}


def _copy_deep(obj: Any) -> Any:
    return copy.deepcopy(obj)


def _copy_pickle(obj: Any) -> Any:
    return pickle.loads(pickle.dumps(obj))


COPIES = [
    pytest.param(_copy_deep, id="deepcopy"),
    pytest.param(_copy_pickle, id="pickle"),
]


def _code_board() -> BoardConfig:
    return BoardConfig(name="x", wip_limits={"in_progress": 3})


def _load(repo: Path, monkeypatch: pytest.MonkeyPatch) -> KanbanConfig:
    monkeypatch.chdir(repo)
    return KanbanConfig.load(_config_path(repo))


def _save(config: KanbanConfig, repo: Path) -> None:
    config.save(_config_path(repo))


def _assign(config: KanbanConfig) -> None:
    config.boards[0].wip_limits = copy.deepcopy(CHANGED)


def _replace(config: KanbanConfig) -> None:
    config.boards[0] = dataclasses.replace(
        config.boards[0], wip_limits=copy.deepcopy(CHANGED)
    )


def _mutate(config: KanbanConfig) -> None:
    limits = config.boards[0].wip_limits
    assert limits is not None
    limits.clear()
    limits.update(copy.deepcopy(CHANGED))


CHANGES = [
    pytest.param(_assign, id="assign"),
    pytest.param(_replace, id="replace"),
    pytest.param(_mutate, id="in_place"),
]


# ---------------------------------------------------------------------------
# 1. Copies of a code-built BoardConfig
# ---------------------------------------------------------------------------


class TestSentinelSurvivesCopy:
    @pytest.mark.parametrize("copier", COPIES)
    def test_copied_code_board_to_dict_has_limits(self, copier: Any) -> None:
        board = copier(_code_board())
        assert board.to_dict()["wip_limits"] == {"in_progress": 3}

    @pytest.mark.parametrize("copier", COPIES)
    def test_copied_code_board_safe_dumps(self, copier: Any) -> None:
        board = copier(_code_board())
        text = yaml.safe_dump(board.to_dict())
        assert yaml.safe_load(text)["wip_limits"] == {"in_progress": 3}

    @pytest.mark.parametrize("copier", COPIES)
    def test_copied_twice_still_has_limits(self, copier: Any) -> None:
        board = copier(copier(_code_board()))
        assert board.to_dict()["wip_limits"] == {"in_progress": 3}

    @pytest.mark.parametrize("copier", COPIES)
    def test_copied_code_kanban_config_saves_valid_yaml(
        self, tmp_path: Path, copier: Any
    ) -> None:
        config = KanbanConfig(
            version="2.0",
            boards=[_code_board(), BoardConfig(name="y", wip_limits={"review": 1})],
        )
        path = tmp_path / ".kanban" / "config.yaml"
        copier(config).save(path)
        text = path.read_text()
        assert "!!python" not in text, text
        data = yaml.safe_load(text)
        limits = {b["name"]: b.get("wip_limits") for b in data["boards"]}
        assert limits == {"x": {"in_progress": 3}, "y": {"review": 1}}, limits
        loaded = KanbanConfig.load(path)
        assert [b.wip_limits for b in loaded.boards] == [
            {"in_progress": 3},
            {"review": 1},
        ]

    @pytest.mark.parametrize("copier", COPIES)
    def test_copied_loaded_config_saves_valid_yaml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, copier: Any
    ) -> None:
        repo = _make(tmp_path, copy.deepcopy(RAW))
        config = copier(_load(repo, monkeypatch))
        config.boards.append(BoardConfig(name="x", wip_limits={"in_progress": 3}))
        config = copier(config)
        _save(config, repo)
        assert "!!python" not in _config_path(repo).read_text()
        _assert_raw(_saved_board(repo)["wip_limits"], RAW)
        assert _saved_board(repo, "x")["wip_limits"] == {"in_progress": 3}

    # controls ---------------------------------------------------------------

    def test_control_uncopied_code_board(self) -> None:
        assert _code_board().to_dict()["wip_limits"] == {"in_progress": 3}

    @pytest.mark.parametrize("copier", COPIES)
    def test_control_copied_loaded_board_keeps_raw(self, copier: Any) -> None:
        board = BoardConfig.from_dict(
            {"name": "dev", "path": "work/", "wip_limits": copy.deepcopy(RAW)}
        )
        _assert_raw(copier(board).to_dict()["wip_limits"], RAW)


# ---------------------------------------------------------------------------
# 2. Code changes to a loaded board's limits are saved
# ---------------------------------------------------------------------------


class TestCodeChangeSaved:
    @pytest.mark.parametrize("change", CHANGES)
    def test_change_saved_by_kanban_config_save(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: Any
    ) -> None:
        repo = _make(tmp_path, copy.deepcopy(RAW))
        config = _load(repo, monkeypatch)
        change(config)
        _save(config, repo)
        _assert_raw(_saved_board(repo)["wip_limits"], CHANGED)

    @pytest.mark.parametrize("change", CHANGES)
    def test_change_in_to_dict(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: Any
    ) -> None:
        repo = _make(tmp_path, copy.deepcopy(RAW))
        config = _load(repo, monkeypatch)
        change(config)
        _assert_raw(config.boards[0].to_dict()["wip_limits"], CHANGED)

    @pytest.mark.parametrize("change", CHANGES)
    def test_change_saved_by_board_add(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: Any
    ) -> None:
        repo = _make(tmp_path, copy.deepcopy(RAW))
        real_load = KanbanConfig.load.__func__  # type: ignore[attr-defined]

        def load(cls: type[KanbanConfig], path: Path) -> KanbanConfig:
            config = real_load(cls, path)
            change(config)
            return config

        monkeypatch.setattr(KanbanConfig, "load", classmethod(load))
        result = _board_add(repo, monkeypatch)
        _no_crash(result)
        assert result.exit_code == 0, result.output
        _assert_raw(_saved_board(repo)["wip_limits"], CHANGED)

    def test_change_to_none_saved(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _make(tmp_path, copy.deepcopy(RAW))
        config = _load(repo, monkeypatch)
        config.boards[0].wip_limits = None
        _save(config, repo)
        saved = _saved_board(repo)
        assert "wip_limits" in saved and saved["wip_limits"] is None, saved

    def test_change_to_empty_saved(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # {} means "no overrides" and is omitted, as for a code-built board
        repo = _make(tmp_path, copy.deepcopy(RAW))
        config = _load(repo, monkeypatch)
        config.boards[0].wip_limits = {}
        _save(config, repo)
        assert "wip_limits" not in _saved_board(repo)

    def test_change_then_reload_runs_new_limit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _make(tmp_path, copy.deepcopy(RAW))
        config = _load(repo, monkeypatch)
        _assign(config)
        _save(config, repo)
        assert _load(repo, monkeypatch).boards[0].wip_limits == CHANGED

    # controls ---------------------------------------------------------------

    def test_control_unchanged_saves_raw_verbatim(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _make(tmp_path, copy.deepcopy(RAW))
        _save(_load(repo, monkeypatch), repo)
        saved = _saved_board(repo)["wip_limits"]
        _assert_raw(saved, RAW)
        assert saved["in_progress"] == "3" and type(saved["in_progress"]) is str

    def test_control_unchanged_board_add_saves_raw_verbatim(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _make(tmp_path, copy.deepcopy(RAW))
        result = _board_add(repo, monkeypatch)
        _no_crash(result)
        assert result.exit_code == 0, result.output
        _assert_raw(_saved_board(repo)["wip_limits"], RAW)

    def test_control_replace_other_field_keeps_raw(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _make(tmp_path, copy.deepcopy(RAW))
        config = _load(repo, monkeypatch)
        config.boards[0] = dataclasses.replace(config.boards[0], path="elsewhere/")
        _save(config, repo)
        saved = _saved_board(repo)
        assert saved["path"] == "elsewhere/"
        _assert_raw(saved["wip_limits"], RAW)

    def test_control_reassign_equal_value_keeps_raw(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # assigning what the board already runs is not a change: raw stays
        repo = _make(tmp_path, copy.deepcopy(RAW))
        config = _load(repo, monkeypatch)
        config.boards[0].wip_limits = copy.deepcopy(config.boards[0].wip_limits)
        _save(config, repo)
        _assert_raw(_saved_board(repo)["wip_limits"], RAW)


# ---------------------------------------------------------------------------
# 3. No warnings on save
# ---------------------------------------------------------------------------


class TestSaveIsQuiet:
    @pytest.mark.parametrize(
        "change",
        [pytest.param(None, id="unchanged"), *CHANGES],
    )
    def test_save_emits_no_warnings(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        warnings_log: pytest.LogCaptureFixture,  # noqa: F811
        change: Any,
    ) -> None:
        repo = _make(tmp_path, {"in_progress": "3", "review": {"chore": -1}, "ready": 4})
        config = _load(repo, monkeypatch)
        loaded = _warnings(warnings_log)
        assert len(loaded) == 2, loaded  # the load warns once per bad limit (#411)
        warnings_log.clear()
        if change is not None:
            change(config)
        config.boards[0].to_dict()
        _save(config, repo)
        assert _warnings(warnings_log) == [], _warnings(warnings_log)

    def test_load_then_save_warns_only_for_load(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        warnings_log: pytest.LogCaptureFixture,  # noqa: F811
    ) -> None:
        raw = {"in_progress": "3", "review": {"chore": -1}, "ready": 4}
        repo = _make(tmp_path, copy.deepcopy(raw))
        _load(repo, monkeypatch)
        load_only = len(_warnings(warnings_log))
        warnings_log.clear()
        _save(_load(repo, monkeypatch), repo)
        assert len(_warnings(warnings_log)) == load_only, _warnings(warnings_log)
