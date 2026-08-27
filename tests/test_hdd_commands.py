"""Tests for HDD (Hypothesis-Driven Development) CLI commands and TemplateEngine."""

import subprocess
import tempfile
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from yurtle_kanban.cli import main
from yurtle_kanban.config import KanbanConfig, PathConfig
from yurtle_kanban.models import WorkItemStatus, WorkItemType
from yurtle_kanban.service import KanbanService
from yurtle_kanban.template_engine import TemplateEngine


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_repo(tmp_path):
    """Create a minimal git repo for HDD testing."""
    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path,
        capture_output=True,
        check=True,
    )
    # HDD directory structure
    (tmp_path / ".kanban").mkdir()
    (tmp_path / "research" / "ideas").mkdir(parents=True)
    (tmp_path / "research" / "literature").mkdir(parents=True)
    (tmp_path / "research" / "papers").mkdir(parents=True)
    (tmp_path / "research" / "hypotheses").mkdir(parents=True)
    (tmp_path / "research" / "experiments").mkdir(parents=True)
    (tmp_path / "research" / "measures").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def hdd_config(temp_repo):
    """Config with HDD theme."""
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
    config.save(temp_repo / ".kanban" / "config.yaml")
    return config


@pytest.fixture
def hdd_service(temp_repo, hdd_config):
    """KanbanService with HDD config."""
    return KanbanService(hdd_config, temp_repo)


@pytest.fixture
def templates_dir():
    """Get the real templates directory from the package."""
    pkg_dir = Path(__file__).parent.parent / "templates"
    if pkg_dir.exists():
        return pkg_dir
    pytest.skip("Templates directory not found")


@pytest.fixture
def engine(templates_dir):
    """TemplateEngine pointing to real templates."""
    return TemplateEngine(templates_dir)


@pytest.fixture
def runner(temp_repo, hdd_config, monkeypatch):
    """Click CLI test runner with cwd set to temp_repo.

    Clears the theme cache to avoid cross-test contamination.
    """
    from yurtle_kanban import config as config_mod

    config_mod._theme_cache.clear()
    monkeypatch.chdir(temp_repo)
    return CliRunner()


# ---------------------------------------------------------------------------
# TestTemplateEngine
# ---------------------------------------------------------------------------


