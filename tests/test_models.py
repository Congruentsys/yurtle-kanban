"""Tests for work item models."""

from datetime import date
from pathlib import Path

import pytest
import yaml

from yurtle_kanban.models import (
    Board,
    Column,
    Comment,
    WorkItem,
    WorkItemStatus,
    WorkItemType,
)


class TestWorkItem:
    """Tests for the WorkItem model."""

    def test_create_work_item(self):
        """Test creating a basic work item."""
        item = WorkItem(
            id="FEAT-001",
            title="Add dark mode",
            item_type=WorkItemType.FEATURE,
            status=WorkItemStatus.IN_PROGRESS,
            file_path=Path("work/FEAT-001.md"),
        )

        assert item.id == "FEAT-001"
        assert item.title == "Add dark mode"
        assert item.item_type == WorkItemType.FEATURE
        assert item.status == WorkItemStatus.IN_PROGRESS

    def test_work_item_with_optional_fields(self):
        """Test work item with all optional fields."""
        item = WorkItem(
            id="BUG-042",
            title="Fix login issue",
            item_type=WorkItemType.BUG,
            status=WorkItemStatus.READY,
            file_path=Path("work/bugs/BUG-042.md"),
            priority="high",
            assignee="../team/dev-1.md",
            created=date(2026, 1, 12),
            tags=["auth", "critical"],
            depends_on=["FEAT-001"],
        )

        assert item.priority == "high"
        assert item.assignee == "../team/dev-1.md"
        assert item.created == date(2026, 1, 12)
        assert "auth" in item.tags
        assert "FEAT-001" in item.depends_on

    def test_to_yurtle(self):
        """Test generating Yurtle block content."""
        item = WorkItem(
            id="FEAT-001",
            title="Test feature",
            item_type=WorkItemType.FEATURE,
            status=WorkItemStatus.IN_PROGRESS,
            file_path=Path("work/FEAT-001.md"),
            priority="high",
            tags=["ui", "ux"],
        )

        yurtle = item.to_yurtle()

        assert "@prefix kb:" in yurtle
        assert 'kb:id "FEAT-001"' in yurtle
        assert "kb:status kb:in_progress" in yurtle
        assert "kb:priority kb:high" in yurtle
        assert '"ui"' in yurtle
        assert '"ux"' in yurtle

    def test_to_dict(self):
        """Test converting to dictionary."""
        item = WorkItem(
            id="FEAT-001",
            title="Test feature",
            item_type=WorkItemType.FEATURE,
            status=WorkItemStatus.READY,
            file_path=Path("work/FEAT-001.md"),
            priority="high",
        )

        data = item.to_dict()

        assert data["id"] == "FEAT-001"
        assert data["title"] == "Test feature"
        assert data["item_type"] == "feature"
        assert data["status"] == "ready"
        assert data["priority"] == "high"

    def test_priority_score(self):
        """Test priority score calculation."""
        critical = WorkItem(
            id="FEAT-001",
            title="Critical item",
            item_type=WorkItemType.FEATURE,
            status=WorkItemStatus.READY,
            file_path=Path("work/FEAT-001.md"),
            priority="critical",
        )
        low = WorkItem(
            id="FEAT-002",
            title="Low item",
            item_type=WorkItemType.FEATURE,
            status=WorkItemStatus.READY,
            file_path=Path("work/FEAT-002.md"),
            priority="low",
        )

        assert critical.priority_score > low.priority_score

    def test_is_blocked(self):
        """Test blocked status detection."""
        blocked = WorkItem(
            id="FEAT-001",
            title="Blocked item",
            item_type=WorkItemType.FEATURE,
            status=WorkItemStatus.BLOCKED,
            file_path=Path("work/FEAT-001.md"),
        )
        active = WorkItem(
            id="FEAT-002",
            title="Active item",
            item_type=WorkItemType.FEATURE,
            status=WorkItemStatus.IN_PROGRESS,
            file_path=Path("work/FEAT-002.md"),
        )

        assert blocked.is_blocked is True
        assert active.is_blocked is False

    def test_to_markdown(self):
        """Test generating full markdown content."""
        item = WorkItem(
            id="FEAT-001",
            title="Test feature",
            item_type=WorkItemType.FEATURE,
            status=WorkItemStatus.READY,
            file_path=Path("work/FEAT-001.md"),
            priority="high",
            description="This is a test feature.",
        )

        md = item.to_markdown()

        assert "---" in md
        assert "id: FEAT-001" in md
        assert 'title: "Test feature"' in md
        assert "type: feature" in md
        assert "status: ready" in md
        assert "assignee: null" in md
        assert "# Test feature" in md

    def test_resolution_fields_default(self):
        """resolution and superseded_by default to None/empty."""
        item = WorkItem(
            id="FEAT-001",
            title="Test",
            item_type=WorkItemType.FEATURE,
            status=WorkItemStatus.READY,
            file_path=Path("test.md"),
        )
        assert item.resolution is None
        assert item.superseded_by == []

    def test_resolution_in_to_dict(self):
        """to_dict includes resolution and superseded_by."""
        item = WorkItem(
            id="FEAT-001",
            title="Test",
            item_type=WorkItemType.FEATURE,
            status=WorkItemStatus.DONE,
            file_path=Path("test.md"),
            resolution="superseded",
            superseded_by=["FEAT-002"],
        )
        data = item.to_dict()
        assert data["resolution"] == "superseded"
        assert data["superseded_by"] == ["FEAT-002"]

    def test_resolution_in_to_markdown(self):
        """to_markdown includes resolution when set."""
        item = WorkItem(
            id="FEAT-001",
            title="Closed item",
            item_type=WorkItemType.FEATURE,
            status=WorkItemStatus.DONE,
            file_path=Path("test.md"),
            resolution="wont_do",
        )
        md = item.to_markdown()
        assert "resolution: wont_do" in md

    def test_superseded_by_in_to_markdown(self):
        """to_markdown includes superseded_by when set."""
        item = WorkItem(
            id="FEAT-001",
            title="Replaced item",
            item_type=WorkItemType.FEATURE,
            status=WorkItemStatus.DONE,
            file_path=Path("test.md"),
            resolution="superseded",
            superseded_by=["FEAT-002", "FEAT-003"],
        )
        md = item.to_markdown()
        assert "superseded_by: [FEAT-002, FEAT-003]" in md

    def test_resolution_in_to_yurtle(self):
        """to_yurtle includes resolution triple when set."""
        item = WorkItem(
            id="FEAT-001",
            title="Test",
            item_type=WorkItemType.FEATURE,
            status=WorkItemStatus.DONE,
            file_path=Path("test.md"),
            resolution="completed",
        )
        yurtle = item.to_yurtle()
        assert 'kb:resolution "completed"' in yurtle

    def test_superseded_by_in_to_yurtle(self):
        """to_yurtle includes kb:supersededBy triple when set."""
        item = WorkItem(
            id="FEAT-001",
            title="Replaced",
            item_type=WorkItemType.FEATURE,
            status=WorkItemStatus.DONE,
            file_path=Path("test.md"),
            resolution="superseded",
            superseded_by=["FEAT-002"],
        )
        yurtle = item.to_yurtle()
        assert "kb:supersededBy" in yurtle
        assert "<FEAT-002>" in yurtle

    def test_no_resolution_fields_when_unset(self):
        """to_markdown/to_yurtle omit resolution fields when not set."""
        item = WorkItem(
            id="FEAT-001",
            title="Test",
            item_type=WorkItemType.FEATURE,
            status=WorkItemStatus.READY,
            file_path=Path("test.md"),
        )
        md = item.to_markdown()
        yurtle = item.to_yurtle()
        assert "resolution" not in md
        assert "superseded_by" not in md
        assert "resolution" not in yurtle
        assert "supersededBy" not in yurtle


