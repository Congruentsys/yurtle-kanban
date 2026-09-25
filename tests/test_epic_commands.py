"""Tests for epic/voyage CLI commands."""

import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from tests.issues._snapshot import glob_outside_git
from yurtle_kanban.cli import main
from yurtle_kanban.config import KanbanConfig, PathConfig
from yurtle_kanban.models import WorkItem, WorkItemStatus, WorkItemType
from yurtle_kanban.service import KanbanService

# ---------------------------------------------------------------------------
# Fixtures — Nautical theme (voyages)
# ---------------------------------------------------------------------------


@pytest.fixture
def nautical_repo(tmp_path):
    """Create a minimal git repo with nautical theme for voyage testing."""
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


# ---------------------------------------------------------------------------
# Fixtures — Software theme (epics)
# ---------------------------------------------------------------------------


@pytest.fixture
def software_repo(tmp_path):
    """Create a minimal git repo with software theme for epic testing."""
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
    (tmp_path / "work" / "features").mkdir(parents=True)
    (tmp_path / "work" / "epics").mkdir(parents=True)
    (tmp_path / "work" / "bugs").mkdir(parents=True)

    config = KanbanConfig(
        theme="software",
        paths=PathConfig(
            root="work/",
            scan_paths=[
                "work/features/",
                "work/epics/",
                "work/bugs/",
            ],
        ),
    )
    config.save(tmp_path / ".kanban" / "config.yaml")
    return tmp_path


@pytest.fixture
def software_runner(software_repo, monkeypatch):
    """Click runner with cwd set to software repo."""
    from yurtle_kanban import config as config_mod

    config_mod._theme_cache.clear()
    monkeypatch.chdir(software_repo)
    return CliRunner()


# ---------------------------------------------------------------------------
# Epic Create (primary command, software theme)
# ---------------------------------------------------------------------------


