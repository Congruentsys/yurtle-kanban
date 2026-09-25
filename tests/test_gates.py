"""Tests for transition gate evaluation — EXP-1063."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from yurtle_kanban.cli import main
from yurtle_kanban.config import BoardConfig, KanbanConfig
from yurtle_kanban.gates import GateEvaluator
from yurtle_kanban.models import WorkItem, WorkItemStatus, WorkItemType
from yurtle_kanban.service import KanbanService

# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def base_item():
    """A minimal WorkItem for unit tests."""
    return WorkItem(
        id="EXP-001",
        title="Test Expedition",
        item_type=WorkItemType.EXPEDITION,
        status=WorkItemStatus.IN_PROGRESS,
        file_path=Path("test.md"),
    )


@pytest.fixture
def kanban_with_gates(tmp_path):
    """Minimal kanban setup with gates configured on the dev board."""
    config_path = tmp_path / ".kanban" / "config.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        """
version: "2.0"
boards:
  - name: development
    preset: nautical
    path: work/
    gates:
      "* -> in_progress":
        - id: require_assignee
          check: item.assignee
          message: "Assignee required (use --assign)"
      "in_progress -> review":
        - id: require_self_review
          check: context.self_reviewed
          message: "Self-review required (use --self-reviewed)"
      "review -> done":
        - id: require_resolution
          check: item.resolution
          message: "Resolution required (set resolution: in frontmatter)"
default_board: development
"""
    )

    (tmp_path / "work" / "expeditions").mkdir(parents=True)

    item_file = tmp_path / "work" / "expeditions" / "EXP-100.md"
    item_file.write_text(
        """---
id: EXP-100
title: "Test Expedition"
type: expedition
status: backlog
assignee: Mini
---
# Test Expedition
"""
    )

    config = KanbanConfig.load(config_path)
    service = KanbanService(config, tmp_path)
    return {"service": service, "item_file": item_file, "tmp_path": tmp_path}


@pytest.fixture
def kanban_no_gates(tmp_path):
    """Minimal kanban setup with NO gates configured."""
    config_path = tmp_path / ".kanban" / "config.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        """
version: "2.0"
boards:
  - name: development
    preset: nautical
    path: work/
default_board: development
"""
    )

    (tmp_path / "work" / "expeditions").mkdir(parents=True)

    item_file = tmp_path / "work" / "expeditions" / "EXP-200.md"
    item_file.write_text(
        """---
id: EXP-200
title: "No Gate Expedition"
type: expedition
status: backlog
---
# No Gate Expedition
"""
    )

    config = KanbanConfig.load(config_path)
    service = KanbanService(config, tmp_path)
    return {"service": service, "item_file": item_file}


@pytest.fixture
def kanban_v1_with_gates(tmp_path):
    """Minimal v1 single-board kanban setup with gates configured."""
    config_path = tmp_path / ".kanban" / "config.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        """
kanban:
  theme: software
  paths:
    root: work/
  workflows: {}
  gates:
    "* -> in_progress":
      - id: require_assignee
        check: item.assignee
        message: "Assignee required (use --assign)"
    "in_progress -> review":
      - id: require_self_review
        check: context.self_reviewed
        message: "Self-review required (use --self-reviewed)"
"""
    )

    (tmp_path / "work").mkdir(parents=True)

    item_file = tmp_path / "work" / "EXP-300.md"
    item_file.write_text(
        """---
