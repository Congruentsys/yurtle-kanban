"""Tests for work item models."""

from datetime import date
from pathlib import Path

import pytest

from yurtle_kanban.models import (
    Board,
    Column,
    Comment,
    WorkItem,
    WorkItemStatus,
    WorkItemType,
)


class TestWorkItem:
    """Tests for the WorkItem model."""

    def test_create_work_item(self):
        """Test creating a basic work item."""
        item = WorkItem(
            id="FEAT-001",
            title="Add dark mode",
            item_type=WorkItemType.FEATURE,
            status=WorkItemStatus.IN_PROGRESS,
            file_path=Path("work/FEAT-001.md"),
        )

        assert item.id == "FEAT-001"
        assert item.title == "Add dark mode"
        assert item.item_type == WorkItemType.FEATURE
        assert item.status == WorkItemStatus.IN_PROGRESS

    def test_work_item_with_optional_fields(self):
        """Test work item with all optional fields."""
        item = WorkItem(
            id="BUG-042",
            title="Fix login issue",
            item_type=WorkItemType.BUG,
            status=WorkItemStatus.READY,
            file_path=Path("work/bugs/BUG-042.md"),
            priority="high",
            assignee="../team/dev-1.md",
            created=date(2026, 1, 12),
            tags=["auth", "critical"],
            depends_on=["FEAT-001"],
        )

        assert item.priority == "high"
        assert item.assignee == "../team/dev-1.md"
        assert item.created == date(2026, 1, 12)
        assert "auth" in item.tags
        assert "FEAT-001" in item.depends_on

    def test_to_yurtle(self):
        """Test generating Yurtle block content."""
        item = WorkItem(
            id="FEAT-001",
            title="Test feature",
            item_type=WorkItemType.FEATURE,
            status=WorkItemStatus.IN_PROGRESS,
            file_path=Path("work/FEAT-001.md"),
            priority="high",
            tags=["ui", "ux"],
        )

        yurtle = item.to_yurtle()

        assert "@prefix kb:" in yurtle
        assert 'kb:id "FEAT-001"' in yurtle
        assert "kb:status kb:in_progress" in yurtle
        assert "kb:priority kb:high" in yurtle
        assert '"ui"' in yurtle
        assert '"ux"' in yurtle

    def test_to_dict(self):
        """Test converting to dictionary."""
        item = WorkItem(
            id="FEAT-001",
            title="Test feature",
            item_type=WorkItemType.FEATURE,
            status=WorkItemStatus.READY,
            file_path=Path("work/FEAT-001.md"),
            priority="high",
        )

        data = item.to_dict()

        assert data["id"] == "FEAT-001"
        assert data["title"] == "Test feature"
        assert data["item_type"] == "feature"
        assert data["status"] == "ready"
        assert data["priority"] == "high"

    def test_priority_score(self):
        """Test priority score calculation."""
        critical = WorkItem(
            id="FEAT-001",
            title="Critical item",
            item_type=WorkItemType.FEATURE,
            status=WorkItemStatus.READY,
            file_path=Path("work/FEAT-001.md"),
            priority="critical",
        )
        low = WorkItem(
            id="FEAT-002",
            title="Low item",
            item_type=WorkItemType.FEATURE,
            status=WorkItemStatus.READY,
            file_path=Path("work/FEAT-002.md"),
            priority="low",
        )

        assert critical.priority_score > low.priority_score

    def test_is_blocked(self):
        """Test blocked status detection."""
        blocked = WorkItem(
            id="FEAT-001",
            title="Blocked item",
            item_type=WorkItemType.FEATURE,
            status=WorkItemStatus.BLOCKED,
            file_path=Path("work/FEAT-001.md"),
        )
        active = WorkItem(
            id="FEAT-002",
            title="Active item",
            item_type=WorkItemType.FEATURE,
            status=WorkItemStatus.IN_PROGRESS,
            file_path=Path("work/FEAT-002.md"),
        )

        assert blocked.is_blocked is True
        assert active.is_blocked is False

    def test_to_markdown(self):
        """Test generating full markdown content."""
        item = WorkItem(
            id="FEAT-001",
            title="Test feature",
            item_type=WorkItemType.FEATURE,
            status=WorkItemStatus.READY,
            file_path=Path("work/FEAT-001.md"),
            priority="high",
            description="This is a test feature.",
        )

        md = item.to_markdown()

        assert "---" in md
        assert "id: FEAT-001" in md
        assert 'title: "Test feature"' in md
        assert "type: feature" in md
        assert "status: ready" in md
        assert "assignee: null" in md
        assert "# Test feature" in md

    def test_resolution_fields_default(self):
        """resolution and superseded_by default to None/empty."""
        item = WorkItem(
            id="FEAT-001",
            title="Test",
            item_type=WorkItemType.FEATURE,
            status=WorkItemStatus.READY,
            file_path=Path("test.md"),
        )
        assert item.resolution is None
        assert item.superseded_by == []

    def test_resolution_in_to_dict(self):
        """to_dict includes resolution and superseded_by."""
        item = WorkItem(
            id="FEAT-001",
            title="Test",
            item_type=WorkItemType.FEATURE,
            status=WorkItemStatus.DONE,
            file_path=Path("test.md"),
            resolution="superseded",
            superseded_by=["FEAT-002"],
        )
        data = item.to_dict()
        assert data["resolution"] == "superseded"
        assert data["superseded_by"] == ["FEAT-002"]

    def test_resolution_in_to_markdown(self):
        """to_markdown includes resolution when set."""
        item = WorkItem(
            id="FEAT-001",
            title="Closed item",
            item_type=WorkItemType.FEATURE,
            status=WorkItemStatus.DONE,
            file_path=Path("test.md"),
            resolution="wont_do",
        )
        md = item.to_markdown()
        assert "resolution: wont_do" in md

    def test_superseded_by_in_to_markdown(self):
        """to_markdown includes superseded_by when set."""
        item = WorkItem(
            id="FEAT-001",
            title="Replaced item",
            item_type=WorkItemType.FEATURE,
            status=WorkItemStatus.DONE,
            file_path=Path("test.md"),
            resolution="superseded",
            superseded_by=["FEAT-002", "FEAT-003"],
        )
        md = item.to_markdown()
        assert "superseded_by: [FEAT-002, FEAT-003]" in md

    def test_resolution_in_to_yurtle(self):
        """to_yurtle includes resolution triple when set."""
        item = WorkItem(
            id="FEAT-001",
            title="Test",
            item_type=WorkItemType.FEATURE,
            status=WorkItemStatus.DONE,
            file_path=Path("test.md"),
            resolution="completed",
        )
        yurtle = item.to_yurtle()
        assert 'kb:resolution "completed"' in yurtle

    def test_superseded_by_in_to_yurtle(self):
        """to_yurtle includes kb:supersededBy triple when set."""
        item = WorkItem(
            id="FEAT-001",
            title="Replaced",
            item_type=WorkItemType.FEATURE,
            status=WorkItemStatus.DONE,
            file_path=Path("test.md"),
            resolution="superseded",
            superseded_by=["FEAT-002"],
        )
        yurtle = item.to_yurtle()
        assert "kb:supersededBy" in yurtle
        assert "<FEAT-002>" in yurtle

    def test_no_resolution_fields_when_unset(self):
        """to_markdown/to_yurtle omit resolution fields when not set."""
        item = WorkItem(
            id="FEAT-001",
            title="Test",
            item_type=WorkItemType.FEATURE,
            status=WorkItemStatus.READY,
            file_path=Path("test.md"),
        )
        md = item.to_markdown()
        yurtle = item.to_yurtle()
        assert "resolution" not in md
        assert "superseded_by" not in md
        assert "resolution" not in yurtle
        assert "supersededBy" not in yurtle


