"""Issue #775 — ``_stem_holds`` slices the stem where it compared it.

The [steer] on #775 is the spec. ``KanbanService._stem_holds(stem, key)`` compared
``stem.upper().startswith(text)`` but then sliced the ORIGINAL stem at ``len(text)``.
A character whose upper-case form is longer (``ß`` -> ``SS``, ``ﬀ`` -> ``FF``,
``ﬃ`` -> ``FFI``) makes the upper-cased stem longer than the stem, so the slice lands
inside the number: ``"ß-12-x".upper()`` is ``"SS-12-X"``, which starts with ``SS-``,
then ``stem[3:]`` is ``"2-x"`` and ``ß-12`` "holds" ``SS-2``.

After the fix ``_stem_holds`` compares the stem's own head slice,
``stem[:len(text)].upper()``, with the key's text (which ``_id_key`` folds) and reads
the number from that same offset — the rule ``_stem_id`` follows since #765.

- RED: every misaligned pair below holds today (a false positive) and must not.
- No false negative exists: whenever ``stem[:n].upper() == text`` the old
  ``stem.upper().startswith(text)`` is also true and both slice at ``n``, so the old
  code never rejects what the new one accepts. The ``ß-12-x`` / ``SS-12`` control pins
  that the fix does not start accepting a stem whose head only matches upper-cased.
- Controls (green before and after): ``EXP-003-Title`` and ``exp-003.v2`` hold
  (``EXP-``, 3); ``H1.2-Title`` does not hold (``H``, 1); ``EXPR-3`` and ``exp3`` do
  not hold (``EXP-``, 3); a non-ASCII character after the head is harmless.
"""

from __future__ import annotations

import pytest

from yurtle_kanban.service import KanbanService

# (stem, id): the old code's misaligned slice reads a number that is not the stem's.
MISALIGNED = [
    ("ß-12-x", "SS-2"),  # stem[3:] is "2-x"
    ("ß-12-x", "ss-2"),  # the key's text is folded; same misalignment
    ("ß12", "SS2"),  # no separator: stem[2:] is "2"
    ("ß-33", "SS-3"),  # stem[3:] is "3"
    ("ﬀ-12", "FF-2"),  # U+FB00 -> "FF"
    ("ﬃ-123", "FFI-3"),  # U+FB03 -> "FFI": off by two, stem[4:] is "3"
    ("Straße-12", "STRASSE-2"),  # the expanding character mid-head
]


@pytest.mark.parametrize(("stem", "item_id"), MISALIGNED)
def test_misaligned_slice_does_not_hold(stem: str, item_id: str) -> None:
    key = KanbanService._id_key(item_id)
    assert key is not None
    assert not KanbanService._stem_holds(stem, key), (
        f"{stem!r} held {item_id!r} by reading a number from a misaligned slice"
    )


@pytest.mark.parametrize(
    ("stem", "prefix", "num"),
    [("ß-12-x", "SS", 2), ("ß-12-x", "SS", 12), ("ﬀ-12", "FF", 2), ("ﬃ-123", "FFI", 3)],
)
def test_stem_holds_agrees_with_stem_id(stem: str, prefix: str, num: int) -> None:
    """One rule for both: ``_stem_id`` (since #765) says these stems hold no id in the
    prefix's space, so ``_stem_holds`` must not say they hold one."""
    sep = KanbanService._id_sep(prefix)
    held = KanbanService._stem_holds(stem, (prefix + sep, num))
    assert held == (KanbanService._stem_id(stem, prefix) == num)


# --- controls: green before and after ---------------------------------------------------


@pytest.mark.parametrize(
    ("stem", "item_id", "holds"),
    [
        ("EXP-003-Title", "EXP-3", True),
        ("exp-003.v2", "EXP-3", True),
        ("EXP-003-Straße", "EXP-3", True),  # non-ASCII after the head: no misalignment
        ("H1.2-Title", "H1", False),
        ("EXPR-3", "EXP-3", False),
        ("exp3", "EXP-3", False),
        ("ß-12-x", "SS-12", False),  # the head only matches once upper-cased: no false negative to fix
        ("SS-12-x", "ss-12", True),  # plain ASCII SS still holds, case folded
    ],
)
def test_controls(stem: str, item_id: str, holds: bool) -> None:
    key = KanbanService._id_key(item_id)
    assert key is not None
    assert KanbanService._stem_holds(stem, key) is holds
