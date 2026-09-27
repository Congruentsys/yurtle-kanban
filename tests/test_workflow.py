"""Tests for workflow configuration parsing and validation."""

from pathlib import Path

import pytest

from yurtle_kanban.models import WorkItem, WorkItemStatus, WorkItemType
from yurtle_kanban.workflow import (
    StateConfig,
    TransitionRule,
    WorkflowConfig,
    WorkflowParser,
    get_default_workflow,
)


class TestStateConfig:
    """Tests for StateConfig."""

    def test_can_transition_to(self):
        """Test transition checking."""
        state = StateConfig(
            id="ready",
            name="Ready",
            allowed_transitions=["in_progress", "blocked"]
        )

        assert state.can_transition_to("in_progress") is True
        assert state.can_transition_to("blocked") is True
        assert state.can_transition_to("done") is False

    def test_initial_and_terminal(self):
        """Test initial and terminal flags."""
        initial = StateConfig(id="backlog", name="Backlog", is_initial=True)
        terminal = StateConfig(id="done", name="Done", is_terminal=True)

        assert initial.is_initial is True
        assert initial.is_terminal is False
        assert terminal.is_initial is False
        assert terminal.is_terminal is True


class TestWorkflowConfig:
    """Tests for WorkflowConfig."""

    def test_get_state(self):
        """Test getting state by ID."""
        workflow = WorkflowConfig(
            id="test",
            states=[
                StateConfig(id="ready", name="Ready"),
                StateConfig(id="in_progress", name="In Progress"),
            ]
        )

        assert workflow.get_state("ready") is not None
        assert workflow.get_state("ready").name == "Ready"
        assert workflow.get_state("in_progress") is not None
        assert workflow.get_state("unknown") is None

    def test_get_state_normalized(self):
        """Test state lookup with different formats."""
        workflow = WorkflowConfig(
            id="test",
            states=[StateConfig(id="in_progress", name="In Progress")]
        )

        assert workflow.get_state("in_progress") is not None
        assert workflow.get_state("in-progress") is not None
        assert workflow.get_state("IN_PROGRESS") is not None

    def test_get_initial_states(self):
        """Test getting initial states."""
        workflow = WorkflowConfig(
            id="test",
            states=[
                StateConfig(id="backlog", name="Backlog", is_initial=True),
                StateConfig(id="ready", name="Ready"),
                StateConfig(id="done", name="Done", is_terminal=True),
            ]
        )

        initial = workflow.get_initial_states()
        assert len(initial) == 1
        assert initial[0].id == "backlog"

    def test_get_terminal_states(self):
        """Test getting terminal states."""
        workflow = WorkflowConfig(
            id="test",
            states=[
                StateConfig(id="backlog", name="Backlog", is_initial=True),
                StateConfig(id="done", name="Done", is_terminal=True),
            ]
        )

        terminal = workflow.get_terminal_states()
        assert len(terminal) == 1
        assert terminal[0].id == "done"

    def test_to_mermaid(self):
        """Test Mermaid diagram generation."""
        workflow = WorkflowConfig(
            id="test",
            name="Test Workflow",
            states=[
                StateConfig(id="ready", name="Ready", is_initial=True,
                           allowed_transitions=["in_progress"]),
                StateConfig(id="in_progress", name="In Progress",
                           allowed_transitions=["done"]),
                StateConfig(id="done", name="Done", is_terminal=True),
            ]
        )

        mermaid = workflow.to_mermaid()
        assert "stateDiagram-v2" in mermaid
        assert "[*] --> ready" in mermaid
        assert "ready --> in_progress" in mermaid
        assert "done --> [*]" in mermaid

    def test_to_ascii(self):
        """Test ASCII diagram generation."""
        workflow = WorkflowConfig(
            id="test",
            name="Test Workflow",
            applies_to="feature",
            states=[
                StateConfig(id="ready", name="Ready",
                           allowed_transitions=["in_progress"]),
            ]
        )

        ascii_diagram = workflow.to_ascii()
        assert "Test Workflow" in ascii_diagram
        assert "feature" in ascii_diagram
        assert "[ready]" in ascii_diagram


class TestWorkflowParser:
    """Tests for WorkflowParser."""

    def test_get_default_workflow(self):
        """Test getting default workflow."""
        workflow = get_default_workflow()

        assert workflow.id == "default"
        assert len(workflow.states) > 0

        # Check we have expected states
        state_ids = [s.id for s in workflow.states]
        assert "backlog" in state_ids
        assert "ready" in state_ids
        assert "in_progress" in state_ids
        assert "done" in state_ids