id: EXP-300
title: "V1 Gate Test"
type: expedition
status: backlog
assignee: Mini
---
# V1 Gate Test
"""
    )

    config = KanbanConfig.load(config_path)
    service = KanbanService(config, tmp_path)
    return {"service": service, "item_file": item_file, "config": config}


# ── TestGateEvaluation (unit) ──────────────────────────────────────────


class TestGateEvaluation:
    """Unit tests for GateEvaluator check expressions."""

    def test_assignee_gate_passes(self, base_item):
        """item.assignee check passes when assignee is set."""
        base_item.assignee = "Mini"
        evaluator = GateEvaluator(
            {
                "* -> in_progress": [
                    {"id": "require_assignee", "check": "item.assignee", "message": "Need assignee"}
                ]
            }
        )
        results = evaluator.evaluate(base_item, "backlog", "in_progress")
        assert len(results) == 1
        assert results[0].passed is True

    def test_assignee_gate_fails_when_none(self, base_item):
        """item.assignee check fails when assignee is None."""
        base_item.assignee = None
        evaluator = GateEvaluator(
            {
                "* -> in_progress": [
                    {"id": "require_assignee", "check": "item.assignee", "message": "Need assignee"}
                ]
            }
        )
        results = evaluator.evaluate(base_item, "backlog", "in_progress")
        assert len(results) == 1
        assert results[0].passed is False

    def test_assignee_gate_fails_when_empty(self, base_item):
        """item.assignee check fails when assignee is empty string."""
        base_item.assignee = ""
        evaluator = GateEvaluator(
            {
                "* -> in_progress": [
                    {"id": "require_assignee", "check": "item.assignee", "message": "Need assignee"}
                ]
            }
        )
        results = evaluator.evaluate(base_item, "backlog", "in_progress")
        assert results[0].passed is False

    def test_description_gate_passes(self, base_item):
        """item.description check passes when description is set."""
        base_item.description = "This expedition does something"
        evaluator = GateEvaluator(
            {
                "in_progress -> review": [
                    {"id": "require_desc", "check": "item.description", "message": "Need desc"}
                ]
            }
        )
        results = evaluator.evaluate(base_item, "in_progress", "review")
        assert results[0].passed is True

    def test_description_gate_fails(self, base_item):
        """item.description check fails when description is None."""
        base_item.description = None
        evaluator = GateEvaluator(
            {
                "in_progress -> review": [
                    {"id": "require_desc", "check": "item.description", "message": "Need desc"}
                ]
            }
        )
        results = evaluator.evaluate(base_item, "in_progress", "review")
        assert results[0].passed is False

    def test_context_gate_passes(self, base_item):
        """context.self_reviewed check passes when context key is True."""
        evaluator = GateEvaluator(
            {
                "in_progress -> review": [
                    {"id": "self_review", "check": "context.self_reviewed", "message": "Need review"}
                ]
            }
        )
        results = evaluator.evaluate(
            base_item, "in_progress", "review", context={"self_reviewed": True}
        )
        assert results[0].passed is True

    def test_context_gate_fails(self, base_item):
        """context.self_reviewed check fails when context key is missing."""
        evaluator = GateEvaluator(
            {
                "in_progress -> review": [
                    {"id": "self_review", "check": "context.self_reviewed", "message": "Need review"}
                ]
            }
        )
        results = evaluator.evaluate(base_item, "in_progress", "review")
        assert results[0].passed is False

    def test_unknown_check_fails_closed(self, base_item):
        """Unknown check expression returns False (fail-closed)."""
        evaluator = GateEvaluator(
            {
                "* -> review": [
                    {"id": "unknown", "check": "some_unknown_thing", "message": "Unknown"}
                ]
            }
        )
        results = evaluator.evaluate(base_item, "in_progress", "review")
        assert results[0].passed is False

    def test_advisory_gate_does_not_block(self, base_item):
        """Advisory gate fails but is not in blocking failures."""
        base_item.description = None
        evaluator = GateEvaluator(
            {
                "in_progress -> review": [
                    {
                        "id": "nice_to_have_desc",
                        "check": "item.description",
                        "message": "Description recommended",
                        "severity": "advisory",
                    }
                ]
            }
        )
        results = evaluator.evaluate(base_item, "in_progress", "review")
        assert results[0].passed is False
        assert results[0].severity == "advisory"
        blocking = evaluator.get_blocking_failures(results)
        assert len(blocking) == 0

    def test_resolution_gate_passes(self, base_item):
        """item.resolution check passes when resolution is set."""
        base_item.resolution = "completed"
        evaluator = GateEvaluator(
            {
                "review -> done": [
                    {"id": "require_resolution", "check": "item.resolution", "message": "Need resolution"}
                ]
            }
        )
        results = evaluator.evaluate(base_item, "review", "done")
        assert results[0].passed is True

    def test_item_metadata_nested_access(self, base_item):
        """item.metadata.key check supports nested dict access."""
        base_item.metadata = {"custom_flag": True}
        evaluator = GateEvaluator(
            {
                "* -> review": [
                    {"id": "custom", "check": "item.metadata.custom_flag", "message": "Need flag"}
                ]
            }
        )
        results = evaluator.evaluate(base_item, "in_progress", "review")
        assert results[0].passed is True


# ── TestGateMatching (unit) ────────────────────────────────────────────


class TestGateMatching:
    """Unit tests for gate transition key matching."""

    def test_exact_transition_match(self, base_item):
        """Exact transition key matches only that transition."""
        evaluator = GateEvaluator(
            {
                "in_progress -> review": [
                    {"id": "g1", "check": "item.assignee", "message": "test"}
                ]
            }
        )
        base_item.assignee = "Mini"
        # Match
        results = evaluator.evaluate(base_item, "in_progress", "review")
        assert len(results) == 1
        # No match — different transition
        results = evaluator.evaluate(base_item, "backlog", "in_progress")
        assert len(results) == 0

    def test_wildcard_source_match(self, base_item):
        """Wildcard source matches any source status."""
        evaluator = GateEvaluator(
            {
                "* -> in_progress": [
                    {"id": "g1", "check": "item.assignee", "message": "test"}
                ]
            }
        )
        base_item.assignee = "Mini"
        # Matches from backlog
        results = evaluator.evaluate(base_item, "backlog", "in_progress")
        assert len(results) == 1
        # Matches from ready
        results = evaluator.evaluate(base_item, "ready", "in_progress")
        assert len(results) == 1
        # Does NOT match different target
        results = evaluator.evaluate(base_item, "backlog", "review")
        assert len(results) == 0

    def test_wildcard_target_match(self, base_item):
        """Wildcard target matches any target status."""
        evaluator = GateEvaluator(
            {
                "review -> *": [
                    {"id": "g1", "check": "item.assignee", "message": "test"}
                ]
            }
        )
        base_item.assignee = "Mini"
        # Matches review → done
        results = evaluator.evaluate(base_item, "review", "done")
        assert len(results) == 1
        # Matches review → in_progress
        results = evaluator.evaluate(base_item, "review", "in_progress")
        assert len(results) == 1
        # Does NOT match different source
        results = evaluator.evaluate(base_item, "in_progress", "done")
        assert len(results) == 0

    def test_no_match_returns_empty(self, base_item):
        """Unmatched transition returns no gates."""
        evaluator = GateEvaluator(
            {
                "in_progress -> review": [
                    {"id": "g1", "check": "item.assignee", "message": "test"}
                ]
            }
        )
        results = evaluator.evaluate(base_item, "backlog", "ready")
        assert len(results) == 0

    def test_multiple_gates_same_transition(self, base_item):
        """Multiple gates on the same transition all run."""
        base_item.assignee = "Mini"
        base_item.description = None
        evaluator = GateEvaluator(
            {
                "in_progress -> review": [
                    {"id": "g1", "check": "item.assignee", "message": "need assignee"},
                    {"id": "g2", "check": "item.description", "message": "need desc"},
                ]
            }
        )
        results = evaluator.evaluate(base_item, "in_progress", "review")
        assert len(results) == 2
        assert results[0].passed is True  # assignee passes
        assert results[1].passed is False  # description fails


# ── TestGateConfigParsing (unit) ───────────────────────────────────────


class TestGateConfigParsing:
    """Unit tests for gate config parsing."""

    def test_parse_gates_from_board_config(self):
        """BoardConfig.from_dict() parses gates key."""
        data = {
            "name": "dev",
            "preset": "nautical",
            "path": "work/",
            "gates": {
                "* -> in_progress": [
                    {"id": "require_assignee", "check": "item.assignee", "message": "Need assignee"}
                ]
            },
        }
        board = BoardConfig.from_dict(data)
        assert "* -> in_progress" in board.gates
        assert len(board.gates["* -> in_progress"]) == 1
        assert board.gates["* -> in_progress"][0]["id"] == "require_assignee"

    def test_empty_gates_default(self):
        """No gates key in config defaults to empty dict."""
        data = {"name": "dev", "preset": "nautical", "path": "work/"}
        board = BoardConfig.from_dict(data)
        assert board.gates == {}

    def test_gates_serialized_in_to_dict(self):
        """BoardConfig.to_dict() includes gates when present."""
        board = BoardConfig(
            name="dev",
            preset="nautical",
            path="work/",
            gates={
                "* -> in_progress": [
                    {"id": "g1", "check": "item.assignee", "message": "test"}
                ]
            },
        )
        d = board.to_dict()
        assert "gates" in d

    def test_gates_omitted_when_empty(self):
        """BoardConfig.to_dict() omits gates when empty."""
        board = BoardConfig(name="dev", preset="nautical", path="work/")
        d = board.to_dict()
        assert "gates" not in d

    def test_invalid_transition_key_warns(self):
        """Invalid transition key format is silently skipped."""
        evaluator = GateEvaluator(
            {"not_valid_format": [{"id": "g1", "check": "item.assignee", "message": "test"}]}
        )
        # Gate should not be registered
        results = evaluator.evaluate(
            WorkItem(
                id="T-1",
                title="T",
                item_type=WorkItemType.EXPEDITION,
                status=WorkItemStatus.BACKLOG,
                file_path=Path("t.md"),
                assignee="Mini",
            ),
            "backlog",
            "in_progress",
        )
        assert len(results) == 0

    def test_v1_config_parses_gates(self, kanban_v1_with_gates):
        """v1 single-board config loads gates into KanbanConfig.gates."""
        config = kanban_v1_with_gates["config"]
        assert "* -> in_progress" in config.gates
        assert config.gates["* -> in_progress"][0]["id"] == "require_assignee"

    def test_v1_config_serializes_gates(self):
        """v1 config round-trips gates through save/load."""
        from yurtle_kanban.config import KanbanConfig, PathConfig

        config = KanbanConfig(
            theme="software",
            paths=PathConfig(root="work/"),
            gates={
                "* -> in_progress": [
                    {"id": "g1", "check": "item.assignee", "message": "test"}
                ]
            },
        )
        d = config._to_dict_v1()
        assert "gates" in d["kanban"]
        assert "* -> in_progress" in d["kanban"]["gates"]

    def test_v1_config_omits_gates_when_empty(self):
        """v1 config omits gates key when empty."""
        from yurtle_kanban.config import KanbanConfig, PathConfig

        config = KanbanConfig(theme="software", paths=PathConfig(root="work/"))
        d = config._to_dict_v1()
        assert "gates" not in d["kanban"]

    def test_arrow_without_spaces_normalized(self):
        """Transition key 'a->b' is normalized to 'a -> b'."""
        evaluator = GateEvaluator(
            {"in_progress->review": [{"id": "g1", "check": "item.assignee", "message": "test"}]}
        )
        item = WorkItem(
            id="T-1",
            title="T",
            item_type=WorkItemType.EXPEDITION,
            status=WorkItemStatus.IN_PROGRESS,
            file_path=Path("t.md"),
            assignee="Mini",
        )
        results = evaluator.evaluate(item, "in_progress", "review")
        assert len(results) == 1


# ── TestGateIntegration (service-level) ────────────────────────────────


class TestGateIntegration:
    """Integration tests: gates evaluated during move_item()."""

    def test_move_blocked_by_gate(self, kanban_with_gates):
        """Move raises ValueError when a blocking gate fails."""
        service = kanban_with_gates["service"]

        # EXP-100 is in backlog with assignee=Mini, move to in_progress should pass
        # First, move to in_progress (has assignee, passes gate)
        service.move_item(
            "EXP-100",
            WorkItemStatus.IN_PROGRESS,
            commit=False,
            validate_workflow=False,
        )

        # Now try to move to review without self_reviewed context — should fail
        with pytest.raises(ValueError, match="Gate check failed"):
            service.move_item(
                "EXP-100",
                WorkItemStatus.REVIEW,
                commit=False,
                validate_workflow=False,
            )

    def test_move_succeeds_when_gates_pass(self, kanban_with_gates):
        """Move works when all gates pass."""
        service = kanban_with_gates["service"]

        # Move to in_progress (assignee is set, gate passes)
        item = service.move_item(
            "EXP-100",
            WorkItemStatus.IN_PROGRESS,
            commit=False,
            validate_workflow=False,
        )
        assert item.status == WorkItemStatus.IN_PROGRESS

        # Move to review with self_reviewed context
        item = service.move_item(
            "EXP-100",
            WorkItemStatus.REVIEW,
            commit=False,
            validate_workflow=False,
            gate_context={"self_reviewed": True},
        )
        assert item.status == WorkItemStatus.REVIEW

    def test_skip_gates_bypasses_check(self, kanban_with_gates):
        """skip_gates=True allows move despite failing gate."""
        service = kanban_with_gates["service"]

        # Move to in_progress first
        service.move_item(
            "EXP-100",
            WorkItemStatus.IN_PROGRESS,
            commit=False,
            validate_workflow=False,
        )

        # Move to review without self_reviewed but with skip_gates
        item = service.move_item(
            "EXP-100",
            WorkItemStatus.REVIEW,
            commit=False,
            validate_workflow=False,
            skip_gates=True,
        )
        assert item.status == WorkItemStatus.REVIEW

    def test_skip_gates_audit_trail(self, kanban_with_gates):
        """kb:gatesSkipped appears in TTL when gates are skipped."""
        service = kanban_with_gates["service"]
        item_file = kanban_with_gates["item_file"]

        # Move with skip_gates
        service.move_item(
            "EXP-100",
            WorkItemStatus.IN_PROGRESS,
            commit=False,
            validate_workflow=False,
            skip_gates=True,
        )

        content = item_file.read_text()
        assert "kb:gatesSkipped" in content

    def test_force_also_skips_gates(self, kanban_with_gates):
        """validate_workflow=False (force) implies gates skipped."""
        service = kanban_with_gates["service"]

        # Move to in_progress first
        service.move_item(
            "EXP-100",
            WorkItemStatus.IN_PROGRESS,
            commit=False,
            validate_workflow=False,
        )

        # Force move to review (validate_workflow=False, skip_wip=True)
        # The CLI passes skip_gates=True when force=True
        item = service.move_item(
            "EXP-100",
            WorkItemStatus.REVIEW,
            commit=False,
            validate_workflow=False,
            skip_wip_check=True,
            skip_gates=True,
        )
        assert item.status == WorkItemStatus.REVIEW

    def test_no_gates_config_allows_all(self, kanban_no_gates):
        """Board without gates config does not block moves."""
        service = kanban_no_gates["service"]

        # Move without assignee — no gate configured, should work
        item = service.move_item(
            "EXP-200",
            WorkItemStatus.IN_PROGRESS,
            commit=False,
            validate_workflow=False,
        )
        assert item.status == WorkItemStatus.IN_PROGRESS

    def test_v1_gates_block_move(self, kanban_v1_with_gates):
        """v1 single-board config gates block transitions."""
        service = kanban_v1_with_gates["service"]
        item_file = kanban_v1_with_gates["item_file"]

        # Rewrite item without assignee
        item_file.write_text(
            """---
