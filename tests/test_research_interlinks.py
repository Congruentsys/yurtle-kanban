"""Tests for research interlinks display in campaign/voyage show."""

import subprocess
from io import StringIO
from pathlib import Path

import pytest
from click.testing import CliRunner
from rich.console import Console

from yurtle_kanban.cli import main
from yurtle_kanban.config import KanbanConfig, PathConfig
from yurtle_kanban.models import WorkItem, WorkItemStatus, WorkItemType
from yurtle_kanban.research_interlinks import (
    EXPR,
    HYP,
    MEASURE,
    _first_triple,
    _obj_id,
    has_research_items,
    render_research_interlinks,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def nautical_repo(tmp_path):
    """Create a minimal git repo with nautical theme."""
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
    (tmp_path / "kanban-work" / "expeditions").mkdir(parents=True)
    (tmp_path / "kanban-work" / "voyages").mkdir(parents=True)
    (tmp_path / "kanban-work" / "signals").mkdir(parents=True)

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
    config.save(tmp_path / ".kanban" / "config.yaml")
    return tmp_path


@pytest.fixture
def nautical_runner(nautical_repo, monkeypatch):
    """Click runner with cwd set to nautical repo."""
    from yurtle_kanban import config as config_mod

    config_mod._theme_cache.clear()
    monkeypatch.chdir(nautical_repo)
    return CliRunner()


def _write_hypothesis(repo: Path, hyp_id: str, title: str, paper: str, target: str, related: list[str] | None = None):
    """Write a hypothesis markdown file with Turtle block."""
    related_line = f"related: [{', '.join(related)}]\n" if related else ""
    content = (
        f"---\n"
        f"id: {hyp_id}\n"
        f'title: "{title}"\n'
        f"type: hypothesis\n"
        f"status: backlog\n"
        f"created: 2026-02-28\n"
        f"priority: high\n"
        f"{related_line}"
        f"---\n\n"
        f"# {title}\n\n"
        f"```turtle\n"
        f"@prefix hyp: <https://nusy.dev/hypothesis/> .\n"
        f"@prefix paper: <https://nusy.dev/paper/> .\n"
        f"@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n"
        f"\n"
        f"<#{hyp_id}> a hyp:Hypothesis ;\n"
        f'    rdfs:label "{title}" ;\n'
        f"    hyp:paper paper:{paper} ;\n"
        f'    hyp:target "{target}" .\n'
        f"```\n"
    )
    file_path = repo / "kanban-work" / "expeditions" / f"{hyp_id}-{title.replace(' ', '-')[:30]}.md"
    file_path.write_text(content)
    return file_path


def _write_experiment(repo: Path, expr_id: str, title: str, paper: str, hypothesis: str, related: list[str] | None = None):
    """Write an experiment markdown file with Turtle block."""
    related_line = f"related: [{', '.join(related)}]\n" if related else ""
    content = (
        f"---\n"
        f"id: {expr_id}\n"
        f'title: "{title}"\n'
        f"type: experiment\n"
        f"status: in_progress\n"
        f"created: 2026-02-28\n"
        f"priority: medium\n"
        f"{related_line}"
        f"---\n\n"
        f"# {title}\n\n"
        f"```turtle\n"
        f"@prefix expr: <https://nusy.dev/experiment/> .\n"
        f"@prefix hyp: <https://nusy.dev/hypothesis/> .\n"
        f"@prefix paper: <https://nusy.dev/paper/> .\n"
        f"@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n"
        f"\n"
        f"<#{expr_id}> a expr:Experiment ;\n"
        f'    rdfs:label "{title}" ;\n'
        f"    expr:paper paper:{paper} ;\n"
        f"    expr:hypothesis hyp:{hypothesis} .\n"
        f"```\n"
    )
    file_path = repo / "kanban-work" / "expeditions" / f"{expr_id}-{title.replace(' ', '-')[:30]}.md"
    file_path.write_text(content)
    return file_path


def _write_measure(repo: Path, measure_id: str, title: str, unit: str, category: str, related: list[str] | None = None):
    """Write a measure markdown file with Turtle block."""
    related_line = f"related: [{', '.join(related)}]\n" if related else ""
    content = (
        f"---\n"
        f"id: {measure_id}\n"
        f'title: "{title}"\n'
        f"type: measure\n"
        f"status: done\n"
        f"created: 2026-02-28\n"
        f"priority: low\n"
        f"{related_line}"
        f"---\n\n"
        f"# {title}\n\n"
        f"```turtle\n"
        f"@prefix measure: <https://nusy.dev/measure/> .\n"
        f"@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n"
        f"\n"
        f"<#{measure_id}> a measure:Measure ;\n"
        f'    rdfs:label "{title}" ;\n'
        f'    measure:unit "{unit}" ;\n'
        f'    measure:category "{category}" .\n'
        f"```\n"
    )
    file_path = repo / "kanban-work" / "expeditions" / f"{measure_id}-{title.replace(' ', '-')[:30]}.md"
    file_path.write_text(content)
    return file_path


def _make_item_with_graph(
    file_path: Path, item_id: str, title: str,
    item_type: WorkItemType, status: WorkItemStatus,
) -> WorkItem:
    """Build a WorkItem with its graph populated via yurtle-rdflib.

    Mirrors what KanbanService._parse_graph() does: reads the file content
    and parses it with yurtle_rdflib.parse_yurtle() to get the RDF graph
    (frontmatter + fenced blocks).
    """
    import yurtle_rdflib

    content = file_path.read_text()
    doc = yurtle_rdflib.parse_yurtle(content)
    return WorkItem(
        id=item_id,
        title=title,
        item_type=item_type,
        status=status,
        file_path=file_path,
        graph=doc.graph,
    )


# ---------------------------------------------------------------------------
# Unit tests: _obj_id
# ---------------------------------------------------------------------------


class TestObjId:
    def test_fragment_uri(self):
        assert _obj_id("file:///tmp/test.md#H130.1") == "H130.1"

    def test_namespace_uri(self):
        assert _obj_id("https://nusy.dev/paper/PAPER-130") == "PAPER-130"

    def test_plain_uri(self):
        assert _obj_id("https://example.com/foo/bar") == "bar"


# ---------------------------------------------------------------------------
# Unit tests: has_research_items
# ---------------------------------------------------------------------------


class TestHasResearchItems:
    def test_no_hdd_items(self):
        items = [
            WorkItem(id="EXP-001", title="T", item_type=WorkItemType.EXPEDITION,
                     status=WorkItemStatus.BACKLOG, file_path=Path("/tmp/t.md")),
        ]
        assert has_research_items(items) is False

    def test_hdd_item_without_graph(self):
        """HDD item with no graph should not count as research item."""
        items = [
            WorkItem(id="H130.1", title="T", item_type=WorkItemType.HYPOTHESIS,
                     status=WorkItemStatus.BACKLOG, file_path=Path("/tmp/t.md")),
        ]
        assert has_research_items(items) is False

    def test_hdd_item_with_graph_detected(self, nautical_repo):
        """HDD item with a populated graph should be detected."""
        fp = _write_hypothesis(nautical_repo, "H130.1", "Test", "PAPER-130", ">=85%")
        item = _make_item_with_graph(fp, "H130.1", "Test", WorkItemType.HYPOTHESIS, WorkItemStatus.BACKLOG)
        assert has_research_items([item]) is True


# ---------------------------------------------------------------------------
# Unit tests: _first_triple (queries WorkItem.graph)
# ---------------------------------------------------------------------------


class TestFirstTriple:
    def test_hypothesis_paper_triple(self, nautical_repo):
        """Hypothesis item graph should contain hyp:paper triple."""
        fp = _write_hypothesis(nautical_repo, "H130.1", "Test Hyp", "PAPER-130", ">=85%")
        item = _make_item_with_graph(fp, "H130.1", "Test Hyp", WorkItemType.HYPOTHESIS, WorkItemStatus.BACKLOG)
        paper_val = _first_triple(item, HYP.paper)
        assert paper_val is not None
        assert "PAPER-130" in paper_val

    def test_hypothesis_target_triple(self, nautical_repo):
        """Hypothesis item graph should contain hyp:target triple."""
        fp = _write_hypothesis(nautical_repo, "H130.1", "Test Hyp", "PAPER-130", ">=85%")
        item = _make_item_with_graph(fp, "H130.1", "Test Hyp", WorkItemType.HYPOTHESIS, WorkItemStatus.BACKLOG)
        assert _first_triple(item, HYP.target) == ">=85%"

    def test_experiment_hypothesis_triple(self, nautical_repo):
        """Experiment item graph should contain expr:hypothesis triple."""
        fp = _write_experiment(nautical_repo, "EXPR-130", "Test Exp", "PAPER-130", "H130.1")
        item = _make_item_with_graph(fp, "EXPR-130", "Test Exp", WorkItemType.EXPERIMENT, WorkItemStatus.IN_PROGRESS)
        hyp_val = _first_triple(item, EXPR.hypothesis)
        assert hyp_val is not None
        assert "H130.1" in hyp_val

    def test_experiment_paper_triple(self, nautical_repo):
        """Experiment item graph should contain expr:paper triple."""
        fp = _write_experiment(nautical_repo, "EXPR-130", "Test Exp", "PAPER-130", "H130.1")
        item = _make_item_with_graph(fp, "EXPR-130", "Test Exp", WorkItemType.EXPERIMENT, WorkItemStatus.IN_PROGRESS)
        paper_val = _first_triple(item, EXPR.paper)
        assert paper_val is not None
        assert "PAPER-130" in paper_val

    def test_measure_unit_category(self, nautical_repo):
        """Measure item graph should contain measure:unit and measure:category triples."""
        fp = _write_measure(nautical_repo, "M-001", "Accuracy", "percent", "accuracy")
        item = _make_item_with_graph(fp, "M-001", "Accuracy", WorkItemType.MEASURE, WorkItemStatus.DONE)
        assert _first_triple(item, MEASURE.unit) == "percent"
        assert _first_triple(item, MEASURE.category) == "accuracy"

    def test_no_turtle_block_returns_none(self, tmp_path):
        """Item without Turtle block should return None for HDD predicates."""
        fp = tmp_path / "test.md"
        fp.write_text("---\nid: EXP-001\ntitle: Test\ntype: expedition\nstatus: backlog\n---\n\n# Test\n")
        item = _make_item_with_graph(fp, "EXP-001", "Test", WorkItemType.EXPEDITION, WorkItemStatus.BACKLOG)
        assert _first_triple(item, HYP.paper) is None

    def test_no_graph_returns_none(self):
        """Item with no graph should return None."""
        item = WorkItem(id="X-999", title="T", item_type=WorkItemType.HYPOTHESIS,
                        status=WorkItemStatus.BACKLOG, file_path=Path("/nonexistent/file.md"))
        assert _first_triple(item, HYP.paper) is None


# ---------------------------------------------------------------------------
# Unit tests: render_research_interlinks
# ---------------------------------------------------------------------------


class TestRender:
    def test_empty_items_no_output(self):
        """No HDD items should produce no output."""
        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=120)
        items = [
            WorkItem(id="EXP-001", title="T", item_type=WorkItemType.EXPEDITION,
                     status=WorkItemStatus.BACKLOG, file_path=Path("/tmp/t.md")),
        ]
        render_research_interlinks(items, console)
        assert buf.getvalue() == ""

    def test_hypothesis_renders_table(self, nautical_repo):
        """Hypothesis with Turtle block should render Research Interlinks section."""
        fp = _write_hypothesis(nautical_repo, "H130.1", "Test Hyp", "PAPER-130", ">=85%")
        item = _make_item_with_graph(fp, "H130.1", "Test Hyp", WorkItemType.HYPOTHESIS, WorkItemStatus.BACKLOG)

        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=120)
        render_research_interlinks([item], console)
        output = buf.getvalue()
        assert "Research Interlinks" in output
        assert "Hypotheses" in output
        assert "H130.1" in output
        assert "PAPER-130" in output

    def test_experiment_renders_table(self, nautical_repo):
        """Experiment with Turtle block should render experiment table."""
        fp = _write_experiment(nautical_repo, "EXPR-130", "Signal Fusion", "PAPER-130", "H130.1")
        item = _make_item_with_graph(fp, "EXPR-130", "Signal Fusion", WorkItemType.EXPERIMENT, WorkItemStatus.IN_PROGRESS)

        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=120)
        render_research_interlinks([item], console)
        output = buf.getvalue()
        assert "Experiments" in output
        assert "EXPR-130" in output
        assert "H130.1" in output

    def test_measure_renders_table(self, nautical_repo):
        """Measure with Turtle block should render measure table."""
        fp = _write_measure(nautical_repo, "M-001", "Accuracy", "percent", "accuracy")
        item = _make_item_with_graph(fp, "M-001", "Accuracy", WorkItemType.MEASURE, WorkItemStatus.DONE)

        buf = StringIO()
        console = Console(file=buf, force_terminal=True, width=120)
        render_research_interlinks([item], console)
        output = buf.getvalue()
        assert "Measures" in output
        assert "M-001" in output
        assert "percent" in output