class TestTemplateEngine:
    """Tests for TemplateEngine variable substitution."""

    def test_render_idea_template(self, engine):
        """Idea template should substitute id, title, and date."""
        content = engine.render("hdd", "idea", {
            "id": "IDEA-R-001",
            "title": "Test Idea",
            "date": "2026-02-27",
        })
        assert "id: IDEA-R-001" in content
        assert 'title: "Test Idea"' in content
        assert "created: 2026-02-27" in content
        assert "# Test Idea" in content

    def test_render_literature_with_source_idea(self, engine):
        """Literature template should link to source idea in Turtle block."""
        content = engine.render("hdd", "literature", {
            "id": "LIT-001",
            "title": "Survey of Methods",
            "source_idea": "IDEA-R-003",
        })
        assert "id: LIT-001" in content
        assert "lit:explores idea:IDEA-R-003" in content

    def test_render_hypothesis_with_paper(self, engine):
        """Hypothesis template should substitute paper and n."""
        content = engine.render("hdd", "hypothesis", {
            "id": "H130.1",
            "title": "V12 improves accuracy",
            "paper": "130",
            "n": "1",
        })
        assert "id: H130.1" in content
        assert "paper: PAPER-130" in content
        # Body references should be substituted
        assert "EXPR-130" in content  # Experiment Design table

    def test_render_paper_template(self, engine):
        """Paper template should substitute number."""
        content = engine.render("hdd", "paper", {
            "id": "PAPER-130",
            "title": "NuSy Brain Architecture",
            "paper_num": "130",
        })
        assert "id: PAPER-130" in content
        assert 'title: "NuSy Brain Architecture"' in content

    def test_render_measure_template(self, engine):
        """Measure template should substitute unit and category."""
        content = engine.render("hdd", "measure", {
            "id": "M-042",
            "title": "Reasoning Accuracy",
            "unit": "percent",
            "category": "accuracy",
        })
        assert "id: M-042" in content
        assert 'title: "Reasoning Accuracy"' in content

    def test_render_experiment_template(self, engine):
        """Experiment template should substitute paper, hypothesis, and title."""
        content = engine.render("hdd", "experiment", {
            "id": "EXPR-130",
            "title": "V12 accuracy test",
            "paper": "130",
            "n": "1",
            "hypothesis_id": "H130.1",
        })
        assert "id: EXPR-130" in content
        assert "hypothesis: H130.1" in content

    def test_missing_template_raises(self, engine):
        """Non-existent template should raise FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            engine.render("nonexistent", "foo", {"id": "X-1"})

    def test_date_auto_filled(self, engine):
        """Date should be auto-filled with today if not provided."""
        content = engine.render("hdd", "idea", {
            "id": "IDEA-R-001",
            "title": "Test",
        })
        assert "YYYY-MM-DD" not in content
        assert "created:" in content


# ---------------------------------------------------------------------------
# TestWorkItemTypeEnum
# ---------------------------------------------------------------------------


class TestHDDEnumTypes:
    """Verify HDD types exist in WorkItemType enum."""

    @pytest.mark.parametrize("type_str", [
        "literature", "paper", "hypothesis", "experiment", "measure",
    ])
    def test_hdd_type_from_string(self, type_str):
        """HDD types should parse from string."""
        result = WorkItemType.from_string(type_str)
        assert result.value == type_str

    def test_existing_types_unchanged(self):
        """Existing software/nautical types should still work."""
        assert WorkItemType.from_string("feature") == WorkItemType.FEATURE
        assert WorkItemType.from_string("expedition") == WorkItemType.EXPEDITION


# ---------------------------------------------------------------------------
# TestHDDIdeaCreate
# ---------------------------------------------------------------------------


class TestHDDIdeaCreate:
    """Tests for 'yurtle-kanban idea create'."""

    def test_idea_create_research(self, runner, temp_repo, hdd_config):
        """Create a research idea with auto-allocated IDEA-R ID."""
        result = runner.invoke(main, ["idea", "create", "Test Idea"], catch_exceptions=False)
        assert result.exit_code == 0, result.output
        assert "IDEA-R-001" in result.output

    def test_idea_create_feature(self, runner, temp_repo, hdd_config):
        """Create a feature idea with IDEA-F prefix."""
        result = runner.invoke(
            main, ["idea", "create", "Dashboard Widget", "--type", "feature"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        assert "IDEA-F-001" in result.output

    def test_idea_create_auto_increments(self, runner, temp_repo, hdd_config):
        """Second idea should get IDEA-R-002."""
        runner.invoke(main, ["idea", "create", "First Idea"], catch_exceptions=False)
        result = runner.invoke(main, ["idea", "create", "Second Idea"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "IDEA-R-002" in result.output

    def test_idea_file_uses_template(self, runner, temp_repo, hdd_config):
        """Created file should contain HDD template sections."""
        runner.invoke(main, ["idea", "create", "Template Test"], catch_exceptions=False)
        # Find the created file
        ideas_dir = temp_repo / "research" / "ideas"
        files = list(ideas_dir.glob("IDEA-R-001*.md"))
        assert len(files) == 1
        content = files[0].read_text()
        assert "## Observation" in content
        assert "## Prior Art Check" in content


# ---------------------------------------------------------------------------
# TestHDDLiteratureCreate
# ---------------------------------------------------------------------------


class TestHDDLiteratureCreate:
    """Tests for 'yurtle-kanban literature create'."""

    def test_literature_create(self, runner, temp_repo, hdd_config):
        """Create a literature review."""
        result = runner.invoke(
            main, ["literature", "create", "Transfer Learning Survey"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert "LIT-001" in result.output

    def test_literature_with_idea_link(self, runner, temp_repo, hdd_config):
        """Literature should link to source idea in Turtle block."""
        runner.invoke(main, ["idea", "create", "Base Idea"], catch_exceptions=False)
        result = runner.invoke(
            main,
            ["literature", "create", "Related Survey", "--idea", "IDEA-R-001"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert "LIT-001" in result.output
        # Check file content — relationship is in Turtle block, not frontmatter
        lit_dir = temp_repo / "research" / "literature"
        files = list(lit_dir.glob("LIT-001*.md"))
        assert len(files) == 1
        content = files[0].read_text()
        assert "lit:explores idea:IDEA-R-001" in content


# ---------------------------------------------------------------------------
# TestHDDPaperCreate
# ---------------------------------------------------------------------------


class TestHDDPaperCreate:
    """Tests for 'yurtle-kanban paper create'."""

    def test_paper_create(self, runner, temp_repo, hdd_config):
        """Create a paper with user-provided number."""
        result = runner.invoke(
            main, ["paper", "create", "130", "NuSy Brain Architecture"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert "PAPER-130" in result.output

    def test_paper_duplicate_rejected(self, runner, temp_repo, hdd_config):
        """Duplicate paper number should be rejected."""
        runner.invoke(
            main, ["paper", "create", "130", "First Paper"],
            catch_exceptions=False,
        )
        result = runner.invoke(
            main, ["paper", "create", "130", "Duplicate Paper"],
            catch_exceptions=False,
        )
        assert result.exit_code != 0
        assert "already exists" in result.output

    def test_paper_file_has_template_sections(self, runner, temp_repo, hdd_config):
        """Paper file should have standard paper sections."""
        runner.invoke(
            main, ["paper", "create", "131", "Test Paper"],
            catch_exceptions=False,
        )
        papers_dir = temp_repo / "research" / "papers"
        files = list(papers_dir.glob("PAPER-131*.md"))
        assert len(files) == 1
        content = files[0].read_text()
        assert "## Abstract" in content
        assert "## Methodology" in content


# ---------------------------------------------------------------------------
# TestHDDHypothesisCreate
# ---------------------------------------------------------------------------


class TestHDDHypothesisCreate:
    """Tests for 'yurtle-kanban hypothesis create'."""

    def test_hypothesis_create_with_auto_id(self, runner, temp_repo, hdd_config):
        """Hypothesis without --id should auto-allocate H{paper}.1."""
        result = runner.invoke(
            main,
            ["hypothesis", "create", "V12 improves accuracy", "--paper", "130"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert "H130.1" in result.output

    def test_hypothesis_create_with_explicit_id(self, runner, temp_repo, hdd_config):
        """Hypothesis with --id should use the provided ID."""
        result = runner.invoke(
            main,
            ["hypothesis", "create", "Better recall", "--paper", "130", "--id", "H130.5"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert "H130.5" in result.output

    def test_hypothesis_auto_increments(self, runner, temp_repo, hdd_config):
        """Second hypothesis for same paper should be H130.2."""
        runner.invoke(
            main,
            ["hypothesis", "create", "First hyp", "--paper", "130"],
            catch_exceptions=False,
        )
        result = runner.invoke(
            main,
            ["hypothesis", "create", "Second hyp", "--paper", "130"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert "H130.2" in result.output

    # INVERTED, not deleted (issue #77). This used to be
    # `test_hypothesis_requires_paper`, asserting that `hypothesis create`
    # without `--paper` FAILS. That was the defect: a new user's first HDD act
    # was refused until they invented a research paper to hang it on. The old
    # assertion is kept in inverted form rather than removed, so the change of
    # contract is legible instead of reading as lost coverage.
    def test_hypothesis_without_paper_succeeds_and_is_unparented(
        self, runner, temp_repo, hdd_config,
    ):
        """No --paper: the hypothesis stands on its own with an H-NNN id."""
        result = runner.invoke(main, ["hypothesis", "create", "No paper given"])
        assert result.exit_code == 0, result.output
        assert "H-001" in result.output

    def test_unparented_hypothesis_file_has_no_stray_placeholders(
        self, runner, temp_repo, hdd_config,
    ):
        """The template is written around H{paper}.{n}; without a paper it must
        still render a clean file rather than shipping literal braces."""
        result = runner.invoke(main, ["hypothesis", "create", "Standalone claim"])
        assert result.exit_code == 0, result.output

        written = list((temp_repo / "research" / "hypotheses").glob("H-001-*.md"))
        assert written, "expected an H-001 file on disk"
        body = written[0].read_text()

        assert "{paper}" not in body
        assert "{n}" not in body
        assert "PAPER-None" not in body
        # The turtle subject must match the frontmatter id, or the file
        # describes a different thing than it claims to be.
        assert "id: H-001" in body
        assert "<#H-001>" in body

    def test_unparented_and_paper_scoped_ids_do_not_collide(
        self, runner, temp_repo, hdd_config,
    ):
        """H-NNN and H{paper}.{n} are separate spaces and must stay separate.

        ⚠ This arm reaches SOURCES 2 AND 3 ONLY (parsed items and filenames).
        It cannot see Source 1, the allocations file, because `create_item`
        only writes that under `--push`. See the sibling test below for the
        arm that covers it — this one PASSED while the invariant was false.
        """
        runner.invoke(main, ["hypothesis", "create", "First standalone"])
        runner.invoke(main, ["hypothesis", "create", "Scoped", "--paper", "130"])
        result = runner.invoke(main, ["hypothesis", "create", "Second standalone"])

        assert result.exit_code == 0, result.output
        # H-002, not H-003: the paper-scoped H130.1 must not advance this counter.
        assert "H-002" in result.output

    def test_paper_scoped_allocations_do_not_advance_the_dashed_counter(
        self, temp_repo, hdd_config,
    ):
        """SOURCE 1 — the arm the test above could not reach.

        `_ID_ALLOCATIONS.json` stamps every `--push`-created `H130.1` as
        `prefix: "H", number: 1`, and Source 1 matched on that PREFIX FIELD
        alone while Sources 2 and 3 filtered on `prefix + "-"`. So in any repo
        that had done paper-scoped work with `--push`, the first unparented
        hypothesis came out `H-004`, not `H-001` — contradicting the docstring,
        the `--help` text and the README example all at once.

        Written at the allocator rather than through `--push` deliberately: the
        defect is in the read, `--push` merely produces the file, and driving
        real git from a unit test would buy nothing but flakiness.
        """
        import json

        alloc = temp_repo / ".kanban" / "_ID_ALLOCATIONS.json"
        alloc.parent.mkdir(parents=True, exist_ok=True)
        alloc.write_text(json.dumps([
            {"id": "H130.1", "prefix": "H", "number": 1},
            {"id": "H130.2", "prefix": "H", "number": 2},
            {"id": "H130.3", "prefix": "H", "number": 3},
        ]))

        service = KanbanService(hdd_config, temp_repo)
        assert service.get_next_unparented_hypothesis_id() == "H-001"

    def test_dashed_allocations_still_advance_the_dashed_counter(
        self, temp_repo, hdd_config,
    ):
        """NON-VACUITY CONTROL for the arm above.

        Filtering Source 1 must NARROW what counts, not switch it off — the
        whole reason Source 1 exists is to remember ids allocated but not yet
        on disk. Without this arm, deleting the `max_num` update entirely would
        leave the test above green.
        """
        import json

        alloc = temp_repo / ".kanban" / "_ID_ALLOCATIONS.json"
        alloc.parent.mkdir(parents=True, exist_ok=True)
        alloc.write_text(json.dumps([
            {"id": "H-007", "prefix": "H", "number": 7},
        ]))

        service = KanbanService(hdd_config, temp_repo)
        assert service.get_next_unparented_hypothesis_id() == "H-008"

    def test_legacy_allocation_record_without_an_id_still_counts(
        self, temp_repo, hdd_config,
    ):
        """The filter must fail toward SKIPPING an id, never toward reusing one.

        A record predating the `id` field cannot be classified, so it keeps
        counting. That can only over-count, and over-counting skips an id —
        whereas under-counting would mint a duplicate over live work.
        """
        import json

        alloc = temp_repo / ".kanban" / "_ID_ALLOCATIONS.json"
        alloc.parent.mkdir(parents=True, exist_ok=True)
        alloc.write_text(json.dumps([
            {"prefix": "H", "number": 5},
        ]))

        service = KanbanService(hdd_config, temp_repo)
        assert service.get_next_unparented_hypothesis_id() == "H-006"

    def test_paper_scoped_creation_is_unchanged(self, runner, temp_repo, hdd_config):
        """Regression guard on the half that was already working."""
        first = runner.invoke(main, ["hypothesis", "create", "One", "--paper", "130"])
        second = runner.invoke(main, ["hypothesis", "create", "Two", "--paper", "130"])
        assert "H130.1" in first.output
        assert "H130.2" in second.output

    def test_dotted_explicit_id_derives_its_own_paper(
        self, runner, temp_repo, hdd_config,
    ):
        """`--id H130.1` names paper 130; making --paper optional must not orphan it.

        Click used to make this state unreachable by requiring --paper. Once it
        became optional, `--id H130.1` with no --paper produced a hypothesis
        whose id ASSERTS membership of paper 130 while `paper:` was blank and
        no inverse reference was written — which `hdd validate` then correctly
        flagged. The id already carries the answer, so read it.
        """
        _make_paper_file(temp_repo / "research" / "papers", "130")
        result = runner.invoke(
            main, ["hypothesis", "create", "Scoped by id alone", "--id", "H130.9"],
        )
        assert result.exit_code == 0, result.output

        written = list((temp_repo / "research" / "hypotheses").glob("H130.9-*.md"))
        assert written
        assert "paper: PAPER-130" in written[0].read_text()

    def test_undotted_explicit_id_stays_unparented(
        self, runner, temp_repo, hdd_config,
    ):
        """NON-VACUITY CONTROL: the derivation must key on the DOT.

        Without this, "derive a paper from any explicit id" would pass the arm
        above while inventing papers for dashed ids too.
        """
        result = runner.invoke(
            main, ["hypothesis", "create", "Explicit dashed", "--id", "H-042"],
        )
        assert result.exit_code == 0, result.output

        written = list((temp_repo / "research" / "hypotheses").glob("H-042-*.md"))
        assert written
        body = written[0].read_text()
        assert "PAPER-" not in body, body[:400]

    def test_negative_paper_is_refused(self, runner, temp_repo, hdd_config):
        """`--paper=-1` renders `H-1.1`, which lands in the DASHED space.

        A literal counterexample to the "dot, never a dash" separation the two
        allocators rely on. IntRange(min=0) refuses it; `--paper 0` is still
        legitimate and is covered below.
        """
        result = runner.invoke(
            main, ["hypothesis", "create", "Negative", "--paper", "-1"],
        )
        assert result.exit_code != 0

    def test_paper_zero_is_still_accepted(self, runner, temp_repo, hdd_config):
        """NON-VACUITY CONTROL: the bound is min=0, not truthiness.

        Guards against "fix" the negative case by testing `if paper_num:`,
        which would silently orphan every `--paper 0` hypothesis.
        """
        result = runner.invoke(
            main, ["hypothesis", "create", "Zero", "--paper", "0"],
        )
        assert result.exit_code == 0, result.output
        assert "H0.1" in result.output

    def test_unparented_hypothesis_blanks_the_paper_field(
        self, runner, temp_repo, hdd_config,
    ):
        """Pins the `paper:` blanking re.sub in the no-paper branch.

        Removing it leaves the `PAPER-XXX` scaffold, so an ABSENT paper reads
        as an UNFILLED one. Added because a reviewer's mutation of exactly this
        line survived the whole suite.
        """
        result = runner.invoke(main, ["hypothesis", "create", "No paper"])
        assert result.exit_code == 0, result.output

        written = list((temp_repo / "research" / "hypotheses").glob("H-001-*.md"))
        assert written
        body = written[0].read_text()
        assert "PAPER-XXX" not in body, body[:400]
        assert "PAPER-{paper}" not in body

    def test_unparented_experiment_blanks_the_hypothesis_field(
        self, runner, temp_repo, hdd_config,
    ):
        """Pins the `hypothesis:` scaffold blanking in the experiment branch.

        Same reason as the arm above: the reviewer's mutation of this line
        survived. An experiment attached to nothing must not ship a frontmatter
        `hypothesis: H-XXX` that looks like a real, missing item.
        """
        result = runner.invoke(
            main, ["experiment", "create", "--title", "Standalone"],
        )
        assert result.exit_code == 0, result.output

        written = list((temp_repo / "research" / "experiments").glob("EXPR-001-*.md"))
        assert written
        body = written[0].read_text()
        assert "hypothesis: H-XXX" not in body, body[:400]
        assert "hypothesis: H{paper}.{n}" not in body

    def test_measure_template_is_not_touched_by_the_no_paper_branch(
        self, runner, temp_repo, hdd_config,
    ):
        """The no-paper substitution must not reach OTHER item types.

        Caught during this change, not predicted: render() is the shared path
        for every HDD type, and `measure.md` also contains `H{paper}.{n}` in an
        example table. Guarding the branch on `"id" in variables` alone fired
        for `measure create` — which passes an id and no paper — and rewrote
        that hypothesis placeholder to the MEASURE's own id, so the example row
        claimed the measure was its own hypothesis.

        Scoping the branch on item_type fixed it. This pins that: the measure
        template's scaffold must survive untouched.
        """
        result = runner.invoke(
            main,
            ["measure", "create", "Ticket concentration",
             "--unit", "percent", "--category", "quality"],
        )
        assert result.exit_code == 0, result.output

        written = list((temp_repo / "research" / "measures").glob("M-001-*.md"))
        assert written, "expected an M-001 file on disk"
        body = written[0].read_text()

        # The measure's own id must NOT appear in the hypothesis column of the
        # example cross-reference table.
        assert "| EXPR-XXX | M-001 |" not in body

    def test_hypothesis_duplicate_rejected(self, runner, temp_repo, hdd_config):
        """Duplicate hypothesis ID should be rejected."""
        runner.invoke(
            main,
            ["hypothesis", "create", "First", "--paper", "130", "--id", "H130.1"],
            catch_exceptions=False,
        )
        result = runner.invoke(
            main,
            ["hypothesis", "create", "Duplicate", "--paper", "130", "--id", "H130.1"],
            catch_exceptions=False,
        )
        assert result.exit_code != 0
        assert "already exists" in result.output


# ---------------------------------------------------------------------------
# TestHDDHypothesisTurtleBlock
# ---------------------------------------------------------------------------


class TestHDDHypothesisTurtleBlock:
    """CLI integration tests for hypothesis Turtle block generation."""

    def test_hypothesis_with_measures(self, runner, temp_repo, hdd_config):
        """Hypothesis with --measures should include hyp:measuredBy in Turtle block."""
        result = runner.invoke(
            main,
            [
                "hypothesis", "create", "Accuracy improves",
                "--paper", "130",
                "--measures", "M-007,M-025",
            ],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        hyp_dir = temp_repo / "research" / "hypotheses"
        files = list(hyp_dir.glob("H130.1*.md"))
        assert len(files) == 1
        content = files[0].read_text()
        assert "hyp:measuredBy measure:M-007, measure:M-025" in content

    def test_hypothesis_with_literature(self, runner, temp_repo, hdd_config):
        """Hypothesis with --literature should include hyp:informedBy in Turtle block."""
        result = runner.invoke(
            main,
            [
                "hypothesis", "create", "Better recall",
                "--paper", "130",
                "--literature", "LIT-001,LIT-003",
            ],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        hyp_dir = temp_repo / "research" / "hypotheses"
        files = list(hyp_dir.glob("H130.1*.md"))
        assert len(files) == 1
        content = files[0].read_text()
        assert "hyp:informedBy lit:LIT-001, lit:LIT-003" in content


# ---------------------------------------------------------------------------
# TestHDDExperimentCreate
# ---------------------------------------------------------------------------


class TestHDDExperimentCreate:
    """Tests for 'yurtle-kanban experiment create'."""

    def test_experiment_create(self, runner, temp_repo, hdd_config):
        """Create an experiment."""
        result = runner.invoke(
            main,
            [
                "experiment", "create", "EXPR-130",
                "--hypothesis", "H130.1",
                "--title", "V12 accuracy test",
            ],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert "EXPR-130" in result.output

    def test_experiment_auto_prefixes(self, runner, temp_repo, hdd_config):
        """Experiment ID without EXPR- prefix should be normalized."""
        result = runner.invoke(
            main,
            [
                "experiment", "create", "130",
                "--hypothesis", "H130.1",
                "--title", "Test experiment",
            ],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert "EXPR-130" in result.output

    # INVERTED, not deleted — same reason as the hypothesis one above. A paper
    # is optional across the HDD family, and an experiment reaches a paper only
    # THROUGH a hypothesis, so requiring one made the paper mandatory in
    # disguise.
    def test_experiment_without_hypothesis_succeeds(self, runner, temp_repo, hdd_config):
        """No --hypothesis: the experiment stands on its own."""
        result = runner.invoke(
            main, ["experiment", "create", "EXPR-130", "--title", "No hyp"],
        )
        assert result.exit_code == 0, result.output
        assert "EXPR-130" in result.output

    def test_experiment_id_is_optional_and_auto_allocated(
        self, runner, temp_repo, hdd_config,
    ):
        """Omitting EXPR_ID allocates the next id, like every other type.

        Requiring the user to invent `EXPR-130` is the same produce-the-artifact-
        first problem `--paper` was on hypotheses.
        """
        result = runner.invoke(main, ["experiment", "create", "--title", "Standalone probe"])
        assert result.exit_code == 0, result.output
        assert "EXPR-001" in result.output

    def test_unparented_experiment_file_is_clean(self, runner, temp_repo, hdd_config):
        """No paper and no hypothesis: still a clean file, no literal braces."""
        result = runner.invoke(main, ["experiment", "create", "--title", "Standalone"])
        assert result.exit_code == 0, result.output

        written = list((temp_repo / "research" / "experiments").glob("EXPR-001-*.md"))
        assert written, "expected an EXPR-001 file on disk"
        body = written[0].read_text()

        assert "{paper}" not in body
        assert "{n}" not in body
        assert "id: EXPR-001" in body
        assert "<#EXPR-001>" in body
        # The hypothesis placeholder must NOT become the experiment's own id —
        # they are different things, and an experiment attached to itself is
        # worse than one visibly attached to nothing.
        assert "hypothesis: EXPR-001" not in body

    def test_experiment_paper_comes_from_the_hypothesis_not_its_own_id(
        self, runner, temp_repo, hdd_config,
    ):
        """Regression: the paper used to be derived from the EXPERIMENT'S id.

        `paper_num = expr_id.replace("EXPR-", "")` meant EXPR-001 produced
        `paper: PAPER-001` no matter which paper the hypothesis belonged to.
        """
        result = runner.invoke(
            main,
            ["experiment", "create", "EXPR-007", "--hypothesis", "H130.1",
             "--title", "Paper comes from the hypothesis"],
        )
        assert result.exit_code == 0, result.output

        written = list((temp_repo / "research" / "experiments").glob("EXPR-007-*.md"))
        assert written
        body = written[0].read_text()
        assert "paper: PAPER-130" in body, body[:400]
        assert "PAPER-007" not in body
        # ⚠ AND the run path must still name the EXPERIMENT. Fixing `paper:`
        # broke this, and the original version of this test built the very file
        # carrying the bug while asserting only on PAPER-007 — it walked past
        # its own evidence. See the dedicated arm below for why.
        assert "research/runs/EXPR-007/" in body, body[-400:]

    def test_experiment_run_path_names_the_experiment_not_the_paper(
        self, runner, temp_repo, hdd_config,
    ):
        """`EXPR-{paper}` in experiment.md is the EXPERIMENT'S OWN ID.

        The template spells the Data Location run path `EXPR-{paper}`, and on
        main that was right only BY ACCIDENT: the paper was derived from the
        experiment's own id, so the two strings coincided. Deriving the paper
        from the hypothesis — the fix one commit earlier — made the generated
        doc point at `research/runs/EXPR-130/` while `experiment run` writes to
        `research/runs/EXPR-001/`.

        Asserted against the id the service actually uses, so this cannot drift
        back into agreeing with itself.
        """
        result = runner.invoke(
            main,
            ["experiment", "create", "--hypothesis", "H130.1",
             "--title", "Auto id under a scoped hypothesis"],
        )
        assert result.exit_code == 0, result.output

        written = list((temp_repo / "research" / "experiments").glob("EXPR-001-*.md"))
        assert written, list((temp_repo / "research" / "experiments").iterdir())
        body = written[0].read_text()
        assert "research/runs/EXPR-001/" in body, body[-400:]
        assert "research/runs/EXPR-130/" not in body

    def test_malformed_hypothesis_id_does_not_mint_a_paper(
        self, runner, temp_repo, hdd_config,
    ):
        """A hypothesis id we cannot parse means we do not know the paper.

        `lstrip("H")` is a character SET, not a prefix: it turned `HH130.1`
        into a plausible `PAPER-130`, and left `h130.1` as `PAPER-h130` — an
        unresolvable id written into the RDF as `expr:paper paper:PAPER-h130`.
        Inventing a wrong paper is worse than recording none.
        """
        for bad in ("h130.1", "HH130.1", "X130.1", "H13O.1"):
            result = runner.invoke(
                main,
                ["experiment", "create", "--hypothesis", bad, "--title", f"E {bad}"],
            )
            assert result.exit_code == 0, result.output
            written = sorted(
                (temp_repo / "research" / "experiments").glob("EXPR-*.md")
            )[-1].read_text()
            assert "PAPER-h130" not in written, bad
            assert "PAPER-130" not in written, bad
            assert "PAPER-13O" not in written, bad

    def test_experiment_requires_title(self, runner, temp_repo, hdd_config):
        """Experiment without --title should fail."""
        result = runner.invoke(
            main,
            ["experiment", "create", "EXPR-130", "--hypothesis", "H130.1"],
        )
        assert result.exit_code != 0

    def test_experiment_with_measures(self, runner, temp_repo, hdd_config):
        """Experiment with --measures should include expr:measure in Turtle block."""
        result = runner.invoke(
            main,
            [
                "experiment", "create", "EXPR-130",
                "--hypothesis", "H130.1",
                "--title", "Accuracy test",
                "--measures", "M-007,M-025",
            ],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        exp_dir = temp_repo / "research" / "experiments"
        files = list(exp_dir.glob("EXPR-130*.md"))
        assert len(files) == 1
        content = files[0].read_text()
        assert "expr:measure measure:M-007, measure:M-025" in content

    def test_experiment_file_links_hypothesis(self, runner, temp_repo, hdd_config):
        """Experiment file should reference the hypothesis."""
        runner.invoke(
            main,
            [
                "experiment", "create", "EXPR-130",
                "--hypothesis", "H130.1",
                "--title", "Linking test",
            ],
            catch_exceptions=False,
        )
        exp_dir = temp_repo / "research" / "experiments"
        files = list(exp_dir.glob("EXPR-130*.md"))
        assert len(files) == 1
        content = files[0].read_text()
        assert "hypothesis: H130.1" in content


# ---------------------------------------------------------------------------
# TestHDDMeasureCreate
# ---------------------------------------------------------------------------


class TestHDDMeasureCreate:
    """Tests for 'yurtle-kanban measure create'."""

    def test_measure_create_auto_id(self, runner, temp_repo, hdd_config):
        """Measure without --id should auto-allocate M-001."""
        result = runner.invoke(
            main,
            ["measure", "create", "Reasoning Accuracy", "--unit", "percent", "--category", "accuracy"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert "M-001" in result.output

    def test_measure_create_explicit_id(self, runner, temp_repo, hdd_config):
        """Measure with --id should use provided ID."""
        result = runner.invoke(
            main,
            [
                "measure", "create", "Latency",
                "--unit", "ms", "--category", "performance",
                "--id", "M-042",
            ],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert "M-042" in result.output

    def test_measure_auto_increments(self, runner, temp_repo, hdd_config):
        """Second measure should get M-002."""
        runner.invoke(
            main,
            ["measure", "create", "First", "--unit", "count", "--category", "test"],
            catch_exceptions=False,
        )
        result = runner.invoke(
            main,
            ["measure", "create", "Second", "--unit", "count", "--category", "test"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert "M-002" in result.output

    def test_measure_requires_unit(self, runner, temp_repo, hdd_config):
        """Measure without --unit should fail."""
        result = runner.invoke(
            main,
            ["measure", "create", "No Unit", "--category", "test"],
        )
        assert result.exit_code != 0

    def test_measure_requires_category(self, runner, temp_repo, hdd_config):
        """Measure without --category should fail."""
        result = runner.invoke(
            main,
            ["measure", "create", "No Category", "--unit", "count"],
        )
        assert result.exit_code != 0

    def test_measure_duplicate_rejected(self, runner, temp_repo, hdd_config):
        """Duplicate measure ID should be rejected."""
        runner.invoke(
            main,
            [
                "measure", "create", "First",
                "--unit", "count", "--category", "test",
                "--id", "M-042",
            ],
            catch_exceptions=False,
        )
        result = runner.invoke(
            main,
            [
                "measure", "create", "Duplicate",
                "--unit", "count", "--category", "test",
                "--id", "M-042",
            ],
            catch_exceptions=False,
        )
        assert result.exit_code != 0
        assert "already exists" in result.output


# ---------------------------------------------------------------------------
# TestMultiSegmentPrefix
# ---------------------------------------------------------------------------


class TestMultiSegmentPrefix:
    """Regression test: IDEA-R-003 → next should be IDEA-R-004."""

    def test_idea_r_prefix_increment(self, temp_repo, hdd_config):
        """_get_next_id_number should handle IDEA-R multi-segment prefix."""
        service = KanbanService(hdd_config, temp_repo)

        # Create a fake IDEA-R-003 file
        ideas_dir = temp_repo / "research" / "ideas"
        fake_file = ideas_dir / "IDEA-R-003-Some-Idea.md"
        fake_file.write_text(
            "---\nid: IDEA-R-003\ntitle: \"Test\"\ntype: idea\nstatus: draft\n"
            "created: 2026-01-01\npriority: medium\ntags: []\n---\n# Test\n"
        )

        # Re-scan
        service.scan()
        next_num = service._get_next_id_number("IDEA-R")
        assert next_num == 4, f"Expected 4, got {next_num}"

    def test_idea_f_prefix_independent(self, temp_repo, hdd_config):
        """IDEA-F numbering should be independent of IDEA-R."""
        service = KanbanService(hdd_config, temp_repo)

        # Create an IDEA-R file
        ideas_dir = temp_repo / "research" / "ideas"
        (ideas_dir / "IDEA-R-005-Research.md").write_text(
            "---\nid: IDEA-R-005\ntitle: \"R\"\ntype: idea\nstatus: draft\n"
            "created: 2026-01-01\npriority: medium\ntags: []\n---\n# R\n"
        )
        # Create an IDEA-F file
        (ideas_dir / "IDEA-F-002-Feature.md").write_text(
            "---\nid: IDEA-F-002\ntitle: \"F\"\ntype: idea\nstatus: draft\n"
            "created: 2026-01-01\npriority: medium\ntags: []\n---\n# F\n"
        )

        service.scan()
        assert service._get_next_id_number("IDEA-R") == 6
        assert service._get_next_id_number("IDEA-F") == 3


# ---------------------------------------------------------------------------
# TestHypothesisNumbering
# ---------------------------------------------------------------------------


class TestHypothesisNumbering:
    """Tests for get_next_hypothesis_number()."""

    def test_first_hypothesis_for_paper(self, temp_repo, hdd_config):
        """First hypothesis for a paper should be 1."""
        service = KanbanService(hdd_config, temp_repo)
        assert service.get_next_hypothesis_number("130") == 1

    def test_auto_increment_hypothesis(self, temp_repo, hdd_config):
        """Hypothesis numbering should increment based on existing items."""
        service = KanbanService(hdd_config, temp_repo)

        # Create H130.1 and H130.2
        hyp_dir = temp_repo / "research" / "hypotheses"
        for n in (1, 2):
            (hyp_dir / f"H130.{n}-test.md").write_text(
                f"---\nid: H130.{n}\ntitle: \"Test\"\ntype: hypothesis\nstatus: draft\n"
                f"created: 2026-01-01\npaper: PAPER-130\ntags: []\n---\n# Test\n"
            )

        service.scan()
        assert service.get_next_hypothesis_number("130") == 3

    def test_different_papers_independent(self, temp_repo, hdd_config):
        """Hypothesis numbering should be per-paper."""
        service = KanbanService(hdd_config, temp_repo)

        hyp_dir = temp_repo / "research" / "hypotheses"
        (hyp_dir / "H130.1-test.md").write_text(
            "---\nid: H130.1\ntitle: \"Test\"\ntype: hypothesis\nstatus: draft\n"
            "created: 2026-01-01\npaper: PAPER-130\ntags: []\n---\n# Test\n"
        )
        (hyp_dir / "H131.1-test.md").write_text(
            "---\nid: H131.1\ntitle: \"Test\"\ntype: hypothesis\nstatus: draft\n"
            "created: 2026-01-01\npaper: PAPER-131\ntags: []\n---\n# Test\n"
        )

        service.scan()
        assert service.get_next_hypothesis_number("130") == 2
        assert service.get_next_hypothesis_number("131") == 2
        assert service.get_next_hypothesis_number("132") == 1


# ---------------------------------------------------------------------------
# TestParentAutoUpdate
# ---------------------------------------------------------------------------


def _make_paper_file(papers_dir, paper_num):
    """Create a paper file with a turtle knowledge block."""
    content = f'''---
id: PAPER-{paper_num}
title: "Test Paper {paper_num}"
type: paper
status: draft
created: 2026-01-01
priority: medium
tags: []
---

# PAPER-{paper_num}: Test Paper {paper_num}

```turtle
@prefix paper: <https://nusy.dev/paper/> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

<#PAPER-{paper_num}> a paper:Paper ;
    rdfs:label "Test Paper {paper_num}" .
```

## Abstract

Test paper for parent auto-update.
'''
    path = papers_dir / f"PAPER-{paper_num}-Test-Paper-{paper_num}.md"
    path.write_text(content)
    return path


def _make_hypothesis_file(hyp_dir, hyp_id, paper_num):
    """Create a hypothesis file with a turtle knowledge block."""
    content = f'''---
id: {hyp_id}
title: "Test Hypothesis"
type: hypothesis
status: draft
paper: PAPER-{paper_num}
created: 2026-01-01
priority: medium
tags: []
---

# {hyp_id}: Test Hypothesis

```turtle
@prefix hyp: <https://nusy.dev/hypothesis/> .
@prefix paper: <https://nusy.dev/paper/> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

<#{hyp_id}> a hyp:Hypothesis ;
    rdfs:label "Test Hypothesis" ;
    hyp:paper paper:PAPER-{paper_num} .
```

## Rationale

Test hypothesis for parent auto-update.
'''
    path = hyp_dir / f"{hyp_id}-Test-Hypothesis.md"
    path.write_text(content)
    return path


def _make_idea_file(ideas_dir, idea_id):
    """Create an idea file with a turtle knowledge block."""
    content = f'''---
id: {idea_id}
title: "Test Idea"
type: idea
status: draft
created: 2026-01-01
priority: medium
tags: []
---

# {idea_id}: Test Idea

```turtle
@prefix idea: <https://nusy.dev/idea/> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

<#{idea_id}> a idea:Idea ;
    rdfs:label "Test Idea" .
```

## Observation

Test idea for parent auto-update.
'''
    path = ideas_dir / f"{idea_id}-Test-Idea.md"
    path.write_text(content)
    return path


class TestParentAutoUpdate:
    """Integration tests: creating a child HDD item auto-updates the parent's turtle block."""

    def test_hypothesis_updates_paper(self, runner, temp_repo, hdd_config):
        """Creating a hypothesis should add paper:hasHypothesis to the paper file."""
        paper_path = _make_paper_file(temp_repo / "research" / "papers", "130")

        result = runner.invoke(
            main,
            ["hypothesis", "create", "V12 improves accuracy", "--paper", "130"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        assert "H130.1" in result.output

        # Paper file should now contain the inverse reference
        content = paper_path.read_text()
        assert "hasHypothesis" in content

    def test_second_hypothesis_adds_to_paper(self, runner, temp_repo, hdd_config):
        """Two hypotheses should both be referenced in the paper file."""
        paper_path = _make_paper_file(temp_repo / "research" / "papers", "130")

        runner.invoke(
            main,
            ["hypothesis", "create", "First claim", "--paper", "130"],
            catch_exceptions=False,
        )
        runner.invoke(
            main,
            ["hypothesis", "create", "Second claim", "--paper", "130"],
            catch_exceptions=False,
        )

        content = paper_path.read_text()
        assert "H130.1" in content
        assert "H130.2" in content

    def test_experiment_updates_hypothesis(self, runner, temp_repo, hdd_config):
        """Creating an experiment should add hyp:hasExperiment to the hypothesis file."""
        _make_paper_file(temp_repo / "research" / "papers", "130")
        hyp_path = _make_hypothesis_file(
            temp_repo / "research" / "hypotheses", "H130.1", "130",
        )

        result = runner.invoke(
            main,
            [
                "experiment", "create", "EXPR-130",
                "--hypothesis", "H130.1",
                "--title", "V12 accuracy test",
            ],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output

        # Hypothesis file should contain the inverse reference
        content = hyp_path.read_text()
        assert "hasExperiment" in content

    def test_literature_updates_idea(self, runner, temp_repo, hdd_config):
        """Creating literature with --idea should add idea:hasLiterature to the idea file."""
        idea_path = _make_idea_file(temp_repo / "research" / "ideas", "IDEA-R-001")

        result = runner.invoke(
            main,
            ["literature", "create", "Transfer Learning Survey", "--idea", "IDEA-R-001"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output

        # Idea file should contain the inverse reference
        content = idea_path.read_text()
        assert "hasLiterature" in content

    def test_literature_without_idea_no_update(self, runner, temp_repo, hdd_config):
        """Creating literature without --idea should not attempt parent update."""
        result = runner.invoke(
            main,
            ["literature", "create", "Standalone Survey"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        assert "Updated" not in result.output

    def test_missing_parent_child_still_created(self, runner, temp_repo, hdd_config):
        """If the parent file doesn't exist, child should still be created successfully."""
        # No paper file exists — hypothesis should still be created
        result = runner.invoke(
            main,
            ["hypothesis", "create", "Orphan hypothesis", "--paper", "999"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        assert "H999.1" in result.output

        # Verify child file was created
        hyp_dir = temp_repo / "research" / "hypotheses"
        files = list(hyp_dir.glob("H999.1*.md"))
        assert len(files) == 1


# ---------------------------------------------------------------------------
# TestExperimentRun
# ---------------------------------------------------------------------------


class TestExperimentRun:
    """Tests for 'yurtle-kanban experiment run'."""

    def test_experiment_run_creates_folder(self, runner, temp_repo, hdd_config):
        """experiment run should create a timestamped folder with config.yaml."""
        result = runner.invoke(
            main,
            ["experiment", "run", "EXPR-130", "--being", "santiago-toddler-v12.4"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        assert "Created run for EXPR-130" in result.output

        # Verify folder structure
        runs_dir = temp_repo / "research" / "runs" / "EXPR-130"
        assert runs_dir.exists()
        run_folders = list(runs_dir.iterdir())
        assert len(run_folders) == 1
        config_path = run_folders[0] / "config.yaml"
        assert config_path.exists()

    def test_experiment_run_config_contents(self, runner, temp_repo, hdd_config):
        """config.yaml should contain experiment, being, and status fields."""
        runner.invoke(
            main,
            ["experiment", "run", "EXPR-130", "--being", "test-being-v12"],
            catch_exceptions=False,
        )

        runs_dir = temp_repo / "research" / "runs" / "EXPR-130"
        run_folders = list(runs_dir.iterdir())
        config_path = run_folders[0] / "config.yaml"
        config = yaml.safe_load(config_path.read_text())

        assert config["experiment"] == "EXPR-130"
        assert config["being"] == "test-being-v12"
        assert config["status"] == "running"
        assert "created" in config

    def test_experiment_run_with_params(self, runner, temp_repo, hdd_config):
        """--params should be parsed into config.yaml params dict."""
        runner.invoke(
            main,
            [
                "experiment", "run", "EXPR-130",
                "--being", "test-being",
                "--params", "kbdd_rounds=3,wikidata=true",
            ],
            catch_exceptions=False,
        )

        runs_dir = temp_repo / "research" / "runs" / "EXPR-130"
        run_folders = list(runs_dir.iterdir())
        config = yaml.safe_load((run_folders[0] / "config.yaml").read_text())

        assert config["params"]["kbdd_rounds"] == "3"
        assert config["params"]["wikidata"] == "true"

    def test_experiment_run_auto_prefixes(self, runner, temp_repo, hdd_config):
        """ID without EXPR- prefix should be normalized."""
        result = runner.invoke(
            main,
            ["experiment", "run", "130", "--being", "test-being"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert "EXPR-130" in result.output

    def test_experiment_run_with_run_by(self, runner, temp_repo, hdd_config):
        """--run-by should be stored in config.yaml."""
        runner.invoke(
            main,
            [
                "experiment", "run", "EXPR-130",
                "--being", "test-being",
                "--run-by", "Mini",
            ],
            catch_exceptions=False,
        )

        runs_dir = temp_repo / "research" / "runs" / "EXPR-130"
        run_folders = list(runs_dir.iterdir())
        config = yaml.safe_load((run_folders[0] / "config.yaml").read_text())
        assert config["run_by"] == "Mini"


# ---------------------------------------------------------------------------
# TestExperimentStatus
# ---------------------------------------------------------------------------


class TestExperimentStatus:
    """Tests for 'yurtle-kanban experiment status'."""

    def test_status_no_runs(self, runner, temp_repo, hdd_config):
        """Status with no runs should show informative message."""
        result = runner.invoke(
            main,
            ["experiment", "status", "EXPR-130"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert "No runs found" in result.output

    def test_status_shows_runs(self, runner, temp_repo, hdd_config):
        """Status should show a table of existing runs."""
        # Create two runs
        runner.invoke(
            main,
            ["experiment", "run", "EXPR-130", "--being", "being-v1"],
            catch_exceptions=False,
        )
        runner.invoke(
            main,
            ["experiment", "run", "EXPR-130", "--being", "being-v2"],
            catch_exceptions=False,
        )

        result = runner.invoke(
            main,
            ["experiment", "status", "EXPR-130"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert "being-v1" in result.output
        assert "being-v2" in result.output

    def test_status_json_output(self, runner, temp_repo, hdd_config):
        """--json should output valid JSON."""
        import json

        runner.invoke(
            main,
            ["experiment", "run", "EXPR-130", "--being", "test-being"],
            catch_exceptions=False,
        )

        result = runner.invoke(
            main,
            ["experiment", "status", "EXPR-130", "--json"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) == 1
        assert data[0]["being"] == "test-being"
        assert data[0]["status"] == "running"

    def test_status_auto_prefixes(self, runner, temp_repo, hdd_config):
        """ID without EXPR- prefix should be normalized."""
        runner.invoke(
            main,
            ["experiment", "run", "EXPR-130", "--being", "test-being"],
            catch_exceptions=False,
        )
        result = runner.invoke(
            main,
            ["experiment", "status", "130"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert "test-being" in result.output


# ---------------------------------------------------------------------------
# TestExperimentRunService
# ---------------------------------------------------------------------------


class TestExperimentRunService:
    """Tests for KanbanService experiment run methods."""

    def test_create_experiment_run(self, temp_repo, hdd_config):
        """create_experiment_run should create folder and config.yaml."""
        service = KanbanService(hdd_config, temp_repo)
        run_path = service.create_experiment_run(
            expr_id="EXPR-130",
            being="test-being-v12",
            params={"rounds": "3"},
            run_by="TestAgent",
        )

        assert run_path.exists()
        config = yaml.safe_load((run_path / "config.yaml").read_text())
        assert config["experiment"] == "EXPR-130"
        assert config["being"] == "test-being-v12"
        assert config["run_by"] == "TestAgent"
        assert config["params"]["rounds"] == "3"
        assert config["status"] == "running"

    def test_get_experiment_runs_empty(self, temp_repo, hdd_config):
        """get_experiment_runs should return empty list for non-existent experiment."""
        service = KanbanService(hdd_config, temp_repo)
        runs = service.get_experiment_runs("EXPR-999")
        assert runs == []

    def test_get_experiment_runs_returns_metadata(self, temp_repo, hdd_config):
        """get_experiment_runs should return run metadata."""
        service = KanbanService(hdd_config, temp_repo)
        service.create_experiment_run(
            expr_id="EXPR-130",
            being="test-being",
            run_by="Agent",
        )

        runs = service.get_experiment_runs("EXPR-130")
        assert len(runs) == 1
        assert runs[0]["being"] == "test-being"
        assert runs[0]["status"] == "running"

    def test_update_run_status(self, temp_repo, hdd_config):
        """update_run_status should modify config.yaml."""
        service = KanbanService(hdd_config, temp_repo)
        run_path = service.create_experiment_run(
            expr_id="EXPR-130",
            being="test-being",
        )

        service.update_run_status(run_path, "complete", outcome="VALIDATED")

        config = yaml.safe_load((run_path / "config.yaml").read_text())
        assert config["status"] == "complete"
        assert config["outcome"] == "VALIDATED"

    def test_update_run_status_missing_folder(self, temp_repo, hdd_config):
        """update_run_status should raise on missing config.yaml."""
        service = KanbanService(hdd_config, temp_repo)
        with pytest.raises(FileNotFoundError):
            service.update_run_status(temp_repo / "nonexistent", "complete")

    def test_create_experiment_run_dotted_id(self, temp_repo, hdd_config):
        """create_experiment_run should accept dotted sub-IDs like EXPR-131.5."""
        service = KanbanService(hdd_config, temp_repo)
        run_path = service.create_experiment_run(
            expr_id="EXPR-131.5",
            being="test-being-v12",
            run_by="TestAgent",
        )
        assert run_path.exists()
        config = yaml.safe_load((run_path / "config.yaml").read_text())
        assert config["experiment"] == "EXPR-131.5"

    def test_get_experiment_runs_dotted_id(self, temp_repo, hdd_config):
        """get_experiment_runs should accept dotted sub-IDs like EXPR-131.5."""
        service = KanbanService(hdd_config, temp_repo)
        service.create_experiment_run(
            expr_id="EXPR-131.5",
            being="test-being",
            run_by="Agent",
        )
        runs = service.get_experiment_runs("EXPR-131.5")
        assert len(runs) == 1

    def test_create_experiment_run_rejects_path_traversal(self, temp_repo, hdd_config):
        """create_experiment_run should reject IDs with path traversal."""
        service = KanbanService(hdd_config, temp_repo)
        with pytest.raises(ValueError, match="Invalid experiment ID format"):
            service.create_experiment_run(
                expr_id="../etc/passwd",
                being="test-being",
            )

    def test_get_experiment_runs_with_metrics(self, temp_repo, hdd_config):
        """Runs with metrics.json should include outcome in metadata."""
        import json

        service = KanbanService(hdd_config, temp_repo)
        run_path = service.create_experiment_run(
            expr_id="EXPR-130",
            being="test-being",
        )

        # Write metrics.json
        metrics = {"outcome": "VALIDATED", "summary": "85.2% accuracy"}
        (run_path / "metrics.json").write_text(json.dumps(metrics))

        runs = service.get_experiment_runs("EXPR-130")
        assert len(runs) == 1
        assert runs[0]["outcome"] == "VALIDATED"
        assert runs[0]["summary"] == "85.2% accuracy"


# ---------------------------------------------------------------------------
# TestHDDRegistry
# ---------------------------------------------------------------------------


def _make_experiment_file(exp_dir, expr_id, hyp_id):
    """Create an experiment file with frontmatter."""
    content = f'''---
id: {expr_id}
title: "Test Experiment"
type: experiment
status: draft
hypothesis: {hyp_id}
created: 2026-01-01
priority: medium
tags: []
---

# {expr_id}: Test Experiment
'''
    path = exp_dir / f"{expr_id}-Test-Experiment.md"
    path.write_text(content)
    return path


def _make_measure_file(measure_dir, measure_id, unit="percent", category="accuracy"):
    """Create a measure file with frontmatter."""
    content = f'''---
id: {measure_id}
title: "Test Measure"
type: measure
status: draft
unit: {unit}
category: {category}
created: 2026-01-01
priority: medium
tags: []
---

# {measure_id}: Test Measure
'''
    path = measure_dir / f"{measure_id}-Test-Measure.md"
    path.write_text(content)
    return path


class TestHDDRegistry:
    """Tests for 'yurtle-kanban hdd registry'."""

    def test_registry_generates_file(self, runner, temp_repo, hdd_config):
        """Registry command should generate REGISTRY.md."""
        result = runner.invoke(
            main,
            ["hdd", "registry"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        assert "Registry written" in result.output

        registry = temp_repo / "research" / "REGISTRY.md"
        assert registry.exists()
        content = registry.read_text()
        assert "# HDD Research Registry" in content

    def test_registry_includes_papers(self, runner, temp_repo, hdd_config):
        """Registry should list papers with hypothesis links."""
        _make_paper_file(temp_repo / "research" / "papers", "130")
        _make_hypothesis_file(
            temp_repo / "research" / "hypotheses", "H130.1", "130",
        )

        result = runner.invoke(
            main,
            ["hdd", "registry"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0

        registry = temp_repo / "research" / "REGISTRY.md"
        content = registry.read_text()
        assert "PAPER-130" in content
        assert "H130.1" in content

    def test_registry_includes_experiments(self, runner, temp_repo, hdd_config):
        """Registry should list experiments with hypothesis links."""
        _make_hypothesis_file(
            temp_repo / "research" / "hypotheses", "H130.1", "130",
        )
        _make_experiment_file(
            temp_repo / "research" / "experiments", "EXPR-130", "H130.1",
        )

        result = runner.invoke(
            main,
            ["hdd", "registry"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0

        registry = temp_repo / "research" / "REGISTRY.md"
        content = registry.read_text()
        assert "EXPR-130" in content

    def test_registry_shows_orphaned(self, runner, temp_repo, hdd_config):
        """Registry should list orphaned items (hypothesis without paper)."""
        # Create hypothesis without a paper file
        hyp_dir = temp_repo / "research" / "hypotheses"
        (hyp_dir / "H999.1-Orphan.md").write_text(
            "---\nid: H999.1\ntitle: \"Orphan\"\ntype: hypothesis\n"
            "status: draft\ncreated: 2026-01-01\ntags: []\n---\n# Orphan\n"
        )

        result = runner.invoke(
            main,
            ["hdd", "registry"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0

        registry = temp_repo / "research" / "REGISTRY.md"
        content = registry.read_text()
        assert "Orphaned" in content
        assert "H999.1" in content

    def test_registry_custom_output(self, runner, temp_repo, hdd_config):
        """Registry should write to custom output path."""
        output = str(temp_repo / "custom_registry.md")
        result = runner.invoke(
            main,
            ["hdd", "registry", "--output", output],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert Path(output).exists()

    def test_registry_empty(self, runner, temp_repo, hdd_config):
        """Registry with no items should still generate valid markdown."""
        result = runner.invoke(
            main,
            ["hdd", "registry"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert "0 items indexed" in result.output


# ---------------------------------------------------------------------------
# TestHDDValidate
# ---------------------------------------------------------------------------


class TestHDDValidate:
    """Tests for 'yurtle-kanban hdd validate'."""

    def test_validate_clean(self, runner, temp_repo, hdd_config):
        """Validate with well-linked items should exit 0."""
        _make_paper_file(temp_repo / "research" / "papers", "130")
        _make_hypothesis_file(
            temp_repo / "research" / "hypotheses", "H130.1", "130",
        )
        _make_experiment_file(
            temp_repo / "research" / "experiments", "EXPR-130", "H130.1",
        )

        result = runner.invoke(
            main,
            ["hdd", "validate"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert "Validation Report" in result.output

    def test_validate_missing_paper(self, runner, temp_repo, hdd_config):
        """Hypothesis referencing non-existent paper should be an error (exit 1)."""
        # H130.1 references PAPER-130 but the paper doesn't exist
        _make_hypothesis_file(
            temp_repo / "research" / "hypotheses", "H130.1", "130",
        )

        result = runner.invoke(
            main,
            ["hdd", "validate"],
        )
        assert result.exit_code == 1  # errors always cause exit 1
        assert "not found" in result.output or "Error" in result.output

    def test_validate_missing_hypothesis(self, runner, temp_repo, hdd_config):
        """Experiment without hypothesis link should generate a warning."""
        exp_dir = temp_repo / "research" / "experiments"
        (exp_dir / "EXPR-999-No-Hyp.md").write_text(
            "---\nid: EXPR-999\ntitle: \"No Hyp\"\ntype: experiment\n"
            "status: draft\ncreated: 2026-01-01\ntags: []\n---\n# No Hyp\n"
        )

        result = runner.invoke(
            main,
            ["hdd", "validate"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert "Warning" in result.output or "warning" in result.output.lower()

    def test_validate_strict_fails_on_warnings(self, runner, temp_repo, hdd_config):
        """--strict should exit 1 when warnings exist.

        RETARGETED, not weakened (issue #77). This used to build an experiment
        with no hypothesis, which is now a legitimate standing state and no
        longer warns — so the test would have passed only by asserting the
        blessed workflow is broken. The warning it needs is still available from
        a real inconsistency: a PAPER-SHAPED id whose paper field is blank
        claims membership of paper 130 while recording none.
        """
        hyp_dir = temp_repo / "research" / "hypotheses"
        (hyp_dir / "H130.9-Blank-Paper.md").write_text(
            "---\nid: H130.9\ntitle: \"Blank Paper\"\ntype: hypothesis\n"
            "status: draft\npaper:\ncreated: 2026-01-01\ntags: []\n---\n"
            "# Blank Paper\n"
        )

        result = runner.invoke(
            main,
            ["hdd", "validate", "--strict"],
        )
        assert result.exit_code != 0

    def test_validate_json_output(self, runner, temp_repo, hdd_config):
        """--json should output valid JSON report."""
        import json

        _make_paper_file(temp_repo / "research" / "papers", "130")
        _make_hypothesis_file(
            temp_repo / "research" / "hypotheses", "H130.1", "130",
        )

        result = runner.invoke(
            main,
            ["hdd", "validate", "--json"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "errors" in data
        assert "warnings" in data
        assert "summary" in data


# ---------------------------------------------------------------------------
# TestHDDCrossReferences (Service)
# ---------------------------------------------------------------------------


class TestHDDCrossReferences:
    """Tests for KanbanService.get_hdd_cross_references()."""

    def test_empty_repo(self, temp_repo, hdd_config):
        """Empty repo should return empty cross-references."""
        service = KanbanService(hdd_config, temp_repo)
        xrefs = service.get_hdd_cross_references()
        assert xrefs["papers"] == []
        assert xrefs["hypotheses"] == []
        assert xrefs["orphaned"] == []

    def test_paper_hypothesis_link(self, temp_repo, hdd_config):
        """Paper should list its hypotheses in cross-references."""
        _make_paper_file(temp_repo / "research" / "papers", "130")
        _make_hypothesis_file(
            temp_repo / "research" / "hypotheses", "H130.1", "130",
        )
        _make_hypothesis_file(
            temp_repo / "research" / "hypotheses", "H130.2", "130",
        )

        service = KanbanService(hdd_config, temp_repo)
        xrefs = service.get_hdd_cross_references()

        papers = xrefs["papers"]
        assert len(papers) == 1
        assert set(papers[0]["hypotheses"]) == {"H130.1", "H130.2"}

    def test_hypothesis_experiment_link(self, temp_repo, hdd_config):
        """Hypothesis should list its experiments."""
        _make_hypothesis_file(
            temp_repo / "research" / "hypotheses", "H130.1", "130",
        )
        _make_experiment_file(
            temp_repo / "research" / "experiments", "EXPR-130", "H130.1",
        )

        service = KanbanService(hdd_config, temp_repo)
        xrefs = service.get_hdd_cross_references()

        hyps = xrefs["hypotheses"]
        assert len(hyps) == 1
        assert "EXPR-130" in hyps[0]["experiments"]

    def test_orphaned_hypothesis_detected(self, temp_repo, hdd_config):
        """Hypothesis without paper field should appear in orphaned."""
        hyp_dir = temp_repo / "research" / "hypotheses"
        (hyp_dir / "H999.1-Orphan.md").write_text(
            "---\nid: H999.1\ntitle: \"Orphan\"\ntype: hypothesis\n"
            "status: draft\ncreated: 2026-01-01\ntags: []\n---\n# Orphan\n"
        )

        service = KanbanService(hdd_config, temp_repo)
        xrefs = service.get_hdd_cross_references()

        orphaned_ids = [o["id"] for o in xrefs["orphaned"]]
        assert "H999.1" in orphaned_ids

    def test_standalone_experiment_is_not_orphaned(self, temp_repo, hdd_config):
        """An experiment with no hypothesis is legitimate, not orphaned.

        INVERTED from test_orphaned_experiment_detected, not deleted — same
        treatment as test_hypothesis_requires_paper and
        test_experiment_requires_hypothesis, and for the same reason: it encoded
        the contract issue #77 changes. Keeping the inversion visible makes the
        change legible rather than reading as lost coverage.

        REGISTRY.md renders `orphaned`, so leaving this as-was meant the tool
        printed a new user's first experiment under "Orphaned Items".
        """
        exp_dir = temp_repo / "research" / "experiments"
        (exp_dir / "EXPR-999-No-Hyp.md").write_text(
            "---\nid: EXPR-999\ntitle: \"No Hyp\"\ntype: experiment\n"
            "status: draft\ncreated: 2026-01-01\ntags: []\n---\n# No Hyp\n"
        )

        service = KanbanService(hdd_config, temp_repo)
        xrefs = service.get_hdd_cross_references()

        orphaned_ids = [o["id"] for o in xrefs["orphaned"]]
        assert "EXPR-999" not in orphaned_ids

    def test_unparented_hypothesis_is_not_orphaned(self, temp_repo, hdd_config):
        """A dashed H-NNN hypothesis is unparented by FORM, not by omission."""
        hyp_dir = temp_repo / "research" / "hypotheses"
        (hyp_dir / "H-001-Standalone.md").write_text(
            "---\nid: H-001\ntitle: \"Standalone\"\ntype: hypothesis\n"
            "status: draft\ncreated: 2026-01-01\ntags: []\n---\n# Standalone\n"
        )

        service = KanbanService(hdd_config, temp_repo)
        xrefs = service.get_hdd_cross_references()

        orphaned_ids = [o["id"] for o in xrefs["orphaned"]]
        assert "H-001" not in orphaned_ids

    def test_paper_shaped_id_with_no_paper_IS_still_orphaned(
        self, temp_repo, hdd_config
    ):
        """The exemption is keyed on the id FORM, so it must not blanket-pass.

        `H130.9` claims membership of paper 130. A blank paper field there is a
        real inconsistency and must still be reported — otherwise
        _is_deliberately_unparented would have silenced the check entirely
        rather than narrowed it.
        """
        hyp_dir = temp_repo / "research" / "hypotheses"
        (hyp_dir / "H130.9-Blank-Paper.md").write_text(
            "---\nid: H130.9\ntitle: \"Blank Paper\"\ntype: hypothesis\n"
            "status: draft\npaper:\ncreated: 2026-01-01\ntags: []\n---\n"
            "# Blank Paper\n"
        )

        service = KanbanService(hdd_config, temp_repo)
        xrefs = service.get_hdd_cross_references()

        orphaned_ids = [o["id"] for o in xrefs["orphaned"]]
        assert "H130.9" in orphaned_ids


# ---------------------------------------------------------------------------
# TestHDDValidation (Service)
# ---------------------------------------------------------------------------


class TestHDDValidation:
    """Tests for KanbanService.validate_hdd_links()."""

    def test_clean_validation(self, temp_repo, hdd_config):
        """Well-linked items should produce 0 errors."""
        _make_paper_file(temp_repo / "research" / "papers", "130")
        _make_hypothesis_file(
            temp_repo / "research" / "hypotheses", "H130.1", "130",
        )
        _make_experiment_file(
            temp_repo / "research" / "experiments", "EXPR-130", "H130.1",
        )

        service = KanbanService(hdd_config, temp_repo)
        report = service.validate_hdd_links()
        assert report["summary"]["errors"] == 0

    def test_broken_paper_reference(self, temp_repo, hdd_config):
        """Hypothesis referencing non-existent paper should produce error."""
        _make_hypothesis_file(
            temp_repo / "research" / "hypotheses", "H130.1", "130",
        )
        # No PAPER-130 file

        service = KanbanService(hdd_config, temp_repo)
        report = service.validate_hdd_links()
        error_ids = [e["id"] for e in report["errors"]]
        assert "H130.1" in error_ids

    def test_missing_hypothesis_link_is_not_a_warning(self, temp_repo, hdd_config):
        """An experiment with no hypothesis must not warn (issue #77).

        INVERTED from test_missing_hypothesis_link_warning. README documents
        `--strict` as "Warnings = errors (for CI)", so leaving this warning in
        place meant a new user following the documented happy path landed a red
        CI gate on their first experiment.
        """
        exp_dir = temp_repo / "research" / "experiments"
        (exp_dir / "EXPR-999-No-Hyp.md").write_text(
            "---\nid: EXPR-999\ntitle: \"No Hyp\"\ntype: experiment\n"
            "status: draft\ncreated: 2026-01-01\ntags: []\n---\n# No Hyp\n"
        )

        service = KanbanService(hdd_config, temp_repo)
        report = service.validate_hdd_links()
        warning_ids = [w["id"] for w in report["warnings"]]
        assert "EXPR-999" not in warning_ids

    def test_dangling_hypothesis_reference_is_still_an_error(
        self, temp_repo, hdd_config
    ):
        """Silencing "no hypothesis" must not silence "hypothesis not found".

        The two shared one if/elif, so dropping the warning branch could easily
        have taken the error branch with it. NAMING a hypothesis that does not
        exist is broken data and stays an error.
        """
        exp_dir = temp_repo / "research" / "experiments"
        (exp_dir / "EXPR-998-Dangling.md").write_text(
            "---\nid: EXPR-998\ntitle: \"Dangling\"\ntype: experiment\n"
            "status: draft\nhypothesis: H999.9\ncreated: 2026-01-01\ntags: []\n"
            "---\n# Dangling\n"
        )

        service = KanbanService(hdd_config, temp_repo)
        report = service.validate_hdd_links()
        error_ids = [e["id"] for e in report["errors"]]
        assert "EXPR-998" in error_ids

    def test_unused_measure_warning(self, temp_repo, hdd_config):
        """Unreferenced measure should produce warning."""
        _make_measure_file(temp_repo / "research" / "measures", "M-042")

        service = KanbanService(hdd_config, temp_repo)
        report = service.validate_hdd_links()
        warning_ids = [w["id"] for w in report["warnings"]]
        assert "M-042" in warning_ids


# ---------------------------------------------------------------------------
# Helpers for critical-path tests
# ---------------------------------------------------------------------------


def _make_expedition_file(exp_dir, exp_id, status="backlog", assignee=None):
    """Create a dev board expedition file (EXP-XXX)."""
    content = f'''---
id: {exp_id}
title: "Dev Work for {exp_id}"
type: expedition
status: {status}
created: 2026-01-01
priority: medium
tags: []
'''
    if assignee:
        content += f"assignee: {assignee}\n"
    content += f'''---

# {exp_id}: Dev Work for {exp_id}
'''
    path = exp_dir / f"{exp_id}-Dev-Work.md"
    path.write_text(content)
    return path


def _make_experiment_with_implements(exp_dir, expr_id, hyp_id, implements, assignee=None, status="draft"):
    """Create an experiment file with implements field linking to dev board."""
    if isinstance(implements, list):
        impl_yaml = "[" + ", ".join(implements) + "]"
    else:
        impl_yaml = implements
    content = f'''---
id: {expr_id}
title: "Experiment {expr_id}"
type: experiment
status: {status}
hypothesis: {hyp_id}
implements: {impl_yaml}
created: 2026-01-01
priority: medium
tags: []
'''
    if assignee:
        content += f"assignee: {assignee}\n"
    content += f'''---

# {expr_id}: Experiment {expr_id}
'''
    path = exp_dir / f"{expr_id}-Experiment.md"
    path.write_text(content)
    return path


@pytest.fixture
def cross_board_repo(tmp_path):
    """Create a repo with both research and dev board directories."""
    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=tmp_path, capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path, capture_output=True, check=True,
    )
    # Research board dirs
    (tmp_path / ".kanban").mkdir()
    (tmp_path / "research" / "ideas").mkdir(parents=True)
    (tmp_path / "research" / "literature").mkdir(parents=True)
    (tmp_path / "research" / "papers").mkdir(parents=True)
    (tmp_path / "research" / "hypotheses").mkdir(parents=True)
    (tmp_path / "research" / "experiments").mkdir(parents=True)
    (tmp_path / "research" / "measures").mkdir(parents=True)
    (tmp_path / "research" / "runs").mkdir(parents=True)
    # Dev board dir
    (tmp_path / "kanban-work" / "expeditions").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def cross_board_config(cross_board_repo):
    """Config scanning both research and dev board directories."""
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
                "kanban-work/expeditions/",
            ],
        ),
    )
    config.save(cross_board_repo / ".kanban" / "config.yaml")
    return config


@pytest.fixture
def cross_board_service(cross_board_repo, cross_board_config):
    """KanbanService scanning both boards."""
    return KanbanService(cross_board_config, cross_board_repo)


@pytest.fixture
def cross_board_runner(cross_board_repo, cross_board_config, monkeypatch):
    """Click CLI runner for cross-board tests."""
    from yurtle_kanban import config as config_mod

    config_mod._theme_cache.clear()
    monkeypatch.chdir(cross_board_repo)
    return CliRunner()


# ---------------------------------------------------------------------------
# TestCriticalPathService
# ---------------------------------------------------------------------------


class TestCriticalPathService:
    """Tests for KanbanService cross-board dependency engine."""

    def test_build_cross_board_graph_basic(self, cross_board_repo, cross_board_config):
        """Graph should link experiment → expedition via implements field."""
        _make_paper_file(cross_board_repo / "research" / "papers", "130")
        _make_hypothesis_file(
            cross_board_repo / "research" / "hypotheses", "H130.1", "130",
        )
        _make_experiment_with_implements(
            cross_board_repo / "research" / "experiments",
            "EXPR-130", "H130.1", "EXP-992",
        )
        _make_expedition_file(
            cross_board_repo / "kanban-work" / "expeditions", "EXP-992", status="done",
        )

        service = KanbanService(cross_board_config, cross_board_repo)
        graph = service.build_cross_board_graph()

        assert graph["summary"]["total_experiments"] >= 1
        expr_node = next(
            e for e in graph["experiments"] if e["experiment_id"] == "EXPR-130"
        )
        assert expr_node["implements"] == ["EXP-992"]
        assert expr_node["implements_status"]["EXP-992"] == "done"
        assert expr_node["readiness"] == "ready_for_training"
        assert "EXPR-130" in graph["ready_for_training"]

    def test_blocked_by_dev(self, cross_board_repo, cross_board_config):
        """Experiment should be blocked when expedition is not done."""
        _make_paper_file(cross_board_repo / "research" / "papers", "127")
        _make_hypothesis_file(
            cross_board_repo / "research" / "hypotheses", "H127.3", "127",
        )
        _make_experiment_with_implements(
            cross_board_repo / "research" / "experiments",
            "EXPR-127", "H127.3", "EXP-991",
        )
        _make_expedition_file(
            cross_board_repo / "kanban-work" / "expeditions", "EXP-991", status="backlog",
        )

        service = KanbanService(cross_board_config, cross_board_repo)
        graph = service.build_cross_board_graph()

        expr_node = next(
            e for e in graph["experiments"] if e["experiment_id"] == "EXPR-127"
        )
        assert expr_node["readiness"] == "blocked_by_dev"
        assert "EXP-991" in graph["dev_blockers"]

    def test_multiple_implements(self, cross_board_repo, cross_board_config):
        """Experiment with multiple implements should be blocked if any is not done."""
        _make_paper_file(cross_board_repo / "research" / "papers", "128")
        _make_hypothesis_file(
            cross_board_repo / "research" / "hypotheses", "H128.1", "128",
        )
        _make_experiment_with_implements(
            cross_board_repo / "research" / "experiments",
            "EXPR-128", "H128.1", ["EXP-878", "EXP-879"],
        )
        _make_expedition_file(
            cross_board_repo / "kanban-work" / "expeditions", "EXP-878", status="done",
        )
        _make_expedition_file(
            cross_board_repo / "kanban-work" / "expeditions", "EXP-879", status="in_progress",
        )

        service = KanbanService(cross_board_config, cross_board_repo)
        graph = service.build_cross_board_graph()

        expr_node = next(
            e for e in graph["experiments"] if e["experiment_id"] == "EXPR-128"
        )
        assert expr_node["readiness"] == "blocked_by_dev"
        assert expr_node["implements_status"]["EXP-878"] == "done"
        assert expr_node["implements_status"]["EXP-879"] == "in_progress"

    def test_all_implements_done_ready_for_training(self, cross_board_repo, cross_board_config):
        """When all implements are done, experiment should be ready_for_training."""
        _make_paper_file(cross_board_repo / "research" / "papers", "128")
        _make_hypothesis_file(
            cross_board_repo / "research" / "hypotheses", "H128.1", "128",
        )
        _make_experiment_with_implements(
            cross_board_repo / "research" / "experiments",
            "EXPR-128", "H128.1", ["EXP-878", "EXP-879"],
        )
        _make_expedition_file(
            cross_board_repo / "kanban-work" / "expeditions", "EXP-878", status="done",
        )
        _make_expedition_file(
            cross_board_repo / "kanban-work" / "expeditions", "EXP-879", status="done",
        )

        service = KanbanService(cross_board_config, cross_board_repo)
        graph = service.build_cross_board_graph()

        expr_node = next(
            e for e in graph["experiments"] if e["experiment_id"] == "EXPR-128"
        )
        assert expr_node["readiness"] == "ready_for_training"

    def test_training_complete_with_run(self, cross_board_repo, cross_board_config):
        """Experiment with completed run should show training_complete."""
        _make_paper_file(cross_board_repo / "research" / "papers", "121")
        _make_hypothesis_file(
            cross_board_repo / "research" / "hypotheses", "H121.1", "121",
        )
        _make_experiment_with_implements(
            cross_board_repo / "research" / "experiments",
            "EXPR-121", "H121.1", "EXP-992",
        )
        _make_expedition_file(
            cross_board_repo / "kanban-work" / "expeditions", "EXP-992", status="done",
        )

        # Create a completed training run
        service = KanbanService(cross_board_config, cross_board_repo)
        run_path = service.create_experiment_run("EXPR-121", "santiago-v12")
        service.update_run_status(run_path, "complete")

        # Re-scan
        service = KanbanService(cross_board_config, cross_board_repo)
        graph = service.build_cross_board_graph()

        expr_node = next(
            e for e in graph["experiments"] if e["experiment_id"] == "EXPR-121"
        )
        assert expr_node["readiness"] == "training_complete"

    def test_no_dev_dependency(self, cross_board_repo, cross_board_config):
        """Experiment without implements should be no_dev_dependency."""
        _make_paper_file(cross_board_repo / "research" / "papers", "130")
        _make_hypothesis_file(
            cross_board_repo / "research" / "hypotheses", "H130.1", "130",
        )
        _make_experiment_file(
            cross_board_repo / "research" / "experiments", "EXPR-130", "H130.1",
        )

        service = KanbanService(cross_board_config, cross_board_repo)
        graph = service.build_cross_board_graph()

        expr_node = next(
            e for e in graph["experiments"] if e["experiment_id"] == "EXPR-130"
        )
        assert expr_node["readiness"] == "no_dev_dependency"

    def test_downstream_impact_scoring(self, cross_board_repo, cross_board_config):
        """Experiments with more downstream items should score higher."""
        _make_paper_file(cross_board_repo / "research" / "papers", "130")
        _make_hypothesis_file(
            cross_board_repo / "research" / "hypotheses", "H130.1", "130",
        )
        # Two experiments sharing the same hypothesis → higher impact
        _make_experiment_with_implements(
            cross_board_repo / "research" / "experiments",
            "EXPR-130A", "H130.1", "EXP-997",
        )
        _make_experiment_with_implements(
            cross_board_repo / "research" / "experiments",
            "EXPR-130B", "H130.1", "EXP-998",
        )
        _make_expedition_file(
            cross_board_repo / "kanban-work" / "expeditions", "EXP-997", status="done",
        )
        _make_expedition_file(
            cross_board_repo / "kanban-work" / "expeditions", "EXP-998", status="done",
        )

        service = KanbanService(cross_board_config, cross_board_repo)
        graph = service.build_cross_board_graph()

        for exp in graph["experiments"]:
            if exp["experiment_id"] in ("EXPR-130A", "EXPR-130B"):
                # Both share H130.1 → 1 sibling experiment (excludes self)
                assert exp["downstream_impact"] >= 1


class TestExperimentReadiness:
    """Tests for KanbanService.get_experiment_readiness()."""

    def test_readiness_blocked(self, cross_board_repo, cross_board_config):
        """Should detect blocked_by_dev when expedition not done."""
        _make_experiment_with_implements(
            cross_board_repo / "research" / "experiments",
            "EXPR-127", "H127.3", "EXP-991",
        )
        _make_expedition_file(
            cross_board_repo / "kanban-work" / "expeditions", "EXP-991", status="backlog",
        )

        service = KanbanService(cross_board_config, cross_board_repo)
        result = service.get_experiment_readiness("EXPR-127")
        assert result["readiness"] == "blocked_by_dev"
        assert result["implements_status"]["EXP-991"] == "backlog"

    def test_readiness_ready(self, cross_board_repo, cross_board_config):
        """Should detect ready_for_training when all expeditions done."""
        _make_experiment_with_implements(
            cross_board_repo / "research" / "experiments",
            "EXPR-121", "H121.1", "EXP-992",
        )
        _make_expedition_file(
            cross_board_repo / "kanban-work" / "expeditions", "EXP-992", status="done",
        )

        service = KanbanService(cross_board_config, cross_board_repo)
        result = service.get_experiment_readiness("EXPR-121")
        assert result["readiness"] == "ready_for_training"

    def test_readiness_not_found(self, cross_board_repo, cross_board_config):
        """Should return error for non-existent experiment."""
        service = KanbanService(cross_board_config, cross_board_repo)
        result = service.get_experiment_readiness("EXPR-999")
        assert result["readiness"] == "unknown"
        assert "error" in result


class TestGetCriticalPath:
    """Tests for KanbanService.get_critical_path()."""

    def test_filter_by_agent(self, cross_board_repo, cross_board_config):
        """Should filter experiments by assignee."""
        _make_experiment_with_implements(
            cross_board_repo / "research" / "experiments",
            "EXPR-A", "H130.1", "EXP-997", assignee="DGX",
        )
        _make_experiment_with_implements(
            cross_board_repo / "research" / "experiments",
            "EXPR-B", "H130.1", "EXP-998", assignee="Mini",
        )
        _make_expedition_file(
            cross_board_repo / "kanban-work" / "expeditions", "EXP-997", status="done",
        )
        _make_expedition_file(
            cross_board_repo / "kanban-work" / "expeditions", "EXP-998", status="done",
        )

        service = KanbanService(cross_board_config, cross_board_repo)
        dgx_items = service.get_critical_path(agent="DGX")
        assert all(e["assignee"] == "DGX" for e in dgx_items)
        assert len(dgx_items) == 1

    def test_ready_only_filter(self, cross_board_repo, cross_board_config):
        """--ready-for-training should only return ready experiments."""
        _make_experiment_with_implements(
            cross_board_repo / "research" / "experiments",
            "EXPR-READY", "H130.1", "EXP-997",
        )
        _make_experiment_with_implements(
            cross_board_repo / "research" / "experiments",
            "EXPR-BLOCKED", "H127.3", "EXP-991",
        )
        _make_expedition_file(
            cross_board_repo / "kanban-work" / "expeditions", "EXP-997", status="done",
        )
        _make_expedition_file(
            cross_board_repo / "kanban-work" / "expeditions", "EXP-991", status="backlog",
        )

        service = KanbanService(cross_board_config, cross_board_repo)
        ready = service.get_critical_path(ready_only=True)
        assert all(e["readiness"] == "ready_for_training" for e in ready)
        assert any(e["experiment_id"] == "EXPR-READY" for e in ready)
        assert not any(e["experiment_id"] == "EXPR-BLOCKED" for e in ready)

    def test_dev_blockers_only(self, cross_board_repo, cross_board_config):
        """--dev-blockers should return expedition items blocking research."""
        _make_experiment_with_implements(
            cross_board_repo / "research" / "experiments",
            "EXPR-127", "H127.3", "EXP-991",
        )
        _make_expedition_file(
            cross_board_repo / "kanban-work" / "expeditions", "EXP-991", status="backlog",
        )

        service = KanbanService(cross_board_config, cross_board_repo)
        blockers = service.get_critical_path(dev_blockers_only=True)
        assert len(blockers) >= 1
        assert blockers[0]["expedition_id"] == "EXP-991"
        assert "EXPR-127" in blockers[0]["unblocks_experiments"]

    def test_sorting_ready_before_blocked(self, cross_board_repo, cross_board_config):
        """Ready experiments should appear before blocked ones."""
        _make_experiment_with_implements(
            cross_board_repo / "research" / "experiments",
            "EXPR-READY", "H130.1", "EXP-997",
        )
        _make_experiment_with_implements(
            cross_board_repo / "research" / "experiments",
            "EXPR-BLOCKED", "H127.3", "EXP-991",
        )
        _make_expedition_file(
            cross_board_repo / "kanban-work" / "expeditions", "EXP-997", status="done",
        )
        _make_expedition_file(
            cross_board_repo / "kanban-work" / "expeditions", "EXP-991", status="backlog",
        )

        service = KanbanService(cross_board_config, cross_board_repo)
        path = service.get_critical_path()
        # Find positions
        ready_idx = next(
            i for i, e in enumerate(path) if e["experiment_id"] == "EXPR-READY"
        )
        blocked_idx = next(
            i for i, e in enumerate(path) if e["experiment_id"] == "EXPR-BLOCKED"
        )
        assert ready_idx < blocked_idx


# ---------------------------------------------------------------------------
# TestCriticalPathCLI
# ---------------------------------------------------------------------------


class TestCriticalPathCLI:
    """Tests for 'yurtle-kanban hdd critical-path' CLI command."""

    def test_critical_path_basic(self, cross_board_runner, cross_board_repo, cross_board_config):
        """Critical path command should run and show output."""
        _make_paper_file(cross_board_repo / "research" / "papers", "130")
        _make_hypothesis_file(
            cross_board_repo / "research" / "hypotheses", "H130.1", "130",
        )
        _make_experiment_with_implements(
            cross_board_repo / "research" / "experiments",
            "EXPR-130", "H130.1", "EXP-992",
        )
        _make_expedition_file(
            cross_board_repo / "kanban-work" / "expeditions", "EXP-992", status="done",
        )

        result = cross_board_runner.invoke(
            main, ["hdd", "critical-path"], catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert "EXPR-130" in result.output
        assert "Ready for Training" in result.output

    def test_critical_path_json(self, cross_board_runner, cross_board_repo, cross_board_config):
        """--json flag should output valid JSON."""
        import json as json_mod

        _make_experiment_with_implements(
            cross_board_repo / "research" / "experiments",
            "EXPR-130", "H130.1", "EXP-992",
        )
        _make_expedition_file(
            cross_board_repo / "kanban-work" / "expeditions", "EXP-992", status="done",
        )

        result = cross_board_runner.invoke(
            main, ["hdd", "critical-path", "--json"], catch_exceptions=False,
        )
        assert result.exit_code == 0
        data = json_mod.loads(result.output)
        assert isinstance(data, list)

    def test_critical_path_dev_blockers(self, cross_board_runner, cross_board_repo, cross_board_config):
        """--dev-blockers should show blocking expeditions."""
        _make_experiment_with_implements(
            cross_board_repo / "research" / "experiments",
            "EXPR-127", "H127.3", "EXP-991",
        )
        _make_expedition_file(
            cross_board_repo / "kanban-work" / "expeditions", "EXP-991", status="backlog",
        )

        result = cross_board_runner.invoke(
            main, ["hdd", "critical-path", "--dev-blockers"], catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert "EXP-991" in result.output

    def test_critical_path_agent_filter(self, cross_board_runner, cross_board_repo, cross_board_config):
        """--agent should filter output to that agent."""
        _make_experiment_with_implements(
            cross_board_repo / "research" / "experiments",
            "EXPR-A", "H130.1", "EXP-997", assignee="DGX",
        )
        _make_experiment_with_implements(
            cross_board_repo / "research" / "experiments",
            "EXPR-B", "H130.1", "EXP-998", assignee="Mini",
        )
        _make_expedition_file(
            cross_board_repo / "kanban-work" / "expeditions", "EXP-997", status="done",
        )
        _make_expedition_file(
            cross_board_repo / "kanban-work" / "expeditions", "EXP-998", status="done",
        )

        result = cross_board_runner.invoke(
            main, ["hdd", "critical-path", "--agent", "DGX"], catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert "EXPR-A" in result.output
        # EXPR-B (Mini) should not appear
        assert "EXPR-B" not in result.output

    def test_critical_path_empty(self, cross_board_runner, cross_board_repo, cross_board_config):
        """Empty board should not crash."""
        result = cross_board_runner.invoke(
            main, ["hdd", "critical-path"], catch_exceptions=False,
        )
        assert result.exit_code == 0
