"""
Tests for multi-board configuration support.
"""

from pathlib import Path

import pytest

from yurtle_kanban.config import (
    CONFIG_VERSION_MULTI,
    CONFIG_VERSION_SINGLE,
    BoardConfig,
    KanbanConfig,
    PathConfig,
)
from yurtle_kanban.models import WorkItemStatus, WorkItemType
from yurtle_kanban.service import KanbanService


class TestBoardConfig:
    """Tests for BoardConfig dataclass."""

    def test_board_config_defaults(self):
        """Test BoardConfig with default values."""
        board = BoardConfig(name="test")
        assert board.name == "test"
        assert board.preset == "software"
        assert board.path == "work/"
        assert board.wip_limits == {}

    def test_board_config_custom(self):
        """Test BoardConfig with custom values."""
        board = BoardConfig(
            name="research",
            preset="hdd",
            path="research/",
            wip_limits={"active": 2, "draft": 5},
        )
        assert board.name == "research"
        assert board.preset == "hdd"
        assert board.path == "research/"
        assert board.wip_limits == {"active": 2, "draft": 5}

    def test_board_config_from_dict(self):
        """Test BoardConfig.from_dict."""
        data = {
            "name": "development",
            "preset": "nautical",
            "path": "kanban-work/",
            "wip_limits": {"underway": 3},
        }
        board = BoardConfig.from_dict(data)
        assert board.name == "development"
        assert board.preset == "nautical"
        assert board.path == "kanban-work/"
        assert board.wip_limits == {"underway": 3}

    def test_board_config_to_dict(self):
        """Test BoardConfig.to_dict."""
        board = BoardConfig(
            name="research",
            preset="hdd",
            path="research/",
            wip_limits={"active": 2},
        )
        data = board.to_dict()
        assert data == {
            "name": "research",
            "preset": "hdd",
            "path": "research/",
            "wip_limits": {"active": 2},
        }

    def test_board_config_get_path(self):
        """Test BoardConfig.get_path."""
        board = BoardConfig(name="test", path="my/path/")
        assert board.get_path() == Path("my/path/")


class TestKanbanConfigMultiBoard:
    """Tests for multi-board KanbanConfig."""

    def test_single_board_default(self):
        """Test default single-board configuration."""
        config = KanbanConfig()
        assert config.version == CONFIG_VERSION_SINGLE
        assert not config.is_multi_board
        assert len(config.boards) == 0

    def test_multi_board_detection(self):
        """Test multi-board mode detection."""
        config = KanbanConfig(
            version=CONFIG_VERSION_MULTI,
            boards=[
                BoardConfig(name="dev", preset="software", path="work/"),
                BoardConfig(name="research", preset="hdd", path="research/"),
            ],
        )
        assert config.is_multi_board
        assert len(config.boards) == 2

    def test_get_board_by_name(self):
        """Test getting a board by name."""
        config = KanbanConfig(
            version=CONFIG_VERSION_MULTI,
            boards=[
                BoardConfig(name="dev", preset="software", path="work/"),
                BoardConfig(name="research", preset="hdd", path="research/"),
            ],
        )
        board = config.get_board("research")
        assert board is not None
        assert board.name == "research"
        assert board.preset == "hdd"

    def test_get_board_not_found(self):
        """Test getting a non-existent board."""
        config = KanbanConfig(
            version=CONFIG_VERSION_MULTI,
            boards=[BoardConfig(name="dev")],
        )
        board = config.get_board("nonexistent")
        assert board is None

    def test_get_default_board(self):
        """Test getting the default board."""
        config = KanbanConfig(
            version=CONFIG_VERSION_MULTI,
            boards=[
                BoardConfig(name="dev"),
                BoardConfig(name="research"),
            ],
            default_board="research",
        )
        board = config.get_default_board()
        assert board is not None
        assert board.name == "research"

    def test_get_default_board_fallback(self):
        """Test default board falls back to first board."""
        config = KanbanConfig(
            version=CONFIG_VERSION_MULTI,
            boards=[
                BoardConfig(name="first"),
                BoardConfig(name="second"),
            ],
        )
        board = config.get_default_board()
        assert board is not None
        assert board.name == "first"

    def test_add_board_single_to_multi(self):
        """Test adding a board upgrades from single to multi."""
        config = KanbanConfig(
            theme="software",
            paths=PathConfig(root="work/"),
        )
        assert not config.is_multi_board

        config.add_board(BoardConfig(name="research", preset="hdd", path="research/"))

        assert config.is_multi_board
        assert len(config.boards) == 2  # Original + new
        assert config.boards[0].name == "default"  # Original converted
        assert config.boards[1].name == "research"

    def test_add_board_multi(self):
        """Test adding a board in multi-board mode."""
        config = KanbanConfig(
            version=CONFIG_VERSION_MULTI,
            boards=[BoardConfig(name="dev")],
        )
        config.add_board(BoardConfig(name="research"))

        assert len(config.boards) == 2
        assert config.boards[1].name == "research"


class TestKanbanConfigLoadSave:
    """Tests for loading and saving multi-board configs."""

    def test_load_v2_config(self, tmp_path):
        """Test loading a v2 multi-board config."""
        config_path = tmp_path / ".kanban" / "config.yaml"
        config_path.parent.mkdir(parents=True)
        config_path.write_text("""
version: "2.0"
boards:
  - name: development
    preset: nautical
    path: kanban-work/
    wip_limits:
      underway: 3
  - name: research
    preset: hdd
    path: research/
default_board: development
namespace: "https://example.com/"
""")

        config = KanbanConfig.load(config_path)

        assert config.is_multi_board
        assert len(config.boards) == 2
        assert config.boards[0].name == "development"
        assert config.boards[0].preset == "nautical"
        assert config.boards[0].wip_limits == {"underway": 3}
        assert config.boards[1].name == "research"
        assert config.boards[1].preset == "hdd"
        assert config.default_board == "development"
        assert config.namespace == "https://example.com/"

    def test_load_v1_config_backward_compat(self, tmp_path):
        """Test loading a v1 config (backward compatibility)."""
        config_path = tmp_path / ".kanban" / "config.yaml"
        config_path.parent.mkdir(parents=True)
        config_path.write_text("""
kanban:
  theme: nautical
  paths:
    root: kanban-work/
""")

        config = KanbanConfig.load(config_path)

        assert not config.is_multi_board
        assert config.theme == "nautical"
        assert config.paths.root == "kanban-work/"

    def test_save_v2_config(self, tmp_path):
        """Test saving a v2 multi-board config."""
        config = KanbanConfig(
            version=CONFIG_VERSION_MULTI,
            boards=[
                BoardConfig(name="dev", preset="software", path="work/"),
                BoardConfig(name="research", preset="hdd", path="research/"),
            ],
            default_board="dev",
            namespace="https://test.dev/",
        )

        config_path = tmp_path / ".kanban" / "config.yaml"
        config.save(config_path)

        # Reload and verify
        loaded = KanbanConfig.load(config_path)
        assert loaded.is_multi_board
        assert len(loaded.boards) == 2
        assert loaded.default_board == "dev"
        assert loaded.namespace == "https://test.dev/"

    def test_get_work_paths_multi(self):
        """Test get_work_paths in multi-board mode."""
        config = KanbanConfig(
            version=CONFIG_VERSION_MULTI,
            boards=[
                BoardConfig(name="dev", path="work/"),
                BoardConfig(name="research", path="research/"),
            ],
        )
        paths = config.get_work_paths()
        assert len(paths) == 2
        assert Path("work/") in paths
        assert Path("research/") in paths