class TestWorkItemStatus:
    """Tests for work item statuses."""

    def test_all_statuses_exist(self):
        """Test that all expected statuses are defined."""
        statuses = [s.value for s in WorkItemStatus]
        assert "backlog" in statuses
        assert "in_progress" in statuses
        assert "done" in statuses

    def test_from_string(self):
        """Test parsing status from string."""
        assert WorkItemStatus.from_string("backlog") == WorkItemStatus.BACKLOG
        assert WorkItemStatus.from_string("in_progress") == WorkItemStatus.IN_PROGRESS
        assert WorkItemStatus.from_string("in-progress") == WorkItemStatus.IN_PROGRESS

    def test_from_string_invalid(self):
        """Test parsing invalid status."""
        with pytest.raises(ValueError):
            WorkItemStatus.from_string("invalid")


class TestWorkItemType:
    """Tests for work item types."""

    def test_all_types_exist(self):
        """Test that all expected types are defined."""
        types = [t.value for t in WorkItemType]
        assert "feature" in types
        assert "bug" in types
        assert "epic" in types

    def test_from_string(self):
        """Test parsing type from string."""
        assert WorkItemType.from_string("feature") == WorkItemType.FEATURE
        assert WorkItemType.from_string("bug") == WorkItemType.BUG


class TestColumn:
    """Tests for Column model."""

    def test_wip_limit(self):
        """Test WIP limit checking."""
        col = Column(id="in_progress", name="In Progress", order=3, wip_limit=3)

        assert col.is_over_wip(2) is False
        assert col.is_over_wip(3) is False
        assert col.is_over_wip(4) is True

    def test_no_wip_limit(self):
        """Test column without WIP limit."""
        col = Column(id="backlog", name="Backlog", order=1)

        assert col.is_over_wip(100) is False


