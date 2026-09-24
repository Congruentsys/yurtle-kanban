"""Issue #178 — CHANGELOG fragments in `changelog.d/`, assembled at release time.

Decided design: each PR adds `changelog.d/<N>.md` (or `<N>-<anything>.md`) whose first
line is `<!-- section: Fixed -->` (Added, Changed, Deprecated, Removed, Fixed or
Security) and whose rest is the bullet, used verbatim. `scripts/assemble_changelog.py
VERSION [--date D] [--changelog P] [--fragments DIR]` inserts `## [VERSION] - DATE`
right after `## [Unreleased]` (kept, now empty), holding the Unreleased entries merged
with the fragments: per section, Unreleased entries first, then fragments by issue
number numerically; sections in Keep-a-Changelog order. Consumed fragments are deleted;
README.md and non-matching files are ignored. Nothing to release -> exit 0, no change.
An existing `## [VERSION]` or a bad fragment -> non-zero exit, nothing changed.
"""
from __future__ import annotations

import datetime
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts/assemble_changelog.py"
DATE = "2026-09-24"

HEADER = (
    "# Changelog\n\n"
    "All notable changes are documented here.\n\n"
)
UNRELEASED = (
    "## [Unreleased]\n\n"
    "### Fixed\n\n"
    "- **Existing fix** already under Unreleased (#1).\n\n"
    "### Added\n\n"
    "- **Existing add** already under Unreleased (#2).\n\n"
)
OLD = (
    "## [1.0.0] - 2026-01-01\n\n"
    "### Fixed\n\n"
    "- Old released fix.\n\n"
    "## [0.9.0] - 2025-12-01\n\n"
    "### Added\n\n"
    "- Older feature.\n"
)
README = "# changelog.d\n\nOne fragment per PR.\n"


def run(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    assert SCRIPT.exists(), f"{SCRIPT.relative_to(REPO)} does not exist"
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=tmp_path, capture_output=True, text=True, timeout=60,
    )


def frag(section: str, body: str) -> str:
    return f"<!-- section: {section} -->\n{body}\n"


def setup(tmp_path: Path, changelog: str, fragments: dict[str, str]) -> tuple[Path, Path]:
    cl = tmp_path / "CHANGELOG.md"
    cl.write_text(changelog)
    d = tmp_path / "changelog.d"
    d.mkdir()
    (d / "README.md").write_text(README)
    for name, text in fragments.items():
        (d / name).write_text(text)
    return cl, d


def release(tmp_path: Path, version: str = "1.1.0") -> subprocess.CompletedProcess[str]:
    return run(tmp_path, version, "--date", DATE, "--changelog", str(tmp_path / "CHANGELOG.md"),
               "--fragments", str(tmp_path / "changelog.d"))


def section(text: str, heading: str) -> str:
    """The body of the `## [...]` section starting with `heading`, up to the next `## [`."""
    start = text.index(heading) + len(heading)
    end = text.find("\n## [", start)
    return text[start:] if end == -1 else text[start:end]


FRAGS = {
    "10.md": frag("Fixed", "- **Ten** fixed (#10)."),
    "9.md": frag("Fixed", "- **Nine** fixed (#9)."),
    "12-b.md": frag("Added", "- **Twelve B** added (#12)."),
    "12.md": frag("Added", "- **Twelve** added (#12)."),
    "3.md": frag("Security", "- **Three** secured (#3)."),
    "7.md": frag("Changed", "- **Seven** changed (#7),\n  over two lines.   "),
}