class TestEpicCreate:
    """Tests for 'yurtle-kanban epic create'."""

    def test_epic_create_software(self, software_runner, software_repo):
        """Create an epic in software theme should produce EPIC-XXX."""
        result = software_runner.invoke(
            main, ["epic", "create", "User Auth Overhaul"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        assert "EPIC-" in result.output
        assert "User Auth Overhaul" in result.output

    def test_epic_create_with_items(self, nautical_runner, nautical_repo):
        """Create with --items should link items to the new epic."""
        nautical_runner.invoke(
            main, ["create", "expedition", "Phase 1 Work", "--priority", "high"],
            catch_exceptions=False,
        )
        result = nautical_runner.invoke(
            main, ["epic", "create", "Big Project", "--items", "EXP-001"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert "Linked EXP-001" in result.output


# ---------------------------------------------------------------------------
# Voyage Create (nautical alias)
# ---------------------------------------------------------------------------


class TestVoyageCreate:
    """Tests for 'yurtle-kanban voyage create' (nautical alias)."""

    def test_voyage_create_nautical(self, nautical_runner, nautical_repo):
        """Create a voyage in nautical theme should produce VOY-XXX."""
        result = nautical_runner.invoke(
            main, ["voyage", "create", "Campaign Management"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        assert "VOY-" in result.output
        assert "Campaign Management" in result.output

    def test_voyage_create_auto_increments(self, nautical_runner, nautical_repo):
        """Second voyage should get next ID number."""
        nautical_runner.invoke(
            main, ["voyage", "create", "First Voyage"],
            catch_exceptions=False,
        )
        result = nautical_runner.invoke(
            main, ["voyage", "create", "Second Voyage"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert "VOY-" in result.output

    def test_voyage_create_with_priority(self, nautical_runner, nautical_repo):
        """Voyage should accept --priority flag."""
        result = nautical_runner.invoke(
            main, ["voyage", "create", "Critical Voyage", "-p", "critical"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Epic Show
# ---------------------------------------------------------------------------


class TestEpicShow:
    """Tests for 'yurtle-kanban epic show' / 'voyage show'."""

    def test_show_existing_voyage(self, nautical_runner, nautical_repo):
        """Show should display a created voyage."""
        nautical_runner.invoke(
            main, ["voyage", "create", "Test Voyage"],
            catch_exceptions=False,
        )
        result = nautical_runner.invoke(
            main, ["voyage", "show", "VOY-001"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert "Test Voyage" in result.output

    def test_show_nonexistent_raises_error(self, nautical_runner, nautical_repo):
        """Show should fail for nonexistent ID with ClickException."""
        result = nautical_runner.invoke(
            main, ["voyage", "show", "VOY-999"],
        )
        assert result.exit_code != 0
        assert "not found" in result.output

    def test_show_with_linked_items(self, nautical_runner, nautical_repo):
        """Show should display items linked via related field."""
        nautical_runner.invoke(
            main, ["voyage", "create", "Big Voyage"],
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
        result = nautical_runner.invoke(
            main, ["voyage", "show", "VOY-001"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert "EXP-001" in result.output
        assert "0/1" in result.output or "Progress" in result.output


# ---------------------------------------------------------------------------
# Epic Add
# ---------------------------------------------------------------------------


class TestEpicAdd:
    """Tests for 'yurtle-kanban epic add' / 'voyage add'."""

    def test_add_links_item(self, nautical_runner, nautical_repo):
        """Add should write voyage ID to item's related field."""
        nautical_runner.invoke(
            main, ["voyage", "create", "Link Test"],
            catch_exceptions=False,
        )
        nautical_runner.invoke(
            main, ["create", "expedition", "Item To Link", "--priority", "high"],
            catch_exceptions=False,
        )
        result = nautical_runner.invoke(
            main, ["voyage", "add", "VOY-001", "EXP-001"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert "Linked" in result.output

        # Verify the file was updated
        exp_files = list(
            (nautical_repo / "kanban-work" / "expeditions").glob("EXP-001*.md")
        )
        assert len(exp_files) == 1
        content = exp_files[0].read_text()
        assert "VOY-001" in content

    def test_add_idempotent(self, nautical_runner, nautical_repo):
        """Adding the same link twice should not duplicate."""
        nautical_runner.invoke(
            main, ["voyage", "create", "Idem Test"],
            catch_exceptions=False,
        )
        nautical_runner.invoke(
            main, ["create", "expedition", "Item", "--priority", "high"],
            catch_exceptions=False,
        )
        nautical_runner.invoke(
            main, ["voyage", "add", "VOY-001", "EXP-001"],
            catch_exceptions=False,
        )
        result = nautical_runner.invoke(
            main, ["voyage", "add", "VOY-001", "EXP-001"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert "already linked" in result.output

    def test_add_nonexistent_epic_raises_error(self, nautical_runner, nautical_repo):
        """Adding to nonexistent epic should fail with ClickException."""
        result = nautical_runner.invoke(
            main, ["voyage", "add", "VOY-999", "EXP-001"],
        )
        assert result.exit_code != 0
        assert "not found" in result.output

    def test_add_nonexistent_item_raises_error(self, nautical_runner, nautical_repo):
        """Adding nonexistent item should fail with ClickException."""
        nautical_runner.invoke(
            main, ["voyage", "create", "Test"],
            catch_exceptions=False,
        )
        result = nautical_runner.invoke(
            main, ["voyage", "add", "VOY-001", "EXP-999"],
        )
        assert "not found" in result.output


# ---------------------------------------------------------------------------
# Related Field
# ---------------------------------------------------------------------------


class TestRelatedField:
    """Tests for the related field on WorkItem."""

    def test_related_field_exists(self):
        """WorkItem should have a related field."""
        item = WorkItem(
            id="EXP-001",
            title="Test",
            item_type=WorkItemType.EXPEDITION,
            status=WorkItemStatus.BACKLOG,
            file_path="/tmp/test.md",
            related=["VOY-001", "EXP-002"],
        )
        assert item.related == ["VOY-001", "EXP-002"]

    def test_related_default_empty(self):
        """Related should default to empty list."""
        item = WorkItem(
            id="EXP-001",
            title="Test",
            item_type=WorkItemType.EXPEDITION,
            status=WorkItemStatus.BACKLOG,
            file_path="/tmp/test.md",
        )
        assert item.related == []

    def test_related_in_to_dict(self):
        """to_dict should include related."""
        item = WorkItem(
            id="EXP-001",
            title="Test",
            item_type=WorkItemType.EXPEDITION,
            status=WorkItemStatus.BACKLOG,
            file_path="/tmp/test.md",
            related=["VOY-001"],
        )
        d = item.to_dict()
        assert d["related"] == ["VOY-001"]

    def test_related_in_to_markdown(self):
        """to_markdown should include related in frontmatter."""
        item = WorkItem(
            id="EXP-001",
            title="Test",
            item_type=WorkItemType.EXPEDITION,
            status=WorkItemStatus.BACKLOG,
            file_path="/tmp/test.md",
            related=["VOY-001", "EXP-002"],
        )
        md = item.to_markdown()
        assert "related: [VOY-001, EXP-002]" in md

    def test_related_in_to_yurtle(self):
        """to_yurtle should include kb:related."""
        item = WorkItem(
            id="EXP-001",
            title="Test",
            item_type=WorkItemType.EXPEDITION,
            status=WorkItemStatus.BACKLOG,
            file_path="/tmp/test.md",
            related=["VOY-001"],
        )
        yurtle = item.to_yurtle()
        assert "kb:related" in yurtle

    def test_related_parsed_from_frontmatter(self, nautical_runner, nautical_repo):
        """Service should parse related field from frontmatter."""
        exp_dir = nautical_repo / "kanban-work" / "expeditions"
        (exp_dir / "EXP-001-Test.md").write_text(
            "---\n"
            "id: EXP-001\n"
            'title: "Test"\n'
            "type: expedition\n"
            "status: backlog\n"
            "created: 2026-02-27\n"
            "priority: medium\n"
            "related: [VOY-001, EXP-002]\n"
            "---\n\n# Test\n"
        )
        config = KanbanConfig.load(nautical_repo / ".kanban" / "config.yaml")
        service = KanbanService(config, nautical_repo)
        service.scan()

        item = service._items.get("EXP-001")
        assert item is not None
        assert "VOY-001" in item.related
        assert "EXP-002" in item.related


# ---------------------------------------------------------------------------
# Board --epic filter (NB4: strengthened assertions)
# ---------------------------------------------------------------------------


class TestBoardEpicFilter:
    """Tests for 'yurtle-kanban board --epic'."""

    def test_board_epic_filter_shows_linked_excludes_unlinked(self, nautical_runner, nautical_repo):
        """Board --epic should show linked item and exclude unlinked."""
        nautical_runner.invoke(
            main, ["voyage", "create", "Filter Test"],
            catch_exceptions=False,
        )
        nautical_runner.invoke(
            main, ["create", "expedition", "Linked Item", "--priority", "high"],
            catch_exceptions=False,
        )
        nautical_runner.invoke(
            main, ["create", "expedition", "Unlinked Item", "--priority", "low"],
            catch_exceptions=False,
        )
        nautical_runner.invoke(
            main, ["voyage", "add", "VOY-001", "EXP-001"],
            catch_exceptions=False,
        )
        result = nautical_runner.invoke(
            main, ["board", "--epic", "VOY-001"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        # Rich table truncates IDs (EXP-001 → EXP-0…), so check titles
        assert "Linked" in result.output
        assert "Unlinked" not in result.output


# ---------------------------------------------------------------------------
# Cross-theme: epic in nautical, voyage in software
# ---------------------------------------------------------------------------


class TestCrossTheme:
    """Both commands work in any theme — they auto-detect."""

    def test_epic_command_in_nautical_creates_voyage(self, nautical_runner, nautical_repo):
        """Using 'epic create' in nautical theme should still create a VOY- item."""
        result = nautical_runner.invoke(
            main, ["epic", "create", "Cross Theme Test"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert "VOY-" in result.output

    def test_voyage_command_in_software_creates_epic(self, software_runner, software_repo):
        """Using 'voyage create' in software theme should still create an EPIC- item."""
        result = software_runner.invoke(
            main, ["voyage", "create", "Cross Theme Test"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert "EPIC-" in result.output


# ---------------------------------------------------------------------------
# NB2: --items + --push warns about partial state
# ---------------------------------------------------------------------------


class TestCreateItemsPushWarning:
    """Using --items with --push should warn about local-only link changes."""

    def test_items_push_warns(self, nautical_runner, nautical_repo):
        """Create with --items and --push should print a warning."""
        nautical_runner.invoke(
            main, ["create", "expedition", "Phase 1", "--priority", "high"],
            catch_exceptions=False,
        )
        result = nautical_runner.invoke(
            main, ["voyage", "create", "Warned Voyage", "--items", "EXP-001", "--push"],
        )
        # --push without a remote will fail, but the warning should appear first
        assert "Warning" in result.output or "local-only" in result.output


class TestEpicCreatePriority:
    """epic create -p must reach both frontmatter and the kb:priority triple (#99)."""

    def _epic_text(self, software_repo: Path) -> str:
        return next(glob_outside_git(software_repo, "EPIC-*.md")).read_text()

    def test_explicit_priority_written(self, software_runner, software_repo):
        result = software_runner.invoke(
            main, ["epic", "create", "Auth", "-p", "critical"], catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        text = self._epic_text(software_repo)
        assert "\npriority: critical\n" in text
        assert "kb:priority kb:critical" in text
        assert "kb:medium" not in text

    def test_default_priority_is_documented_high(self, software_runner, software_repo):
        """--priority defaults to high; the template's medium used to win."""
        software_runner.invoke(main, ["epic", "create", "Auth"], catch_exceptions=False)
        text = self._epic_text(software_repo)
        assert "\npriority: high\n" in text
        assert "kb:priority kb:high" in text

    def test_invalid_priority_rejected(self, software_runner, software_repo):
        """`-p "very high"` would write the invalid Turtle `kb:very high`."""
        result = software_runner.invoke(main, ["epic", "create", "Auth", "-p", "very high"])
        assert result.exit_code == 2
        assert not list(glob_outside_git(software_repo, "EPIC-*.md"))


# ---------------------------------------------------------------------------
# Issue #102 — epic create must land under the configured root
# ---------------------------------------------------------------------------


class TestThemePathsUnderConfiguredRoot:
    """epic create on a board whose root is not kanban-work/ must place the
    epic under that root so board/list can see it (#102)."""

    def test_epic_create_lands_under_configured_root(self, software_runner, software_repo):
        """Issue repro: software theme, root work/ → epic create goes under work/."""
        result = software_runner.invoke(
            main, ["epic", "create", "Auth"], catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output

        files = sorted(
            p.relative_to(software_repo) for p in glob_outside_git(software_repo, "EPIC-*.md")
        )
        assert len(files) == 1, files
        assert files[0].parts[0] == "work", (
            f"epic created at {files[0]}, outside the configured root work/"
        )
        assert not (software_repo / "kanban-work").exists()

    def test_created_epic_is_listed(self, software_runner, software_repo):
        """After epic create, list must show the epic."""
        software_runner.invoke(main, ["epic", "create", "Auth"], catch_exceptions=False)
        result = software_runner.invoke(main, ["list", "--json"], catch_exceptions=False)

        assert result.exit_code == 0, result.output
        assert "EPIC-001" in result.output

    def test_control_epic_create_default_root_unchanged(self, nautical_runner, nautical_repo):
        """Control: nautical board at kanban-work/ keeps epics beside the theme default."""
        result = nautical_runner.invoke(
            main, ["voyage", "create", "Campaign"], catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        files = sorted(
            p.relative_to(nautical_repo) for p in glob_outside_git(nautical_repo, "VOY-*.md")
        )
        assert len(files) == 1, files
        assert files[0].parent == Path("kanban-work/voyages")


# ---------------------------------------------------------------------------
# Issue #142 — epic/voyage create renders the title through the template
# ---------------------------------------------------------------------------

_EPIC_ROUND_TRIP_VALUES = [
    pytest.param('Could X identify what is "stale" vs "fresh"?', id="double-quotes"),
    pytest.param(r"C:\new\table and a\b", id="backslash"),
    pytest.param("ends with a backslash\\", id="trailing-backslash"),
    pytest.param("team: core", id="colon-space"),
    pytest.param("#tag", id="hash"),
    pytest.param("first line\nsecond line", id="newline"),
]


def _epic_frontmatter(path: Path) -> dict:
    """Parse the file's frontmatter; unparseable YAML is an assertion failure."""
    import yaml

    text = path.read_text()
    assert text.startswith("---\n"), text[:200]
    end = text.index("\n---\n", 4)
    try:
        fm = yaml.safe_load(text[4:end])
    except yaml.YAMLError as exc:
        raise AssertionError(
            f"frontmatter does not parse as YAML: {exc}\n{text[: end + 5]}"
        ) from None
    assert isinstance(fm, dict), text[: end + 5]
    return fm


class TestTemplateValuesRoundTrip:
    """epic/voyage create titles with YAML-special characters read back exactly (#142)."""

    @pytest.mark.parametrize("value", _EPIC_ROUND_TRIP_VALUES)
    @pytest.mark.parametrize("runner_name, repo_name, command, glob", [
        pytest.param("software_runner", "software_repo", "epic", "EPIC-*.md", id="epic"),
        pytest.param("nautical_runner", "nautical_repo", "voyage", "VOY-*.md", id="voyage"),
    ])
    def test_created_epic_is_listed_and_title_round_trips(
        self, request, runner_name, repo_name, command, glob, value,
    ):
        import json

        runner = request.getfixturevalue(runner_name)
        repo = request.getfixturevalue(repo_name)
        result = runner.invoke(main, [command, "create", value])
        assert result.exit_code == 0, (result.output, result.exception)

        files = list(glob_outside_git(repo, glob))
        assert len(files) == 1, files
        fm = _epic_frontmatter(files[0])
        assert fm["title"] == value
        item_id = fm["id"]

        listed = runner.invoke(main, ["list", "--json"])
        assert listed.exit_code == 0, listed.output
        assert item_id in [i["id"] for i in json.loads(listed.output)]

        shown = runner.invoke(main, ["show", item_id, "--json"])
        assert shown.exit_code == 0, shown.output
        assert json.loads(shown.output)["title"] == value

    def test_ordinary_epic_title_renders_textually_unchanged(
        self, software_runner, software_repo,
    ):
        """Control: an ordinary title keeps today's `title: "..."` line and heading."""
        result = software_runner.invoke(
            main, ["epic", "create", "User Auth Overhaul"], catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        text = next(glob_outside_git(software_repo, "EPIC-*.md")).read_text()
        assert 'title: "User Auth Overhaul"\n' in text
        assert "\n# User Auth Overhaul\n" in text


# ---------------------------------------------------------------------------
# Issue #148 — linking an item to an epic keeps its existing 'related' elements
# ---------------------------------------------------------------------------


def _write_feature(repo: Path, related: list[str]) -> Path:
    """Write FEAT-001 through WorkItem.to_markdown (which quotes per #121)."""
    path = repo / "work" / "features" / "FEAT-001-linked-item.md"
    item = WorkItem(
        id="FEAT-001",
        title="Linked item",
        item_type=WorkItemType.FEATURE,
        status=WorkItemStatus.BACKLOG,
        file_path=path,
        priority="medium",
        related=related,
    )
    path.write_text(item.to_markdown())
    return path


def _frontmatter_of(path: Path) -> dict:
    import yaml

    text = path.read_text()
    lines = text.split("\n")
    assert lines[0] == "---", text
    fm_text = "\n".join(lines[1:lines.index("---", 1)])
    try:
        fm = yaml.safe_load(fm_text)
    except yaml.YAMLError as exc:
        pytest.fail(f"frontmatter is not valid YAML after linking: {exc}\n{text}")
    assert isinstance(fm, dict), text
    return fm


_RELATED_MUST_STAY_QUOTED = [
    pytest.param(["a, b"], id="comma-space"),
    pytest.param(["team: core"], id="colon-space"),
    pytest.param(["yes"], id="yes-bool"),
    pytest.param(["FEAT-002", "a, b", "team: core"], id="mixed"),
]


class TestEpicLinkKeepsRelatedElementsIssue148:
    """Linking an item to an epic re-writes 'related' so every element survives (#148)."""

    def _create_epic(self, runner) -> None:
        result = runner.invoke(main, ["epic", "create", "Big Project"], catch_exceptions=False)
        assert result.exit_code == 0, result.output

    @pytest.mark.parametrize("related", _RELATED_MUST_STAY_QUOTED)
    def test_epic_add_keeps_related_elements(self, software_runner, software_repo, related):
        """`epic add` appends the epic id; the existing elements read back unchanged."""
        path = _write_feature(software_repo, related)
        assert _frontmatter_of(path)["related"] == related  # #121 precondition
        self._create_epic(software_runner)

        result = software_runner.invoke(
            main, ["epic", "add", "EPIC-001", "FEAT-001"], catch_exceptions=False
        )
        assert result.exit_code == 0, result.output

        fm = _frontmatter_of(path)
        assert fm["related"] == related + ["EPIC-001"]
        assert len(fm["related"]) == len(related) + 1

    @pytest.mark.parametrize("related", _RELATED_MUST_STAY_QUOTED)
    def test_epic_create_items_keeps_related_elements(
        self, software_runner, software_repo, related
    ):
        """`epic create --items` links the same way and keeps the elements too."""
        path = _write_feature(software_repo, related)
        result = software_runner.invoke(
            main,
            ["epic", "create", "Big Project", "--items", "FEAT-001"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        assert "Linked FEAT-001" in result.output

        fm = _frontmatter_of(path)
        assert fm["related"] == related + ["EPIC-001"]

    @pytest.mark.parametrize("related", _RELATED_MUST_STAY_QUOTED)
    def test_linked_item_still_listed_with_related(
        self, software_runner, software_repo, related
    ):
        """After linking, the item is still loaded and its related list is intact."""
        import json

        _write_feature(software_repo, related)
        self._create_epic(software_runner)
        software_runner.invoke(
            main, ["epic", "add", "EPIC-001", "FEAT-001"], catch_exceptions=False
        )

        result = software_runner.invoke(
            main, ["show", "FEAT-001", "--json"], catch_exceptions=False
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["related"] == related + ["EPIC-001"]

    # -- must stay true (controls) -------------------------------------------

    def test_control_plain_related_written_unquoted(self, software_runner, software_repo):
        """Ordinary ids stay plain: `related: [FEAT-002, EPIC-001]`, exactly as today."""
        path = _write_feature(software_repo, ["FEAT-002"])
        self._create_epic(software_runner)
        software_runner.invoke(
            main, ["epic", "add", "EPIC-001", "FEAT-001"], catch_exceptions=False
        )
        assert "\nrelated: [FEAT-002, EPIC-001]\n" in path.read_text()
        assert _frontmatter_of(path)["related"] == ["FEAT-002", "EPIC-001"]

    def test_control_no_related_line_inserted(self, software_runner, software_repo):
        """An item with no related line gets `related: [EPIC-001]`."""
        path = _write_feature(software_repo, [])
        self._create_epic(software_runner)
        software_runner.invoke(
            main, ["epic", "add", "EPIC-001", "FEAT-001"], catch_exceptions=False
        )
        assert "\nrelated: [EPIC-001]\n" in path.read_text()
        assert _frontmatter_of(path)["related"] == ["EPIC-001"]

    def test_control_add_idempotent(self, software_runner, software_repo):
        path = _write_feature(software_repo, ["FEAT-002"])
        self._create_epic(software_runner)
        for _ in range(2):
            software_runner.invoke(
                main, ["epic", "add", "EPIC-001", "FEAT-001"], catch_exceptions=False
            )
        assert _frontmatter_of(path)["related"] == ["FEAT-002", "EPIC-001"]


def _assert_all_crlf(path: Path) -> None:
    import re

    data = path.read_bytes()
    bare = [
        data[max(0, m.start() - 30): m.start() + 1]
        for m in re.finditer(rb"(?<!\r)\n", data)
    ]
    assert not bare, (
        f"{len(bare)} bare LF line ending(s) in a CRLF file, first near: {bare[:3]}"
    )
    assert b"\r\r\n" not in data, data
    assert data.endswith(b"\r\n"), data[-40:]


_RELATED_SHAPES = [
    pytest.param([], id="no-related-line"),
    pytest.param(["FEAT-002"], id="existing-related-line"),
]


class TestEpicLinkKeepsLineEndingsIssue151:
    """Linking an item to an epic keeps the item file's own line ending (#151)."""

    @staticmethod
    def _write_crlf_feature(repo: Path, related: list[str]) -> Path:
        path = _write_feature(repo, related)
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
        assert b"\r\n" in path.read_bytes()
        _assert_all_crlf(path)
        return path

    @pytest.mark.parametrize("related", _RELATED_SHAPES)
    def test_epic_add_keeps_crlf(self, software_runner, software_repo, related):
        """`epic add` on a CRLF item file: every line still ends in CRLF."""
        path = self._write_crlf_feature(software_repo, related)
        result = software_runner.invoke(
            main, ["epic", "create", "Big Project"], catch_exceptions=False
        )
        assert result.exit_code == 0, result.output

        result = software_runner.invoke(
            main, ["epic", "add", "EPIC-001", "FEAT-001"], catch_exceptions=False
        )
        assert result.exit_code == 0, result.output

        assert _frontmatter_of(path)["related"] == related + ["EPIC-001"]
        _assert_all_crlf(path)

    @pytest.mark.parametrize("related", _RELATED_SHAPES)
    def test_epic_create_items_keeps_crlf(self, software_runner, software_repo, related):
        """`epic create --items` on a CRLF item file: every line still ends in CRLF."""
        path = self._write_crlf_feature(software_repo, related)
        result = software_runner.invoke(
            main,
            ["epic", "create", "Big Project", "--items", "FEAT-001"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        assert "Linked FEAT-001" in result.output

        assert _frontmatter_of(path)["related"] == related + ["EPIC-001"]
        _assert_all_crlf(path)

    # -- must stay true (controls) -------------------------------------------

    @pytest.mark.parametrize("related", _RELATED_SHAPES)
    def test_control_lf_item_stays_lf(self, software_runner, software_repo, related):
        """An LF item file stays LF after linking; only the related line changes."""
        path = _write_feature(software_repo, related)
        before = path.read_bytes().split(b"\n")
        software_runner.invoke(
            main, ["epic", "create", "Big Project"], catch_exceptions=False
        )
        software_runner.invoke(
            main, ["epic", "add", "EPIC-001", "FEAT-001"], catch_exceptions=False
        )

        data = path.read_bytes()
        assert b"\r" not in data
        after = data.split(b"\n")
        assert [ln for ln in after if not ln.startswith(b"related:")] == [
            ln for ln in before if not ln.startswith(b"related:")
        ]
        assert _frontmatter_of(path)["related"] == related + ["EPIC-001"]


# ---------------------------------------------------------------------------
# Issue #161 — `related` ids never go through a re.sub replacement string
# ---------------------------------------------------------------------------

_RELATED_REPLACEMENT_VALUES = [
    pytest.param(["H1\\"], id="trailing-backslash"),
    pytest.param(["H1\\b"], id="backslash-b"),
    pytest.param(["A\\d"], id="backslash-d"),
    pytest.param(["X\\g<0>Y"], id="group-ref"),
    pytest.param(["FEAT-002", "X&Y", "A\\d"], id="mixed"),
]


class TestEpicRelatedReplacementValuesIssue161:
    """Linking keeps `related` ids with `\\`, `\\g<0>`, `&` exactly (#161)."""

    @pytest.mark.parametrize("related", _RELATED_REPLACEMENT_VALUES)
    def test_epic_add_keeps_related_ids_exactly(self, software_runner, software_repo, related):
        """`epic add` rewrites an existing `related:` line; every id reads back exactly."""
        path = _write_feature(software_repo, related)
        assert _frontmatter_of(path)["related"] == related  # precondition
        result = software_runner.invoke(main, ["epic", "create", "Big Project"])
        assert result.exit_code == 0, (result.output, result.exception)

        result = software_runner.invoke(main, ["epic", "add", "EPIC-001", "FEAT-001"])
        assert result.exception is None or isinstance(result.exception, SystemExit), (
            result.output, repr(result.exception),
        )
        assert result.exit_code == 0, result.output
        assert _frontmatter_of(path)["related"] == related + ["EPIC-001"]

    @pytest.mark.parametrize("related", _RELATED_REPLACEMENT_VALUES)
    def test_epic_create_items_keeps_related_ids_exactly(
        self, software_runner, software_repo, related,
    ):
        """`epic create --items` links the same way and keeps the ids too."""
        path = _write_feature(software_repo, related)
        result = software_runner.invoke(
            main, ["epic", "create", "Big Project", "--items", "FEAT-001"],
        )
        assert result.exception is None or isinstance(result.exception, SystemExit), (
            result.output, repr(result.exception),
        )
        assert result.exit_code == 0, result.output
        assert _frontmatter_of(path)["related"] == related + ["EPIC-001"]

    def test_control_plain_related_line_unchanged(self, software_runner, software_repo):
        """Control: ordinary ids keep today's `related: [FEAT-002, EPIC-001]` line."""
        path = _write_feature(software_repo, ["FEAT-002"])
        software_runner.invoke(main, ["epic", "create", "Big Project"], catch_exceptions=False)
        software_runner.invoke(
            main, ["epic", "add", "EPIC-001", "FEAT-001"], catch_exceptions=False
        )
        assert "\nrelated: [FEAT-002, EPIC-001]\n" in path.read_text()
