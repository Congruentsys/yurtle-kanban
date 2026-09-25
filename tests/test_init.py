"""Tests for the init command scaffolding (Issue #7)."""

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from tests.issues._snapshot import glob_outside_git
from yurtle_kanban.cli import _get_skills_dir, _get_templates_dir, main


class TestSharedDataResolution:
    """_get_templates_dir() and _get_skills_dir() should find shared data (#19)."""

    def test_get_templates_dir_finds_templates(self):
        """Templates dir should exist and contain expected theme subdirs."""
        templates_dir = _get_templates_dir()
        assert templates_dir.exists(), f"templates dir not found: {templates_dir}"
        # Should contain at least the standard themes
        for theme in ("hdd", "nautical", "software"):
            assert (templates_dir / theme).is_dir(), f"Missing theme subdir: {theme}"

    def test_get_skills_dir_finds_skills(self):
        """Skills dir should exist and contain expected theme subdirs."""
        skills_dir = _get_skills_dir()
        assert skills_dir.exists(), f"skills dir not found: {skills_dir}"
        # Should contain at least the standard themes
        for theme in ("hdd", "nautical", "software"):
            assert (skills_dir / theme).is_dir(), f"Missing theme subdir: {theme}"


class TestInitScaffolding:
    """Init should scaffold directories and templates from the theme."""

    def test_software_theme_creates_all_directories(self, tmp_path, monkeypatch):
        """Software theme should create 6 type directories."""
        monkeypatch.chdir(tmp_path)
        # Init a git repo so the CLI doesn't complain
        import subprocess
        subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, capture_output=True, check=True)

        runner = CliRunner()
        result = runner.invoke(main, ["init", "--theme", "software"])

        assert result.exit_code == 0, result.output

        expected_dirs = [
            "kanban-work/features",
            "kanban-work/bugs",
            "kanban-work/epics",
            "kanban-work/issues",
            "kanban-work/tasks",
            "kanban-work/ideas",
        ]
        for d in expected_dirs:
            assert (tmp_path / d).is_dir(), f"Missing directory: {d}"

    def test_nautical_theme_creates_all_directories(self, tmp_path, monkeypatch):
        """Nautical theme should create 5 type directories."""
        monkeypatch.chdir(tmp_path)
        import subprocess
        subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, capture_output=True, check=True)

        runner = CliRunner()
        result = runner.invoke(main, ["init", "--theme", "nautical"])

        assert result.exit_code == 0, result.output

        expected_dirs = [
            "kanban-work/expeditions",
            "kanban-work/voyages",
            "kanban-work/chores",
            "kanban-work/hazards",
            "kanban-work/signals",
        ]
        for d in expected_dirs:
            assert (tmp_path / d).is_dir(), f"Missing directory: {d}"

    def test_templates_created_in_each_directory(self, tmp_path, monkeypatch):
        """Each directory should get a _TEMPLATE.md file."""
        monkeypatch.chdir(tmp_path)
        import subprocess
        subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, capture_output=True, check=True)

        runner = CliRunner()
        runner.invoke(main, ["init", "--theme", "software"])

        for d in ["features", "bugs", "epics", "issues", "tasks", "ideas"]:
            template = tmp_path / "kanban-work" / d / "_TEMPLATE.md"
            assert template.exists(), f"Missing template: {template}"

    def test_template_has_correct_prefix(self, tmp_path, monkeypatch):
        """Template frontmatter should use the correct ID prefix."""
        monkeypatch.chdir(tmp_path)
        import subprocess
        subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, capture_output=True, check=True)

        runner = CliRunner()
        runner.invoke(main, ["init", "--theme", "software"])

        template = (tmp_path / "kanban-work" / "features" / "_TEMPLATE.md").read_text()
        assert "FEAT-XXX" in template

        template = (tmp_path / "kanban-work" / "bugs" / "_TEMPLATE.md").read_text()
        assert "BUG-XXX" in template

    def test_nautical_template_has_correct_prefix(self, tmp_path, monkeypatch):
        """Nautical template should use EXP, VOY, etc."""
        monkeypatch.chdir(tmp_path)
        import subprocess
        subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, capture_output=True, check=True)

        runner = CliRunner()
        runner.invoke(main, ["init", "--theme", "nautical"])

        template = (tmp_path / "kanban-work" / "expeditions" / "_TEMPLATE.md").read_text()
        assert "EXP-XXX" in template

        template = (tmp_path / "kanban-work" / "signals" / "_TEMPLATE.md").read_text()
        assert "SIG-XXX" in template

    def test_config_yaml_has_scan_paths(self, tmp_path, monkeypatch):
        """Generated config scans the theme root, not one entry per type (#112).

        The per-type folders are still scaffolded on disk; the root scan covers them.
        """
        monkeypatch.chdir(tmp_path)
        import subprocess
        subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, capture_output=True, check=True)

        runner = CliRunner()
        result = runner.invoke(main, ["init", "--theme", "software"])
        assert result.exit_code == 0, result.output

        raw = yaml.safe_load((tmp_path / ".kanban" / "config.yaml").read_text())
        scan_paths = [p.rstrip("/") for p in raw["kanban"]["paths"]["scan_paths"]]
        assert "kanban-work" in scan_paths, f"theme root not scanned: {scan_paths!r}"
        assert (tmp_path / "kanban-work" / "features").is_dir()
        assert (tmp_path / "kanban-work" / "bugs").is_dir()

    def test_config_yaml_has_ignore_templates(self, tmp_path, monkeypatch):
        """Config should ignore _TEMPLATE* files."""
        monkeypatch.chdir(tmp_path)
        import subprocess
        subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, capture_output=True, check=True)

        runner = CliRunner()
        runner.invoke(main, ["init", "--theme", "software"])

        config_text = (tmp_path / ".kanban" / "config.yaml").read_text()
        assert "_TEMPLATE" in config_text

    def test_flat_directory_structure(self, tmp_path, monkeypatch):
        """All directories should be flat (no nesting like idea-intake/ideas-queue)."""
        monkeypatch.chdir(tmp_path)
        import subprocess
        subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, capture_output=True, check=True)

        runner = CliRunner()
        runner.invoke(main, ["init", "--theme", "software"])

        # Check that ideas/ is a flat directory, not nested
        ideas_dir = tmp_path / "kanban-work" / "ideas"
        assert ideas_dir.is_dir()
        # Should NOT have any nested subdirectories (only _TEMPLATE.md)
        subdirs = [p for p in ideas_dir.iterdir() if p.is_dir()]
        assert len(subdirs) == 0, f"Unexpected nested dirs in ideas/: {subdirs}"

    def test_template_sections_match_type(self, tmp_path, monkeypatch):
        """Bug templates should have 'Steps to Reproduce', expeditions 'Plan', etc."""
        monkeypatch.chdir(tmp_path)
        import subprocess
        subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, capture_output=True, check=True)

        runner = CliRunner()
        runner.invoke(main, ["init", "--theme", "software"])

        bug_template = (tmp_path / "kanban-work" / "bugs" / "_TEMPLATE.md").read_text()
        assert "## Steps to Reproduce" in bug_template
        assert "## Expected Behavior" in bug_template

        feat_template = (tmp_path / "kanban-work" / "features" / "_TEMPLATE.md").read_text()
        assert "## Goal" in feat_template
        assert "## Acceptance Criteria" in feat_template

    def test_software_skills_installed(self, tmp_path, monkeypatch):
        """Software theme should install /feature skill (not /expedition)."""
        monkeypatch.chdir(tmp_path)
        import subprocess
        subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, capture_output=True, check=True)

        runner = CliRunner()
        result = runner.invoke(main, ["init", "--theme", "software"])

        assert result.exit_code == 0, result.output

        skills_dir = tmp_path / ".claude" / "skills"
        # Software theme should get /feature, not /expedition
        assert (skills_dir / "feature" / "SKILL.md").exists()
        assert not (skills_dir / "expedition" / "SKILL.md").exists()
        # Theme-neutral skills should also be installed
        assert (skills_dir / "sync" / "SKILL.md").exists()
        assert (skills_dir / "status" / "SKILL.md").exists()
        assert (skills_dir / "release" / "SKILL.md").exists()

    def test_nautical_skills_installed(self, tmp_path, monkeypatch):
        """Nautical theme should install /expedition skill (not /feature)."""
        monkeypatch.chdir(tmp_path)
        import subprocess
        subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, capture_output=True, check=True)

        runner = CliRunner()
        result = runner.invoke(main, ["init", "--theme", "nautical"])

        assert result.exit_code == 0, result.output

        skills_dir = tmp_path / ".claude" / "skills"
        # Nautical theme should get /expedition, not /feature
        assert (skills_dir / "expedition" / "SKILL.md").exists()
        assert not (skills_dir / "feature" / "SKILL.md").exists()
        # Theme-neutral skills
        assert (skills_dir / "sync" / "SKILL.md").exists()

    def test_skill_content_matches_theme(self, tmp_path, monkeypatch):
        """Software /feature skill should reference FEAT-, not EXP-."""
        monkeypatch.chdir(tmp_path)
        import subprocess
        subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, capture_output=True, check=True)

        runner = CliRunner()
        runner.invoke(main, ["init", "--theme", "software"])

        skill = (tmp_path / ".claude" / "skills" / "feature" / "SKILL.md").read_text()
        assert "FEAT-" in skill
        assert "EXP-" not in skill

        work_skill = (tmp_path / ".claude" / "skills" / "work" / "SKILL.md").read_text()
        assert "FEAT-" in work_skill
        assert "expedition" not in work_skill.lower()


