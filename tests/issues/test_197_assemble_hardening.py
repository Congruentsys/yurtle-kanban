"""Issue #197 — hardening `scripts/assemble_changelog.py`, plus two doc fixes.

Decided behaviour:
1. Fence-aware: lines inside ``` / ~~~ fences (column 0 or indented) under
   `## [Unreleased]` are never `### ` or `## [` headings. The fence stays verbatim in
   its section, fragments go after it, and nothing after it leaks out of the release.
2. A CRLF CHANGELOG stays CRLF (new lines too); older sections are byte-identical.
3. A fragment with a section line but an empty/whitespace body -> non-zero exit naming
   the file, nothing changed, no fragment deleted.
4. `## [Unreleased] - TBD`: the trailing text does not leak into the new section.
5. `--date` must be YYYY-MM-DD (a real date) -> otherwise non-zero exit, nothing changed.
6. Fragments are deleted only after the CHANGELOG write succeeded; a failed write
   exits non-zero cleanly (no traceback).
7. CONTRIBUTING says the assemble step replaces step 4 of skills/release/SKILL.md;
   pairit's "Rebased after approval?" paragraph has no line under 60 chars but the last,
   and says only once that the PR's own patch is unchanged.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts/assemble_changelog.py"
CONTRIBUTING = REPO / "CONTRIBUTING.md"
PAIRIT = REPO / ".claude/skills/pairit/SKILL.md"
DATE = "2026-09-24"
NEW = f"## [1.1.0] - {DATE}"

HEADER = "# Changelog\n\nAll notable changes are documented here.\n\n"
OLD = (
    "## [1.0.0] - 2026-01-01\n\n"
    "### Fixed\n\n"
    "- Old released fix.\n\n"
    "## [0.9.0] - 2025-12-01\n\n"
    "### Added\n\n"
    "- Older feature.\n"
)
UNRELEASED = "## [Unreleased]\n\n### Fixed\n\n- **Existing** (#1).\n\n"
README = "# changelog.d\n\nOne fragment per PR.\n"


def run(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    assert SCRIPT.exists(), f"{SCRIPT.relative_to(REPO)} does not exist"
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=tmp_path, capture_output=True, text=True, timeout=60,
    )


def frag(section: str, body: str) -> str:
    return f"<!-- section: {section} -->\n{body}\n"


def setup(tmp_path: Path, changelog: str | bytes,
          fragments: dict[str, str]) -> tuple[Path, Path]:
    cl = tmp_path / "CHANGELOG.md"
    cl.write_bytes(changelog if isinstance(changelog, bytes) else changelog.encode())
    d = tmp_path / "changelog.d"
    d.mkdir()
    (d / "README.md").write_bytes(README.encode())
    for name, text in fragments.items():
        (d / name).write_bytes(text.encode())
    return cl, d


def release(tmp_path: Path, date: str = DATE) -> subprocess.CompletedProcess[str]:
    return run(tmp_path, "1.1.0", "--date", date, "--changelog", str(tmp_path / "CHANGELOG.md"),
               "--fragments", str(tmp_path / "changelog.d"))


def snapshot(tmp_path: Path) -> dict[str, bytes]:
    return {str(p.relative_to(tmp_path)): p.read_bytes()
            for p in sorted(tmp_path.rglob("*")) if p.is_file()}


def between(text: str, start: str, end: str) -> str:
    """Text after the first `start` up to the first `end` after it."""
    i = text.index(start) + len(start)
    return text[i:text.index(end, i)]


# --- 1. fences -----------------------------------------------------------------------

FENCES = {
    "backticks-col0": ("```", "```"),
    "tildes-col0": ("~~~", "~~~"),
    # an indented (list-item) fence whose content lines start at column 0
    "backticks-indented": ("  ```text", "  ```"),
}


def fenced_block(open_: str, close: str) -> str:
    return f"{open_}\n### Fixed\n## [0.1] fake\n{close}"


class TestFenceAware:
    """Headings inside a fenced block under Unreleased are just text (#197)."""

    @pytest.mark.parametrize(("open_", "close"), FENCES.values(), ids=FENCES.keys())
    def test_fence_kept_verbatim_and_fragments_after_it(
        self, tmp_path: Path, open_: str, close: str
    ) -> None:
        block = fenced_block(open_, close)
        unreleased = (
            "## [Unreleased]\n\n"
            "### Fixed\n\n"
            "- **Fenced** example:\n\n"
            f"{block}\n\n"
            "- **After fence** (#1).\n\n"
        )
        cl, d = setup(tmp_path, HEADER + unreleased + OLD,
                      {"5.md": frag("Fixed", "- **Five** (#5).")})
        r = release(tmp_path)
        assert r.returncode == 0, r.stderr
        text = cl.read_text()
        assert NEW in text, text
        new = between(text, NEW, "## [1.0.0]")
        # the fence is intact, verbatim, inside the new release section
        assert block in new, f"fence not kept verbatim in the release:\n{new}"
        # the fragment is appended after the fence, not inside it
        assert new.index("**Five**") > new.index(block) + len(block), new
        # nothing after the fence leaks out of the release section
        assert "**After fence**" in new, f"entry after the fence left the release:\n{text}"
        assert "**Fenced**" in new
        # Unreleased is empty, older sections untouched, the fake heading is still text
        assert between(text, "## [Unreleased]", NEW).strip() == "", text
        assert text[text.index("## [1.0.0]"):] == OLD
        assert text.count("## [0.1] fake") == 1
        assert not (d / "5.md").exists()


class TestFenceClosing:
    """Only a bare fence run (same char, length >= opener) closes a fence (#197, PR #221)."""

    INNER = {
        # an info string makes it content, not a closer
        "backticks-info-string": ("```", "```python", "```"),
        "tildes-info-string": ("~~~", "~~~ sh", "~~~"),
        # a shorter run of the same char doesn't close a longer opener
        "four-open-three-inside": ("````", "```", "````"),
        # the other fence char never closes
        "tildes-inside-backticks": ("```", "~~~", "```"),
    }

    @pytest.mark.parametrize(("open_", "inner", "close"), INNER.values(), ids=INNER.keys())
    def test_inner_fence_line_does_not_close(
        self, tmp_path: Path, open_: str, inner: str, close: str
    ) -> None:
        block = f"{open_}\n### Fixed\n{inner}\n## [0.1] fake\n\n- Inside.\n{close}"
        unreleased = (
            "## [Unreleased]\n\n### Fixed\n\n- **E**:\n\n"
            f"{block}\n\n- **After** (#1).\n\n"
        )
        cl, d = setup(tmp_path, HEADER + unreleased + OLD,
                      {"5.md": frag("Fixed", "- **Five** (#5).")})
        r = release(tmp_path)
        assert r.returncode == 0, r.stderr
        text = cl.read_text()
        assert NEW in text, text
        new = between(text, NEW, "## [1.0.0]")
        assert block in new, f"fence not kept verbatim in the release:\n{text}"
        assert new.index("**Five**") > new.index(block) + len(block), new
        assert "**After**" in new, f"entry after the fence left the release:\n{text}"
        assert between(text, "## [Unreleased]", NEW).strip() == "", text
        assert text[text.index("## [1.0.0]"):] == OLD
        assert text.count("## [0.1] fake") == 1
        assert not (d / "5.md").exists()


UNCLOSED = {
    # the reviewer's repro: a bare fence under Unreleased, never closed
    "under-unreleased": HEADER + (
        "## [Unreleased]\n\n### Fixed\n\n- E:\n\n"
        "```\n### Fixed\n## [0.1] fake\n\n- After.\n\n"
    ) + OLD,
    # a stray fence above ## [Unreleased]
    "above-unreleased": HEADER + "```\n\n" + UNRELEASED + OLD,
    "tildes-under-unreleased": HEADER + "## [Unreleased]\n\n### Fixed\n\n~~~\n- x.\n\n" + OLD,
}


class TestUnclosedFenceRefused:
    """An unclosed fence is refused, not silently swallowing older releases (PR #221)."""

    @pytest.mark.parametrize("changelog", UNCLOSED.values(), ids=UNCLOSED.keys())
    def test_unclosed_fence_refused(self, tmp_path: Path, changelog: str) -> None:
        setup(tmp_path, changelog, {"5.md": frag("Fixed", "- **Five** (#5).")})
        before = snapshot(tmp_path)
        r = release(tmp_path)
        assert r.returncode != 0, f"unclosed fence accepted:\n{r.stdout}"
        assert "unclosed" in r.stderr.lower(), r.stderr
        assert "Traceback" not in r.stderr, r.stderr
        assert snapshot(tmp_path) == before, "something changed on a refused release"


# --- 2. CRLF -------------------------------------------------------------------------

class TestCRLF:
    """A CRLF CHANGELOG stays CRLF, byte for byte (#197)."""

    def test_crlf_preserved(self, tmp_path: Path) -> None:
        unreleased = "## [Unreleased]\n\n### Fixed\n\n- **Existing** (#1).\n\n"
        crlf = (HEADER + unreleased + OLD).replace("\n", "\r\n").encode()
        cl, _ = setup(tmp_path, crlf, {"5.md": frag("Fixed", "- **Five** (#5).")})
        r = release(tmp_path)
        assert r.returncode == 0, r.stderr
        out = cl.read_bytes()
        assert f"{NEW}\r\n".encode() in out, out
        assert b"**Five**" in out and b"**Existing**" in out
        bare = out.replace(b"\r\n", b"")
        assert b"\n" not in bare, f"bare LF in a CRLF CHANGELOG: {out!r}"
        assert b"\r" not in bare, f"stray CR in a CRLF CHANGELOG: {out!r}"
        old = OLD.replace("\n", "\r\n").encode()
        assert out[out.index(b"## [1.0.0]"):] == old, "older sections are not byte-identical"


CRLF_FRAG = "<!-- section: Fixed -->\r\n- **Five** (#5),\r\n  two lines.\r\n"


class TestCRLFFragment:
    """A CRLF fragment takes the CHANGELOG's line endings (#197, PR #221)."""

    def test_crlf_fragment_in_lf_changelog(self, tmp_path: Path) -> None:
        cl, _ = setup(tmp_path, HEADER + UNRELEASED + OLD, {"5.md": CRLF_FRAG})
        r = release(tmp_path)
        assert r.returncode == 0, r.stderr
        out = cl.read_bytes()
        assert b"- **Five** (#5),\n  two lines.\n" in out, out
        assert b"\r" not in out, f"stray CR from a CRLF fragment: {out!r}"

    def test_crlf_fragment_in_crlf_changelog(self, tmp_path: Path) -> None:
        crlf = (HEADER + UNRELEASED + OLD).replace("\n", "\r\n").encode()
        cl, _ = setup(tmp_path, crlf, {"5.md": CRLF_FRAG})
        r = release(tmp_path)
        assert r.returncode == 0, r.stderr
        out = cl.read_bytes()
        assert b"- **Five** (#5),\r\n  two lines.\r\n" in out, out
        assert b"\r\r\n" not in out, f"doubled CR from a CRLF fragment: {out!r}"
        bare = out.replace(b"\r\n", b"")
        assert b"\n" not in bare and b"\r" not in bare, out


# --- 3. empty fragments --------------------------------------------------------------

EMPTY = {
    "section-line-only": "<!-- section: Fixed -->\n",
    "no-newline": "<!-- section: Fixed -->",
    "whitespace-body": "<!-- section: Fixed -->\n   \n\t\n\n",
}


class TestEmptyFragmentRefused:
    """A fragment with no body is refused, like a bad section line (#197)."""

    @pytest.mark.parametrize("body", EMPTY.values(), ids=EMPTY.keys())
    def test_with_other_content(self, tmp_path: Path, body: str) -> None:
        setup(tmp_path, HEADER + UNRELEASED + OLD,
              {"5.md": frag("Fixed", "- **Five** (#5)."), "6-empty.md": body})
        before = snapshot(tmp_path)
        r = release(tmp_path)
        assert r.returncode != 0, f"empty fragment accepted:\n{r.stdout}{r.stderr}"
        assert "6-empty.md" in r.stderr, r.stderr
        assert snapshot(tmp_path) == before, "something changed on a refused release"

    @pytest.mark.parametrize("body", EMPTY.values(), ids=EMPTY.keys())
    def test_only_fragment(self, tmp_path: Path, body: str) -> None:
        setup(tmp_path, HEADER + "## [Unreleased]\n\n" + OLD, {"6-empty.md": body})
        before = snapshot(tmp_path)
        r = release(tmp_path)
        assert r.returncode != 0, f"empty fragment accepted:\n{r.stdout}{r.stderr}"
        assert "6-empty.md" in r.stderr, r.stderr
        assert snapshot(tmp_path) == before, "something changed on a refused release"


# --- 4. trailing text on the Unreleased heading --------------------------------------

class TestUnreleasedHeadingText:
    """`## [Unreleased] - TBD` does not leak into the new section (#197)."""

    def test_tbd_does_not_leak(self, tmp_path: Path) -> None:
        unreleased = "## [Unreleased] - TBD\n\n### Fixed\n\n- **Existing** (#1).\n\n"
        cl, _ = setup(tmp_path, HEADER + unreleased + OLD,
                      {"5.md": frag("Fixed", "- **Five** (#5).")})
        r = release(tmp_path)
        assert r.returncode == 0, r.stderr
        text = cl.read_text()
        assert f"\n{NEW}\n" in text, text
        new = between(text, NEW, "## [1.0.0]")
        assert "TBD" not in new, f"heading text leaked into the release:\n{new}"
        assert "**Existing**" in new and "**Five**" in new
        heads = [ln for ln in text.splitlines() if ln.startswith("## [Unreleased]")]
        assert heads in (["## [Unreleased] - TBD"], ["## [Unreleased]"]), heads
        assert text.index(heads[0]) < text.index(NEW)
        assert text[text.index("## [1.0.0]"):] == OLD


# --- 5. --date -----------------------------------------------------------------------

BAD_DATES = [
    "yesterday", "2026-13-01", "2026-02-30", f"{DATE}\n## [x", "", " 2026-09-24",
    "20260924", "2026-W39-4",
]


class TestDateValidated:
    """`--date` must be a real YYYY-MM-DD date (#197)."""

    @pytest.mark.parametrize("date", BAD_DATES, ids=repr)
    def test_bad_date_refused(self, tmp_path: Path, date: str) -> None:
        setup(tmp_path, HEADER + UNRELEASED + OLD, {"5.md": frag("Fixed", "- **Five** (#5).")})
        before = snapshot(tmp_path)
        r = release(tmp_path, date)
        assert r.returncode != 0, f"--date {date!r} accepted:\n{r.stdout}"
        assert snapshot(tmp_path) == before, "something changed on a refused release"

    def test_good_date_accepted(self, tmp_path: Path) -> None:
        cl, _ = setup(tmp_path, HEADER + UNRELEASED + OLD,
                      {"5.md": frag("Fixed", "- **Five** (#5).")})
        r = release(tmp_path, "2024-02-29")
        assert r.returncode == 0, r.stderr
        assert "## [1.1.0] - 2024-02-29\n" in cl.read_text()


# --- 6. write order ------------------------------------------------------------------

class TestWriteOrder:
    """Fragments are deleted only after the CHANGELOG was written (#197)."""

    @pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0,
                        reason="root ignores file permissions")
    def test_failed_write_keeps_fragments(self, tmp_path: Path) -> None:
        cl, d = setup(tmp_path, HEADER + UNRELEASED + OLD,
                      {"5.md": frag("Fixed", "- **Five** (#5)."),
                       "6.md": frag("Added", "- **Six** (#6).")})
        before = snapshot(tmp_path)
        cl.chmod(0o444)
        try:
            r = release(tmp_path)
        finally:
            cl.chmod(0o644)
        assert r.returncode != 0, f"write to a read-only CHANGELOG reported success:\n{r.stdout}"
        assert "Traceback" not in r.stderr, f"failed write crashed:\n{r.stderr}"
        assert (d / "5.md").exists() and (d / "6.md").exists(), "fragments deleted"
        assert snapshot(tmp_path) == before


