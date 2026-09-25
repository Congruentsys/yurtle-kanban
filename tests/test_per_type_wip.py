"""Tests for per-type WIP limits (Issue #65).

Tests cover:
- Column model: get_wip_limit(), is_over_wip() with per-type limits
- Board model: get_wip_violations() with per-type limits
- Config parsing: YAML per-type wip_limits, null board limits
- Yurtle WIP policy loading
- Service: move_item() type-aware WIP enforcement
- Service: _apply_wip_overrides() for board config → column mapping
"""

from __future__ import annotations

from pathlib import Path

import pytest

from yurtle_kanban.config import BoardConfig, KanbanConfig, load_wip_policy
from yurtle_kanban.models import Board, Column, WorkItem, WorkItemStatus, WorkItemType

# ---------------------------------------------------------------------------
# Column model tests
# ---------------------------------------------------------------------------


class TestColumnPerTypeWIP:
    """Test Column.get_wip_limit() and is_over_wip() with per-type limits."""

    def test_legacy_wip_limit_unchanged(self):
        """Legacy wip_limit still works when no type_wip_limits."""
        col = Column("in_progress", "In Progress", 3, wip_limit=5)
        assert col.get_wip_limit() == 5
        assert col.get_wip_limit("expedition") == 5
        assert col.is_over_wip(5) is False
        assert col.is_over_wip(6) is True

    def test_per_type_limit_for_known_type(self):
        """Per-type limit returned for a configured type."""
        col = Column(
            "in_progress", "Underway", 3,
            type_wip_limits={"expedition": 5, "voyage": 3},
        )
        assert col.get_wip_limit("expedition") == 5
        assert col.get_wip_limit("voyage") == 3

    def test_per_type_unlimited(self):
        """None value means unlimited for that type."""
        col = Column(
            "in_progress", "Underway", 3,
            type_wip_limits={"expedition": 5, "chore": None},
        )
        assert col.get_wip_limit("chore") is None
        assert col.is_over_wip(100, item_type="chore") is False

    def test_per_type_default_fallback(self):
        """_default used for types not explicitly listed."""
        col = Column(
            "in_progress", "Underway", 3,
            type_wip_limits={"expedition": 5, "_default": 10},
        )
        assert col.get_wip_limit("hazard") == 10
        assert col.get_wip_limit("signal") == 10

    def test_per_type_no_default_falls_to_legacy(self):
        """When type_wip_limits has no match and no _default, use legacy."""
        col = Column(
            "in_progress", "Underway", 3,
            wip_limit=8,
            type_wip_limits={"expedition": 5},
        )
        assert col.get_wip_limit("hazard") == 8

    def test_per_type_no_default_no_legacy(self):
        """No match, no _default, no legacy → None (unlimited)."""
        col = Column(
            "in_progress", "Underway", 3,
            type_wip_limits={"expedition": 5},
        )
        assert col.get_wip_limit("hazard") is None
        assert col.is_over_wip(100, item_type="hazard") is False

    def test_is_over_wip_with_type(self):
        """is_over_wip checks per-type limit correctly."""
        col = Column(
            "in_progress", "Underway", 3,
            type_wip_limits={"expedition": 3, "chore": None},
        )
        assert col.is_over_wip(3, item_type="expedition") is False
        assert col.is_over_wip(4, item_type="expedition") is True
        assert col.is_over_wip(999, item_type="chore") is False

    def test_no_wip_at_all(self):
        """Column with no limits at all."""
        col = Column("backlog", "Harbor", 1)
        assert col.get_wip_limit() is None
        assert col.get_wip_limit("expedition") is None
        assert col.is_over_wip(100) is False


# ---------------------------------------------------------------------------
# Board model tests
# ---------------------------------------------------------------------------


def _make_item(
    item_id: str,
    item_type: WorkItemType,
    status: WorkItemStatus,
) -> WorkItem:
    return WorkItem(
        id=item_id,
        title=f"Test {item_id}",
        item_type=item_type,
        status=status,
        file_path=Path(f"/tmp/{item_id}.md"),
    )


