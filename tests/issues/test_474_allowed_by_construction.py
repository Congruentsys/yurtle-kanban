"""Issue #474 — ``get_allowed_transitions`` under-offers when a native name is also
an unmapped canonical value.

Follow-up from the review of PR #470 (#467). With
``status_mappings: {draft: backlog, done: review}`` and ``transitions: {draft: [done]}``,
``move`` accepts ``done`` (``reverse.get('done', 'done') == 'done'`` is listed) and
also ``review`` (``reverse['review'] == 'done'``), but ``get_allowed_transitions``
offers only ``review``.

Decided behaviour: ``get_allowed_transitions`` offers exactly the statuses ``move``
accepts, by construction — a canonical target ``t`` is offered iff
``reverse.get(t, t) in board_transitions[from_native]``, which is ``move``'s own
check (``KanbanService._validate_transition``). The list keeps the theme's order, so
the #461 / #467 order expectations stay green.

1. The issue's repro, single-board and multi-board: both ``done`` and ``review`` are
   offered, because ``move`` accepts both.
2. A seeded random property: over ~200 small themes (collisions, natives that are
   also canonical values, unmapped and misspelt entries), for every item status and
   every target, offered <=> ``_validate_transition`` accepts.
"""

from __future__ import annotations

import dataclasses
import random
from pathlib import Path
from typing import Any

import pytest

