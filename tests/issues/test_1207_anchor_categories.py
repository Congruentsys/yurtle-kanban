"""Issue #1207 — `_anchor` follows GitHub's slugger by Unicode category.

GitHub (github-slugger) lowercases the heading, keeps letters (L*), combining marks
(Mn, Mc, Me), decimal and letter numbers (Nd, Nl), connector punctuation (Pc, including `_`) and `-`, turns
spaces into `-`, and drops everything else — including other numbers (No) such as `²`.
Python's `\\w` got two of these wrong: it drops combining marks and keeps `²`.

The #1191 wording ("the Removed and Deprecated sections") predates #1203, which lists
a Breaking entry once, under Breaking changes; the unreleased fragment says so.
"""
from __future__ import annotations

import importlib.util
import unicodedata
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "release_notes.py"
FRAGMENT = ROOT / "changelog.d" / "1207.md"


def _module():
    spec = importlib.util.spec_from_file_location("release_notes", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("heading", "anchor"),
    [
        # Mn: an NFD heading keeps its combining acute accent (U+0301).
        (unicodedata.normalize("NFD", "## Café"), unicodedata.normalize("NFD", "café")),
        # Mc: a spacing combining mark (Devanagari vowel sign AA, U+093E) is kept.
        ("## का", "का"),
        # No: superscript two is dropped, not kept as a "digit".
        ("## x² y", "x-y"),
        ("## ½ cup", "-cup"),
        # Pc: connector punctuation other than `_` (U+203F UNDERTIE) is kept.
        ("## a‿b", "a‿b"),
        # Nd in another script is still a digit.
        ("## v٣", "v٣"),
        # Other punctuation and symbols drop; `-` and `_` stay.
        ("## a+b=c & d_e-f", "abc--d_e-f"),
    ],
)
def test_anchor_follows_githubs_slugger_by_category(heading: str, anchor: str) -> None:
    assert _module()._anchor(heading) == anchor


def test_version_headings_unchanged() -> None:  # control
    assert _module()._anchor("## [3.1.0] - 2026-10-01") == "310---2026-10-01"


def test_fragment_wording_excludes_breaking_from_removed_and_deprecated() -> None:
    text = FRAGMENT.read_text(encoding="utf-8")
    assert text.splitlines()[0] == "<!-- section: Fixed -->"
    flat = " ".join(text.split())
    assert "except Breaking entries, listed once under Breaking changes" in flat
    assert "(#1207)" in flat


def test_r1_enclosing_marks_and_letter_numbers_are_kept() -> None:
    """r1: github-slugger also keeps Me (U+20DD) and Nl (Roman numeral Ⅻ, U+216B)."""
    assert _module()._anchor("a\u20ddb") == "a\u20ddb"
    assert _module()._anchor("Part Ⅻ") == "part-ⅻ"
