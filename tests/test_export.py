"""Tests for export functionality."""

import json
from pathlib import Path

import pytest

from yurtle_kanban.export import export_html, export_json, export_markdown
from yurtle_kanban.models import (
    Board,
    Column,
    WorkItem,
    WorkItemStatus,
    WorkItemType,
)


@pytest.fixture
def sample_board():
    """Create a sample board with items for testing."""
    columns = [
        Column(id="backlog", name="Backlog", order=1),
        Column(id="in_progress", name="In Progress", order=2, wip_limit=3),
        Column(id="done", name="Done", order=3),
    ]

    items = [
        WorkItem(
            id="FEAT-001",
            title="Add dark mode",
            item_type=WorkItemType.FEATURE,
            status=WorkItemStatus.IN_PROGRESS,
            file_path=Path("work/FEAT-001.md"),
            priority="high",
            assignee="dev-1",
            tags=["ui", "theme"],
        ),
        WorkItem(
            id="BUG-001",
            title="Fix login bug",
            item_type=WorkItemType.BUG,
            status=WorkItemStatus.BACKLOG,
            file_path=Path("work/BUG-001.md"),
            priority="critical",
        ),
        WorkItem(
            id="TASK-001",
            title="Update docs",
            item_type=WorkItemType.TASK,
            status=WorkItemStatus.DONE,
            file_path=Path("work/TASK-001.md"),
        ),
    ]

    return Board(
        id="test-board",
        name="Test Board",
        columns=columns,
        items=items,
    )


@pytest.fixture
def empty_board():
    """Create an empty board for testing."""
    columns = [
        Column(id="backlog", name="Backlog", order=1),
        Column(id="done", name="Done", order=2),
    ]
    return Board(
        id="empty-board",
        name="Empty Board",
        columns=columns,
        items=[],
    )


class TestExportMarkdown:
    """Tests for markdown export."""

    def test_export_markdown_contains_board_name(self, sample_board):
        """Exported markdown should contain the board name."""
        md = export_markdown(sample_board)
        assert "# Test Board" in md

    def test_export_markdown_contains_columns(self, sample_board):
        """Exported markdown should contain column headers."""
        md = export_markdown(sample_board)
        assert "Backlog" in md
        assert "In Progress" in md
        assert "Done" in md

    def test_export_markdown_contains_items(self, sample_board):
        """Exported markdown should contain work item IDs."""
        md = export_markdown(sample_board)
        assert "FEAT-001" in md
        assert "BUG-001" in md
        assert "TASK-001" in md

    def test_export_markdown_contains_statistics(self, sample_board):
        """Exported markdown should contain statistics section."""
        md = export_markdown(sample_board)
        assert "Statistics" in md
        assert "Total" in md

    def test_export_markdown_empty_board(self, empty_board):
        """Exporting empty board should not raise errors."""
        md = export_markdown(empty_board)
        assert "# Empty Board" in md
        assert "Backlog" in md

    def test_export_markdown_is_valid_table(self, sample_board):
        """Exported markdown should contain valid table separators."""
        md = export_markdown(sample_board)
        # Check for markdown table format
        assert "| --- |" in md or "|---|" in md


