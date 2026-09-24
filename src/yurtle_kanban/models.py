"""
Work item models for yurtle-kanban.

These models represent the core data structures for file-based kanban.
Each WorkItem corresponds to a Yurtle markdown file.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from rdflib import Graph, URIRef

# Priority values a create command may write (they also become kb:priority
# terms in Turtle blocks, so free text is not allowed there)
PRIORITIES = ("critical", "high", "medium", "low")


def unknown_priority_message(shown: str) -> str:
    """The one wording for a refused priority, everywhere (CLI, MCP, service; #171)."""
    return f"Unknown priority: {shown}; valid: {', '.join(PRIORITIES)}"


def check_encodable(field: str, value: str | list[str] | None) -> None:
    """Refuse text that can't be written as UTF-8: a lone surrogate, which is what
    Python's surrogateescape makes of undecodable argv bytes (#172). Checked before
    anything is written, so a bad value never leaves a 0-byte item file behind."""
    for text in value if isinstance(value, list) else [value]:
        if isinstance(text, str):
            try:
                text.encode("utf-8")
            except UnicodeEncodeError:
                raise ValueError(
                    f"{field} contains invalid UTF-8 (undecodable bytes): {text!r}"
                ) from None


# Turtle short-string escaping (ECHAR): the one escaper for every Turtle literal
# built from user input — titles, targets, units, ids, agents (#120, #141). A
# value with `"`, `\\`, a newline or a CR must stay one literal and never break
# the block or inject triples.
_TURTLE_ESCAPES = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t"}
_TURTLE_UNESCAPES = {"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f"}


def turtle_string(value: str) -> str:
    """Escape a value for use inside a Turtle "..." literal."""
    return "".join(_TURTLE_ESCAPES.get(ch, ch) for ch in value)


def turtle_unescape(value: str) -> str:
    """Invert turtle_string (and Turtle's other single-character escapes)."""
    return re.sub(r"\\(.)", lambda m: _TURTLE_UNESCAPES.get(m.group(1), m.group(1)), value)

# Characters YAML won't take literally inside a double-quoted scalar: outside
# its printable set (C1 controls, DEL, U+FFFE/FFFF, lone surrogates), plus NEL
# (U+0085) and LINE/PARAGRAPH SEPARATOR (U+2028/2029), which YAML treats as
# line breaks and folds with adjacent spaces. json.dumps leaves them raw (#148).
_YAML_UNSAFE = re.compile("[\x7f-\x9f\u2028\u2029\ufffe\uffff\ud800-\udfff]")


def yaml_quote(value: str) -> str:
    """A YAML double-quoted scalar that reads back as exactly `value` (#121, #148)."""
    quoted = json.dumps(value, ensure_ascii=False)  # a JSON string is a YAML scalar
    return _YAML_UNSAFE.sub(lambda m: f"\\u{ord(m.group()):04x}", quoted)

def yaml_scalar(value: str) -> str:
    """Render a string as a frontmatter value that YAML reads back unchanged (#104).

    Plain when YAML already reads it as the same string (`agent-x`); otherwise
    double-quoted, e.g. `team: core`, `yes`, `null`, `#core` or `[core]`, which
    YAML would otherwise read as an error, a bool, None, a comment or a list.
    """
    import yaml

    try:
        if yaml.safe_load(f"k: {value}") == {"k": value}:
            return value
    except yaml.YAMLError:
        pass
    return yaml_quote(value)


def yaml_flow_item(value: str) -> str:
    """Render a string as one element of a YAML flow list (`[a, b]`) (#121).

    Inside a flow list a comma or bracket also ends the element, so `a, b`
    (plain as a scalar) must be quoted here to stay ONE element.
    """
    import yaml

    try:
        if yaml.safe_load(f"k: [{value}]") == {"k": [value]}:
            return value
    except yaml.YAMLError:
        pass
    return yaml_quote(value)


def yaml_flow_list(values: list[str]) -> str:
    """Render strings as a YAML flow list whose elements read back unchanged."""
    return "[" + ", ".join(yaml_flow_item(v) for v in values) + "]"


class WorkItemStatus(Enum):
    """Standard work item statuses."""

    BACKLOG = "backlog"
    READY = "ready"
    IN_PROGRESS = "in_progress"
    REVIEW = "review"
    DONE = "done"
    BLOCKED = "blocked"

    @classmethod
    def from_string(cls, value: str) -> WorkItemStatus:
        """Parse status from string, handling various formats."""
        normalized = value.lower().replace("-", "_").replace(" ", "_")
        for status in cls:
            if status.value == normalized:
                return status
        raise ValueError(f"Unknown status: {value}")


class WorkItemType(Enum):
    """Standard work item types (supports both software and nautical themes)."""

    # Software theme
    FEATURE = "feature"
    BUG = "bug"
    EPIC = "epic"
    ISSUE = "issue"
    TASK = "task"
    IDEA = "idea"
    # Nautical theme
    EXPEDITION = "expedition"
    VOYAGE = "voyage"
    DIRECTIVE = "directive"
    HAZARD = "hazard"
    SIGNAL = "signal"
    CHORE = "chore"
    # HDD (Hypothesis-Driven Development) theme
    LITERATURE = "literature"
    PAPER = "paper"
    HYPOTHESIS = "hypothesis"
    EXPERIMENT = "experiment"
    MEASURE = "measure"

    @classmethod
    def from_string(cls, value: str) -> WorkItemType:
        """Parse type from string."""
        normalized = value.lower()
        for item_type in cls:
            if item_type.value == normalized:
                return item_type
        raise ValueError(f"Unknown item type: {value}")


@dataclass
class Comment:
    """A comment on a work item."""

    content: str
    author: str
    created_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "author": self.author,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class Column:
    """A kanban column definition."""

    id: str
    name: str
    order: int
    wip_limit: int | None = None
    description: str | None = None
    type_wip_limits: dict[str, int | None] | None = None

    def get_wip_limit(self, item_type: str | None = None) -> int | None:
        """Get the effective WIP limit, optionally for a specific item type.

        Resolution order:
        1. Per-type limit if item_type given and type_wip_limits configured
        2. _default in type_wip_limits if item_type not listed
        3. Legacy wip_limit (applies to all types)

        Returns None for unlimited.
        """
        if item_type and self.type_wip_limits is not None:
            if item_type in self.type_wip_limits:
                return self.type_wip_limits[item_type]
            if "_default" in self.type_wip_limits:
                return self.type_wip_limits["_default"]
            # type_wip_limits configured but no match and no _default:
            # fall through to legacy wip_limit
        return self.wip_limit

    def is_over_wip(self, count: int, item_type: str | None = None) -> bool:
        """Check if column is over WIP limit, optionally for a specific type."""
        limit = self.get_wip_limit(item_type)
        if limit is None:
            return False
        return count > limit


@dataclass
class WorkItem:
    """A work item represented by a Yurtle markdown file."""

    id: str
    title: str
    item_type: WorkItemType
    status: WorkItemStatus
    file_path: Path

    # Optional fields
    priority: str | None = None
    assignee: str | None = None
    created: date | None = None
    updated: datetime | None = None
    tags: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    related: list[str] = field(default_factory=list)
    blocks: list[str] = field(default_factory=list)
    description: str | None = None
    comments: list[Comment] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    resolution: str | None = None  # completed, superseded, wont_do, duplicate, obsolete, merged
    superseded_by: list[str] = field(default_factory=list)  # list of item IDs
    graph: Graph | None = None  # RDF graph from frontmatter + fenced blocks
    priority_rank: int | None = None  # Explicit priority rank (lower = higher priority)
    value_summary: str | None = None  # Brief value statement for prioritization
    compute_requirement: str | None = None  # e.g. dgx-training, gpu, cpu-safe

    def __post_init__(self):
        if self.updated is None:
            self.updated = datetime.now()

    @property
    def uri(self) -> str:
        """Return the file URI for this work item."""
        return f"file://{self.file_path.absolute()}"

    @property
    def is_blocked(self) -> bool:
        """Check if item is blocked."""
        return self.status == WorkItemStatus.BLOCKED

    @property
    def priority_score(self) -> int:
        """Get numeric priority score for sorting."""
        priority_map = {
            "critical": 100,
            "high": 75,
            "medium": 50,
            "low": 25,
            # not writable since #106/#125, kept so legacy items still rank (#125)
            "backlog": 10,
        }
        return priority_map.get(self.priority or "medium", 50)

    @property
    def numeric_id(self) -> int:
        """Extract trailing number from ID for numeric sorting.

        EXP-1016 → 1016, H130.1 → 130, M-007 → 7, CHORE-080 → 80.
        Falls back to 0 if no number found.
        """
        import re
        match = re.search(r"(\d+)", self.id or "")
        return int(match.group(1)) if match else 0

    def get_knowledge_triples(self, predicate: URIRef) -> list[str]:
        """Get all object values for a predicate from the knowledge graph.

        Queries the RDF graph (frontmatter + fenced blocks) for all triples
        matching (any_subject, predicate, ?object) and returns string values.
        """
        if self.graph is None:
            return []
        return [str(obj) for _, _, obj in self.graph.triples((None, predicate, None))]

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "title": self.title,
            "item_type": self.item_type.value,
            "status": self.status.value,
            "file_path": str(self.file_path),
            "priority": self.priority,
            "assignee": self.assignee,
            "created": self.created.isoformat() if self.created else None,
            "updated": self.updated.isoformat() if self.updated else None,
            "tags": self.tags,
            "depends_on": self.depends_on,
            "related": self.related,
            "blocks": self.blocks,
            "description": self.description,
            "resolution": self.resolution,
            "superseded_by": self.superseded_by,
            "priority_rank": self.priority_rank,
            "value_summary": self.value_summary,
            "compute_requirement": self.compute_requirement,
            "triple_count": len(self.graph) if self.graph else 0,
        }

    @staticmethod
    def _esc(value: str) -> str:
        """Escape a value for a Turtle "..." literal (the shared escaper, #141)."""
        return turtle_string(value)

    @staticmethod
    def _safe_uri(value: str) -> str:
        """Sanitize a value for use inside Turtle angle-bracket URIs.

        Strips characters that could break TTL syntax (<, >, newlines,
        spaces). Returns the sanitized value for use inside <...>.
        """
        import re as _re
        return _re.sub(r'[<>\s\\"]', "", value)

    def to_yurtle(self) -> str:
        """Generate Yurtle block content for this work item."""
        lines = [
            "@prefix kb: <https://yurtle.dev/kanban/> .",
            "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .",
            "",
            f"<> a kb:{self.item_type.value.title()} ;",
            f'   kb:id "{self._esc(self.id)}" ;',
            f"   kb:status kb:{self.status.value} ;",
        ]

        if self.priority:
            lines.append(f"   kb:priority kb:{self.priority} ;")

        if self.assignee:
            lines.append(f"   kb:assignee <{self._safe_uri(self.assignee)}> ;")

        if self.created:
            lines.append(f'   kb:created "{self.created.isoformat()}"^^xsd:date ;')

        if self.tags:
            tag_str = ", ".join(f'"{self._esc(tag)}"' for tag in self.tags)
            lines.append(f"   kb:tag {tag_str} ;")

        if self.depends_on:
            deps_str = ", ".join(f"<{self._safe_uri(dep)}>" for dep in self.depends_on)
            lines.append(f"   kb:dependsOn {deps_str} ;")

        if self.related:
            rel_str = ", ".join(f"<{self._safe_uri(rel)}>" for rel in self.related)
            lines.append(f"   kb:related {rel_str} ;")

        if self.resolution:
            lines.append(f'   kb:resolution "{self._esc(self.resolution)}" ;')

        if self.superseded_by:
            refs_str = ", ".join(f"<{self._safe_uri(ref)}>" for ref in self.superseded_by)
            lines.append(f"   kb:supersededBy {refs_str} ;")

        if self.compute_requirement:
            lines.append(f'   kb:computeRequirement "{self._esc(self.compute_requirement)}" ;')

        # Remove trailing semicolon from last line and add period
        lines[-1] = lines[-1].rstrip(" ;") + " ."

        return "\n".join(lines)

    def to_markdown(self) -> str:
        """Generate full markdown file content.

        Format: Frontmatter (single source of truth) + Title + Content.
        No duplicate formatted sections. No yurtle blocks.
        """
        lines = [
            "---",
            f"id: {self.id}",
            # always double-quoted; json.dumps also escapes \\ and newlines (#121)
            f"title: {yaml_quote(self.title)}",
            f"type: {self.item_type.value}",
            f"status: {self.status.value}",
        ]

        if self.priority:
            lines.append(f"priority: {self.priority}")
        # Always include assignee (null when unset), matching the templates,
        # so the key is present for later moves to fill in
        lines.append(f"assignee: {yaml_scalar(self.assignee) if self.assignee else 'null'}")
        if self.created:
            lines.append(f"created: {self.created.isoformat()}")
        if self.tags:
            lines.append(f"tags: {yaml_flow_list(self.tags)}")

        # Always include depends_on (even if empty)
        if self.depends_on:
            lines.append(f"depends_on: {yaml_flow_list(self.depends_on)}")
        else:
            lines.append("depends_on: []")

        if self.related:
            lines.append(f"related: {yaml_flow_list(self.related)}")

        if self.priority_rank is not None:
            lines.append(f"priority_rank: {self.priority_rank}")
        if self.value_summary:
            lines.append(f"value_summary: {yaml_quote(self.value_summary)}")

        if self.compute_requirement:
            lines.append(f"compute_requirement: {yaml_scalar(self.compute_requirement)}")

        if self.resolution:
            lines.append(f"resolution: {yaml_scalar(self.resolution)}")
        if self.superseded_by:
            lines.append(f"superseded_by: {yaml_flow_list(self.superseded_by)}")

        lines.extend(
            [
                "---",
                "",
                f"# {self.title}",
                "",
            ]
        )

        if self.description:
            lines.append(self.description)

        return "\n".join(lines)


