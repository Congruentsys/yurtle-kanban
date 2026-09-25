"""Issue #223 — `scripts/assemble_changelog.py`: fragment fences, backtick info strings,
and naming the fragments left behind when unlinking fails.

Decided behaviour:
1. A fragment that opens a code fence and never closes it -> non-zero exit, stderr names
   the fragment and says "unclosed", nothing changed, no traceback. A fragment with a
   closed fence assembles fine.
2. A backtick fence run followed by text holding a backtick ("```x``` inline") is not a
   fence opener (CommonMark), so an Unreleased body with such a line assembles. A tilde
   fence with a backtick in its info string ("~~~x`~") is still an opener.
3. If a fragment cannot be unlinked after the CHANGELOG write -> non-zero exit, no
   traceback, stderr names every fragment that remains and says the CHANGELOG was
   already written.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts/assemble_changelog.py"
DATE = "2026-09-25"
NEW = f"## [1.1.0] - {DATE}"

HEADER = "# Changelog\n\nAll notable changes are documented here.\n\n"
OLD = "## [1.0.0] - 2026-01-01\n\n### Fixed\n\n- Old released fix.\n"
README = "# changelog.d\n\nOne fragment per PR.\n"
IS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0


def frag(section: str, body: str) -> str:
    return f"<!-- section: {section} -->\n{body}\n"


def setup(tmp_path: Path, changelog: str, fragments: dict[str, str]) -> tuple[Path, Path]:
    cl = tmp_path / "CHANGELOG.md"
    cl.write_bytes(changelog.encode())
    d = tmp_path / "changelog.d"
    d.mkdir()
    (d / "README.md").write_bytes(README.encode())
    for name, text in fragments.items():
        (d / name).write_bytes(text.encode())
    return cl, d


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


def new_section(text: str) -> str:
    start = text.index(NEW)
    end = text.index("## [1.0.0]", start)
    return text[start:end]


# --- 1. a fragment with an unclosed fence -----------------------------------------

UNCLOSED_BODIES = [
    "- **Thing** (#101):\n\n  ```python\n  x = 1\n",
    "- **Thing** (#101):\n\n~~~\n### Not a heading\n",
    "- **Thing** (#101):\n\n````\n```\n",  # a shorter run does not close
    "- **Thing** (#101):\n\n```\n~~~\n",  # the other character does not close
]


class TestFragmentFence:
    @pytest.mark.parametrize("body", UNCLOSED_BODIES)
    def test_unclosed_fence_in_fragment_refused(self, tmp_path: Path, body: str) -> None:
        setup(tmp_path, HEADER + "## [Unreleased]\n\n" + OLD, {
            "100.md": frag("Fixed", "- **Fine** (#100)."),
            "101-thing.md": frag("Fixed", body),
        })
        before = snapshot(tmp_path)
        r = release(tmp_path)
        assert r.returncode != 0, f"unclosed fence in a fragment accepted:\n{r.stdout}"
        assert "Traceback" not in r.stderr, r.stderr
        assert "101-thing.md" in r.stderr, f"fragment not named:\n{r.stderr}"
        assert "unclosed" in r.stderr.lower(), r.stderr
        assert snapshot(tmp_path) == before, "files changed on a refused release"

    @pytest.mark.parametrize("body", [
        "- **Thing** (#101):\n\n  ```python\n  x = 1\n  ```",
        "- **Thing** (#101):\n\n~~~\n### Not a heading\n~~~",
        "- **Thing** (#101):\n\n````\n```\n````",
    ])
    def test_closed_fence_in_fragment_assembled(self, tmp_path: Path, body: str) -> None:
        cl, d = setup(tmp_path, HEADER + "## [Unreleased]\n\n" + OLD, {
            "101-thing.md": frag("Fixed", body),
        })
        r = release(tmp_path)
        assert r.returncode == 0, r.stderr
        section = new_section(cl.read_text())
        assert body in section, section
        assert not (d / "101-thing.md").exists()


# --- 2. backtick info strings -------------------------------------------------------

INLINE = "```x``` inline"


class TestInfoString:
    @pytest.mark.parametrize("line", [INLINE, "```py `x`", "  ````a`b"])
    def test_backtick_in_backtick_info_is_not_a_fence(self, tmp_path: Path,
                                                     line: str) -> None:
        unreleased = (
            f"## [Unreleased]\n\nA note:\n{line}\n\n"
            "### Added\n\n- **New** (#2).\n\n"
            "### Fixed\n\n- **Existing** (#1).\n\n"
        )
        cl, _ = setup(tmp_path, HEADER + unreleased + OLD, {
            "101.md": frag("Fixed", "- **Frag** (#101)."),
        })
        r = release(tmp_path)
        assert r.returncode == 0, f"line {line!r} taken as a fence opener:\n{r.stderr}"
        assert "unclosed" not in r.stderr.lower(), r.stderr
        section = new_section(cl.read_text())
        assert f"\n{line}\n" in section, section
        assert "### Added\n\n- **New** (#2)." in section, section
        assert "- **Existing** (#1).\n- **Frag** (#101)." in section, section

    def test_backtick_in_backtick_info_in_fragment(self, tmp_path: Path) -> None:
        cl, _ = setup(tmp_path, HEADER + "## [Unreleased]\n\n" + OLD, {
            "101.md": frag("Fixed", f"- **Frag** (#101):\n\n{INLINE}"),
        })
        r = release(tmp_path)
        assert r.returncode == 0, r.stderr
        assert INLINE in new_section(cl.read_text())

    def test_tilde_fence_with_backtick_info_opens(self, tmp_path: Path) -> None:
        unreleased = (
            "## [Unreleased]\n\n### Fixed\n\n- **Existing** (#1):\n\n"
            "~~~x`~\n### Not a heading\n~~~\n\n"
        )
        cl, _ = setup(tmp_path, HEADER + unreleased + OLD, {
            "101.md": frag("Fixed", "- **Frag** (#101)."),
        })
        r = release(tmp_path)
        assert r.returncode == 0, r.stderr
        section = new_section(cl.read_text())
        assert "~~~x`~\n### Not a heading\n~~~\n- **Frag** (#101)." in section, section

    def test_tilde_fence_with_backtick_info_unclosed_refused(self, tmp_path: Path) -> None:
        unreleased = "## [Unreleased]\n\n### Fixed\n\n~~~x`~\n### Not a heading\n\n"
        setup(tmp_path, HEADER + unreleased + OLD, {
            "101.md": frag("Fixed", "- **Frag** (#101)."),
        })
        before = snapshot(tmp_path)
        r = release(tmp_path)
        assert r.returncode != 0, r.stdout
        assert "Traceback" not in r.stderr, r.stderr
        assert "unclosed" in r.stderr.lower(), r.stderr
        assert snapshot(tmp_path) == before

    def test_tilde_fence_with_backtick_info_unclosed_in_fragment(self,
                                                                 tmp_path: Path) -> None:
        setup(tmp_path, HEADER + "## [Unreleased]\n\n" + OLD, {
            "101.md": frag("Fixed", "- **Frag** (#101):\n\n~~~x`~\ncode"),
        })
        before = snapshot(tmp_path)
        r = release(tmp_path)
        assert r.returncode != 0, r.stdout
        assert "101.md" in r.stderr and "unclosed" in r.stderr.lower(), r.stderr
        assert snapshot(tmp_path) == before


# --- 3. unlink failure after the write ------------------------------------------------

class TestUnlinkFailure:
    @pytest.mark.skipif(IS_ROOT, reason="root can unlink in a read-only directory")
    def test_remaining_fragments_named(self, tmp_path: Path) -> None:
        names = ["100.md", "101-thing.md", "102.md"]
        cl, d = setup(tmp_path, HEADER + "## [Unreleased]\n\n" + OLD,
                      {n: frag("Fixed", f"- **F{n}**.") for n in names})
        d.chmod(0o555)
        try:
            r = release(tmp_path)
            remaining = sorted(p.name for p in d.iterdir() if p.name != "README.md")
        finally:
            d.chmod(0o755)
        assert remaining == sorted(names), "the directory was meant to block unlinking"
        assert NEW in cl.read_text(), "the CHANGELOG write should have happened"
        assert r.returncode != 0, r.stdout
        assert "Traceback" not in r.stderr, r.stderr
        for n in names:
            assert n in r.stderr, f"remaining fragment {n} not named:\n{r.stderr}"
        err = r.stderr.lower()
        assert "changelog" in err and ("written" in err or "already" in err), r.stderr