class TestExportHTML:
    """Tests for HTML export."""

    def test_export_html_is_valid_document(self, sample_board):
        """Exported HTML should be a valid document."""
        html = export_html(sample_board)
        assert "<!DOCTYPE html>" in html
        assert "<html" in html
        assert "</html>" in html

    def test_export_html_contains_board_name(self, sample_board):
        """Exported HTML should contain the board name."""
        html = export_html(sample_board)
        assert "Test Board" in html

    def test_export_html_contains_items(self, sample_board):
        """Exported HTML should contain work item content."""
        html = export_html(sample_board)
        assert "FEAT-001" in html
        assert "Add dark mode" in html
        assert "BUG-001" in html

    def test_export_html_contains_columns(self, sample_board):
        """Exported HTML should contain column names."""
        html = export_html(sample_board)
        assert "Backlog" in html
        assert "In Progress" in html
        assert "Done" in html

    def test_export_html_has_embedded_styles(self, sample_board):
        """Exported HTML should have embedded CSS."""
        html = export_html(sample_board)
        assert "<style>" in html
        assert "</style>" in html

    def test_export_html_priority_classes(self, sample_board):
        """Exported HTML should use priority CSS classes."""
        html = export_html(sample_board)
        assert "priority-high" in html
        assert "priority-critical" in html

    def test_export_html_wip_limit(self, sample_board):
        """Exported HTML should show WIP limit."""
        html = export_html(sample_board)
        # In Progress has wip_limit=3, 1 item
        assert "1/3" in html

    def test_export_html_empty_board(self, empty_board):
        """Exporting empty board should not raise errors."""
        html = export_html(empty_board)
        assert "Empty Board" in html
        assert "No items" in html


class TestExportJSON:
    """Tests for JSON export."""

    def test_export_json_is_valid(self, sample_board):
        """Exported JSON should be valid."""
        json_str = export_json(sample_board)
        data = json.loads(json_str)
        assert isinstance(data, dict)

    def test_export_json_contains_board_info(self, sample_board):
        """Exported JSON should contain board metadata."""
        json_str = export_json(sample_board)
        data = json.loads(json_str)

        assert "board" in data
        assert data["board"]["id"] == "test-board"
        assert data["board"]["name"] == "Test Board"

    def test_export_json_contains_columns(self, sample_board):
        """Exported JSON should contain columns."""
        json_str = export_json(sample_board)
        data = json.loads(json_str)

        assert "columns" in data
        assert len(data["columns"]) == 3

        column_ids = [c["id"] for c in data["columns"]]
        assert "backlog" in column_ids
        assert "in_progress" in column_ids
        assert "done" in column_ids

    def test_export_json_contains_items(self, sample_board):
        """Exported JSON should contain all items."""
        json_str = export_json(sample_board)
        data = json.loads(json_str)

        assert "items" in data
        assert len(data["items"]) == 3

        item_ids = [i["id"] for i in data["items"]]
        assert "FEAT-001" in item_ids
        assert "BUG-001" in item_ids
        assert "TASK-001" in item_ids

    def test_export_json_contains_statistics(self, sample_board):
        """Exported JSON should contain statistics."""
        json_str = export_json(sample_board)
        data = json.loads(json_str)

        assert "statistics" in data
        assert "total" in data["statistics"]
        assert data["statistics"]["total"] == 3

    def test_export_json_empty_board(self, empty_board):
        """Exporting empty board should work."""
        json_str = export_json(empty_board)
        data = json.loads(json_str)

        assert data["statistics"]["total"] == 0
        assert len(data["items"]) == 0


class TestExportIntegration:
    """Integration tests for export functionality."""

    def test_all_exports_same_item_count(self, sample_board):
        """All export formats should represent the same items."""
        md = export_markdown(sample_board)
        html = export_html(sample_board)
        json_str = export_json(sample_board)

        # Count occurrences of item IDs
        for item_id in ["FEAT-001", "BUG-001", "TASK-001"]:
            assert item_id in md
            assert item_id in html
            assert item_id in json_str

    def test_exports_handle_special_characters(self):
        """Exports should handle special characters in titles."""
        items = [
            WorkItem(
                id="FEAT-002",
                title='Test "quotes" & <brackets>',
                item_type=WorkItemType.FEATURE,
                status=WorkItemStatus.BACKLOG,
                file_path=Path("work/FEAT-002.md"),
            ),
        ]
        columns = [Column(id="backlog", name="Backlog", order=1)]
        board = Board(id="test", name="Test", columns=columns, items=items)

        # These should not raise errors
        md = export_markdown(board)
        html = export_html(board)
        json_str = export_json(board)

        assert "FEAT-002" in md
        assert "FEAT-002" in html
        # JSON should have the item
        data = json.loads(json_str)
        assert len(data["items"]) == 1
