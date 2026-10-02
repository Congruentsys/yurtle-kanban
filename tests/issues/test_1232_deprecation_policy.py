"""Issue #1232 (E) — a deprecation policy, and a release check that a major's removals
each have an upgrade-guide entry.

Decided design:
1. `scripts/check_upgrade_guide.py X.Y.Z [--changelog PATH] [--guide PATH]` (defaults: the
   repo's CHANGELOG.md and UPGRADING.md). Module API:
   `check(changelog_text, version, guide_text: str | None) -> list[str]` — the problems,
   empty means OK.
   - Only a major (X.0.0, X >= 1) is checked; a minor or patch returns [].
   - Entries needing a guide entry: every top-level `- ` entry of the version's
     `### Removed`, plus every top-level entry in ANY section containing `**Breaking`.
     Indented sub-bullets belong to their entry.
   - Satisfied when the entry's FIRST `#N` appears in the guide as a whole `#N`
     (`#58` does not match `#580`).
   - An entry with no `#N` is a problem; a missing guide (None) is ONE problem naming
     UPGRADING.md; a version not in the changelog raises ValueError.
   - CLI: exit 0 with no problems, 1 otherwise (problems on stderr); an unknown version
     exits 1 with a message on stderr.
2. CONTRIBUTING.md `### Deprecation policy`: deprecate in a minor first (keeps working,
   warns once on stderr with the replacement, listed under Deprecated); the major removes
   it and adds an upgrade guide (UPGRADING.md) entry; "no aliases kept" needs the
   Captain's explicit ruling; the release check is scripts/check_upgrade_guide.py.
3. CLAUDE.md's `### Versioning` points to the deprecation policy.
4. The repo-local release skill runs `python scripts/check_upgrade_guide.py X.Y.Z` for a
   major, non-zero = stop, before the release is committed.
"""
from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_upgrade_guide.py"
CONTRIBUTING = ROOT / "CONTRIBUTING.md"
CLAUDE_MD = ROOT / "CLAUDE.md"
LOCAL_SKILL = ROOT / ".claude" / "skills" / "release-yurtle-kanban" / "SKILL.md"


def _module():
    assert SCRIPT.exists(), f"{SCRIPT.relative_to(ROOT)} does not exist"
    spec = importlib.util.spec_from_file_location("check_upgrade_guide", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    assert SCRIPT.exists(), f"{SCRIPT.relative_to(ROOT)} does not exist"
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT, capture_output=True, text=True, timeout=60,
    )


HEADER = "# Changelog\n\n## [Unreleased]\n\n"
OLD = "## [2.2.0] - 2026-01-01\n\n### Removed\n\n- OLDREMOVAL gone long ago (#7).\n"


def _changelog(version: str, body: str) -> str:
    return HEADER + f"## [{version}] - 2026-10-02\n\n" + body + "\n" + OLD


MAJOR_REMOVED = (
    "### Added\n\n"
    "- A new thing (#900).\n\n"
    "### Removed\n\n"
    "- `--old-flag` is gone; use `--new-flag` (#580).\n"
    "- The `legacy` command is removed (#612, see also #700).\n"
)


# --- module API --------------------------------------------------------------------------


def test_major_with_removed_entries_all_covered_is_ok() -> None:
    m = _module()
    guide = "# Upgrading\n\n## 2.x -> 3.0\n\n- #580: rename the flag.\n- #612: drop `legacy`.\n"
    assert m.check(_changelog("3.0.0", MAJOR_REMOVED), "3.0.0", guide) == []


def test_uncovered_removed_entry_is_a_problem_naming_its_issue() -> None:
    m = _module()
    guide = "# Upgrading\n\n- #580: rename the flag.\n"
    problems = m.check(_changelog("3.0.0", MAJOR_REMOVED), "3.0.0", guide)
    assert len(problems) == 1, problems
    assert "#612" in problems[0], problems
    assert "#580" not in problems[0], problems


def test_only_the_first_issue_reference_counts() -> None:
    # the `legacy` entry's FIRST reference is #612; #700 in the guide does not cover it
    m = _module()
    guide = "# Upgrading\n\n- #580: rename the flag.\n- #700: something else.\n"
    problems = m.check(_changelog("3.0.0", MAJOR_REMOVED), "3.0.0", guide)
    assert len(problems) == 1 and "#612" in problems[0], problems