# --- 7. docs -------------------------------------------------------------------------

def rebased_paragraph() -> list[str]:
    lines = PAIRIT.read_text().splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("**Rebased after approval?**"))
    end = next((i for i in range(start, len(lines)) if not lines[i].strip()), len(lines))
    return lines[start:end]


class TestDocs:
    """CONTRIBUTING and pairit wording (#197)."""

    def test_contributing_says_assemble_replaces_step_4(self) -> None:
        text = CONTRIBUTING.read_text()
        sec = between(text, "### Releasing", "\n### ")
        assert "skills/release/SKILL.md" in sec, sec
        assert "step 4" in sec.lower(), f"Releasing doesn't name step 4:\n{sec}"
        low = sec.lower()
        assert "instead" in low or "replaces" in low, \
            f"Releasing doesn't say the assemble step replaces step 4:\n{sec}"

    def test_pairit_rebased_paragraph_has_no_odd_wrap(self) -> None:
        para = rebased_paragraph()
        assert len(para) > 1
        short = [ln for ln in para[:-1] if len(ln) < 60]
        assert not short, f"odd hard wrap in the Rebased paragraph: {short}"

    def test_pairit_rebased_paragraph_says_patch_unchanged_once(self) -> None:
        text = " ".join(rebased_paragraph())
        hits = re.findall(r"(?i)(no change to the PR's own patch|patch is unchanged)", text)
        assert len(hits) <= 1, f"'patch unchanged' said {len(hits)} times: {hits}\n{text}"