class TestKanbanServiceMultiBoard:
    """Tests for KanbanService with multi-board support."""

    @pytest.fixture
    def multi_board_setup(self, tmp_path):
        """Create a multi-board test environment."""
        # Create config
        config_path = tmp_path / ".kanban" / "config.yaml"
        config_path.parent.mkdir(parents=True)
        config_path.write_text("""
version: "2.0"
boards:
  - name: development
    preset: nautical
    path: kanban-work/
  - name: research
    preset: software
    path: research/
default_board: development
""")

        # Create work item directories
        (tmp_path / "kanban-work" / "expeditions").mkdir(parents=True)
        (tmp_path / "research" / "experiments").mkdir(parents=True)

        # Create some work items
        dev_item = tmp_path / "kanban-work" / "expeditions" / "EXP-001-Test.md"
        dev_item.write_text("""---
id: EXP-001
title: "Test Expedition"
type: expedition
status: backlog
---
# Test Expedition
""")

        research_item = tmp_path / "research" / "experiments" / "EXPR-101.md"
        research_item.write_text("""---
id: EXPR-101
title: "Test Experiment"
type: feature
status: in_progress
---
# Test Experiment
""")

        config = KanbanConfig.load(config_path)
        service = KanbanService(config, tmp_path)

        return {
            "tmp_path": tmp_path,
            "config": config,
            "service": service,
        }

    def test_get_board_default(self, multi_board_setup):
        """Test getting the default board."""
        service = multi_board_setup["service"]
        board = service.get_board()

        assert board.id == "development"
        assert board.name == "Development Board"

    def test_get_board_by_name(self, multi_board_setup):
        """Test getting a specific board by name."""
        service = multi_board_setup["service"]
        board = service.get_board(board_name="research")

        assert board.id == "research"
        assert board.name == "Research Board"

    def test_board_items_filtered(self, multi_board_setup):
        """Test that each board only shows its own items."""
        service = multi_board_setup["service"]

        dev_board = service.get_board(board_name="development")
        research_board = service.get_board(board_name="research")

        # Development board should have EXP-001
        dev_ids = [item.id for item in dev_board.items]
        assert "EXP-001" in dev_ids
        assert "EXPR-101" not in dev_ids

        # Research board should have EXPR-101
        research_ids = [item.id for item in research_board.items]
        assert "EXPR-101" in research_ids
        assert "EXP-001" not in research_ids


class TestGetItemsBoardFilter:
    """Tests for get_items(board=...) filtering."""

    @pytest.fixture
    def multi_board_setup(self, tmp_path):
        """Create a multi-board test environment with items on both boards."""
        config_path = tmp_path / ".kanban" / "config.yaml"
        config_path.parent.mkdir(parents=True)
        config_path.write_text("""
version: "2.0"
boards:
  - name: development
    preset: nautical
    path: kanban-work/
  - name: research
    preset: software
    path: research/
default_board: development
""")

        (tmp_path / "kanban-work" / "expeditions").mkdir(parents=True)
        (tmp_path / "research" / "experiments").mkdir(parents=True)

        (tmp_path / "kanban-work" / "expeditions" / "EXP-001-Test.md").write_text("""---
id: EXP-001
title: "Dev Item"
type: expedition
status: backlog
---
# Dev Item
""")
        (tmp_path / "kanban-work" / "expeditions" / "EXP-002-Active.md").write_text("""---
id: EXP-002
title: "Active Dev Item"
type: expedition
status: in_progress
---
# Active Dev Item
""")
        (tmp_path / "research" / "experiments" / "EXPR-101.md").write_text("""---
id: EXPR-101
title: "Research Experiment"
type: feature
status: in_progress
---
# Research Experiment
""")
        (tmp_path / "research" / "experiments" / "EXPR-102.md").write_text("""---
id: EXPR-102
title: "Draft Experiment"
type: feature
status: backlog
---
# Draft Experiment
""")

        config = KanbanConfig.load(config_path)
        service = KanbanService(config, tmp_path)
        return service

    def test_get_items_no_board_returns_all(self, multi_board_setup):
        """Without board filter, get_items returns items from all boards."""
        items = multi_board_setup.get_items()
        ids = {i.id for i in items}
        assert "EXP-001" in ids
        assert "EXP-002" in ids
        assert "EXPR-101" in ids
        assert "EXPR-102" in ids

    def test_get_items_board_development(self, multi_board_setup):
        """With board='development', only dev items returned."""
        items = multi_board_setup.get_items(board="development")
        ids = {i.id for i in items}
        assert "EXP-001" in ids
        assert "EXP-002" in ids
        assert "EXPR-101" not in ids
        assert "EXPR-102" not in ids

    def test_get_items_board_research(self, multi_board_setup):
        """With board='research', only research items returned."""
        items = multi_board_setup.get_items(board="research")
        ids = {i.id for i in items}
        assert "EXPR-101" in ids
        assert "EXPR-102" in ids
        assert "EXP-001" not in ids
        assert "EXP-002" not in ids

    def test_get_items_board_with_status_filter(self, multi_board_setup):
        """Board filter combines with status filter."""
        from yurtle_kanban.models import WorkItemStatus
        items = multi_board_setup.get_items(
            board="research",
            status=WorkItemStatus.IN_PROGRESS,
        )
        ids = {i.id for i in items}
        assert ids == {"EXPR-101"}

    def test_get_items_board_invalid_name(self, multi_board_setup):
        """Invalid board name returns empty list."""
        items = multi_board_setup.get_items(board="nonexistent")
        assert items == []

    def test_get_items_board_ignored_in_single_board(self, tmp_path):
        """In single-board mode, board parameter is ignored."""
        config_path = tmp_path / ".kanban" / "config.yaml"
        config_path.parent.mkdir(parents=True)
        config_path.write_text("""
theme: software
paths:
  root: work/
""")
        (tmp_path / "work").mkdir()
        (tmp_path / "work" / "ITEM-001.md").write_text("""---
id: ITEM-001
title: "Solo Item"
type: feature
status: backlog
---
# Solo
""")
        config = KanbanConfig.load(config_path)
        service = KanbanService(config, tmp_path)
        items = service.get_items(board="research")
        assert len(items) == 1
        assert items[0].id == "ITEM-001"


class TestBoardForPath:
    """Tests for detecting board from path."""

    def test_get_board_for_path(self, tmp_path):
        """Test detecting board from file path."""
        config = KanbanConfig(
            version=CONFIG_VERSION_MULTI,
            boards=[
                BoardConfig(name="dev", path="kanban-work/"),
                BoardConfig(name="research", path="research/"),
            ],
        )

        # Test path matching
        board = config.get_board_for_path(
            tmp_path / "kanban-work" / "expeditions" / "EXP-001.md",
            repo_root=tmp_path,
        )
        assert board is not None
        assert board.name == "dev"

        board = config.get_board_for_path(
            tmp_path / "research" / "experiments" / "EXPR-101.md",
            repo_root=tmp_path,
        )
        assert board is not None
        assert board.name == "research"

    def test_get_board_for_path_no_match(self, tmp_path):
        """Test path that doesn't match any board."""
        config = KanbanConfig(
            version=CONFIG_VERSION_MULTI,
            boards=[
                BoardConfig(name="dev", path="kanban-work/"),
            ],
        )

        board = config.get_board_for_path(
            tmp_path / "other" / "file.md",
            repo_root=tmp_path,
        )
        assert board is None