class TestScaffoldIsUsableByTheToolThatWroteIt:
    """A fresh `init` must produce a board this tool can read back correctly.

    Every assertion here is on the READ-BACK side, not the write side. Reading
    the generated YAML with `yaml.safe_load` and checking the keys are present
    would have passed while all three defects below were live: the config's
    `ignore:` block WAS in the file, under a key the loader never looks at. So
    these go through KanbanConfig.load and KanbanService — the same path the CLI
    uses.
    """

    @staticmethod
    def _init(tmp_path, monkeypatch, theme="software"):
        monkeypatch.chdir(tmp_path)
        import subprocess

        subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, capture_output=True, check=True)
        runner = CliRunner()
        result = runner.invoke(main, ["init", "--theme", theme])
        assert result.exit_code == 0, result.output
        return result

    def test_scaffolded_template_declares_its_own_type(self, tmp_path, monkeypatch):
        """Each _TEMPLATE.md must carry `type:`, or a copy of it becomes a task.

        service.py defaults a missing `type` to "task", so a beginner who copies
        the scaffolded bug template — which is what the README tells them the
        templates are for — silently files a task.
        """
        self._init(tmp_path, monkeypatch)

        expected = {
            "features": "feature",
            "bugs": "bug",
            "epics": "epic",
            "tasks": "task",
            "ideas": "idea",
        }
        checked = 0
        for directory, type_name in expected.items():
            template = tmp_path / "kanban-work" / directory / "_TEMPLATE.md"
            assert template.exists(), f"missing template: {template}"
            frontmatter = template.read_text().split("---")[1]
            parsed = yaml.safe_load(frontmatter)
            assert parsed.get("type") == type_name, (
                f"{directory}/_TEMPLATE.md declares type={parsed.get('type')!r}, "
                f"expected {type_name!r} — a copy of it would be read as a task"
            )
            checked += 1
        assert checked == len(expected), "non-vacuity: not every template was examined"

    def test_scaffolded_config_ignore_survives_the_loader(self, tmp_path, monkeypatch):
        """The written ignore list must be the one KanbanConfig.load returns.

        The defect this pins: `init` wrote `ignore:` as a sibling of `paths:`
        while the loader reads `kanban.paths.ignore`, so the whole list was
        silently discarded and the loader's two-entry default applied instead.
        """
        from yurtle_kanban.config import KanbanConfig

        self._init(tmp_path, monkeypatch)
        config = KanbanConfig.load(tmp_path / ".kanban" / "config.yaml")

        assert "**/_TEMPLATE*" in config.paths.ignore, (
            f"loader sees ignore={config.paths.ignore!r} — the scaffolded entry was dropped, "
            "which means every custom ignore rule a user adds there is dropped too"
        )

    def test_scaffolded_templates_are_not_listed_as_work_items(self, tmp_path, monkeypatch):
        """A fresh board must be EMPTY.

        The user-visible consequence of the two defects above: `list` on an
        untouched board showed six phantom entries (FEAT-XXX, BUG-XXX, ...)
        before the user had created anything.
        """
        from yurtle_kanban.config import KanbanConfig
        from yurtle_kanban.service import KanbanService

        self._init(tmp_path, monkeypatch)

        # Non-vacuity: the templates must actually be on disk, or "no items"
        # would be trivially true and this test would prove nothing.
        templates = list((tmp_path / "kanban-work").rglob("_TEMPLATE.md"))
        assert len(templates) >= 5, f"expected scaffolded templates, found {len(templates)}"

        config = KanbanConfig.load(tmp_path / ".kanban" / "config.yaml")
        items = KanbanService(config, tmp_path).get_items()

        assert items == [], (
            f"a fresh board lists {len(items)} item(s): "
            f"{[i.id for i in items]} — these are the _TEMPLATE.md files"
        )

    def test_next_steps_teaches_the_safe_create_form(self, tmp_path, monkeypatch):
        """The post-init hint must carry --push.

        README.md states "Never create items without `--push`", so the first
        command the tool itself prints must not be the unsafe form.
        """
        result = self._init(tmp_path, monkeypatch)

        create_lines = [
            line for line in result.output.splitlines() if "yurtle-kanban create" in line
        ]
        assert create_lines, "non-vacuity: init printed no create example to check"
        for line in create_lines:
            assert "--push" in line, f"post-init hint omits --push: {line.strip()!r}"