class TestWorkItemStatus:
    """Tests for work item statuses."""

    def test_all_statuses_exist(self):
        """Test that all expected statuses are defined."""
        statuses = [s.value for s in WorkItemStatus]
        assert "backlog" in statuses
        assert "in_progress" in statuses
        assert "done" in statuses

    def test_from_string(self):
        """Test parsing status from string."""
        assert WorkItemStatus.from_string("backlog") == WorkItemStatus.BACKLOG
        assert WorkItemStatus.from_string("in_progress") == WorkItemStatus.IN_PROGRESS
        assert WorkItemStatus.from_string("in-progress") == WorkItemStatus.IN_PROGRESS

    def test_from_string_invalid(self):
        """Test parsing invalid status."""
        with pytest.raises(ValueError):
            WorkItemStatus.from_string("invalid")


class TestWorkItemType:
    """Tests for work item types."""

    def test_all_types_exist(self):
        """Test that all expected types are defined."""
        types = [t.value for t in WorkItemType]
        assert "feature" in types
        assert "bug" in types
        assert "epic" in types

    def test_from_string(self):
        """Test parsing type from string."""
        assert WorkItemType.from_string("feature") == WorkItemType.FEATURE
        assert WorkItemType.from_string("bug") == WorkItemType.BUG


class TestColumn:
    """Tests for Column model."""

    def test_wip_limit(self):
        """Test WIP limit checking."""
        col = Column(id="in_progress", name="In Progress", order=3, wip_limit=3)

        assert col.is_over_wip(2) is False
        assert col.is_over_wip(3) is False
        assert col.is_over_wip(4) is True

    def test_no_wip_limit(self):
        """Test column without WIP limit."""
        col = Column(id="backlog", name="Backlog", order=1)

        assert col.is_over_wip(100) is False