class TestAssembleFragments:
    """Fragments + Unreleased entries become a new version section (#178)."""

    def test_assembles_grouped_and_ordered(self, tmp_path: Path) -> None:
        cl, _ = setup(tmp_path, HEADER + UNRELEASED + OLD, FRAGS)
        r = release(tmp_path)
        assert r.returncode == 0, r.stderr
        text = cl.read_text()
        assert text.startswith(HEADER + "## [Unreleased]\n\n## [1.1.0] - 2026-09-24\n"), text
        new = section(text, "## [1.1.0] - 2026-09-24")
        # Keep-a-Changelog section order
        pos = [new.index(f"### {s}\n") for s in ("Added", "Changed", "Fixed", "Security")]
        assert pos == sorted(pos), new
        # per section: Unreleased entries first, then fragments by issue number numerically
        fixed = new[new.index("### Fixed"):new.index("### Security")]
        assert fixed.index("Existing fix") < fixed.index("**Nine**") < fixed.index("**Ten**")
        added = new[new.index("### Added"):new.index("### Changed")]
        assert added.index("Existing add") < added.index("**Twelve**") \
            < added.index("**Twelve B**")
        # fragment body verbatim, trailing whitespace trimmed
        assert "- **Seven** changed (#7),\n  over two lines.\n" in new
        assert "over two lines.   " not in text
        assert "<!-- section" not in text

    def test_unreleased_left_empty(self, tmp_path: Path) -> None:
        cl, _ = setup(tmp_path, HEADER + UNRELEASED + OLD, FRAGS)
        assert release(tmp_path).returncode == 0
        unrel = section(cl.read_text(), "## [Unreleased]")
        assert unrel.strip() == "", f"Unreleased is not empty after the release: {unrel!r}"

    def test_older_sections_byte_identical(self, tmp_path: Path) -> None:
        cl, _ = setup(tmp_path, HEADER + UNRELEASED + OLD, FRAGS)
        assert release(tmp_path).returncode == 0
        text = cl.read_text()
        assert text[text.index("## [1.0.0]"):] == OLD

    def test_fragments_deleted_readme_and_others_kept(self, tmp_path: Path) -> None:
        extra = {"notes.txt": "not a fragment\n", "abc.md": frag("Fixed", "- **Abc**.")}
        cl, d = setup(tmp_path, HEADER + UNRELEASED + OLD, {**FRAGS, **extra})
        assert release(tmp_path).returncode == 0
        assert sorted(p.name for p in d.iterdir()) == ["README.md", "abc.md", "notes.txt"]
        assert (d / "README.md").read_text() == README
        text = cl.read_text()
        assert "**Abc**" not in text and "not a fragment" not in text
        assert "One fragment per PR" not in text

    def test_fragments_only(self, tmp_path: Path) -> None:
        cl, d = setup(tmp_path, HEADER + "## [Unreleased]\n\n" + OLD,
                      {"5.md": frag("Fixed", "- **Five** (#5).")})
        assert release(tmp_path).returncode == 0
        text = cl.read_text()
        new = section(text, "## [1.1.0] - 2026-09-24")
        assert "### Fixed" in new and "- **Five** (#5)." in new
        assert not (d / "5.md").exists()

    def test_unreleased_only_no_fragments(self, tmp_path: Path) -> None:
        cl, _ = setup(tmp_path, HEADER + UNRELEASED + OLD, {})
        assert release(tmp_path).returncode == 0
        new = section(cl.read_text(), "## [1.1.0] - 2026-09-24")
        assert "Existing fix" in new and "Existing add" in new

    def test_defaults_relative_to_cwd_and_today(self, tmp_path: Path) -> None:
        cl, d = setup(tmp_path, HEADER + UNRELEASED + OLD, {"4.md": frag("Fixed", "- **Four**.")})
        r = run(tmp_path, "1.1.0")
        assert r.returncode == 0, r.stderr
        today = datetime.date.today().isoformat()
        assert f"## [1.1.0] - {today}\n" in cl.read_text()
        assert not (d / "4.md").exists()


