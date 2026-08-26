"""Tests for the init command scaffolding (Issue #7)."""

import yaml
from pathlib import Path
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
        """Generated config should include scan_paths for all type dirs."""
        monkeypatch.chdir(tmp_path)
        import subprocess
        subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, capture_output=True, check=True)

        runner = CliRunner()
        runner.invoke(main, ["init", "--theme", "software"])

        config_text = (tmp_path / ".kanban" / "config.yaml").read_text()
        assert "kanban-work/features/" in config_text
        assert "kanban-work/bugs/" in config_text

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