class TestComment:
    """Tests for Comment model."""

    def test_create_comment(self):
        """Test creating a comment."""
        comment = Comment(
            content="This looks good!",
            author="reviewer",
        )

        assert comment.content == "This looks good!"
        assert comment.author == "reviewer"
        assert comment.created_at is not None

    def test_to_dict(self):
        """Test converting comment to dict."""
        comment = Comment(content="Test", author="user")
        data = comment.to_dict()

        assert data["content"] == "Test"
        assert data["author"] == "user"
        assert "created_at" in data


class TestBoard:
    """Tests for Board model."""

    def test_get_items_by_status(self):
        """Test filtering items by status."""
        columns = [
            Column("backlog", "Backlog", 1),
            Column("ready", "Ready", 2),
            Column("done", "Done", 3),
        ]
        items = [
            WorkItem("FEAT-001", "Item 1", WorkItemType.FEATURE, WorkItemStatus.READY, Path(".")),
            WorkItem("FEAT-002", "Item 2", WorkItemType.FEATURE, WorkItemStatus.READY, Path(".")),
            WorkItem("FEAT-003", "Item 3", WorkItemType.FEATURE, WorkItemStatus.DONE, Path(".")),
        ]
        board = Board(id="test", name="Test Board", columns=columns, items=items)

        ready_items = board.get_items_by_status(WorkItemStatus.READY)
        assert len(ready_items) == 2

        done_items = board.get_items_by_status(WorkItemStatus.DONE)
        assert len(done_items) == 1

    def test_get_column_counts(self):
        """Test getting column counts."""
        columns = [
            Column("ready", "Ready", 1),
            Column("in_progress", "In Progress", 2),
        ]
        items = [
            WorkItem("FEAT-001", "Item 1", WorkItemType.FEATURE, WorkItemStatus.READY, Path(".")),
            WorkItem("FEAT-002", "Item 2", WorkItemType.FEATURE, WorkItemStatus.IN_PROGRESS, Path(".")),
            WorkItem("FEAT-003", "Item 3", WorkItemType.FEATURE, WorkItemStatus.IN_PROGRESS, Path(".")),
        ]
        board = Board(id="test", name="Test Board", columns=columns, items=items)

        counts = board.get_column_counts()
        assert counts["ready"] == 1
        assert counts["in_progress"] == 2

    def test_get_wip_violations(self):
        """Test detecting WIP violations."""
        columns = [
            Column("in_progress", "In Progress", 1, wip_limit=2),
        ]
        items = [
            WorkItem("FEAT-001", "Item 1", WorkItemType.FEATURE, WorkItemStatus.IN_PROGRESS, Path(".")),
            WorkItem("FEAT-002", "Item 2", WorkItemType.FEATURE, WorkItemStatus.IN_PROGRESS, Path(".")),
            WorkItem("FEAT-003", "Item 3", WorkItemType.FEATURE, WorkItemStatus.IN_PROGRESS, Path(".")),
        ]
        board = Board(id="test", name="Test Board", columns=columns, items=items)

        violations = board.get_wip_violations()
        assert len(violations) == 1
        assert violations[0][0].id == "in_progress"
        assert violations[0][1] == 3