id: EXP-300
title: "V1 Gate Test"
type: expedition
status: backlog
---
# V1 Gate Test
"""
        )
        service._items.clear()

        with pytest.raises(ValueError, match="Assignee required"):
            service.move_item(
                "EXP-300",
                WorkItemStatus.IN_PROGRESS,
                commit=False,
                validate_workflow=False,
            )

    def test_v1_gates_pass_when_satisfied(self, kanban_v1_with_gates):
        """v1 single-board config gates pass when conditions met."""
        service = kanban_v1_with_gates["service"]

        # EXP-300 has assignee=Mini, should pass require_assignee gate
        item = service.move_item(
            "EXP-300",
            WorkItemStatus.IN_PROGRESS,
            commit=False,
            validate_workflow=False,
        )
        assert item.status == WorkItemStatus.IN_PROGRESS

    def test_no_gates_skip_gates_no_audit_trail(self, kanban_no_gates):
        """skip_gates=True with no gates configured does NOT write kb:gatesSkipped."""
        service = kanban_no_gates["service"]
        item_file = kanban_no_gates["item_file"]

        service.move_item(
            "EXP-200",
            WorkItemStatus.IN_PROGRESS,
            commit=False,
            validate_workflow=False,
            skip_gates=True,
        )

        content = item_file.read_text()
        assert "kb:gatesSkipped" not in content

    def test_gate_blocked_without_assignee(self, kanban_with_gates):
        """Require_assignee gate blocks when no assignee."""
        service = kanban_with_gates["service"]
        item_file = kanban_with_gates["item_file"]

        # Rewrite item without assignee
        item_file.write_text(
            """---