class TestSkillsDoNotAssumeOneConsumersTree:
    """A skill shipped by a general-purpose tool must run in a stranger's project.

    This is a guard for a class, not for the two files that had it. The skills are
    installed verbatim into whatever repo runs `init`, so a path that only exists in
    one particular consumer's tree becomes an instruction the reader cannot follow —
    they run a command against a directory they do not have, and nothing tells them
    why. `grep`ping for today's two offenders would pass the moment someone writes a
    third; the scan below is over every skill we ship.

    The offenders that motivated it, all in the nautical theme, all naming
    nusy-product-team's own layout:

        skills/nautical/review/SKILL.md   pytest brain/tests/ ...  and the
                                          live-being-tests/ tier (7 lines)
        skills/nautical/done/SKILL.md     pytest brain/MODULE/tests/ ... (3 lines)

    NOT included, deliberately: a bare word like "brain" in prose, and a sample URL in
    a test fixture. The test is for PATHS a skill tells a reader to run against.
    """

    # Path fragments that belong to one consumer's tree rather than to any project
    # that installs this tool. Each is anchored so it matches a path, not a word.
    FOREIGN_TREE_PATHS = (
        "brain/",
        "live-being-tests",
        "beings/",
    )

    def test_no_shipped_skill_references_a_foreign_tree(self):
        from pathlib import Path

        # ⚠ THE REPOSITORY'S skills/, deliberately NOT _get_skills_dir().
        #
        # _get_skills_dir() resolves sys.prefix/share/yurtle-kanban/skills first —
        # the INSTALLED copy — so on any machine with the package installed this test
        # scans build output rather than source. The first version of this test did
        # exactly that and was vacuous: restoring the pre-fix SKILL.md with its seven
        # leaking lines left it GREEN, because the copy it was reading had been
        # refreshed by the install that ran after the edit.
        #
        # What is under test is what this repo SHIPS, and that is the source tree.
        skills_dir = Path(__file__).resolve().parent.parent / "skills"
        assert skills_dir.is_dir(), f"repo skills dir not found: {skills_dir}"

        skill_files = sorted(skills_dir.rglob("SKILL.md"))
        # Non-vacuity: a glob that matched nothing would pass this test while
        # proving nothing at all.
        assert len(skill_files) >= 15, (
            f"expected the shipped skill set, found {len(skill_files)} SKILL.md file(s) "
            f"under {skills_dir} — the scan below would be checking almost nothing"
        )

        offenders = []
        for path in skill_files:
            text = path.read_text(encoding="utf-8")
            for lineno, line in enumerate(text.splitlines(), start=1):
                for fragment in self.FOREIGN_TREE_PATHS:
                    if fragment in line:
                        offenders.append(
                            f"{path.relative_to(skills_dir)}:{lineno} contains {fragment!r}"
                            f" — {line.strip()[:90]}"
                        )

        assert not offenders, (
            "a shipped skill names a path from one consumer's tree; a stranger following it "
            "runs a command against a directory they do not have:\n  " + "\n  ".join(offenders)
        )