class TestHDDTypeRoutingMultiBoard:
    """Issue #20: HDD item types must route to the research board in multi-board mode.

    When a multi-board config has development (nautical) + research (hdd) boards,
    HDD item types (hypothesis, experiment, literature, measure, paper, idea)
    should be created in the research board's directories, not the development board's.
    """

    @pytest.fixture
    def multiboard_hdd_setup(self, tmp_path):
        """Create a multi-board environment with development + research boards."""
        import subprocess

        # Init git repo (needed for create_item_and_push)
        subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, capture_output=True, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=tmp_path, capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=tmp_path, capture_output=True, check=True,
        )

        # Create config
        config_path = tmp_path / ".kanban" / "config.yaml"
        config_path.parent.mkdir(parents=True)
        config_path.write_text("""
version: "2.0"
boards:
  - name: development
    preset: nautical
    path: kanban-work/
  - name: research
    preset: hdd
    path: research/
default_board: development
""")

        # Create directory structure
        for subdir in ["expeditions", "chores", "voyages"]:
            (tmp_path / "kanban-work" / subdir).mkdir(parents=True)
        for subdir in ["ideas", "literature", "papers", "hypotheses", "experiments", "measures"]:
            (tmp_path / "research" / subdir).mkdir(parents=True)

        config = KanbanConfig.load(config_path)
        service = KanbanService(config, tmp_path)

        return {"tmp_path": tmp_path, "config": config, "service": service}

    def test_hypothesis_routes_to_research(self, multiboard_hdd_setup):
        """Hypothesis should be created in research/hypotheses/, not kanban-work/."""
        from yurtle_kanban.models import WorkItemType

        service = multiboard_hdd_setup["service"]

        item = service.create_item(
            item_type=WorkItemType.HYPOTHESIS,
            title="V12 improves accuracy",
            item_id="H130.1",
        )

        assert "research/hypotheses/" in str(item.file_path)
        assert "kanban-work" not in str(item.file_path)
        assert item.file_path.exists()

    def test_experiment_routes_to_research(self, multiboard_hdd_setup):
        """Experiment should be created in research/experiments/."""
        from yurtle_kanban.models import WorkItemType

        service = multiboard_hdd_setup["service"]

        item = service.create_item(
            item_type=WorkItemType.EXPERIMENT,
            title="V12 accuracy test",
            item_id="EXPR-130",
        )

        assert "research/experiments/" in str(item.file_path)
        assert "kanban-work" not in str(item.file_path)

    def test_paper_routes_to_research(self, multiboard_hdd_setup):
        """Paper should be created in research/papers/."""
        from yurtle_kanban.models import WorkItemType

        service = multiboard_hdd_setup["service"]

        item = service.create_item(
            item_type=WorkItemType.PAPER,
            title="NuSy Brain Architecture",
            item_id="PAPER-130",
        )

        assert "research/papers/" in str(item.file_path)

    def test_literature_routes_to_research(self, multiboard_hdd_setup):
        """Literature should be created in research/literature/."""
        from yurtle_kanban.models import WorkItemType

        service = multiboard_hdd_setup["service"]

        item = service.create_item(
            item_type=WorkItemType.LITERATURE,
            title="Transfer learning survey",
            item_id="LIT-001",
        )

        assert "research/literature/" in str(item.file_path)

    def test_measure_routes_to_research(self, multiboard_hdd_setup):
        """Measure should be created in research/measures/."""
        from yurtle_kanban.models import WorkItemType

        service = multiboard_hdd_setup["service"]

        item = service.create_item(
            item_type=WorkItemType.MEASURE,
            title="Reasoning Accuracy",
            item_id="M-001",
        )

        assert "research/measures/" in str(item.file_path)

    def test_idea_routes_to_research(self, multiboard_hdd_setup):
        """Idea should be created in research/ideas/."""
        from yurtle_kanban.models import WorkItemType

        service = multiboard_hdd_setup["service"]

        item = service.create_item(
            item_type=WorkItemType.IDEA,
            title="Explore transfer learning",
            item_id="IDEA-R-001",
        )

        assert "research/ideas/" in str(item.file_path)

    def test_expedition_still_routes_to_kanban_work(self, multiboard_hdd_setup):
        """Nautical types should still route to kanban-work/, not research/."""
        from yurtle_kanban.models import WorkItemType

        service = multiboard_hdd_setup["service"]

        item = service.create_item(
            item_type=WorkItemType.EXPEDITION,
            title="Test expedition",
            item_id="EXP-999",
        )

        assert "kanban-work" in str(item.file_path)
        assert "research" not in str(item.file_path)


class TestBoardConfigScanPaths:
    """Tests for BoardConfig scan_paths parsing (fix for dropped scan_paths)."""

    def test_from_dict_parses_scan_paths(self):
        """scan_paths should be preserved when loading from dict."""
        data = {
            "name": "research",
            "preset": "hdd",
            "path": "research/",
            "scan_paths": [
                "research/hypotheses/",
                "research/experiments/",
            ],
        }
        board = BoardConfig.from_dict(data)
        assert board.scan_paths == [
            "research/hypotheses/",
            "research/experiments/",
        ]

    def test_from_dict_defaults_empty_scan_paths(self):
        """Missing scan_paths should default to empty list."""
        board = BoardConfig.from_dict({"name": "dev"})
        assert board.scan_paths == []

    def test_to_dict_includes_scan_paths(self):
        """scan_paths should be serialized when present."""
        board = BoardConfig(
            name="research",
            preset="hdd",
            path="research/",
            scan_paths=["research/hypotheses/", "research/experiments/"],
        )
        data = board.to_dict()
        assert data["scan_paths"] == [
            "research/hypotheses/",
            "research/experiments/",
        ]

    def test_to_dict_omits_empty_scan_paths(self):
        """Empty scan_paths should not appear in serialized form."""
        board = BoardConfig(name="dev", path="work/")
        data = board.to_dict()
        assert "scan_paths" not in data

    def test_v2_load_aggregates_scan_paths(self, tmp_path):
        """_load_v2 should populate PathConfig.scan_paths from all boards."""
        config_path = tmp_path / ".kanban" / "config.yaml"
        config_path.parent.mkdir(parents=True)
        config_path.write_text("""
version: "2.0"
boards:
  - name: development
    preset: nautical
    path: kanban-work/
    scan_paths:
      - "kanban-work/expeditions/"
      - "kanban-work/chores/"
  - name: research
    preset: hdd
    path: research/
    scan_paths:
      - "research/hypotheses/"
      - "research/experiments/"
default_board: development
""")
        config = KanbanConfig.load(config_path)
        assert len(config.paths.scan_paths) == 4
        assert "kanban-work/expeditions/" in config.paths.scan_paths
        assert "research/hypotheses/" in config.paths.scan_paths

    def test_v2_save_load_roundtrip_with_scan_paths(self, tmp_path):
        """scan_paths should survive a save+load roundtrip."""
        config = KanbanConfig(
            version=CONFIG_VERSION_MULTI,
            boards=[
                BoardConfig(
                    name="dev",
                    preset="nautical",
                    path="work/",
                    scan_paths=["work/features/", "work/bugs/"],
                ),
            ],
        )
        config_path = tmp_path / ".kanban" / "config.yaml"
        config.save(config_path)

        loaded = KanbanConfig.load(config_path)
        assert loaded.boards[0].scan_paths == ["work/features/", "work/bugs/"]


class TestIrregularPluralRouting:
    """Tests for scan_path keyword matching with irregular plurals."""

    def test_hypothesis_matches_hypotheses_scan_path(self, tmp_path):
        """hypothesis type should match 'hypotheses' in scan_paths."""
        from yurtle_kanban.models import WorkItemType

        config = KanbanConfig(
            theme="nonexistent",
            paths=PathConfig(
                root="work/",
                scan_paths=["research/hypotheses/"],
            ),
        )
        (tmp_path / "research" / "hypotheses").mkdir(parents=True)
        svc = KanbanService(config, tmp_path)
        path = svc._get_type_directory(WorkItemType.HYPOTHESIS)
        assert "hypotheses" in str(path)

    def test_literature_matches_literature_scan_path(self, tmp_path):
        """literature type should match 'literature' in scan_paths."""
        from yurtle_kanban.models import WorkItemType

        config = KanbanConfig(
            theme="nonexistent",
            paths=PathConfig(
                root="work/",
                scan_paths=["research/literature/"],
            ),
        )
        (tmp_path / "research" / "literature").mkdir(parents=True)
        svc = KanbanService(config, tmp_path)
        path = svc._get_type_directory(WorkItemType.LITERATURE)
        assert "literature" in str(path)

    def test_experiment_still_matches_with_regular_plural(self, tmp_path):
        """Regular plurals (experiment→experiments) should still work."""
        from yurtle_kanban.models import WorkItemType

        config = KanbanConfig(
            theme="nonexistent",
            paths=PathConfig(
                root="work/",
                scan_paths=["research/experiments/"],
            ),
        )
        (tmp_path / "research" / "experiments").mkdir(parents=True)
        svc = KanbanService(config, tmp_path)
        path = svc._get_type_directory(WorkItemType.EXPERIMENT)
        assert "experiments" in str(path)


class TestBoardAwareMove:
    """CHORE-079: move command must respect per-board preset states and WIP limits.

    When multi-board is configured with development (nautical) + research (hdd),
    moving a research item should use the HDD preset's WIP limits, not the
    development board's.
    """

    @pytest.fixture
    def move_test_setup(self, tmp_path):
        """Create a multi-board environment with items for move testing."""
        config_path = tmp_path / ".kanban" / "config.yaml"
        config_path.parent.mkdir(parents=True)
        config_path.write_text("""
version: "2.0"
boards:
  - name: development
    preset: nautical
    path: kanban-work/
  - name: research
    preset: hdd
    path: research/
default_board: development
""")

        (tmp_path / "kanban-work" / "expeditions").mkdir(parents=True)
        (tmp_path / "research" / "experiments").mkdir(parents=True)

        # Create a dev item
        dev_item = tmp_path / "kanban-work" / "expeditions" / "EXP-001-Test.md"
        dev_item.write_text("""---
id: EXP-001
title: "Dev Item"
type: expedition
status: backlog
---
# Dev Item
""")

        # Create a research item
        research_item = tmp_path / "research" / "experiments" / "EXPR-200.md"
        research_item.write_text("""---
id: EXPR-200
title: "Research Item"
type: experiment
status: backlog
---
# Research Item
""")

        config = KanbanConfig.load(config_path)
        service = KanbanService(config, tmp_path)
        return {"tmp_path": tmp_path, "config": config, "service": service}

    def test_column_status_map_includes_hdd_aliases(self, move_test_setup):
        """_get_column_status_map should include HDD aliases from theme status_mappings."""
        from yurtle_kanban.models import WorkItemStatus

        service = move_test_setup["service"]
        col_map = service._get_column_status_map()

        # HDD theme status_mappings: active→in_progress, complete→done, abandoned→blocked
        assert col_map.get("active") == WorkItemStatus.IN_PROGRESS
        assert col_map.get("complete") == WorkItemStatus.DONE
        assert col_map.get("abandoned") == WorkItemStatus.BLOCKED
        # draft→backlog was already in the hardcoded mappings
        assert col_map.get("draft") == WorkItemStatus.BACKLOG

    def test_move_research_item_uses_research_board_wip(self, move_test_setup):
        """Moving a research item should check WIP on the research board, not dev."""
        from yurtle_kanban.models import WorkItemStatus

        service = move_test_setup["service"]

        # Move EXPR-200 from backlog → ready (should succeed regardless of dev board WIP)
        item = service.move_item(
            "EXPR-200", WorkItemStatus.READY, commit=False, validate_workflow=False
        )
        assert item.status == WorkItemStatus.READY

    def test_move_dev_item_still_uses_dev_board(self, move_test_setup):
        """Dev items should still use the dev board for WIP checks."""
        from yurtle_kanban.models import WorkItemStatus

        service = move_test_setup["service"]

        item = service.move_item(
            "EXP-001", WorkItemStatus.READY, commit=False, validate_workflow=False
        )
        assert item.status == WorkItemStatus.READY


