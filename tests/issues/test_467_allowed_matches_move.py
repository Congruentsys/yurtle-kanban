"""Issue #467 — ``get_allowed_transitions`` offers a canonical target that ``move``
refuses.

Follow-up from the review of PR #464 (#461). With an hdd-like theme where
``transitions.draft`` is ``[in_progress]`` (the canonical spelling) and ``active``
maps to ``in_progress``, ``get_allowed_transitions`` offers ``in_progress``; but
``move`` maps it to the native ``active`` (``to_native``), which is not listed, and
refuses it.

Decided behaviour: ``get_allowed_transitions`` offers a target only if ``move``
would accept it (``reverse.get(value, value) == native``, what ``move`` checks), so
the offer agrees with ``move`` for every candidate — single-board and multi-board.

Controls:

- ``[active]`` still offers ``in_progress``, and ``move`` accepts it;
- ``[active, in_progress]`` still offers ``in_progress`` once;
- the #461 and #457 tests stay green.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest

from tests.issues.test_457_transitions_shapes import (  # noqa: F401 (fixtures)
    CFGS,
    _allowed,
    _clean_theme_cache,
    _hdd,
    _hdd_with,
    _move,
    _move_ok,
    _repo,
    _seed,
    _status,
    wide,
)
from tests.issues.test_461_transitions_list_entries import _assert_agrees
from yurtle_kanban.models import WorkItemStatus


def _repo_with(tmp_path: Path, cfg: str, theme: dict[str, Any], status: str = "backlog") -> Path:
    repo = _repo(tmp_path / "repo", cfg, theme)
    _seed(repo, "IDEA-001", status)
    return repo


def _active_to(targets: list[str]) -> dict[str, Any]:
    """hdd with ``transitions.active`` set to ``targets``."""
    data = _hdd()
    data["transitions"]["active"] = targets
    return data


# ---------------------------------------------------------------------------
# The bug: a mapped status's canonical spelling is listed as a target
# ---------------------------------------------------------------------------


class TestCanonicalSpellingOfMappedStatus:
    @pytest.mark.parametrize("cfg", CFGS)
    def test_move_refuses_canonical_spelling(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        wide: io.StringIO,  # noqa: F811
        cfg: str,
    ) -> None:
        """Premise: `move` checks the native `active`, which `[in_progress]` lacks."""
        repo = _repo_with(tmp_path, cfg, _hdd_with(["in_progress"]))

        result, out = _move(repo, monkeypatch, wide, "IDEA-001", "in_progress")

        assert result.exit_code != 0, out
        assert "Invalid transition" in out, out
        assert _status(repo, "IDEA-001") == WorkItemStatus.BACKLOG
        assert not _move_ok(repo, "IDEA-001", WorkItemStatus.IN_PROGRESS)

    @pytest.mark.parametrize("cfg", CFGS)
    def test_not_offered_alone(self, tmp_path: Path, cfg: str) -> None:
        repo = _repo_with(tmp_path, cfg, _hdd_with(["in_progress"]))

        assert _allowed(repo, "IDEA-001") == []

    @pytest.mark.parametrize("cfg", CFGS)
    def test_agrees_with_move_alone(self, tmp_path: Path, cfg: str) -> None:
        repo = _repo_with(tmp_path, cfg, _hdd_with(["in_progress"]))

        _assert_agrees(repo, "IDEA-001")

    @pytest.mark.parametrize("cfg", CFGS)
    def test_not_offered_beside_valid_native(self, tmp_path: Path, cfg: str) -> None:
        """`[in_progress, abandoned]`: only `abandoned` (-> blocked) is movable."""
        repo = _repo_with(tmp_path, cfg, _hdd_with(["in_progress", "abandoned"]))

        assert _allowed(repo, "IDEA-001") == ["blocked"]
        _assert_agrees(repo, "IDEA-001")

    @pytest.mark.parametrize("cfg", CFGS)
    def test_other_from_status(self, tmp_path: Path, cfg: str) -> None:
        """Same rule from `active`: `[done, abandoned]` — `done`'s native is `complete`."""
        repo = _repo_with(tmp_path, cfg, _active_to(["done", "abandoned"]), status="active")

        assert _allowed(repo, "IDEA-001") == ["blocked"]
        _assert_agrees(repo, "IDEA-001")


# ---------------------------------------------------------------------------
# Controls
# ---------------------------------------------------------------------------


class TestControls:
    @pytest.mark.parametrize("cfg", CFGS)
    def test_native_offered_and_accepted(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        wide: io.StringIO,  # noqa: F811
        cfg: str,
    ) -> None:
        repo = _repo_with(tmp_path, cfg, _hdd_with(["active"]))

        assert _allowed(repo, "IDEA-001") == ["in_progress"]
        _assert_agrees(repo, "IDEA-001")

        result, out = _move(repo, monkeypatch, wide, "IDEA-001", "in_progress")
        assert result.exit_code == 0, out
        assert _status(repo, "IDEA-001") == WorkItemStatus.IN_PROGRESS

    @pytest.mark.parametrize("cfg", CFGS)
    def test_native_and_canonical_offered_once(self, tmp_path: Path, cfg: str) -> None:
        repo = _repo_with(tmp_path, cfg, _hdd_with(["active", "in_progress"]))

        assert _allowed(repo, "IDEA-001") == ["in_progress"]
        _assert_agrees(repo, "IDEA-001")

    @pytest.mark.parametrize("cfg", CFGS)
    def test_stock_hdd_agrees(self, tmp_path: Path, cfg: str) -> None:
        repo = _repo_with(tmp_path, cfg, _hdd())

        assert _allowed(repo, "IDEA-001") == ["in_progress", "blocked"]
        _assert_agrees(repo, "IDEA-001")