class TestInitWritesThemeRoot:
    """`init` writes the theme's own root as `root:` and scans it (#112).

    Decided in #109 (option A). Before: `init` defaulted `--path` to `work/`, wrote
    `root: work/`, created an empty `work/` nothing ever read, and listed one
    scan path per type. After: with no `--path`, `root:` is the common parent of
    the theme's per-type paths, `scan_paths` is exactly `[<root>]`, and no `work/`
    appears. An explicit `--path` still wins. Existing configs are untouched, and
    a fresh init + create + list works for every theme with files landing exactly
    where they did before. (#112)
    """

    THEMES_DIR = Path(__file__).resolve().parent.parent / "themes"

    # theme -> (a type of that theme, the ID prefix its file carries)
    PROBES = {
        "software": ("feature", "FEAT"),
        "nautical": ("expedition", "EXP"),
        "hdd": ("hypothesis", "H"),
    }

    @pytest.fixture(autouse=True)
    def _clear_theme_cache(self):
        import yurtle_kanban.config as config_mod

        config_mod._theme_cache.clear()
        yield
        config_mod._theme_cache.clear()

    # ---- helpers -------------------------------------------------------

    @classmethod
    def _theme_type_paths(cls, theme: str) -> dict[str, str]:
        data = yaml.safe_load((cls.THEMES_DIR / f"{theme}.yaml").read_text())
        return {
            type_id: type_def["path"]
            for type_id, type_def in (data.get("item_types") or {}).items()
            if type_def.get("path")
        }

    @classmethod
    def _theme_root(cls, theme: str) -> str:
        """The common parent of the theme's per-type paths, derived from its YAML."""
        paths = list(cls._theme_type_paths(theme).values())
        assert paths, f"non-vacuity: theme {theme!r} declares no per-type paths"
        return os.path.commonpath([p.rstrip("/") for p in paths]) + "/"

    @staticmethod
    def _norm(p: str) -> str:
        return str(p).strip().rstrip("/")

    @staticmethod
    def _run(tmp_path, monkeypatch, *args):
        monkeypatch.chdir(tmp_path)
        if not (tmp_path / ".git").exists():
            subprocess.run(
                ["git", "init", "-b", "main"], cwd=tmp_path, capture_output=True, check=True
            )
        result = CliRunner().invoke(main, list(args))
        assert result.exit_code == 0, (result.output, result.exception)
        return result

    @classmethod
    def _listed_ids(cls, tmp_path, monkeypatch) -> list[str]:
        """IDs `list --json` reports (it prints a plain notice, not JSON, when empty)."""
        out = cls._run(tmp_path, monkeypatch, "list", "--json").output
        if "No work items found" in out:
            return []
        return [i["id"] for i in json.loads(out)]

    @staticmethod
    def _config_paths(tmp_path) -> dict:
        raw = yaml.safe_load((tmp_path / ".kanban" / "config.yaml").read_text())
        return raw["kanban"]["paths"]

    # ---- the change ----------------------------------------------------

    @pytest.mark.parametrize("theme", ["software", "nautical", "hdd"])
    def test_expected_theme_roots(self, theme):
        """Pin the derivation against the values the issue names, so it can't drift."""
        expected = {"software": "kanban-work/", "nautical": "kanban-work/", "hdd": "research/"}
        assert self._theme_root(theme) == expected[theme]

    @pytest.mark.parametrize("theme", ["software", "nautical", "hdd"])
    def test_default_init_writes_theme_root_as_root(self, tmp_path, monkeypatch, theme):
        self._run(tmp_path, monkeypatch, "init", "--theme", theme)
        paths = self._config_paths(tmp_path)
        assert self._norm(paths.get("root")) == self._norm(self._theme_root(theme)), (
            f"init --theme {theme} wrote root: {paths.get('root')!r}, "
            f"expected the theme's own root {self._theme_root(theme)!r}"
        )

    @pytest.mark.parametrize("theme", ["software", "nautical", "hdd"])
    def test_default_init_scans_exactly_the_root(self, tmp_path, monkeypatch, theme):
        self._run(tmp_path, monkeypatch, "init", "--theme", theme)
        scan_paths = self._config_paths(tmp_path).get("scan_paths") or []
        assert [self._norm(p) for p in scan_paths] == [self._norm(self._theme_root(theme))], (
            f"init --theme {theme} wrote scan_paths={scan_paths!r}, "
            f"expected exactly [{self._theme_root(theme)!r}]"
        )

    @pytest.mark.parametrize("theme", ["software", "nautical", "hdd"])
    def test_default_init_creates_no_stray_work_dir(self, tmp_path, monkeypatch, theme):
        self._run(tmp_path, monkeypatch, "init", "--theme", theme)
        assert not (tmp_path / "work").exists(), (
            f"init --theme {theme} created a stray work/ that nothing scans"
        )

    def test_explicit_path_wins(self, tmp_path, monkeypatch):
        self._run(tmp_path, monkeypatch, "init", "--theme", "software", "--path", "custom/")
        assert self._norm(self._config_paths(tmp_path).get("root")) == "custom"
        assert (tmp_path / "custom").is_dir(), "an explicit --path must be created"

    # ---- must stay true ------------------------------------------------

    @pytest.mark.parametrize("theme", ["software", "nautical", "hdd"])
    def test_fresh_init_create_list_round_trip(self, tmp_path, monkeypatch, theme):
        """create lands exactly where it does today and list finds it."""
        item_type, prefix = self.PROBES[theme]
        self._run(tmp_path, monkeypatch, "init", "--theme", theme)
        self._run(tmp_path, monkeypatch, "create", item_type, "probe")

        type_dir = self._theme_type_paths(theme)[item_type]
        expected_file = tmp_path / type_dir / f"{prefix}-001-probe.md"
        assert expected_file.is_file(), (
            f"created item not at {type_dir}{prefix}-001-probe.md; found "
            f"{[str(p.relative_to(tmp_path)) for p in glob_outside_git(tmp_path, '*probe*')]}"
        )

        listed = self._listed_ids(tmp_path, monkeypatch)
        assert listed == [f"{prefix}-001"], listed

    def test_spec_theme_fresh_init_create_list_round_trip(self, tmp_path, monkeypatch):
        """spec declares no per-type paths; init + create + list must still work."""
        assert self._theme_type_paths("spec") == {}, "non-vacuity: spec gained paths"
        self._run(tmp_path, monkeypatch, "init", "--theme", "spec")
        self._run(tmp_path, monkeypatch, "create", "task", "probe")
        listed = self._listed_ids(tmp_path, monkeypatch)
        assert listed == ["TASK-001"], listed

    @pytest.mark.parametrize("theme", ["software", "nautical", "hdd"])
    def test_scaffolded_type_dirs_and_templates_still_created(self, tmp_path, monkeypatch, theme):
        self._run(tmp_path, monkeypatch, "init", "--theme", theme)
        type_paths = self._theme_type_paths(theme)
        assert type_paths, "non-vacuity"
        for type_id, type_path in type_paths.items():
            assert (tmp_path / type_path).is_dir(), f"missing {type_path} ({type_id})"
            assert (tmp_path / type_path / "_TEMPLATE.md").is_file(), (
                f"missing {type_path}_TEMPLATE.md ({type_id})"
            )

    @pytest.mark.parametrize("theme", ["software", "nautical", "hdd"])
    def test_scaffolded_templates_not_listed(self, tmp_path, monkeypatch, theme):
        self._run(tmp_path, monkeypatch, "init", "--theme", theme)
        assert list(glob_outside_git(tmp_path, "_TEMPLATE.md")), "non-vacuity: no templates on disk"
        listed = self._listed_ids(tmp_path, monkeypatch)
        assert listed == [], f"fresh board lists {listed}"

    def test_reinit_is_idempotent(self, tmp_path, monkeypatch):
        """Pinned current behaviour: init (re)writes config.yaml unconditionally,
        so re-running it with the same theme yields the same config."""
        self._run(tmp_path, monkeypatch, "init", "--theme", "software")
        first = (tmp_path / ".kanban" / "config.yaml").read_text()
        self._run(tmp_path, monkeypatch, "init", "--theme", "software")
        assert (tmp_path / ".kanban" / "config.yaml").read_text() == first

    def test_hand_written_v1_config_still_works_untouched(self, tmp_path, monkeypatch):
        """An existing `root: work/` + per-type `kanban-work/*` config keeps working."""
        (tmp_path / ".kanban").mkdir()
        config_text = (
            "kanban:\n"
            "  theme: software\n"
            "  paths:\n"
            "    root: work/\n"
            "    scan_paths:\n"
            '      - "kanban-work/features/"\n'
            '      - "kanban-work/bugs/"\n'
            "    ignore:\n"
            '      - "**/_TEMPLATE*"\n'
        )
        config_file = tmp_path / ".kanban" / "config.yaml"
        config_file.write_text(config_text)
        (tmp_path / "kanban-work" / "features").mkdir(parents=True)
        (tmp_path / "kanban-work" / "bugs").mkdir(parents=True)

        self._run(tmp_path, monkeypatch, "create", "feature", "probe")
        assert (tmp_path / "kanban-work" / "features" / "FEAT-001-probe.md").is_file()
        listed = self._listed_ids(tmp_path, monkeypatch)
        assert listed == ["FEAT-001"], listed
        assert config_file.read_text() == config_text, "existing config was rewritten"

    # ---- round 2: a custom theme whose type folders sit at the repo root ----

    @classmethod
    def _write_flat_theme(cls, tmp_path) -> None:
        """A local `.kanban/themes/flat.yaml`: the software theme with its type
        folders moved to the repo root (`features/`, `bugs/`), so the per-type
        paths share NO common parent."""
        theme = yaml.safe_load((cls.THEMES_DIR / "software.yaml").read_text())
        theme["name"] = "Flat"
        theme["item_types"] = {
            "feature": {**theme["item_types"]["feature"], "path": "features/"},
            "bug": {**theme["item_types"]["bug"], "path": "bugs/"},
        }
        themes_dir = tmp_path / ".kanban" / "themes"
        themes_dir.mkdir(parents=True)
        (themes_dir / "flat.yaml").write_text(yaml.safe_dump(theme, sort_keys=False))

    def test_flat_custom_theme_create_lands_at_repo_root_folder(self, tmp_path, monkeypatch):
        """`create feature` lands at ./features/ exactly as before, not work/features/."""
        self._write_flat_theme(tmp_path)
        self._run(tmp_path, monkeypatch, "init", "--theme", "flat")
        assert (tmp_path / "features" / "_TEMPLATE.md").is_file(), (
            "non-vacuity: flat not scaffolded"
        )

        self._run(tmp_path, monkeypatch, "create", "feature", "probe")
        found = sorted(
            str(p.relative_to(tmp_path)) for p in glob_outside_git(tmp_path, "FEAT-001*")
        )
        assert found == ["features/FEAT-001-probe.md"], (
            f"flat theme: create feature landed at {found}, expected features/FEAT-001-probe.md"
        )
        assert self._listed_ids(tmp_path, monkeypatch) == ["FEAT-001"]

    def test_flat_custom_theme_scans_every_scaffolded_folder(self, tmp_path, monkeypatch):
        """Both scaffolded folders are scanned: a feature and a bug are both listed."""
        self._write_flat_theme(tmp_path)
        self._run(tmp_path, monkeypatch, "init", "--theme", "flat")
        self._run(tmp_path, monkeypatch, "create", "feature", "probe")
        self._run(tmp_path, monkeypatch, "create", "bug", "x")

        on_disk = sorted(
            str(p.relative_to(tmp_path))
            for p in glob_outside_git(tmp_path, "*.md")
            if p.name.startswith(("FEAT-", "BUG-"))
        )
        assert on_disk == ["bugs/BUG-001-x.md", "features/FEAT-001-probe.md"], on_disk
        assert sorted(self._listed_ids(tmp_path, monkeypatch)) == ["BUG-001", "FEAT-001"]

    def test_flat_custom_theme_never_scans_repo_root(self, tmp_path, monkeypatch):
        """No scan path may be the repo root: it would pull in .claude/**/*.md."""
        self._write_flat_theme(tmp_path)
        self._run(tmp_path, monkeypatch, "init", "--theme", "flat")
        scan_paths = self._config_paths(tmp_path).get("scan_paths") or []
        assert scan_paths, "non-vacuity: init wrote no scan_paths"
        bad = [p for p in scan_paths if self._norm(p) in ("", ".", "./")]
        assert not bad, f"init --theme flat scans the repo root: {scan_paths!r}"


