"""Tests for the init command scaffolding (Issue #7)."""

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from yurtle_kanban.cli import main, _get_templates_dir, _get_skills_dir


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
                            f"{path.relative_to(skills_dir)}:{lineno} contains {fragment!r} — {line.strip()[:90]}"
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
            f"{[str(p.relative_to(tmp_path)) for p in tmp_path.rglob('*probe*')]}"
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
        assert list(tmp_path.rglob("_TEMPLATE.md")), "non-vacuity: no templates on disk"
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