def test_issue_number_matches_whole_number_only() -> None:
    m = _module()
    body = "### Removed\n\n- `--x` is removed (#58).\n"
    guide = "# Upgrading\n\n- #580: unrelated.\n- #1058: unrelated.\n"
    problems = m.check(_changelog("3.0.0", body), "3.0.0", guide)
    assert len(problems) == 1 and "#58" in problems[0], problems
    # and the bare number without `#` is not a reference either
    assert m.check(_changelog("3.0.0", body), "3.0.0", "see issue 58\n") != []
    # while the exact reference, followed by punctuation, does cover it
    assert m.check(_changelog("3.0.0", body), "3.0.0", "Covered by #58.\n") == []


def test_breaking_entry_in_changed_is_required_and_its_sub_bullets_are_not_entries() -> None:
    m = _module()
    body = (
        "### Changed\n\n"
        "- **Breaking:** `status` values are renamed (#640).\n"
        "  - `in_progress` becomes `in-progress`, no issue number here\n"
        "  - see #9999 for the background\n"
        "- A harmless change with no number at all.\n"
    )
    # nothing in the guide: exactly the one Breaking entry is a problem
    problems = m.check(_changelog("3.0.0", body), "3.0.0", "# Upgrading\n")
    assert len(problems) == 1, problems
    assert "#640" in problems[0], problems
    # the guide covering #640 satisfies it; #9999 (a sub-bullet's) is not needed
    assert m.check(_changelog("3.0.0", body), "3.0.0", "- #640: rename statuses.\n") == []
    # a sub-bullet's reference does not stand in for the entry's first one
    assert m.check(_changelog("3.0.0", body), "3.0.0", "- #9999: background.\n") != []


def test_breaking_entry_in_removed_is_checked_once() -> None:
    m = _module()
    body = "### Removed\n\n- **Breaking:** `--y` is removed (#641).\n"
    problems = m.check(_changelog("3.0.0", body), "3.0.0", "# Upgrading\n")
    assert len(problems) == 1 and "#641" in problems[0], problems


def test_entry_without_issue_number_is_a_problem() -> None:
    m = _module()
    body = "### Removed\n\n- `--z` is removed, no reference.\n"
    problems = m.check(_changelog("3.0.0", body), "3.0.0", "# Upgrading\n\n- #1 something\n")
    assert len(problems) == 1, problems
    assert "issue" in problems[0].lower(), problems
    assert "--z" in problems[0], "the problem should quote the entry it is about"


def test_missing_guide_is_one_problem_naming_upgrading_md() -> None:
    m = _module()
    problems = m.check(_changelog("3.0.0", MAJOR_REMOVED), "3.0.0", None)
    assert len(problems) == 1, problems
    assert "UPGRADING.md" in problems[0], problems


def test_major_with_nothing_to_guide_needs_no_guide() -> None:
    m = _module()
    body = "### Added\n\n- A thing (#901).\n\n### Fixed\n\n- A fix (#902).\n"
    assert m.check(_changelog("4.0.0", body), "4.0.0", None) == []


def test_other_versions_entries_are_not_checked() -> None:
    m = _module()
    # OLD (2.2.0) has an uncovered Removed entry; it must not leak into 3.0.0's check
    body = "### Added\n\n- A thing (#901).\n"
    assert m.check(_changelog("3.0.0", body), "3.0.0", "# Upgrading\n") == []


@pytest.mark.parametrize("version", ["3.3.0", "3.0.1", "0.9.0"])
def test_minor_or_patch_is_not_checked(version: str) -> None:
    m = _module()
    assert m.check(_changelog(version, MAJOR_REMOVED), version, None) == []
    assert m.check(_changelog(version, MAJOR_REMOVED), version, "# Upgrading\n") == []


def test_zero_major_is_not_a_major() -> None:
    m = _module()
    assert m.check(_changelog("0.0.0", MAJOR_REMOVED), "0.0.0", None) == []


def test_unknown_version_raises() -> None:
    m = _module()
    with pytest.raises(ValueError):
        m.check(_changelog("3.0.0", MAJOR_REMOVED), "5.0.0", "# Upgrading\n")


# --- CLI ---------------------------------------------------------------------------------


def _files(tmp_path: Path, changelog: str, guide: str | None) -> list[str]:
    cl = tmp_path / "CHANGELOG.md"
    cl.write_text(changelog, encoding="utf-8")
    gp = tmp_path / "UPGRADING.md"
    if guide is not None:
        gp.write_text(guide, encoding="utf-8")
    return ["--changelog", str(cl), "--guide", str(gp)]


