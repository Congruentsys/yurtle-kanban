"""Issue #243 — `scripts/assemble_changelog.py`: the unclosed-fence error names the line
the fence opened on; `left` in `main` carries a type hint.

Decided behaviour:
1. An unclosed code fence -> non-zero exit, nothing changed, no traceback, and stderr says
   `opened at line N`, N the opener's 1-based line:
   - in the CHANGELOG, N is counted in the CHANGELOG file;
   - in a fragment, N is counted in the fragment FILE (its section line is line 1), and
     stderr still names the fragment's path.
2. In `main`, `left` is annotated: `left: list[str] = []`.
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

HEADER = "# Changelog\n\nAll notable changes are documented here.\n\n"
OLD = "## [1.0.0] - 2026-01-01\n\n### Fixed\n\n- Old released fix.\n"
GOOD_CHANGELOG = f"{HEADER}## [Unreleased]\n\n{OLD}"
README = "# changelog.d\n\nOne fragment per PR.\n"


def setup(tmp_path: Path, changelog: bytes, fragments: dict[str, str]) -> None:
    (tmp_path / "CHANGELOG.md").write_bytes(changelog)
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


def line_of(text: str, marker: str) -> int:
    """1-based number of the first line starting with `marker` (after indentation)."""
    for n, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith(marker):
            return n
    raise AssertionError(f"{marker!r} not in text")


def assert_refused(tmp_path: Path, before: dict[str, bytes],
                   r: subprocess.CompletedProcess[str], line: int) -> None:
    assert r.returncode != 0, f"expected refusal; stdout={r.stdout!r} stderr={r.stderr!r}"
    assert "Traceback" not in r.stderr, r.stderr
    assert "unclosed" in r.stderr, r.stderr
    assert re.search(rf"opened at line {line}(?!\d)", r.stderr), (
        f"stderr should say 'opened at line {line}': {r.stderr!r}"
    )
    assert snapshot(tmp_path) == before, "a refused release changed files"


# --- 1a. an unclosed fence in the CHANGELOG -----------------------------------------

CHANGELOG_CASES = {
    # opener inside Unreleased
    "in-unreleased": (
        f"{HEADER}## [Unreleased]\n\n### Fixed\n\n- A fix:\n\n```python\nx = 1\n{OLD}",
        "```",
    ),
    # opener after an earlier closed fence: the second one is named
    "after-closed-fence": (
        f"{HEADER}## [Unreleased]\n\n### Added\n\n```\ncode\n```\n\nText.\n\n"
        f"~~~\nquoted\n{OLD}",
        "~~~",
    ),
    # opener deep in an old release, indented
    "in-old-release": (
        f"{HEADER}## [Unreleased]\n\n### Fixed\n\n- New.\n\n{OLD}\n"
        "- Another:\n\n    ````\n    ### not a heading\n",
        "````",
    ),
}


@pytest.mark.parametrize("crlf", [False, True], ids=["lf", "crlf"])
@pytest.mark.parametrize("case", sorted(CHANGELOG_CASES))
def test_changelog_unclosed_fence_names_opener_line(tmp_path: Path, case: str,
                                                    crlf: bool) -> None:
    text, marker = CHANGELOG_CASES[case]
    line = line_of(text, marker)
    raw = text.replace("\n", "\r\n") if crlf else text
    setup(tmp_path, raw.encode(), {"101.md": "<!-- section: Fixed -->\n- **Thing** (#101)\n"})
    before = snapshot(tmp_path)
    r = release(tmp_path)
    assert_refused(tmp_path, before, r, line)
    assert "CHANGELOG" in r.stderr, r.stderr


# --- 1b. an unclosed fence in a fragment --------------------------------------------

FRAGMENT_CASES = {
    # the issue's example: the section line is line 1, the fence the file's 3rd line
    "third-line": ("<!-- section: Fixed -->\n- **Thing** (#101):\n```python\nx = 1\n", 3),
    "indented-later": (
        "<!-- section: Added -->\n- **Thing** (#101):\n\n  Details.\n\n  ~~~\n  ### x\n",
        6,
    ),
    "after-closed-fence": (
        "<!-- section: Fixed -->\n- **Thing** (#101):\n\n```\na\n```\n\n````\n```\n",
        8,
    ),
    "second-line": ("<!-- section: Fixed -->\n```\n- **Thing** (#101)\n", 2),
}


@pytest.mark.parametrize("case", sorted(FRAGMENT_CASES))
def test_fragment_unclosed_fence_names_line_in_fragment_file(tmp_path: Path,
                                                             case: str) -> None:
    body, line = FRAGMENT_CASES[case]
    assert body.splitlines()[line - 1].lstrip()[:3] in ("```", "~~~")  # self-check
    setup(tmp_path, GOOD_CHANGELOG.encode(), {
        "100.md": "<!-- section: Fixed -->\n- **Fine** (#100)\n",
        "101-thing.md": body,
    })
    before = snapshot(tmp_path)
    r = release(tmp_path)
    assert_refused(tmp_path, before, r, line)
    assert "101-thing.md" in r.stderr, r.stderr


def test_fragment_crlf_line_number_counts_file_lines(tmp_path: Path) -> None:
    body = "<!-- section: Fixed -->\r\n- **Thing** (#101):\r\n\r\n```\r\nx\r\n"
    setup(tmp_path, GOOD_CHANGELOG.encode(), {"101.md": body})
    before = snapshot(tmp_path)
    r = release(tmp_path)
    assert_refused(tmp_path, before, r, 4)
    assert "101.md" in r.stderr, r.stderr


# --- 2. `left` is annotated ---------------------------------------------------------

def test_left_is_annotated_in_main() -> None:
    src = SCRIPT.read_text()
    main = src[src.index("def main("):]
    assert re.search(r"^\s*left: list\[str\] = \[\]\s*$", main, re.M), (
        "main should declare `left: list[str] = []`"
    )
    assert not re.search(r"^\s*left = \[\]\s*$", main, re.M), "unannotated `left = []` remains"
