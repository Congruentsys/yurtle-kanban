"""#606: pin `wip_exempt_types` combined with a within-column move.

Follow-up from the review of PR #601 (#598). `wip_exempt_types` is a BOARD
setting (config.yaml `boards[].wip_exempt_types`); no column carries its own.
`move_item` uses it in two places, on top of the #595 self-exclusion
(`i.id != item.id`):

- skip: if the moving item's type is exempt, the WIP check is skipped entirely;
- filter: the aggregate count of the target column leaves exempt items out.

Current behaviour pinned here (in_progress limit 2, `wip_exempt_types: [voyage]`):

1. a forced `in_progress -> in_progress` move (workflow off, WIP check on) of an
   EXEMPT voyage succeeds in a column whose non-exempt items already fill the
   limit, and in a column whose voyages alone would overflow it;
2. the same move of a NON-exempt expedition at the full column succeeds: it is
   not counted against itself, and the voyages in the column are not counted;
3. controls: a new expedition into the full column is refused with `(2/2)`
   (voyages not counted), and a new voyage into it still moves in.

With a per-type limit, the skip still applies: an exempt voyage moves within a
column whose voyage count is already over its per-type limit.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.models import WorkItemStatus
from yurtle_kanban.service import KanbanService

CONFIG = """
version: "2.0"
boards:
  - name: development
    preset: nautical
    path: work/
    wip_exempt_types:
      - voyage
    wip_limits:
      in_progress: {limit}
default_board: development
"""


def _item(item_id: str, item_type: str, status: str, assignee: str | None = None) -> str:
    who = f"assignee: {assignee}\n" if assignee else ""
    return (
        f"---\nid: {item_id}\ntitle: \"{item_type.title()} {item_id}\"\ntype: {item_type}\n"
        f"status: {status}\n{who}---\n# {item_id}\n\nA description long enough.\n"
    )


def _repo(tmp_path: Path, limit: str, items: dict[str, tuple[str, str]]) -> KanbanService:
    cfg = tmp_path / ".kanban" / "config.yaml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(CONFIG.format(limit=limit))
    work = tmp_path / "work"
    work.mkdir()
    for item_id, (item_type, status) in items.items():
        who = "Mini" if status == "in_progress" else None
        (work / f"{item_id}.md").write_text(_item(item_id, item_type, status, who))
    return KanbanService(KanbanConfig.load(cfg), tmp_path)


def _move(svc: KanbanService, item_id: str, assignee: str = "DGX"):
    """Forced move to in_progress (workflow off, WIP ON)."""
    return svc.move_item(
        item_id,
        WorkItemStatus.IN_PROGRESS,
        commit=False,
        assignee=assignee,
        validate_workflow=False,
        skip_wip_check=False,
        actor="Mini",  # the holder, past #574's holder guard
    )


def _must_move(svc: KanbanService, item_id: str):
    try:
        return _move(svc, item_id)
    except ValueError as e:
        pytest.fail(f"move of {item_id} refused: {e}")


# ── Aggregate limit 2, exempt voyages ────────────────────────────────────


class TestExemptAggregate:
    @pytest.fixture
    def svc(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> KanbanService:
        """in_progress (limit 2): EXP-101, EXP-102 (full) + VOY-201, VOY-202 (exempt)."""
        monkeypatch.chdir(tmp_path)
        return _repo(tmp_path, "2", {
            "EXP-101": ("expedition", "in_progress"),
            "EXP-102": ("expedition", "in_progress"),
            "VOY-201": ("voyage", "in_progress"),
            "VOY-202": ("voyage", "in_progress"),
            "EXP-103": ("expedition", "ready"),
            "VOY-203": ("voyage", "ready"),
        })

    def test_config_guard(self, svc: KanbanService) -> None:
        """Guard: aggregate branch (no per-type limits), voyage exempt on the board."""
        col = next(c for c in svc.get_board().columns if c.id == "in_progress")
        assert col.wip_limit == 2
        assert col.type_wip_limits is None
        assert svc.config.get_board("development").wip_exempt_types == ["voyage"]

    # 1. exempt item, within the column
    def test_exempt_within_full_column_succeeds(self, svc: KanbanService) -> None:
        moved = _must_move(svc, "VOY-201")
        assert moved.status == WorkItemStatus.IN_PROGRESS
        assert moved.assignee == "DGX"

    # 2. non-exempt item, within the column
    def test_non_exempt_within_full_column_succeeds(self, svc: KanbanService) -> None:
        moved = _must_move(svc, "EXP-101")
        assert moved.status == WorkItemStatus.IN_PROGRESS
        assert moved.assignee == "DGX"

    # 3. controls
    def test_new_non_exempt_refused_voyages_not_counted(self, svc: KanbanService) -> None:
        with pytest.raises(ValueError, match=r"WIP limit reached .*\(2/2\)"):
            _move(svc, "EXP-103")

    def test_new_exempt_moves_in(self, svc: KanbanService) -> None:
        assert _must_move(svc, "VOY-203").status == WorkItemStatus.IN_PROGRESS


class TestExemptOnlyColumn:
    def test_exempt_within_column_of_voyages_over_limit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Three voyages alone would overflow limit 2: an exempt re-move still passes.

        Either the skip or the filter alone lets this through; it fails only
        when both are gone (exempt items would then fill WIP slots).
        """
        monkeypatch.chdir(tmp_path)
        svc = _repo(tmp_path, "2", {
            "VOY-201": ("voyage", "in_progress"),
            "VOY-202": ("voyage", "in_progress"),
            "VOY-203": ("voyage", "in_progress"),
        })
        assert _must_move(svc, "VOY-201").status == WorkItemStatus.IN_PROGRESS


# ── Per-type limit, exempt voyages ───────────────────────────────────────


class TestExemptPerType:
    def test_exempt_within_column_over_its_type_limit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """{voyage: 1} with two voyages in the column: the exempt skip wins."""
        monkeypatch.chdir(tmp_path)
        svc = _repo(tmp_path, "{voyage: 1, expedition: 1}", {
            "VOY-201": ("voyage", "in_progress"),
            "VOY-202": ("voyage", "in_progress"),
        })
        col = next(c for c in svc.get_board().columns if c.id == "in_progress")
        assert col.type_wip_limits == {"voyage": 1, "expedition": 1}
        assert _must_move(svc, "VOY-201").status == WorkItemStatus.IN_PROGRESS
