"""
Workflow Configuration Parser - Parse Kanban Workflows from Yurtle Markdown

Parses .yurtle.md files containing workflow definitions:
- YAML frontmatter (document metadata)
- Prose documentation with descriptions
- Yurtle blocks (```yurtle ... ```) with RDF Turtle content

Usage:
    from yurtle_kanban.workflow import WorkflowParser

    parser = WorkflowParser(Path(".kanban"))
    workflow = parser.load_workflow("feature")

    # Whether a move is legal is KanbanService.legal_next's call (#589, #651);
    # the parser supplies the workflow graph and its content rules (check_rules).
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    yaml = None

try:
    from rdflib import RDF, Graph, Literal, Namespace
except ImportError:
    Graph = None
    Namespace = None
    Literal = None
    RDF = None

from ._logging import get_logger
from .models import WorkItem, WorkItemStatus

logger = get_logger("yurtle-kanban.workflow")  # escapes control characters (#215)

# The default lifecycle: the one table `move`, `states` and the default workflow
# read when neither the theme's `transitions` nor a per-type workflow applies (#589)
DEFAULT_TRANSITIONS: dict[WorkItemStatus, list[WorkItemStatus]] = {
    WorkItemStatus.BACKLOG: [WorkItemStatus.READY, WorkItemStatus.BLOCKED],
    WorkItemStatus.READY: [
        WorkItemStatus.IN_PROGRESS,
        WorkItemStatus.BACKLOG,
        WorkItemStatus.BLOCKED,
    ],
    WorkItemStatus.IN_PROGRESS: [
        WorkItemStatus.REVIEW,
        WorkItemStatus.DONE,
        WorkItemStatus.BLOCKED,
        WorkItemStatus.READY,
    ],
    WorkItemStatus.REVIEW: [
        WorkItemStatus.DONE,
        WorkItemStatus.IN_PROGRESS,
        WorkItemStatus.BLOCKED,
    ],
    WorkItemStatus.DONE: [],  # terminal
    WorkItemStatus.BLOCKED: [
        WorkItemStatus.READY,
        WorkItemStatus.IN_PROGRESS,
        WorkItemStatus.BACKLOG,
    ],
}


# Namespaces for workflow configuration
if Namespace:
    WORKFLOW = Namespace("https://yurtle.dev/kanban/workflow/")
    KANBAN = Namespace("https://yurtle.dev/kanban/")
else:
    WORKFLOW = None
    KANBAN = None


@dataclass
class StateConfig:
    """Configuration for a workflow state."""

    id: str
    name: str
    is_initial: bool = False
    is_terminal: bool = False
    allowed_transitions: list[str] = field(default_factory=list)
    description: str = ""

    def can_transition_to(self, target_id: str) -> bool:
        """Check if this state can transition to target."""
        return target_id in self.allowed_transitions


@dataclass
class TransitionRule:
    """Validation rule for state transitions."""

    id: str
    applies_to: str  # State ID this rule applies to
    condition: str  # Python expression to evaluate
    message: str  # Error message if condition fails


@dataclass
class WorkflowConfig:
    """Configuration for a kanban workflow."""

    id: str
    name: str = ""
    applies_to: str = "feature"  # Item type this workflow governs
    states: list[StateConfig] = field(default_factory=list)
    rules: list[TransitionRule] = field(default_factory=list)
    version: int = 1
    source_file: str | None = None

    def get_state(self, state_id: str) -> StateConfig | None:
        """Get state by ID."""
        normalized = state_id.lower().strip().replace(" ", "_").replace("-", "_")
        for state in self.states:
            if state.id.lower() == normalized:
                return state
        return None

    def get_initial_states(self) -> list[StateConfig]:
        """Get states that items can start in."""
        return [s for s in self.states if s.is_initial]

    def get_terminal_states(self) -> list[StateConfig]:
        """Get states where items end."""
        return [s for s in self.states if s.is_terminal]

    def to_mermaid(self) -> str:
        """Generate Mermaid diagram of the workflow."""
        lines = ["stateDiagram-v2"]

        for state in self.states:
            if state.is_initial:
                lines.append(f"    [*] --> {state.id}")
            if state.is_terminal:
                lines.append(f"    {state.id} --> [*]")
            for target in state.allowed_transitions:
                lines.append(f"    {state.id} --> {target}")

        return "\n".join(lines)

    def to_ascii(self) -> str:
        """Generate ASCII diagram of the workflow."""
        # Simple text representation
        lines = [f"Workflow: {self.name} (applies to: {self.applies_to})", ""]

        for state in self.states:
            markers = []
            if state.is_initial:
                markers.append("initial")
            if state.is_terminal:
                markers.append("terminal")
            marker_str = f" ({', '.join(markers)})" if markers else ""

            lines.append(f"  [{state.id}]{marker_str}")
            if state.allowed_transitions:
                targets = ", ".join(state.allowed_transitions)
                lines.append(f"    -> {targets}")

        return "\n".join(lines)


class WorkflowParser:
    """
    Parse Yurtle markdown workflow configuration files.

    Yurtle files have three layers:
    1. YAML frontmatter (metadata)
    2. Prose documentation (human-readable)
    3. Yurtle blocks (```yurtle ... ```) with RDF Turtle

    This parser extracts workflow configurations from all three layers.
    """

    def __init__(self, config_dir: Path = None):
        self.config_dir = Path(config_dir) if config_dir else Path(".kanban")
        self._workflow_cache: dict[str, WorkflowConfig] = {}

    def load_all_workflows(self) -> dict[str, WorkflowConfig]:
        """Load all workflow configurations from workflows/ directory."""
        workflows_dir = self.config_dir / "workflows"
        if not workflows_dir.exists():
            return {}

        for workflow_file in workflows_dir.glob("*.yurtle.md"):
            try:
                config = self.parse_workflow_file(workflow_file)
                if config:
                    self._workflow_cache[config.applies_to] = config
            except Exception as e:
                logger.warning(f"Failed to parse workflow config {workflow_file}: {e}")

        # Also look for .md files (simpler naming)
        for workflow_file in workflows_dir.glob("*.md"):
            if workflow_file.name.endswith(".yurtle.md"):
                continue  # Already processed
            try:
                config = self.parse_workflow_file(workflow_file)
                if config:
                    self._workflow_cache[config.applies_to] = config
            except Exception as e:
                logger.warning(f"Failed to parse workflow config {workflow_file}: {e}")

        return self._workflow_cache

    def load_workflow(self, applies_to: str) -> WorkflowConfig | None:
        """Load workflow configuration for an item type."""
        if applies_to in self._workflow_cache:
            return self._workflow_cache[applies_to]

        # Load all workflows and find matching one
        self.load_all_workflows()
        return self._workflow_cache.get(applies_to)

    def parse_workflow_file(self, file_path: Path) -> WorkflowConfig | None:
        """Parse a workflow configuration file."""
        content = file_path.read_text(encoding="utf-8")
        frontmatter = self._extract_frontmatter(content)

        if frontmatter.get("type") != "kanban-workflow":
            return None

        yurtle_blocks = self._extract_yurtle_blocks(content)
        states, rules = self._parse_workflow_from_yurtle(yurtle_blocks)

        return WorkflowConfig(
            id=frontmatter.get("id", file_path.stem.replace(".yurtle", "")),
            name=self._extract_title(content) or frontmatter.get("id", ""),
            applies_to=frontmatter.get("applies_to", "feature"),
            states=states,
            rules=rules,
            version=frontmatter.get("version", 1),
            source_file=str(file_path),
        )

    def _extract_frontmatter(self, content: str) -> dict[str, Any]:
        """Extract YAML frontmatter from markdown."""
        if not content.startswith("---"):
            return {}

        try:
            end = content.find("---", 3)
            if end == -1:
                return {}

            if yaml:
                data = yaml.safe_load(content[3:end])
                # a YAML list or scalar is no workflow config, like broken YAML (#321, #330)
                return data if isinstance(data, dict) else {}
            else:
                # Simple fallback
                result = {}
                for line in content[3:end].strip().split("\n"):
                    if ":" in line:
                        key, value = line.split(":", 1)
                        result[key.strip()] = value.strip()
                return result
        except Exception as e:
            logger.warning(f"Failed to parse frontmatter: {e}")
            return {}

    def _extract_yurtle_blocks(self, content: str) -> list[str]:
        """Extract ```yurtle ... ``` blocks from markdown."""
        pattern = r"```yurtle\n(.*?)```"
        matches = re.findall(pattern, content, re.DOTALL)
        return matches

    def _extract_title(self, content: str) -> str | None:
        """Extract first heading as title."""
        for line in content.split("\n"):
            if line.startswith("# "):
                return line[2:].strip()
        return None

    def _parse_workflow_from_yurtle(
        self, yurtle_blocks: list[str]
    ) -> tuple[list[StateConfig], list[TransitionRule]]:
        """Parse workflow states and rules from yurtle blocks."""
        states = []
        rules = []

        if not Graph or not WORKFLOW:
            # RDFlib not available, return defaults
            logger.warning("RDFlib not installed, using default workflow")
            return self._get_default_states(), []

        for block in yurtle_blocks:
            try:
                g = Graph()
                g.parse(data=block, format="turtle")

                # Find all State nodes
                for subj in g.subjects(RDF.type, WORKFLOW.State):
                    state_id = self._extract_local_id(str(subj))

                    name = str(g.value(subj, WORKFLOW.name, default=state_id))

                    is_initial_lit = g.value(subj, WORKFLOW.isInitial, default=Literal("false"))
                    is_initial = str(is_initial_lit).lower() == "true"

                    is_terminal_lit = g.value(subj, WORKFLOW.isTerminal, default=Literal("false"))
                    is_terminal = str(is_terminal_lit).lower() == "true"

                    transitions_lit = g.value(subj, WORKFLOW.transitions, default="")
                    transitions_str = str(transitions_lit)
                    # Parse transitions (comma-separated state references)
                    transitions = []
                    for t in transitions_str.split(","):
                        t = t.strip().strip("<>")
                        if t:
                            transitions.append(self._extract_local_id(t))

                    description_lit = g.value(subj, WORKFLOW.description, default="")
                    description = str(description_lit)

                    states.append(
                        StateConfig(
                            id=state_id,
                            name=name,
                            is_initial=is_initial,
                            is_terminal=is_terminal,
                            allowed_transitions=transitions,
                            description=description,
                        )
                    )

                # Find all Rule nodes
                for subj in g.subjects(RDF.type, WORKFLOW.Rule):
                    rule_id = self._extract_local_id(str(subj))

                    applies_to_uri = g.value(subj, WORKFLOW.appliesTo)
                    applies_to = (
                        self._extract_local_id(str(applies_to_uri)) if applies_to_uri else ""
                    )

                    condition = str(g.value(subj, WORKFLOW.condition, default=""))
                    message = str(g.value(subj, WORKFLOW.message, default=""))

                    rules.append(
                        TransitionRule(
                            id=rule_id, applies_to=applies_to, condition=condition, message=message
                        )
                    )

            except Exception as e:
                logger.warning(f"Failed to parse yurtle block for workflow: {e}")

        return states, rules

    def _extract_local_id(self, uri: str) -> str:
        """Extract local ID from URI (last path segment)."""
        # Handle URIs like <state/draft> or https://.../#draft
        if "/" in uri:
            return uri.rsplit("/", 1)[-1].strip("<>")
        elif "#" in uri:
            return uri.rsplit("#", 1)[-1].strip("<>")
        return uri.strip("<>")

    def _get_default_states(self) -> list[StateConfig]:
        """The default workflow's states: the one default table, as states (#589)."""
        return [
            StateConfig(
                id=status.value,
                name=status.value.replace("_", " ").title(),
                is_initial=status == WorkItemStatus.BACKLOG,
                is_terminal=not targets,
                allowed_transitions=[t.value for t in targets],
            )
            for status, targets in DEFAULT_TRANSITIONS.items()
        ]

    def check_rules(
        self, item: WorkItem, new_status: WorkItemStatus, workflow: WorkflowConfig
    ) -> tuple[bool, str]:
        """The workflow's rules for entering `new_status` (content checks such as
        "has an assignee"), apart from whether the move is legal at all (#589)."""
        target_state = workflow.get_state(new_status.value)
        if target_state is None:
            return True, ""
        for rule in workflow.rules:
            if rule.applies_to == target_state.id:
                try:
                    # Evaluate condition (safely)
                    if not self._evaluate_rule_condition(rule.condition, item):
                        return False, rule.message
                except Exception as e:
                    logger.warning(f"Failed to evaluate rule {rule.id}: {e}")

        return True, ""

    def _evaluate_rule_condition(self, condition: str, item: WorkItem) -> bool:
        """Safely evaluate a rule condition.

        Uses pattern matching against known condition strings from workflow files.
        Unknown conditions fail closed (return False) to prevent silently passing
        rules that the engine doesn't understand.
        """
        # Assignee check
        if "item.assignee is not None" in condition:
            return item.assignee is not None and item.assignee != ""

        # Description length check
        # the body only: comments are their own field since #605 (#635)
        if "len(item.description" in condition:
            desc = item.description or ""
            match = re.search(r">\s*(\d+)", condition)
            if match:
                min_len = int(match.group(1))
                return len(desc) > min_len
            return len(desc) > 0

        # Resolution check (e.g., "item.resolution is not None")
        if "item.resolution is not None" in condition:
            return item.resolution is not None and item.resolution != ""

        # Compound superseded_by check
        # e.g., "item.resolution != 'superseded' or len(item.superseded_by) > 0"
        if "item.resolution" in condition and "superseded_by" in condition:
            if item.resolution != "superseded":
                return True
            return len(item.superseded_by) > 0

        # Objective check (e.g., "'objective' in item.title.lower() or item.description")
        if "'objective'" in condition and "item.title" in condition:
            title_has = "objective" in (item.title or "").lower()
            desc_has = bool(item.description)
            return title_has or desc_has

        # Fail closed: unknown conditions block the transition
        logger.warning(
            f"Unknown rule condition (fail-closed): {condition!r}. "
            f"Add a handler in _evaluate_rule_condition() to support it."
        )
        return False

    def get_default_workflow(self) -> WorkflowConfig:
        """Get default workflow configuration."""
        return WorkflowConfig(
            id="default",
            name="Default Workflow",
            applies_to="feature",
            states=self._get_default_states(),
        )


def get_default_workflow() -> WorkflowConfig:
    """Get the default workflow configuration."""
    parser = WorkflowParser()
    return parser.get_default_workflow()