# ---------------------------------------------------------------------------
# Issue #121 -- every string value to_markdown writes reads back unchanged
# ---------------------------------------------------------------------------

_YAML_MISREAD_VALUES = [
    pytest.param("team: core", id="colon-space"),
    pytest.param("yes", id="yes-bool"),
    pytest.param("null", id="null"),
    pytest.param("#core", id="leading-hash"),
    pytest.param("a, b", id="comma-space"),
    pytest.param("[x]", id="leading-bracket"),
    pytest.param("{x}", id="leading-brace"),
    pytest.param("&a", id="leading-ampersand"),
    pytest.param("*a", id="leading-star"),
    pytest.param("123", id="int"),
]

_LIST_FIELDS = ["tags", "related", "depends_on", "superseded_by"]
_SCALAR_FIELDS = ["resolution", "compute_requirement"]

# What to_markdown writes today for an ordinary item -- must not change.
_ORDINARY_MARKDOWN = (
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
    "Body text.\n"
)


def _ordinary_item(**overrides) -> WorkItem:
    fields = dict(
        id="FEAT-001",
        title="Add dark mode",
        item_type=WorkItemType.FEATURE,
        status=WorkItemStatus.READY,
        file_path=Path("work/FEAT-001.md"),
        priority="high",
        assignee="agent-x",
        created=date(2026, 1, 12),
        tags=["backend", "ui-polish"],
        depends_on=["EXP-001"],
        related=["FEAT-002", "BUG-003"],
        resolution="completed",
        superseded_by=["FEAT-009"],
        compute_requirement="gpu",
        description="Body text.\n",
    )
    fields.update(overrides)
    return WorkItem(**fields)


