"""Tests for priority_rank field, rank command, and get_ranked_items().

EXP-1034: Captain's priority queue ordering.
"""

import json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from yurtle_kanban.cli import main
from yurtle_kanban.config import KanbanConfig, PathConfig
from yurtle_kanban.models import WorkItem, WorkItemStatus, WorkItemType
from yurtle_kanban.service import KanbanService

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ranked_repo(tmp_path):
    """Create a repo with items, some ranked."""
    subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=tmp_path, capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=tmp_path, capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path, capture_output=True, check=True,
    )

    (tmp_path / ".kanban").mkdir(exist_ok=True)
    (tmp_path / "kanban-work" / "expeditions").mkdir(parents=True)

    config = KanbanConfig(
        theme="nautical",
        paths=PathConfig(
            root="kanban-work/",
            scan_paths=["kanban-work/expeditions/"],
        ),
    )
    config.save(tmp_path / ".kanban" / "config.yaml")

    items = [
        ("EXP-001", "Top Priority", "high", "backlog", 1, "Most urgent"),
        ("EXP-002", "Second Priority", "medium", "in_progress", 2, None),
        ("EXP-003", "Unranked High", "high", "backlog", None, None),
        ("EXP-004", "Unranked Low", "low", "backlog", None, None),
        ("EXP-005", "Done Item", "high", "done", None, None),
    ]

    for item_id, title, priority, status, rank, summary in items:
        slug = title.replace(" ", "-")
        path = (
            tmp_path / "kanban-work" / "expeditions"
            / f"{item_id}-{slug}.md"
        )
        lines = [
            "---",
            f"id: {item_id}",
            f'title: "{title}"',
            "type: expedition",
            f"status: {status}",
            "created: 2026-03-01",
            f"priority: {priority}",
            "tags: []",
            "depends_on: []",
        ]
        if rank is not None:
            lines.append(f"priority_rank: {rank}")
        if summary:
            lines.append(f'value_summary: "{summary}"')
        lines.extend(["---", "", f"# {title}", ""])
        path.write_text("\n".join(lines))

    # Initial commit so git operations work
    subprocess.run(
        ["git", "add", "."],
        cwd=tmp_path, capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=tmp_path, capture_output=True, check=True,
    )

    return tmp_path


@pytest.fixture
def ranked_service(ranked_repo, monkeypatch):
    """KanbanService backed by ranked_repo."""
    monkeypatch.chdir(ranked_repo)
    config = KanbanConfig.load(ranked_repo / ".kanban" / "config.yaml")
    return KanbanService(config, ranked_repo)


# ---------------------------------------------------------------------------
# WorkItem model — priority_rank and value_summary fields
# ---------------------------------------------------------------------------


class TestWorkItemModel:
    def test_priority_rank_in_to_dict(self):
        """priority_rank appears in to_dict output."""
        item = WorkItem(
            id="EXP-001", title="Test",
            status=WorkItemStatus.BACKLOG,
            item_type=WorkItemType.EXPEDITION,
            file_path=Path("/tmp/test.md"),
            priority_rank=3,
        )
        d = item.to_dict()
        assert d["priority_rank"] == 3

    def test_value_summary_in_to_dict(self):
        """value_summary appears in to_dict output."""
        item = WorkItem(
            id="EXP-001", title="Test",
            status=WorkItemStatus.BACKLOG,
            item_type=WorkItemType.EXPEDITION,
            file_path=Path("/tmp/test.md"),
            value_summary="Unblocks Paper 127",
        )
        d = item.to_dict()
        assert d["value_summary"] == "Unblocks Paper 127"

    def test_to_markdown_includes_rank(self):
        """to_markdown includes priority_rank field."""
        item = WorkItem(
            id="EXP-001", title="Test",
            status=WorkItemStatus.BACKLOG,
            item_type=WorkItemType.EXPEDITION,
            file_path=Path("/tmp/test.md"),
            priority_rank=2,
        )
        md = item.to_markdown()
        assert "priority_rank: 2" in md

    def test_to_markdown_includes_value_summary(self):
        """to_markdown includes value_summary field."""
        item = WorkItem(
            id="EXP-001", title="Test",
            status=WorkItemStatus.BACKLOG,
            item_type=WorkItemType.EXPEDITION,
            file_path=Path("/tmp/test.md"),
            value_summary="Important work",
        )
        md = item.to_markdown()
        assert 'value_summary: "Important work"' in md

    def test_to_markdown_escapes_quotes_in_summary(self):
        """Embedded quotes in value_summary are escaped."""
        item = WorkItem(
            id="EXP-001", title="Test",
            status=WorkItemStatus.BACKLOG,
            item_type=WorkItemType.EXPEDITION,
            file_path=Path("/tmp/test.md"),
            value_summary='Unblocks "Paper" 127',
        )
        md = item.to_markdown()
        assert r'value_summary: "Unblocks \"Paper\" 127"' in md


# ---------------------------------------------------------------------------
# KanbanService — parsing, rank_item, get_ranked_items
# ---------------------------------------------------------------------------


class TestServiceParsing:
    def test_parse_priority_rank(self, ranked_service):
        """priority_rank parsed from frontmatter."""
        item = ranked_service.get_item("EXP-001")
        assert item is not None
        assert item.priority_rank == 1

    def test_parse_value_summary(self, ranked_service):
        """value_summary parsed from frontmatter."""
        item = ranked_service.get_item("EXP-001")
        assert item is not None
        assert item.value_summary == "Most urgent"

    def test_parse_unranked_item(self, ranked_service):
        """Items without priority_rank have None."""
        item = ranked_service.get_item("EXP-003")
        assert item is not None
        assert item.priority_rank is None
        assert item.value_summary is None