class TestAssembleNoOpAndRefusals:
    """Nothing to release, an existing version, a bad fragment (#178)."""

    def test_nothing_to_release_is_noop(self, tmp_path: Path) -> None:
        before = HEADER + "## [Unreleased]\n\n" + OLD
        cl, d = setup(tmp_path, before, {})
        r = release(tmp_path)
        assert r.returncode == 0, r.stderr
        assert "nothing to release" in (r.stdout + r.stderr).lower()
        assert cl.read_text() == before
        assert (d / "README.md").exists()

    def test_idempotent_second_run_changes_nothing(self, tmp_path: Path) -> None:
        cl, _ = setup(tmp_path, HEADER + UNRELEASED + OLD, FRAGS)
        assert release(tmp_path).returncode == 0
        after_first = cl.read_text()
        release(tmp_path)
        assert cl.read_text() == after_first

    def test_existing_version_refused(self, tmp_path: Path) -> None:
        cl, d = setup(tmp_path, HEADER + UNRELEASED + OLD, {"8.md": frag("Fixed", "- **Eight**.")})
        r = release(tmp_path, "1.0.0")
        assert r.returncode != 0, "releasing an existing version was not refused"
        assert cl.read_text() == HEADER + UNRELEASED + OLD
        assert (d / "8.md").exists()

    def test_missing_section_line_refused(self, tmp_path: Path) -> None:
        good = frag("Fixed", "- **Good**.")
        cl, d = setup(tmp_path, HEADER + UNRELEASED + OLD,
                      {"6.md": good, "11.md": "- **No section line** (#11).\n"})
        r = release(tmp_path)
        assert r.returncode != 0
        assert "11.md" in r.stdout + r.stderr
        assert cl.read_text() == HEADER + UNRELEASED + OLD
        assert (d / "6.md").read_text() == good and (d / "11.md").exists()

    def test_unknown_section_refused(self, tmp_path: Path) -> None:
        cl, d = setup(tmp_path, HEADER + UNRELEASED + OLD,
                      {"6.md": frag("Fixed", "- **Good**."), "13.md": frag("Bugs", "- **Bad**.")})
        r = release(tmp_path)
        assert r.returncode != 0
        assert "13.md" in r.stdout + r.stderr
        assert cl.read_text() == HEADER + UNRELEASED + OLD
        assert (d / "6.md").exists() and (d / "13.md").exists()


class TestAssembleNoUnreleasedHeading:
    """A CHANGELOG without `## [Unreleased]` gets one, plus the new section (#178)."""

    def test_inserted_before_first_release(self, tmp_path: Path) -> None:
        cl, _ = setup(tmp_path, HEADER + OLD, {"5.md": frag("Fixed", "- **Five**.")})
        assert release(tmp_path).returncode == 0
        text = cl.read_text()
        assert text.startswith(HEADER), text
        assert text.endswith(OLD), text
        i_unrel, i_new = text.index("## [Unreleased]"), text.index("## [1.1.0] - 2026-09-24")
        assert i_unrel < i_new < text.index("## [1.0.0]")
        assert "- **Five**." in section(text, "## [1.1.0] - 2026-09-24")

    def test_appended_when_no_release_heading(self, tmp_path: Path) -> None:
        cl, _ = setup(tmp_path, HEADER, {"5.md": frag("Fixed", "- **Five**.")})
        assert release(tmp_path).returncode == 0
        text = cl.read_text()
        assert text.startswith(HEADER), text
        assert text.index("## [Unreleased]") < text.index("## [1.1.0] - 2026-09-24")
        assert "- **Five**." in text


class TestFragmentDocs:
    """The docs point contributors at fragments; the shipped release skill is untouched."""

    def test_pairit_mentions_changelog_d(self) -> None:
        assert "changelog.d/" in (REPO / ".claude/skills/pairit/SKILL.md").read_text()

    def test_changelog_header_mentions_changelog_d(self) -> None:
        text = (REPO / "CHANGELOG.md").read_text()
        assert "## [Unreleased]" in text
        assert "changelog.d/" in text[:text.index("## [Unreleased]")]

    def test_changelog_d_readme_exists(self) -> None:
        assert (REPO / "changelog.d/README.md").is_file()

    def test_contributing_mentions_assemble_script(self) -> None:
        assert "scripts/assemble_changelog.py" in (REPO / "CONTRIBUTING.md").read_text()

    def test_release_skill_unchanged(self) -> None:  # control
        main = subprocess.run(
            ["git", "-C", str(REPO), "show", "origin/main:skills/release/SKILL.md"],
            capture_output=True, check=True,
        ).stdout
        assert (REPO / "skills/release/SKILL.md").read_bytes() == main
