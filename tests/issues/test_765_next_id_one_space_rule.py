# ruff: noqa: F811  -- the `world` fixture imported from #752's module is re-bound as an arg
"""Issue #765 — every next-ID source judges the id space by one rule.

The [steer] on #765 (bucket 1) is the spec. ``_get_next_id_number(prefix)`` is the max
over frontmatter ids, filename stems, allocation records, and the same three on
origin/<default>. Stems (``_stem_id``) and allocations (``_max_allocated``, through
``_id_space``) already judge an id by its own space; the frontmatter-id sources (local
``_scanned_next_id_number`` and origin ``_next_id_number_at``) used
``upper().startswith(prefix + sep)`` plus a trailing number, so ``IDEA-R-003`` counted
toward a bare ``IDEA`` space. After the fix every source uses ``_id_space``, case folded
(#752):

- with only ``IDEA-R-003`` (or ``idea-r-003``) present, the next ``IDEA`` is 1 and the
  next ``IDEA-R`` is 4 — RED for the frontmatter and origin-frontmatter sources;
- ``H130.2`` / ``h130.2`` counts toward ``H130.``, never toward ``H`` (control);
- ``_stem_id`` compares the stem's own head slice, so a stem whose characters change
  length when upper-cased cannot misalign the number: ``ß-12-x`` in the ASCII ``SS``
  space used to yield 2 (``"ß-12".upper()`` is ``"SS-12"``, then ``stem[3:]`` is
  ``"2-x"``) — RED. ``exp-12-straße`` in ``EXP`` (the non-ASCII character after the
  head) already gives 12 — control.

Reuses the #752 harness (``World``, ``seed``).
"""

from __future__ import annotations

import pytest

from tests.issues.test_585_create_push_loop import output_of
from tests.issues.test_603_push_failure_messages import invoke
from tests.issues.test_752_next_id_folds_case import (  # noqa: F401  (fixtures)
    ALL_SOURCES,
    HYP_DIR,
    IDEA_DIR,
    _clean_theme_cache,
    allocated_id,
    hdd,
    seed,
    service,
    world,
)
from yurtle_kanban.service import KanbanService

FRONTMATTER_SOURCES = ["frontmatter", "origin-frontmatter"]
OTHER_SOURCES = [s for s in ALL_SOURCES if s not in FRONTMATTER_SOURCES]


# --- RED: the frontmatter sources count a multi-segment id toward its bare head -------------


@pytest.mark.parametrize("idea_id", ["IDEA-R-003", "idea-r-003"])
@pytest.mark.parametrize("source", FRONTMATTER_SOURCES)
def test_multi_segment_frontmatter_id_not_counted_for_bare_prefix(world, source, idea_id) -> None:
    hdd(world)
    seed(world, source, idea_id, under=IDEA_DIR, item_type="idea")
    assert service(world)._get_next_id_number("IDEA") == 1, (
        f"{idea_id} ({source}) was counted in the bare IDEA space"
    )


@pytest.mark.parametrize("source", FRONTMATTER_SOURCES)
def test_multi_segment_frontmatter_id_both_spaces_at_once(world, source) -> None:
    hdd(world)
    seed(world, source, "IDEA-R-003", under=IDEA_DIR, item_type="idea")
    svc = service(world)
    assert (svc._get_next_id_number("IDEA"), svc._get_next_id_number("IDEA-R")) == (1, 4)


@pytest.mark.parametrize("source", FRONTMATTER_SOURCES)
def test_cli_next_id_bare_prefix_ignores_multi_segment_id(world, monkeypatch, source) -> None:
    hdd(world)
    seed(world, source, "IDEA-R-003", under=IDEA_DIR, item_type="idea")
    result = invoke(world, monkeypatch, ["next-id", "IDEA", "--no-sync", "--json"])
    out = output_of(result)
    assert result.exit_code == 0, out
    assert allocated_id(out) == "IDEA-001", f"IDEA-R-003 ({source}) counted for IDEA: {out}"


def test_stem_id_not_misaligned_by_upper_case_expansion() -> None:
    # `"ß-12".upper()` is `"SS-12"`: the old code matched the ASCII head `SS-` on the
    # upper-cased stem, then sliced the ORIGINAL stem by len(head) == 3 -> "2-x" -> 2.
    # Whether `ß` is the `SS` space is the fold's call; 2 is never the number.
    assert KanbanService._stem_id("ß-12-x", "SS") in (None, 12)
    assert KanbanService._stem_id("ﬀ-12-x", "FF") in (None, 12)  # U+FB00 -> "FF"


# --- controls (GREEN before and after) -------------------------------------------------------


@pytest.mark.parametrize("source", OTHER_SOURCES)
def test_control_stem_and_allocation_ignore_multi_segment_for_bare(world, source) -> None:
    hdd(world)
    seed(world, source, "IDEA-R-003", under=IDEA_DIR, item_type="idea")
    assert service(world)._get_next_id_number("IDEA") == 1


@pytest.mark.parametrize("idea_id", ["IDEA-R-003", "idea-r-003"])
@pytest.mark.parametrize("source", ALL_SOURCES)
def test_control_multi_segment_counts_in_its_own_space(world, source, idea_id) -> None:
    hdd(world)
    seed(world, source, idea_id, under=IDEA_DIR, item_type="idea")
    assert service(world)._get_next_id_number("IDEA-R") == 4


@pytest.mark.parametrize("paper_id", ["H130.2", "h130.2"])
@pytest.mark.parametrize("source", ALL_SOURCES)
def test_control_paper_scoped_id_only_in_its_space(world, source, paper_id) -> None:
    hdd(world)
    seed(world, source, paper_id, under=HYP_DIR, item_type="hypothesis")
    svc = service(world)
    assert svc._get_next_id_number("H") == 1, f"{paper_id} ({source}) counted in H"
    assert svc._get_next_id_number("H130.") == 3


@pytest.mark.parametrize("source", ALL_SOURCES)
def test_control_uppercase_counts_as_before(world, source) -> None:
    seed(world, source, "EXP-12")
    assert service(world)._get_next_id_number("EXP") == 13


@pytest.mark.parametrize("other", ["EXPR-50", "expr-50"])
@pytest.mark.parametrize("source", ALL_SOURCES)
def test_control_longer_prefix_stays_apart(world, source, other) -> None:
    seed(world, source, other)
    assert service(world)._get_next_id_number("EXP") == 1


@pytest.mark.parametrize(
    ("stem", "prefix", "expected"),
    [
        ("exp-12-straße", "EXP", 12),  # non-ASCII after the head
        ("EXP-12-STRASSE", "EXP", 12),
        ("EXP-608-Some-Title", "EXP", 608),
        ("h130.2-thing", "H130.", 2),
        ("EXPR-50-x", "EXP", None),
        ("H130.2-thing", "H", None),
    ],
)
def test_control_stem_id(stem, prefix, expected) -> None:
    assert KanbanService._stem_id(stem, prefix) == expected
