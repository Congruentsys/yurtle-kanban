"""Tests for KanbanService — ID allocation and item creation."""

import json
import re
import tempfile
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from yurtle_kanban.cli import main
from yurtle_kanban.config import KanbanConfig, PathConfig
from yurtle_kanban.models import WorkItemStatus, WorkItemType
from yurtle_kanban.service import KanbanService


@pytest.fixture
def temp_repo(tmp_path):
    """Create a minimal git repo with kanban config."""
    # Init git repo
    import subprocess
    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=tmp_path, capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path, capture_output=True, check=True,
    )
    # Create kanban dirs
    (tmp_path / ".kanban").mkdir()
    (tmp_path / "kanban-work" / "expeditions").mkdir(parents=True)
    (tmp_path / "kanban-work" / "voyages").mkdir(parents=True)
    (tmp_path / "kanban-work" / "signals").mkdir(parents=True)
    (tmp_path / "kanban-work" / "features").mkdir(parents=True)
    (tmp_path / "kanban-work" / "bugs").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def nautical_config(temp_repo):
    """Config with nautical theme and scan_paths."""
    config = KanbanConfig(
        theme="nautical",
        paths=PathConfig(
            root="kanban-work/",
            scan_paths=[
                "kanban-work/expeditions/",
                "kanban-work/voyages/",
                "kanban-work/signals/",
            ],
        ),
    )
    config.save(temp_repo / ".kanban" / "config.yaml")
    return config


@pytest.fixture
def software_config(temp_repo):
    """Config with software theme."""
    config = KanbanConfig(
        theme="software",
        paths=PathConfig(
            root="kanban-work/",
            scan_paths=[
                "kanban-work/features/",
                "kanban-work/bugs/",
            ],
        ),
    )
    config.save(temp_repo / ".kanban" / "config.yaml")
    return config


class TestNextIdIncrement:
    """Bug #8: next-id must increment for new prefixes."""

    def test_next_id_increments_without_files(self, temp_repo, nautical_config):
        """Repeated next-id calls should return incrementing numbers."""
        svc = KanbanService(nautical_config, temp_repo)

        result1 = svc.allocate_next_id("EXP", sync_remote=False, commit_allocation=True)
        assert result1["number"] == 1
        assert result1["id"] == "EXP-001"

        result2 = svc.allocate_next_id("EXP", sync_remote=False, commit_allocation=True)
        assert result2["number"] == 2
        assert result2["id"] == "EXP-002"

        result3 = svc.allocate_next_id("EXP", sync_remote=False, commit_allocation=True)
        assert result3["number"] == 3
        assert result3["id"] == "EXP-003"

    def test_next_id_increments_new_prefix(self, temp_repo, nautical_config):
        """A brand-new prefix with no files should still increment."""
        svc = KanbanService(nautical_config, temp_repo)

        r1 = svc.allocate_next_id("SIG", sync_remote=False, commit_allocation=True)
        r2 = svc.allocate_next_id("SIG", sync_remote=False, commit_allocation=True)

        assert r1["id"] == "SIG-001"
        assert r2["id"] == "SIG-002"

    def test_next_id_respects_existing_files(self, temp_repo, nautical_config):
        """If EXP-005 already exists on disk, next should be EXP-006."""
        # Create a file on disk
        exp_dir = temp_repo / "kanban-work" / "expeditions"
        (exp_dir / "EXP-005-Some-Title.md").write_text(
            "---\nid: EXP-005\ntitle: Existing\nstatus: backlog\n---\n"
        )

        svc = KanbanService(nautical_config, temp_repo)
        result = svc.allocate_next_id("EXP", sync_remote=False, commit_allocation=True)

        assert result["number"] == 6
        assert result["id"] == "EXP-006"

    def test_next_id_reads_allocations_file(self, temp_repo, nautical_config):
        """_get_next_id_number should read from _ID_ALLOCATIONS.json."""
        # Pre-populate allocations file
        lock_file = temp_repo / ".kanban" / "_ID_ALLOCATIONS.json"
        lock_file.write_text(json.dumps([
            {"id": "FEAT-001", "prefix": "FEAT", "number": 1},
            {"id": "FEAT-002", "prefix": "FEAT", "number": 2},
            {"id": "FEAT-003", "prefix": "FEAT", "number": 3},
        ]))

        svc = KanbanService(nautical_config, temp_repo)
        next_num = svc._get_next_id_number("FEAT")

        assert next_num == 4

    def test_next_id_uses_max_across_sources(self, temp_repo, nautical_config):
        """Should use max of allocations file AND filesystem."""
        # Allocations file says EXP-003
        lock_file = temp_repo / ".kanban" / "_ID_ALLOCATIONS.json"
        lock_file.write_text(json.dumps([
            {"id": "EXP-003", "prefix": "EXP", "number": 3},
        ]))

        # But filesystem has EXP-010
        exp_dir = temp_repo / "kanban-work" / "expeditions"
        (exp_dir / "EXP-010-Big-One.md").write_text(
            "---\nid: EXP-010\ntitle: Big\nstatus: backlog\n---\n"
        )

        svc = KanbanService(nautical_config, temp_repo)
        next_num = svc._get_next_id_number("EXP")

        assert next_num == 11  # max(3, 10) + 1


class TestCreateItemPlacement:
    """Bug #9: create should place files in type-specific directories."""

    def test_create_expedition_goes_to_expeditions_dir(self, temp_repo, nautical_config):
        """Expedition files should land in kanban-work/expeditions/."""
        svc = KanbanService(nautical_config, temp_repo)
        item = svc.create_item(WorkItemType.EXPEDITION, "Test Expedition")

        assert "kanban-work/expeditions/" in str(item.file_path)
        assert item.file_path.exists()
        assert item.id == "EXP-001"

    def test_create_signal_goes_to_signals_dir(self, temp_repo, nautical_config):
        """Signal files should land in kanban-work/signals/."""
        svc = KanbanService(nautical_config, temp_repo)
        item = svc.create_item(WorkItemType.SIGNAL, "New Idea")

        assert "kanban-work/signals/" in str(item.file_path)
        assert item.file_path.exists()

    def test_create_feature_goes_to_features_dir(self, temp_repo, software_config):
        """Feature files should land in kanban-work/features/."""
        svc = KanbanService(software_config, temp_repo)
        item = svc.create_item(WorkItemType.FEATURE, "Dark Mode")

        assert "kanban-work/features/" in str(item.file_path)
        assert item.file_path.exists()

    def test_create_includes_title_slug_in_filename(self, temp_repo, nautical_config):
        """Filename should include a title slug."""
        svc = KanbanService(nautical_config, temp_repo)
        item = svc.create_item(WorkItemType.EXPEDITION, "Fix The Bug")

        assert "Fix-The-Bug" in item.file_path.name

    def test_create_multiple_items_get_unique_ids(self, temp_repo, nautical_config):
        """Creating multiple items should yield unique IDs."""
        svc = KanbanService(nautical_config, temp_repo)

        item1 = svc.create_item(WorkItemType.SIGNAL, "First")
        item2 = svc.create_item(WorkItemType.SIGNAL, "Second")
        item3 = svc.create_item(WorkItemType.SIGNAL, "Third")

        assert item1.id == "SIG-001"
        assert item2.id == "SIG-002"
        assert item3.id == "SIG-003"
        assert item1.file_path != item2.file_path
        assert item2.file_path != item3.file_path


class TestCreateItemAndPush:
    """Test atomic create + push flow."""

    @pytest.fixture
    def repo_with_remote(self, tmp_path):
        """Create a git repo with a local 'remote' for push tests."""
        import subprocess

        # Create a bare repo to act as remote
        remote_path = tmp_path / "remote.git"
        remote_path.mkdir()
        subprocess.run(
            ["git", "init", "--bare", "-b", "main"],
            cwd=remote_path, capture_output=True, check=True,
        )

        # Create working repo
        work_path = tmp_path / "work"
        work_path.mkdir()
        subprocess.run(
            ["git", "init", "-b", "main"],
            cwd=work_path, capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=work_path, capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=work_path, capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "remote", "add", "origin", str(remote_path)],
            cwd=work_path, capture_output=True, check=True,
        )

        # Create kanban dirs and initial commit
        (work_path / ".kanban").mkdir()
        (work_path / "kanban-work" / "expeditions").mkdir(parents=True)
        (work_path / "kanban-work" / "signals").mkdir(parents=True)
        (work_path / ".gitkeep").write_text("")
        subprocess.run(["git", "add", "."], cwd=work_path, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=work_path, capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "push", "-u", "origin", "main"],
            cwd=work_path, capture_output=True, check=True,
        )

        return work_path

    def test_atomic_create_returns_success(self, repo_with_remote, nautical_config):
        """Atomic create should return success with item details."""
        nautical_config.save(repo_with_remote / ".kanban" / "config.yaml")
        svc = KanbanService(nautical_config, repo_with_remote)

        result = svc.create_item_and_push(
            item_type=WorkItemType.EXPEDITION,
            title="Test Atomic",
        )

        assert result["success"] is True
        assert result["id"] == "EXP-001"
        assert result["item"] is not None
        assert result["item"].file_path.exists()

    def test_atomic_create_writes_allocation(self, repo_with_remote, nautical_config):
        """Atomic create should update _ID_ALLOCATIONS.json."""
        nautical_config.save(repo_with_remote / ".kanban" / "config.yaml")
        svc = KanbanService(nautical_config, repo_with_remote)

        svc.create_item_and_push(
            item_type=WorkItemType.EXPEDITION,
            title="Test Alloc",
        )

        alloc_file = repo_with_remote / ".kanban" / "_ID_ALLOCATIONS.json"
        assert alloc_file.exists()
        allocations = json.loads(alloc_file.read_text())
        assert len(allocations) == 1
        assert allocations[0]["id"] == "EXP-001"

    def test_atomic_create_pushes_to_remote(self, repo_with_remote, nautical_config):
        """Atomic create should push to the remote."""
        import subprocess

        nautical_config.save(repo_with_remote / ".kanban" / "config.yaml")
        svc = KanbanService(nautical_config, repo_with_remote)

        svc.create_item_and_push(
            item_type=WorkItemType.SIGNAL,
            title="Pushed Signal",
        )

        # Check remote has the commit
        log = subprocess.run(
            ["git", "log", "--oneline", "origin/main"],
            cwd=repo_with_remote,
            capture_output=True,
            text=True,
        )
        assert "SIG-001" in log.stdout

    def test_atomic_create_sequential_ids(self, repo_with_remote, nautical_config):
        """Multiple atomic creates should get sequential IDs."""
        nautical_config.save(repo_with_remote / ".kanban" / "config.yaml")
        svc = KanbanService(nautical_config, repo_with_remote)

        r1 = svc.create_item_and_push(WorkItemType.EXPEDITION, "First")
        r2 = svc.create_item_and_push(WorkItemType.EXPEDITION, "Second")

        assert r1["id"] == "EXP-001"
        assert r2["id"] == "EXP-002"

    def test_atomic_create_places_file_correctly(self, repo_with_remote, nautical_config):
        """File should go in the theme-defined directory with slug."""
        nautical_config.save(repo_with_remote / ".kanban" / "config.yaml")
        svc = KanbanService(nautical_config, repo_with_remote)

        result = svc.create_item_and_push(
            WorkItemType.EXPEDITION,
            "Fix The Bug",
            priority="high",
        )

        item = result["item"]
        assert "kanban-work/expeditions/" in str(item.file_path)
        assert "Fix-The-Bug" in item.file_path.name

    def test_atomic_create_reports_pushed_true(self, repo_with_remote, nautical_config):
        """Result should indicate pushed=True when remote exists."""
        nautical_config.save(repo_with_remote / ".kanban" / "config.yaml")
        svc = KanbanService(nautical_config, repo_with_remote)

        result = svc.create_item_and_push(WorkItemType.EXPEDITION, "Test Push Flag")
        assert result["pushed"] is True

    def test_atomic_create_without_remote(self, temp_repo, nautical_config):
        """Without a remote, should succeed with pushed=False."""
        svc = KanbanService(nautical_config, temp_repo)

        result = svc.create_item_and_push(
            WorkItemType.EXPEDITION,
            "Local Only Item",
        )

        assert result["success"] is True
        assert result["pushed"] is False
        assert result["id"] == "EXP-001"
        assert result["item"].file_path.exists()
        assert "no remote" in result["message"].lower()

    def test_atomic_create_without_remote_commits(self, temp_repo, nautical_config):
        """Without a remote, the commit should still happen."""
        import subprocess

        svc = KanbanService(nautical_config, temp_repo)
        svc.create_item_and_push(WorkItemType.SIGNAL, "Local Signal")

        log = subprocess.run(
            ["git", "log", "--oneline"],
            cwd=temp_repo,
            capture_output=True,
            text=True,
        )
        assert "SIG-001" in log.stdout


class TestSlugify:
    """Test the title slugification."""

    def test_basic_slug(self):
        assert KanbanService._slugify("Fix The Bug") == "Fix-The-Bug"

    def test_special_chars_removed(self):
        assert KanbanService._slugify("Add feature: auth!") == "Add-feature-auth"

    def test_long_title_truncated(self):
        slug = KanbanService._slugify("A" * 100)
        assert len(slug) <= 50

    def test_empty_title(self):
        assert KanbanService._slugify("") == ""


class TestGetTypeDirectory:
    """Test type-to-directory resolution."""

    def test_theme_path_takes_priority(self, temp_repo, nautical_config):
        """Theme-defined path should be used first."""
        svc = KanbanService(nautical_config, temp_repo)
        path = svc._get_type_directory(WorkItemType.EXPEDITION)

        assert path == temp_repo / "kanban-work/expeditions/"

    def test_scan_path_fallback(self, temp_repo):
        """If no theme path, scan_paths keyword match is used."""
        config = KanbanConfig(
            theme="nautical",
            paths=PathConfig(
                root="kanban-work/",
                scan_paths=["kanban-work/voyages/"],
            ),
        )
        # Clear the theme path for voyages to test scan_path fallback
        svc = KanbanService(config, temp_repo)
        # Even without explicit path in theme, scan_paths should match "voyages"
        path = svc._get_type_directory(WorkItemType.VOYAGE)
        assert "voyages" in str(path)

    def test_root_fallback(self, temp_repo):
        """If nothing matches, fall back under the root, in the type's own folder (#113)."""
        config = KanbanConfig(
            theme="nonexistent",  # No theme loaded → no theme paths
            paths=PathConfig(root="work/", scan_paths=[]),
        )
        svc = KanbanService(config, temp_repo)
        path = svc._get_type_directory(WorkItemType.FEATURE)

        assert path == temp_repo / "work" / "features"


class TestForceAuditTrail:
    """Test that forced moves leave an audit trail in the status history."""

    def test_forced_move_records_audit_triple(self, temp_repo, nautical_config):
        """Forced moves should include kb:forcedMove in status history."""
        svc = KanbanService(nautical_config, temp_repo)
        item = svc.create_item(WorkItemType.EXPEDITION, "Test Force Audit")

        # Move to in_progress with force (skipping validation)
        svc.move_item(
            item.id,
            WorkItemStatus.IN_PROGRESS,
            commit=False,
            skip_wip_check=True,
            validate_workflow=False,
        )

        content = item.file_path.read_text()
        assert 'kb:forcedMove "true"' in content

    def test_normal_move_no_forced_triple(self, temp_repo, nautical_config):
        """Normal moves should NOT include kb:forcedMove."""
        svc = KanbanService(nautical_config, temp_repo)
        item = svc.create_item(
            WorkItemType.EXPEDITION, "Test Normal Move",
            assignee="Agent-A",
            description="Has a description for rules."
        )

        # backlog → ready is a valid default transition
        svc.move_item(
            item.id,
            WorkItemStatus.READY,
            commit=False,
        )

        content = item.file_path.read_text()
        assert "forcedMove" not in content

    def test_forced_move_only_skip_wip_not_marked_forced(self, temp_repo, nautical_config):
        """skip_wip_check=True with validate_workflow=True should NOT be marked forced."""
        svc = KanbanService(nautical_config, temp_repo)
        item = svc.create_item(
            WorkItemType.EXPEDITION, "Test WIP-only skip",
            assignee="Agent-A",
            description="Has description."
        )

        # backlog → ready is valid, skip WIP but keep workflow validation
        svc.move_item(
            item.id,
            WorkItemStatus.READY,
            commit=False,
            skip_wip_check=True,
            validate_workflow=True,
        )

        content = item.file_path.read_text()
        assert "forcedMove" not in content

    def test_status_history_includes_forced_flag(self, temp_repo, nautical_config):
        """get_status_history() should parse forced flag from TTL entries."""
        svc = KanbanService(nautical_config, temp_repo)
        item = svc.create_item(
            WorkItemType.EXPEDITION, "Test History Forced",
            assignee="Agent-A",
            description="Has description."
        )

        # Normal move: backlog → ready
        svc.move_item(item.id, WorkItemStatus.READY, commit=False)

        # Forced move: ready → in_progress
        svc.move_item(
            item.id,
            WorkItemStatus.IN_PROGRESS,
            commit=False,
            skip_wip_check=True,
            validate_workflow=False,
        )

        history = svc.get_status_history(item.id)
        assert len(history) == 2

        # First entry (normal) should not be forced
        assert history[0]["forced"] is False
        # Second entry (forced) should be forced
        assert history[1]["forced"] is True

    def test_resolution_fields_parsed_from_file(self, temp_repo, nautical_config):
        """Service should parse resolution and superseded_by from frontmatter."""
        exp_dir = temp_repo / "kanban-work" / "expeditions"
        (exp_dir / "EXP-001-Closed-Item.md").write_text(
            "---\n"
            "id: EXP-001\n"
            "title: Closed Item\n"
            "type: expedition\n"
            "status: done\n"
            "resolution: superseded\n"
            "superseded_by: [EXP-002, EXP-003]\n"
            "depends_on: []\n"
            "---\n\n# Closed Item\n"
        )

        svc = KanbanService(nautical_config, temp_repo)
        svc.scan()
        item = svc.get_item("EXP-001")

        assert item is not None
        assert item.resolution == "superseded"
        assert item.superseded_by == ["EXP-002", "EXP-003"]