class TestComment:
    """Tests for Comment model."""

    def test_create_comment(self):
        """Test creating a comment."""
        comment = Comment(
            content="This looks good!",
            author="reviewer",
        )

        assert comment.content == "This looks good!"
        assert comment.author == "reviewer"
        assert comment.created_at is not None

    def test_to_dict(self):
        """Test converting comment to dict."""
        comment = Comment(content="Test", author="user")
        data = comment.to_dict()

        assert data["content"] == "Test"
        assert data["author"] == "user"
        assert "created_at" in data


class TestBoard:
    """Tests for Board model."""

    def test_get_items_by_status(self):
        """Test filtering items by status."""
        columns = [
            Column("backlog", "Backlog", 1),
            Column("ready", "Ready", 2),
            Column("done", "Done", 3),
        ]
        items = [
            WorkItem("FEAT-001", "Item 1", WorkItemType.FEATURE, WorkItemStatus.READY, Path(".")),
            WorkItem("FEAT-002", "Item 2", WorkItemType.FEATURE, WorkItemStatus.READY, Path(".")),
            WorkItem("FEAT-003", "Item 3", WorkItemType.FEATURE, WorkItemStatus.DONE, Path(".")),
        ]
        board = Board(id="test", name="Test Board", columns=columns, items=items)

        ready_items = board.get_items_by_status(WorkItemStatus.READY)
        assert len(ready_items) == 2

        done_items = board.get_items_by_status(WorkItemStatus.DONE)
        assert len(done_items) == 1

    def test_get_column_counts(self):
        """Test getting column counts."""
        columns = [
            Column("ready", "Ready", 1),
            Column("in_progress", "In Progress", 2),
        ]
        items = [
            WorkItem("FEAT-001", "Item 1", WorkItemType.FEATURE, WorkItemStatus.READY, Path(".")),
            WorkItem("FEAT-002", "Item 2", WorkItemType.FEATURE, WorkItemStatus.IN_PROGRESS, Path(".")),
            WorkItem("FEAT-003", "Item 3", WorkItemType.FEATURE, WorkItemStatus.IN_PROGRESS, Path(".")),
        ]
        board = Board(id="test", name="Test Board", columns=columns, items=items)

        counts = board.get_column_counts()
        assert counts["ready"] == 1
        assert counts["in_progress"] == 2

    def test_get_wip_violations(self):
        """Test detecting WIP violations."""
        columns = [
            Column("in_progress", "In Progress", 1, wip_limit=2),
        ]
        items = [
            WorkItem("FEAT-001", "Item 1", WorkItemType.FEATURE, WorkItemStatus.IN_PROGRESS, Path(".")),
            WorkItem("FEAT-002", "Item 2", WorkItemType.FEATURE, WorkItemStatus.IN_PROGRESS, Path(".")),
            WorkItem("FEAT-003", "Item 3", WorkItemType.FEATURE, WorkItemStatus.IN_PROGRESS, Path(".")),
        ]
        board = Board(id="test", name="Test Board", columns=columns, items=items)

        violations = board.get_wip_violations()
        assert len(violations) == 1
        assert violations[0][0].id == "in_progress"
        assert violations[0][1] == 3