@dataclass
class Board:
    """A kanban board containing work items organized by columns."""

    id: str
    name: str
    columns: list[Column]
    items: list[WorkItem] = field(default_factory=list)
    # Optional mapping from column ID to WorkItemStatus (for themed columns)
    column_status_map: dict[str, WorkItemStatus] = field(default_factory=dict)

    def get_items_by_status(self, status: WorkItemStatus) -> list[WorkItem]:
        """Get all items with a specific status."""
        return [item for item in self.items if item.status == status]

    def get_column_counts(self) -> dict[str, int]:
        """Get count of items in each column."""
        counts = {}
        for col in self.columns:
            # Use column_status_map if available, otherwise try standard parsing
            if col.id in self.column_status_map:
                status = self.column_status_map[col.id]
            else:
                try:
                    status = WorkItemStatus.from_string(col.id)
                except ValueError:
                    # Unknown column, count as 0
                    counts[col.id] = 0
                    continue
            counts[col.id] = len(self.get_items_by_status(status))
        return counts

    def get_items_by_status_and_type(
        self, status: WorkItemStatus, item_type: WorkItemType
    ) -> list[WorkItem]:
        """Get all items with a specific status and type."""
        return [
            item for item in self.items
            if item.status == status and item.item_type == item_type
        ]

    def get_wip_violations(self) -> list[tuple[Column, int, str | None]]:
        """Get columns that are over WIP limit.

        Returns list of (column, count, item_type_or_none) tuples.
        When per-type limits are configured, returns per-type violations.
        When only aggregate limits exist, returns (col, count, None).
        """
        violations: list[tuple[Column, int, str | None]] = []
        for col in self.columns:
            # Resolve column to status
            if col.id in self.column_status_map:
                status = self.column_status_map[col.id]
            else:
                try:
                    status = WorkItemStatus.from_string(col.id)
                except ValueError:
                    continue

            if col.type_wip_limits is not None:
                # Check per-type limits
                items_in_col = self.get_items_by_status(status)
                type_counts: dict[str, int] = {}
                for item in items_in_col:
                    type_counts[item.item_type.value] = (
                        type_counts.get(item.item_type.value, 0) + 1
                    )
                for type_name, type_count in type_counts.items():
                    if col.is_over_wip(type_count, item_type=type_name):
                        violations.append((col, type_count, type_name))
            else:
                # Aggregate check
                count = len(self.get_items_by_status(status))
                if col.is_over_wip(count):
                    violations.append((col, count, None))
        return violations