class TestForceCliIntegration:
    """Test that --force flag wires correctly through the CLI layer."""

    def test_force_flag_skips_workflow_and_wip(self, temp_repo, nautical_config, monkeypatch):
        """CLI --force should pass skip_wip_check=True, validate_workflow=False."""
        runner = CliRunner()

        # Create an item first
        svc = KanbanService(nautical_config, temp_repo)
        item = svc.create_item(
            WorkItemType.EXPEDITION, "CLI Force Test",
            assignee="Agent-A",
            description="Has description.",
        )

        # CLI needs to run from repo root so get_service() finds .kanban/config.yaml
        monkeypatch.chdir(temp_repo)

        # Move via CLI with --force
        result = runner.invoke(
            main,
            ["move", item.id, "ready", "--force", "--no-commit"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert "Moved" in result.output

        # Verify the forced move was recorded in the file
        content = item.file_path.read_text()
        assert 'kb:forcedMove "true"' in content


class TestShowJson:
    """Test show --json CLI output."""

    def test_show_json_outputs_item_dict(self, temp_repo, nautical_config, monkeypatch):
        """show --json should output valid JSON with item fields."""
        runner = CliRunner()
        svc = KanbanService(nautical_config, temp_repo)
        item = svc.create_item(WorkItemType.EXPEDITION, "JSON Test")
        monkeypatch.chdir(temp_repo)

        result = runner.invoke(main, ["show", item.id, "--json"], catch_exceptions=False)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["id"] == item.id
        assert data["title"] == "JSON Test"
        assert data["status"] == "backlog"

    def test_show_json_not_found(self, temp_repo, nautical_config, monkeypatch):
        """show --json with bad ID should output JSON error and exit 1."""
        runner = CliRunner()
        monkeypatch.chdir(temp_repo)

        result = runner.invoke(main, ["show", "EXP-999", "--json"], catch_exceptions=False)
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert "error" in data

    def test_show_without_json_still_works(self, temp_repo, nautical_config, monkeypatch):
        """show without --json should still render Rich output."""
        runner = CliRunner()
        svc = KanbanService(nautical_config, temp_repo)
        item = svc.create_item(WorkItemType.EXPEDITION, "Rich Test")
        monkeypatch.chdir(temp_repo)

        result = runner.invoke(main, ["show", item.id], catch_exceptions=False)
        assert result.exit_code == 0
        assert "Rich Test" in result.output


class TestValidateJson:
    """Test validate --json CLI output."""

    def test_validate_json_clean(self, temp_repo, nautical_config, monkeypatch):
        """validate --json with no issues should return valid=true."""
        runner = CliRunner()
        svc = KanbanService(nautical_config, temp_repo)
        svc.create_item(WorkItemType.EXPEDITION, "Valid Item")
        monkeypatch.chdir(temp_repo)

        result = runner.invoke(main, ["validate", "--json"], catch_exceptions=False)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["valid"] is True
        assert data["issues"] == []
        assert data["items_checked"] >= 1

    def test_validate_json_filename_mismatch(self, temp_repo, nautical_config, monkeypatch):
        """validate --json should report filename mismatches."""
        runner = CliRunner()
        exp_dir = temp_repo / "kanban-work" / "expeditions"

        # File name doesn't match ID in frontmatter
        (exp_dir / "WRONG-NAME.md").write_text(
            "---\nid: EXP-002\ntitle: Mismatched\ntype: expedition\nstatus: backlog\n---\n"
        )
        monkeypatch.chdir(temp_repo)

        result = runner.invoke(main, ["validate", "--json"], catch_exceptions=False)
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert data["valid"] is False
        assert any(i["type"] == "filename_mismatch" for i in data["issues"])

    def test_validate_without_json_still_works(self, temp_repo, nautical_config, monkeypatch):
        """validate without --json should still render Rich output."""
        runner = CliRunner()
        svc = KanbanService(nautical_config, temp_repo)
        svc.create_item(WorkItemType.EXPEDITION, "Valid Item")
        monkeypatch.chdir(temp_repo)

        result = runner.invoke(main, ["validate"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "All work items valid" in result.output


class TestWorkItemGraph:
    """Tests for WorkItem.graph populated from fenced blocks."""

    def test_graph_populated_on_create(self, temp_repo, nautical_config):
        """create_item() should populate graph immediately."""
        svc = KanbanService(nautical_config, temp_repo)
        item = svc.create_item(WorkItemType.EXPEDITION, "Test Graph")
        assert item.graph is not None
        assert len(item.graph) > 0

    def test_graph_populated_from_yaml_frontmatter(self, temp_repo, nautical_config):
        """WorkItem.graph should contain triples from YAML frontmatter after scan."""
        svc = KanbanService(nautical_config, temp_repo)
        svc.create_item(WorkItemType.EXPEDITION, "Test Graph")
        svc.scan()
        reloaded = svc.get_item("EXP-001")
        assert reloaded is not None
        assert reloaded.graph is not None
        assert len(reloaded.graph) > 0

    def test_graph_includes_fenced_blocks(self, temp_repo, nautical_config):
        """WorkItem.graph should include triples from fenced turtle blocks."""
        from rdflib import Namespace

        svc = KanbanService(nautical_config, temp_repo)
        item = svc.create_item(WorkItemType.EXPEDITION, "Graph Blocks")

        # Add a fenced turtle block to the file
        content = item.file_path.read_text()
        block = '''
```turtle
@prefix test: <https://test.dev/> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

<#finding> a test:Discovery ;
    rdfs:label "Important finding" .
```
'''
        content += block
        item.file_path.write_text(content)

        # Force re-scan to pick up file changes
        svc.scan()
        reloaded = svc.get_item(item.id)
        assert reloaded is not None
        assert reloaded.graph is not None

        # Query for the fenced block triple
        from rdflib.namespace import RDFS
        labels = list(reloaded.graph.objects(predicate=RDFS.label))
        assert any("Important finding" in str(label) for label in labels)

    def test_get_knowledge_triples(self, temp_repo, nautical_config):
        """WorkItem.get_knowledge_triples() should query the graph."""
        from rdflib import Namespace

        svc = KanbanService(nautical_config, temp_repo)
        item = svc.create_item(WorkItemType.EXPEDITION, "Knowledge Query")

        # Add HDD-style turtle block
        content = item.file_path.read_text()
        block = '''
```turtle
@prefix hyp: <https://nusy.dev/hypothesis/> .
@prefix paper: <https://nusy.dev/paper/> .
@prefix measure: <https://nusy.dev/measure/> .

<#H130.1> a hyp:Hypothesis ;
    hyp:paper paper:PAPER-130 ;
    hyp:measuredBy measure:M-007, measure:M-025 .
```
'''
        content += block
        item.file_path.write_text(content)

        # Force re-scan to pick up file changes
        svc.scan()
        reloaded = svc.get_item(item.id)
        hyp = Namespace("https://nusy.dev/hypothesis/")
        measures = reloaded.get_knowledge_triples(hyp.measuredBy)
        assert len(measures) == 2
        assert any("M-007" in m for m in measures)
        assert any("M-025" in m for m in measures)

    def test_graph_empty_for_yaml_only(self, temp_repo, nautical_config):
        """YAML-only files should still have a graph (from frontmatter conversion)."""
        svc = KanbanService(nautical_config, temp_repo)
        item = svc.create_item(WorkItemType.EXPEDITION, "YAML Only")
        # create_item now populates graph immediately
        assert item.graph is not None
        assert len(item.graph) >= 1

    def test_malformed_block_still_has_frontmatter_graph(self, temp_repo, nautical_config):
        """Malformed turtle block should not prevent frontmatter graph parsing."""
        svc = KanbanService(nautical_config, temp_repo)
        item = svc.create_item(WorkItemType.EXPEDITION, "Bad Block")

        # Append a malformed turtle block
        content = item.file_path.read_text()
        content += '\n```turtle\nNOT VALID TURTLE!!!\n```\n'
        item.file_path.write_text(content)

        svc.scan()
        reloaded = svc.get_item(item.id)
        assert reloaded is not None
        # Graph should still exist with frontmatter triples (malformed block skipped)
        assert reloaded.graph is not None
        assert len(reloaded.graph) >= 1


# ---------------------------------------------------------------------------
# Parent Turtle Block Auto-Update (EXP-1026)
# ---------------------------------------------------------------------------


@pytest.fixture
def hdd_repo(tmp_path):
    """Create a minimal git repo with HDD directory structure."""
    import subprocess

    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=tmp_path, capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path, capture_output=True, check=True,
    )
    (tmp_path / ".kanban").mkdir()
    (tmp_path / "research" / "ideas").mkdir(parents=True)
    (tmp_path / "research" / "literature").mkdir(parents=True)
    (tmp_path / "research" / "papers").mkdir(parents=True)
    (tmp_path / "research" / "hypotheses").mkdir(parents=True)
    (tmp_path / "research" / "experiments").mkdir(parents=True)
    (tmp_path / "research" / "measures").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def hdd_svc_config(hdd_repo):
    """HDD config for parent update tests."""
    config = KanbanConfig(
        theme="hdd",
        paths=PathConfig(
            root="research/",
            scan_paths=[
                "research/ideas/",
                "research/literature/",
                "research/papers/",
                "research/hypotheses/",
                "research/experiments/",
                "research/measures/",
            ],
        ),
    )
    config.save(hdd_repo / ".kanban" / "config.yaml")
    return config


def _paper_turtle_block() -> str:
    """Minimal paper turtle block for testing."""
    return (
        '@prefix paper: <https://nusy.dev/paper/> .\n'
        '@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n'
        '\n'
        '<#PAPER-130> a paper:Paper ;\n'
        '    rdfs:label "Brain Architecture" .\n'
    )


def _hypothesis_turtle_block() -> str:
    """Minimal hypothesis turtle block for testing."""
    return (
        '@prefix hyp: <https://nusy.dev/hypothesis/> .\n'
        '@prefix paper: <https://nusy.dev/paper/> .\n'
        '@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n'
        '\n'
        '<#H130.1> a hyp:Hypothesis ;\n'
        '    rdfs:label "Accuracy improves" ;\n'
        '    hyp:paper paper:PAPER-130 .\n'
    )


def _idea_turtle_block() -> str:
    """Minimal idea turtle block for testing."""
    return (
        '@prefix idea: <https://nusy.dev/idea/> .\n'
        '@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n'
        '\n'
        '<#IDEA-R-010> a idea:Idea ;\n'
        '    rdfs:label "Test Idea" .\n'
    )


class TestModifyTurtleBlock:
    """Tests for KanbanService._modify_turtle_block()."""

    def _svc(self, hdd_repo, hdd_svc_config):
        return KanbanService(hdd_svc_config, hdd_repo)

    def test_add_first_child(self, hdd_repo, hdd_svc_config):
        """Adding a new predicate should produce a block with the triple."""
        from rdflib import Namespace

        svc = self._svc(hdd_repo, hdd_svc_config)
        paper_ns = Namespace("https://nusy.dev/paper/")
        hyp_ns = Namespace("https://nusy.dev/hypothesis/")

        new_content, changed = svc._modify_turtle_block(
            _paper_turtle_block(),
            paper_ns["hasHypothesis"],
            hyp_ns["H130.1"],
        )
        assert changed is True
        assert "hasHypothesis" in new_content
        assert "H130.1" in new_content

    def test_add_second_child(self, hdd_repo, hdd_svc_config):
        """Adding a second child — both URIs should be present."""
        from rdflib import Namespace

        svc = self._svc(hdd_repo, hdd_svc_config)
        paper_ns = Namespace("https://nusy.dev/paper/")
        hyp_ns = Namespace("https://nusy.dev/hypothesis/")

        content1, _ = svc._modify_turtle_block(
            _paper_turtle_block(),
            paper_ns["hasHypothesis"],
            hyp_ns["H130.1"],
        )
        content2, changed = svc._modify_turtle_block(
            content1,
            paper_ns["hasHypothesis"],
            hyp_ns["H130.2"],
        )
        assert changed is True
        assert "H130.1" in content2
        assert "H130.2" in content2

    def test_idempotent(self, hdd_repo, hdd_svc_config):
        """Adding the same child twice should return changed=False."""
        from rdflib import Namespace

        svc = self._svc(hdd_repo, hdd_svc_config)
        paper_ns = Namespace("https://nusy.dev/paper/")
        hyp_ns = Namespace("https://nusy.dev/hypothesis/")

        content1, _ = svc._modify_turtle_block(
            _paper_turtle_block(),
            paper_ns["hasHypothesis"],
            hyp_ns["H130.1"],
        )
        _, changed = svc._modify_turtle_block(
            content1,
            paper_ns["hasHypothesis"],
            hyp_ns["H130.1"],
        )
        assert changed is False

    def test_existing_triples_preserved(self, hdd_repo, hdd_svc_config):
        """Original triples should survive modification."""
        from rdflib import Graph, Namespace

        svc = self._svc(hdd_repo, hdd_svc_config)
        paper_ns = Namespace("https://nusy.dev/paper/")
        hyp_ns = Namespace("https://nusy.dev/hypothesis/")

        new_content, _ = svc._modify_turtle_block(
            _paper_turtle_block(),
            paper_ns["hasHypothesis"],
            hyp_ns["H130.1"],
        )
        # Parse the result and verify original triples
        g = Graph()
        g.parse(data=new_content, format="turtle", publicID="urn:yurtle:block")
        from rdflib.namespace import RDFS
        labels = list(g.objects(predicate=RDFS.label))
        assert any("Brain Architecture" in str(label) for label in labels)

    def test_prefixes_bound(self, hdd_repo, hdd_svc_config):
        """Child prefix should appear in output prefix declarations."""
        from rdflib import Namespace

        svc = self._svc(hdd_repo, hdd_svc_config)
        paper_ns = Namespace("https://nusy.dev/paper/")
        hyp_ns = Namespace("https://nusy.dev/hypothesis/")

        new_content, _ = svc._modify_turtle_block(
            _paper_turtle_block(),
            paper_ns["hasHypothesis"],
            hyp_ns["H130.1"],
        )
        assert "@prefix hyp:" in new_content

    def test_empty_block(self, hdd_repo, hdd_svc_config):
        """Empty turtle content should return unchanged."""
        from rdflib import Namespace

        svc = self._svc(hdd_repo, hdd_svc_config)
        paper_ns = Namespace("https://nusy.dev/paper/")
        hyp_ns = Namespace("https://nusy.dev/hypothesis/")

        _, changed = svc._modify_turtle_block(
            "",
            paper_ns["hasHypothesis"],
            hyp_ns["H130.1"],
        )
        assert changed is False

    def test_no_base_in_output(self, hdd_repo, hdd_svc_config):
        """Output should not contain @base declaration."""
        from rdflib import Namespace

        svc = self._svc(hdd_repo, hdd_svc_config)
        paper_ns = Namespace("https://nusy.dev/paper/")
        hyp_ns = Namespace("https://nusy.dev/hypothesis/")

        new_content, _ = svc._modify_turtle_block(
            _paper_turtle_block(),
            paper_ns["hasHypothesis"],
            hyp_ns["H130.1"],
        )
        assert "@base" not in new_content

    def test_hypothesis_gets_experiment(self, hdd_repo, hdd_svc_config):
        """Adding hasExperiment to a hypothesis block."""
        from rdflib import Namespace

        svc = self._svc(hdd_repo, hdd_svc_config)
        hyp_ns = Namespace("https://nusy.dev/hypothesis/")
        expr_ns = Namespace("https://nusy.dev/experiment/")

        new_content, changed = svc._modify_turtle_block(
            _hypothesis_turtle_block(),
            hyp_ns["hasExperiment"],
            expr_ns["EXPR-130"],
        )
        assert changed is True
        assert "hasExperiment" in new_content
        assert "EXPR-130" in new_content
        # Original paper reference should still be there
        assert "PAPER-130" in new_content


class TestUpdateParentTurtleBlock:
    """Tests for KanbanService.update_parent_turtle_block()."""

    def _create_paper_file(self, hdd_repo):
        """Create a PAPER-130 file with a turtle block."""
        papers_dir = hdd_repo / "research" / "papers"
        paper_file = papers_dir / "PAPER-130-Brain-Architecture.md"
        paper_file.write_text(
            "---\n"
            "id: PAPER-130\n"
            'title: "Brain Architecture"\n'
            "type: paper\n"
            "status: draft\n"
            "created: 2026-01-01\n"
            "tags: []\n"
            "---\n"
            "\n"
            "# PAPER-130: Brain Architecture\n"
            "\n"
            "```turtle\n"
            + _paper_turtle_block()
            + "```\n"
            "\n"
            "## Content\n"
        )
        return paper_file

    def test_nonexistent_parent(self, hdd_repo, hdd_svc_config):
        """Updating a non-existent parent returns False."""
        svc = KanbanService(hdd_svc_config, hdd_repo)
        result = svc.update_parent_turtle_block("PAPER-999", "hypothesis", "H999.1")
        assert result is False

    def test_parent_without_turtle_block(self, hdd_repo, hdd_svc_config):
        """Parent file without turtle block returns False."""
        papers_dir = hdd_repo / "research" / "papers"
        paper_file = papers_dir / "PAPER-130-No-Block.md"
        paper_file.write_text(
            "---\n"
            "id: PAPER-130\n"
            'title: "No Block"\n'
            "type: paper\n"
            "status: draft\n"
            "created: 2026-01-01\n"
            "tags: []\n"
            "---\n"
            "\n"
            "# PAPER-130: No Block\n"
            "\n"
            "No turtle block here.\n"
        )
        svc = KanbanService(hdd_svc_config, hdd_repo)
        svc.scan()
        result = svc.update_parent_turtle_block("PAPER-130", "hypothesis", "H130.1")
        assert result is False

    def test_hypothesis_updates_paper_file(self, hdd_repo, hdd_svc_config):
        """Creating a hypothesis should add paper:hasHypothesis to paper file."""
        self._create_paper_file(hdd_repo)
        svc = KanbanService(hdd_svc_config, hdd_repo)
        svc.scan()

        result = svc.update_parent_turtle_block("PAPER-130", "hypothesis", "H130.1")
        assert result is True

        content = (hdd_repo / "research" / "papers" / "PAPER-130-Brain-Architecture.md").read_text()
        assert "hasHypothesis" in content
        assert "H130.1" in content

    def test_idempotent_file_update(self, hdd_repo, hdd_svc_config):
        """Updating the same parent with same child twice returns False on second call."""
        self._create_paper_file(hdd_repo)
        svc = KanbanService(hdd_svc_config, hdd_repo)
        svc.scan()

        assert svc.update_parent_turtle_block("PAPER-130", "hypothesis", "H130.1") is True
        svc.scan()
        assert svc.update_parent_turtle_block("PAPER-130", "hypothesis", "H130.1") is False

    def test_markdown_content_preserved(self, hdd_repo, hdd_svc_config):
        """Markdown content outside the turtle block should be preserved."""
        self._create_paper_file(hdd_repo)
        svc = KanbanService(hdd_svc_config, hdd_repo)
        svc.scan()

        svc.update_parent_turtle_block("PAPER-130", "hypothesis", "H130.1")

        content = (hdd_repo / "research" / "papers" / "PAPER-130-Brain-Architecture.md").read_text()
        assert "# PAPER-130: Brain Architecture" in content
        assert "## Content" in content
        assert content.startswith("---\n")


# ---------------------------------------------------------------------------
# TestBuildExpectedGraph
# ---------------------------------------------------------------------------


class TestBuildExpectedGraph:
    """Tests for _build_expected_graph — frontmatter → rdflib Graph."""

    def test_idea_graph(self, hdd_repo, hdd_svc_config):
        """Idea frontmatter produces idea:Idea type + rdfs:label triples."""
        from rdflib import RDF, RDFS, Literal
        svc = KanbanService(hdd_svc_config, hdd_repo)
        fm = {"id": "IDEA-R-001", "title": "Test Idea", "type": "idea"}
        g = svc._build_expected_graph("idea", fm)
        assert len(g) == 2
        subjects = list(g.subjects())
        assert any("IDEA-R-001" in str(s) for s in subjects)
        assert any(str(o) == "Test Idea" for _, _, o in g.triples((None, RDFS.label, None)))

    def test_hypothesis_with_paper(self, hdd_repo, hdd_svc_config):
        """Hypothesis with paper field produces hyp:paper triple."""
        svc = KanbanService(hdd_svc_config, hdd_repo)
        fm = {"id": "H130.1", "title": "Test Hyp", "type": "hypothesis", "paper": "Paper130", "target": ">=85%"}
        g = svc._build_expected_graph("hypothesis", fm)
        # type + label + paper + target = 4 triples
        assert len(g) == 4
        assert any("PAPER-130" in str(o) for _, _, o in g)
        assert any(">=85%" in str(o) for _, _, o in g)

    def test_experiment_with_measures(self, hdd_repo, hdd_svc_config):
        """Experiment with measures list produces expr:measure triples."""
        svc = KanbanService(hdd_svc_config, hdd_repo)
        fm = {
            "id": "EXPR-103", "title": "Test Exp", "type": "experiment",
            "paper": "Paper103", "hypotheses": ["H103.1", "H103.2"],
            "measures": ["M-002", "M-013"],
        }
        g = svc._build_expected_graph("experiment", fm)
        # type + label + paper + hypothesis(first) + 2 measures = 6 triples
        assert len(g) == 6
        assert any("M-002" in str(o) for _, _, o in g)
        assert any("M-013" in str(o) for _, _, o in g)

    def test_secondary_hypothesis_alias(self, hdd_repo, hdd_svc_config):
        """secondary-hypothesis type is handled via _TYPE_ALIASES externally."""
        svc = KanbanService(hdd_svc_config, hdd_repo)
        fm = {"id": "H9.1", "title": "Accuracy Equivalence", "type": "secondary-hypothesis"}
        # Caller normalizes type; _build_expected_graph receives "hypothesis"
        g = svc._build_expected_graph("hypothesis", fm)
        assert len(g) == 2  # type + label
        assert any("Hypothesis" in str(o) for _, _, o in g)

    def test_minimal_frontmatter(self, hdd_repo, hdd_svc_config):
        """Frontmatter with only id (no title) produces just the type triple."""
        svc = KanbanService(hdd_svc_config, hdd_repo)
        fm = {"id": "IDEA-R-099", "type": "idea"}
        g = svc._build_expected_graph("idea", fm)
        assert len(g) == 1  # just the type triple

    def test_measure_graph(self, hdd_repo, hdd_svc_config):
        """Measure with unit and category produces measure triples."""
        from rdflib import Literal
        svc = KanbanService(hdd_svc_config, hdd_repo)
        fm = {"id": "M-007", "title": "Accuracy", "type": "measure", "unit": "percent", "category": "accuracy"}
        g = svc._build_expected_graph("measure", fm)
        # type + label + unit + category = 4 triples
        assert len(g) == 4
        assert any(str(o) == "percent" for _, _, o in g)
        assert any(str(o) == "accuracy" for _, _, o in g)

    def test_hypothesis_with_literature(self, hdd_repo, hdd_svc_config):
        """Hypothesis with literature field produces hyp:informedBy triples."""
        svc = KanbanService(hdd_svc_config, hdd_repo)
        fm = {"id": "H50.1", "title": "Lit Hyp", "type": "hypothesis", "literature": ["LIT-001", "LIT-002"]}
        g = svc._build_expected_graph("hypothesis", fm)
        # type + label + 2 informedBy = 4 triples
        assert len(g) == 4
        assert any("LIT-001" in str(o) for _, _, o in g)
        assert any("LIT-002" in str(o) for _, _, o in g)

    def test_literature_with_source_idea(self, hdd_repo, hdd_svc_config):
        """Literature with source_idea produces lit:explores triple."""
        svc = KanbanService(hdd_svc_config, hdd_repo)
        fm = {"id": "LIT-001", "title": "Survey", "type": "literature", "source_idea": "IDEA-R-001"}
        g = svc._build_expected_graph("literature", fm)
        # type + label + explores = 3 triples
        assert len(g) == 3
        assert any("IDEA-R-001" in str(o) for _, _, o in g)

    def test_paper_graph(self, hdd_repo, hdd_svc_config):
        """Paper frontmatter produces paper:Paper type + rdfs:label."""
        svc = KanbanService(hdd_svc_config, hdd_repo)
        fm = {"id": "PAPER-130", "title": "Brain Architecture", "type": "paper"}
        g = svc._build_expected_graph("paper", fm)
        assert len(g) == 2
        assert any("Paper" in str(o) for _, _, o in g)


# ---------------------------------------------------------------------------
# TestSerializeAsTurtleBlock
# ---------------------------------------------------------------------------


class TestSerializeAsTurtleBlock:
    """Tests for _serialize_as_turtle_block — rdflib Graph → fenced block."""

    def test_fenced_output(self, hdd_repo, hdd_svc_config):
        """Output is wrapped in ```turtle fences."""
        from rdflib import RDF, RDFS, Graph, Literal, Namespace, URIRef
        svc = KanbanService(hdd_svc_config, hdd_repo)
        g = Graph()
        idea_ns = Namespace("https://nusy.dev/idea/")
        subject = URIRef("urn:yurtle:block#IDEA-R-001")
        g.add((subject, RDF.type, idea_ns.Idea))
        g.add((subject, RDFS.label, Literal("Test")))
        block = svc._serialize_as_turtle_block(g)
        assert block.startswith("```turtle\n")
        assert block.endswith("\n```")

    def test_prefixes_bound(self, hdd_repo, hdd_svc_config):
        """HDD prefixes appear in serialized output."""
        from rdflib import RDF, Graph, Namespace, URIRef
        svc = KanbanService(hdd_svc_config, hdd_repo)
        g = Graph()
        idea_ns = Namespace("https://nusy.dev/idea/")
        subject = URIRef("urn:yurtle:block#IDEA-R-001")
        g.add((subject, RDF.type, idea_ns.Idea))
        block = svc._serialize_as_turtle_block(g)
        assert "@prefix idea:" in block


# ---------------------------------------------------------------------------
# TestBackfillTurtleBlocks
# ---------------------------------------------------------------------------


class TestBackfillTurtleBlocks:
    """Tests for backfill_turtle_blocks — graph-native backfill."""

    def _write_idea_file(self, repo, idea_id, title):
        """Create an idea file without a turtle block."""
        fp = repo / "research" / "ideas" / f"{idea_id}-test.md"
        fp.write_text(
            f"---\nid: {idea_id}\ntitle: \"{title}\"\ntype: idea\nstatus: captured\n"
            f"created: 2026-01-01\n---\n\n# {idea_id}: {title}\n\nContent here.\n"
        )
        return fp

    def _write_idea_file_with_block(self, repo, idea_id, title):
        """Create an idea file WITH a turtle block."""
        fp = repo / "research" / "ideas" / f"{idea_id}-test.md"
        fp.write_text(
            f"---\nid: {idea_id}\ntitle: \"{title}\"\ntype: idea\nstatus: captured\n"
            f"created: 2026-01-01\n---\n\n"
            f"```turtle\n"
            f"@prefix idea: <https://nusy.dev/idea/> .\n"
            f"@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n\n"
            f"<#{idea_id}> a idea:Idea ;\n"
            f'    rdfs:label "{title}" .\n'
            f"```\n\n# {idea_id}: {title}\n"
        )
        return fp

    def _write_expedition_file(self, repo, exp_id, title):
        """Create a non-HDD expedition file."""
        (repo / "kanban-work" / "expeditions").mkdir(parents=True, exist_ok=True)
        fp = repo / "kanban-work" / "expeditions" / f"{exp_id}-test.md"
        fp.write_text(
            f"---\nid: {exp_id}\ntitle: \"{title}\"\ntype: expedition\nstatus: backlog\n"
            f"created: 2026-01-01\n---\n\n# {exp_id}: {title}\n"
        )
        return fp

    def test_backfill_adds_block(self, hdd_repo, hdd_svc_config):
        """Idea file without turtle block gets one added."""
        self._write_idea_file(hdd_repo, "IDEA-R-001", "Test Idea")
        svc = KanbanService(hdd_svc_config, hdd_repo)
        svc.scan()

        results = svc.backfill_turtle_blocks(dry_run=False)

        backfilled = [r for r in results if r["action"] == "backfill"]
        assert len(backfilled) == 1
        assert backfilled[0]["id"] == "IDEA-R-001"
        assert backfilled[0]["triples_added"] > 0

        content = (hdd_repo / "research" / "ideas" / "IDEA-R-001-test.md").read_text()
        assert "```turtle" in content
        assert "idea:Idea" in content

    def test_skip_when_triples_exist(self, hdd_repo, hdd_svc_config):
        """File with existing turtle block that has all triples → up_to_date."""
        self._write_idea_file_with_block(hdd_repo, "IDEA-R-002", "Complete Idea")
        svc = KanbanService(hdd_svc_config, hdd_repo)
        svc.scan()

        results = svc.backfill_turtle_blocks(dry_run=False)

        up_to_date = [r for r in results if r["action"] == "up_to_date"]
        assert len(up_to_date) == 1
        assert up_to_date[0]["id"] == "IDEA-R-002"

    def test_dry_run_no_write(self, hdd_repo, hdd_svc_config):
        """dry_run=True reports would_backfill but doesn't modify file."""
        self._write_idea_file(hdd_repo, "IDEA-R-003", "Dry Run Test")
        svc = KanbanService(hdd_svc_config, hdd_repo)
        svc.scan()

        results = svc.backfill_turtle_blocks(dry_run=True)

        assert any(r["action"] == "would_backfill" for r in results)
        content = (hdd_repo / "research" / "ideas" / "IDEA-R-003-test.md").read_text()
        assert "```turtle" not in content  # File unchanged

    def test_non_hdd_skipped(self, hdd_repo, hdd_svc_config):
        """Expedition files are not processed by backfill."""
        # Add expeditions scan path
        hdd_svc_config.paths.scan_paths.append("kanban-work/expeditions/")
        hdd_svc_config.save(hdd_repo / ".kanban" / "config.yaml")

        self._write_expedition_file(hdd_repo, "EXP-001", "Test Expedition")
        svc = KanbanService(hdd_svc_config, hdd_repo)
        svc.scan()

        results = svc.backfill_turtle_blocks(dry_run=True)
        assert not any(r["id"] == "EXP-001" for r in results)

    def test_idempotent(self, hdd_repo, hdd_svc_config):
        """Running backfill twice produces the same result."""
        self._write_idea_file(hdd_repo, "IDEA-R-004", "Idempotent Test")
        svc = KanbanService(hdd_svc_config, hdd_repo)
        svc.scan()

        # First run: backfill
        results1 = svc.backfill_turtle_blocks(dry_run=False)
        assert any(r["action"] == "backfill" for r in results1)

        # Re-scan and run again
        svc.scan()
        results2 = svc.backfill_turtle_blocks(dry_run=False)
        assert all(r["action"] == "up_to_date" for r in results2)

    def test_hypothesis_backfill_with_paper(self, hdd_repo, hdd_svc_config):
        """Hypothesis file with paper field gets hyp:paper triple."""
        fp = hdd_repo / "research" / "hypotheses" / "H130.1-test.md"
        fp.write_text(
            "---\nid: H130.1\ntitle: \"Test Hyp\"\ntype: hypothesis\n"
            "status: active\npaper: Paper130\ntarget: \">=85%\"\n"
            "created: 2026-01-01\n---\n\n# H130.1: Test Hyp\n"
        )
        svc = KanbanService(hdd_svc_config, hdd_repo)
        svc.scan()

        results = svc.backfill_turtle_blocks(dry_run=False)
        backfilled = [r for r in results if r["action"] == "backfill"]
        assert len(backfilled) == 1

        content = fp.read_text()
        assert "```turtle" in content
        assert "PAPER-130" in content
        assert ">=85%" in content

    def test_literature_backfill_with_source_idea(self, hdd_repo, hdd_svc_config):
        """Literature file with source_idea gets lit:explores triple."""
        fp = hdd_repo / "research" / "literature" / "LIT-001-test.md"
        fp.write_text(
            "---\nid: LIT-001\ntitle: \"Survey of RDF\"\ntype: literature\n"
            "status: captured\nsource_idea: IDEA-R-001\n"
            "created: 2026-01-01\n---\n\n# LIT-001: Survey of RDF\n"
        )
        svc = KanbanService(hdd_svc_config, hdd_repo)
        svc.scan()

        results = svc.backfill_turtle_blocks(dry_run=False)
        backfilled = [r for r in results if r["action"] == "backfill"]
        assert len(backfilled) == 1

        content = fp.read_text()
        assert "```turtle" in content
        assert "lit:Literature" in content or "Literature" in content
        assert "IDEA-R-001" in content

    def test_paper_backfill(self, hdd_repo, hdd_svc_config):
        """Paper file gets paper:Paper type + rdfs:label."""
        fp = hdd_repo / "research" / "papers" / "PAPER-999-test.md"
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(
            "---\nid: PAPER-999\ntitle: \"Test Paper\"\ntype: paper\n"
            "status: draft\ncreated: 2026-01-01\n---\n\n# PAPER-999: Test Paper\n"
        )
        # Add papers scan path
        hdd_svc_config.paths.scan_paths.append("research/papers/")
        hdd_svc_config.save(hdd_repo / ".kanban" / "config.yaml")
        svc = KanbanService(hdd_svc_config, hdd_repo)
        svc.scan()

        results = svc.backfill_turtle_blocks(dry_run=False)
        backfilled = [r for r in results if r["action"] == "backfill" and r["id"] == "PAPER-999"]
        assert len(backfilled) == 1

        content = fp.read_text()
        assert "```turtle" in content
        assert "paper:Paper" in content or "Paper" in content

    def test_partial_block_augmented(self, hdd_repo, hdd_svc_config):
        """File with turtle block missing some triples gets them added."""
        fp = hdd_repo / "research" / "hypotheses" / "H200.1-test.md"
        # Block has type triple but is missing label, paper, target
        fp.write_text(
            "---\nid: H200.1\ntitle: \"Partial Hyp\"\ntype: hypothesis\n"
            "status: active\npaper: Paper200\ntarget: \">=90%\"\n"
            "created: 2026-01-01\n---\n\n"
            "```turtle\n"
            "@prefix hyp: <https://nusy.dev/hyp/> .\n\n"
            "<#H200.1> a hyp:Hypothesis .\n"
            "```\n\n# H200.1: Partial Hyp\n"
        )
        svc = KanbanService(hdd_svc_config, hdd_repo)
        svc.scan()

        results = svc.backfill_turtle_blocks(dry_run=False)
        backfilled = [r for r in results if r["action"] == "backfill"]
        assert len(backfilled) == 1
        # Missing: label + paper + target = 3 triples
        assert backfilled[0]["triples_added"] >= 3

        content = fp.read_text()
        assert "PAPER-200" in content
        assert ">=90%" in content

    def test_hypothesis_with_literature(self, hdd_repo, hdd_svc_config):
        """Hypothesis with literature field gets hyp:informedBy triple."""
        fp = hdd_repo / "research" / "hypotheses" / "H300.1-test.md"
        fp.write_text(
            "---\nid: H300.1\ntitle: \"Lit Hyp\"\ntype: hypothesis\n"
            "status: active\nliterature:\n  - LIT-001\n  - LIT-002\n"
            "created: 2026-01-01\n---\n\n# H300.1: Lit Hyp\n"
        )
        svc = KanbanService(hdd_svc_config, hdd_repo)
        svc.scan()

        results = svc.backfill_turtle_blocks(dry_run=False)
        backfilled = [r for r in results if r["action"] == "backfill"]
        assert len(backfilled) == 1

        content = fp.read_text()
        assert "```turtle" in content
        assert "LIT-001" in content
        assert "LIT-002" in content


class TestMoveAssignMissingKey:
    """move --assign must record the assignee even when the key is absent (#97)."""

    @staticmethod
    def _frontmatter(path: Path) -> dict:
        return yaml.safe_load(path.read_text().split("---", 2)[1])

    def test_create_writes_null_assignee(self, temp_repo, nautical_config):
        """create emits `assignee: null`, matching the scaffolded templates."""
        svc = KanbanService(nautical_config, temp_repo)
        item = svc.create_item(WorkItemType.EXPEDITION, "No assignee")

        assert "\nassignee: null\n" in item.file_path.read_text()
        assert self._frontmatter(item.file_path)["assignee"] is None

    def test_move_adds_assignee_when_key_absent(self, temp_repo, nautical_config):
        """A hand-written file with no assignee: key gains one on move -a."""
        svc = KanbanService(nautical_config, temp_repo)
        path = temp_repo / "kanban-work" / "expeditions" / "EXP-001-probe.md"
        path.write_text(
            "---\nid: EXP-001\ntitle: \"probe\"\ntype: expedition\n"
            "status: backlog\n---\n\n# probe\n"
        )
        svc.scan()

        svc.move_item(
            "EXP-001", WorkItemStatus.READY, commit=False,
            assignee="agent-x", validate_workflow=False,
        )

        fm = self._frontmatter(path)
        assert fm["assignee"] == "agent-x"
        assert fm["status"] == "ready"
        assert fm["id"] == "EXP-001"
        svc.scan()
        assert [i.id for i in svc.get_items(assignee="agent-x")] == ["EXP-001"]

    def test_move_adds_status_when_key_absent(self, temp_repo, nautical_config):
        """status gets the same add-if-absent treatment as assignee."""
        svc = KanbanService(nautical_config, temp_repo)
        path = temp_repo / "kanban-work" / "expeditions" / "EXP-001-probe.md"
        path.write_text(
            "---\nid: EXP-001\ntitle: \"probe\"\ntype: expedition\n---\n\n# probe\n"
        )
        svc.scan()

        svc.move_item(
            "EXP-001", WorkItemStatus.READY, commit=False, validate_workflow=False,
        )

        assert self._frontmatter(path)["status"] == "ready"

    def test_cli_create_then_move_assign_is_listed(
        self, temp_repo, nautical_config, monkeypatch,
    ):
        """The issue's repro: create → move -a → list --assignee finds the item."""
        runner = CliRunner()
        monkeypatch.chdir(temp_repo)

        for args in (
            ["create", "expedition", "probe"],
            ["move", "EXP-001", "ready", "--no-commit"],
            ["move", "EXP-001", "in_progress", "-a", "agent-x", "--no-commit"],
        ):
            result = runner.invoke(main, args, catch_exceptions=False)
            assert result.exit_code == 0, result.output

        result = runner.invoke(
            main, ["list", "--assignee", "agent-x", "--json"], catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        assert [i["id"] for i in json.loads(result.output)] == ["EXP-001"]


class TestFrontmatterCloser:
    """A closing line that merely starts with `---` still ends the frontmatter (#101)."""

    @pytest.mark.parametrize("closer", ["--- # end", "----"])
    def test_move_assign_with_decorated_closer(
        self, temp_repo, nautical_config, closer,
    ):
        svc = KanbanService(nautical_config, temp_repo)
        path = temp_repo / "kanban-work" / "expeditions" / "EXP-001-probe.md"
        path.write_text(
            f"---\nid: EXP-001\ntitle: \"probe\"\ntype: expedition\n"
            f"status: backlog\n{closer}\n\n# probe\n\n---\n\nnotes\n"
        )
        svc.scan()

        svc.move_item(
            "EXP-001", WorkItemStatus.READY, commit=False,
            assignee="agent-x", validate_workflow=False,
        )

        head, body = path.read_text().split(f"\n{closer}\n", 1)
        assert "\nassignee: agent-x" in head
        assert "\nstatus: ready" in head
        assert "assignee" not in body


# ---------------------------------------------------------------------------
# Issue #102 — theme per-type paths must live under the configured root
# ---------------------------------------------------------------------------


def _init_board_repo(root: Path, config_yaml: str) -> Path:
    """Init a git repo at ``root`` with ``.kanban/config.yaml`` = ``config_yaml``."""
    import subprocess

    subprocess.run(["git", "init", "-b", "main"], cwd=root, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=root, capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=root, capture_output=True, check=True,
    )
    (root / ".kanban").mkdir()
    (root / ".kanban" / "config.yaml").write_text(config_yaml)
    return root


def _single_board_yaml(theme: str, root: str) -> str:
    return (
        "kanban:\n"
        f"  theme: {theme}\n"
        "  paths:\n"
        f"    root: {root}\n"
        "    scan_paths:\n"
        f"      - {root}\n"
    )


@pytest.fixture
def board_runner(monkeypatch):
    """Factory: build a board repo from config YAML, chdir into it, return a runner."""
    from yurtle_kanban import config as config_mod

    def _make(repo: Path, config_yaml: str) -> CliRunner:
        _init_board_repo(repo, config_yaml)
        config_mod._theme_cache.clear()
        monkeypatch.chdir(repo)
        return CliRunner()

    yield _make
    config_mod._theme_cache.clear()


def _created_files(repo: Path, prefix: str) -> list[Path]:
    """All work-item files with the given ID prefix, repo-relative, outside .kanban/."""
    return sorted(
        p.relative_to(repo)
        for p in repo.rglob(f"{prefix}-*.md")
        if ".kanban" not in p.relative_to(repo).parts
    )


class TestThemePathsUnderConfiguredRoot:
    """create must place items under the board's configured root/path, so that
    board/list can see them — theme per-type paths are not repo-absolute (#102)."""

    def test_software_create_feature_lands_under_root(self, tmp_path, board_runner):
        """Issue repro: software theme, root work/ → create feature goes under work/."""
        runner = board_runner(tmp_path, _single_board_yaml("software", "work/"))

        result = runner.invoke(main, ["create", "feature", "probe"], catch_exceptions=False)
        assert result.exit_code == 0, result.output

        files = _created_files(tmp_path, "FEAT")
        assert len(files) == 1, files
        assert files[0].parts[0] == "work", (
            f"feature created at {files[0]}, outside the configured root work/"
        )
        assert not (tmp_path / "kanban-work").exists()

    def test_software_created_feature_is_listed(self, tmp_path, board_runner):
        """Issue repro: after create feature, list must show it (not 'No work items')."""
        runner = board_runner(tmp_path, _single_board_yaml("software", "work/"))

        runner.invoke(main, ["create", "feature", "probe"], catch_exceptions=False)
        result = runner.invoke(main, ["list", "--json"], catch_exceptions=False)

        assert result.exit_code == 0, result.output
        assert "No work items found" not in result.output
        assert "FEAT-001" in result.output

    def test_nautical_create_expedition_lands_under_root(self, tmp_path, board_runner):
        """Issue repro: nautical theme, root work/ → create expedition goes under work/."""
        runner = board_runner(tmp_path, _single_board_yaml("nautical", "work/"))

        result = runner.invoke(main, ["create", "expedition", "probe"], catch_exceptions=False)
        assert result.exit_code == 0, result.output

        files = _created_files(tmp_path, "EXP")
        assert len(files) == 1, files
        assert files[0].parts[0] == "work", (
            f"expedition created at {files[0]}, outside the configured root work/"
        )
        assert not (tmp_path / "kanban-work").exists()

    def test_nautical_created_expedition_is_listed(self, tmp_path, board_runner):
        """Issue repro: after create expedition, list must show it."""
        runner = board_runner(tmp_path, _single_board_yaml("nautical", "work/"))

        runner.invoke(main, ["create", "expedition", "probe"], catch_exceptions=False)
        result = runner.invoke(main, ["list", "--json"], catch_exceptions=False)

        assert result.exit_code == 0, result.output
        assert "No work items found" not in result.output
        assert "EXP-001" in result.output

    def test_multi_board_software_path_create_feature_lands_under_board_path(
        self, tmp_path, board_runner,
    ):
        """Multi-board form: a software board with path work/ → feature under work/."""
        config_yaml = (
            'version: "2.0"\n'
            "boards:\n"
            "  - name: dev\n"
            "    preset: software\n"
            "    path: work/\n"
            "default_board: dev\n"
        )
        runner = board_runner(tmp_path, config_yaml)

        result = runner.invoke(main, ["create", "feature", "probe"], catch_exceptions=False)
        assert result.exit_code == 0, result.output

        files = _created_files(tmp_path, "FEAT")
        assert len(files) == 1, files
        assert files[0].parts[0] == "work", (
            f"feature created at {files[0]}, outside the dev board path work/"
        )

        listed = runner.invoke(main, ["list", "--json"], catch_exceptions=False)
        assert "FEAT-001" in listed.output

    # -- negative controls: boards whose root IS the theme default -----------

    def test_control_default_root_software_placement_unchanged(self, tmp_path, board_runner):
        """Control: root kanban-work/ keeps kanban-work/features/FEAT-001-….md."""
        runner = board_runner(tmp_path, _single_board_yaml("software", "kanban-work/"))

        result = runner.invoke(main, ["create", "feature", "probe"], catch_exceptions=False)
        assert result.exit_code == 0, result.output

        files = _created_files(tmp_path, "FEAT")
        assert files == [Path("kanban-work/features/FEAT-001-probe.md")]

        listed = runner.invoke(main, ["list", "--json"], catch_exceptions=False)
        assert "FEAT-001" in listed.output

    def test_control_default_root_nautical_placement_unchanged(self, tmp_path, board_runner):
        """Control: root kanban-work/ keeps kanban-work/expeditions/EXP-001-….md."""
        runner = board_runner(tmp_path, _single_board_yaml("nautical", "kanban-work/"))

        result = runner.invoke(main, ["create", "expedition", "probe"], catch_exceptions=False)
        assert result.exit_code == 0, result.output

        files = _created_files(tmp_path, "EXP")
        assert files == [Path("kanban-work/expeditions/EXP-001-probe.md")]

    def test_control_hdd_research_root_placement_unchanged(self, tmp_path, board_runner):
        """Control: hdd board with root research/ keeps research/ideas/, research/hypotheses/."""
        runner = board_runner(tmp_path, _single_board_yaml("hdd", "research/"))

        result = runner.invoke(main, ["create", "idea", "probe"], catch_exceptions=False)
        assert result.exit_code == 0, result.output
        result = runner.invoke(
            main, ["create", "hypothesis", "probe"], catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output

        created = sorted(
            p.relative_to(tmp_path).parent
            for p in (tmp_path / "research").rglob("*.md")
        )
        assert Path("research/ideas") in created
        assert Path("research/hypotheses") in created
        assert all(p.parts[0] == "research" for p in created)

    # -- negative controls: boards made by the default `init` ---------------
    # `init` (no --path) writes root: work/ but scan_paths under kanban-work/.
    # The invariant is "create writes somewhere the board SCANS", so these
    # boards must keep placing items under kanban-work/ and keep listing them.

    @pytest.fixture
    def init_runner(self, tmp_path, monkeypatch):
        """Factory: `yurtle-kanban init --theme <theme>` in a fresh git repo."""
        import subprocess

        from yurtle_kanban import config as config_mod

        subprocess.run(
            ["git", "init", "-b", "main"], cwd=tmp_path, capture_output=True, check=True,
        )
        config_mod._theme_cache.clear()
        monkeypatch.chdir(tmp_path)

        def _make(theme: str) -> CliRunner:
            runner = CliRunner()
            result = runner.invoke(main, ["init", "--theme", theme], catch_exceptions=False)
            assert result.exit_code == 0, result.output
            config_mod._theme_cache.clear()
            return runner

        yield _make
        config_mod._theme_cache.clear()

    def test_control_default_init_software_placement_unchanged(self, tmp_path, init_runner):
        """Control: default `init --theme software` board keeps kanban-work/features/."""
        runner = init_runner("software")

        result = runner.invoke(main, ["create", "feature", "probe"], catch_exceptions=False)
        assert result.exit_code == 0, result.output

        assert _created_files(tmp_path, "FEAT") == [
            Path("kanban-work/features/FEAT-001-probe.md")
        ]
        listed = runner.invoke(main, ["list", "--json"], catch_exceptions=False)
        assert "FEAT-001" in listed.output

    def test_control_default_init_nautical_placement_unchanged(self, tmp_path, init_runner):
        """Control: default `init --theme nautical` board keeps kanban-work/expeditions/."""
        runner = init_runner("nautical")

        result = runner.invoke(main, ["create", "expedition", "probe"], catch_exceptions=False)
        assert result.exit_code == 0, result.output

        assert _created_files(tmp_path, "EXP") == [
            Path("kanban-work/expeditions/EXP-001-probe.md")
        ]
        listed = runner.invoke(main, ["list", "--json"], catch_exceptions=False)
        assert "EXP-001" in listed.output


# ---------------------------------------------------------------------------
# Issue #103 — frontmatter delimiters are whole `---` lines, not substrings
# ---------------------------------------------------------------------------


class TestFrontmatterDashInValue:
    """A `---` inside a frontmatter value must not end the frontmatter (#103)."""

    @staticmethod
    def _write(path: Path, frontmatter: str, body: str) -> Path:
        path.write_text(f"---\n{frontmatter}---\n\n{body}")
        return path

    @staticmethod
    def _frontmatter_block(content: str) -> str:
        """Text between the opening `---` line and the next whole `---` line."""
        lines = content.split("\n")
        assert lines[0] == "---"
        end = lines.index("---", 1)
        return "\n".join(lines[1:end])

    # -- the issue's own repro (reader) ------------------------------------

    def test_cli_create_then_list_shows_dashed_title(
        self, temp_repo, software_config, monkeypatch,
    ):
        """`create feature "A --- B"` then `list` lists the item, full title."""
        monkeypatch.chdir(temp_repo)
        runner = CliRunner()

        created = runner.invoke(
            main, ["create", "feature", "A --- B"], catch_exceptions=False,
        )
        assert created.exit_code == 0, created.output
        files = list((temp_repo / "kanban-work" / "features").glob("FEAT-001*.md"))
        assert len(files) == 1
        assert 'title: "A --- B"' in files[0].read_text()  # the file is fine

        listed = runner.invoke(main, ["list"], catch_exceptions=False)
        assert listed.exit_code == 0
        assert "No work items found" not in listed.output
        assert "FEAT-001" in listed.output
        assert "A --- B" in listed.output

    def test_cli_show_json_dashed_title(
        self, temp_repo, software_config, monkeypatch,
    ):
        """`show FEAT-001 --json` finds the item and reports the full title."""
        monkeypatch.chdir(temp_repo)
        runner = CliRunner()
        runner.invoke(main, ["create", "feature", "A --- B"], catch_exceptions=False)

        result = runner.invoke(main, ["show", "FEAT-001", "--json"], catch_exceptions=False)
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["id"] == "FEAT-001"
        assert data["title"] == "A --- B"

    def test_scan_dashed_title_keeps_all_fields(self, temp_repo, software_config):
        """An item titled `A --- B` scans with its title, status and priority."""
        self._write(
            temp_repo / "kanban-work" / "features" / "FEAT-007-dash.md",
            'id: FEAT-007\ntitle: "A --- B"\ntype: feature\nstatus: in_progress\n'
            "priority: high\ncreated: 2026-01-01\n",
            "# FEAT-007: A --- B\n\nReal body.\n",
        )
        svc = KanbanService(software_config, temp_repo)
        items = {i.id: i for i in svc.scan()}

        assert "FEAT-007" in items
        item = items["FEAT-007"]
        assert item.title == "A --- B"
        assert item.status == WorkItemStatus.IN_PROGRESS
        assert str(getattr(item.priority, "value", item.priority)) == "high"

    def test_scan_dash_in_last_value_is_not_truncated(self, temp_repo, software_config):
        """A `---` in the last frontmatter value is kept whole, not cut off."""
        self._write(
            temp_repo / "kanban-work" / "features" / "FEAT-008-dash.md",
            'id: FEAT-008\ntype: feature\nstatus: backlog\ntitle: A --- B\n',
            "# FEAT-008\n\nReal body.\n",
        )
        svc = KanbanService(software_config, temp_repo)
        items = {i.id: i for i in svc.scan()}

        assert "FEAT-008" in items
        assert items["FEAT-008"].title == "A --- B"

    # -- description extractor ---------------------------------------------

    def test_description_is_body_not_frontmatter_fragment(
        self, temp_repo, software_config, monkeypatch,
    ):
        """Description is the real body, not the tail of the frontmatter."""
        self._write(
            temp_repo / "kanban-work" / "features" / "FEAT-009-dash.md",
            'id: FEAT-009\ntitle: "Plain"\ntype: feature\nstatus: backlog\n'
            'note: x --- y\n',
            "# FEAT-009: Plain\n\nThe real body.\n",
        )
        svc = KanbanService(software_config, temp_repo)
        items = {i.id: i for i in svc.scan()}
        assert "FEAT-009" in items
        assert items["FEAT-009"].description == "The real body."

        monkeypatch.chdir(temp_repo)
        result = CliRunner().invoke(
            main, ["show", "FEAT-009", "--json"], catch_exceptions=False,
        )
        data = json.loads(result.output)
        assert data["description"] == "The real body."

    # -- Turtle-block insertion after frontmatter (hdd backfill) -----------

    def test_backfill_inserts_block_after_closing_line(self, hdd_repo, hdd_svc_config):
        """`backfill_turtle_blocks` puts the block after the closing `---` line."""
        fp = self._write(
            hdd_repo / "research" / "ideas" / "IDEA-R-103-dash.md",
            'id: IDEA-R-103\ntitle: "Plain Idea"\ntype: idea\nstatus: captured\n'
            'created: 2026-01-01\nnote: x --- y\n',
            "# IDEA-R-103: Plain Idea\n\nContent here.\n",
        )
        svc = KanbanService(hdd_svc_config, hdd_repo)
        svc.scan()

        results = svc.backfill_turtle_blocks(dry_run=False)
        assert [r["id"] for r in results if r["action"] == "backfill"] == ["IDEA-R-103"]

        content = fp.read_text()
        front = self._frontmatter_block(content)
        assert "```" not in front
        assert 'note: x --- y' in front
        after = content.split("\n---\n", 1)[1]
        assert "```turtle" in after
        assert after.index("```turtle") < after.index("# IDEA-R-103")

        rescanned = {i.id: i for i in KanbanService(hdd_svc_config, hdd_repo).scan()}
        assert rescanned["IDEA-R-103"].title == "Plain Idea"
        assert rescanned["IDEA-R-103"].description == "Content here."

    def test_backfill_dashed_title_item(self, hdd_repo, hdd_svc_config):
        """An idea titled `A --- B` is backfilled; its title line stays intact."""
        fp = self._write(
            hdd_repo / "research" / "ideas" / "IDEA-R-104-dash.md",
            'id: IDEA-R-104\ntitle: "A --- B"\ntype: idea\nstatus: captured\n'
            "created: 2026-01-01\n",
            "# IDEA-R-104: A --- B\n\nContent here.\n",
        )
        svc = KanbanService(hdd_svc_config, hdd_repo)
        svc.scan()

        results = svc.backfill_turtle_blocks(dry_run=False)
        assert [r["id"] for r in results if r["action"] == "backfill"] == ["IDEA-R-104"]

        content = fp.read_text()
        front = self._frontmatter_block(content)
        assert 'title: "A --- B"' in front
        assert "```" not in front

    # -- negative controls (must stay green) --------------------------------

    def test_control_normal_item_parses(self, temp_repo, software_config):
        """An item with no `---` in any value parses exactly as before."""
        self._write(
            temp_repo / "kanban-work" / "features" / "FEAT-010-plain.md",
            'id: FEAT-010\ntitle: "Plain title"\ntype: feature\nstatus: review\n'
            "priority: low\ncreated: 2026-01-01\n",
            "# FEAT-010: Plain title\n\nPlain body.\n",
        )
        svc = KanbanService(software_config, temp_repo)
        items = {i.id: i for i in svc.scan()}

        item = items["FEAT-010"]
        assert item.title == "Plain title"
        assert item.status == WorkItemStatus.REVIEW
        assert str(getattr(item.priority, "value", item.priority)) == "low"
        assert item.description == "Plain body."

    def test_control_body_horizontal_rule_kept(self, temp_repo, software_config):
        """A `---` horizontal rule in the body leaves the body intact."""
        self._write(
            temp_repo / "kanban-work" / "features" / "FEAT-011-hr.md",
            'id: FEAT-011\ntitle: "Ruled"\ntype: feature\nstatus: backlog\n',
            "# FEAT-011: Ruled\n\nAbove.\n\n---\n\nBelow.\n",
        )
        svc = KanbanService(software_config, temp_repo)
        items = {i.id: i for i in svc.scan()}

        assert items["FEAT-011"].title == "Ruled"
        assert items["FEAT-011"].description == "Above.\n\n---\n\nBelow."

    def test_control_file_without_leading_dashes_is_not_item(
        self, temp_repo, software_config,
    ):
        """A file that does not start with `---` is still not an item."""
        (temp_repo / "kanban-work" / "features" / "NOTES.md").write_text(
            "# Notes\n\nid: FEAT-012\n---\ntitle: nope\n---\n"
        )
        (temp_repo / "kanban-work" / "features" / "FEAT-013-lead.md").write_text(
            '\n---\nid: FEAT-013\ntitle: "Leading blank"\ntype: feature\n'
            "status: backlog\n---\n\n# FEAT-013\n"
        )
        svc = KanbanService(software_config, temp_repo)

        assert svc.scan() == []

    def test_control_backfill_normal_item(self, hdd_repo, hdd_svc_config):
        """Backfill of an ordinary idea still lands between `---` and the heading."""
        fp = self._write(
            hdd_repo / "research" / "ideas" / "IDEA-R-105-plain.md",
            'id: IDEA-R-105\ntitle: "Plain Idea"\ntype: idea\nstatus: captured\n'
            "created: 2026-01-01\n",
            "# IDEA-R-105: Plain Idea\n\nContent here.\n",
        )
        svc = KanbanService(hdd_svc_config, hdd_repo)
        svc.scan()
        svc.backfill_turtle_blocks(dry_run=False)

        content = fp.read_text()
        assert "```" not in self._frontmatter_block(content)
        after = content.split("\n---\n", 1)[1]
        assert after.index("```turtle") < after.index("# IDEA-R-105")


# ---------------------------------------------------------------------------
# Issue #104 — frontmatter values must be written YAML-safe (quoted as needed)
# ---------------------------------------------------------------------------

# Values YAML would misread if written bare: a mapping (`team: core`), a
# boolean (`yes`), null (`null`), a comment (`#…`, `… #…`), an anchor (`&`),
# an alias (`*`), a flow sequence (`[`) and a flow mapping (`{`).
_YAML_SPECIAL_ASSIGNEES = [
    pytest.param("team: core", id="colon-space"),
    pytest.param("yes", id="yes-bool"),
    pytest.param("null", id="null"),
    pytest.param("#core", id="leading-hash"),
    pytest.param("core #1", id="space-hash"),
    pytest.param("&anchor", id="leading-ampersand"),
    pytest.param("*alias", id="leading-star"),
    pytest.param("[core]", id="leading-bracket"),
    pytest.param("{core}", id="leading-brace"),
]


class TestFrontmatterValuesRoundTrip:
    """A frontmatter value always reads back as the same string (#104)."""

    @staticmethod
    def _item_file(repo: Path, item_id: str) -> Path:
        files = list((repo / "kanban-work" / "features").glob(f"{item_id}*.md"))
        assert len(files) == 1, files
        return files[0]

    @staticmethod
    def _frontmatter_text(path: Path) -> str:
        lines = path.read_text().split("\n")
        assert lines[0] == "---"
        return "\n".join(lines[1:lines.index("---", 1)])

    @classmethod
    def _frontmatter(cls, path: Path) -> dict:
        return yaml.safe_load(cls._frontmatter_text(path))

    @staticmethod
    def _run(runner: CliRunner, args: list[str]):
        """Invoke the CLI; a crash fails as an assertion, not a raw exception."""
        result = runner.invoke(main, args)
        assert result.exit_code == 0, (
            f"{args} exited {result.exit_code}: {result.exception!r}\n{result.output}"
        )
        return result

    def _assert_reads_back(self, runner: CliRunner, repo: Path, value: str) -> None:
        """`list`, `show --json` and the file all see assignee == value (a str)."""
        listed = self._run(runner, ["list"])
        assert "No work items found" not in listed.output
        assert "FEAT-001" in listed.output

        shown = self._run(runner, ["show", "FEAT-001", "--json"])
        data = json.loads(shown.output)
        assert data["id"] == "FEAT-001"
        assert data["assignee"] == value

        path = self._item_file(repo, "FEAT-001")
        try:
            fm = self._frontmatter(path)
        except yaml.YAMLError as exc:
            pytest.fail(f"frontmatter is not valid YAML: {exc}\n{path.read_text()}")
        assert fm["assignee"] == value
        assert isinstance(fm["assignee"], str)

    # -- move --assign on an existing item ---------------------------------

    @pytest.mark.parametrize("value", _YAML_SPECIAL_ASSIGNEES)
    def test_move_assign_special_value_round_trips(
        self, temp_repo, software_config, monkeypatch, value,
    ):
        """`move FEAT-001 ready -a <value>` reads back exactly <value>."""
        monkeypatch.chdir(temp_repo)
        runner = CliRunner()
        self._run(runner, ["create", "feature", "probe"])
        self._run(runner, ["move", "FEAT-001", "ready", "-a", value, "--no-commit"])

        self._assert_reads_back(runner, temp_repo, value)

    # -- create --assignee (WorkItem.to_markdown) --------------------------

    @pytest.mark.parametrize("value", _YAML_SPECIAL_ASSIGNEES)
    def test_create_assignee_special_value_round_trips(
        self, temp_repo, software_config, monkeypatch, value,
    ):
        """`create feature probe --assignee <value>` reads back exactly <value>."""
        monkeypatch.chdir(temp_repo)
        runner = CliRunner()
        self._run(runner, ["create", "feature", "probe", "--assignee", value])

        self._assert_reads_back(runner, temp_repo, value)

    # -- negative controls (must stay green) --------------------------------

    @pytest.mark.parametrize("value", ["Claude-M5", "agent-x"])
    def test_control_move_plain_assignee_unquoted(
        self, temp_repo, software_config, monkeypatch, value,
    ):
        """An ordinary assignee is written plain, and status keeps its plain form."""
        monkeypatch.chdir(temp_repo)
        runner = CliRunner()
        self._run(runner, ["create", "feature", "probe"])
        self._run(runner, ["move", "FEAT-001", "ready", "-a", value, "--no-commit"])

        front = self._frontmatter_text(self._item_file(temp_repo, "FEAT-001"))
        assert f"\nassignee: {value}\n" in f"\n{front}\n"
        assert "\nstatus: ready\n" in f"\n{front}\n"
        self._assert_reads_back(runner, temp_repo, value)

    @pytest.mark.parametrize("value", ["Claude-M5", "agent-x"])
    def test_control_create_plain_assignee_unquoted(
        self, temp_repo, software_config, monkeypatch, value,
    ):
        """create writes an ordinary assignee, status and priority plain."""
        monkeypatch.chdir(temp_repo)
        runner = CliRunner()
        self._run(
            runner,
            ["create", "feature", "probe", "--assignee", value, "--priority", "high"],
        )

        front = self._frontmatter_text(self._item_file(temp_repo, "FEAT-001"))
        assert f"\nassignee: {value}\n" in f"\n{front}\n"
        assert "\nstatus: backlog\n" in f"\n{front}\n"
        assert "\npriority: high\n" in f"\n{front}\n"
        self._assert_reads_back(runner, temp_repo, value)


# ---------------------------------------------------------------------------
# Issue #105 — a frontmatter edit replaces the key's WHOLE value
# ---------------------------------------------------------------------------


class TestFrontmatterEditReplacesWholeValue:
    """Editing a frontmatter key replaces its whole value, continuation lines too (#105)."""

    _BODY = (
        "\n# probe\n\n"
        "Body text that must survive untouched.\n\n"
        "  - alice\n"
        "assignee: body-text\n"
        "status: body-text\n\n"
        "---\n\n"
        "notes after a rule\n"
    )

    @staticmethod
    def _run(runner: CliRunner, args: list[str]):
        """Invoke the CLI; a crash fails as an assertion, not a raw exception."""
        result = runner.invoke(main, args)
        assert result.exit_code == 0, (
            f"{args} exited {result.exit_code}: {result.exception!r}\n{result.output}"
        )
        return result

    @staticmethod
    def _item_file(repo: Path) -> Path:
        files = list((repo / "kanban-work" / "features").glob("FEAT-001*.md"))
        assert len(files) == 1, files
        return files[0]

    @staticmethod
    def _split(path: Path) -> tuple[str, str]:
        """(frontmatter text, body text) split on the first whole `---` closer."""
        text = path.read_text()
        lines = text.split("\n")
        assert lines[0] == "---"
        close = lines.index("---", 1)
        return "\n".join(lines[1:close]), "\n".join(lines[close + 1:])

    def _frontmatter(self, path: Path) -> dict:
        front, _ = self._split(path)
        try:
            fm = yaml.safe_load(front)
        except yaml.YAMLError as exc:
            pytest.fail(f"frontmatter is not valid YAML: {exc}\n{path.read_text()}")
        assert isinstance(fm, dict), front
        return fm

    def _setup(self, repo: Path, runner: CliRunner, middle: str) -> Path:
        """create FEAT-001, then hand-edit its frontmatter to carry `middle`.

        `middle` is spliced between fixed leading keys and fixed trailing
        keys (`created:` and a `tags:` block list) so tests can check the
        keys after the edited one survive exactly.
        """
        self._run(runner, ["create", "feature", "probe"])
        path = self._item_file(repo)
        path.write_text(
            "---\n"
            "id: FEAT-001\n"
            'title: "probe"\n'
            "type: feature\n"
            f"{middle}"
            "created: 2026-09-24\n"
            "tags:\n"
            "  - one\n"
            "  - two\n"
            "---\n" + self._BODY
        )
        # Sanity: the hand-edited file is valid YAML to begin with.
        self._frontmatter(path)
        return path

    def _assert_tail_and_body_intact(self, path: Path) -> None:
        front, body = self._split(path)
        assert front.endswith(
            "\ncreated: 2026-09-24\ntags:\n  - one\n  - two"
        ), front
        # `move` may append its own ```yurtle status block after the body;
        # everything that was already there must be byte-for-byte intact.
        assert body.startswith(self._BODY), body
        fm = self._frontmatter(path)
        assert fm["created"] is not None
        assert fm["tags"] == ["one", "two"]
        assert fm["id"] == "FEAT-001"

    def _show(self, runner: CliRunner) -> dict:
        data = json.loads(self._run(runner, ["show", "FEAT-001", "--json"]).output)
        assert data["id"] == "FEAT-001"
        return data

    def _listed_for(self, runner: CliRunner, assignee: str) -> list[str]:
        out = self._run(runner, ["list", "--assignee", assignee, "--json"]).output
        if "No work items found" in out:
            return []
        return [i["id"] for i in json.loads(out)]

    # -- the issue's repro: assignee as a block list ------------------------

    def test_move_assign_replaces_block_list_assignee(
        self, temp_repo, software_config, monkeypatch,
    ):
        """`move -a carol` over `assignee:\\n  - alice\\n  - bob` yields exactly carol."""
        monkeypatch.chdir(temp_repo)
        runner = CliRunner()
        path = self._setup(
            temp_repo, runner,
            "status: backlog\npriority: medium\nassignee:\n  - alice\n  - bob\n",
        )

        self._run(runner, ["move", "FEAT-001", "ready", "-a", "carol", "--no-commit"])

        assert self._show(runner)["assignee"] == "carol"
        assert self._listed_for(runner, "carol") == ["FEAT-001"]
        fm = self._frontmatter(path)
        assert fm["assignee"] == "carol"
        assert fm["status"] == "ready"
        front, _ = self._split(path)
        assert "- alice" not in front
        assert "- bob" not in front
        self._assert_tail_and_body_intact(path)

    def test_move_assign_replaces_folded_plain_continuation(
        self, temp_repo, software_config, monkeypatch,
    ):
        """`assignee: carol\\n  smith` (one plain scalar over two lines) is fully replaced."""
        monkeypatch.chdir(temp_repo)
        runner = CliRunner()
        path = self._setup(
            temp_repo, runner,
            "status: backlog\npriority: medium\nassignee: carol\n  smith\n",
        )
        assert self._frontmatter(path)["assignee"] == "carol smith"

        self._run(runner, ["move", "FEAT-001", "ready", "-a", "dave", "--no-commit"])

        assert self._show(runner)["assignee"] == "dave"
        assert self._listed_for(runner, "dave") == ["FEAT-001"]
        assert self._frontmatter(path)["assignee"] == "dave"
        front, _ = self._split(path)
        assert "smith" not in front
        self._assert_tail_and_body_intact(path)

    # -- status (the same helper) --------------------------------------------

    @pytest.mark.parametrize(
        "status_form",
        [
            pytest.param("status:\n  backlog\n", id="value-on-next-line"),
            pytest.param("status: >-\n  backlog\n", id="folded-block-scalar"),
        ],
    )
    def test_move_replaces_multiline_status(
        self, temp_repo, software_config, monkeypatch, status_form,
    ):
        """A status written over several lines is replaced whole by `move`."""
        monkeypatch.chdir(temp_repo)
        runner = CliRunner()
        path = self._setup(
            temp_repo, runner,
            f"{status_form}priority: medium\nassignee: alice\n",
        )
        assert self._frontmatter(path)["status"] == "backlog"

        self._run(runner, ["move", "FEAT-001", "ready", "--no-commit"])

        assert self._show(runner)["status"] == "ready"
        fm = self._frontmatter(path)
        assert fm["status"] == "ready"
        assert fm["assignee"] == "alice"
        front, _ = self._split(path)
        assert "backlog" not in front
        self._assert_tail_and_body_intact(path)

    # -- priority_rank / value_summary via `rank` ---------------------------

    def test_rank_replaces_multiline_value_summary(
        self, temp_repo, software_config, monkeypatch,
    ):
        """`rank --summary` replaces a folded, multi-line value_summary whole."""
        monkeypatch.chdir(temp_repo)
        runner = CliRunner()
        path = self._setup(
            temp_repo, runner,
            "status: backlog\npriority: medium\nassignee: alice\n"
            # priority_rank seeded so `rank` updates it in place rather than
            # appending it after the tail keys this test checks.
            "priority_rank: 5\n"
            "value_summary: >-\n  old value\n  spanning lines\n",
        )
        assert self._frontmatter(path)["value_summary"] == "old value spanning lines"

        self._run(
            runner, ["rank", "FEAT-001", "1", "--summary", "new value", "--no-commit"],
        )

        data = self._show(runner)
        assert data["value_summary"] == "new value"
        assert data["priority_rank"] == 1
        fm = self._frontmatter(path)
        assert fm["value_summary"] == "new value"
        front, _ = self._split(path)
        assert "old value" not in front
        assert "spanning lines" not in front
        self._assert_tail_and_body_intact(path)

    def test_rank_replaces_priority_rank_on_continuation_line(
        self, temp_repo, software_config, monkeypatch,
    ):
        """`priority_rank:\\n  7` (value on an indented line) is replaced whole by `rank`."""
        monkeypatch.chdir(temp_repo)
        runner = CliRunner()
        path = self._setup(
            temp_repo, runner,
            "status: backlog\npriority: medium\nassignee: alice\npriority_rank:\n  7\n",
        )
        assert self._frontmatter(path)["priority_rank"] == 7

        self._run(runner, ["rank", "FEAT-001", "1", "--no-commit"])

        assert self._show(runner)["priority_rank"] == 1
        assert self._frontmatter(path)["priority_rank"] == 1
        self._assert_tail_and_body_intact(path)

    # -- negative controls (must stay green) --------------------------------

    def test_control_single_line_assignee_replaced_following_keys_exact(
        self, temp_repo, software_config, monkeypatch,
    ):
        """A single-line value is replaced as today; later keys and body are untouched."""
        monkeypatch.chdir(temp_repo)
        runner = CliRunner()
        path = self._setup(
            temp_repo, runner,
            "status: backlog\npriority: medium\nassignee: alice\n",
        )

        self._run(runner, ["move", "FEAT-001", "ready", "-a", "carol", "--no-commit"])

        front, _ = self._split(path)
        assert front == (
            "id: FEAT-001\n"
            'title: "probe"\n'
            "type: feature\n"
            "status: ready\n"
            "priority: medium\n"
            "assignee: carol\n"
            "created: 2026-09-24\n"
            "tags:\n"
            "  - one\n"
            "  - two"
        )
        assert self._show(runner)["assignee"] == "carol"
        assert self._listed_for(runner, "carol") == ["FEAT-001"]
        self._assert_tail_and_body_intact(path)

    def test_control_same_prefix_key_untouched(
        self, temp_repo, software_config, monkeypatch,
    ):
        """`assignee_note:` right after `assignee:` is not part of assignee's value."""
        monkeypatch.chdir(temp_repo)
        runner = CliRunner()
        path = self._setup(
            temp_repo, runner,
            "status: backlog\npriority: medium\nassignee: alice\n"
            "assignee_note: keep me\n",
        )

        self._run(runner, ["move", "FEAT-001", "ready", "-a", "carol", "--no-commit"])

        front, _ = self._split(path)
        assert "\nassignee: carol\nassignee_note: keep me\n" in f"\n{front}\n"
        fm = self._frontmatter(path)
        assert fm["assignee"] == "carol"
        assert fm["assignee_note"] == "keep me"
        self._assert_tail_and_body_intact(path)

    def test_control_status_edit_leaves_following_block_list_key(
        self, temp_repo, software_config, monkeypatch,
    ):
        """A single-line status followed by a key with an indented block is untouched."""
        monkeypatch.chdir(temp_repo)
        runner = CliRunner()
        path = self._setup(
            temp_repo, runner,
            "status: backlog\nreviewers:\n  - x\n  - y\npriority: medium\n"
            "assignee: alice\n",
        )

        self._run(runner, ["move", "FEAT-001", "ready", "--no-commit"])

        front, _ = self._split(path)
        assert "\nstatus: ready\nreviewers:\n  - x\n  - y\npriority: medium\n" in (
            f"\n{front}\n"
        )
        fm = self._frontmatter(path)
        assert fm["reviewers"] == ["x", "y"]
        assert fm["status"] == "ready"
        self._assert_tail_and_body_intact(path)

    # -- round 2: block scalars with blank lines, column-0 lists ------------

    @pytest.mark.parametrize(
        "gap",
        [
            pytest.param("\n", id="empty-line"),
            pytest.param("  \n", id="whitespace-only-line"),
        ],
    )
    def test_rank_replaces_literal_block_with_paragraph_break(
        self, temp_repo, software_config, monkeypatch, gap,
    ):
        """A `value_summary: |` with a blank line inside is one value, replaced whole."""
        monkeypatch.chdir(temp_repo)
        runner = CliRunner()
        path = self._setup(
            temp_repo, runner,
            "status: backlog\npriority: medium\nassignee: alice\n"
            "priority_rank: 5\n"
            f"value_summary: |\n  para one\n{gap}  para two\n",
        )
        assert self._frontmatter(path)["value_summary"] == "para one\n\npara two\n"

        self._run(
            runner, ["rank", "FEAT-001", "2", "--summary", "fresh", "--no-commit"],
        )

        data = self._show(runner)
        assert data["value_summary"] == "fresh"
        assert data["priority_rank"] == 2
        assert "FEAT-001" in self._run(runner, ["list"]).output
        fm = self._frontmatter(path)
        assert fm["value_summary"] == "fresh"
        assert fm["assignee"] == "alice"
        front, _ = self._split(path)
        assert "para one" not in front
        assert "para two" not in front
        self._assert_tail_and_body_intact(path)

    def test_move_assign_replaces_literal_block_with_paragraph_break(
        self, temp_repo, software_config, monkeypatch,
    ):
        """`move -a carol` over a two-paragraph `assignee: |` yields exactly carol."""
        monkeypatch.chdir(temp_repo)
        runner = CliRunner()
        path = self._setup(
            temp_repo, runner,
            "status: backlog\npriority: medium\n"
            "assignee: |\n  alice\n\n  bob\n",
        )

        self._run(runner, ["move", "FEAT-001", "ready", "-a", "carol", "--no-commit"])

        assert self._show(runner)["assignee"] == "carol"
        assert self._listed_for(runner, "carol") == ["FEAT-001"]
        fm = self._frontmatter(path)
        assert fm["assignee"] == "carol"
        front, _ = self._split(path)
        assert "alice" not in front
        assert "bob" not in front
        self._assert_tail_and_body_intact(path)

    def test_move_assign_replaces_column0_block_list(
        self, temp_repo, software_config, monkeypatch,
    ):
        """`assignee:\\n- alice\\n- bob` (items at column 0) is replaced whole."""
        monkeypatch.chdir(temp_repo)
        runner = CliRunner()
        path = self._setup(
            temp_repo, runner,
            "status: backlog\npriority: medium\nassignee:\n- alice\n- bob\n",
        )
        assert self._frontmatter(path)["assignee"] == ["alice", "bob"]

        self._run(runner, ["move", "FEAT-001", "ready", "-a", "carol", "--no-commit"])

        assert self._show(runner)["assignee"] == "carol"
        assert self._listed_for(runner, "carol") == ["FEAT-001"]
        fm = self._frontmatter(path)
        assert fm["assignee"] == "carol"
        front, _ = self._split(path)
        assert "- alice" not in front
        assert "- bob" not in front
        self._assert_tail_and_body_intact(path)

    def test_control_blank_line_after_value_kept(
        self, temp_repo, software_config, monkeypatch,
    ):
        """A blank line between the edited value and the next key survives the edit."""
        monkeypatch.chdir(temp_repo)
        runner = CliRunner()
        path = self._setup(
            temp_repo, runner,
            "status: backlog\npriority: medium\nassignee: alice\n\n",
        )

        self._run(runner, ["move", "FEAT-001", "ready", "-a", "carol", "--no-commit"])

        front, _ = self._split(path)
        assert "\nassignee: carol\n\ncreated: 2026-09-24\n" in f"\n{front}\n"
        assert self._frontmatter(path)["assignee"] == "carol"
        self._assert_tail_and_body_intact(path)

    def test_control_blank_line_before_closer_kept(
        self, temp_repo, software_config, monkeypatch,
    ):
        """A blank line right before the closing `---` survives editing the last key."""
        monkeypatch.chdir(temp_repo)
        runner = CliRunner()
        self._run(runner, ["create", "feature", "probe"])
        path = self._item_file(temp_repo)
        path.write_text(
            "---\n"
            "id: FEAT-001\n"
            'title: "probe"\n'
            "type: feature\n"
            "status: backlog\n"
            "priority: medium\n"
            "created: 2026-09-24\n"
            "assignee: alice\n"
            "\n"
            "---\n" + self._BODY
        )

        self._run(runner, ["move", "FEAT-001", "ready", "-a", "carol", "--no-commit"])

        front, body = self._split(path)
        assert front.endswith("\nassignee: carol\n"), repr(front)
        assert body.startswith(self._BODY), body
        fm = self._frontmatter(path)
        assert fm["assignee"] == "carol"
        assert fm["status"] == "ready"


# ---------------------------------------------------------------------------
# Issue #128 — frontmatter edit edge cases: a comment inside a block list,
# and CRLF files keeping their own line ending
# ---------------------------------------------------------------------------


class TestFrontmatterEditEdgeCases:
    """A comment inside a value's block is consumed with it; CRLF files stay CRLF (#128)."""

    # Reuse the #105 class's helpers (setup, split, YAML check, tail/body check).
    _w = TestFrontmatterEditReplacesWholeValue()
    _BODY = TestFrontmatterEditReplacesWholeValue._BODY

    _TAIL = "created: 2026-09-24\ntags:\n  - one\n  - two\n"

    def _lf_text(self, middle: str) -> str:
        """The exact file text `_setup` writes for `middle` (LF endings)."""
        return (
            "---\n"
            "id: FEAT-001\n"
            'title: "probe"\n'
            "type: feature\n"
            f"{middle}"
            f"{self._TAIL}"
            "---\n" + self._BODY
        )

    def _setup_crlf(self, repo: Path, runner: CliRunner, middle: str) -> Path:
        """Like the #105 `_setup`, but the whole file is written with CRLF endings."""
        path = self._w._setup(repo, runner, middle)
        raw = self._lf_text(middle).replace("\n", "\r\n").encode()
        path.write_bytes(raw)
        # Sanity: every line really is CRLF before the edit, and it still parses.
        assert not re.search(rb"(?<!\r)\n", path.read_bytes())
        self._w._frontmatter(path)
        return path

    @staticmethod
    def _assert_all_crlf(path: Path) -> None:
        data = path.read_bytes()
        bare = [
            data[max(0, m.start() - 30): m.start() + 1]
            for m in re.finditer(rb"(?<!\r)\n", data)
        ]
        assert not bare, (
            f"{len(bare)} bare LF line ending(s) in a CRLF file, first near: "
            f"{bare[:3]}"
        )
        assert b"\r\r\n" not in data, data
        assert data.endswith(b"\r\n"), data[-40:]

    @staticmethod
    def _front_lines(front: str) -> list[str]:
        return [line.strip() for line in front.split("\n")]

    # -- 1. a comment line inside a block list --------------------------------

    @pytest.mark.parametrize(
        "assignee_block",
        [
            pytest.param("assignee:\n  - a\n# c\n  - b\n", id="column0-comment"),
            pytest.param("assignee:\n  - a\n  # c\n  - b\n", id="indented-comment"),
            pytest.param("assignee:\n- a\n# c\n- b\n", id="column0-items-column0-comment"),
        ],
    )
    def test_move_assign_consumes_comment_inside_block_list(
        self, temp_repo, software_config, monkeypatch, assignee_block,
    ):
        """`move -a carol` over a block list with a comment line inside it yields carol."""
        monkeypatch.chdir(temp_repo)
        runner = CliRunner()
        path = self._w._setup(
            temp_repo, runner, f"status: backlog\npriority: medium\n{assignee_block}",
        )
        assert self._w._frontmatter(path)["assignee"] == ["a", "b"]

        self._w._run(runner, ["move", "FEAT-001", "ready", "-a", "carol", "--no-commit"])

        fm = self._w._frontmatter(path)
        assert fm["assignee"] == "carol"
        assert fm["status"] == "ready"
        front, _ = self._w._split(path)
        lines = self._front_lines(front)
        assert "- a" not in lines, front
        assert "- b" not in lines, front
        assert self._w._show(runner)["assignee"] == "carol"
        assert self._w._listed_for(runner, "carol") == ["FEAT-001"]
        self._w._assert_tail_and_body_intact(path)

    @pytest.mark.parametrize(
        "assignee_block",
        [
            pytest.param("assignee: alice\n", id="after-single-line-value"),
            pytest.param("assignee:\n  - a\n  - b\n", id="after-block-list"),
        ],
    )
    def test_control_top_level_comment_between_keys_kept(
        self, temp_repo, software_config, monkeypatch, assignee_block,
    ):
        """A column-0 comment followed by the next KEY is not part of the value: kept."""
        monkeypatch.chdir(temp_repo)
        runner = CliRunner()
        path = self._w._setup(
            temp_repo, runner,
            f"status: backlog\npriority: medium\n{assignee_block}# about created\n",
        )

        self._w._run(runner, ["move", "FEAT-001", "ready", "-a", "carol", "--no-commit"])

        front, _ = self._w._split(path)
        assert "\nassignee: carol\n# about created\ncreated: 2026-09-24\n" in (
            f"\n{front}\n"
        ), front
        assert self._w._frontmatter(path)["assignee"] == "carol"
        self._w._assert_tail_and_body_intact(path)

    # -- 2. CRLF files keep CRLF ---------------------------------------------

    def test_crlf_move_updates_status_and_assignee(
        self, temp_repo, software_config, monkeypatch,
    ):
        """`move ready -a carol` on a CRLF file replaces both values; no bare LF appears."""
        monkeypatch.chdir(temp_repo)
        runner = CliRunner()
        path = self._setup_crlf(
            temp_repo, runner, "status: backlog\npriority: medium\nassignee: alice\n",
        )

        self._w._run(runner, ["move", "FEAT-001", "ready", "-a", "carol", "--no-commit"])

        self._assert_all_crlf(path)
        fm = self._w._frontmatter(path)
        assert fm["assignee"] == "carol"
        assert fm["status"] == "ready"
        assert fm["priority"] == "medium"
        assert self._w._show(runner)["assignee"] == "carol"
        self._w._assert_tail_and_body_intact(path)

    def test_crlf_move_replaces_block_list_assignee(
        self, temp_repo, software_config, monkeypatch,
    ):
        """Replacing a multi-line (block list) value in a CRLF file keeps CRLF."""
        monkeypatch.chdir(temp_repo)
        runner = CliRunner()
        path = self._setup_crlf(
            temp_repo, runner,
            "status: backlog\npriority: medium\nassignee:\n  - alice\n  - bob\n",
        )

        self._w._run(runner, ["move", "FEAT-001", "ready", "-a", "carol", "--no-commit"])

        self._assert_all_crlf(path)
        fm = self._w._frontmatter(path)
        assert fm["assignee"] == "carol"
        front, _ = self._w._split(path)
        assert "alice" not in front and "bob" not in front, front
        self._w._assert_tail_and_body_intact(path)

    def test_crlf_move_appends_missing_assignee(
        self, temp_repo, software_config, monkeypatch,
    ):
        """`move -a carol` on a CRLF file with no `assignee:` appends it with CRLF."""
        monkeypatch.chdir(temp_repo)
        runner = CliRunner()
        path = self._setup_crlf(temp_repo, runner, "status: backlog\npriority: medium\n")

        self._w._run(runner, ["move", "FEAT-001", "ready", "-a", "carol", "--no-commit"])

        self._assert_all_crlf(path)
        fm = self._w._frontmatter(path)
        assert fm["assignee"] == "carol"
        assert fm["status"] == "ready"
        assert fm["tags"] == ["one", "two"]
        assert self._w._show(runner)["assignee"] == "carol"
        assert self._w._split(path)[1].startswith(self._BODY)

    def test_crlf_rank_appends_missing_priority_rank(
        self, temp_repo, software_config, monkeypatch,
    ):
        """`rank` on a CRLF file with no `priority_rank:` appends it with CRLF."""
        monkeypatch.chdir(temp_repo)
        runner = CliRunner()
        path = self._setup_crlf(
            temp_repo, runner, "status: backlog\npriority: medium\nassignee: alice\n",
        )

        self._w._run(runner, ["rank", "FEAT-001", "3", "--no-commit"])

        self._assert_all_crlf(path)
        fm = self._w._frontmatter(path)
        assert fm["priority_rank"] == 3
        assert fm["tags"] == ["one", "two"]
        assert self._w._show(runner)["priority_rank"] == 3
        assert self._w._split(path)[1] == self._BODY

    def test_crlf_rank_updates_existing_fields(
        self, temp_repo, software_config, monkeypatch,
    ):
        """`rank --summary` replacing existing single- and multi-line values keeps CRLF."""
        monkeypatch.chdir(temp_repo)
        runner = CliRunner()
        path = self._setup_crlf(
            temp_repo, runner,
            "status: backlog\npriority: medium\nassignee: alice\n"
            "priority_rank: 5\nvalue_summary: >-\n  old value\n  spanning lines\n",
        )

        self._w._run(
            runner, ["rank", "FEAT-001", "2", "--summary", "new value", "--no-commit"],
        )

        self._assert_all_crlf(path)
        fm = self._w._frontmatter(path)
        assert fm["priority_rank"] == 2
        assert fm["value_summary"] == "new value"
        self._w._assert_tail_and_body_intact(path)

    def test_lone_cr_between_keys_does_not_swallow_next_key(
        self, temp_repo, software_config, monkeypatch,
    ):
        """`assignee: alice\\rpriority: high` is two keys; editing one keeps the other."""
        monkeypatch.chdir(temp_repo)
        runner = CliRunner()
        middle = "status: backlog\nassignee: alice\rpriority: high\n"
        path = self._w._setup(temp_repo, runner, "status: backlog\npriority: medium\n")
        path.write_bytes(self._lf_text(middle).encode())
        # Sanity: the lone CR really is in the file, and it reads as two keys.
        assert b"alice\rpriority" in path.read_bytes()
        fm = self._w._frontmatter(path)
        assert fm["assignee"] == "alice"
        assert fm["priority"] == "high"
        assert self._w._show(runner)["priority"] == "high"

        self._w._run(runner, ["move", "FEAT-001", "ready", "-a", "carol", "--no-commit"])

        fm = self._w._frontmatter(path)
        assert fm["assignee"] == "carol"
        assert fm.get("priority") == "high", f"priority lost: {path.read_bytes()!r}"
        assert fm["status"] == "ready"
        data = self._w._show(runner)
        assert data["assignee"] == "carol"
        assert data["priority"] == "high"
        self._w._assert_tail_and_body_intact(path)

    # -- negative controls: LF files stay LF, byte-for-byte ------------------

    def test_control_lf_rank_update_changes_only_edited_line(
        self, temp_repo, software_config, monkeypatch,
    ):
        """On an LF file, `rank` changes exactly the priority_rank line, nothing else."""
        monkeypatch.chdir(temp_repo)
        runner = CliRunner()
        middle = "status: backlog\npriority: medium\nassignee: alice\npriority_rank: 5\n"
        path = self._w._setup(temp_repo, runner, middle)
        before = self._lf_text(middle).encode()
        assert path.read_bytes() == before

        self._w._run(runner, ["rank", "FEAT-001", "3", "--no-commit"])

        assert path.read_bytes() == before.replace(
            b"\npriority_rank: 5\n", b"\npriority_rank: 3\n",
        )

    def test_control_lf_rank_append_adds_only_one_line(
        self, temp_repo, software_config, monkeypatch,
    ):
        """On an LF file, `rank` appending priority_rank adds one LF line before `---`."""
        monkeypatch.chdir(temp_repo)
        runner = CliRunner()
        middle = "status: backlog\npriority: medium\nassignee: alice\n"
        path = self._w._setup(temp_repo, runner, middle)

        self._w._run(runner, ["rank", "FEAT-001", "3", "--no-commit"])

        expected = self._lf_text(middle).replace(
            "  - two\n---\n", "  - two\npriority_rank: 3\n---\n", 1,
        )
        assert path.read_bytes() == expected.encode()

    def test_control_lf_move_introduces_no_cr(
        self, temp_repo, software_config, monkeypatch,
    ):
        """On an LF file, `move -a` writes no CR anywhere and keeps the frontmatter exact."""
        monkeypatch.chdir(temp_repo)
        runner = CliRunner()
        path = self._w._setup(
            temp_repo, runner, "status: backlog\npriority: medium\nassignee: alice\n",
        )

        self._w._run(runner, ["move", "FEAT-001", "ready", "-a", "carol", "--no-commit"])

        data = path.read_bytes()
        assert b"\r" not in data
        expected_head = self._lf_text(
            "status: ready\npriority: medium\nassignee: carol\n",
        ).encode()
        assert data.startswith(expected_head), data


# ---------------------------------------------------------------------------
# Issue #113 — each item type goes into its own named folder, and the board
# always scans the type folders it writes into
# ---------------------------------------------------------------------------


def _v1_yaml(theme: str, root: str, scan_paths: list[str]) -> str:
    """A v1 single-board config with an explicit root and scan_paths list."""
    lines = [
        "kanban:",
        f"  theme: {theme}",
        "  paths:",
        f"    root: {root}",
        "    scan_paths:",
    ]
    lines += [f"      - {p}" for p in scan_paths]
    return "\n".join(lines) + "\n"


def _create_and_list(runner: CliRunner, item_type: str, title: str = "probe") -> str:
    """`create <type> <title>` then `list --json`; return the list output."""
    result = runner.invoke(main, ["create", item_type, title], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    listed = runner.invoke(main, ["list", "--json"], catch_exceptions=False)
    assert listed.exit_code == 0, listed.output
    return listed.output


class TestTypeNamedFolders:
    """Placement: a type always goes to ``<board root>/<type folder>/`` (the theme's
    per-type folder, else the plural of the type), never nested inside another
    type's folder and never the bare root. Visibility: the board always scans the
    type folders it writes into, whatever ``scan_paths`` says (#113)."""

    # noesis-ship: nautical, root kanban-work/, scans only expeditions/tasks/bugs
    _NOESIS = _v1_yaml(
        "nautical",
        "kanban-work/",
        ["kanban-work/expeditions/", "kanban-work/tasks/", "kanban-work/bugs/"],
    )

    @pytest.fixture
    def init_runner(self, tmp_path, monkeypatch):
        """Factory: `yurtle-kanban init --theme <theme>` in a fresh git repo."""
        import subprocess

        from yurtle_kanban import config as config_mod

        subprocess.run(
            ["git", "init", "-b", "main"], cwd=tmp_path, capture_output=True, check=True,
        )
        config_mod._theme_cache.clear()
        monkeypatch.chdir(tmp_path)

        def _make(theme: str) -> CliRunner:
            runner = CliRunner()
            result = runner.invoke(main, ["init", "--theme", theme], catch_exceptions=False)
            assert result.exit_code == 0, result.output
            config_mod._theme_cache.clear()
            return runner

        yield _make
        config_mod._theme_cache.clear()

    # -- Do: placement + visibility -----------------------------------------

    @pytest.mark.parametrize(
        ("item_type", "prefix", "folder"),
        [
            ("voyage", "VOY", "voyages"),
            ("chore", "CHORE", "chores"),
            ("hazard", "HAZ", "hazards"),
            ("signal", "SIG", "signals"),
        ],
    )
    def test_noesis_unscanned_theme_type_goes_to_own_named_folder(
        self, tmp_path, board_runner, item_type, prefix, folder,
    ):
        """noesis-ship shape: a theme type whose folder isn't in scan_paths goes to
        kanban-work/<its folder>/, not nested in kanban-work/expeditions/, and is listed."""
        runner = board_runner(tmp_path, self._NOESIS)

        listed = _create_and_list(runner, item_type)

        files = _created_files(tmp_path, prefix)
        assert len(files) == 1, files
        assert files[0].parent == Path("kanban-work") / folder, (
            f"{item_type} created at {files[0]}, expected kanban-work/{folder}/"
        )
        assert not (tmp_path / "kanban-work" / "expeditions" / folder).exists()
        assert f"{prefix}-001" in listed, listed

    def test_default_init_software_type_not_in_theme_goes_to_plural_folder(
        self, tmp_path, init_runner,
    ):
        """#111 shape: default `init --theme software`, create expedition (not a
        software type) → kanban-work/expeditions/EXP-001-….md, not the bare root, and listed."""
        runner = init_runner("software")

        listed = _create_and_list(runner, "expedition")

        files = _created_files(tmp_path, "EXP")
        assert files == [Path("kanban-work/expeditions/EXP-001-probe.md")], files
        assert "EXP-001" in listed, listed

    def test_v1_config_scanning_one_type_folder_still_lists_other_types(
        self, tmp_path, board_runner,
    ):
        """Visibility regardless of scan_paths: root kanban-work/, scan only
        kanban-work/features/ → create bug lands in kanban-work/bugs/ and IS listed."""
        runner = board_runner(
            tmp_path, _v1_yaml("software", "kanban-work/", ["kanban-work/features/"]),
        )

        listed = _create_and_list(runner, "bug", "x")

        files = _created_files(tmp_path, "BUG")
        assert len(files) == 1, files
        assert files[0].parent == Path("kanban-work/bugs"), (
            f"bug created at {files[0]}, expected kanban-work/bugs/"
        )
        assert "BUG-001" in listed, listed

    @pytest.mark.parametrize(
        ("preset", "item_type", "prefix", "folder"),
        [
            ("nautical", "voyage", "VOY", "voyages"),
            ("nautical", "chore", "CHORE", "chores"),
            ("software", "feature", "FEAT", "features"),
            ("software", "bug", "BUG", "bugs"),
            ("software", "expedition", "EXP", "expeditions"),
        ],
    )
    def test_multi_board_kanban_work_path_types_go_to_named_folders(
        self, tmp_path, board_runner, preset, item_type, prefix, folder,
    ):
        """Multi-board: a board with path kanban-work/ → each type in
        kanban-work/<type folder>/ (theme's, else the plural), and listed."""
        config_yaml = (
            'version: "2.0"\n'
            "boards:\n"
            "  - name: main\n"
            f"    preset: {preset}\n"
            "    path: kanban-work/\n"
            "default_board: main\n"
        )
        runner = board_runner(tmp_path, config_yaml)

        listed = _create_and_list(runner, item_type)

        files = _created_files(tmp_path, prefix)
        assert len(files) == 1, files
        assert files[0].parent == Path("kanban-work") / folder, (
            f"{item_type} created at {files[0]}, expected kanban-work/{folder}/"
        )
        assert f"{prefix}-001" in listed, listed

    # -- Must stay true (controls) -------------------------------------------

    @pytest.mark.parametrize(
        ("theme", "item_type", "expected"),
        [
            ("software", "feature", "kanban-work/features/FEAT-001-probe.md"),
            ("software", "bug", "kanban-work/bugs/BUG-001-probe.md"),
            ("nautical", "expedition", "kanban-work/expeditions/EXP-001-probe.md"),
            ("nautical", "voyage", "kanban-work/voyages/VOY-001-probe.md"),
            ("hdd", "hypothesis", "research/hypotheses/H-001-probe.md"),
            ("hdd", "idea", "research/ideas/IDEA-R-001-probe.md"),
        ],
    )
    def test_control_default_init_placement_unchanged(
        self, tmp_path, init_runner, theme, item_type, expected,
    ):
        """Control: a default `init` board places and lists theme types exactly as today."""
        runner = init_runner(theme)

        listed = _create_and_list(runner, item_type)

        created = sorted(
            p.relative_to(tmp_path)
            for p in tmp_path.rglob("*-001-probe.md")
            if ".kanban" not in p.relative_to(tmp_path).parts
        )
        assert created == [Path(expected)], created
        assert Path(expected).name.split("-probe")[0] in listed, listed

    def test_control_board_scanning_kanban_work_places_voyage_in_voyages(
        self, tmp_path, board_runner,
    ):
        """Control: a nautical board scanning kanban-work/ puts voyages in kanban-work/voyages/."""
        runner = board_runner(tmp_path, _single_board_yaml("nautical", "kanban-work/"))

        listed = _create_and_list(runner, "voyage")

        assert _created_files(tmp_path, "VOY") == [
            Path("kanban-work/voyages/VOY-001-probe.md")
        ]
        assert "VOY-001" in listed, listed

    @pytest.mark.parametrize(
        ("item_type", "prefix", "folder"),
        [
            ("feature", "FEAT", "features"),
            ("bug", "BUG", "bugs"),
            ("task", "TASK", "tasks"),
        ],
    )
    def test_control_carclaw_like_v1_unchanged(
        self, tmp_path, board_runner, item_type, prefix, folder,
    ):
        """Control: carclaw-like v1 (root kanban-work, software, scan_paths under it)."""
        runner = board_runner(
            tmp_path,
            _v1_yaml(
                "software",
                "kanban-work",
                ["kanban-work/features/", "kanban-work/bugs/", "kanban-work/tasks/"],
            ),
        )

        listed = _create_and_list(runner, item_type)

        assert _created_files(tmp_path, prefix) == [
            Path(f"kanban-work/{folder}/{prefix}-001-probe.md")
        ]
        assert f"{prefix}-001" in listed, listed

    def test_control_noesis_scanned_types_unchanged(self, tmp_path, board_runner):
        """Control: noesis-ship's scanned expeditions/ keep placing there, and are listed."""
        runner = board_runner(tmp_path, self._NOESIS)

        listed = _create_and_list(runner, "expedition")

        assert _created_files(tmp_path, "EXP") == [
            Path("kanban-work/expeditions/EXP-001-probe.md")
        ]
        assert "EXP-001" in listed, listed

    # -- Must stay true: absolute root (review of PR #133) -------------------

    @staticmethod
    def _abs_root_yaml(root: Path) -> str:
        """The review repro: software theme, an absolute root, no scan_paths."""
        return (
            "kanban:\n"
            "  theme: software\n"
            "  paths:\n"
            f"    root: {root}\n"
        )

    def test_absolute_root_outside_repo_list_does_not_crash(self, tmp_path, board_runner):
        """An absolute root outside the repo: `list` exits 0 instead of raising
        (on main it prints 'No work items found.')."""
        repo = tmp_path / "repo"
        repo.mkdir()
        outside = tmp_path / "abs"
        outside.mkdir()
        runner = board_runner(repo, self._abs_root_yaml(outside))

        result = runner.invoke(main, ["list"])

        assert result.exception is None, repr(result.exception)
        assert result.exit_code == 0, result.output

    def test_absolute_root_inside_repo_lists_created_item(self, tmp_path, board_runner):
        """An absolute root inside the repo: `list` exits 0, and a created feature is listed."""
        inside = tmp_path / "abs"
        inside.mkdir()
        runner = board_runner(tmp_path, self._abs_root_yaml(inside))

        result = runner.invoke(main, ["list"])
        assert result.exception is None, repr(result.exception)
        assert result.exit_code == 0, result.output

        created = runner.invoke(main, ["create", "feature", "probe"])
        assert created.exception is None, repr(created.exception)
        assert created.exit_code == 0, created.output

        listed = runner.invoke(main, ["list", "--json"])
        assert listed.exception is None, repr(listed.exception)
        assert listed.exit_code == 0, listed.output
        assert "FEAT-001" in listed.output, listed.output


class TestFrontmatterOpeningLineComment:
    """An opening `---` line carrying a YAML comment still opens the frontmatter (#116)."""

    # Only openers that YAML itself reads as a document marker + comment.
    # (`---#c` is not one: yaml.safe_load rejects it, so it is not pinned here.)
    OPENERS = ["--- # generated", "---   # generated  ", "--- #"]

    _BODY = "\n# probe\n\nIntro text.\n\n---\n\nnotes after a rule\n"

    @staticmethod
    def _run(runner: CliRunner, args: list[str]):
        result = runner.invoke(main, args)
        assert result.exit_code == 0, (
            f"{args} exited {result.exit_code}: {result.exception!r}\n{result.output}"
        )
        return result

    @staticmethod
    def _path(repo: Path) -> Path:
        return repo / "kanban-work" / "features" / "FEAT-001-probe.md"

    def _write(self, repo: Path, opener: str, closer: str = "---") -> Path:
        path = self._path(repo)
        path.write_text(
            f"{opener}\n"
            "id: FEAT-001\n"
            'title: "probe"\n'
            "type: feature\n"
            "status: backlog\n"
            f"{closer}\n" + self._BODY
        )
        return path

    @staticmethod
    def _split(path: Path, closer: str = "---") -> tuple[str, str, str]:
        """(opening line, frontmatter text, body) split on the first `closer` line."""
        lines = path.read_text().split("\n")
        close = lines.index(closer, 1)
        return lines[0], "\n".join(lines[1:close]), "\n".join(lines[close + 1:])

    def _list(self, runner: CliRunner) -> list[dict]:
        out = self._run(runner, ["list", "--json"]).output
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            # An empty board prints a plain message ("No work items found.").
            return []

    @pytest.mark.parametrize("opener", OPENERS)
    def test_opener_is_valid_yaml_document_marker(self, opener):
        """Sanity: YAML accepts each opener as `---` + comment."""
        assert yaml.safe_load(f"{opener}\nid: FEAT-001\n") == {"id": "FEAT-001"}

    @pytest.mark.parametrize("opener", OPENERS)
    def test_list_shows_item(self, temp_repo, software_config, monkeypatch, opener):
        """The issue's repro: the item is listed with its id, title and status."""
        monkeypatch.chdir(temp_repo)
        self._write(temp_repo, opener)
        items = self._list(CliRunner())
        assert [(i["id"], i["title"], i["status"]) for i in items] == [
            ("FEAT-001", "probe", "backlog")
        ], items

    @pytest.mark.parametrize("opener", OPENERS)
    def test_service_scan_finds_item(self, temp_repo, software_config, opener):
        self._write(temp_repo, opener)
        svc = KanbanService(software_config, temp_repo)
        svc.scan()
        item = svc.get_item("FEAT-001")
        assert item is not None
        assert item.title == "probe"
        assert item.status == WorkItemStatus.BACKLOG

    @pytest.mark.parametrize("opener", OPENERS)
    def test_show_works(self, temp_repo, software_config, monkeypatch, opener):
        monkeypatch.chdir(temp_repo)
        self._write(temp_repo, opener)
        data = json.loads(self._run(CliRunner(), ["show", "FEAT-001", "--json"]).output)
        assert data["id"] == "FEAT-001"
        assert data["title"] == "probe"
        assert data["status"] == "backlog"

    @pytest.mark.parametrize("opener", OPENERS)
    def test_move_assign_updates_frontmatter_and_keeps_comment(
        self, temp_repo, software_config, monkeypatch, opener,
    ):
        """The writer accepts the same file: status + assignee land in the frontmatter."""
        monkeypatch.chdir(temp_repo)
        path = self._write(temp_repo, opener)
        runner = CliRunner()
        self._run(runner, ["move", "FEAT-001", "ready", "-a", "carol", "--no-commit"])

        first, front, body = self._split(path)
        assert first == opener, path.read_text()
        fm = yaml.safe_load(front)
        assert fm["status"] == "ready", path.read_text()
        assert fm["assignee"] == "carol", path.read_text()
        assert fm["id"] == "FEAT-001"
        assert body.startswith(self._BODY), body
        # And the whole file still reads as that YAML document.
        assert yaml.safe_load(f"{first}\n{front}\n")["assignee"] == "carol"

        items = self._list(runner)
        assert [(i["id"], i["status"], i["assignee"]) for i in items] == [
            ("FEAT-001", "ready", "carol")
        ], items

    def test_commented_opener_and_commented_closer(
        self, temp_repo, software_config, monkeypatch,
    ):
        """Both lines decorated: `--- # generated` … `--- # end`."""
        monkeypatch.chdir(temp_repo)
        path = self._write(temp_repo, "--- # generated", closer="--- # end")
        runner = CliRunner()
        assert [i["id"] for i in self._list(runner)] == ["FEAT-001"]
        self._run(runner, ["move", "FEAT-001", "ready", "-a", "carol", "--no-commit"])
        first, front, body = self._split(path, closer="--- # end")
        assert first == "--- # generated"
        fm = yaml.safe_load(front)
        assert (fm["status"], fm["assignee"]) == ("ready", "carol"), path.read_text()
        assert "assignee" not in body
        assert body.startswith(self._BODY), body

    # --- Round 2: YAML-strict openers and a performance bound -----------

    @pytest.mark.parametrize("opener", ["---\t# x", "---#x"])
    def test_opener_yaml_rejects_is_not_frontmatter(
        self, temp_repo, software_config, monkeypatch, opener,
    ):
        """Openers YAML itself rejects (tab before `#`, no space) are not frontmatter."""
        with pytest.raises(yaml.YAMLError):
            list(yaml.safe_load_all(f"{opener}\nid: FEAT-001\n"))
        monkeypatch.chdir(temp_repo)
        self._write(temp_repo, opener)
        assert self._list(CliRunner()) == []

    @pytest.mark.parametrize(
        "content",
        [
            "--- #" + " " * 64_000 + "x",
            "--- # c" + " " * 1000 + "\n" + "line\n" * 10_000,
        ],
        ids=["one-long-line", "long-line-then-10k-lines-no-closer"],
    )
    def test_unclosed_commented_opener_is_fast(
        self, temp_repo, software_config, content,
    ):
        """A pathological unclosed opener must not trigger regex backtracking."""
        import time

        svc = KanbanService(software_config, temp_repo)
        start = time.perf_counter()
        split = svc._split_frontmatter(content)
        parsed = svc._parse_frontmatter(content)
        elapsed = time.perf_counter() - start
        assert split is None and parsed is None
        assert elapsed < 0.5, f"frontmatter split took {elapsed:.2f}s"

    def test_unclosed_commented_opener_list_is_fast(
        self, temp_repo, software_config, monkeypatch,
    ):
        """Same bound end to end: the file sits under a scan path and `list` runs."""
        import time

        monkeypatch.chdir(temp_repo)
        (temp_repo / "kanban-work" / "features" / "FEAT-001-probe.md").write_text(
            "--- #" + " " * 64_000 + "x"
        )
        runner = CliRunner()
        start = time.perf_counter()
        items = self._list(runner)
        elapsed = time.perf_counter() - start
        assert items == []
        assert elapsed < 0.5 + self._baseline_list(runner), (
            f"list took {elapsed:.2f}s"
        )

    def _baseline_list(self, runner: CliRunner) -> float:
        """Time of `list` on the same board with the probe file emptied."""
        import time

        path = self._path(Path.cwd())
        saved = path.read_text()
        path.write_text("")
        try:
            start = time.perf_counter()
            self._list(runner)
            return time.perf_counter() - start
        finally:
            path.write_text(saved)

    # --- Negative controls (already green) --------------------------------

    def test_control_plain_opener_list_show_move(
        self, temp_repo, software_config, monkeypatch,
    ):
        """A normal `---` file behaves exactly as before."""
        monkeypatch.chdir(temp_repo)
        path = self._write(temp_repo, "---")
        runner = CliRunner()
        assert [(i["id"], i["title"], i["status"]) for i in self._list(runner)] == [
            ("FEAT-001", "probe", "backlog")
        ]
        data = json.loads(self._run(runner, ["show", "FEAT-001", "--json"]).output)
        assert data["id"] == "FEAT-001"
        self._run(runner, ["move", "FEAT-001", "ready", "-a", "carol", "--no-commit"])
        first, front, body = self._split(path)
        assert first == "---"
        fm = yaml.safe_load(front)
        assert (fm["status"], fm["assignee"]) == ("ready", "carol")
        assert body.startswith(self._BODY), body

    def test_control_body_rule_does_not_close_early(
        self, temp_repo, software_config, monkeypatch,
    ):
        """A `---` rule in the body is body text, not a frontmatter boundary."""
        monkeypatch.chdir(temp_repo)
        path = self._write(temp_repo, "---")
        self._run(CliRunner(), ["move", "FEAT-001", "ready", "-a", "carol", "--no-commit"])
        _, front, body = self._split(path)
        assert "assignee: carol" in front
        assert "assignee" not in body
        assert body.startswith(self._BODY), body
        assert "\n---\n\nnotes after a rule\n" in body

    def test_control_commented_closer_still_closes(
        self, temp_repo, software_config, monkeypatch,
    ):
        """`--- # end` as the closing line still closes (#101)."""
        monkeypatch.chdir(temp_repo)
        path = self._write(temp_repo, "---", closer="--- # end")
        runner = CliRunner()
        assert [i["id"] for i in self._list(runner)] == ["FEAT-001"]
        self._run(runner, ["move", "FEAT-001", "ready", "-a", "carol", "--no-commit"])
        _, front, body = self._split(path, closer="--- # end")
        fm = yaml.safe_load(front)
        assert (fm["status"], fm["assignee"]) == ("ready", "carol")
        assert "assignee" not in body
        assert body.startswith(self._BODY), body


# ---------------------------------------------------------------------------
# Issue #120 — kb:by in the status-change Turtle is an escaped string literal
# ---------------------------------------------------------------------------


class TestStatusChangeTurtleEscaping:
    """A status change's kb:by literal is escaped per Turtle string rules, so
    any agent name keeps the item's yurtle block valid and reads back exactly
    (#120)."""

    KB = "https://yurtle.dev/kanban/"

    @staticmethod
    def _run(runner: CliRunner, args: list[str]):
        """Invoke the CLI; a crash fails as an assertion, not a raw exception."""
        result = runner.invoke(main, args)
        assert result.exit_code == 0, (
            f"{args} exited {result.exit_code}: {result.exception!r}\n{result.output}"
        )
        return result

    @staticmethod
    def _item_file(repo: Path) -> Path:
        files = list((repo / "kanban-work" / "features").glob("FEAT-001*.md"))
        assert len(files) == 1, files
        return files[0]

    @staticmethod
    def _status_block(path: Path) -> str:
        """The ```yurtle block holding the kb:statusChange history."""
        import re

        text = path.read_text()
        blocks = [
            b for b in re.findall(r"```yurtle\n(.*?)```", text, re.DOTALL)
            if "kb:statusChange" in b
        ]
        assert len(blocks) == 1, f"expected one statusChange block:\n{text}"
        return blocks[0]

    def _graph(self, path: Path):
        from rdflib import Graph

        block = self._status_block(path)
        try:
            return Graph().parse(
                data=block, format="turtle", publicID=path.resolve().as_uri(),
            )
        except Exception as exc:  # rdflib raises BadSyntax (not a ValueError)
            pytest.fail(
                f"status-change yurtle block is not valid Turtle: {exc}\n{block}"
            )

    def _changes(self, path: Path) -> tuple:
        """The status-change nodes of the item's graph."""
        from rdflib import URIRef

        g = self._graph(path)
        return g, list(g.objects(None, URIRef(self.KB + "statusChange")))

    def _by_values(self, path: Path) -> list[str]:
        from rdflib import Literal, URIRef

        g, nodes = self._changes(path)
        values = []
        for node in nodes:
            bys = list(g.objects(node, URIRef(self.KB + "by")))
            assert len(bys) == 1, f"node {node} has kb:by {bys}"
            assert isinstance(bys[0], Literal), bys[0]
            values.append(str(bys[0]))
        return values

    def _two_moves(self, runner: CliRunner, agent: str) -> None:
        self._run(runner, ["create", "feature", "probe"])
        self._run(runner, ["move", "FEAT-001", "ready", "-a", agent, "--no-commit"])
        self._run(
            runner,
            ["move", "FEAT-001", "in_progress", "-a", agent, "--no-commit", "--force"],
        )

    # -- the bug: special agent names ----------------------------------------

    @pytest.mark.parametrize(
        "agent",
        [
            pytest.param('x"y', id="double-quote"),
            pytest.param("a\\b", id="backslash"),
            pytest.param('say "hi"\\', id="quote-and-trailing-backslash"),
            pytest.param("x\ny", id="newline"),
            pytest.param("x\ry", id="carriage-return"),
        ],
    )
    def test_special_agent_block_parses_and_kb_by_exact(
        self, temp_repo, software_config, monkeypatch, agent,
    ):
        """After two `move -a <agent>`, the block parses and both kb:by == agent."""
        monkeypatch.chdir(temp_repo)
        runner = CliRunner()
        self._two_moves(runner, agent)

        assert self._by_values(self._item_file(temp_repo)) == [agent, agent]

    def test_injection_attempt_adds_no_triples(
        self, temp_repo, software_config, monkeypatch,
    ):
        """An agent shaped like Turtle stays one literal: no extra kb:status."""
        from rdflib import URIRef

        agent = 'evil" ; kb:status kb:done ; kb:by "x'
        monkeypatch.chdir(temp_repo)
        runner = CliRunner()
        self._two_moves(runner, agent)

        path = self._item_file(temp_repo)
        g, nodes = self._changes(path)
        assert len(nodes) == 2, nodes
        status = URIRef(self.KB + "status")
        for node in nodes:
            assert len(list(g.objects(node, status))) == 1, list(g.objects(node, status))
        assert (None, status, URIRef(self.KB + "done")) not in g
        assert self._by_values(path) == [agent, agent]

    def test_newline_injection_adds_no_triples(
        self, temp_repo, software_config, monkeypatch,
    ):
        """A newline-bearing agent can't smuggle a new statement into the graph."""
        from rdflib import URIRef

        agent = 'x" ;\n] .\n<> kb:status kb:done .\n<> kb:statusChange [\n kb:by "y'
        monkeypatch.chdir(temp_repo)
        runner = CliRunner()
        self._two_moves(runner, agent)

        path = self._item_file(temp_repo)
        g, nodes = self._changes(path)
        assert len(nodes) == 2, nodes
        assert (None, URIRef(self.KB + "status"), URIRef(self.KB + "done")) not in g
        assert self._by_values(path) == [agent, agent]

    @pytest.mark.parametrize(
        "agent",
        [
            pytest.param('x"y', id="double-quote"),
            pytest.param("a\\b", id="backslash"),
            pytest.param('evil" ; kb:status kb:done ; kb:by "x', id="injection"),
        ],
    )
    def test_special_agent_item_still_loads_and_assignee_round_trips(
        self, temp_repo, software_config, monkeypatch, agent,
    ):
        """`show --json` still works and frontmatter assignee == agent (#104)."""
        monkeypatch.chdir(temp_repo)
        runner = CliRunner()
        self._two_moves(runner, agent)

        shown = self._run(runner, ["show", "FEAT-001", "--json"])
        data = json.loads(shown.output)
        assert data["id"] == "FEAT-001"
        assert data["assignee"] == agent

        path = self._item_file(temp_repo)
        lines = path.read_text().split("\n")
        fm = yaml.safe_load("\n".join(lines[1:lines.index("---", 1)]))
        assert fm["assignee"] == agent
        self._graph(path)  # and the block still parses

    def test_git_user_name_with_quote_is_escaped(
        self, temp_repo, software_config, monkeypatch,
    ):
        """With no -a, kb:by is the git user.name — escaped just the same."""
        import subprocess

        name = 'Ann "Nan" O\\Brien'
        subprocess.run(
            ["git", "config", "user.name", name],
            cwd=temp_repo, capture_output=True, check=True,
        )
        monkeypatch.chdir(temp_repo)
        runner = CliRunner()
        self._run(runner, ["create", "feature", "probe"])
        self._run(runner, ["move", "FEAT-001", "ready", "--no-commit"])
        self._run(runner, ["move", "FEAT-001", "in_progress", "--no-commit", "--force"])

        assert self._by_values(self._item_file(temp_repo)) == [name, name]

    @pytest.mark.parametrize(
        "agent",
        [
            pytest.param('x"y', id="double-quote"),
            pytest.param("a\\b", id="backslash"),
            pytest.param("x\ny", id="newline"),
            pytest.param("Claude-M5", id="plain-control"),
        ],
    )
    def test_status_history_reads_agent_back_exactly(
        self, temp_repo, software_config, monkeypatch, agent,
    ):
        """The service's own history reader sees by == agent for both moves."""
        monkeypatch.chdir(temp_repo)
        runner = CliRunner()
        self._two_moves(runner, agent)

        history = KanbanService(software_config, temp_repo).get_status_history(
            "FEAT-001",
        )
        assert [(h["status"], h["by"]) for h in history] == [
            ("ready", agent), ("in_progress", agent),
        ]

    # -- negative controls (must stay green) ---------------------------------

    def test_control_plain_agent_written_textually_unchanged(
        self, temp_repo, software_config, monkeypatch,
    ):
        """A plain agent is still written exactly `kb:by "Claude-M5" ;`."""
        monkeypatch.chdir(temp_repo)
        runner = CliRunner()
        self._two_moves(runner, "Claude-M5")

        path = self._item_file(temp_repo)
        block = self._status_block(path)
        assert block.count('    kb:by "Claude-M5" ;\n') == 2, block
        assert self._by_values(path) == ["Claude-M5", "Claude-M5"]

    def test_control_plain_git_user_written_textually_unchanged(
        self, temp_repo, software_config, monkeypatch,
    ):
        """With no -a, the fixture's git user `Test` is written plain."""
        monkeypatch.chdir(temp_repo)
        runner = CliRunner()
        self._run(runner, ["create", "feature", "probe"])
        self._run(runner, ["move", "FEAT-001", "ready", "--no-commit"])

        block = self._status_block(self._item_file(temp_repo))
        assert '    kb:by "Test" ;\n' in block, block

    @pytest.mark.parametrize(
        "uri",
        [
            pytest.param("https://evil.com> ; kb:status kb:backlog ; <x", id="angle"),
            pytest.param('https://x.com/"q', id="quote"),
            pytest.param("https://x.com/<a", id="lt"),
            pytest.param("https://x.com/a\nb", id="newline"),
        ],
    )
    def test_control_closed_by_still_rejects_bad_uri(
        self, temp_repo, software_config, monkeypatch, uri,
    ):
        """--closed-by keeps rejecting TTL-breaking URIs (not escaping them)."""
        monkeypatch.chdir(temp_repo)
        runner = CliRunner()
        self._run(runner, ["create", "feature", "probe"])
        path = self._item_file(temp_repo)
        before = path.read_text()

        result = runner.invoke(
            main,
            ["move", "FEAT-001", "ready", "--no-commit", "--closed-by", uri],
        )
        assert "disallowed characters" in result.output, result.output
        assert "kb:closedBy" not in path.read_text()
        assert path.read_text() == before

    def test_control_closed_by_plain_uri_still_written(
        self, temp_repo, software_config, monkeypatch,
    ):
        """A clean --closed-by URI is still recorded as kb:closedBy <uri>."""
        monkeypatch.chdir(temp_repo)
        runner = CliRunner()
        self._run(runner, ["create", "feature", "probe"])
        uri = "https://github.com/repo/pull/42"
        self._run(
            runner, ["move", "FEAT-001", "ready", "--no-commit", "--closed-by", uri],
        )
        path = self._item_file(temp_repo)
        assert f"kb:closedBy <{uri}> ;" in self._status_block(path)
        self._graph(path)


# ---------------------------------------------------------------------------
# Issue #121 -- every frontmatter value create writes reads back unchanged
# ---------------------------------------------------------------------------

_YAML_SPECIAL_TAGS = [
    pytest.param("team: core", id="colon-space"),
    pytest.param("yes", id="yes-bool"),
    pytest.param("null", id="null"),
    pytest.param("#core", id="leading-hash"),
    pytest.param("[x]", id="leading-bracket"),
    pytest.param("{x}", id="leading-brace"),
    pytest.param("&a", id="leading-ampersand"),
    pytest.param("*a", id="leading-star"),
    pytest.param("123", id="int"),
]

_ORDINARY_ITEM_FILE = (
    "---\n"
    "id: FEAT-001\n"
    'title: "Add dark mode"\n'
    "type: feature\n"
    "status: ready\n"
    "priority: high\n"
    "assignee: agent-x\n"
    "created: 2026-01-12\n"
    "tags: [backend, ui-polish]\n"
    "depends_on: [EXP-001]\n"
    "related: [FEAT-002, BUG-003]\n"
    "compute_requirement: gpu\n"
    "resolution: completed\n"
    "superseded_by: [FEAT-009]\n"
    "---\n"
    "\n"
    "# Add dark mode\n"
    "\n"
    "Body text."  # to_markdown writes no trailing newline
)


class TestAllFrontmatterValuesRoundTrip:
    """create --tags values read back as the same strings; ordinary files are unchanged (#121)."""

    @staticmethod
    def _run(runner: CliRunner, args: list[str]):
        """Invoke the CLI; a crash fails as an assertion, not a raw exception."""
        result = runner.invoke(main, args)
        assert result.exit_code == 0, (
            f"{args} exited {result.exit_code}: {result.exception!r}\n{result.output}"
        )
        return result

    @staticmethod
    def _item_file(repo: Path) -> Path:
        files = list((repo / "kanban-work" / "features").glob("FEAT-001*.md"))
        assert len(files) == 1, files
        return files[0]

    @staticmethod
    def _frontmatter_text(path: Path) -> str:
        lines = path.read_text().split("\n")
        assert lines[0] == "---"
        return "\n".join(lines[1:lines.index("---", 1)])

    def _assert_tags_read_back(self, runner: CliRunner, repo: Path, tags: list[str]) -> None:
        shown = self._run(runner, ["show", "FEAT-001", "--json"])
        data = json.loads(shown.output)
        assert data["id"] == "FEAT-001"
        assert data["tags"] == tags

        path = self._item_file(repo)
        try:
            fm = yaml.safe_load(self._frontmatter_text(path))
        except yaml.YAMLError as exc:
            pytest.fail(f"frontmatter is not valid YAML: {exc}\n{path.read_text()}")
        assert fm["tags"] == tags

    def test_issue_repro_create_tags_team_core(
        self, temp_repo, software_config, monkeypatch,
    ):
        """The issue's repro: create --tags "team: core" -> tags == ["team: core"]."""
        monkeypatch.chdir(temp_repo)
        runner = CliRunner()
        self._run(runner, ["create", "feature", "probe", "--tags", "team: core"])

        self._assert_tags_read_back(runner, temp_repo, ["team: core"])

    @pytest.mark.parametrize("value", _YAML_SPECIAL_TAGS)
    def test_create_special_tag_round_trips(
        self, temp_repo, software_config, monkeypatch, value,
    ):
        """create --tags "backend,<value>" reads back as two strings, value intact."""
        monkeypatch.chdir(temp_repo)
        runner = CliRunner()
        self._run(runner, ["create", "feature", "probe", "--tags", f"backend,{value}"])

        self._assert_tags_read_back(runner, temp_repo, ["backend", value])

    # -- must stay true (controls) -------------------------------------------

    def test_control_create_ordinary_tags_unquoted(
        self, temp_repo, software_config, monkeypatch,
    ):
        """Ordinary tags are written plain, and depends_on stays `[]`."""
        monkeypatch.chdir(temp_repo)
        runner = CliRunner()
        self._run(runner, ["create", "feature", "probe", "--tags", "backend,ui-polish"])

        front = self._frontmatter_text(self._item_file(temp_repo)).split("\n")
        assert "tags: [backend, ui-polish]" in front
        assert "depends_on: []" in front
        self._assert_tags_read_back(runner, temp_repo, ["backend", "ui-polish"])

    def test_control_existing_ordinary_file_rewrites_identically(
        self, temp_repo, software_config,
    ):
        """Parsing an existing ordinary file and re-writing it is byte-identical."""
        path = temp_repo / "kanban-work" / "features" / "FEAT-001-add-dark-mode.md"
        path.write_text(_ORDINARY_ITEM_FILE)
        svc = KanbanService(software_config, temp_repo)

        item = svc._parse_file(path)
        assert item is not None
        assert item.to_markdown() == _ORDINARY_ITEM_FILE


# --- #125: priority validated once in the service's write paths ------------


def _item_md_files(root: Path) -> set[Path]:
    return {p for p in root.rglob("*.md") if ".git" not in p.parts}


def _git_log(root: Path) -> str:
    import subprocess

    return subprocess.run(
        ["git", "log", "--oneline", "--all"], cwd=root, capture_output=True, text=True,
    ).stdout


_TEMPLATE_CONTENT = (
    "---\n"
    "id: FEAT-001\n"
    "title: Templated\n"
    "type: feature\n"
    "status: backlog\n"
    "priority: medium\n"
    "---\n"
    "\n"
    "# Templated\n"
    "\n"
    "```turtle\n"
    "@prefix kb: <https://yurtle.dev/kanban/> .\n"
    "\n"
    "<> kb:priority kb:medium .\n"
    "```\n"
)


class TestServicePriorityValidated:
    """KanbanService create/update validate priority against PRIORITIES and
    lowercase it, so every entry point (hooks, direct callers) shares the
    rule the CLI and MCP already apply (#125)."""

    VALID = ("critical", "high", "medium", "low")

    # --- create_item -------------------------------------------------------

    def test_create_item_rejects_unknown_priority(self, temp_repo, software_config):
        svc = KanbanService(software_config, temp_repo)
        before = _item_md_files(temp_repo)

        with pytest.raises(ValueError, match="urgent"):
            svc.create_item(WorkItemType.FEATURE, "probe", priority="urgent")

        assert _item_md_files(temp_repo) == before
        assert all(i.priority != "urgent" for i in svc.get_items())

    def test_create_item_uppercase_priority_is_lowercased(self, temp_repo, software_config):
        svc = KanbanService(software_config, temp_repo)

        item = svc.create_item(WorkItemType.FEATURE, "probe", priority="HIGH")

        assert item.priority == "high"
        assert "\npriority: high\n" in item.file_path.read_text()

    def test_create_item_with_template_content_rejects_unknown_priority(
        self, temp_repo, software_config,
    ):
        svc = KanbanService(software_config, temp_repo)
        before = _item_md_files(temp_repo)

        with pytest.raises(ValueError, match="urgent"):
            svc.create_item(
                WorkItemType.FEATURE, "Templated", priority="urgent",
                content=_TEMPLATE_CONTENT, item_id="FEAT-001",
            )

        assert _item_md_files(temp_repo) == before

    def test_create_item_with_template_content_lowercases_priority(
        self, temp_repo, software_config,
    ):
        """The _apply_priority path writes lowercase to frontmatter and triple."""
        svc = KanbanService(software_config, temp_repo)

        item = svc.create_item(
            WorkItemType.FEATURE, "Templated", priority="HIGH",
            content=_TEMPLATE_CONTENT, item_id="FEAT-001",
        )

        text = item.file_path.read_text()
        assert "\npriority: high\n" in text
        assert "kb:priority kb:high" in text
        assert "HIGH" not in text

    # --- create_item_and_push ---------------------------------------------

    def test_create_item_and_push_rejects_unknown_priority(self, temp_repo, software_config):
        svc = KanbanService(software_config, temp_repo)
        before_files = _item_md_files(temp_repo)
        before_log = _git_log(temp_repo)

        try:
            result = svc.create_item_and_push(
                WorkItemType.FEATURE, "probe", priority="urgent",
            )
        except ValueError as e:
            assert "urgent" in str(e)
        else:
            assert not result.get("success"), result

        assert _item_md_files(temp_repo) == before_files
        assert _git_log(temp_repo) == before_log

    def test_create_item_and_push_uppercase_priority_is_lowercased(
        self, temp_repo, software_config,
    ):
        svc = KanbanService(software_config, temp_repo)
        before = _item_md_files(temp_repo)

        result = svc.create_item_and_push(WorkItemType.FEATURE, "probe", priority="HIGH")

        assert result.get("success"), result
        new = sorted(_item_md_files(temp_repo) - before)
        assert len(new) == 1
        assert "\npriority: high\n" in new[0].read_text()

    def test_create_item_and_push_template_content_lowercases_priority(
        self, temp_repo, software_config,
    ):
        svc = KanbanService(software_config, temp_repo)
        before = _item_md_files(temp_repo)

        result = svc.create_item_and_push(
            WorkItemType.FEATURE, "Templated", priority="HIGH",
            content=_TEMPLATE_CONTENT, item_id="FEAT-001",
        )

        assert result.get("success"), result
        new = sorted(_item_md_files(temp_repo) - before)
        assert len(new) == 1
        text = new[0].read_text()
        assert "\npriority: high\n" in text
        assert "kb:priority kb:high" in text

    # --- update_item --------------------------------------------------------

    def _existing(self, temp_repo: Path, priority: str = "low") -> Path:
        path = temp_repo / "kanban-work" / "features" / "FEAT-001-Existing.md"
        path.write_text(
            f"---\nid: FEAT-001\ntitle: Existing\nstatus: backlog\npriority: {priority}\n---\n"
        )
        return path

    def test_update_item_rejects_unknown_priority(self, temp_repo, software_config):
        path = self._existing(temp_repo)
        svc = KanbanService(software_config, temp_repo)
        before_text = path.read_text()
        before_log = _git_log(temp_repo)

        with pytest.raises(ValueError, match="urgent"):
            svc.update_item("FEAT-001", priority="urgent")

        assert path.read_text() == before_text
        assert _git_log(temp_repo) == before_log
        assert svc.get_item("FEAT-001").priority == "low"

    def test_update_item_uppercase_priority_is_lowercased(self, temp_repo, software_config):
        path = self._existing(temp_repo)
        svc = KanbanService(software_config, temp_repo)

        item = svc.update_item("FEAT-001", priority="HIGH", commit=False)

        assert item.priority == "high"
        assert "\npriority: high\n" in path.read_text()

    # --- negative controls --------------------------------------------------

    @pytest.mark.parametrize("prio", VALID)
    def test_create_item_accepts_each_valid_priority(self, temp_repo, software_config, prio):
        svc = KanbanService(software_config, temp_repo)

        item = svc.create_item(WorkItemType.FEATURE, "probe", priority=prio)

        assert item.priority == prio
        assert f"\npriority: {prio}\n" in item.file_path.read_text()

    @pytest.mark.parametrize("prio", VALID)
    def test_update_item_accepts_each_valid_priority(self, temp_repo, software_config, prio):
        path = self._existing(temp_repo, priority="medium" if prio != "medium" else "low")
        svc = KanbanService(software_config, temp_repo)

        item = svc.update_item("FEAT-001", priority=prio, commit=False)

        assert item.priority == prio
        assert f"\npriority: {prio}\n" in path.read_text()

    def test_create_item_default_priority_is_medium(self, temp_repo, software_config):
        svc = KanbanService(software_config, temp_repo)

        item = svc.create_item(WorkItemType.FEATURE, "probe")

        assert item.priority == "medium"
        assert "\npriority: medium\n" in item.file_path.read_text()

    def test_update_item_priority_none_leaves_priority_unchanged(
        self, temp_repo, software_config,
    ):
        path = self._existing(temp_repo, priority="critical")
        svc = KanbanService(software_config, temp_repo)

        item = svc.update_item("FEAT-001", title="Renamed", priority=None, commit=False)

        assert item.priority == "critical"
        assert "\npriority: critical\n" in path.read_text()

    def test_update_item_other_field_on_legacy_priority_item_still_works(
        self, temp_repo, software_config,
    ):
        """Reads stay permissive: editing a legacy `P0` item's title is fine."""
        path = self._existing(temp_repo, priority="P0")
        svc = KanbanService(software_config, temp_repo)

        item = svc.update_item("FEAT-001", title="Renamed", commit=False)

        assert item.title == "Renamed"
        assert item.priority == "P0"
        assert "\npriority: P0\n" in path.read_text()

    @pytest.mark.parametrize(
        "legacy,score",
        [("P0", 50), ("HIGH", 50), ("normal", 50), ("strategic", 50), ("backlog", 10)],
    )
    def test_legacy_priorities_still_read(self, temp_repo, software_config, legacy, score):
        """Items already on disk with non-canonical priorities still load, list
        and score; `backlog` keeps its score of 10 (#125 triage)."""
        self._existing(temp_repo, priority=legacy)
        svc = KanbanService(software_config, temp_repo)

        item = svc.get_item("FEAT-001")
        assert item is not None
        assert item.priority == legacy
        assert item.priority_score == score
        assert [i.id for i in svc.get_items()] == ["FEAT-001"]

    @pytest.mark.parametrize("legacy", ["P0", "HIGH", "normal", "strategic"])
    def test_legacy_priorities_list_and_show_via_cli(
        self, temp_repo, software_config, monkeypatch, legacy,
    ):
        self._existing(temp_repo, priority=legacy)
        monkeypatch.chdir(temp_repo)
        runner = CliRunner()

        listed = runner.invoke(main, ["list", "--json"])
        assert listed.exit_code == 0, listed.output
        assert [d["id"] for d in json.loads(listed.output)] == ["FEAT-001"]

        shown = runner.invoke(main, ["show", "FEAT-001", "--json"])
        assert shown.exit_code == 0, shown.output
        assert json.loads(shown.output)["priority"] == legacy


class TestWarnOnUnparseableItems:
    """A scanned `.md` that starts with `---` but doesn't parse is reported on
    stderr by `list`/`board`, instead of vanishing silently; valid items, plain
    notes, templates and ignored paths are unaffected (#139)."""

    FEATURES = Path("kanban-work") / "features"

    # name -> (file content, reason substring the warning must carry, or None
    # when the issue doesn't name the reason text for that case)
    BROKEN = {
        "no-closing": (
            "---\nid: FEAT-002\ntitle: \"broken\"\ntype: feature\nstatus: backlog\n"
            "\n# broken\n\nNo closing delimiter anywhere.\n",
            "no closing ---",
        ),
        "invalid-yaml": (
            "---\nid: FEAT-002\ntitle: \"unterminated\ntype: feature\nstatus: backlog\n"
            "---\n\n# broken\n",
            "YAML error",
        ),
        "rejected-opener": (
            "---\t# x\nid: FEAT-002\ntitle: \"broken\"\ntype: feature\nstatus: backlog\n"
            "---\n\n# broken\n",
            None,
        ),
        "non-mapping": (
            "---\n- id: FEAT-002\n- title: broken\n---\n\n# broken\n",
            None,
        ),
    }

    @staticmethod
    def _write_valid(repo: Path) -> Path:
        path = repo / "kanban-work" / "features" / "FEAT-001-good.md"
        path.write_text(
            "---\n"
            "id: FEAT-001\n"
            'title: "good item"\n'
            "type: feature\n"
            "status: backlog\n"
            "---\n\n# good item\n"
        )
        return path

    @staticmethod
    def _invoke(args: list[str]):
        result = CliRunner().invoke(main, args)
        assert result.exit_code == 0, (
            f"{args} exited {result.exit_code}: {result.exception!r}\n"
            f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
        )
        return result

    @staticmethod
    def _lines_naming(text: str, rel: Path) -> list[str]:
        return [ln for ln in text.splitlines() if str(rel) in ln]

    def _assert_one_line_warning(self, result, rel: Path, reason: str | None) -> None:
        lines = self._lines_naming(result.stderr, rel)
        assert len(lines) == 1, (
            f"expected exactly one stderr line naming {rel}; stderr={result.stderr!r}"
        )
        line = lines[0]
        if reason is not None:
            assert reason in line, f"warning lacks reason {reason!r}: {line!r}"
        else:
            # Some reason beyond the path itself.
            rest = line.replace(str(rel), "")
            assert re.search(r"[A-Za-z]{3,}", rest), f"warning has no reason: {line!r}"
        assert not self._lines_naming(result.stdout, rel), (
            f"warning leaked onto stdout: {result.stdout!r}"
        )

    # ---- Expected: broken files are warned about -------------------------

    @pytest.mark.parametrize("case", list(BROKEN))
    def test_list_warns_on_stderr_and_shows_valid_items(
        self, temp_repo, software_config, monkeypatch, case,
    ):
        monkeypatch.chdir(temp_repo)
        self._write_valid(temp_repo)
        content, reason = self.BROKEN[case]
        rel = self.FEATURES / "FEAT-002-broken.md"
        (temp_repo / rel).write_text(content)

        result = self._invoke(["list"])

        assert "FEAT-001" in result.stdout, result.stdout
        self._assert_one_line_warning(result, rel, reason)

    @pytest.mark.parametrize("case", list(BROKEN))
    def test_board_warns_on_stderr_and_shows_valid_items(
        self, temp_repo, software_config, monkeypatch, case,
    ):
        monkeypatch.chdir(temp_repo)
        self._write_valid(temp_repo)
        content, reason = self.BROKEN[case]
        rel = self.FEATURES / "FEAT-002-broken.md"
        (temp_repo / rel).write_text(content)

        result = self._invoke(["board"])

        assert "FEAT-001" in result.stdout, result.stdout
        self._assert_one_line_warning(result, rel, reason)

    @pytest.mark.parametrize("case", list(BROKEN))
    def test_list_json_stdout_is_clean_and_warning_on_stderr(
        self, temp_repo, software_config, monkeypatch, case,
    ):
        monkeypatch.chdir(temp_repo)
        self._write_valid(temp_repo)
        content, reason = self.BROKEN[case]
        rel = self.FEATURES / "FEAT-002-broken.md"
        (temp_repo / rel).write_text(content)

        result = self._invoke(["list", "--json"])

        data = json.loads(result.stdout)
        assert [d["id"] for d in data] == ["FEAT-001"], data
        self._assert_one_line_warning(result, rel, reason)

    def test_each_broken_file_gets_its_own_warning(
        self, temp_repo, software_config, monkeypatch,
    ):
        monkeypatch.chdir(temp_repo)
        self._write_valid(temp_repo)
        rels = []
        for n, (content, _reason) in enumerate(self.BROKEN.values(), start=2):
            rel = self.FEATURES / f"FEAT-00{n}-broken.md"
            (temp_repo / rel).write_text(content)
            rels.append(rel)

        result = self._invoke(["list", "--json"])

        assert [d["id"] for d in json.loads(result.stdout)] == ["FEAT-001"]
        for rel in rels:
            assert len(self._lines_naming(result.stderr, rel)) == 1, (
                f"{rel}: stderr={result.stderr!r}"
            )

    # ---- Must stay true: silent cases ------------------------------------

    @pytest.mark.parametrize("args", [["list"], ["list", "--json"], ["board"]])
    def test_valid_items_only_prints_nothing_to_stderr(
        self, temp_repo, software_config, monkeypatch, args,
    ):
        monkeypatch.chdir(temp_repo)
        self._write_valid(temp_repo)

        result = self._invoke(args)

        assert "FEAT-001" in result.stdout
        assert result.stderr == "", result.stderr

    @pytest.mark.parametrize(
        "name,content",
        [
            ("notes.md", "# Notes\n\nSome plain notes.\n\n---\n\nmore\n"),
            (
                "KANBAN-BOARD.md",
                "# Kanban Board\n\n| ID | Title |\n|----|-------|\n| FEAT-001 | good item |\n",
            ),
            ("empty.md", ""),
        ],
    )
    @pytest.mark.parametrize("args", [["list"], ["list", "--json"], ["board"]])
    def test_plain_note_without_frontmatter_is_silent(
        self, temp_repo, software_config, monkeypatch, name, content, args,
    ):
        monkeypatch.chdir(temp_repo)
        self._write_valid(temp_repo)
        (temp_repo / self.FEATURES / name).write_text(content)

        result = self._invoke(args)

        assert "FEAT-001" in result.stdout
        assert result.stderr == "", result.stderr

    @pytest.mark.parametrize("case", list(BROKEN))
    @pytest.mark.parametrize(
        "rel",
        [
            FEATURES / "_TEMPLATE.md",
            FEATURES / "archive" / "FEAT-009-old.md",
            FEATURES / "templates" / "feature.md",
        ],
    )
    @pytest.mark.parametrize("args", [["list"], ["list", "--json"], ["board"]])
    def test_templates_and_ignored_paths_are_silent_even_if_malformed(
        self, temp_repo, software_config, monkeypatch, case, rel, args,
    ):
        monkeypatch.chdir(temp_repo)
        self._write_valid(temp_repo)
        path = temp_repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.BROKEN[case][0])

        result = self._invoke(args)

        assert "FEAT-001" in result.stdout
        assert result.stderr == "", result.stderr

    def test_valid_items_still_parse_alongside_broken_ones(
        self, temp_repo, software_config,
    ):
        """Service level: a broken file doesn't disturb the valid item."""
        self._write_valid(temp_repo)
        for n, (content, _reason) in enumerate(self.BROKEN.values(), start=2):
            (temp_repo / self.FEATURES / f"FEAT-00{n}-broken.md").write_text(content)
        svc = KanbanService(software_config, temp_repo)

        items = svc.scan()

        assert [i.id for i in items] == ["FEAT-001"]
        assert items[0].title == "good item"

    # ---- Must stay true: Yurtle docs with Turtle (not YAML) frontmatter ----

    TURTLE_FRONTMATTER = {
        "prefix": (
            "---\n"
            "@prefix paper: <https://nusy.dev/ontology/paper#> .\n"
            "@prefix dc: <http://purl.org/dc/terms/> .\n"
            "<> a paper:Paper ;\n"
            '    dc:title "A research note" .\n'
            "---\n\n# A research note\n\nBody text.\n"
        ),
        "base": (
            "---\n"
            "@base <https://nusy.dev/papers/> .\n"
            "@prefix paper: <https://nusy.dev/ontology/paper#> .\n"
            "<> a paper:Paper .\n"
            "---\n\n# Based note\n"
        ),
        "sparql-prefix": (
            "---\n"
            "PREFIX ex: <https://example.org/ns#>\n"
            '<> a ex:Doc ; ex:title "Sparql-style" .\n'
            "---\n\n# Sparql-style note\n"
        ),
    }

    @pytest.mark.parametrize("kind", list(TURTLE_FRONTMATTER))
    @pytest.mark.parametrize("args", [["list"], ["list", "--json"], ["board"]])
    def test_turtle_frontmatter_doc_is_silent(
        self, temp_repo, software_config, monkeypatch, kind, args,
    ):
        """A Yurtle document whose frontmatter is Turtle is legitimate, not broken."""
        monkeypatch.chdir(temp_repo)
        self._write_valid(temp_repo)
        (temp_repo / self.FEATURES / "research-note.md").write_text(
            self.TURTLE_FRONTMATTER[kind]
        )

        result = self._invoke(args)

        assert "FEAT-001" in result.stdout, result.stdout
        if args == ["list", "--json"]:
            assert [d["id"] for d in json.loads(result.stdout)] == ["FEAT-001"]
        assert result.stderr == "", result.stderr

    @pytest.mark.parametrize("args", [["list"], ["list", "--json"], ["board"]])
    def test_mixed_turtle_doc_and_broken_item_warns_only_for_broken(
        self, temp_repo, software_config, monkeypatch, args,
    ):
        monkeypatch.chdir(temp_repo)
        self._write_valid(temp_repo)
        turtle_rel = self.FEATURES / "research-note.md"
        (temp_repo / turtle_rel).write_text(self.TURTLE_FRONTMATTER["prefix"])
        broken_rel = self.FEATURES / "FEAT-003-broken.md"
        (temp_repo / broken_rel).write_text(
            "---\nid: FEAT-003\ntitle: [unclosed\ntype: feature\nstatus: backlog\n"
            "---\n\n# broken\n"
        )

        result = self._invoke(args)

        assert "FEAT-001" in result.stdout, result.stdout
        warnings = [ln for ln in result.stderr.splitlines() if ln.strip()]
        assert len(warnings) == 1, f"expected exactly one warning: {result.stderr!r}"
        assert str(broken_rel) in warnings[0], warnings
        assert not self._lines_naming(result.stderr, turtle_rel), result.stderr