id: EXP-100
title: "Test Expedition"
type: expedition
status: backlog
---
# Test Expedition
"""
        )
        # Re-scan to pick up change
        service._items.clear()

        with pytest.raises(ValueError, match="Assignee required"):
            service.move_item(
                "EXP-100",
                WorkItemStatus.IN_PROGRESS,
                commit=False,
                validate_workflow=False,
            )


# ── TestGateCli (CLI-level) ────────────────────────────────────────────


class TestGateCli:
    """CLI tests for gate-related flags.

    Note: The nautical theme enforces workflow transitions (backlog→ready→in_progress).
    CLI tests use --force where needed to bypass workflow validation and isolate
    gate behavior, or start items at the correct status.
    """

    def test_skip_gates_flag(self, kanban_with_gates, monkeypatch):
        """--skip-gates flag passes through to service."""
        monkeypatch.chdir(kanban_with_gates["tmp_path"])
        runner = CliRunner()

        # Use --force to bypass workflow (backlog→in_progress not directly allowed)
        # but --skip-gates is what we're testing — force also sets skip_gates
        # So test with an item already at ready status
        kanban_with_gates["item_file"].write_text(
            """---
id: EXP-100
title: "Test Expedition"
type: expedition
status: ready
assignee: Mini
---
# Test Expedition
"""
        )

        result = runner.invoke(
            main,
            ["move", "EXP-100", "in_progress", "--no-commit", "--skip-gates"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output

        content = kanban_with_gates["item_file"].read_text()
        assert "kb:gatesSkipped" in content

    def test_self_reviewed_flag_passes_gate(self, kanban_with_gates, monkeypatch):
        """--self-reviewed flag satisfies self_review gate."""
        monkeypatch.chdir(kanban_with_gates["tmp_path"])
        runner = CliRunner()

        # Start item at in_progress so review is a valid transition
        kanban_with_gates["item_file"].write_text(
            """---
