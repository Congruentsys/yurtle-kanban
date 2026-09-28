"""#907: init's fallback prefix keeps combining marks and counts [:4] by base+marks.

Found in the PR #906 (#875) review. `_default_prefix` (#840, #875):

- drops combining marks that NFC does not compose, although `id_prefix` accepts
  category M: `हिन्दी` gives `हनद`, losing its vowel signs and virama.
  Expected: keep category-M characters that follow a kept letter.
- slices `[:4]` by code points after `.upper()`, which can decompose: `ΐabc` gives
  `Ϊ́A`, the accented letter using 3 slots, and the slice can split a combining
  sequence. Expected: fold with `fold_id` and count a base plus its marks as one unit.

Expected values below are derived from those two rules: fold the key with
`fold_id`, keep letters/digits (from the first letter) plus every category-M
character that follows a kept letter, group each base with its following marks
into one unit, and keep the first four units.

Controls: plain ASCII keys (`spike` -> `SPIK`, `my-project` -> `MYPR`), an
NFC-composable accent (`Café-app` -> `CAFÉ`, precomposed É), and a leading bare
mark (nothing kept precedes it, so it is dropped) are unchanged.
"""

from __future__ import annotations

import unicodedata
from pathlib import Path

import pytest

# Reuse #840's fixtures and helpers: init with a one-type custom theme.
from tests.issues.test_840_init_prefix_fallback import (  # noqa: F401
    _clean_theme_cache,
    _init_template,
    _template_prefix,
)
from yurtle_kanban.cli import _default_prefix
from yurtle_kanban.models import fold_id, id_prefix

HINDI = "हिन्दी"  # ह ि | न ् | द ी — 3 units
HINDI_LONG = "हिन्दीभाषा"  # हि | न् | दी | भा | षा — 5 units
IOTA = "ΐ"  # U+0390; upper() decomposes to Ι + U+0308 + U+0301
FOLDED_IOTA = "Ϊ́"  # fold_id("ΐ"): Ϊ (U+03AA) + COMBINING ACUTE


def _is_mark(c: str) -> bool:
    return unicodedata.category(c).startswith("M")


def _units(text: str) -> list[str]:
    """`text` split into base + following marks units (a leading mark is its own unit)."""
    units: list[str] = []
    for c in text:
        if _is_mark(c) and units:
            units[-1] += c
        else:
            units.append(c)
    return units


def test_fixtures_are_what_they_claim() -> None:
    """Non-vacuity: the keys carry the marks and decompositions the issue names."""
    assert [unicodedata.category(c) for c in HINDI] == ["Lo", "Mc", "Lo", "Mn", "Lo", "Mc"]
    assert len(_units(HINDI)) == 3 and len(_units(HINDI_LONG)) == 5
    assert unicodedata.is_normalized("NFC", HINDI) and fold_id(HINDI) == HINDI
    assert IOTA.upper() == "Ϊ́"  # 3 code points
    assert fold_id(IOTA) == FOLDED_IOTA
    assert len(_units(FOLDED_IOTA)) == 1


# --- 1. marks after a kept letter are kept ------------------------------------------

MARK_CASES = [
    # key, expected, how derived
    pytest.param(HINDI, "हिन्दी", id="hindi-3-units-all-kept"),
    pytest.param(HINDI_LONG, "हिन्दीभा", id="hindi-5-units-first-4"),
    pytest.param("abcq̃xyz", "ABCQ̃", id="4th-unit-q-tilde"),
    pytest.param("ab́̂cdef", "AB́̂CD", id="two-marks-on-2nd"),
]


@pytest.mark.parametrize("key, expected", MARK_CASES)
def test_default_prefix_keeps_marks_after_kept_letter(key: str, expected: str) -> None:
    prefix = _default_prefix(key)
    assert prefix == expected, f"{key!r} -> {prefix!r}, want {expected!r}"
    assert id_prefix(prefix) is not None, prefix


def test_hindi_keeps_vowel_signs_and_virama() -> None:
    prefix = _default_prefix(HINDI)
    for mark in ("ि", "्", "ी"):
        assert mark in prefix, f"{HINDI!r} -> {prefix!r} lost {mark!r}"


# --- 2. four units, folded, never split ---------------------------------------------

UNIT_CASES = [
    pytest.param(IOTA + "abc", FOLDED_IOTA + "ABC", id="iota-first"),
    pytest.param("abc" + IOTA + "xyz", "ABC" + FOLDED_IOTA, id="iota-4th-of-6"),
    pytest.param("ab" + IOTA + IOTA + "xy", "AB" + FOLDED_IOTA * 2, id="iota-3rd-and-4th"),
]


@pytest.mark.parametrize("key, expected", UNIT_CASES)
def test_default_prefix_counts_base_plus_marks_as_one_unit(key: str, expected: str) -> None:
    prefix = _default_prefix(key)
    assert prefix == expected, f"{key!r} -> {prefix!r}, want {expected!r}"
    assert len(_units(prefix)) == 4, _units(prefix)
    assert id_prefix(prefix) is not None, prefix


@pytest.mark.parametrize("key", [p.values[0] for p in MARK_CASES + UNIT_CASES])
def test_default_prefix_never_splits_a_combining_sequence(key: str) -> None:
    """Each unit of the prefix is a whole unit of the folded key, in order."""
    prefix = _default_prefix(key)
    assert prefix, key
    assert not _is_mark(prefix[0]), f"{key!r} -> {prefix!r} starts with a bare mark"
    source = [u for u in _units(fold_id(key)) if u[0].isalpha() or u[0].isdigit()]
    got = _units(prefix)
    assert got == source[: len(got)], f"{key!r} -> {got!r}, units of key {source!r}"
    assert unicodedata.is_normalized("NFC", prefix), prefix


# --- through `init` ------------------------------------------------------------------


@pytest.mark.parametrize(
    "key, expected",
    [
        pytest.param(HINDI, "हिन्दी", id="hindi"),
        pytest.param(IOTA + "abc", FOLDED_IOTA + "ABC", id="iota"),
    ],
)
def test_init_template_prefix_keeps_marks_by_unit(
    key: str, expected: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prefix = _template_prefix(_init_template(tmp_path, monkeypatch, key))
    assert prefix == expected, f"{key!r} -> {prefix!r}, want {expected!r}"


# --- 3. controls (green before the fix) ----------------------------------------------


@pytest.mark.parametrize(
    "key, expected",
    [
        pytest.param("spike", "SPIK", id="ascii-spike"),
        pytest.param("my-project", "MYPR", id="ascii-dashed"),
        pytest.param("1ab", "AB", id="leading-digit"),
        pytest.param("Café-app", "CAFÉ", id="precomposed-accent"),
        pytest.param("Café-app", "CAFÉ", id="decomposed-accent-nfc"),
        pytest.param("́abc", "ABC", id="leading-bare-mark-dropped"),
    ],
)
def test_control_default_prefix_unchanged(key: str, expected: str) -> None:
    prefix = _default_prefix(key)
    assert prefix == expected, f"{key!r} -> {prefix!r}, want {expected!r}"
    assert unicodedata.is_normalized("NFC", prefix)


def test_control_init_composable_accent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prefix = _template_prefix(_init_template(tmp_path, monkeypatch, "Café-app"))
    assert prefix == "CAFÉ"