class TestBoardPerTypeViolations:
    """Test Board.get_wip_violations() with per-type limits."""

    def test_aggregate_violation_backward_compat(self):
        """Legacy aggregate violations still work (returns None for type)."""
        board = Board(
            id="test",
            name="Test",
            columns=[
                Column("in_progress", "In Progress", 1, wip_limit=2),
            ],
            items=[
                _make_item("EXP-1", WorkItemType.EXPEDITION, WorkItemStatus.IN_PROGRESS),
                _make_item("EXP-2", WorkItemType.EXPEDITION, WorkItemStatus.IN_PROGRESS),
                _make_item("EXP-3", WorkItemType.EXPEDITION, WorkItemStatus.IN_PROGRESS),
            ],
        )
        violations = board.get_wip_violations()
        assert len(violations) == 1
        col, count, item_type = violations[0]
        assert col.id == "in_progress"
        assert count == 3
        assert item_type is None

    def test_per_type_violation(self):
        """Per-type violation detected correctly."""
        board = Board(
            id="test",
            name="Test",
            columns=[
                Column(
                    "in_progress", "Underway", 1,
                    type_wip_limits={"expedition": 2, "chore": None},
                ),
            ],
            items=[
                _make_item("EXP-1", WorkItemType.EXPEDITION, WorkItemStatus.IN_PROGRESS),
                _make_item("EXP-2", WorkItemType.EXPEDITION, WorkItemStatus.IN_PROGRESS),
                _make_item("EXP-3", WorkItemType.EXPEDITION, WorkItemStatus.IN_PROGRESS),
                _make_item("CHORE-1", WorkItemType.CHORE, WorkItemStatus.IN_PROGRESS),
                _make_item("CHORE-2", WorkItemType.CHORE, WorkItemStatus.IN_PROGRESS),
            ],
        )
        violations = board.get_wip_violations()
        # Only expeditions should violate (3 > 2), chores are unlimited
        assert len(violations) == 1
        col, count, item_type = violations[0]
        assert item_type == "expedition"
        assert count == 3

    def test_no_violation_when_within_limits(self):
        """No violations when all types within limits."""
        board = Board(
            id="test",
            name="Test",
            columns=[
                Column(
                    "in_progress", "Underway", 1,
                    type_wip_limits={"expedition": 5, "chore": None},
                ),
            ],
            items=[
                _make_item("EXP-1", WorkItemType.EXPEDITION, WorkItemStatus.IN_PROGRESS),
                _make_item("CHORE-1", WorkItemType.CHORE, WorkItemStatus.IN_PROGRESS),
            ],
        )
        violations = board.get_wip_violations()
        assert len(violations) == 0

    def test_get_items_by_status_and_type(self):
        """Board.get_items_by_status_and_type works correctly."""
        board = Board(
            id="test",
            name="Test",
            columns=[],
            items=[
                _make_item("EXP-1", WorkItemType.EXPEDITION, WorkItemStatus.IN_PROGRESS),
                _make_item("EXP-2", WorkItemType.EXPEDITION, WorkItemStatus.DONE),
                _make_item("CHORE-1", WorkItemType.CHORE, WorkItemStatus.IN_PROGRESS),
            ],
        )
        result = board.get_items_by_status_and_type(
            WorkItemStatus.IN_PROGRESS, WorkItemType.EXPEDITION
        )
        assert len(result) == 1
        assert result[0].id == "EXP-1"


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------