class TestInitExplicitPathScaffolding:
    """`init --path <dir>` scaffolds the theme's type folders under that root (#134).

    Follow-up from the PR #131 (#112) review. Before: with an explicit
    `--path custom/`, `init` wrote `root: custom/` and scanned it, but still
    scaffolded `kanban-work/*/_TEMPLATE.md` from the theme's per-type paths,
    while `create` writes to `custom/<type folder>/` (#102/#113) -- leaving a
    `kanban-work/` tree nothing uses. Decided: scaffold each type folder (the
    theme's per-type path minus its first component, the theme root -- so
    `kw/x/features/` maps to `custom/x/features/`) and its `_TEMPLATE.md`
    under the explicit root, and create no `kanban-work/` (or `research/`)
    tree. With `--path .` the template and the item `create` writes must
    share one folder, and no scaffolded type folder may go unused.
    Also pinned (round-2 follow-up A): `init` creates no unscanned root, e.g.
    no `work/` for a flat custom theme. Default `init` (no `--path`) is
    unchanged. (#134)
    """

    H = TestInitWritesThemeRoot
    THEMES = ["software", "nautical", "hdd"]

    # What default `init --theme <t>` writes, recorded as literals from
    # origin/main before #134 (not derived from the code under test).
    # Type folders under the theme root, by type id:
    DEFAULT_TYPE_DIRS = {
        "software": {
            "feature": "kanban-work/features", "bug": "kanban-work/bugs",
            "epic": "kanban-work/epics", "issue": "kanban-work/issues",
            "task": "kanban-work/tasks", "idea": "kanban-work/ideas",
        },
        "nautical": {
            "expedition": "kanban-work/expeditions", "voyage": "kanban-work/voyages",
            "chore": "kanban-work/chores", "hazard": "kanban-work/hazards",
            "signal": "kanban-work/signals",
        },
        "hdd": {
            "idea": "research/ideas", "literature": "research/literature",
            "paper": "research/papers", "hypothesis": "research/hypotheses",
            "experiment": "research/experiments", "measure": "research/measures",
        },
    }
    # (ID prefix, sections) each type's _TEMPLATE.md carries.
    DEFAULT_TEMPLATE_PARTS = {
        "feature": ("FEAT", ["Goal", "Acceptance Criteria"]),
        "bug": ("BUG", ["Description", "Steps to Reproduce", "Expected Behavior",
                        "Actual Behavior"]),
        "epic": ("EPIC", ["Goal", "Scope", "Milestones"]),
        "issue": ("ISSUE", ["Description", "Context"]),
        "task": ("TASK", ["Goal", "Steps", "Acceptance Criteria"]),
        "expedition": ("EXP", ["Context", "Plan", "Definition of Done"]),
        "voyage": ("VOY", ["Vision", "Expeditions", "Success Criteria"]),
        "chore": ("CHORE", ["Description"]),
        "hazard": ("HAZ", ["Description", "Impact", "Mitigation"]),
        "signal": ("SIG", ["Observation", "Potential Value"]),
        "literature": ("LIT", ["Topic", "Search Strategy", "Key Findings", "Gaps",
                               "References"]),
        "paper": ("PAPER", ["Abstract", "Introduction", "Methodology", "Results",
                            "Conclusion"]),
        "hypothesis": ("H", ["Hypothesis Statement", "Target", "Rationale",
                             "Testable Predictions"]),
        "experiment": ("EXPR", ["Purpose", "Method", "Results", "Conclusion"]),
        "measure": ("M", ["Description", "Specification", "Collection Method"]),
    }
    DEFAULT_IDEA_PARTS = {  # `idea` differs by theme only in its prefix
        "software": ("IDEA", ["Description", "Motivation"]),
        "hdd": ("IDEA-R", ["Description", "Motivation"]),
    }
    TEMPLATE_FORMAT = (
        "---\n"
        "id: {prefix}-XXX\n"
        'title: ""\n'
        "type: {type_id}\n"
        "status: backlog\n"
        "created: YYYY-MM-DD\n"
        "priority: medium\n"
        "assignee:\n"
        "tags: []\n"
        "related: []\n"
        "---\n"
        "\n"
        "# {prefix}-XXX: Title\n"
        "\n"
        "{sections}"
    )
    DEFAULT_PATHS_BLOCK = (
        "  paths:\n"
        "    root: {root}/\n"
        "    scan_paths:\n"
        '    - "{root}/"\n'
        "\n"
        "    ignore:\n"
        '      - "**/archive/**"\n'
        '      - "**/templates/**"\n'
        '      - "**/_TEMPLATE*"\n'
    )

    @pytest.fixture(autouse=True)
    def _clear_theme_cache(self):
        import yurtle_kanban.config as config_mod

        config_mod._theme_cache.clear()
        yield
        config_mod._theme_cache.clear()

    # ---- helpers -------------------------------------------------------

    @classmethod
    def _type_folders(cls, theme: str) -> dict[str, str]:
        """type id -> the theme's per-type path minus its first component (the
        theme root), e.g. 'kanban-work/features/' -> 'features'."""
        folders = {
            type_id: Path(*Path(p.rstrip("/")).parts[1:]).as_posix()
            for type_id, p in cls.H._theme_type_paths(theme).items()
        }
        assert folders, f"non-vacuity: theme {theme!r} declares no per-type paths"
        return folders

    @classmethod
    def _init_custom(cls, tmp_path, monkeypatch, theme: str):
        return cls.H._run(tmp_path, monkeypatch, "init", "--theme", theme, "--path", "custom/")

    @classmethod
    def _expected_template(cls, theme: str, type_id: str) -> str:
        if type_id == "idea":
            prefix, sections = cls.DEFAULT_IDEA_PARTS[theme]
        else:
            prefix, sections = cls.DEFAULT_TEMPLATE_PARTS[type_id]
        return cls.TEMPLATE_FORMAT.format(
            prefix=prefix,
            type_id=type_id,
            sections="\n\n".join(f"## {s}\n" for s in sections),
        )

    @staticmethod
    def _created_paths(root: Path) -> list[str]:
        """Every path init created, minus .git/, .claude/ and .kanban/templates/*."""
        out = []
        # os.walk, pruning the root .git: rglob would descend into .git/objects,
        # which git may repack concurrently (#259).
        for dirpath, dirs, names in os.walk(root):
            if Path(dirpath) == root:
                dirs[:] = [d for d in dirs if d != ".git"]
            for name in dirs + names:
                f = Path(dirpath) / name
                parts = f.relative_to(root).parts
                if parts[0] in (".git", ".claude") or (
                    parts[:2] == (".kanban", "templates") and len(parts) > 2
                ):
                    continue
                out.append(f.relative_to(root).as_posix())
        return sorted(out)

    # ---- the change ----------------------------------------------------

    @pytest.mark.parametrize("theme", THEMES)
    def test_explicit_path_scaffolds_type_folders_under_root(self, tmp_path, monkeypatch, theme):
        self._init_custom(tmp_path, monkeypatch, theme)
        missing = [
            f"custom/{folder}/_TEMPLATE.md"
            for folder in self._type_folders(theme).values()
            if not (tmp_path / "custom" / folder / "_TEMPLATE.md").is_file()
        ]
        assert not missing, (
            f"init --theme {theme} --path custom/ did not scaffold {missing}; found "
            + str(sorted(
                str(p.relative_to(tmp_path)) for p in glob_outside_git(tmp_path, "_TEMPLATE.md")
            ))
        )

    @pytest.mark.parametrize("theme", THEMES)
    def test_explicit_path_creates_no_theme_root_tree(self, tmp_path, monkeypatch, theme):
        self._init_custom(tmp_path, monkeypatch, theme)
        theme_root = self.H._norm(self.H._theme_root(theme))
        assert theme_root != "custom", "non-vacuity"
        assert not (tmp_path / theme_root).exists(), (
            f"init --theme {theme} --path custom/ still created an unscanned "
            f"{theme_root}/ tree: "
            f"{sorted(str(p.relative_to(tmp_path)) for p in (tmp_path / theme_root).rglob('*'))}"
        )

    @pytest.mark.parametrize("theme", THEMES)
    def test_explicit_path_templates_match_default_templates(self, tmp_path, monkeypatch, theme):
        """The templates under custom/ are the same bytes default init writes."""
        default_dir = tmp_path / "default"
        custom_dir = tmp_path / "explicit"
        default_dir.mkdir()
        custom_dir.mkdir()
        self.H._run(default_dir, monkeypatch, "init", "--theme", theme)
        import yurtle_kanban.config as config_mod

        config_mod._theme_cache.clear()
        self._init_custom(custom_dir, monkeypatch, theme)
        folders = self._type_folders(theme)
        for type_id, type_path in self.H._theme_type_paths(theme).items():
            folder = folders[type_id]
            got = custom_dir / "custom" / folder / "_TEMPLATE.md"
            assert got.is_file(), f"missing custom/{folder}/_TEMPLATE.md ({type_id})"
            assert got.read_bytes() == (default_dir / type_path / "_TEMPLATE.md").read_bytes(), (
                f"custom/{folder}/_TEMPLATE.md differs from default {type_path}_TEMPLATE.md"
            )

    @pytest.mark.parametrize("theme", THEMES)
    def test_explicit_path_config_root_and_scan_paths(self, tmp_path, monkeypatch, theme):
        self._init_custom(tmp_path, monkeypatch, theme)
        paths = self.H._config_paths(tmp_path)
        assert self.H._norm(paths.get("root")) == "custom", paths
        assert [self.H._norm(p) for p in paths.get("scan_paths") or []] == ["custom"], paths

    @pytest.mark.parametrize("theme", THEMES)
    def test_explicit_path_create_lands_in_scaffolded_folder_and_lists(
        self, tmp_path, monkeypatch, theme
    ):
        item_type, prefix = self.H.PROBES[theme]
        folder = self._type_folders(theme)[item_type]
        self._init_custom(tmp_path, monkeypatch, theme)
        self.H._run(tmp_path, monkeypatch, "create", item_type, "probe")

        found = sorted(
            str(p.relative_to(tmp_path)) for p in glob_outside_git(tmp_path, f"{prefix}-001*")
        )
        assert found == [f"custom/{folder}/{prefix}-001-probe.md"], found
        # The folder create wrote into is the one init scaffolded.
        assert (tmp_path / "custom" / folder / "_TEMPLATE.md").is_file(), (
            f"create landed in custom/{folder}/ but init scaffolded no template there"
        )
        assert self.H._listed_ids(tmp_path, monkeypatch) == [f"{prefix}-001"]

    @pytest.mark.parametrize("theme", THEMES)
    def test_explicit_path_templates_not_listed(self, tmp_path, monkeypatch, theme):
        self._init_custom(tmp_path, monkeypatch, theme)
        assert list((tmp_path / "custom").rglob("_TEMPLATE.md")), (
            "non-vacuity: no templates scaffolded under custom/"
        )
        listed = self.H._listed_ids(tmp_path, monkeypatch)
        assert listed == [], f"fresh --path board lists {listed}"

    @pytest.mark.parametrize("theme", ["software", "nautical"])
    @pytest.mark.parametrize("dot", [".", "./"])
    def test_dot_path_template_and_item_share_a_folder(self, tmp_path, monkeypatch, theme, dot):
        """`init --path .` then `create` of every type: each item lands in the
        folder holding that type's _TEMPLATE.md, and no scaffolded type folder
        is left unused (PR #154 round-1 review)."""
        self.H._run(tmp_path, monkeypatch, "init", "--theme", theme, "--path", dot)
        type_ids = list(self.H._theme_type_paths(theme))
        assert type_ids, "non-vacuity"
        for type_id in type_ids:
            self.H._run(tmp_path, monkeypatch, "create", type_id, "probe")

        def rel(p: Path) -> str:
            return p.relative_to(tmp_path).as_posix()

        items = {
            f.parent
            for f in glob_outside_git(tmp_path, "*-001-probe.md")
            if rel(f).split("/")[0] not in (".git", ".claude", ".kanban")
        }
        templates = {
            f.parent
            for f in glob_outside_git(tmp_path, "_TEMPLATE.md")
            if rel(f).split("/")[0] not in (".git", ".claude", ".kanban")
        }
        assert len(items) == len(type_ids), sorted(rel(p) for p in items)
        stray_items = sorted(rel(p) for p in items - templates)
        unused_templates = sorted(rel(p) for p in templates - items)
        assert not stray_items and not unused_templates, (
            f"init --theme {theme} --path {dot}: items created in folders with no "
            f"_TEMPLATE.md {stray_items}; scaffolded type folders create never "
            f"writes to {unused_templates}"
        )

    def test_flat_custom_theme_creates_no_work_dir(self, tmp_path, monkeypatch):
        """Round-2 follow-up A: init creates no unscanned root (no work/)."""
        self.H._write_flat_theme(tmp_path)
        self.H._run(tmp_path, monkeypatch, "init", "--theme", "flat")
        assert (tmp_path / "features" / "_TEMPLATE.md").is_file(), (
            "non-vacuity: flat not scaffolded"
        )
        assert not (tmp_path / "work").exists(), (
            "init --theme flat created a work/ directory nothing scans"
        )

    # ---- must stay true ------------------------------------------------

    @pytest.mark.parametrize("theme", THEMES)
    def test_default_init_tree_unchanged(self, tmp_path, monkeypatch, theme):
        """Default init (no --path) scaffolds the same tree, template bytes and
        paths: block it did before #134 (literals recorded from origin/main)."""
        self.H._run(tmp_path, monkeypatch, "init", "--theme", theme)
        type_dirs = self.DEFAULT_TYPE_DIRS[theme]
        root = {d.split("/")[0] for d in type_dirs.values()}.pop()

        expected = {".kanban", ".kanban/config.yaml", ".kanban/templates",
                    ".kanban/workflows", root}
        for d in type_dirs.values():
            expected |= {d, f"{d}/_TEMPLATE.md"}
        assert self._created_paths(tmp_path) == sorted(expected), (
            f"default init --theme {theme} scaffolded a different tree"
        )

        for type_id, d in type_dirs.items():
            got = (tmp_path / d / "_TEMPLATE.md").read_text()
            assert got == self._expected_template(theme, type_id), (
                f"default init --theme {theme}: {d}/_TEMPLATE.md bytes changed"
            )

        config = (tmp_path / ".kanban" / "config.yaml").read_text()
        block = config[config.index("  paths:\n"):]
        assert block == self.DEFAULT_PATHS_BLOCK.format(root=root), block

    @pytest.mark.parametrize("theme", THEMES)
    def test_default_init_scaffolds_under_theme_root(self, tmp_path, monkeypatch, theme):
        self.H._run(tmp_path, monkeypatch, "init", "--theme", theme)
        for type_id, type_path in self.H._theme_type_paths(theme).items():
            assert (tmp_path / type_path / "_TEMPLATE.md").is_file(), (type_id, type_path)
        assert not (tmp_path / "custom").exists()
        paths = self.H._config_paths(tmp_path)
        assert self.H._norm(paths.get("root")) == self.H._norm(self.H._theme_root(theme))

    def test_spec_theme_explicit_path_create_and_list(self, tmp_path, monkeypatch):
        """spec declares no per-type paths; --path custom/ + create + list still works."""
        assert self.H._theme_type_paths("spec") == {}, "non-vacuity: spec gained paths"
        self.H._run(tmp_path, monkeypatch, "init", "--theme", "spec", "--path", "custom/")
        self.H._run(tmp_path, monkeypatch, "create", "task", "probe")
        found = [str(p.relative_to(tmp_path)) for p in glob_outside_git(tmp_path, "TASK-001*")]
        assert len(found) == 1 and found[0].startswith("custom/"), found
        assert self.H._listed_ids(tmp_path, monkeypatch) == ["TASK-001"]