class TestHDDStateRoundTrip:
    """Issue #41: HDD state names should be preserved on move write-back.

    When moving a research item like H130.1 to 'active', the file should have
    `status: active` (HDD native name), not `status: in_progress` (canonical name).
    Also, HDD transitions (draft→active) should work without --force.
    """

    @pytest.fixture
    def hdd_state_setup(self, tmp_path):
        """Create a multi-board environment with HDD research board."""
        config_path = tmp_path / ".kanban" / "config.yaml"
        config_path.parent.mkdir(parents=True)
        config_path.write_text("""
version: "2.0"
boards:
  - name: development
    preset: nautical
    path: kanban-work/
  - name: research
    preset: hdd
    path: research/
default_board: development
""")

        (tmp_path / "kanban-work" / "expeditions").mkdir(parents=True)
        (tmp_path / "research" / "hypotheses").mkdir(parents=True)
        (tmp_path / "research" / "experiments").mkdir(parents=True)

        # Create a hypothesis with HDD native status 'draft'
        hyp_file = tmp_path / "research" / "hypotheses" / "H130.1.md"
        hyp_file.write_text("""---
id: H130.1
title: "Test Hypothesis"
type: hypothesis
status: draft
---
# Test Hypothesis
""")

        # Create an experiment with HDD native status 'active'
        expr_file = tmp_path / "research" / "experiments" / "EXPR-130.md"
        expr_file.write_text("""---
id: EXPR-130
title: "Test Experiment"
type: experiment
status: active
---
# Test Experiment
""")

        # Create a dev item for comparison
        dev_file = tmp_path / "kanban-work" / "expeditions" / "EXP-999.md"
        dev_file.write_text("""---
id: EXP-999
title: "Test Expedition"
type: expedition
status: backlog
---
# Test Expedition
""")

        config = KanbanConfig.load(config_path)
        service = KanbanService(config, tmp_path)
        return {
            "tmp_path": tmp_path,
            "config": config,
            "service": service,
            "hyp_file": hyp_file,
            "expr_file": expr_file,
            "dev_file": dev_file,
        }

    def test_move_hdd_item_writes_native_status(self, hdd_state_setup):
        """Moving H130.1 to 'active' should write 'status: active', not 'in_progress'."""
        from yurtle_kanban.models import WorkItemStatus

        service = hdd_state_setup["service"]
        hyp_file = hdd_state_setup["hyp_file"]

        # Move hypothesis from draft (backlog) to active (in_progress)
        item = service.move_item(
            "H130.1", WorkItemStatus.IN_PROGRESS, commit=False, validate_workflow=False
        )
        assert item.status == WorkItemStatus.IN_PROGRESS

        # Read the file and verify it has HDD native name
        content = hyp_file.read_text()
        assert "status: active" in content
        assert "status: in_progress" not in content

    def test_move_hdd_item_to_complete_writes_complete(self, hdd_state_setup):
        """Moving EXPR-130 to 'complete' should write 'status: complete', not 'done'."""
        from yurtle_kanban.models import WorkItemStatus

        service = hdd_state_setup["service"]
        expr_file = hdd_state_setup["expr_file"]

        # Move experiment from active (in_progress) to complete (done)
        item = service.move_item(
            "EXPR-130", WorkItemStatus.DONE, commit=False, validate_workflow=False
        )
        assert item.status == WorkItemStatus.DONE

        # Read the file and verify it has HDD native name
        content = expr_file.read_text()
        assert "status: complete" in content
        assert "status: done" not in content

    def test_move_dev_item_writes_canonical_status(self, hdd_state_setup):
        """Dev items should still use canonical names (in_progress, not active)."""
        from yurtle_kanban.models import WorkItemStatus

        service = hdd_state_setup["service"]
        dev_file = hdd_state_setup["dev_file"]

        # Move dev item to in_progress
        item = service.move_item(
            "EXP-999", WorkItemStatus.IN_PROGRESS, commit=False, validate_workflow=False
        )
        assert item.status == WorkItemStatus.IN_PROGRESS

        # Read the file and verify it has canonical name (nautical doesn't define reverse mapping)
        content = dev_file.read_text()
        assert "status: in_progress" in content

    def test_hdd_draft_to_active_transition_valid(self, hdd_state_setup):
        """HDD draft→active transition should be valid without --force."""
        from yurtle_kanban.models import WorkItemStatus

        service = hdd_state_setup["service"]

        # This should NOT raise an error with validate_workflow=True
        item = service.move_item(
            "H130.1", WorkItemStatus.IN_PROGRESS, commit=False, validate_workflow=True
        )
        assert item.status == WorkItemStatus.IN_PROGRESS

    def test_hdd_active_to_complete_transition_valid(self, hdd_state_setup):
        """HDD active→complete transition should be valid without --force."""
        from yurtle_kanban.models import WorkItemStatus

        service = hdd_state_setup["service"]

        # Move experiment from active to complete
        item = service.move_item(
            "EXPR-130", WorkItemStatus.DONE, commit=False, validate_workflow=True
        )
        assert item.status == WorkItemStatus.DONE

    def test_hdd_invalid_transition_rejected(self, hdd_state_setup):
        """HDD draft→complete transition should be rejected (not a valid HDD transition)."""
        from yurtle_kanban.models import WorkItemStatus

        service = hdd_state_setup["service"]

        # draft→complete is not allowed in HDD (must go draft→active→complete)
        with pytest.raises(ValueError, match="Invalid transition"):
            service.move_item(
                "H130.1", WorkItemStatus.DONE, commit=False, validate_workflow=True
            )

    def test_reverse_status_mapping_hdd(self, hdd_state_setup):
        """Test _get_reverse_status_mapping for HDD board."""
        service = hdd_state_setup["service"]

        board = service.config.get_board("research")
        mapping = service._get_reverse_status_mapping(board)

        assert mapping.get("backlog") == "draft"
        assert mapping.get("in_progress") == "active"
        assert mapping.get("done") == "complete"
        assert mapping.get("blocked") == "abandoned"

    def test_reverse_status_mapping_no_board(self, hdd_state_setup):
        """Test _get_reverse_status_mapping returns empty for non-HDD board."""
        service = hdd_state_setup["service"]

        # Nautical preset has no status_mappings → empty reverse mapping
        board = service.config.get_board("development")
        mapping = service._get_reverse_status_mapping(board)

        assert mapping == {}

    def test_move_hdd_item_to_abandoned_writes_abandoned(self, hdd_state_setup):
        """Moving an HDD item to 'abandoned' should write native name."""
        from yurtle_kanban.models import WorkItemStatus

        service = hdd_state_setup["service"]
        hyp_file = hdd_state_setup["hyp_file"]

        # draft→abandoned is valid in HDD transitions
        item = service.move_item(
            "H130.1", WorkItemStatus.BLOCKED, commit=False,
            validate_workflow=True,
        )
        assert item.status == WorkItemStatus.BLOCKED

        content = hyp_file.read_text()
        assert "status: abandoned" in content
        assert "status: blocked" not in content

    def test_board_transitions_hdd(self, hdd_state_setup):
        """Test _get_board_transitions returns HDD-specific transitions."""
        service = hdd_state_setup["service"]

        board = service.config.get_board("research")
        transitions = service._get_board_transitions(board)

        assert transitions is not None
        assert "draft" in transitions
        assert "active" in transitions["draft"]
        assert "abandoned" in transitions["draft"]
        assert "complete" in transitions["active"]