class TestBoardConfigPerTypeWIP:
    """Test BoardConfig parsing of per-type WIP limits."""

    def test_legacy_int_wip_limits(self):
        """Legacy integer wip_limits still parse correctly."""
        bc = BoardConfig.from_dict({
            "name": "dev",
            "wip_limits": {"underway": 4, "approaching": 3},
        })
        assert bc.wip_limits == {"underway": 4, "approaching": 3}

    def test_per_type_dict_wip_limits(self):
        """Per-type dict values parse correctly."""
        bc = BoardConfig.from_dict({
            "name": "dev",
            "wip_limits": {
                "underway": {
                    "expedition": 5,
                    "voyage": 5,
                    "chore": None,
                    "_default": 10,
                },
            },
        })
        assert bc.wip_limits["underway"] == {
            "expedition": 5,
            "voyage": 5,
            "chore": None,
            "_default": 10,
        }

    def test_null_board_wip_limits(self):
        """Null wip_limits means no limits on the board."""
        bc = BoardConfig.from_dict({
            "name": "research",
            "wip_limits": None,
        })
        assert bc.wip_limits is None

    def test_missing_wip_limits_defaults_to_empty(self):
        """Missing wip_limits defaults to empty dict."""
        bc = BoardConfig.from_dict({"name": "dev"})
        assert bc.wip_limits == {}

    def test_mixed_int_and_dict(self):
        """Some columns use legacy int, others use per-type dict."""
        bc = BoardConfig.from_dict({
            "name": "dev",
            "wip_limits": {
                "provisioning": 50,  # legacy
                "underway": {"expedition": 5, "chore": None},  # per-type
            },
        })
        assert bc.wip_limits["provisioning"] == 50
        assert isinstance(bc.wip_limits["underway"], dict)


class TestKanbanConfigV2WIP:
    """Test full config loading with per-type WIP."""

    def test_v2_config_with_per_type_wip(self, tmp_path):
        """v2 config with per-type WIP limits loads correctly."""
        import yaml
        config_data = {
            "version": "2.0",
            "boards": [
                {
                    "name": "development",
                    "preset": "nautical",
                    "path": "kanban-work/",
                    "wip_limits": {
                        "in_progress": {
                            "expedition": 5,
                            "voyage": 5,
                            "chore": None,
                        },
                    },
                },
                {
                    "name": "research",
                    "preset": "hdd",
                    "path": "research/",
                    "wip_limits": None,
                },
            ],
        }
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(config_data))

        config = KanbanConfig.load(config_path)
        assert config.is_multi_board

        dev = config.get_board("development")
        assert dev is not None
        assert isinstance(dev.wip_limits["in_progress"], dict)
        assert dev.wip_limits["in_progress"]["expedition"] == 5

        research = config.get_board("research")
        assert research is not None
        assert research.wip_limits is None


# ---------------------------------------------------------------------------
# Yurtle WIP Policy tests
# ---------------------------------------------------------------------------


class TestYurtleWIPPolicy:
    """Test loading WIP policy from Yurtle markdown files."""

    def test_no_policy_file(self, tmp_path):
        """Returns None when no policy file exists."""
        result = load_wip_policy(tmp_path)
        assert result is None

    def test_policy_with_turtle_blocks(self, tmp_path):
        """Parse WIP policy from turtle fenced blocks in markdown."""
        policy_content = """---
title: WIP Policy
---

# WIP Policy

Per-type work-in-progress limits.

```turtle
@prefix wip: <https://yurtle.dev/kanban/wip/> .
@prefix kb: <https://yurtle.dev/kanban/> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

<#development> rdf:type wip:Policy ;
    wip:board "development" .

<#dev-underway-expedition> rdf:type wip:TypeLimit ;
    wip:policy <#development> ;
    wip:column "in_progress" ;
    wip:itemType "expedition" ;
    wip:limit 5 .

<#dev-underway-chore> rdf:type wip:TypeLimit ;
    wip:policy <#development> ;
    wip:column "in_progress" ;
    wip:itemType "chore" ;
    wip:unlimited "true"^^xsd:boolean .
```
"""
        (tmp_path / "wip-policy.md").write_text(policy_content)

        result = load_wip_policy(tmp_path)
        assert result is not None
        assert "development" in result

        dev_wip = result["development"]
        assert isinstance(dev_wip, dict)
        assert "in_progress" in dev_wip
        col_limits = dev_wip["in_progress"]
        assert isinstance(col_limits, dict)
        assert col_limits["expedition"] == 5
        assert col_limits["chore"] is None  # unlimited

    def test_policy_with_unlimited_board(self, tmp_path):
        """Board marked unlimited in Yurtle policy."""
        policy_content = """---
title: WIP Policy
---

# WIP Policy

```turtle
@prefix wip: <https://yurtle.dev/kanban/wip/> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

<#research> rdf:type wip:Policy ;
    wip:board "research" ;
    wip:unlimited "true"^^xsd:boolean .
```
"""
        (tmp_path / "wip-policy.md").write_text(policy_content)

        result = load_wip_policy(tmp_path)
        assert result is not None
        assert result["research"] is None  # unlimited


