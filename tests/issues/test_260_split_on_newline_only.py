"""Issue #260 — `scripts/assemble_changelog.py` splits lines on "\\n" only.

`str.splitlines()` also breaks on form feed, vertical tab, \\x1c-\\x1e, \\x85, \\u2028,
\\u2029 and a lone \\r. Decided behaviour: only "\\n" (and "\\r\\n", normalised on read) is
a line break. So:
1. `- a<sep>b` on line 5 and an unclosed fence opener on line 7 -> `opened at line 7`
   (in the CHANGELOG; and in a fragment, counted in the fragment file).
2. Heading detection: `- note<sep>### Fixed` under Unreleased is one line, not a
   `### Fixed` heading — the text stays verbatim in its bullet; `- entry<sep>## [0.1] fake`
   doesn't end Unreleased.
3. A lone \\r inside an entry line of an otherwise-LF file is not a break either: the text
   survives verbatim in the release.
Controls: plain LF and CRLF files behave as before.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts/assemble_changelog.py"
DATE = "2026-09-25"
NEW = f"## [1.1.0] - {DATE}"

OLD = "## [1.0.0] - 2026-01-01\n\n### Fixed\n\n- Old released fix.\n"
GOOD_CHANGELOG = f"# Changelog\n\n## [Unreleased]\n\n{OLD}"
FRAGMENT = "<!-- section: Added -->\n- **Thing** (#101)\n"
README = "# changelog.d\n\nOne fragment per PR.\n"

# every character splitlines() breaks on besides "\n" (and "\r\n")
SEPS = {
    "ff": "\x0c", "vt": "\x0b", "fs": "\x1c", "gs": "\x1d", "rs": "\x1e",
    "nel": "\x85", "ls": " ", "ps": " ", "cr": "\r",
}
# a fragment is read with universal newlines, so a lone \r there is a break by design
FRAGMENT_SEPS = {k: v for k, v in SEPS.items() if k != "cr"}


def setup(tmp_path: Path, changelog: str, fragments: dict[str, str]) -> None:
    (tmp_path / "CHANGELOG.md").write_bytes(changelog.encode())
    d = tmp_path / "changelog.d"
    d.mkdir()
    (d / "README.md").write_bytes(README.encode())
    for name, text in fragments.items():
        (d / name).write_bytes(text.encode())


def release(tmp_path: Path) -> subprocess.CompletedProcess[str]:
    assert SCRIPT.exists(), f"{SCRIPT.relative_to(REPO)} does not exist"
    return subprocess.run(
        [sys.executable, str(SCRIPT), "1.1.0", "--date", DATE,
         "--changelog", str(tmp_path / "CHANGELOG.md"),
         "--fragments", str(tmp_path / "changelog.d")],
        cwd=tmp_path, capture_output=True, text=True, timeout=60,
    )


def snapshot(tmp_path: Path) -> dict[str, bytes]:
    return {str(p.relative_to(tmp_path)): p.read_bytes()
            for p in sorted(tmp_path.rglob("*")) if p.is_file()}


def assert_refused(tmp_path: Path, before: dict[str, bytes],
                   r: subprocess.CompletedProcess[str], line: int) -> None:
    assert r.returncode != 0, f"expected refusal; stdout={r.stdout!r} stderr={r.stderr!r}"
    assert "Traceback" not in r.stderr, r.stderr
    assert "unclosed" in r.stderr, r.stderr
    assert re.search(rf"opened at line {line}(?!\d)", r.stderr), (
        f"stderr should say 'opened at line {line}': {r.stderr!r}"
    )
    assert snapshot(tmp_path) == before, "a refused release changed files"


def released(tmp_path: Path) -> str:
    r = release(tmp_path)
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert "Traceback" not in r.stderr, r.stderr
    return (tmp_path / "CHANGELOG.md").read_bytes().decode()


# --- 1. line numbers count "\n" lines only ------------------------------------------

def unclosed_changelog(sep: str) -> str:
    # line 5: `- a<sep>b`; line 7: the unclosed opener
    return f"# Changelog\n\n## [Unreleased]\n\n- a{sep}b\n\n```python\nx = 1\n{OLD}"


@pytest.mark.parametrize("sep", sorted(SEPS))
def test_changelog_opener_line_ignores_non_newline_breaks(tmp_path: Path, sep: str) -> None:
    setup(tmp_path, unclosed_changelog(SEPS[sep]), {"101.md": FRAGMENT})
    before = snapshot(tmp_path)
    r = release(tmp_path)
    assert_refused(tmp_path, before, r, 7)
    assert "CHANGELOG" in r.stderr, r.stderr


def test_changelog_opener_line_control_plain_lf(tmp_path: Path) -> None:
    setup(tmp_path, unclosed_changelog(""), {"101.md": FRAGMENT})
    before = snapshot(tmp_path)
    assert_refused(tmp_path, before, release(tmp_path), 7)


def test_changelog_opener_line_control_crlf(tmp_path: Path) -> None:
    text = unclosed_changelog("").replace("\n", "\r\n")
    setup(tmp_path, text, {"101.md": FRAGMENT})
    before = snapshot(tmp_path)
    assert_refused(tmp_path, before, release(tmp_path), 7)


@pytest.mark.parametrize("sep", sorted(FRAGMENT_SEPS))
def test_fragment_opener_line_ignores_non_newline_breaks(tmp_path: Path, sep: str) -> None:
    # section line is line 1, `- a<sep>b` line 2, the opener line 3
    body = f"<!-- section: Fixed -->\n- a{FRAGMENT_SEPS[sep]}b\n```\nx = 1\n"
    setup(tmp_path, GOOD_CHANGELOG, {"101-thing.md": body})
    before = snapshot(tmp_path)
    r = release(tmp_path)
    assert_refused(tmp_path, before, r, 3)
    assert "101-thing.md" in r.stderr, r.stderr


# --- 2/3. heading detection: an embedded `### ` / `## [` is not a heading -----------

def expected(entry: str, crlf: bool = False) -> str:
    text = (
        f"# Changelog\n\n## [Unreleased]\n\n{NEW}\n\n### Added\n\n{entry}\n"
        f"- **Thing** (#101)\n\n{OLD}"
    )
    return text.replace("\n", "\r\n") if crlf else text


@pytest.mark.parametrize("sep", sorted(SEPS))
def test_embedded_section_heading_stays_in_its_bullet(tmp_path: Path, sep: str) -> None:
    entry = f"- note{SEPS[sep]}### Fixed"
    setup(tmp_path, f"# Changelog\n\n## [Unreleased]\n\n### Added\n\n{entry}\n\n{OLD}",
          {"101.md": FRAGMENT})
    new = released(tmp_path)
    assert new == expected(entry), (
        f"`{entry!r}` should stay one bullet under ### Added, no ### Fixed section:\n{new!r}"
    )


@pytest.mark.parametrize("sep", sorted(SEPS))
def test_embedded_release_heading_does_not_end_unreleased(tmp_path: Path, sep: str) -> None:
    entry = f"- entry{SEPS[sep]}## [0.1] fake"
    setup(tmp_path, f"# Changelog\n\n## [Unreleased]\n\n### Added\n\n{entry}\n\n{OLD}",
          {"101.md": FRAGMENT})
    new = released(tmp_path)
    assert new == expected(entry), (
        f"`{entry!r}` should be released verbatim under ### Added:\n{new!r}"
    )


def test_lone_cr_mid_entry_survives_verbatim(tmp_path: Path) -> None:
    entry = "- **Fix** (#9): was\rnow"
    setup(tmp_path, f"# Changelog\n\n## [Unreleased]\n\n### Added\n\n{entry}\n\n{OLD}",
          {"101.md": FRAGMENT})
    new = released(tmp_path)
    assert new == expected(entry), new
    assert "\r\n" not in new, "an LF file with a lone \\r must stay LF"


@pytest.mark.parametrize("crlf", [False, True], ids=["lf", "crlf"])
def test_heading_control_plain_file(tmp_path: Path, crlf: bool) -> None:
    text = f"# Changelog\n\n## [Unreleased]\n\n### Added\n\n- note\n\n{OLD}"
    setup(tmp_path, text.replace("\n", "\r\n") if crlf else text, {"101.md": FRAGMENT})
    assert released(tmp_path) == expected("- note", crlf)