class TestAllFrontmatterValuesRoundTrip:
    """Every string value WorkItem.to_markdown writes reads back as the same string (#121)."""

    @staticmethod
    def _frontmatter_text(markdown: str) -> str:
        lines = markdown.split("\n")
        assert lines[0] == "---"
        return "\n".join(lines[1:lines.index("---", 1)])

    @classmethod
    def _frontmatter(cls, item: WorkItem) -> dict:
        markdown = item.to_markdown()
        try:
            fm = yaml.safe_load(cls._frontmatter_text(markdown))
        except yaml.YAMLError as exc:
            pytest.fail(f"frontmatter is not valid YAML: {exc}\n{markdown}")
        assert isinstance(fm, dict), markdown
        return fm

    # -- flow-list elements --------------------------------------------------

    @pytest.mark.parametrize("field_name", _LIST_FIELDS)
    @pytest.mark.parametrize("value", _YAML_MISREAD_VALUES)
    def test_list_element_round_trips(self, field_name, value):
        """A lone element of tags/related/depends_on/superseded_by reads back exactly."""
        fm = self._frontmatter(_ordinary_item(**{field_name: [value]}))
        assert fm[field_name] == [value]

    @pytest.mark.parametrize("field_name", _LIST_FIELDS)
    @pytest.mark.parametrize("value", _YAML_MISREAD_VALUES)
    def test_list_element_among_ordinary_round_trips(self, field_name, value):
        """The element keeps its place and the list keeps its length."""
        fm = self._frontmatter(
            _ordinary_item(**{field_name: ["backend", value, "EXP-001"]})
        )
        assert fm[field_name] == ["backend", value, "EXP-001"]

    # -- scalars -------------------------------------------------------------

    @pytest.mark.parametrize("field_name", _SCALAR_FIELDS)
    @pytest.mark.parametrize("value", _YAML_MISREAD_VALUES)
    def test_scalar_round_trips(self, field_name, value):
        """resolution / compute_requirement read back as the same string."""
        fm = self._frontmatter(_ordinary_item(**{field_name: value}))
        assert fm[field_name] == value
        assert isinstance(fm[field_name], str)

    # -- title ---------------------------------------------------------------

    @pytest.mark.parametrize(
        "title",
        [
            pytest.param("C:\\temp\\new", id="backslash-escapes"),
            pytest.param("ends with \\", id="trailing-backslash"),
            pytest.param("line one\nline two", id="newline"),
            pytest.param('say "hi" \\o/', id="quote-and-backslash"),
        ],
    )
    def test_title_round_trips(self, title):
        """The title reads back exactly, backslashes and newlines included."""
        fm = self._frontmatter(_ordinary_item(title=title))
        assert fm["title"] == title

    # -- must stay true (controls) -------------------------------------------

    @pytest.mark.parametrize("title", ["Add dark mode", 'say "hi"', "team: core"])
    def test_control_title_round_trips(self, title):
        """Titles that already round-trip keep doing so."""
        fm = self._frontmatter(_ordinary_item(title=title))
        assert fm["title"] == title

    def test_control_ordinary_values_written_unquoted(self):
        """Ordinary tags and IDs stay plain, exactly as today."""
        front = self._frontmatter_text(_ordinary_item().to_markdown()).split("\n")
        assert "tags: [backend, ui-polish]" in front
        assert "depends_on: [EXP-001]" in front
        assert "related: [FEAT-002, BUG-003]" in front
        assert "superseded_by: [FEAT-009]" in front
        assert "resolution: completed" in front
        assert "compute_requirement: gpu" in front

    def test_control_empty_depends_on_written_as_empty_list(self):
        front = self._frontmatter_text(
            _ordinary_item(depends_on=[]).to_markdown()
        ).split("\n")
        assert "depends_on: []" in front

    def test_control_ordinary_item_markdown_unchanged(self):
        """An ordinary item's markdown is byte-identical to today's output."""
        assert _ordinary_item().to_markdown() == _ORDINARY_MARKDOWN

    def test_control_ordinary_values_round_trip(self):
        fm = self._frontmatter(_ordinary_item())
        assert fm["tags"] == ["backend", "ui-polish"]
        assert fm["depends_on"] == ["EXP-001"]
        assert fm["related"] == ["FEAT-002", "BUG-003"]
        assert fm["superseded_by"] == ["FEAT-009"]
        assert fm["resolution"] == "completed"
        assert fm["compute_requirement"] == "gpu"


# ---------------------------------------------------------------------------
# Issue #141 — every string literal WorkItem.to_yurtle writes parses and round-trips
# ---------------------------------------------------------------------------

_KB = "https://yurtle.dev/kanban/"

_NASTY_TURTLE_VALUES = [
    pytest.param('say "hi" there', id="quote"),
    pytest.param("C:\\data\\x", id="backslash"),
    pytest.param("ends with backslash\\", id="trailing-backslash"),
    pytest.param("p\nq", id="newline"),
    pytest.param("p\r\nq", id="crlf"),
    pytest.param("p\rq", id="cr"),
    pytest.param("p\tq", id="tab"),
    pytest.param('x" ;\n   kb:status kb:done ;\n   kb:id "y', id="injection"),
    pytest.param("plain-value", id="plain"),
]