# ---------------------------------------------------------------------------
# Service integration tests
# ---------------------------------------------------------------------------


class TestServiceApplyWIPOverrides:
    """Test _apply_wip_overrides in KanbanService."""

    def _make_columns(self) -> list[Column]:
        return [
            Column("backlog", "Harbor", 1),
            Column("in_progress", "Underway", 3, wip_limit=10),
            Column("review", "Approaching", 4, wip_limit=10),
            Column("done", "Arrived", 5),
        ]

    def test_legacy_int_override(self):
        """Legacy int overrides replace theme wip_limit."""
        from yurtle_kanban.service import KanbanService

        columns = self._make_columns()
        bc = BoardConfig(name="dev", wip_limits={"in_progress": 4})

        service = KanbanService.__new__(KanbanService)
        service.repo_root = Path("/tmp/nonexistent")
        result = service._apply_wip_overrides(columns, bc)

        ip_col = next(c for c in result if c.id == "in_progress")
        assert ip_col.wip_limit == 4

    def test_per_type_override(self):
        """Per-type dict overrides set type_wip_limits and clear wip_limit."""
        from yurtle_kanban.service import KanbanService

        columns = self._make_columns()
        bc = BoardConfig(
            name="dev",
            wip_limits={
                "in_progress": {"expedition": 5, "chore": None, "_default": 8},
            },
        )

        service = KanbanService.__new__(KanbanService)
        service.repo_root = Path("/tmp/nonexistent")
        result = service._apply_wip_overrides(columns, bc)

        ip_col = next(c for c in result if c.id == "in_progress")
        assert ip_col.wip_limit is None  # cleared
        assert ip_col.type_wip_limits == {
            "expedition": 5,
            "chore": None,
            "_default": 8,
        }

    def test_null_board_clears_all(self):
        """Null wip_limits clears all limits on all columns."""
        from yurtle_kanban.service import KanbanService

        columns = self._make_columns()
        bc = BoardConfig(name="research", wip_limits=None)

        service = KanbanService.__new__(KanbanService)
        service.repo_root = Path("/tmp/nonexistent")
        result = service._apply_wip_overrides(columns, bc)

        for col in result:
            assert col.wip_limit is None
            assert col.type_wip_limits is None

    def test_unmentioned_columns_keep_theme_defaults(self):
        """Columns not in wip_limits keep their theme defaults."""
        from yurtle_kanban.service import KanbanService

        columns = self._make_columns()
        bc = BoardConfig(name="dev", wip_limits={"in_progress": 4})

        service = KanbanService.__new__(KanbanService)
        service.repo_root = Path("/tmp/nonexistent")
        result = service._apply_wip_overrides(columns, bc)

        review_col = next(c for c in result if c.id == "review")
        assert review_col.wip_limit == 10  # theme default preserved


# ---------------------------------------------------------------------------
# End-to-end move_item tests
# ---------------------------------------------------------------------------