def test_cli_ok_exits_zero(tmp_path: Path) -> None:
    args = _files(tmp_path, _changelog("3.0.0", MAJOR_REMOVED), "- #580\n- #612\n")
    r = _run("3.0.0", *args)
    assert r.returncode == 0, r.stderr
    assert "#612" not in r.stderr


def test_cli_problem_exits_one_with_problem_on_stderr(tmp_path: Path) -> None:
    args = _files(tmp_path, _changelog("3.0.0", MAJOR_REMOVED), "- #580\n")
    r = _run("3.0.0", *args)
    assert r.returncode == 1, (r.stdout, r.stderr)
    assert "#612" in r.stderr


def test_cli_missing_guide_file_exits_one_naming_upgrading_md(tmp_path: Path) -> None:
    args = _files(tmp_path, _changelog("3.0.0", MAJOR_REMOVED), None)
    r = _run("3.0.0", *args)
    assert r.returncode == 1, (r.stdout, r.stderr)
    assert "UPGRADING.md" in r.stderr


def test_cli_minor_exits_zero_and_says_the_check_is_for_majors(tmp_path: Path) -> None:
    args = _files(tmp_path, _changelog("3.3.0", MAJOR_REMOVED), None)
    r = _run("3.3.0", *args)
    assert r.returncode == 0, r.stderr
    assert "major" in (r.stdout + r.stderr).lower()


def test_cli_unknown_version_exits_one_with_message(tmp_path: Path) -> None:
    args = _files(tmp_path, _changelog("3.0.0", MAJOR_REMOVED), "- #580\n- #612\n")
    r = _run("5.0.0", *args)
    assert r.returncode == 1, (r.stdout, r.stderr)
    assert "5.0.0" in r.stderr
    assert "Traceback" not in r.stderr


def test_script_reuses_release_notes_section_parsing() -> None:
    assert SCRIPT.exists(), f"{SCRIPT.relative_to(ROOT)} does not exist"
    src = SCRIPT.read_text(encoding="utf-8")
    assert "release_notes" in src, "reuse scripts/release_notes.py's _section/_subsections"
    assert "_subsections" in src and "_section" in src


# --- docs --------------------------------------------------------------------------------


def _md_section(text: str, heading: str) -> str:
    m = re.search(rf"^{re.escape(heading)}[ \t]*$(.*?)(?=^#{{1,3}} |\Z)", text, re.M | re.S)
    assert m, f"no {heading!r} section"
    return m.group(1)


def test_contributing_has_deprecation_policy() -> None:
    sec = _md_section(CONTRIBUTING.read_text(encoding="utf-8"), "### Deprecation policy")
    low = sec.lower()
    for needle in (
        "minor",            # deprecate in a minor release first
        "warn",             # warns ...
        "stderr",           # ... on stderr
        "replacement",      # ... naming the replacement
        "deprecated",       # listed under Deprecated
        "major",            # the major removes it
        "upgrading.md",     # and adds an upgrade-guide entry
        "upgrade guide",
        "no aliases kept",
        "captain",          # needs the Captain's explicit ruling
        "scripts/check_upgrade_guide.py",
    ):
        assert needle in low, f"CONTRIBUTING's Deprecation policy does not mention {needle!r}"
    assert "once" in low, "the deprecation warning is printed once"


def test_claude_md_versioning_points_to_deprecation_policy() -> None:
    sec = _md_section(CLAUDE_MD.read_text(encoding="utf-8"), "### Versioning")
    low = sec.lower()
    assert "deprecation policy" in low, "CLAUDE.md's Versioning does not point to the policy"
    assert "contributing.md" in low


def test_release_skill_runs_the_check_before_committing() -> None:
    body = LOCAL_SKILL.read_text(encoding="utf-8")
    cmd = "python scripts/check_upgrade_guide.py X.Y.Z"
    assert cmd in body, f"the repo-local release skill does not run {cmd!r}"
    commit = re.search(r"^### \d+\. Commit the Release", body, re.M)
    assert commit, "no 'Commit the Release' step"
    assert body.index(cmd) < commit.start(), "the upgrade-guide check must come before the commit"


def test_release_skill_check_is_for_majors_and_nonzero_stops() -> None:
    body = LOCAL_SKILL.read_text(encoding="utf-8")
    i = body.find("check_upgrade_guide.py")
    assert i >= 0, "the repo-local release skill does not mention check_upgrade_guide.py"
    # the paragraph(s) around the mention say it is for a major, and non-zero stops
    near = body[max(0, i - 600) : i + 600].lower()
    assert "major" in near
    assert "non-zero" in near and "stop" in near