class TestRankItem:
    def test_rank_sets_priority_rank(self, ranked_service):
        """rank_item sets priority_rank on item."""
        item = ranked_service.rank_item("EXP-003", 5, commit=False)
        assert item.priority_rank == 5

    def test_rank_writes_to_frontmatter(self, ranked_service):
        """rank_item persists priority_rank in file frontmatter."""
        ranked_service.rank_item("EXP-003", 3, commit=False)

        content = ranked_service.get_item("EXP-003").file_path.read_text()
        assert "priority_rank: 3" in content

    def test_rank_with_summary(self, ranked_service):
        """rank_item sets value_summary."""
        item = ranked_service.rank_item(
            "EXP-003", 4,
            value_summary="Unblocks training",
            commit=False,
        )
        assert item.value_summary == "Unblocks training"

        content = item.file_path.read_text()
        assert 'value_summary: "Unblocks training"' in content

    def test_rank_rejects_zero(self, ranked_service):
        """rank_item rejects rank < 1."""
        with pytest.raises(ValueError, match="Rank must be >= 1"):
            ranked_service.rank_item("EXP-003", 0, commit=False)

    def test_rank_rejects_negative(self, ranked_service):
        """rank_item rejects negative rank."""
        with pytest.raises(ValueError, match="Rank must be >= 1"):
            ranked_service.rank_item("EXP-003", -1, commit=False)

    def test_rank_updates_existing(self, ranked_service):
        """rank_item updates an already-ranked item."""
        item = ranked_service.rank_item("EXP-001", 10, commit=False)
        assert item.priority_rank == 10

        content = item.file_path.read_text()
        assert "priority_rank: 10" in content
        # Old rank should not appear
        assert content.count("priority_rank:") == 1


class TestGetRankedItems:
    def test_ranked_items_first(self, ranked_service):
        """Ranked items appear before unranked."""
        items = ranked_service.get_ranked_items()

        ids = [i.id for i in items]
        # Ranked (EXP-001 rank=1, EXP-002 rank=2) before unranked
        assert ids.index("EXP-001") < ids.index("EXP-003")
        assert ids.index("EXP-002") < ids.index("EXP-003")

    def test_ranked_sorted_ascending(self, ranked_service):
        """Ranked items sorted by rank ascending (1 first)."""
        items = ranked_service.get_ranked_items()

        ids = [i.id for i in items]
        assert ids.index("EXP-001") < ids.index("EXP-002")

    def test_excludes_done_items(self, ranked_service):
        """get_ranked_items excludes done items."""
        items = ranked_service.get_ranked_items()

        ids = [i.id for i in items]
        assert "EXP-005" not in ids


# ---------------------------------------------------------------------------
# _add_or_update_frontmatter_field
# ---------------------------------------------------------------------------


class TestAddOrUpdateFrontmatterField:
    def test_adds_new_field(self, ranked_service):
        """Adds a field that doesn't exist yet."""
        content = "---\nid: EXP-001\ntitle: Test\n---\n\n# Body\n"
        result = ranked_service._add_or_update_frontmatter_field(
            content, "priority_rank", "5"
        )
        assert "priority_rank: 5" in result
        assert result.count("---") == 2  # frontmatter preserved

    def test_updates_existing_field(self, ranked_service):
        """Updates a field that already exists."""
        content = "---\nid: EXP-001\npriority_rank: 3\n---\n\n# Body\n"
        result = ranked_service._add_or_update_frontmatter_field(
            content, "priority_rank", "7"
        )
        assert "priority_rank: 7" in result
        assert "priority_rank: 3" not in result
        assert result.count("priority_rank:") == 1


# ---------------------------------------------------------------------------
# CLI rank command
# ---------------------------------------------------------------------------


class TestRankCLI:
    def test_rank_command(self, ranked_repo, monkeypatch):
        """rank CLI command sets priority_rank."""
        monkeypatch.chdir(ranked_repo)
        runner = CliRunner()
        result = runner.invoke(main, ["rank", "EXP-003", "5"])

        assert result.exit_code == 0
        assert "Ranked EXP-003 as #5" in result.output

    def test_rank_command_with_summary(self, ranked_repo, monkeypatch):
        """rank CLI with --summary."""
        monkeypatch.chdir(ranked_repo)
        runner = CliRunner()
        result = runner.invoke(
            main, ["rank", "EXP-003", "2", "--summary", "Critical path"]
        )

        assert result.exit_code == 0
        assert "Ranked EXP-003 as #2" in result.output
        assert "Critical path" in result.output


# ---------------------------------------------------------------------------
# CLI roadmap --ranked
# ---------------------------------------------------------------------------


class TestRoadmapRanked:
    def test_roadmap_ranked_flag(self, ranked_repo, monkeypatch):
        """roadmap --ranked shows ranked items."""
        monkeypatch.chdir(ranked_repo)
        runner = CliRunner()
        result = runner.invoke(main, ["roadmap", "--ranked"])

        assert result.exit_code == 0
        assert "EXP-001" in result.output
        assert "Priority Queue" in result.output

    def test_roadmap_ranked_json(self, ranked_repo, monkeypatch):
        """roadmap --ranked --json includes priority_rank."""
        monkeypatch.chdir(ranked_repo)
        runner = CliRunner()
        result = runner.invoke(main, ["roadmap", "--ranked", "--json"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        ranked = [i for i in data if i.get("priority_rank") is not None]
        assert len(ranked) == 2
        # First item should be rank 1
        assert data[0]["priority_rank"] == 1
