"""Tests for roadmap and history commands (Issues #1 and #2)."""

import json
import subprocess
from datetime import date, timedelta

import pytest
from click.testing import CliRunner

from yurtle_kanban.cli import main
from yurtle_kanban.config import KanbanConfig, PathConfig


@pytest.fixture
def populated_repo(tmp_path):
    """Create a repo with several work items in different states."""
    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=tmp_path, capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path, capture_output=True, check=True,
    )

    # Init kanban with nautical theme
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path):
        pass  # just to ensure isolation

    # Set up dirs and config manually for precision
    (tmp_path / ".kanban").mkdir(exist_ok=True)
    (tmp_path / "kanban-work" / "expeditions").mkdir(parents=True, exist_ok=True)
    (tmp_path / "kanban-work" / "signals").mkdir(parents=True, exist_ok=True)

    config = KanbanConfig(
        theme="nautical",
        paths=PathConfig(
            root="kanban-work/",
            scan_paths=[
                "kanban-work/expeditions/",
                "kanban-work/signals/",
            ],
        ),
    )
    config.save(tmp_path / ".kanban" / "config.yaml")

    # Create items at various priorities and statuses
    items_data = [
        ("EXP-001", "Critical Bug Fix", "critical", "in_progress", "Mini"),
        ("EXP-002", "New Feature", "high", "ready", "M5"),
        ("EXP-003", "Refactor Module", "medium", "backlog", None),
        ("EXP-004", "Update Docs", "low", "done", "DGX"),
        ("EXP-005", "Another Done", "medium", "done", "Mini"),
        ("SIG-001", "Cool Idea", "medium", "backlog", None),
    ]

    for item_id, title, priority, status, assignee in items_data:
        prefix = item_id.split("-")[0]
        subdir = "expeditions" if prefix == "EXP" else "signals"
        slug = title.replace(" ", "-")
        path = tmp_path / "kanban-work" / subdir / f"{item_id}-{slug}.md"

        assignee_line = f"assignee: {assignee}" if assignee else "assignee:"
        path.write_text(f"""---
id: {item_id}
title: "{title}"
type: {"expedition" if prefix == "EXP" else "signal"}
status: {status}
created: 2026-02-15
priority: {priority}
{assignee_line}
tags: []
depends_on: []
---

# {title}
""")

    return tmp_path


class TestRoadmapCommand:
    """Issue #1: Prioritized roadmap view."""

    def test_roadmap_excludes_done_items(self, populated_repo, monkeypatch):
        """Roadmap should not show done items."""
        monkeypatch.chdir(populated_repo)
        runner = CliRunner()
        result = runner.invoke(main, ["roadmap"])

        assert result.exit_code == 0
        assert "EXP-001" in result.output  # in_progress
        assert "EXP-002" in result.output  # ready
        assert "EXP-003" in result.output  # backlog
        assert "EXP-004" not in result.output  # done — excluded
        assert "EXP-005" not in result.output  # done — excluded

    def test_roadmap_sorted_by_priority(self, populated_repo, monkeypatch):
        """Critical items should appear before low priority."""
        monkeypatch.chdir(populated_repo)
        runner = CliRunner()
        result = runner.invoke(main, ["roadmap", "--json"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        priorities = [item["priority"] for item in data]
        # Critical should be first, then high, then medium
        assert priorities[0] == "critical"
        assert priorities[1] == "high"

    def test_roadmap_by_type(self, populated_repo, monkeypatch):
        """--by-type should group items."""
        monkeypatch.chdir(populated_repo)
        runner = CliRunner()
        result = runner.invoke(main, ["roadmap", "--by-type"])

        assert result.exit_code == 0
        assert "Expedition" in result.output
        assert "Signal" in result.output

    def test_roadmap_type_filter(self, populated_repo, monkeypatch):
        """--type should filter to a single type."""
        monkeypatch.chdir(populated_repo)
        runner = CliRunner()
        result = runner.invoke(main, ["roadmap", "--type", "signal", "--json"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) == 1
        assert data[0]["id"] == "SIG-001"

    def test_roadmap_export_markdown(self, populated_repo, monkeypatch):
        """--export md should output markdown."""
        monkeypatch.chdir(populated_repo)
        runner = CliRunner()
        result = runner.invoke(main, ["roadmap", "--export", "md"])

        assert result.exit_code == 0
        assert "# Roadmap" in result.output
        assert "**EXP-001**" in result.output


class TestHistoryCommand:
    """Issue #2: Work history (done log)."""

    def test_history_shows_only_done(self, populated_repo, monkeypatch):
        """History should only show done items."""
        monkeypatch.chdir(populated_repo)
        runner = CliRunner()
        result = runner.invoke(main, ["history"])

        assert result.exit_code == 0
        assert "EXP-004" in result.output  # done
        assert "EXP-005" in result.output  # done
        assert "EXP-001" not in result.output  # in_progress
        assert "EXP-002" not in result.output  # ready

    def test_history_shows_total_count(self, populated_repo, monkeypatch):
        """Should show total completed count."""
        monkeypatch.chdir(populated_repo)
        runner = CliRunner()
        result = runner.invoke(main, ["history"])

        assert result.exit_code == 0
        assert "Total completed:" in result.output
        assert "2" in result.output

    def test_history_by_assignee(self, populated_repo, monkeypatch):
        """--by-assignee should group items."""
        monkeypatch.chdir(populated_repo)
        runner = CliRunner()
        result = runner.invoke(main, ["history", "--by-assignee"])

        assert result.exit_code == 0
        assert "@DGX" in result.output
        assert "@Mini" in result.output

    def test_history_json(self, populated_repo, monkeypatch):
        """--json should output JSON."""
        monkeypatch.chdir(populated_repo)
        runner = CliRunner()
        result = runner.invoke(main, ["history", "--json"])

        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) == 2
        # All should be done
        assert all(item["status"] == "done" for item in data)

    def test_history_since_filter(self, populated_repo, monkeypatch):
        """--since should filter by date."""
        monkeypatch.chdir(populated_repo)
        runner = CliRunner()
        # Use a far-future date so items never pass the filter
        # (items have updated=datetime.now() from __post_init__)
        future = (date.today() + timedelta(days=30)).isoformat()
        result = runner.invoke(main, ["history", "--since", future])

        assert result.exit_code == 0
        assert "No completed items" in result.output

    def test_history_empty_when_no_done(self, tmp_path, monkeypatch):
        """Should handle empty done list gracefully."""
        subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, capture_output=True, check=True)
        (tmp_path / ".kanban").mkdir()
        (tmp_path / "kanban-work").mkdir()

        config = KanbanConfig(
            theme="nautical",
            paths=PathConfig(root="kanban-work/", scan_paths=["kanban-work/"]),
        )
        config.save(tmp_path / ".kanban" / "config.yaml")

        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        result = runner.invoke(main, ["history"])

        assert result.exit_code == 0
        assert "No completed items" in result.output