# ---------------------------------------------------------------------------
# Integration test: voyage show with HDD items
# ---------------------------------------------------------------------------


class TestVoyageShowWithResearchInterlinks:
    def test_show_with_hypothesis_displays_interlinks(self, nautical_runner, nautical_repo):
        """Voyage show should display Research Interlinks when HDD items are linked."""
        nautical_runner.invoke(
            main, ["voyage", "create", "Research Campaign"],
            catch_exceptions=False,
        )
        _write_hypothesis(
            nautical_repo, "H130.1", "Test Hypothesis",
            "PAPER-130", ">=85%", related=["VOY-001"],
        )
        result = nautical_runner.invoke(
            main, ["voyage", "show", "VOY-001"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert "Research Interlinks" in result.output
        assert "Hypotheses" in result.output
        assert "H130.1" in result.output

    def test_show_with_mixed_items(self, nautical_runner, nautical_repo):
        """Voyage show with both expeditions and HDD items shows both tables."""
        nautical_runner.invoke(
            main, ["voyage", "create", "Mixed Campaign"],
            catch_exceptions=False,
        )
        nautical_runner.invoke(
            main, ["create", "expedition", "Phase 1 Work", "--priority", "high"],
            catch_exceptions=False,
        )
        nautical_runner.invoke(
            main, ["voyage", "add", "VOY-001", "EXP-001"],
            catch_exceptions=False,
        )
        _write_hypothesis(
            nautical_repo, "H130.1", "Test Hyp",
            "PAPER-130", ">=85%", related=["VOY-001"],
        )
        _write_experiment(
            nautical_repo, "EXPR-130", "Signal Fusion",
            "PAPER-130", "H130.1", related=["VOY-001"],
        )

        result = nautical_runner.invoke(
            main, ["voyage", "show", "VOY-001"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert "EXP-001" in result.output
        assert "Research Interlinks" in result.output
        assert "Hypotheses" in result.output
        assert "Experiments" in result.output
        assert "PAPER-130" in result.output

    def test_show_without_hdd_items_no_interlinks(self, nautical_runner, nautical_repo):
        """Voyage show with only expeditions should NOT show Research Interlinks."""
        nautical_runner.invoke(
            main, ["voyage", "create", "Plain Voyage"],
            catch_exceptions=False,
        )
        nautical_runner.invoke(
            main, ["create", "expedition", "Phase 1", "--priority", "high"],
            catch_exceptions=False,
        )
        nautical_runner.invoke(
            main, ["voyage", "add", "VOY-001", "EXP-001"],
            catch_exceptions=False,
        )
        result = nautical_runner.invoke(
            main, ["voyage", "show", "VOY-001"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert "Research Interlinks" not in result.output
