"""Issue #480 — ``_get_board_transitions`` returns the theme's ``transitions`` raw.

Follow-up from the review of PR #477 (#474). ``_validate_transition``'s
``to_native in allowed`` and ``get_allowed_transitions``' iteration both assume every
``transitions`` entry is a list. The theme loader guarantees that (#457, #461), but a
theme dict that reaches the service another way (built in code, monkeypatched) is
used as is: a string entry ``"in_progress"`` makes ``move`` accept any status whose
theme name is a substring of it (``"in" in "in_progress"``), while the offer iterates
its characters and offers nothing.

Decided behaviour: ``_get_board_transitions`` returns a ``dict[str, list[str]]``
normalised the way the loader does it — a string entry becomes a one-item list, an
entry that is neither a list nor a string is skipped, non-string names inside a list
are dropped, and a ``transitions`` that isn't a mapping gives ``None`` — so ``move``
and the offer always agree.

1. ``_get_board_transitions`` itself, on raw theme dicts.
2. ``move`` (``_validate_transition``) and ``get_allowed_transitions`` on a raw theme
   handed to the service through ``_item_theme``: no substring match, no per-character
   junk, offered <=> accepted.
3. Controls: a well-formed list theme and the loader path are unchanged.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest

from tests.issues.test_457_transitions_shapes import (  # noqa: F401 (fixtures)
    CFGS,
    SINGLE_CFG,
    _allowed,
    _clean_theme_cache,
    _hdd,
    _move_ok,
    _repo,
    _seed,
    _service,
)
from tests.issues.test_461_transitions_list_entries import _assert_agrees
from yurtle_kanban.models import WorkItem, WorkItemStatus
from yurtle_kanban.service import KanbanService

CANONICAL = [s.value for s in WorkItemStatus]

# `ready` is called `in` on this theme: a substring of the `in_progress` entry
TRAP_MAPPINGS = {"draft": "backlog", "in": "ready"}


def _theme(transitions: Any, mappings: dict[str, str] | None = None) -> dict[str, Any]:
    return {
        "status_mappings": dict(TRAP_MAPPINGS if mappings is None else mappings),
        "transitions": transitions,
    }


@pytest.fixture
def service_and_item(tmp_path: Path) -> tuple[KanbanService, WorkItem]:
    repo = _repo(tmp_path / "repo", SINGLE_CFG, _hdd())
    _seed(repo, "IDEA-001", "backlog")
    service = _service(repo)
    item = service.get_item("IDEA-001")
    assert item is not None
    return service, item


def _use_theme(
    service: KanbanService, monkeypatch: pytest.MonkeyPatch, theme: dict[str, Any]
) -> None:
    """Hand the service a raw theme dict, bypassing the loader."""
    monkeypatch.setattr(service, "_item_theme", lambda _item: (None, theme))
    monkeypatch.setattr(service, "_load_board_theme", lambda _board: theme)


def _offer(service: KanbanService, item: WorkItem) -> list[str]:
    """`get_allowed_transitions`; a crash is a failed expectation, not an error."""
    try:
        return service.get_allowed_transitions(item)
    except Exception as e:  # noqa: BLE001
        raise AssertionError(f"get_allowed_transitions crashed: {e!r}") from e


def _accepted(service: KanbanService, item: WorkItem) -> set[str]:
    """The statuses `move` (`_validate_transition`) accepts; a crash fails."""
    out = set()
    for t in WorkItemStatus:
        try:
            ok, _ = service._validate_transition(item, t)
        except Exception as e:  # noqa: BLE001
            raise AssertionError(f"_validate_transition crashed on {t.value}: {e!r}") from e
        if ok:
            out.add(t.value)
    return out


def _disagreements(service: KanbanService, item: WorkItem) -> list[str]:
    out = []
    for status in WorkItemStatus:
        probe = dataclasses.replace(item, status=status)
        offered = set(_offer(service, probe))
        accepted = _accepted(service, probe)
        if offered != accepted:
            out.append(f"from {status.value}: offers {offered}, move accepts {accepted}")
    return out


# ---------------------------------------------------------------------------
# 1. _get_board_transitions normalises a raw theme
# ---------------------------------------------------------------------------


class TestGetBoardTransitions:
    def test_string_entry_becomes_one_item_list(self, service_and_item) -> None:
        service, _ = service_and_item
        got = service._get_board_transitions(None, _theme({"draft": "in_progress"}))
        assert got == {"draft": ["in_progress"]}

    def test_non_string_names_dropped(self, service_and_item) -> None:
        service, _ = service_and_item
        raw = {"draft": [5, "in_progress", {"a": 1}, None, "review"]}
        got = service._get_board_transitions(None, _theme(raw))
        assert got == {"draft": ["in_progress", "review"]}

    @pytest.mark.parametrize("bad", [5, None, {"a": 1}, 1.5, True], ids=repr)
    def test_non_list_non_string_entry_skipped(self, service_and_item, bad: Any) -> None:
        service, _ = service_and_item
        raw = {"draft": bad, "in": ["in_progress"]}
        got = service._get_board_transitions(None, _theme(raw))
        assert got == {"in": ["in_progress"]}

    @pytest.mark.parametrize("bad", ["in_progress", ["draft"], 5], ids=repr)
    def test_non_mapping_transitions_is_none(self, service_and_item, bad: Any) -> None:
        service, _ = service_and_item
        assert service._get_board_transitions(None, _theme(bad)) is None

    def test_every_entry_is_a_list_of_str(self, service_and_item) -> None:
        service, _ = service_and_item
        raw = {"draft": "in_progress", "in": [1, "done"], "review": 7, "done": []}
        got = service._get_board_transitions(None, _theme(raw))
        assert isinstance(got, dict)
        for key, names in got.items():
            assert isinstance(names, list), (key, names)
            assert all(isinstance(n, str) for n in names), (key, names)

    def test_well_formed_lists_unchanged(self, service_and_item) -> None:
        service, _ = service_and_item
        raw = {"draft": ["in", "in_progress"], "in": ["review"], "done": []}
        got = service._get_board_transitions(None, _theme(raw))
        assert got == {"draft": ["in", "in_progress"], "in": ["review"], "done": []}

    def test_no_transitions_is_none(self, service_and_item) -> None:
        service, _ = service_and_item
        assert service._get_board_transitions(None, {"status_mappings": {}}) is None


# ---------------------------------------------------------------------------
# 2. move and the offer on a raw theme
# ---------------------------------------------------------------------------


class TestStringEntry:
    """`transitions: {draft: "in_progress"}` with `ready` named `in`."""

    @pytest.fixture
    def svc(self, service_and_item, monkeypatch) -> tuple[KanbanService, WorkItem]:
        service, item = service_and_item
        _use_theme(service, monkeypatch, _theme({"draft": "in_progress"}))
        return service, item

    def test_move_rejects_substring_name(self, svc) -> None:
        """`in` (ready) is a substring of "in_progress", not the listed name."""
        service, item = svc
        ok, _ = service._validate_transition(item, WorkItemStatus.READY)
        assert not ok

    def test_move_accepts_listed_name(self, svc) -> None:
        service, item = svc
        ok, msg = service._validate_transition(item, WorkItemStatus.IN_PROGRESS)
        assert ok, msg

    def test_move_accepts_exactly_in_progress(self, svc) -> None:
        service, item = svc
        assert _accepted(service, item) == {"in_progress"}

    def test_offer_is_in_progress(self, svc) -> None:
        service, item = svc
        assert _offer(service, item) == ["in_progress"]

    def test_move_item_refuses_ready(self, svc) -> None:
        service, _ = svc
        with pytest.raises(ValueError, match="Invalid transition"):
            service.move_item(
                "IDEA-001",
                WorkItemStatus.READY,
                commit=False,
                skip_wip_check=True,
                skip_gates=True,
            )

    def test_offered_iff_accepted(self, svc) -> None:
        service, item = svc
        assert not _disagreements(service, item)


class TestPlainStringEntry:
    """No mapping trap: `transitions: {backlog: "review"}` offers `review`."""

    @pytest.fixture
    def svc(self, service_and_item, monkeypatch) -> tuple[KanbanService, WorkItem]:
        service, item = service_and_item
        _use_theme(service, monkeypatch, _theme({"backlog": "review"}, mappings={}))
        return service, item

    def test_offer_is_review_not_characters(self, svc) -> None:
        service, item = svc
        assert _offer(service, item) == ["review"]

    def test_move_accepts_exactly_review(self, svc) -> None:
        service, item = svc
        assert _accepted(service, item) == {"review"}

    def test_offered_iff_accepted(self, svc) -> None:
        service, item = svc
        assert not _disagreements(service, item)


class TestMalformedEntries:
    @pytest.mark.parametrize("bad", [5, None, {"a": 1}, 1.5], ids=repr)
    def test_non_list_entry_skipped_no_crash(self, service_and_item, monkeypatch, bad: Any) -> None:
        """`draft: 5` is skipped (nothing allowed from draft); `in` keeps its list."""
        service, item = service_and_item
        _use_theme(service, monkeypatch, _theme({"draft": bad, "in": ["in_progress"]}))

        assert _offer(service, item) == []
        assert _accepted(service, item) == set()
        assert not _disagreements(service, item)

    def test_non_string_names_dropped(self, service_and_item, monkeypatch) -> None:
        service, item = service_and_item
        _use_theme(service, monkeypatch, _theme({"draft": [5, "in_progress", {"a": 1}]}))

        assert _offer(service, item) == ["in_progress"]
        assert _accepted(service, item) == {"in_progress"}

    @pytest.mark.parametrize("bad", ["in_progress", ["in_progress"]], ids=repr)
    def test_non_mapping_transitions_falls_back(
        self, service_and_item, monkeypatch, bad: Any
    ) -> None:
        """No theme transitions: move and the offer take the same fallback, and agree."""
        service, item = service_and_item
        _use_theme(service, monkeypatch, _theme(bad))

        assert not _disagreements(service, item)

    def test_offer_never_has_junk(self, service_and_item, monkeypatch) -> None:
        service, item = service_and_item
        raw = {k: "in_progress" for k in ("draft", "in", "in_progress", "review", "done")}
        _use_theme(service, monkeypatch, _theme(raw))
        for status in WorkItemStatus:
            offered = _offer(service, dataclasses.replace(item, status=status))
            assert set(offered) <= set(CANONICAL), (status, offered)


# ---------------------------------------------------------------------------
# 3. Controls
# ---------------------------------------------------------------------------


class TestControls:
    def test_well_formed_list_theme(self, service_and_item, monkeypatch) -> None:
        service, item = service_and_item
        _use_theme(service, monkeypatch, _theme({"draft": ["in", "in_progress"]}))

        assert _offer(service, item) == ["ready", "in_progress"]
        assert _accepted(service, item) == {"ready", "in_progress"}
        assert not _disagreements(service, item)

    def test_well_formed_list_excludes_substring(self, service_and_item, monkeypatch) -> None:
        """A list entry `["in_progress"]` never matched `in`: still so."""
        service, item = service_and_item
        _use_theme(service, monkeypatch, _theme({"draft": ["in_progress"]}))

        assert _accepted(service, item) == {"in_progress"}
        assert _offer(service, item) == ["in_progress"]

    @pytest.mark.parametrize("cfg", CFGS)
    def test_loader_path_string_entry(self, tmp_path: Path, cfg: str) -> None:
        """Through the loader (#457) the string entry is already a list: unchanged."""
        data = _hdd()
        data["status_mappings"] = dict(TRAP_MAPPINGS)
        data["transitions"] = {"draft": "in_progress"}
        repo = _repo(tmp_path / "repo", cfg, data)
        _seed(repo, "IDEA-001", "backlog")

        assert _allowed(repo, "IDEA-001") == ["in_progress"]
        assert not _move_ok(repo, "IDEA-001", WorkItemStatus.READY)
        _assert_agrees(repo, "IDEA-001")

    @pytest.mark.parametrize("cfg", CFGS)
    def test_loader_path_hdd(self, tmp_path: Path, cfg: str) -> None:
        repo = _repo(tmp_path / "repo", cfg, _hdd())
        _seed(repo, "IDEA-001", "backlog")

        assert _allowed(repo, "IDEA-001") == ["in_progress", "blocked"]
        _assert_agrees(repo, "IDEA-001")
