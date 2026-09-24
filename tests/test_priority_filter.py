"""Tests for --priority filter on the list command."""

import json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from yurtle_kanban.cli import main
from yurtle_kanban.config import KanbanConfig, PathConfig
from yurtle_kanban.models import WorkItemStatus
from yurtle_kanban.service import KanbanService


@pytest.fixture
def temp_repo(tmp_path):
    """Create a minimal git repo with kanban config and items at various priorities."""
    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, capture_output=True, check=True)

    (tmp_path / ".kanban").mkdir()
    (tmp_path / "kanban-work" / "expeditions").mkdir(parents=True)

    config = KanbanConfig(
        theme="nautical",
        paths=PathConfig(
            root="kanban-work/",
            scan_paths=["kanban-work/expeditions/"],
        ),
    )
    config.save(tmp_path / ".kanban" / "config.yaml")

    # Create items with different priorities
    exp_dir = tmp_path / "kanban-work" / "expeditions"
    (exp_dir / "EXP-001-Critical-Item.md").write_text(
        "---\nid: EXP-001\ntitle: Critical Item\nstatus: backlog\npriority: critical\n---\n"
    )
    (exp_dir / "EXP-002-High-Item.md").write_text(
        "---\nid: EXP-002\ntitle: High Item\nstatus: backlog\npriority: high\n---\n"
    )
    (exp_dir / "EXP-003-Medium-Item.md").write_text(
        "---\nid: EXP-003\ntitle: Medium Item\nstatus: backlog\npriority: medium\n---\n"
    )
    (exp_dir / "EXP-004-Low-Item.md").write_text(
        "---\nid: EXP-004\ntitle: Low Item\nstatus: in_progress\npriority: low\n---\n"
    )
    (exp_dir / "EXP-005-No-Priority.md").write_text(
        "---\nid: EXP-005\ntitle: No Priority\nstatus: backlog\n---\n"
    )

    return tmp_path


class TestPriorityFilterService:
    """Test priority filtering at the service layer."""

    def test_filter_single_priority(self, temp_repo):
        config = KanbanConfig.load(temp_repo / ".kanban" / "config.yaml")
        svc = KanbanService(config, temp_repo)

        items = svc.get_items(priority=["critical"])
        assert len(items) == 1
        assert items[0].id == "EXP-001"

    def test_filter_multiple_priorities(self, temp_repo):
        config = KanbanConfig.load(temp_repo / ".kanban" / "config.yaml")
        svc = KanbanService(config, temp_repo)

        items = svc.get_items(priority=["critical", "high"])
        assert len(items) == 2
        ids = {i.id for i in items}
        assert ids == {"EXP-001", "EXP-002"}

    def test_filter_no_priority_defaults_to_medium(self, temp_repo):
        """Items without explicit priority default to 'medium'."""
        config = KanbanConfig.load(temp_repo / ".kanban" / "config.yaml")
        svc = KanbanService(config, temp_repo)

        items = svc.get_items(priority=["medium"])
        ids = {i.id for i in items}
        # EXP-003 (explicit medium) and EXP-005 (no priority → medium)
        assert "EXP-003" in ids
        assert "EXP-005" in ids

    def test_filter_priority_with_status(self, temp_repo):
        """Priority filter combines with status filter."""
        config = KanbanConfig.load(temp_repo / ".kanban" / "config.yaml")
        svc = KanbanService(config, temp_repo)

        items = svc.get_items(
            priority=["low"],
            status=WorkItemStatus.IN_PROGRESS,
        )
        assert len(items) == 1
        assert items[0].id == "EXP-004"

    def test_no_priority_filter_returns_all(self, temp_repo):
        config = KanbanConfig.load(temp_repo / ".kanban" / "config.yaml")
        svc = KanbanService(config, temp_repo)

        items = svc.get_items(priority=None)
        assert len(items) == 5


