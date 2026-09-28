"""#875: init's fallback prefix keeps a combining accent.

`_default_prefix` (#840) keeps a type key's letters and digits. A key written
decomposed (`e` + U+0301 COMBINING ACUTE ACCENT + `xp`) loses the accent: the
combining mark is neither alpha nor digit, so `init` writes the template prefix
`EXP` instead of `ÉXP`.

Decided ([steer] on #875, bucket 1): `_default_prefix` NFC-normalises the type
key first, so the decomposed and the precomposed spellings give the same `ÉXP`
(U+00C9 + `XP`).

Controls: the precomposed key (U+00E9 + `xp`) already gives `ÉXP`.
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

DECOMPOSED = "éxp"  # e + COMBINING ACUTE ACCENT + xp
PRECOMPOSED = "éxp"  # é + xp
EXPECTED = "ÉXP"  # É + XP (NFC)


def test_spellings_are_what_they_claim() -> None:
    """Non-vacuity: the two keys differ as strings but are canonically equal."""
    assert DECOMPOSED != PRECOMPOSED
    assert len(DECOMPOSED) == 4 and len(PRECOMPOSED) == 3
    assert unicodedata.normalize("NFC", DECOMPOSED) == PRECOMPOSED
    assert unicodedata.is_normalized("NFC", EXPECTED)


# --- _default_prefix directly -------------------------------------------------------


def test_default_prefix_decomposed_key_is_nfc() -> None:
    prefix = _default_prefix(DECOMPOSED)
    assert prefix == EXPECTED, f"{DECOMPOSED!r} -> {prefix!r}, want {EXPECTED!r}"


def test_control_default_prefix_precomposed_key() -> None:
    assert _default_prefix(PRECOMPOSED) == EXPECTED


# --- through `init` ------------------------------------------------------------------


def test_init_decomposed_key_template_prefix_is_nfc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prefix = _template_prefix(_init_template(tmp_path, monkeypatch, DECOMPOSED))
    assert prefix == EXPECTED, f"{DECOMPOSED!r} -> {prefix!r}, want {EXPECTED!r}"


def test_control_init_precomposed_key_template_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prefix = _template_prefix(_init_template(tmp_path, monkeypatch, PRECOMPOSED))
    assert prefix == EXPECTED
