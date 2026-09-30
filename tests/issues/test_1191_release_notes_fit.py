"""Issue #1191 — GitHub release notes that fit the 125,000-character release-body cap.

Decided design: `scripts/release_notes.py X.Y.Z [--changelog PATH]` prints release notes
for X.Y.Z on stdout (PATH defaults to the repo's CHANGELOG.md). Module API:
`release_notes(changelog_text, version, *, limit=LIMIT) -> str`, `LIMIT = 125_000`.

The section is `## [X.Y.Z] - DATE` up to the next `## [` heading, without the heading
line, trailing blank lines stripped. At most `limit` characters -> returned unchanged.
Otherwise CONDENSED notes: (a) per-section counts of top-level `- ` bullets
("12 Added, 3 Removed, ..."); (b) every top-level bullet containing `**Breaking`, in full
with sub-bullets, under a heading containing "Breaking"; (c) the whole `### Removed` and
`### Deprecated` sections; (d) a closing link containing `CHANGELOG.md` and the GitHub
anchor of the version heading (`#300---2026-09-30` for `## [3.0.0] - 2026-09-30`);
(e) at most `limit` characters. An unknown version, or condensed notes still over the
limit, raise ValueError (CLI: non-zero exit, message on stderr, nothing on stdout).
"""
from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "release_notes.py"


def _module():
    assert SCRIPT.exists(), f"{SCRIPT.relative_to(ROOT)} does not exist"
    spec = importlib.util.spec_from_file_location("release_notes", SCRIPT)
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


HEADER = "# Changelog\n\nAll notable changes are documented here.\n\n## [Unreleased]\n\n"
OLD = (
    "## [1.0.0] - 2026-01-01\n\n"
    "### Fixed\n\n"
    "- OLDVERSIONTEXT released fix (#1).\n"
)
SMALL_SECTION = (
    "### Added\n\n"
    "- **Small feature** does a thing (#10).\n"
    "  - with a detail\n\n"
    "### Fixed\n\n"
    "- A small fix (#11).\n"
)
SMALL = HEADER + "## [2.0.0] - 2026-09-30\n\n" + SMALL_SECTION + "\n\n" + OLD

BREAKING = (
    "- **Breaking: one actor identity** replaces the old flags (#580).\n"
    "  - BREAKSUB-ONE `--by` is gone\n"
    "  - BREAKSUB-TWO free text is safe\n"
    "    - BREAKSUB-DEEP nested further\n"
)
DEPRECATED = "- DEPRECATED-ENTRY `OldThing` warns now (#5).\n"
REMOVED = (
    "- REMOVED-ENTRY-ONE `WorkItem.blocks` (#6).\n"
    "  - REMOVED-SUB its parser too\n"
    "- REMOVED-ENTRY-TWO `id_formats` (#7).\n"
)


def _fixed(n: int, width: int = 60) -> str:
    lines = []
    for i in range(n):
        lines.append(f"- Fixed entry {i} " + "x" * width + f" (#{1000 + i}).\n")
        lines.append(f"  - sub-bullet of fixed entry {i}\n")
    return "".join(lines)


def _big(n_fixed: int, removed: str = REMOVED) -> str:
    section = (
        "### Added\n\n"
        "- Added one (#2).\n"
        "  - added sub\n"
        "- Added two (#3).\n\n"
        "### Changed\n\n"
        + BREAKING
        + "- A normal change (#4).\n\n"
        "### Deprecated\n\n"
        + DEPRECATED
        + "\n### Removed\n\n"
        + removed
        + "\n### Fixed\n\n"
        + _fixed(n_fixed)
    )
    return HEADER + "## [2.0.0] - 2026-09-30\n\n" + section + "\n" + OLD


def _section(text: str, version: str) -> str:
    m = re.search(rf"(?m)^## \[{re.escape(version)}\][^\n]*\n", text)
    assert m
    rest = text[m.end():]
    nxt = re.search(r"(?m)^## \[", rest)
    return rest[: nxt.start()] if nxt else rest


def _check_condensed(notes: str, n_fixed: int, limit: int) -> None:
    # (e)
    assert len(notes) <= limit, len(notes)
    # (a) top-level bullets only: sub-bullets are not counted
    for count, name in ((2, "Added"), (2, "Changed"), (1, "Deprecated"), (2, "Removed"),
                        (n_fixed, "Fixed")):
        assert re.search(rf"\b{count} {name}\b", notes), f"no '{count} {name}' in notes"
    # (b) the Breaking bullet, with every sub-bullet, under a Breaking heading
    assert "**Breaking: one actor identity**" in notes
    for sub in ("BREAKSUB-ONE", "BREAKSUB-TWO", "BREAKSUB-DEEP"):
        assert sub in notes
    heading = re.search(r"(?m)^#+ [^\n]*Breaking", notes)
    assert heading and heading.start() < notes.index("**Breaking: one actor identity**")
    # (c) Removed and Deprecated in full
    for s in ("DEPRECATED-ENTRY", "REMOVED-ENTRY-ONE", "REMOVED-SUB", "REMOVED-ENTRY-TWO"):
        assert s in notes
    # (d) closing link to the full changelog section
    tail = notes.rstrip()[-300:]
    assert "CHANGELOG.md" in tail
    assert "#200---2026-09-30" in tail
    # it is condensed, not the whole section
    assert "OLDVERSIONTEXT" not in notes
    assert "## [" not in notes