class TestWipExemptTypes:
    """CHORE-137: Voyages (and other exempt types) should not count against WIP.

    Without wip_exempt_types, voyages occupy WIP slots even though they are
    containers, not work items. With wip_exempt_types: [voyage], moving a
    voyage to in_progress must not count toward the limit.
    """

    @pytest.fixture
    def wip_repo(self, tmp_path):
        """Multi-board repo with voyage exempt and wip_limit=2 on in_progress."""
        import subprocess

        subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, capture_output=True, check=True)

        config_path = tmp_path / ".kanban" / "config.yaml"
        config_path.parent.mkdir(parents=True)
        config_path.write_text("""
version: "2.0"
boards:
  - name: development
    preset: nautical
    path: kanban-work/
    wip_exempt_types:
      - voyage
    wip_limits:
      in_progress: 2
default_board: development
""")
        (tmp_path / "kanban-work" / "expeditions").mkdir(parents=True)
        (tmp_path / "kanban-work" / "voyages").mkdir(parents=True)
        (tmp_path / "kanban-work" / "chores").mkdir(parents=True)
        (tmp_path / ".kanban").mkdir(exist_ok=True)

        config = KanbanConfig.load(config_path)
        service = KanbanService(config, tmp_path)
        return {"tmp_path": tmp_path, "config": config, "service": service}

    def test_voyage_exempt_from_wip(self, wip_repo):
        """Voyages don't count toward WIP — moving them in-progress is always allowed."""
        svc = wip_repo["service"]

        # Fill up the WIP limit with 2 expeditions
        exp1 = svc.create_item(WorkItemType.EXPEDITION, "Expedition One",
                               assignee="Agent", description="desc")
        exp2 = svc.create_item(WorkItemType.EXPEDITION, "Expedition Two",
                               assignee="Agent", description="desc")
        svc.move_item(exp1.id, WorkItemStatus.IN_PROGRESS, commit=False, validate_workflow=False)
        svc.move_item(exp2.id, WorkItemStatus.IN_PROGRESS, commit=False, validate_workflow=False)

        # A voyage should still be moveable — it's exempt
        voy = svc.create_item(WorkItemType.VOYAGE, "Big Initiative")
        svc.move_item(voy.id, WorkItemStatus.IN_PROGRESS, commit=False, validate_workflow=False)

        board = svc.get_board()
        in_progress = board.get_items_by_status(WorkItemStatus.IN_PROGRESS)
        assert any(i.id == voy.id for i in in_progress)

    def test_expedition_blocked_at_wip_limit(self, wip_repo):
        """Non-exempt types are still blocked when WIP limit is reached."""
        svc = wip_repo["service"]

        exp1 = svc.create_item(WorkItemType.EXPEDITION, "Expedition One",
                               assignee="Agent", description="desc")
        exp2 = svc.create_item(WorkItemType.EXPEDITION, "Expedition Two",
                               assignee="Agent", description="desc")
        svc.move_item(exp1.id, WorkItemStatus.IN_PROGRESS, commit=False, validate_workflow=False)
        svc.move_item(exp2.id, WorkItemStatus.IN_PROGRESS, commit=False, validate_workflow=False)

        exp3 = svc.create_item(WorkItemType.EXPEDITION, "Expedition Three",
                               assignee="Agent", description="desc")
        with pytest.raises(ValueError, match="WIP limit reached"):
            svc.move_item(exp3.id, WorkItemStatus.IN_PROGRESS, commit=False, validate_workflow=False)

    def test_voyage_does_not_consume_wip_slot_for_expeditions(self, wip_repo):
        """A voyage in-progress must not block a subsequent expedition."""
        svc = wip_repo["service"]

        # Add a voyage first (exempt — doesn't consume a slot)
        voy = svc.create_item(WorkItemType.VOYAGE, "Campaign")
        svc.move_item(voy.id, WorkItemStatus.IN_PROGRESS, commit=False, validate_workflow=False)

        # Should still be able to add 2 expeditions (the full limit)
        exp1 = svc.create_item(WorkItemType.EXPEDITION, "Expedition One",
                               assignee="Agent", description="desc")
        exp2 = svc.create_item(WorkItemType.EXPEDITION, "Expedition Two",
                               assignee="Agent", description="desc")
        svc.move_item(exp1.id, WorkItemStatus.IN_PROGRESS, commit=False, validate_workflow=False)
        svc.move_item(exp2.id, WorkItemStatus.IN_PROGRESS, commit=False, validate_workflow=False)

        # Third expedition should be blocked
        exp3 = svc.create_item(WorkItemType.EXPEDITION, "Expedition Three",
                               assignee="Agent", description="desc")
        with pytest.raises(ValueError, match="WIP limit reached"):
            svc.move_item(exp3.id, WorkItemStatus.IN_PROGRESS, commit=False, validate_workflow=False)

    def test_wip_exempt_types_from_config_roundtrip(self):
        """BoardConfig.wip_exempt_types roundtrips through from_dict/to_dict."""
        data = {
            "name": "development",
            "preset": "nautical",
            "path": "kanban-work/",
            "wip_exempt_types": ["voyage", "chore"],
        }
        board = BoardConfig.from_dict(data)
        assert board.wip_exempt_types == ["voyage", "chore"]

        out = board.to_dict()
        assert out["wip_exempt_types"] == ["voyage", "chore"]

    def test_wip_exempt_types_default_empty(self):
        """BoardConfig.wip_exempt_types defaults to empty list."""
        board = BoardConfig(name="dev")
        assert board.wip_exempt_types == []

    def test_wip_limits_override_theme_default(self, wip_repo):
        """board_config.wip_limits overrides the theme's column wip_limit."""
        svc = wip_repo["service"]
        board = svc.get_board()

        # Theme (nautical) has in_progress wip_limit=10, but config overrides to 2
        in_progress_col = next(c for c in board.columns if c.id == "in_progress")
        assert in_progress_col.wip_limit == 2