class TestPriorityFilterCLI:
    """Test --priority flag via CLI runner."""

    def test_cli_priority_filter(self, temp_repo, monkeypatch):
        monkeypatch.chdir(temp_repo)
        runner = CliRunner()

        result = runner.invoke(main, ["list", "--priority", "critical", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) == 1
        assert data[0]["id"] == "EXP-001"

    def test_cli_priority_comma_separated(self, temp_repo, monkeypatch):
        monkeypatch.chdir(temp_repo)
        runner = CliRunner()

        result = runner.invoke(main, ["list", "--priority", "critical,high", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) == 2
        ids = {d["id"] for d in data}
        assert ids == {"EXP-001", "EXP-002"}

    def test_cli_priority_invalid_value(self, temp_repo, monkeypatch):
        monkeypatch.chdir(temp_repo)
        runner = CliRunner()

        result = runner.invoke(main, ["list", "--priority", "urgent"])
        assert result.exit_code != 0

    def test_cli_priority_short_flag(self, temp_repo, monkeypatch):
        monkeypatch.chdir(temp_repo)
        runner = CliRunner()

        result = runner.invoke(main, ["list", "-p", "high", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) == 1
        assert data[0]["id"] == "EXP-002"


def _md_files(root: Path) -> set[Path]:
    return {p for p in root.rglob("*.md") if ".git" not in p.parts}


def _new_item_files(root: Path, before: set[Path]) -> list[Path]:
    return sorted(_md_files(root) - before)


class TestCreatePriorityValidated:
    """Plain `create -p` and the MCP create/update tools validate priority
    against the same four values `list --priority` accepts, so no item can be
    written with a priority that can never be filtered (#106)."""

    VALID = ("critical", "high", "medium", "low")

    # --- CLI: plain create -------------------------------------------------

    def test_create_rejects_unknown_priority(self, temp_repo, monkeypatch):
        monkeypatch.chdir(temp_repo)
        before = _md_files(temp_repo)

        result = CliRunner().invoke(main, ["create", "feature", "probe", "-p", "urgent"])

        assert result.exit_code == 2, result.output  # click usage error
        assert "urgent" in result.output
        for p in self.VALID:
            assert p in result.output
        assert _new_item_files(temp_repo, before) == []

    def test_create_push_rejects_unknown_priority(self, temp_repo, monkeypatch):
        monkeypatch.chdir(temp_repo)
        before = _md_files(temp_repo)

        result = CliRunner().invoke(
            main, ["create", "feature", "probe", "-p", "urgent", "--push"]
        )

        assert result.exit_code == 2, result.output
        assert "urgent" in result.output
        for p in self.VALID:
            assert p in result.output
        assert _new_item_files(temp_repo, before) == []

    def test_create_priority_is_case_insensitive(self, temp_repo, monkeypatch):
        monkeypatch.chdir(temp_repo)
        before = _md_files(temp_repo)

        result = CliRunner().invoke(main, ["create", "feature", "probe", "-p", "HIGH"])

        assert result.exit_code == 0, result.output
        new = _new_item_files(temp_repo, before)
        assert len(new) == 1
        assert "\npriority: high\n" in new[0].read_text()

    # --- CLI: negative controls ---------------------------------------------

    def test_create_without_priority_defaults_to_medium(self, temp_repo, monkeypatch):
        monkeypatch.chdir(temp_repo)
        before = _md_files(temp_repo)

        result = CliRunner().invoke(main, ["create", "feature", "probe"])

        assert result.exit_code == 0, result.output
        new = _new_item_files(temp_repo, before)
        assert len(new) == 1
        assert "\npriority: medium\n" in new[0].read_text()

    @pytest.mark.parametrize("prio", VALID)
    def test_create_accepts_each_valid_priority(self, temp_repo, monkeypatch, prio):
        monkeypatch.chdir(temp_repo)
        before = _md_files(temp_repo)

        result = CliRunner().invoke(main, ["create", "feature", "probe", "-p", prio])

        assert result.exit_code == 0, result.output
        new = _new_item_files(temp_repo, before)
        assert len(new) == 1
        assert f"\npriority: {prio}\n" in new[0].read_text()

    def test_list_priority_filter_still_works(self, temp_repo, monkeypatch):
        monkeypatch.chdir(temp_repo)

        result = CliRunner().invoke(main, ["list", "--priority", "high", "--json"])

        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert [d["id"] for d in data] == ["EXP-002"]

    # --- MCP create / update tools ------------------------------------------

    def _mcp(self, repo: Path):
        mcp_server = pytest.importorskip("yurtle_kanban.mcp.server")
        return mcp_server.KanbanMCPServer(repo_root=repo)

    def test_mcp_create_rejects_unknown_priority(self, temp_repo, monkeypatch):
        monkeypatch.chdir(temp_repo)
        server = self._mcp(temp_repo)
        before = _md_files(temp_repo)

        result = server.handle_tool_call(
            "kanban_create_item",
            {"item_type": "feature", "title": "probe", "priority": "urgent"},
        )

        assert not result.get("success"), result
        assert "error" in result
        assert _new_item_files(temp_repo, before) == []

    def test_mcp_create_accepts_valid_priority(self, temp_repo, monkeypatch):
        monkeypatch.chdir(temp_repo)
        server = self._mcp(temp_repo)
        before = _md_files(temp_repo)

        result = server.handle_tool_call(
            "kanban_create_item",
            {"item_type": "feature", "title": "probe", "priority": "high"},
        )

        assert result.get("success"), result
        new = _new_item_files(temp_repo, before)
        assert len(new) == 1
        assert "\npriority: high\n" in new[0].read_text()

    def test_mcp_update_rejects_unknown_priority(self, temp_repo, monkeypatch):
        monkeypatch.chdir(temp_repo)
        server = self._mcp(temp_repo)
        path = temp_repo / "kanban-work" / "expeditions" / "EXP-002-High-Item.md"

        result = server.handle_tool_call(
            "kanban_update_item", {"item_id": "EXP-002", "priority": "urgent"}
        )

        assert not result.get("success"), result
        assert "error" in result
        assert "urgent" not in path.read_text()
        assert "\npriority: high\n" in path.read_text()

    def test_mcp_update_accepts_valid_priority(self, temp_repo, monkeypatch):
        monkeypatch.chdir(temp_repo)
        server = self._mcp(temp_repo)
        path = temp_repo / "kanban-work" / "expeditions" / "EXP-002-High-Item.md"

        result = server.handle_tool_call(
            "kanban_update_item", {"item_id": "EXP-002", "priority": "low"}
        )

        assert result.get("success"), result
        assert "\npriority: low\n" in path.read_text()


class TestMcpPriorityCaseInsensitive:
    """MCP create/update accept any case like the CLI does and write the
    lowercased value; unknown priorities are still rejected (#125)."""

    def _mcp(self, repo: Path):
        mcp_server = pytest.importorskip("yurtle_kanban.mcp.server")
        return mcp_server.KanbanMCPServer(repo_root=repo)

    @pytest.mark.parametrize("prio", ["High", "HIGH"])
    def test_mcp_create_mixed_case_priority_written_lowercase(
        self, temp_repo, monkeypatch, prio,
    ):
        monkeypatch.chdir(temp_repo)
        server = self._mcp(temp_repo)
        before = _md_files(temp_repo)

        result = server.handle_tool_call(
            "kanban_create_item",
            {"item_type": "feature", "title": "probe", "priority": prio},
        )

        assert result.get("success"), result
        new = _new_item_files(temp_repo, before)
        assert len(new) == 1
        assert "\npriority: high\n" in new[0].read_text()

    @pytest.mark.parametrize("prio", ["High", "HIGH"])
    def test_mcp_update_mixed_case_priority_written_lowercase(
        self, temp_repo, monkeypatch, prio,
    ):
        monkeypatch.chdir(temp_repo)
        server = self._mcp(temp_repo)
        path = temp_repo / "kanban-work" / "expeditions" / "EXP-004-Low-Item.md"

        result = server.handle_tool_call(
            "kanban_update_item", {"item_id": "EXP-004", "priority": prio}
        )

        assert result.get("success"), result
        assert "\npriority: high\n" in path.read_text()

    # --- negative controls --------------------------------------------------

    @pytest.mark.parametrize("prio", ["urgent", "Urgent", "P0"])
    def test_mcp_create_still_rejects_unknown_priority(self, temp_repo, monkeypatch, prio):
        monkeypatch.chdir(temp_repo)
        server = self._mcp(temp_repo)
        before = _md_files(temp_repo)

        result = server.handle_tool_call(
            "kanban_create_item",
            {"item_type": "feature", "title": "probe", "priority": prio},
        )

        assert not result.get("success"), result
        assert "error" in result
        assert _new_item_files(temp_repo, before) == []

    @pytest.mark.parametrize("prio", ["urgent", "Urgent", "P0"])
    def test_mcp_update_still_rejects_unknown_priority(self, temp_repo, monkeypatch, prio):
        monkeypatch.chdir(temp_repo)
        server = self._mcp(temp_repo)
        path = temp_repo / "kanban-work" / "expeditions" / "EXP-002-High-Item.md"
        before = path.read_text()

        result = server.handle_tool_call(
            "kanban_update_item", {"item_id": "EXP-002", "priority": prio}
        )

        assert not result.get("success"), result
        assert "error" in result
        assert path.read_text() == before


class TestTemplateCreatePriorityLowercase:
    """Template creates (the `_apply_priority` path) write the lowercased
    priority to frontmatter, with or without --push (#125)."""

    @pytest.fixture
    def hdd_repo(self, tmp_path, monkeypatch):
        subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, capture_output=True, check=True)
        subprocess.run(
            ["git", "config", "user.email", "t@t.com"],
            cwd=tmp_path, capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "T"], cwd=tmp_path, capture_output=True, check=True,
        )
        (tmp_path / ".kanban").mkdir()
        (tmp_path / "research" / "ideas").mkdir(parents=True)
        KanbanConfig(
            theme="hdd",
            paths=PathConfig(root="research/", scan_paths=["research/ideas/"]),
        ).save(tmp_path / ".kanban" / "config.yaml")
        monkeypatch.chdir(tmp_path)
        return tmp_path

    @pytest.mark.parametrize("extra", [[], ["--push"]])
    def test_idea_create_uppercase_priority_written_lowercase(self, hdd_repo, extra):
        before = _md_files(hdd_repo)

        result = CliRunner().invoke(
            main, ["idea", "create", "probe", "-p", "HIGH", *extra], catch_exceptions=False,
        )

        assert result.exit_code == 0, result.output
        new = _new_item_files(hdd_repo, before)
        assert len(new) == 1
        text = new[0].read_text()
        assert "\npriority: high\n" in text
        assert "HIGH" not in text
