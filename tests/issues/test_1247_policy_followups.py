"""Issue #1247 — follow-ups to the deprecation policy (#1232) and
`scripts/check_upgrade_guide.py`.

Decided shape:
1. CONTRIBUTING's `### Deprecation policy` says the check matches each entry's FIRST `#N`.
2. The policy says every removal is listed under `### Removed` or marked `**Breaking`.
3. `scripts/release_notes.py` makes its helpers public: `section()` and `subsections()`;
   `check_upgrade_guide.py` uses the public names (test_1232's source test is a ruled edit).
4. `check()` / the CLI:
   - an unreadable guide (a directory, or a file without read permission) exits 1, no
     traceback;
   - a `**Breaking` entry in a third section (`### Fixed`) is required;
   - in the GUIDE, an inline-code `` `#58` `` still counts as a mention of #58, but an
     HTML entity `&#39;` does NOT count as a mention of #39.
5. Pre-release majors (`4.0.0rc1`) are not majors: documented, no behaviour change.
"""
from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "check_upgrade_guide.py"
RELEASE_NOTES = ROOT / "scripts" / "release_notes.py"
CONTRIBUTING = ROOT / "CONTRIBUTING.md"


def _load(path: Path, name: str):
    assert path.exists(), f"{path.relative_to(ROOT)} does not exist"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _module():
    return _load(SCRIPT, "check_upgrade_guide")


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


def _md_section(text: str, heading: str) -> str:
    m = re.search(rf"^{re.escape(heading)}[ \t]*$(.*?)(?=^#{{1,3}} |\Z)", text, re.M | re.S)
    assert m, f"no {heading!r} section"
    return m.group(1)


def _policy() -> str:
    return _md_section(CONTRIBUTING.read_text(encoding="utf-8"), "### Deprecation policy")


# --- 1 + 2: the policy wording -----------------------------------------------------------


def test_policy_says_the_check_matches_the_first_issue_number() -> None:
    low = _policy().lower()
    # "first" close to "#N" or "issue number", either order
    near = re.search(
        r"first.{0,40}(#n|issue number)|(#n|issue number).{0,40}first", low, re.S
    )
    assert near, "the policy should say the check matches each entry's FIRST `#N`"


def test_policy_says_removals_go_under_removed_or_are_marked_breaking() -> None:
    sec = _policy()
    low = sec.lower()
    m = re.search(r"(every|each|any) remov\w*", low)
    assert m, "the policy should say every removal is listed under Removed or marked Breaking"
    near = low[m.start() : m.start() + 300]
    assert "removed" in near, "... is listed under `### Removed`"
    assert "breaking" in near, "... or marked `**Breaking`"
    assert re.search(r"\bor\b", near), "Removed OR Breaking"


# --- 3: public section helpers -----------------------------------------------------------


def test_release_notes_section_helpers_are_public() -> None:
    rn = _load(RELEASE_NOTES, "release_notes")
    assert callable(getattr(rn, "section", None)), "release_notes.section() should be public"
    assert callable(getattr(rn, "subsections", None)), (
        "release_notes.subsections() should be public"
    )
    heading, body = rn.section(_changelog("3.0.0", "### Removed\n\n- x (#1).\n"), "3.0.0")
    assert heading.startswith("## [3.0.0]")
    assert rn.subsections(body) == {"Removed": ["- x (#1)."]}


def test_check_upgrade_guide_uses_the_public_names() -> None:
    src = SCRIPT.read_text(encoding="utf-8")
    assert re.search(r"\.section\(", src), "check_upgrade_guide should call notes.section()"
    assert re.search(r"\.subsections\(", src), (
        "check_upgrade_guide should call notes.subsections()"
    )
    assert "._section(" not in src and "._subsections(" not in src, (
        "check_upgrade_guide still calls the private helpers"
    )


# --- 4: check() and the CLI --------------------------------------------------------------


MAJOR_REMOVED = "### Removed\n\n- `--old-flag` is gone (#580).\n"


