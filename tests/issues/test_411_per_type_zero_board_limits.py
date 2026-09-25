"""Issue #411 — per-type WIP 0 in ``move``; board-config ``wip_limits`` unvalidated.

Follow-ups from the review of PR #409 (#402). Observed today:

- ``wip_limits: {in_progress: {chore: 0}}``: ``board``/``is_over_wip`` treat 0 as "no
  limit" (#402), but ``move``'s per-type check (``service.py``, ``limit is not None and
  type_count >= limit``) blocks every chore.
- A board's own WIP limits (config.yaml ``boards[].wip_limits`` and
  ``.yurtle-kanban/wip-policy.md``) skip the theme-level validation of #378/#391/#402:
  a negative override marks the column as always over (``board`` shows ``2/-1`` and a
  violation, ``move`` blocks everything), a float/str/list/bool is kept or ignored
  silently, and a non-number in wip-policy.md crashes the board.

Sources of a board's WIP limits (all covered here or pinned elsewhere):

1. the theme's ``columns.<id>.wip_limit`` (validated since #378/#402; test_402);
2. config.yaml ``boards[].wip_limits`` — ``{column: int}``, ``{column: {type: int|None,
   _default: int}}``, ``{column: null}`` or ``null`` (``BoardConfig.from_dict``);
3. ``.yurtle-kanban/wip-policy.md`` — ``wip:ColumnLimit`` / ``wip:TypeLimit``
   ``wip:limit`` (``load_wip_policy``), merged on top of (2) in ``_apply_wip_overrides``.
   (``board-add --wip-limit col:N`` only writes (2).)

Decided behaviour (#402's [steer]: ``wip_limit: 0`` means no limit everywhere):

1. A per-type limit of 0 does not block ``move`` of that type into the column.
2. Board-config limits are validated like theme limits: a negative, a non-whole float,
   or a non-int (``"3"``, ``[1]``, ``true``) is dropped with ONE warning naming the
   config file and the column (or column and type); a whole float (``3.0``) is the int 3.
   After a drop that column has no board override, so the theme's limit applies, and
   ``board`` never shows a column as always over because of a negative override.
3. Controls: positive per-type and column limits still block over-limit moves, and
   valid overrides still win.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.issues.test_378_theme_field_shapes import (
    THEME,
    _clean_theme_cache,  # noqa: F401  (autouse fixture)
    _dump,
    _invoke,
    _item,
    _names,
    _no_crash,
    _normalized,
    _repo,
    _warnings,
    warnings_log,  # noqa: F401  (fixture)
)
from yurtle_kanban import config as config_mod
from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.models import Column, WorkItemStatus
from yurtle_kanban.service import KanbanService

# the theme's own in_progress limit: what applies once a bad board override is dropped
THEME_LIMIT = 3
THEME_DICT: dict[str, Any] = {
    "theme": {"name": "acme"},
    "columns": {
        "backlog": {"name": "Backlog", "order": 1},
        "in_progress": {"name": "In Progress", "order": 2, "wip_limit": THEME_LIMIT},
        "done": {"name": "Done", "order": 3},
    },
}

# every board-level limit shape that is not a whole number >= 0 (or null)
BAD_VALUES = {
    "neg_int": -1,
    "neg_big": -5,
    "neg_float": -1.0,
    "float": 1.5,
    "str": "3",
    "list": [1],
    "bool": True,
}
BAD_CASES = [pytest.param(v, id=k) for k, v in BAD_VALUES.items()]
NEG_CASES = [pytest.param(v, id=k) for k, v in BAD_VALUES.items() if k.startswith("neg")]

CONFIG_FILE = "config.yaml"
POLICY_FILE = "wip-policy.md"


def _cfg(wip_limits: Any = ...) -> str:
    """A multi-board config.yaml for ``devboard`` (optionally with ``wip_limits``)."""
    board: dict[str, Any] = {"name": "devboard", "preset": THEME, "path": "work/"}
    if wip_limits is not ...:
        board["wip_limits"] = wip_limits
    return yaml.safe_dump({"version": "2.0", "boards": [board]}, sort_keys=False)


def _make(tmp_path: Path, wip_limits: Any = ...) -> Path:
    return _repo(tmp_path / "repo", _cfg(wip_limits), _dump(THEME_DICT))


def _service(repo: Path, monkeypatch: pytest.MonkeyPatch) -> KanbanService:
    monkeypatch.chdir(repo)
    config_mod._theme_cache.clear()
    return KanbanService(KanbanConfig.load(repo / ".kanban" / "config.yaml"), repo)


def _in_progress(repo: Path, monkeypatch: pytest.MonkeyPatch) -> Column:
    board = _service(repo, monkeypatch).get_board()
    return next(c for c in board.columns if c.id == "in_progress")


def _move(repo: Path, monkeypatch: pytest.MonkeyPatch, item_id: str) -> Any:
    return _service(repo, monkeypatch).move_item(
        item_id, WorkItemStatus.IN_PROGRESS, commit=False, validate_workflow=False
    )


def _status(path: Path) -> str:
    for line in path.read_text().splitlines():
        if line.startswith("status:"):
            return line.split(":", 1)[1].strip()
    raise AssertionError(path.read_text())


def _hits(caplog: pytest.LogCaptureFixture, source: str, *dotted: str) -> list[str]:
    """Warnings naming ``source`` (the config file) and every one of ``dotted``."""
    return [
        m for m in _warnings(caplog) if source in m and all(_names(m, d) or d in m for d in dotted)
    ]


def _policy(repo: Path, body: str) -> None:
    """``.yurtle-kanban/wip-policy.md`` for ``devboard`` with ``body`` turtle triples."""
    d = repo / ".yurtle-kanban"
    d.mkdir(exist_ok=True)
    (d / POLICY_FILE).write_text(
        "---\ntitle: WIP Policy\n---\n\n# WIP Policy\n\n```turtle\n"
        "@prefix wip: <https://yurtle.dev/kanban/wip/> .\n"
        "@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .\n"
        "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .\n\n"
        '<#dev> rdf:type wip:Policy ;\n    wip:board "devboard" ;\n'
        '    wip:unlimited "false"^^xsd:boolean .\n\n'
        f"{body}\n```\n"
    )


def _column_limit(value: str) -> str:
    return (
        "<#dev-ip> rdf:type wip:ColumnLimit ;\n    wip:policy <#dev> ;\n"
        f'    wip:column "in_progress" ;\n    wip:limit {value} .\n'
    )


def _type_limit(item_type: str, value: str) -> str:
    return (
        f"<#dev-ip-{item_type}> rdf:type wip:TypeLimit ;\n    wip:policy <#dev> ;\n"
        f'    wip:column "in_progress" ;\n    wip:itemType "{item_type}" ;\n'
        f"    wip:limit {value} .\n"
    )


# turtle spellings of bad wip:limit values (a decimal, a string, a negative)
POLICY_BAD = {
    "neg_int": "-1",
    "neg_decimal": "-1.0",
    "decimal": "1.5",
    "str_num": '"3"',
    "str_word": '"two"',
}
POLICY_BAD_CASES = [pytest.param(v, id=k) for k, v in POLICY_BAD.items()]


# ---------------------------------------------------------------------------
# 1. Per-type limit of 0 — no limit for `move` too
# ---------------------------------------------------------------------------


class TestPerTypeZeroMove:
    @pytest.mark.parametrize("already", [0, 1, 2])
    def test_zero_type_limit_does_not_block_move(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, already: int
    ) -> None:
        repo = _make(tmp_path, {"in_progress": {"chore": 0, "task": 5}})
        for i in range(already):
            _item(repo, f"CHORE-10{i}", "chore", "in_progress")
        path = _item(repo, "CHORE-001", "chore", "backlog")
        moved = _move(repo, monkeypatch, "CHORE-001")
        assert moved.status == WorkItemStatus.IN_PROGRESS
        assert _status(path) == "in_progress"

    def test_zero_default_does_not_block_move(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _make(tmp_path, {"in_progress": {"_default": 0, "task": 5}})
        _item(repo, "CHORE-100", "chore", "in_progress")
        _item(repo, "CHORE-001", "chore", "backlog")
        assert _move(repo, monkeypatch, "CHORE-001").status == WorkItemStatus.IN_PROGRESS

    def test_zero_type_limit_move_cli(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _make(tmp_path, {"in_progress": {"chore": 0, "task": 5}})
        _item(repo, "CHORE-100", "chore", "in_progress")
        path = _item(repo, "CHORE-001", "chore", "ready")
        result = _invoke(repo, monkeypatch, ["move", "CHORE-001", "in_progress", "--no-commit"])
        _no_crash(result)
        assert "WIP limit" not in result.output, result.output
        assert result.exit_code == 0, result.output
        assert _status(path) == "in_progress"

    def test_zero_type_limit_board_agrees(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # the board already reads 0 as no limit (#402): move must agree with it
        repo = _make(tmp_path, {"in_progress": {"chore": 0, "task": 5}})
        _item(repo, "CHORE-100", "chore", "in_progress")
        _item(repo, "CHORE-101", "chore", "in_progress")
        result = _invoke(repo, monkeypatch, ["board"])
        _no_crash(result)
        assert result.exit_code == 0, result.output
        assert "Violations" not in _normalized(result, repo), result.output

    # controls ---------------------------------------------------------------

    def test_control_positive_type_limit_blocks(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _make(tmp_path, {"in_progress": {"chore": 1, "task": 5}})
        _item(repo, "CHORE-100", "chore", "in_progress")
        _item(repo, "CHORE-001", "chore", "backlog")
        with pytest.raises(ValueError, match="WIP limit reached for chores"):
            _move(repo, monkeypatch, "CHORE-001")

    def test_control_positive_type_limit_room_left(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _make(tmp_path, {"in_progress": {"chore": 2, "task": 5}})
        _item(repo, "CHORE-100", "chore", "in_progress")
        _item(repo, "CHORE-001", "chore", "backlog")
        assert _move(repo, monkeypatch, "CHORE-001").status == WorkItemStatus.IN_PROGRESS

    def test_control_other_type_zero_does_not_free_limited_type(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _make(tmp_path, {"in_progress": {"chore": 0, "task": 1}})
        _item(repo, "TASK-100", "task", "in_progress")
        _item(repo, "TASK-001", "task", "backlog")
        with pytest.raises(ValueError, match="WIP limit reached for tasks"):
            _move(repo, monkeypatch, "TASK-001")

    def test_control_null_type_limit_unlimited(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _make(tmp_path, {"in_progress": {"chore": None, "task": 1}})
        _item(repo, "CHORE-100", "chore", "in_progress")
        _item(repo, "CHORE-001", "chore", "backlog")
        assert _move(repo, monkeypatch, "CHORE-001").status == WorkItemStatus.IN_PROGRESS

    def test_control_positive_column_limit_blocks(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _make(tmp_path, {"in_progress": 1})
        _item(repo, "TASK-100", "task", "in_progress")
        _item(repo, "TASK-001", "task", "backlog")
        with pytest.raises(ValueError, match="WIP limit reached for In Progress"):
            _move(repo, monkeypatch, "TASK-001")

    def test_control_column_zero_does_not_block(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _make(tmp_path, {"in_progress": 0})
        _item(repo, "TASK-100", "task", "in_progress")
        _item(repo, "TASK-001", "task", "backlog")
        assert _move(repo, monkeypatch, "TASK-001").status == WorkItemStatus.IN_PROGRESS


# ---------------------------------------------------------------------------
# 2a. config.yaml boards[].wip_limits — validated like theme limits
# ---------------------------------------------------------------------------


class TestConfigColumnLimit:
    @pytest.mark.parametrize("value", BAD_CASES)
    def test_bad_column_override_dropped_with_one_warning(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        value: Any,
        warnings_log: pytest.LogCaptureFixture,  # noqa: F811
    ) -> None:
        repo = _make(tmp_path, {"in_progress": value})
        col = _in_progress(repo, monkeypatch)
        # no board override: the theme's limit applies
        assert col.wip_limit == THEME_LIMIT and type(col.wip_limit) is int
        assert col.type_wip_limits is None
        assert len(_hits(warnings_log, CONFIG_FILE, "in_progress")) == 1, _warnings(warnings_log)
        assert len(_warnings(warnings_log)) == 1, _warnings(warnings_log)

    @pytest.mark.parametrize("value", [4.0, 0.0])
    def test_whole_float_column_override_is_int(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        value: float,
        warnings_log: pytest.LogCaptureFixture,  # noqa: F811
    ) -> None:
        repo = _make(tmp_path, {"in_progress": value})
        col = _in_progress(repo, monkeypatch)
        assert col.wip_limit == int(value) and type(col.wip_limit) is int
        assert not _warnings(warnings_log), _warnings(warnings_log)

    @pytest.mark.parametrize("value", NEG_CASES)
    def test_board_negative_column_override_not_always_over(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: Any
    ) -> None:
        repo = _make(tmp_path, {"in_progress": value})
        _item(repo, "TASK-100", "task", "in_progress")
        _item(repo, "TASK-101", "task", "in_progress")
        result = _invoke(repo, monkeypatch, ["board"])
        _no_crash(result)
        assert result.exit_code == 0, result.output
        out = _normalized(result, repo)
        assert "/-" not in out, result.output
        assert "Violations" not in out, result.output
        assert f"(2/{THEME_LIMIT})" in out, result.output

    @pytest.mark.parametrize("value", NEG_CASES)
    def test_move_negative_column_override_uses_theme_limit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: Any
    ) -> None:
        repo = _make(tmp_path, {"in_progress": value})
        _item(repo, "TASK-100", "task", "in_progress")
        _item(repo, "TASK-001", "task", "backlog")
        assert _move(repo, monkeypatch, "TASK-001").status == WorkItemStatus.IN_PROGRESS

    # controls ---------------------------------------------------------------

    @pytest.mark.parametrize("value", [0, 1, 7])
    def test_control_valid_column_override_wins(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        value: int,
        warnings_log: pytest.LogCaptureFixture,  # noqa: F811
    ) -> None:
        repo = _make(tmp_path, {"in_progress": value})
        assert _in_progress(repo, monkeypatch).wip_limit == value
        assert not _warnings(warnings_log), _warnings(warnings_log)

    def test_control_null_column_unlimited(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        warnings_log: pytest.LogCaptureFixture,  # noqa: F811
    ) -> None:
        repo = _make(tmp_path, {"in_progress": None})
        col = _in_progress(repo, monkeypatch)
        assert col.wip_limit is None and col.type_wip_limits is None
        assert not _warnings(warnings_log), _warnings(warnings_log)

    def test_control_null_board_unlimited(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        warnings_log: pytest.LogCaptureFixture,  # noqa: F811
    ) -> None:
        repo = _make(tmp_path, None)
        assert _in_progress(repo, monkeypatch).wip_limit is None
        assert not _warnings(warnings_log), _warnings(warnings_log)

    def test_control_no_override_theme_limit(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        warnings_log: pytest.LogCaptureFixture,  # noqa: F811
    ) -> None:
        repo = _make(tmp_path)
        assert _in_progress(repo, monkeypatch).wip_limit == THEME_LIMIT
        assert not _warnings(warnings_log), _warnings(warnings_log)

    def test_control_valid_override_exceeded_is_violation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _make(tmp_path, {"in_progress": 1})
        _item(repo, "TASK-100", "task", "in_progress")
        _item(repo, "TASK-101", "task", "in_progress")
        result = _invoke(repo, monkeypatch, ["board"])
        _no_crash(result)
        out = _normalized(result, repo)
        assert "(2/1)" in out and "Violations" in out, result.output


class TestConfigPerTypeLimit:
    @pytest.mark.parametrize("value", BAD_CASES)
    def test_bad_type_limit_dropped_with_one_warning(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        value: Any,
        warnings_log: pytest.LogCaptureFixture,  # noqa: F811
    ) -> None:
        repo = _make(tmp_path, {"in_progress": {"task": 5, "chore": value}})
        col = _in_progress(repo, monkeypatch)
        assert col.type_wip_limits == {"task": 5}
        assert len(_hits(warnings_log, CONFIG_FILE, "in_progress", "chore")) == 1, _warnings(
            warnings_log
        )
        assert len(_warnings(warnings_log)) == 1, _warnings(warnings_log)

    @pytest.mark.parametrize("value", BAD_CASES)
    def test_bad_default_dropped_with_one_warning(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        value: Any,
        warnings_log: pytest.LogCaptureFixture,  # noqa: F811
    ) -> None:
        repo = _make(tmp_path, {"in_progress": {"task": 5, "_default": value}})
        col = _in_progress(repo, monkeypatch)
        assert col.type_wip_limits == {"task": 5}
        assert len(_hits(warnings_log, CONFIG_FILE, "in_progress", "_default")) == 1, _warnings(
            warnings_log
        )
        assert len(_warnings(warnings_log)) == 1, _warnings(warnings_log)

    @pytest.mark.parametrize("value", [2.0, 0.0])
    def test_whole_float_type_limit_is_int(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        value: float,
        warnings_log: pytest.LogCaptureFixture,  # noqa: F811
    ) -> None:
        repo = _make(tmp_path, {"in_progress": {"task": value}})
        limits = _in_progress(repo, monkeypatch).type_wip_limits
        assert limits == {"task": int(value)}
        assert type(limits["task"]) is int  # type: ignore[index]
        assert not _warnings(warnings_log), _warnings(warnings_log)

    @pytest.mark.parametrize("value", NEG_CASES)
    def test_board_negative_type_limit_not_always_over(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: Any
    ) -> None:
        repo = _make(tmp_path, {"in_progress": {"task": value, "chore": 5}})
        _item(repo, "TASK-100", "task", "in_progress")
        result = _invoke(repo, monkeypatch, ["board"])
        _no_crash(result)
        assert result.exit_code == 0, result.output
        out = _normalized(result, repo)
        assert "/-" not in out, result.output
        assert "Violations" not in out, result.output

    @pytest.mark.parametrize("value", NEG_CASES)
    def test_move_negative_type_limit_not_always_blocked(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: Any
    ) -> None:
        repo = _make(tmp_path, {"in_progress": {"task": value, "chore": 5}})
        _item(repo, "TASK-001", "task", "backlog")
        assert _move(repo, monkeypatch, "TASK-001").status == WorkItemStatus.IN_PROGRESS

    # controls ---------------------------------------------------------------

    def test_control_valid_type_limits_win(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        warnings_log: pytest.LogCaptureFixture,  # noqa: F811
    ) -> None:
        wip = {"task": 1, "chore": None, "bug": 0, "_default": 4}
        repo = _make(tmp_path, {"in_progress": dict(wip)})
        col = _in_progress(repo, monkeypatch)
        assert col.type_wip_limits == wip
        assert col.wip_limit is None
        assert not _warnings(warnings_log), _warnings(warnings_log)


# ---------------------------------------------------------------------------
# 2b. .yurtle-kanban/wip-policy.md — validated like theme limits
# ---------------------------------------------------------------------------


class TestPolicyFile:
    @pytest.mark.parametrize("value", POLICY_BAD_CASES)
    def test_bad_column_limit_dropped_with_one_warning(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        value: str,
        warnings_log: pytest.LogCaptureFixture,  # noqa: F811
    ) -> None:
        repo = _make(tmp_path)
        _policy(repo, _column_limit(value))
        col = _in_progress(repo, monkeypatch)
        assert col.wip_limit == THEME_LIMIT and type(col.wip_limit) is int
        assert col.type_wip_limits is None
        assert len(_hits(warnings_log, POLICY_FILE, "in_progress")) == 1, _warnings(warnings_log)
        assert len(_warnings(warnings_log)) == 1, _warnings(warnings_log)

    @pytest.mark.parametrize("value", POLICY_BAD_CASES)
    def test_bad_type_limit_dropped_with_one_warning(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        value: str,
        warnings_log: pytest.LogCaptureFixture,  # noqa: F811
    ) -> None:
        repo = _make(tmp_path)
        _policy(repo, _type_limit("task", "5") + "\n" + _type_limit("chore", value))
        col = _in_progress(repo, monkeypatch)
        assert col.type_wip_limits == {"task": 5}
        assert len(_hits(warnings_log, POLICY_FILE, "in_progress", "chore")) == 1, _warnings(
            warnings_log
        )
        assert len(_warnings(warnings_log)) == 1, _warnings(warnings_log)

    def test_whole_decimal_column_limit_is_int(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        warnings_log: pytest.LogCaptureFixture,  # noqa: F811
    ) -> None:
        repo = _make(tmp_path)
        _policy(repo, _column_limit("4.0"))
        col = _in_progress(repo, monkeypatch)
        assert col.wip_limit == 4 and type(col.wip_limit) is int
        assert not _warnings(warnings_log), _warnings(warnings_log)

    def test_board_negative_policy_limit_not_always_over(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _make(tmp_path)
        _policy(repo, _column_limit("-1"))
        _item(repo, "TASK-100", "task", "in_progress")
        _item(repo, "TASK-101", "task", "in_progress")
        result = _invoke(repo, monkeypatch, ["board"])
        _no_crash(result)
        assert result.exit_code == 0, result.output
        out = _normalized(result, repo)
        assert "/-" not in out and "Violations" not in out, result.output
        assert f"(2/{THEME_LIMIT})" in out, result.output

    def test_board_non_number_policy_limit_no_crash(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _make(tmp_path)
        _policy(repo, _column_limit('"two"'))
        _item(repo, "TASK-100", "task", "in_progress")
        result = _invoke(repo, monkeypatch, ["board"])
        _no_crash(result)
        assert result.exit_code == 0, result.output
        assert f"(1/{THEME_LIMIT})" in _normalized(result, repo), result.output

    # controls ---------------------------------------------------------------

    def test_control_valid_policy_limits_win(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        warnings_log: pytest.LogCaptureFixture,  # noqa: F811
    ) -> None:
        repo = _make(tmp_path, {"in_progress": 7})
        _policy(repo, _type_limit("task", "2") + "\n" + _type_limit("chore", "0"))
        col = _in_progress(repo, monkeypatch)
        assert col.type_wip_limits == {"task": 2, "chore": 0}
        assert not _warnings(warnings_log), _warnings(warnings_log)

    def test_control_valid_policy_column_limit_blocks_move(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _make(tmp_path)
        _policy(repo, _column_limit("1"))
        _item(repo, "TASK-100", "task", "in_progress")
        _item(repo, "TASK-001", "task", "backlog")
        with pytest.raises(ValueError, match="WIP limit reached for In Progress"):
            _move(repo, monkeypatch, "TASK-001")

    def test_policy_zero_type_limit_does_not_block_move(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _make(tmp_path)
        _policy(repo, _type_limit("task", "5") + "\n" + _type_limit("chore", "0"))
        _item(repo, "CHORE-100", "chore", "in_progress")
        _item(repo, "CHORE-001", "chore", "backlog")
        assert _move(repo, monkeypatch, "CHORE-001").status == WorkItemStatus.IN_PROGRESS
