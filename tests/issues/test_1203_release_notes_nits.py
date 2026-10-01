"""Issue #1203 — release_notes.py nits from the review of #1191.

1. `_anchor` follows GitHub's heading-anchor rule: lowercase; drop anything that is not
   a letter (any script), a digit, a space, `_` or `-`; spaces become `-`.
2. A `**Breaking` entry inside `### Removed` or `### Deprecated` is listed once in the
   condensed notes — under the Breaking heading, not again in its own section.
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "release_notes.py"


def _module():
    spec = importlib.util.spec_from_file_location("release_notes", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("heading", "anchor"),
    [
        ("## [3.1.0] - 2026-10-01", "310---2026-10-01"),
        ("## [3.0.0] - 2026-09-30", "300---2026-09-30"),
        ("## Ünïcode_x", "ünïcode_x"),
        ("## snake_case heading", "snake_case-heading"),
        ("## Café Déjà vu", "café-déjà-vu"),
        ("## Größe (über) — 2!", "größe-über--2"),
        ("## A--b c", "a--b-c"),
    ],
)
def test_anchor_follows_githubs_rule(heading: str, anchor: str) -> None:
    assert _module()._anchor(heading) == anchor


HEADER = "# Changelog\n\n## [Unreleased]\n\n"


def _changelog(removed: str, deprecated: str) -> str:
    fixed = "".join(f"- Fixed entry {i} {'x' * 60} (#{1000 + i}).\n" for i in range(100))
    body = (
        f"### Changed\n\n- A normal change (#4).\n\n"
        f"### Deprecated\n\n{deprecated}\n"
        f"### Removed\n\n{removed}\n"
        f"### Fixed\n\n{fixed}"
    )
    return HEADER + "## [2.0.0] - 2026-09-30\n\n" + body + "\n## [1.0.0] - 2026-01-01\n"


BREAK_REMOVED = (
    "- **Breaking: REMOVED-BREAK** `--by` is gone (#6).\n"
    "  - REMOVED-BREAK-SUB use --as\n"
)
BREAK_DEPRECATED = "- **Breaking: DEPRECATED-BREAK** `OldThing` warns (#5).\n"
PLAIN_REMOVED = "- REMOVED-PLAIN `id_formats` (#7).\n"
PLAIN_DEPRECATED = "- DEPRECATED-PLAIN `Other` warns (#8).\n"


def test_breaking_entry_in_removed_or_deprecated_is_listed_once() -> None:
    text = _changelog(
        removed=BREAK_REMOVED + PLAIN_REMOVED,
        deprecated=BREAK_DEPRECATED + PLAIN_DEPRECATED,
    )
    notes = _module().release_notes(text, "2.0.0", limit=2000)
    for marker in ("REMOVED-BREAK**", "REMOVED-BREAK-SUB", "DEPRECATED-BREAK**"):
        assert notes.count(marker) == 1, (marker, notes)
    breaking = re.search(r"(?m)^#+ [^\n]*Breaking", notes)
    assert breaking
    after = notes[breaking.end():]
    nxt = re.search(r"(?m)^#+ ", after)
    breaking_body = after[: nxt.start()] if nxt else after
    assert "REMOVED-BREAK" in breaking_body and "DEPRECATED-BREAK" in breaking_body
    # the non-breaking entries still appear in their own sections, and the counts
    # still count the Breaking entries where they live
    assert "REMOVED-PLAIN" in notes and "DEPRECATED-PLAIN" in notes
    assert re.search(r"\b2 Removed\b", notes) and re.search(r"\b2 Deprecated\b", notes)


def test_removed_section_of_only_breaking_entries_gets_no_empty_heading() -> None:
    text = _changelog(removed=BREAK_REMOVED, deprecated=PLAIN_DEPRECATED)
    notes = _module().release_notes(text, "2.0.0", limit=2000)
    assert notes.count("REMOVED-BREAK**") == 1
    assert not re.search(r"(?m)^#+ Removed\s*$", notes)