id: EXP-100
title: "Test Expedition"
type: expedition
status: in_progress
assignee: Mini
---
# Test Expedition
"""
        )

        # Move to review with --self-reviewed
        result = runner.invoke(
            main,
            ["move", "EXP-100", "review", "--no-commit", "--self-reviewed"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output

    def test_gate_failure_shows_message(self, kanban_with_gates, monkeypatch):
        """Gate failure error message is shown to user."""
        monkeypatch.chdir(kanban_with_gates["tmp_path"])
        runner = CliRunner()

        # Start item at in_progress so review is a valid transition
        kanban_with_gates["item_file"].write_text(
            """---
id: EXP-100
title: "Test Expedition"
type: expedition
status: in_progress
assignee: Mini
---
# Test Expedition
"""
        )

        # Try review without --self-reviewed — should show gate message
        result = runner.invoke(
            main,
            ["move", "EXP-100", "review", "--no-commit"],
        )
        assert result.exit_code != 0
        assert "Self-review required" in result.output

    def test_force_flag_skips_gates_too(self, kanban_with_gates, monkeypatch):
        """--force flag skips gates in addition to WIP and workflow."""
        monkeypatch.chdir(kanban_with_gates["tmp_path"])
        runner = CliRunner()

        # Force move to in_progress (skips all checks including workflow)
        result = runner.invoke(
            main,
            ["move", "EXP-100", "in_progress", "--no-commit", "--force"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output

        content = kanban_with_gates["item_file"].read_text()
        assert "kb:gatesSkipped" in content
        assert "kb:forcedMove" in content