from tests.issues.test_457_transitions_shapes import (  # noqa: F401 (fixtures)
    CFGS,
    SINGLE_CFG,
    _allowed,
    _clean_theme_cache,
    _git,
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


def _repro_theme() -> dict[str, Any]:
    data = _hdd()
    data["status_mappings"] = {"draft": "backlog", "done": "review"}
    data["transitions"] = {"draft": ["done"]}
    return data


def _repro_repo(tmp_path: Path, cfg: str) -> Path:
    repo = _repo(tmp_path / "repo", cfg, _repro_theme())
    _seed(repo, "IDEA-001", "backlog")
    return repo


# ---------------------------------------------------------------------------
# 1. The issue's repro
# ---------------------------------------------------------------------------


class TestIssueRepro:
    @pytest.mark.parametrize("cfg", CFGS)
    def test_move_accepts_done_and_review(self, tmp_path: Path, cfg: str) -> None:
        """Premise (probed): `move` takes `done` (native `done`, unmapped canonical)
        and `review` (mapped to native `done`); nothing else."""
        repo = _repro_repo(tmp_path, cfg)

        accepted = set()
        for target in WorkItemStatus:
            if _move_ok(repo, "IDEA-001", target):
                accepted.add(target.value)
            _git(repo, "reset", "--hard", "-q")  # undo the probe
            _git(repo, "clean", "-fdq")

        assert accepted == {"done", "review"}

    @pytest.mark.parametrize("cfg", CFGS)
    def test_done_offered(self, tmp_path: Path, cfg: str) -> None:
        repo = _repro_repo(tmp_path, cfg)

        assert "done" in _allowed(repo, "IDEA-001")

    @pytest.mark.parametrize("cfg", CFGS)
    def test_review_still_offered(self, tmp_path: Path, cfg: str) -> None:
        repo = _repro_repo(tmp_path, cfg)

        assert "review" in _allowed(repo, "IDEA-001")

    @pytest.mark.parametrize("cfg", CFGS)
    def test_offers_exactly_done_and_review(self, tmp_path: Path, cfg: str) -> None:
        repo = _repro_repo(tmp_path, cfg)

        assert sorted(_allowed(repo, "IDEA-001")) == ["done", "review"]

    @pytest.mark.parametrize("cfg", CFGS)
    def test_agrees_with_move(self, tmp_path: Path, cfg: str) -> None:
        repo = _repro_repo(tmp_path, cfg)

        _assert_agrees(repo, "IDEA-001")


# ---------------------------------------------------------------------------
# 2. Randomized agreement property
# ---------------------------------------------------------------------------

SEED = 474
N_CASES = 200
# natives: some are also canonical values, one is a misspelling `move` won't match
NATIVES = ["draft", "active", "complete", "done", "review", "backlog", "ready", "wip"]
EXTRA_ENTRIES = ["in-progress", "In_Progress", "shipped"]


def _random_theme(rng: random.Random) -> dict[str, Any]:
    natives = rng.sample(NATIVES, rng.randint(1, 5))
    # canonicals drawn with replacement: collisions (two natives -> one canonical)
    mappings = {n: rng.choice(CANONICAL) for n in natives}
    keys_pool = sorted(set(NATIVES) | set(CANONICAL))
    entry_pool = keys_pool + EXTRA_ENTRIES
    transitions = {
        key: [rng.choice(entry_pool) for _ in range(rng.randint(0, 4))]
        for key in rng.sample(keys_pool, rng.randint(1, len(keys_pool)))
    }
    return {"status_mappings": mappings, "transitions": transitions}


def _themes() -> list[dict[str, Any]]:
    rng = random.Random(SEED)
    return [_random_theme(rng) for _ in range(N_CASES)]


@pytest.fixture
def service_and_item(tmp_path: Path) -> tuple[KanbanService, WorkItem]:
    repo = _repo(tmp_path / "repo", SINGLE_CFG, _hdd())
    _seed(repo, "IDEA-001", "backlog")
    service = _service(repo)
    item = service.get_item("IDEA-001")
    assert item is not None
    return service, item


def _disagreements(
    service: KanbanService,
    item: WorkItem,
    monkeypatch: pytest.MonkeyPatch,
) -> list[str]:
    out: list[str] = []
    for i, theme in enumerate(_themes()):
        monkeypatch.setattr(service, "_item_theme", lambda _item, t=theme: (None, t))
        for status in WorkItemStatus:
            probe = dataclasses.replace(item, status=status)
            offered = service.get_allowed_transitions(probe)
            for target in WorkItemStatus:
                accepted, _ = service._validate_transition(probe, target)
                if (target.value in offered) != accepted:
                    out.append(
                        f"case {i} {theme}: from {status.value}, "
                        f"{'offers' if target.value in offered else 'omits'} "
                        f"{target.value}; move {'accepts' if accepted else 'refuses'} it"
                    )
    return out


class TestAgreementProperty:
    def test_move_goes_through_validate_transition(
        self,
        service_and_item: tuple[KanbanService, WorkItem],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Premise: `move_item` asks `_validate_transition` and obeys it both ways."""
        service, _ = service_and_item
        calls: list[WorkItemStatus] = []

        def refuse(item: WorkItem, new: WorkItemStatus) -> tuple[bool, str]:
            calls.append(new)
            return False, "Invalid transition sentinel-474"

        monkeypatch.setattr(service, "_validate_transition", refuse)
        with pytest.raises(ValueError, match="sentinel-474"):
            service.move_item(
                "IDEA-001", WorkItemStatus.IN_PROGRESS,
                commit=False, skip_wip_check=True, skip_gates=True,
            )
        assert calls == [WorkItemStatus.IN_PROGRESS]

        # hdd refuses draft -> complete; a permissive validator lets it through
        monkeypatch.setattr(service, "_validate_transition", lambda i, n: (True, ""))
        moved = service.move_item(
            "IDEA-001", WorkItemStatus.DONE,
            commit=False, skip_wip_check=True, skip_gates=True,
        )
        assert moved.status == WorkItemStatus.DONE

    def test_themes_are_deterministic_and_varied(self) -> None:
        themes = _themes()
        assert themes == _themes()
        assert len(themes) == N_CASES
        # the generator exercises the shapes #474 is about
        collisions = sum(
            len(set(t["status_mappings"].values())) < len(t["status_mappings"])
            for t in themes
        )
        native_is_canonical = sum(
            any(n in CANONICAL and n != c for n, c in t["status_mappings"].items())
            for t in themes
        )
        assert collisions > 10, collisions
        assert native_is_canonical > 10, native_is_canonical

    def test_offered_iff_move_accepts(
        self,
        service_and_item: tuple[KanbanService, WorkItem],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        service, item = service_and_item

        bad = _disagreements(service, item, monkeypatch)

        assert not bad, f"{len(bad)} disagreements, e.g.:\n" + "\n".join(bad[:10])

    def test_offered_list_has_no_duplicates(
        self,
        service_and_item: tuple[KanbanService, WorkItem],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        service, item = service_and_item
        for theme in _themes():
            monkeypatch.setattr(service, "_item_theme", lambda _item, t=theme: (None, t))
            for status in WorkItemStatus:
                offered = service.get_allowed_transitions(
                    dataclasses.replace(item, status=status)
                )
                assert len(offered) == len(set(offered)), (theme, status, offered)
                assert set(offered) <= set(CANONICAL), (theme, status, offered)
