"""#598: pin WIP self-exclusion for within-column moves and per-type limits.

Follow-up from the review of PR #595 (#586). `move_item` counts the target
column WITHOUT the moving item (`i.id != item.id`), in both the aggregate and
the per-type (`type_wip_limits`) branch. PR #595's tests only covered moves
from another column, where the item was never in the count. These pin:

1. a forced `in_progress -> in_progress` move (workflow validation off, WIP
   check still on; e.g. to change the assignee) in a column already at its WIP
   limit succeeds: the item does not count against itself (aggregate branch);
2. per-type branch: with `in_progress: {expedition: 1}` and one expedition in
   the column, moving a SECOND expedition in is refused, while re-moving the
   first (within the column) is allowed;
3. a per-type limit does not block another type: with the expedition slot
   full, a chore still moves in.

These pass on main after #595; they lock the behaviour in.
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


def _repo(tmp_path: Path, limit: str, items: dict[str, tuple[str, str, str | None]]) -> Path:
    cfg = tmp_path / ".kanban" / "config.yaml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(CONFIG.format(limit=limit))
    work = tmp_path / "work"
    work.mkdir()
    for item_id, (item_type, status, assignee) in items.items():
        (work / f"{item_id}.md").write_text(_item(item_id, item_type, status, assignee))
    return cfg


def _service(tmp_path: Path, cfg: Path) -> KanbanService:
    return KanbanService(KanbanConfig.load(cfg), tmp_path)


def _within_column_move(svc: KanbanService, item_id: str, assignee: str):
    """Forced in_progress -> in_progress (workflow off, WIP ON); refusal fails the test."""
    try:
        return svc.move_item(
            item_id,
            WorkItemStatus.IN_PROGRESS,
            commit=False,
            assignee=assignee,
            validate_workflow=False,
            skip_wip_check=False,
            actor="Mini",  # the holder, past #574's holder guard
        )
    except ValueError as e:
        pytest.fail(f"within-column move of {item_id} refused: {e}")


# ── 1. Aggregate limit: within-column move at a full column ──────────────


class TestAggregateWithinColumn:
    @pytest.fixture
    def full(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
        """in_progress limit 2, holding EXP-101 and EXP-102 (full)."""
        cfg = _repo(tmp_path, "2", {
            "EXP-101": ("expedition", "in_progress", "Mini"),
            "EXP-102": ("expedition", "in_progress", "M5"),
            "EXP-103": ("expedition", "ready", None),
        })
        monkeypatch.chdir(tmp_path)
        return tmp_path, cfg

    def test_reassign_within_full_column_succeeds(self, full: tuple[Path, Path]) -> None:
        tmp_path, cfg = full
        moved = _within_column_move(_service(tmp_path, cfg), "EXP-101", "DGX")
        assert moved.status == WorkItemStatus.IN_PROGRESS
        assert moved.assignee == "DGX"
        text = (tmp_path / "work" / "EXP-101.md").read_text()
        assert "assignee: DGX" in text

    def test_control_new_item_into_full_column_refused(
        self, full: tuple[Path, Path]
    ) -> None:
        tmp_path, cfg = full
        with pytest.raises(ValueError, match=r"WIP limit reached .*\(2/2\)"):
            _service(tmp_path, cfg).move_item(
                "EXP-103", WorkItemStatus.IN_PROGRESS, commit=False,
                assignee="Mini", validate_workflow=False,
            )


# ── 2. Per-type limit: second of a type refused, first re-moves ──────────


class TestPerTypeWithinColumn:
    @pytest.fixture
    def one_slot(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
        """in_progress: {expedition: 1, chore: 1}; EXP-201 fills the expedition slot."""
        cfg = _repo(tmp_path, "{expedition: 1, chore: 1}", {
            "EXP-201": ("expedition", "in_progress", "Mini"),
            "EXP-202": ("expedition", "ready", None),
            "CHORE-301": ("chore", "ready", None),
        })
        monkeypatch.chdir(tmp_path)
        return tmp_path, cfg

    def test_config_is_per_type(self, one_slot: tuple[Path, Path]) -> None:
        """Guard: the column really is on the per-type branch, with no aggregate limit."""
        tmp_path, cfg = one_slot
        board = _service(tmp_path, cfg).get_board()
        col = next(c for c in board.columns if c.id == "in_progress")
        assert col.type_wip_limits == {"expedition": 1, "chore": 1}
        assert col.wip_limit is None

    def test_second_expedition_refused(self, one_slot: tuple[Path, Path]) -> None:
        tmp_path, cfg = one_slot
        item = tmp_path / "work" / "EXP-202.md"
        before = item.read_bytes()
        with pytest.raises(ValueError, match=r"WIP limit reached for expeditions .*\(1/1\)"):
            _service(tmp_path, cfg).move_item(
                "EXP-202", WorkItemStatus.IN_PROGRESS, commit=False,
                assignee="M5", validate_workflow=False,
            )
        assert item.read_bytes() == before

    def test_first_expedition_re_moves_within_column(
        self, one_slot: tuple[Path, Path]
    ) -> None:
        tmp_path, cfg = one_slot
        moved = _within_column_move(_service(tmp_path, cfg), "EXP-201", "DGX")
        assert moved.status == WorkItemStatus.IN_PROGRESS
        assert moved.assignee == "DGX"

    # ── 3. another type is not blocked by the expedition limit ──

    def test_other_type_not_blocked(self, one_slot: tuple[Path, Path]) -> None:
        tmp_path, cfg = one_slot
        moved = _service(tmp_path, cfg).move_item(
            "CHORE-301", WorkItemStatus.IN_PROGRESS, commit=False,
            assignee="M5", validate_workflow=False,
        )
        assert moved.status == WorkItemStatus.IN_PROGRESS