def _parse_yurtle(text: str):
    from rdflib import Graph

    if text.startswith("```"):
        text = "\n".join(text.split("\n")[1:-1])
    g = Graph()
    g.parse(data=text, format="turtle", publicID="http://x/")
    return g


def _literals(g, local: str) -> list[str]:
    from rdflib import URIRef

    return [str(o) for o in g.objects(None, URIRef(_KB + local))]


class TestToYurtleLiteralsRoundTripIssue141:
    """Every string literal WorkItem.to_yurtle writes (kb:id, kb:tag,
    kb:resolution, kb:computeRequirement) parses with rdflib and reads back as
    exactly the value on the item (#141)."""

    @pytest.mark.parametrize("value", _NASTY_TURTLE_VALUES)
    def test_id_round_trips(self, value):
        g = _parse_yurtle(_ordinary_item(id=value).to_yurtle())
        assert _literals(g, "id") == [value]
        assert _literals(g, "status") == [_KB + "ready"]

    @pytest.mark.parametrize("value", _NASTY_TURTLE_VALUES)
    def test_resolution_round_trips(self, value):
        g = _parse_yurtle(_ordinary_item(resolution=value).to_yurtle())
        assert _literals(g, "resolution") == [value]
        assert _literals(g, "id") == ["FEAT-001"]

    @pytest.mark.parametrize("value", _NASTY_TURTLE_VALUES)
    def test_compute_requirement_round_trips(self, value):
        g = _parse_yurtle(_ordinary_item(compute_requirement=value).to_yurtle())
        assert _literals(g, "computeRequirement") == [value]
        assert _literals(g, "id") == ["FEAT-001"]

    @pytest.mark.parametrize("value", _NASTY_TURTLE_VALUES)
    def test_tag_round_trips(self, value):
        g = _parse_yurtle(_ordinary_item(tags=["backend", value]).to_yurtle())
        assert sorted(_literals(g, "tag")) == sorted(["backend", value])

    def test_all_nasty_at_once_round_trips(self):
        """Every literal carrying a newline/CR/tab/quote/backslash at once still parses."""
        v = 'a"b\\c\nd\re\tf'
        item = _ordinary_item(
            id=v + "1", resolution=v + "2", compute_requirement=v + "3", tags=[v + "4"]
        )
        g = _parse_yurtle(item.to_yurtle())
        assert _literals(g, "id") == [v + "1"]
        assert _literals(g, "resolution") == [v + "2"]
        assert _literals(g, "computeRequirement") == [v + "3"]
        assert _literals(g, "tag") == [v + "4"]


class TestToYurtlePlainValuesUnchangedIssue141:
    """Negative controls: plain values are written byte-identically to before (#141)."""

    def test_plain_to_yurtle_bytes(self):
        assert _ordinary_item().to_yurtle() == (
            "@prefix kb: <https://yurtle.dev/kanban/> .\n"
            "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .\n"
            "\n"
            "<> a kb:Feature ;\n"
            '   kb:id "FEAT-001" ;\n'
            "   kb:status kb:ready ;\n"
            "   kb:priority kb:high ;\n"
            "   kb:assignee <agent-x> ;\n"
            '   kb:created "2026-01-12"^^xsd:date ;\n'
            '   kb:tag "backend", "ui-polish" ;\n'
            "   kb:dependsOn <EXP-001> ;\n"
            "   kb:related <FEAT-002>, <BUG-003> ;\n"
            '   kb:resolution "completed" ;\n'
            "   kb:supersededBy <FEAT-009> ;\n"
            '   kb:computeRequirement "gpu" .'
        )

    def test_quote_and_backslash_escaping_unchanged(self):
        y = _ordinary_item(resolution='a "b" C:\\d').to_yurtle()
        assert r'   kb:resolution "a \"b\" C:\\d" ;' in y