class TestBoardAddKeepsDefaultBoardPath:
    """board-add must not move the default board off where its items live.

    Converting a single-board (v1) config to multi-board takes the default
    board's ``path`` from the existing config -- its ``root`` if that is
    scanned, else the common parent of its ``scan_paths`` (e.g.
    ``kanban-work/``) -- never a hardcoded ``work/``. Existing items must
    stay on the board after ``board-add``. (#94)
    """

    @pytest.fixture
    def repo_runner(self, tmp_path, monkeypatch):
        """Empty git repo as cwd, fresh theme cache, and a CliRunner."""
        import subprocess

        from click.testing import CliRunner

        from yurtle_kanban import config as config_mod

        for cmd in (
            ["git", "init", "-b", "main"],
            ["git", "config", "user.email", "test@test.com"],
            ["git", "config", "user.name", "Test"],
        ):
            subprocess.run(cmd, cwd=tmp_path, capture_output=True, check=True)
        config_mod._theme_cache.clear()
        monkeypatch.chdir(tmp_path)
        yield tmp_path, CliRunner()
        config_mod._theme_cache.clear()

    @staticmethod
    def _run(runner, args: list[str]):
        from yurtle_kanban.cli import main

        result = runner.invoke(main, args, catch_exceptions=False)
        assert result.exit_code == 0, f"{args} failed:\n{result.output}"
        return result

    @staticmethod
    def _saved_boards(repo: Path) -> dict[str, dict]:
        import yaml

        data = yaml.safe_load((repo / ".kanban" / "config.yaml").read_text())
        assert "boards" in data, f"config not multi-board:\n{data}"
        return {b["name"]: b for b in data["boards"]}

    @staticmethod
    def _write_v1(repo: Path, root: str, scan_paths: list[str]) -> None:
        cfg = repo / ".kanban" / "config.yaml"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "kanban:",
            "  theme: software",
            "  paths:",
            f"    root: {root}",
            "    scan_paths:",
            *[f'    - "{p}"' for p in scan_paths],
            "    ignore:",
            '      - "**/archive/**"',
            '      - "**/templates/**"',
            '      - "**/_TEMPLATE*"',
        ]
        cfg.write_text("\n".join(lines) + "\n")
        for p in scan_paths:
            (repo / p).mkdir(parents=True, exist_ok=True)

    # -- the issue's own repro ------------------------------------------------

    def test_issue_repro_item_stays_on_board_after_board_add(self, repo_runner):
        repo, runner = repo_runner
        self._run(runner, ["init", "--theme", "software"])
        self._run(runner, ["create", "idea", "Fresh probe item"])
        assert "IDEA-001" in self._run(runner, ["board"]).output

        self._run(runner, ["board-add", "research", "--preset", "hdd", "--path", "research/"])

        assert "IDEA-001" in self._run(runner, ["board"]).output
        assert "IDEA-001" in self._run(runner, ["list"]).output
        # The file itself was never moved.
        assert list((repo / "kanban-work" / "ideas").glob("IDEA-001-*.md"))

    def test_issue_repro_show_still_finds_item(self, repo_runner):
        _, runner = repo_runner
        self._run(runner, ["init", "--theme", "software"])
        self._run(runner, ["create", "idea", "Fresh probe item"])
        self._run(runner, ["board-add", "research", "--preset", "hdd", "--path", "research/"])

        from yurtle_kanban.cli import main

        result = runner.invoke(main, ["show", "IDEA-001"], catch_exceptions=False)
        assert "Item not found" not in result.output
        assert result.exit_code == 0
        assert "Fresh probe item" in result.output

    def test_issue_repro_saved_default_path_is_kanban_work(self, repo_runner):
        repo, runner = repo_runner
        self._run(runner, ["init", "--theme", "software"])
        self._run(runner, ["create", "idea", "Fresh probe item"])
        self._run(runner, ["board-add", "research", "--preset", "hdd", "--path", "research/"])

        boards = self._saved_boards(repo)
        assert boards["default"]["path"].rstrip("/") == "kanban-work"
        assert boards["default"]["path"].rstrip("/") != "work"

    # -- hand-written v1 configs -----------------------------------------------

    def test_v1_explicit_kanban_work_root_is_kept(self, repo_runner):
        repo, runner = repo_runner
        self._write_v1(
            repo,
            root="kanban-work/",
            scan_paths=["kanban-work/features/", "kanban-work/ideas/"],
        )
        self._run(runner, ["create", "idea", "Hand config item"])
        self._run(runner, ["board-add", "research", "--preset", "hdd", "--path", "research/"])

        assert self._saved_boards(repo)["default"]["path"].rstrip("/") == "kanban-work"
        assert "IDEA-001" in self._run(runner, ["list"]).output

    def test_v1_scanned_root_work_is_kept(self, repo_runner):
        repo, runner = repo_runner
        self._write_v1(repo, root="work/", scan_paths=["work/"])
        self._run(runner, ["board-add", "research", "--preset", "hdd", "--path", "research/"])

        assert self._saved_boards(repo)["default"]["path"].rstrip("/") == "work"

    # -- the new board works ---------------------------------------------------

    def test_new_research_board_lists_hdd_item(self, repo_runner):
        repo, runner = repo_runner
        self._run(runner, ["init", "--theme", "software"])
        self._run(runner, ["create", "idea", "Fresh probe item"])
        self._run(runner, ["board-add", "research", "--preset", "hdd", "--path", "research/"])

        created = self._run(runner, ["create", "hypothesis", "Probe hypothesis"])
        assert "research/" in created.output.replace("\n", "")
        listed = self._run(runner, ["list", "--board", "research"]).output
        assert "H-001" in listed
        assert list((repo / "research").rglob("H-001-*.md"))

    def test_existing_item_listed_on_default_board_after_board_add(self, repo_runner):
        _, runner = repo_runner
        self._run(runner, ["init", "--theme", "software"])
        self._run(runner, ["create", "idea", "Fresh probe item"])
        self._run(runner, ["board-add", "research", "--preset", "hdd", "--path", "research/"])

        assert "IDEA-001" in self._run(runner, ["list", "--board", "default"]).output

    # -- round 2: ignore list and root inside a scan path ----------------------

    def test_board_add_keeps_template_ignore_no_phantom_items(self, repo_runner):
        """init's _TEMPLATE.md files must not become XXX items after board-add."""
        import re

        repo, runner = repo_runner
        self._run(runner, ["init", "--theme", "software"])
        self._run(runner, ["create", "idea", "Fresh probe item"])
        assert "Backlog (1)" in self._run(runner, ["board"]).output

        self._run(runner, ["board-add", "research", "--preset", "hdd", "--path", "research/"])

        board_out = self._run(runner, ["board"]).output
        list_out = self._run(runner, ["list"]).output
        default_out = self._run(runner, ["list", "--board", "default"]).output
        for out in (list_out, default_out):
            phantoms = re.findall(r"\b[A-Z]+-XXX\b", out)
            assert not phantoms, f"template files listed as items: {phantoms}\n{out}"
        assert "Backlog (1)" in board_out, board_out

    def test_board_add_saved_default_board_keeps_template_ignore(self, repo_runner):
        repo, runner = repo_runner
        self._run(runner, ["init", "--theme", "software"])
        self._run(runner, ["board-add", "research", "--preset", "hdd", "--path", "research/"])

        default = self._saved_boards(repo)["default"]
        assert "**/_TEMPLATE*" in (default.get("ignore") or []), default

    def test_v1_root_inside_scan_path_keeps_scanned_items(self, repo_runner):
        """root work/sub/ inside scan path work/: the board must still cover work/."""
        repo, runner = repo_runner
        self._write_v1(repo, root="work/sub/", scan_paths=["work/"])
        (repo / "work" / "sub").mkdir(parents=True, exist_ok=True)
        (repo / "work" / "FEAT-001-Outer-item.md").write_text(
            "---\n"
            "id: FEAT-001\n"
            'title: "Outer item"\n'
            "type: feature\n"
            "status: backlog\n"
            "priority: medium\n"
            "assignee: null\n"
            "created: 2026-09-24\n"
            "depends_on: []\n"
            "---\n\n# Outer item\n"
        )
        assert "FEAT-001" in self._run(runner, ["list"]).output

        self._run(runner, ["board-add", "research", "--preset", "hdd", "--path", "research/"])

        assert "FEAT-001" in self._run(runner, ["list"]).output
        assert "FEAT-001" in self._run(runner, ["list", "--board", "default"]).output

    # -- negative controls (must stay green) -----------------------------------

    def test_control_board_add_on_multiboard_keeps_existing_paths(self, repo_runner):
        repo, runner = repo_runner
        cfg = repo / ".kanban" / "config.yaml"
        cfg.parent.mkdir(parents=True)
        cfg.write_text(
            'version: "2.0"\n'
            "boards:\n"
            "  - name: development\n"
            "    preset: nautical\n"
            "    path: ship-work/\n"
            "  - name: tasks\n"
            "    preset: software\n"
            "    path: work/\n"
            "default_board: development\n"
        )
        self._run(runner, ["board-add", "research", "--preset", "hdd", "--path", "research/"])

        boards = self._saved_boards(repo)
        assert boards["development"]["path"] == "ship-work/"
        assert boards["tasks"]["path"] == "work/"
        assert boards["research"]["path"] == "research/"

    def test_control_added_board_path_is_honoured(self, repo_runner):
        repo, runner = repo_runner
        self._run(runner, ["init", "--theme", "software"])
        self._run(runner, ["board-add", "lab", "--preset", "hdd", "--path", "lab-notes/"])

        assert self._saved_boards(repo)["lab"]["path"] == "lab-notes/"
        assert (repo / "lab-notes").is_dir()