def test_cli_guide_is_a_directory_exits_one_without_traceback(tmp_path: Path) -> None:
    cl = tmp_path / "CHANGELOG.md"
    cl.write_text(_changelog("3.0.0", MAJOR_REMOVED), encoding="utf-8")
    guide_dir = tmp_path / "UPGRADING.md"
    guide_dir.mkdir()
    r = _run("3.0.0", "--changelog", str(cl), "--guide", str(guide_dir))
    assert r.returncode == 1, (r.stdout, r.stderr)
    assert "Traceback" not in r.stderr, r.stderr
    assert r.stderr.strip(), "an unreadable guide should say so on stderr"


@pytest.mark.skipif(
    not hasattr(os, "geteuid") or os.geteuid() == 0, reason="root can read a mode-000 file"
)
def test_cli_guide_unreadable_file_exits_one_without_traceback(tmp_path: Path) -> None:
    cl = tmp_path / "CHANGELOG.md"
    cl.write_text(_changelog("3.0.0", MAJOR_REMOVED), encoding="utf-8")
    guide = tmp_path / "UPGRADING.md"
    guide.write_text("- #580\n", encoding="utf-8")
    guide.chmod(0)
    try:
        r = _run("3.0.0", "--changelog", str(cl), "--guide", str(guide))
    finally:
        guide.chmod(0o644)
    assert r.returncode == 1, (r.stdout, r.stderr)
    assert "Traceback" not in r.stderr, r.stderr
    assert r.stderr.strip(), "an unreadable guide should say so on stderr"


def test_breaking_entry_in_fixed_is_required() -> None:
    m = _module()
    body = (
        "### Added\n\n- A new thing (#900).\n\n"
        "### Fixed\n\n"
        "- **Breaking:** `list` no longer prints archived items (#650).\n"
        "- An ordinary fix (#651).\n"
    )
    problems = m.check(_changelog("3.0.0", body), "3.0.0", "# Upgrading\n")
    assert len(problems) == 1, problems
    assert "#650" in problems[0], problems
    assert m.check(_changelog("3.0.0", body), "3.0.0", "- #650: archived items.\n") == []


def test_inline_code_issue_number_in_guide_counts() -> None:
    m = _module()
    body = "### Removed\n\n- `--x` is removed (#58).\n"
    assert m.check(_changelog("3.0.0", body), "3.0.0", "- `#58`: use `--y`.\n") == []


@pytest.mark.parametrize("guide", [
    "# Upgrading\n\nIt&#39;s simple: use `--y`.\n",
    "# Upgrading\n\n&#39;\n",
    "# Upgrading\n\n- &#39;quoted&#39; (see #580)\n",
])
def test_html_entity_in_guide_is_not_an_issue_mention(guide: str) -> None:
    m = _module()
    body = "### Removed\n\n- `--x` is removed (#39).\n"
    problems = m.check(_changelog("3.0.0", body), "3.0.0", guide)
    assert len(problems) == 1 and "#39" in problems[0], (
        f"`&#39;` is an HTML entity, not a mention of #39: {problems}"
    )


def test_html_entity_does_not_hide_a_real_mention() -> None:
    m = _module()
    body = "### Removed\n\n- `--x` is removed (#39).\n"
    guide = "# Upgrading\n\n- #39: it&#39;s gone; use `--y`.\n"
    assert m.check(_changelog("3.0.0", body), "3.0.0", guide) == []


# --- 5: pre-release majors ---------------------------------------------------------------


@pytest.mark.parametrize("version", ["4.0.0rc1", "4.0.0-rc.1", "4.0.0a1", "4.0.0b2"])
def test_pre_release_major_is_not_a_major(version: str) -> None:
    assert _module().is_major(version) is False


def test_pre_release_majors_are_documented() -> None:
    doc = (_module().__doc__ or "").lower()
    policy = _policy().lower()
    pattern = r"pre-?release|release candidate|\brc\d?\b|4\.0\.0rc1"
    assert re.search(pattern, doc) or re.search(pattern, policy), (
        "the script's docstring or the policy should note pre-release majors are not checked"
    )