class TestMoveItemPerTypeWIP:
    """End-to-end test: move_item blocks on per-type WIP limits."""

    @pytest.fixture
    def per_type_repo(self, tmp_path):
        """Set up a repo with per-type WIP limits and items at the limit."""
        import subprocess

        subprocess.run(
            ["git", "init", "-b", "main"], cwd=tmp_path,
            capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=tmp_path, capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=tmp_path, capture_output=True, check=True,
        )

        # Create dirs
        (tmp_path / ".kanban").mkdir()
        (tmp_path / "kanban-work" / "expeditions").mkdir(parents=True)
        (tmp_path / "kanban-work" / "chores").mkdir(parents=True)

        # Write v2 config with per-type WIP: 2 expeditions max in_progress
        import yaml
        config = {
            "version": "2.0",
            "boards": [{
                "name": "development",
                "preset": "nautical",
                "path": "kanban-work/",
                "scan_paths": [
                    "kanban-work/expeditions/",
                    "kanban-work/chores/",
                ],
                "wip_limits": {
                    "in_progress": {
                        "expedition": 2,
                        "chore": None,
                    },
                },
            }],
            "default_board": "development",
        }
        (tmp_path / ".kanban" / "config.yaml").write_text(yaml.dump(config))

        # Create 2 expeditions already in_progress (at limit)
        for i in range(1, 3):
            (tmp_path / "kanban-work" / "expeditions" / f"EXP-{i}.md").write_text(
                f"---\nid: EXP-{i}\ntitle: Test {i}\ntype: expedition\n"
                f"status: in_progress\n---\n\n# Test {i}\n"
            )
        # Create 1 expedition in backlog (candidate to move)
        (tmp_path / "kanban-work" / "expeditions" / "EXP-3.md").write_text(
            "---\nid: EXP-3\ntitle: Test 3\ntype: expedition\n"
            "status: backlog\n---\n\n# Test 3\n"
        )
        # Create 1 chore in backlog (should NOT be blocked)
        (tmp_path / "kanban-work" / "chores" / "CHORE-1.md").write_text(
            "---\nid: CHORE-1\ntitle: Cleanup\ntype: chore\n"
            "status: backlog\n---\n\n# Cleanup\n"
        )

        # Initial commit
        subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=tmp_path, capture_output=True,
        )

        return tmp_path

    def _make_service(self, repo_root):
        from yurtle_kanban.service import KanbanService

        config = KanbanConfig.load(repo_root / ".kanban" / "config.yaml")
        return KanbanService(config=config, repo_root=repo_root)

    def test_move_expedition_blocked_by_per_type_limit(self, per_type_repo):
        """Moving a 3rd expedition to in_progress should be blocked."""
        service = self._make_service(per_type_repo)

        with pytest.raises(ValueError, match="WIP limit reached for expeditions"):
            service.move_item(
                "EXP-3",
                WorkItemStatus.IN_PROGRESS,
                commit=False,
                validate_workflow=False,
            )

    def test_move_chore_allowed_despite_expedition_limit(self, per_type_repo):
        """Moving a chore should succeed even when expedition limit is hit."""
        service = self._make_service(per_type_repo)

        # Chore type is unlimited — should not be blocked
        item = service.move_item(
            "CHORE-1",
            WorkItemStatus.IN_PROGRESS,
            commit=False,
            validate_workflow=False,
        )
        assert item.status == WorkItemStatus.IN_PROGRESS

    def test_force_bypasses_per_type_limit(self, per_type_repo):
        """--force (skip_wip_check=True) should bypass per-type limits."""
        service = self._make_service(per_type_repo)

        item = service.move_item(
            "EXP-3",
            WorkItemStatus.IN_PROGRESS,
            commit=False,
            validate_workflow=False,
            skip_wip_check=True,
        )
        assert item.status == WorkItemStatus.IN_PROGRESS


class TestBoardConfigRoundtrip:
    """Test that BoardConfig with per-type WIP survives save/load."""

    def test_null_wip_limits_roundtrip(self, tmp_path):
        """wip_limits=None (unlimited) survives to_dict → from_dict."""
        bc = BoardConfig(name="research", wip_limits=None)
        d = bc.to_dict()
        assert "wip_limits" in d
        assert d["wip_limits"] is None

        restored = BoardConfig.from_dict(d)
        assert restored.wip_limits is None

    def test_per_type_wip_limits_roundtrip(self):
        """Per-type dict wip_limits survives to_dict → from_dict."""
        bc = BoardConfig(
            name="dev",
            wip_limits={"in_progress": {"expedition": 5, "chore": None}},
        )
        d = bc.to_dict()
        restored = BoardConfig.from_dict(d)
        assert restored.wip_limits["in_progress"]["expedition"] == 5
        assert restored.wip_limits["in_progress"]["chore"] is None