class TestScanUsesEachBoardsIgnore:
    """list/show/move must apply each board's OWN ``ignore`` to that board.

    One board's ``ignore:`` patterns must not hide another board's items, and
    a board with no ``ignore:`` keeps the default ignore (``**/archive/**``).
    ``list``/``show``/``move`` must agree with ``board`` about which items each
    board has. (#124)
    """

    CONFIG = (
        "version: '2.0'\n"
        "boards:\n"
        "- name: alpha\n"
        "  preset: software\n"
        "  path: a/\n"
        '  ignore: ["**/drafts/**"]\n'
        "- name: beta\n"
        "  preset: software\n"
        "  path: b/\n"
    )

    ITEMS = {
        "a/tasks/TASK-001.md": "TASK-001",  # alpha, listed
        "b/drafts/TASK-002.md": "TASK-002",  # beta's draft: beta does not ignore drafts
        "a/drafts/TASK-003.md": "TASK-003",  # alpha's own draft: ignored by alpha
        "b/archive/TASK-004.md": "TASK-004",  # beta default ignore covers archive
    }

    @pytest.fixture
    def repo_runner(self, tmp_path, monkeypatch):
        """Git repo with the issue's two-board config and items, as cwd."""
        import subprocess

        from click.testing import CliRunner

        from yurtle_kanban import config as config_mod

        for cmd in (
            ["git", "init", "-b", "main"],
            ["git", "config", "user.email", "test@test.com"],
            ["git", "config", "user.name", "Test"],
        ):
            subprocess.run(cmd, cwd=tmp_path, capture_output=True, check=True)
        config_mod._theme_cache.clear()
        monkeypatch.chdir(tmp_path)
        yield tmp_path, CliRunner()
        config_mod._theme_cache.clear()

    @classmethod
    def _write_repo(cls, repo: Path, config: str | None = None) -> None:
        cfg = repo / ".kanban" / "config.yaml"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(config if config is not None else cls.CONFIG)
        for rel, item_id in cls.ITEMS.items():
            cls._write_item(repo / rel, item_id)

    @staticmethod
    def _write_item(path: Path, item_id: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "---\n"
            f"id: {item_id}\n"
            f'title: "Item {item_id}"\n'
            "type: task\n"
            "status: backlog\n"
            "priority: medium\n"
            "assignee: null\n"
            "created: 2026-09-24\n"
            "depends_on: []\n"
            "---\n\n"
            f"# Item {item_id}\n"
        )

    @staticmethod
    def _invoke(runner, args: list[str]):
        from yurtle_kanban.cli import main

        return runner.invoke(main, args, catch_exceptions=False)

    @classmethod
    def _run(cls, runner, args: list[str]):
        result = cls._invoke(runner, args)
        assert result.exit_code == 0, f"{args} failed:\n{result.output}"
        return result

    @classmethod
    def _listed_ids(cls, runner, extra: list[str] | None = None) -> set[str]:
        import json

        out = cls._run(runner, ["list", "--json", *(extra or [])]).output
        data = json.loads(out)
        items = data["items"] if isinstance(data, dict) else data
        return {i["id"] for i in items}

    @staticmethod
    def _service(repo: Path) -> KanbanService:
        config = KanbanConfig.load(repo / ".kanban" / "config.yaml")
        return KanbanService(config, repo)

    # -- the issue's own repro ------------------------------------------------

    def test_issue_repro_list_shows_other_boards_draft(self, repo_runner):
        """alpha's ``**/drafts/**`` must not hide beta's b/drafts/TASK-002."""
        repo, runner = repo_runner
        self._write_repo(repo)

        ids = self._listed_ids(runner)
        assert "TASK-001" in ids
        assert "TASK-002" in ids, f"beta's draft hidden by alpha's ignore: {ids}"

    def test_issue_repro_show_other_boards_draft(self, repo_runner):
        repo, runner = repo_runner
        self._write_repo(repo)

        result = self._invoke(runner, ["show", "TASK-002"])
        assert "Item not found" not in result.output, result.output
        assert result.exit_code == 0
        assert "Item TASK-002" in result.output

    def test_issue_repro_move_other_boards_draft(self, repo_runner):
        repo, runner = repo_runner
        self._write_repo(repo)

        result = self._invoke(runner, ["move", "TASK-002", "ready", "--no-commit"])
        assert "not found" not in result.output.lower(), result.output
        assert result.exit_code == 0, result.output
        assert "status: ready" in (repo / "b" / "drafts" / "TASK-002.md").read_text()

    # -- each board's own ignore still applies ----------------------------------

    def test_board_own_ignore_still_hides_its_drafts(self, repo_runner):
        repo, runner = repo_runner
        self._write_repo(repo)

        assert "TASK-003" not in self._listed_ids(runner)
        result = self._invoke(runner, ["show", "TASK-003"])
        assert "Item not found" in result.output, result.output

    def test_board_without_ignore_keeps_default_archive_ignore(self, repo_runner):
        """beta sets no ignore, so the default ``**/archive/**`` applies to it."""
        repo, runner = repo_runner
        self._write_repo(repo)

        assert "TASK-004" not in self._listed_ids(runner)
        result = self._invoke(runner, ["show", "TASK-004"])
        assert "Item not found" in result.output, result.output

    def test_list_matches_exactly_expected_items(self, repo_runner):
        repo, runner = repo_runner
        self._write_repo(repo)

        assert self._listed_ids(runner) == {"TASK-001", "TASK-002"}

    # -- list agrees with board ---------------------------------------------

    def test_list_agrees_with_board_per_board(self, repo_runner):
        """Items ``list`` shows == union of what ``board`` shows per board.."""
        repo, runner = repo_runner
        self._write_repo(repo)

        service = self._service(repo)
        per_board = {
            name: {i.id for i in service.get_board(board_name=name).items}
            for name in ("alpha", "beta")
        }
        assert per_board["beta"] == {"TASK-002"}
        assert per_board["alpha"] == {"TASK-001"}
        assert self._listed_ids(runner, ["--board", "beta"]) == per_board["beta"]
        assert self._listed_ids(runner, ["--board", "alpha"]) == per_board["alpha"]
        assert self._listed_ids(runner) == per_board["alpha"] | per_board["beta"]

    def test_board_command_beta_shows_draft(self, repo_runner):
        repo, runner = repo_runner
        self._write_repo(repo)

        board_out = self._run(runner, ["board", "beta"]).output
        assert "TASK-002" in board_out
        assert "TASK-002" in self._listed_ids(runner)

    def test_service_scan_uses_each_boards_ignore(self, repo_runner):
        repo, _ = repo_runner
        self._write_repo(repo)

        ids = {i.id for i in self._service(repo).scan()}
        assert ids == {"TASK-001", "TASK-002"}

    # -- negative controls (must stay green) -----------------------------------

    def test_control_94_board_add_no_template_phantoms(self, repo_runner):
        import re

        _, runner = repo_runner
        self._run(runner, ["init", "--theme", "software"])
        self._run(runner, ["create", "idea", "Fresh probe item"])
        self._run(runner, ["board-add", "research", "--preset", "hdd", "--path", "research/"])

        out = self._run(runner, ["list"]).output
        assert not re.findall(r"\b[A-Z]+-XXX\b", out), out
        assert "IDEA-001" in out

    def test_control_nusy_style_board_ignore_including_defaults(self, repo_runner):
        """A board whose ignore lists the defaults plus its own keeps working."""
        repo, runner = repo_runner
        config = (
            "version: '2.0'\n"
            "boards:\n"
            "- name: development\n"
            "  preset: software\n"
            "  path: kanban-work/\n"
            "  ignore:\n"
            '  - "**/archive/**"\n'
            '  - "**/templates/**"\n'
            '  - "**/_TEMPLATE*"\n'
            "- name: research\n"
            "  preset: software\n"
            "  path: research/\n"
            "  ignore:\n"
            '  - "**/archive/**"\n'
            '  - "**/templates/**"\n'
            "default_board: development\n"
        )
        cfg = repo / ".kanban" / "config.yaml"
        cfg.parent.mkdir(parents=True)
        cfg.write_text(config)
        self._write_item(repo / "kanban-work/tasks/TASK-010.md", "TASK-010")
        self._write_item(repo / "kanban-work/tasks/_TEMPLATE.md", "TASK-XXX")
        self._write_item(repo / "kanban-work/archive/TASK-011.md", "TASK-011")
        self._write_item(repo / "kanban-work/templates/TASK-012.md", "TASK-012")
        self._write_item(repo / "research/tasks/TASK-020.md", "TASK-020")
        self._write_item(repo / "research/archive/TASK-021.md", "TASK-021")

        assert self._listed_ids(runner) == {"TASK-010", "TASK-020"}
        assert "Item not found" not in self._invoke(runner, ["show", "TASK-020"]).output

    def test_control_single_board_v1_ignore_unchanged(self, repo_runner):
        repo, runner = repo_runner
        cfg = repo / ".kanban" / "config.yaml"
        cfg.parent.mkdir(parents=True)
        cfg.write_text(
            "kanban:\n"
            "  theme: software\n"
            "  paths:\n"
            "    root: work/\n"
            "    scan_paths:\n"
            '    - "work/"\n'
            "    ignore:\n"
            '      - "**/drafts/**"\n'
        )
        self._write_item(repo / "work/tasks/TASK-030.md", "TASK-030")
        self._write_item(repo / "work/drafts/TASK-031.md", "TASK-031")
        self._write_item(repo / "work/archive/TASK-032.md", "TASK-032")

        # v1 ignore is exactly what the config lists (no default merged in).
        assert self._listed_ids(runner) == {"TASK-030", "TASK-032"}


