"""#679: `Board.column_status` folds a column id as the service folds the map's
keys (#633), so a mapped column whose id isn't already folded keeps its status."""

from __future__ import annotations

import pytest

from yurtle_kanban.models import Board, WorkItemStatus

# the service keys the map by the folded theme name (#615, #633)
FOLDED_MAP = {"on_hold": WorkItemStatus.BLOCKED, "code_review": WorkItemStatus.REVIEW}


@pytest.mark.parametrize(
    ("column_id", "want"),
    [
        ("on-hold", WorkItemStatus.BLOCKED),
        ("On Hold", WorkItemStatus.BLOCKED),
        ("Code Review", WorkItemStatus.REVIEW),
        ("code-review", WorkItemStatus.REVIEW),
    ],
)
def test_unfolded_column_id_finds_its_folded_mapping(column_id, want):
    board = Board(id="b", name="b", columns=[], column_status_map=FOLDED_MAP)
    assert board.column_status(column_id) == want


def test_unmapped_unknown_column_has_no_status():
    board = Board(id="b", name="b", columns=[], column_status_map=FOLDED_MAP)
    assert board.column_status("parked") is None


def test_folded_column_places_its_items():
    """Placement (the drawn cards and the header count) follows `column_status`
    for a column id that isn't already folded (#692)."""
    from pathlib import Path

    from yurtle_kanban.models import Column, WorkItem, WorkItemType

    held = WorkItem(
        id="T-1", title="t", item_type=WorkItemType.TASK,
        status=WorkItemStatus.BLOCKED, file_path=Path("t.md"),
    )
    board = Board(
        id="b", name="b",
        columns=[Column(id="On Hold", name="On Hold", order=1)],
        items=[held], column_status_map=FOLDED_MAP,
    )
    assert board.get_column_items("On Hold") == [held]
    assert board.get_column_counts() == {"On Hold": 1}