class TestRuleEvaluation:
    """Tests for _evaluate_rule_condition (fail-closed enforcement)."""

    @pytest.fixture
    def parser(self):
        return WorkflowParser()

    @pytest.fixture
    def base_item(self):
        return WorkItem(
            id="EXP-001",
            title="Test Expedition",
            item_type=WorkItemType.FEATURE,
            status=WorkItemStatus.READY,
            file_path=Path("test.md"),
        )

    def test_assignee_check_passes(self, parser, base_item):
        base_item.assignee = "Claude-M5"
        result = parser._evaluate_rule_condition("item.assignee is not None", base_item)
        assert result is True

    def test_assignee_check_fails_when_none(self, parser, base_item):
        base_item.assignee = None
        result = parser._evaluate_rule_condition("item.assignee is not None", base_item)
        assert result is False

    def test_assignee_check_fails_when_empty(self, parser, base_item):
        base_item.assignee = ""
        result = parser._evaluate_rule_condition("item.assignee is not None", base_item)
        assert result is False

    def test_description_length_passes(self, parser, base_item):
        base_item.description = "A" * 51
        result = parser._evaluate_rule_condition("len(item.description or '') > 50", base_item)
        assert result is True

    def test_description_length_fails(self, parser, base_item):
        base_item.description = "Short"
        result = parser._evaluate_rule_condition("len(item.description or '') > 50", base_item)
        assert result is False

    def test_resolution_check_passes(self, parser, base_item):
        base_item.resolution = "completed"
        result = parser._evaluate_rule_condition("item.resolution is not None", base_item)
        assert result is True

    def test_resolution_check_fails_when_none(self, parser, base_item):
        base_item.resolution = None
        result = parser._evaluate_rule_condition("item.resolution is not None", base_item)
        assert result is False

    def test_resolution_check_fails_when_empty(self, parser, base_item):
        base_item.resolution = ""
        result = parser._evaluate_rule_condition("item.resolution is not None", base_item)
        assert result is False

    def test_superseded_by_passes_when_not_superseded(self, parser, base_item):
        """Non-superseded resolutions don't need superseded_by."""
        base_item.resolution = "wont_do"
        result = parser._evaluate_rule_condition(
            "item.resolution != 'superseded' or len(item.superseded_by) > 0", base_item
        )
        assert result is True

    def test_superseded_by_passes_when_superseded_with_refs(self, parser, base_item):
        """Superseded items with references pass."""
        base_item.resolution = "superseded"
        base_item.superseded_by = ["EXP-002"]
        result = parser._evaluate_rule_condition(
            "item.resolution != 'superseded' or len(item.superseded_by) > 0", base_item
        )
        assert result is True

    def test_superseded_by_fails_when_superseded_without_refs(self, parser, base_item):
        """Superseded items without references fail."""
        base_item.resolution = "superseded"
        base_item.superseded_by = []
        result = parser._evaluate_rule_condition(
            "item.resolution != 'superseded' or len(item.superseded_by) > 0", base_item
        )
        assert result is False

    def test_objective_check_passes_with_title(self, parser, base_item):
        base_item.title = "Expedition Objective: Deploy"
        base_item.description = None
        result = parser._evaluate_rule_condition(
            "'objective' in item.title.lower() or item.description", base_item
        )
        assert result is True

    def test_objective_check_passes_with_description(self, parser, base_item):
        base_item.title = "No keyword here"
        base_item.description = "Has content"
        result = parser._evaluate_rule_condition(
            "'objective' in item.title.lower() or item.description", base_item
        )
        assert result is True

    def test_objective_check_fails_with_neither(self, parser, base_item):
        base_item.title = "No keyword here"
        base_item.description = None
        result = parser._evaluate_rule_condition(
            "'objective' in item.title.lower() or item.description", base_item
        )
        assert result is False

    def test_unknown_condition_fails_closed(self, parser, base_item):
        """Unknown conditions must return False (fail-closed)."""
        result = parser._evaluate_rule_condition("some_unknown_check(item)", base_item)
        assert result is False


class TestTransitionRule:
    """Tests for transition rules."""

    def test_rule_structure(self):
        """Test rule dataclass structure."""
        rule = TransitionRule(
            id="require_assignee",
            applies_to="in_progress",
            condition="item.assignee is not None",
            message="Must have an assignee"
        )

        assert rule.id == "require_assignee"
        assert rule.applies_to == "in_progress"
        assert "assignee" in rule.condition