class TestCreateHonoursDefaultBoard:
    """Multi-board ``create`` with no board uses ``default_board`` when its
    theme defines the type; otherwise the first board in config order that
    does (today's behaviour). Configs without ``default_board``, or where only
    one board defines the type, are unchanged (#114)."""

    @pytest.fixture
    def repo_runner(self, tmp_path, monkeypatch):
        """Empty git repo as cwd, fresh theme cache, and a CliRunner."""
        import subprocess

        from click.testing import CliRunner

        from yurtle_kanban import config as config_mod

        for cmd in (
            ["git", "init", "-b", "main"],
            ["git", "config", "user.email", "test@test.com"],
            ["git", "config", "user.name", "Test"],
        ):
            subprocess.run(cmd, cwd=tmp_path, capture_output=True, check=True)
        config_mod._theme_cache.clear()
        monkeypatch.chdir(tmp_path)
        yield tmp_path, CliRunner()
        config_mod._theme_cache.clear()

    @staticmethod
    def _write_config(repo: Path, text: str) -> None:
        cfg = repo / ".kanban" / "config.yaml"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(text)

    @staticmethod
    def _run(runner, args: list[str]):
        from yurtle_kanban.cli import main

        result = runner.invoke(main, args, catch_exceptions=False)
        assert result.exit_code == 0, f"{args} failed:\n{result.output}"
        return result

    @classmethod
    def _create(cls, runner, repo: Path, item_type: str, title: str) -> tuple[str, str]:
        """Run ``create`` and return (item id, file path relative to repo)."""
        import re

        out = cls._run(runner, ["create", item_type, title]).output
        m_id = re.search(r"Created (\S+):", out)
        assert m_id, out
        item_id = m_id.group(1)
        # Rich wraps the "File:" line, so find the new file by its id instead.
        matches = [
            f for f in repo.rglob(f"{item_id}*.md") if ".kanban" not in f.parts
        ]
        assert len(matches) == 1, f"expected one file for {item_id}: {matches}\n{out}"
        rel = matches[0].resolve().relative_to(repo.resolve()).as_posix()
        return item_id, rel

    @classmethod
    def _listed_ids(cls, runner, board: str) -> set[str]:
        import json

        out = cls._run(runner, ["list", "--json", "--board", board]).output
        # An empty board prints plain text, not JSON.
        if "No work items found" in out:
            return set()
        data = json.loads(out)
        items = data["items"] if isinstance(data, dict) else data
        return {i["id"] for i in items}

    # -- Do: default_board wins when its theme defines the type ---------------

    def test_default_board_wins_when_both_boards_define_type(self, repo_runner):
        """dev + ops both software; default_board ops → feature lands on ops."""
        repo, runner = repo_runner
        self._write_config(
            repo,
            "version: '2.0'\n"
            "boards:\n"
            "- name: dev\n"
            "  preset: software\n"
            "  path: dev/\n"
            "- name: ops\n"
            "  preset: software\n"
            "  path: ops/\n"
            "default_board: ops\n",
        )

        item_id, rel = self._create(runner, repo, "feature", "probe")

        assert rel.startswith("ops/"), f"feature should land under ops/, got {rel}"
        assert item_id in self._listed_ids(runner, "ops")
        assert item_id not in self._listed_ids(runner, "dev")

    def test_default_board_wins_for_type_shared_by_different_themes(self, repo_runner):
        """idea is defined by software AND hdd; default_board research → research/."""
        repo, runner = repo_runner
        self._write_config(
            repo,
            "version: '2.0'\n"
            "boards:\n"
            "- name: development\n"
            "  preset: software\n"
            "  path: kanban-work/\n"
            "- name: research\n"
            "  preset: hdd\n"
            "  path: research/\n"
            "default_board: research\n",
        )

        item_id, rel = self._create(runner, repo, "idea", "probe idea")

        assert rel.startswith("research/"), f"idea should land under research/, got {rel}"
        assert item_id in self._listed_ids(runner, "research")

    # -- Do: fallback when default_board's theme lacks the type ---------------

    def test_default_board_without_type_falls_back_to_first_in_config_order(
        self, repo_runner,
    ):
        """default_board research (hdd) has no feature → first software board."""
        repo, runner = repo_runner
        self._write_config(
            repo,
            "version: '2.0'\n"
            "boards:\n"
            "- name: research\n"
            "  preset: hdd\n"
            "  path: research/\n"
            "- name: alpha\n"
            "  preset: software\n"
            "  path: alpha/\n"
            "- name: beta\n"
            "  preset: software\n"
            "  path: beta/\n"
            "default_board: research\n",
        )

        item_id, rel = self._create(runner, repo, "feature", "fallback probe")

        assert rel.startswith("alpha/"), f"feature should fall back to alpha/, got {rel}"
        assert item_id in self._listed_ids(runner, "alpha")

    # -- Must stay true: negative controls ------------------------------------

    def test_control_no_default_board_first_in_config_order(self, repo_runner):
        """No default_board → first board in config order defining the type."""
        repo, runner = repo_runner
        self._write_config(
            repo,
            "version: '2.0'\n"
            "boards:\n"
            "- name: dev\n"
            "  preset: software\n"
            "  path: dev/\n"
            "- name: ops\n"
            "  preset: software\n"
            "  path: ops/\n",
        )

        item_id, rel = self._create(runner, repo, "feature", "no default probe")

        assert rel.startswith("dev/"), f"feature should land under dev/, got {rel}"
        assert item_id in self._listed_ids(runner, "dev")

    def test_control_only_one_board_defines_type_ignores_default_board(self, repo_runner):
        """hypothesis only in hdd → research, even with default_board development."""
        repo, runner = repo_runner
        self._write_config(
            repo,
            "version: '2.0'\n"
            "boards:\n"
            "- name: development\n"
            "  preset: nautical\n"
            "  path: kanban-work/\n"
            "- name: research\n"
            "  preset: hdd\n"
            "  path: research/\n"
            "default_board: development\n",
        )

        item_id, rel = self._create(runner, repo, "hypothesis", "only hdd has it")

        assert rel.startswith("research/hypotheses/"), rel
        assert item_id in self._listed_ids(runner, "research")

    def test_control_only_one_board_defines_type_default_elsewhere(self, repo_runner):
        """expedition only in nautical → kanban-work, even with default_board research."""
        repo, runner = repo_runner
        self._write_config(
            repo,
            "version: '2.0'\n"
            "boards:\n"
            "- name: development\n"
            "  preset: nautical\n"
            "  path: kanban-work/\n"
            "- name: research\n"
            "  preset: hdd\n"
            "  path: research/\n"
            "default_board: research\n",
        )

        item_id, rel = self._create(runner, repo, "expedition", "only nautical has it")

        assert rel.startswith("kanban-work/expeditions/"), rel
        assert item_id in self._listed_ids(runner, "development")

    def test_control_nusy_product_team_config(self, repo_runner):
        """nusy-product-team shape (no default_board): each type to its own board."""
        repo, runner = repo_runner
        self._write_config(
            repo,
            "version: '2.0'\n"
            "boards:\n"
            "- name: development\n"
            "  preset: nautical\n"
            "  path: kanban-work/\n"
            "- name: research\n"
            "  preset: hdd\n"
            "  path: research/\n",
        )

        exp_id, exp_rel = self._create(runner, repo, "expedition", "nusy expedition")
        hyp_id, hyp_rel = self._create(runner, repo, "hypothesis", "nusy hypothesis")

        assert exp_rel.startswith("kanban-work/expeditions/"), exp_rel
        assert hyp_rel.startswith("research/hypotheses/"), hyp_rel
        assert exp_id in self._listed_ids(runner, "development")
        assert hyp_id in self._listed_ids(runner, "research")

    def test_control_explicit_board_wins_over_default_board(self, repo_runner):
        """The service's explicit board_name beats default_board.

        ``create`` has no --board option, so this exercises the service's
        board-aware placement directly.
        """
        repo, _runner = repo_runner
        self._write_config(
            repo,
            "version: '2.0'\n"
            "boards:\n"
            "- name: dev\n"
            "  preset: software\n"
            "  path: dev/\n"
            "- name: ops\n"
            "  preset: software\n"
            "  path: ops/\n"
            "default_board: ops\n",
        )
        config = KanbanConfig.load(repo / ".kanban" / "config.yaml")
        service = KanbanService(config, repo)

        type_dir = service._get_type_directory(WorkItemType.FEATURE, board_name="dev")

        rel = type_dir.resolve().relative_to(repo.resolve()).as_posix()
        assert rel.startswith("dev/"), f"explicit dev should win, got {rel}"