# 1 ---------------------------------------------------------------------------------


def test_small_section_is_returned_verbatim() -> None:
    notes = _module().release_notes(SMALL, "2.0.0")
    assert notes.strip() == SMALL_SECTION.strip()
    assert not notes.endswith("\n\n")
    assert "## [" not in notes
    assert "OLDVERSIONTEXT" not in notes


def test_last_section_in_file_is_returned_verbatim() -> None:
    notes = _module().release_notes(SMALL, "1.0.0")
    assert notes.strip() == "### Fixed\n\n- OLDVERSIONTEXT released fix (#1)."
    assert "## [" not in notes


def test_limit_constant_is_githubs_cap() -> None:
    assert _module().LIMIT == 125_000


# 2 ---------------------------------------------------------------------------------


def test_big_section_is_condensed_small_limit() -> None:
    text = _big(100)
    assert len(_section(text, "2.0.0")) > 2000
    notes = _module().release_notes(text, "2.0.0", limit=2000)
    _check_condensed(notes, 100, 2000)


def test_big_section_is_condensed_at_default_limit() -> None:
    mod = _module()
    text = _big(2000)
    assert len(_section(text, "2.0.0")) > 125_000
    notes = mod.release_notes(text, "2.0.0")
    _check_condensed(notes, 2000, 125_000)


def test_section_exactly_at_limit_is_unchanged() -> None:
    mod = _module()
    text = _big(100)
    full = mod.release_notes(text, "2.0.0", limit=10**9)
    assert mod.release_notes(text, "2.0.0", limit=len(full)) == full


# 3 ---------------------------------------------------------------------------------


def test_breaking_bullet_keeps_all_sub_bullets_from_any_section() -> None:
    fixed_breaking = (
        "- **Breaking fix** changes exit codes (#900).\n"
        "  - FIXBREAK-SUB-A exit 2 now\n"
        "  - FIXBREAK-SUB-B exit 3 now\n"
    )
    text = _big(100).replace("### Fixed\n\n", "### Fixed\n\n" + fixed_breaking, 1)
    notes = _module().release_notes(text, "2.0.0", limit=2000)
    assert "**Breaking fix**" in notes
    assert "FIXBREAK-SUB-A" in notes and "FIXBREAK-SUB-B" in notes
    assert re.search(r"\b101 Fixed\b", notes)
    # the next top-level bullet after it is not dragged in
    assert "Fixed entry 0 " not in notes


# 4 ---------------------------------------------------------------------------------


def test_unknown_version_raises() -> None:
    with pytest.raises(ValueError):
        _module().release_notes(SMALL, "9.9.9")


def test_unknown_version_cli_fails_with_nothing_on_stdout(tmp_path: Path) -> None:
    cl = tmp_path / "CHANGELOG.md"
    cl.write_text(SMALL)
    r = _run("9.9.9", "--changelog", str(cl))
    assert r.returncode != 0
    assert r.stdout == ""
    assert r.stderr.strip()


# 5 ---------------------------------------------------------------------------------


def test_condensed_still_over_limit_raises() -> None:
    huge_removed = "".join(f"- Removed thing {i} " + "y" * 80 + ".\n" for i in range(100))
    with pytest.raises(ValueError):
        _module().release_notes(_big(100, removed=huge_removed), "2.0.0", limit=2000)


def test_condensed_still_over_limit_cli_fails(tmp_path: Path) -> None:
    huge_removed = "".join(f"- Removed thing {i} " + "y" * 80 + ".\n" for i in range(2000))
    cl = tmp_path / "CHANGELOG.md"
    cl.write_text(_big(100, removed=huge_removed))
    r = _run("2.0.0", "--changelog", str(cl))
    assert r.returncode != 0
    assert r.stdout == ""
    assert r.stderr.strip()


# 6 ---------------------------------------------------------------------------------


@pytest.mark.parametrize("n_fixed", [3, 2000])
def test_cli_prints_release_notes(tmp_path: Path, n_fixed: int) -> None:
    text = _big(n_fixed)
    cl = tmp_path / "CHANGELOG.md"
    cl.write_text(text)
    r = _run("2.0.0", "--changelog", str(cl))
    assert r.returncode == 0, r.stderr
    expected = _module().release_notes(text, "2.0.0")
    assert r.stdout.rstrip("\n") == expected.rstrip("\n")


# 7 ---------------------------------------------------------------------------------


def test_real_changelog() -> None:
    mod = _module()
    text = (ROOT / "CHANGELOG.md").read_text()
    notes = mod.release_notes(text, "3.0.0")
    assert len(notes) <= 125_000
    assert "#580" in notes
    assert "#300---2026-09-30" in notes
    small = mod.release_notes(text, "2.2.0")
    assert small.strip() == _section(text, "2.2.0").strip()


def test_real_changelog_cli_default_path() -> None:
    r = _run("3.0.0")
    assert r.returncode == 0, r.stderr
    assert len(r.stdout) <= 125_001
    assert "#580" in r.stdout
