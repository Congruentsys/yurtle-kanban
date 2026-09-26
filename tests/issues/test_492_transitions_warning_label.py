"""Issue #492 — the service-path ``_clean_transitions`` warning names nothing.

Follow-up from the review of PR #486 (#480). ``_get_board_transitions`` passes the
label ``"theme"`` to ``_clean_transitions``, so a warning for a raw theme dict (one
that bypassed the loader) names neither the board nor the preset.

Decided label (the ``where`` prefix of the warning):

1. a ``BoardConfig`` is known → ``board <name> (theme <preset>)``;
2. else the dict names itself with a string ``theme.name`` → ``theme <name>``;
3. else plain ``theme``.

Controls: the dict with no name still warns (naming ``transitions.draft``), and the
loader path still names the theme file, unchanged.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.issues.test_457_transitions_shapes import (  # noqa: F401 (fixtures)
    LOGGER,
    MULTI_CFG,
    SINGLE_CFG,
    THEME,
    THEME_FILE,
    _clean_theme_cache,
    _hdd,
    _names_entry,
    _repo,
    _seed,
    _service,
    _warnings,
)
from yurtle_kanban.models import WorkItem, WorkItemStatus
from yurtle_kanban.service import KanbanService

BOARD = "research"  # MULTI_CFG's board; its preset is THEME
BOARD_LABEL = f"board {BOARD} (theme {THEME})"


@pytest.fixture
def warnings_log(caplog: pytest.LogCaptureFixture) -> Iterator[pytest.LogCaptureFixture]:
    """caplog, attached straight to the yurtle-kanban logger (as in test_457)."""
    log = logging.getLogger(LOGGER)
    log.addHandler(caplog.handler)
    old_level = log.level
    log.setLevel(logging.WARNING)
    caplog.handler.setLevel(logging.WARNING)
    try:
        yield caplog
    finally:
        log.removeHandler(caplog.handler)
        log.setLevel(old_level)


def _raw(transitions: Any, name: Any = None) -> dict[str, Any]:
    theme: dict[str, Any] = {"status_mappings": {}, "transitions": transitions}
    if name is not None:
        theme["theme"] = {"name": name}
    return theme


def _setup(tmp_path: Path, cfg: str) -> tuple[KanbanService, WorkItem]:
    repo = _repo(tmp_path / "repo", cfg, _hdd())
    _seed(repo, "IDEA-001", "backlog")
    service = _service(repo)
    item = service.get_item("IDEA-001")
    assert item is not None
    return service, item


def _hits(caplog: pytest.LogCaptureFixture, entry: str = "transitions.draft") -> list[str]:
    return [m for m in _warnings(caplog) if _names_entry(m, entry)]


# ---------------------------------------------------------------------------
# 1. multi-board: the board and its preset
# ---------------------------------------------------------------------------


class TestMultiBoardLabel:
    @pytest.fixture
    def svc(self, tmp_path: Path) -> tuple[KanbanService, WorkItem]:
        service, item = _setup(tmp_path, MULTI_CFG)
        board = service._get_board_for_item(item)
        assert board is not None and board.name == BOARD and board.preset == THEME  # premise
        return service, item

    @pytest.mark.parametrize(
        "draft", [5, [5, "review"]], ids=["not-a-list", "non-string-name"]
    )
    def test_direct_call_names_board_and_preset(
        self, svc, warnings_log: pytest.LogCaptureFixture, draft: Any
    ) -> None:
        service, item = svc
        board = service._get_board_for_item(item)
        warnings_log.clear()

        service._get_board_transitions(board, _raw({"draft": draft}))

        hits = _hits(warnings_log)
        assert hits, _warnings(warnings_log)
        assert all(BOARD_LABEL in m for m in hits), hits

    def test_board_wins_over_theme_name(
        self, svc, warnings_log: pytest.LogCaptureFixture
    ) -> None:
        service, item = svc
        board = service._get_board_for_item(item)
        warnings_log.clear()

        service._get_board_transitions(board, _raw({"draft": 5}, name="lab"))

        hits = _hits(warnings_log)
        assert hits and all(BOARD_LABEL in m for m in hits), hits

    def test_offer_on_raw_board_theme_names_board(
        self, svc, monkeypatch: pytest.MonkeyPatch, warnings_log: pytest.LogCaptureFixture
    ) -> None:
        """Raw theme handed in through `_load_board_theme`; `_item_theme` keeps the board."""
        service, item = svc
        monkeypatch.setattr(service, "_load_board_theme", lambda _b: _raw({"draft": 5}))
        warnings_log.clear()

        service.get_allowed_transitions(item)
        service._validate_transition(item, WorkItemStatus.IN_PROGRESS)

        hits = _hits(warnings_log)
        assert hits, _warnings(warnings_log)
        assert all(BOARD_LABEL in m for m in hits), hits


# ---------------------------------------------------------------------------
# 2. single board: the dict's own theme.name
# ---------------------------------------------------------------------------


class TestThemeNameLabel:
    @pytest.fixture
    def svc(self, tmp_path: Path) -> tuple[KanbanService, WorkItem]:
        return _setup(tmp_path, SINGLE_CFG)

    @pytest.mark.parametrize(
        "draft", [5, [5, "review"]], ids=["not-a-list", "non-string-name"]
    )
    def test_direct_call_names_theme(
        self, svc, warnings_log: pytest.LogCaptureFixture, draft: Any
    ) -> None:
        service, _ = svc
        warnings_log.clear()

        service._get_board_transitions(None, _raw({"draft": draft}, name="lab"))

        hits = _hits(warnings_log)
        assert hits, _warnings(warnings_log)
        assert all("theme lab" in m for m in hits), hits

    def test_offer_on_raw_single_theme_names_theme(
        self, svc, monkeypatch: pytest.MonkeyPatch, warnings_log: pytest.LogCaptureFixture
    ) -> None:
        service, item = svc
        raw = _raw({"draft": 5}, name="lab")
        monkeypatch.setattr(service.config, "get_theme", lambda *a, **k: raw)
        assert service._item_theme(item) == (None, raw)  # premise
        warnings_log.clear()

        service.get_allowed_transitions(item)

        hits = _hits(warnings_log)
        assert hits and all("theme lab" in m for m in hits), hits


# ---------------------------------------------------------------------------
# 3. controls
# ---------------------------------------------------------------------------


class TestControls:
    @pytest.mark.parametrize("name", [None, 5, {"x": 1}], ids=["absent", "int", "mapping"])
    def test_no_usable_name_still_warns(
        self, tmp_path: Path, warnings_log: pytest.LogCaptureFixture, name: Any
    ) -> None:
        service, _ = _setup(tmp_path, SINGLE_CFG)
        warnings_log.clear()

        got = service._get_board_transitions(None, _raw({"draft": 5, "done": []}, name=name))

        assert got == {"done": []}
        hits = _hits(warnings_log)
        assert len(hits) == 1, _warnings(warnings_log)
        assert hits[0].startswith("theme"), hits
        assert "board" not in hits[0], hits

    def test_well_formed_raw_theme_no_warning(
        self, tmp_path: Path, warnings_log: pytest.LogCaptureFixture
    ) -> None:
        service, item = _setup(tmp_path, MULTI_CFG)
        board = service._get_board_for_item(item)
        warnings_log.clear()

        service._get_board_transitions(board, _raw({"draft": ["review"]}, name="lab"))

        assert not _warnings(warnings_log), _warnings(warnings_log)

    @pytest.mark.parametrize("cfg", [SINGLE_CFG, MULTI_CFG], ids=["single", "multi"])
    def test_loader_path_names_theme_file(
        self, tmp_path: Path, warnings_log: pytest.LogCaptureFixture, cfg: str
    ) -> None:
        """Through the loader the warning names the theme file, once; the service's
        pass over the already-clean dict adds none."""
        data = _hdd()
        data["transitions"]["draft"] = 5
        repo = _repo(tmp_path / "repo", cfg, data)
        _seed(repo, "IDEA-001", "backlog")
        service = _service(repo)
        item = service.get_item("IDEA-001")
        assert item is not None

        service.get_allowed_transitions(item)
        service._validate_transition(item, WorkItemStatus.IN_PROGRESS)

        hits = _hits(warnings_log)
        assert len(hits) == 1, _warnings(warnings_log)
        assert "theme file" in hits[0] and THEME_FILE in hits[0], hits
        assert "board" not in hits[0], hits
