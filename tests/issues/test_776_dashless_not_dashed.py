# ruff: noqa: F811  -- the `world` fixture imported from #752's module is re-bound as an arg
"""Issue #776 — a dashless id does not count toward the dashed id space.

The [steer] on #776 is the spec. Since #765 the next-id allocator's frontmatter source
(local ``_scanned_next_id_number`` and origin ``_next_id_number_at``, through
``_number_in_space``) and ``_max_allocated`` (allocation records) judge an id's space by
``_id_space``, which strips an optional dash: ``_id_space("EXP003")`` is ``("EXP", 3)``,
so a dashless ``EXP003`` counted toward ``EXP``. The stem source (``_stem_id``) needs the
``EXP-`` head, so ``EXP003-x.md`` did not. One rule for every source: an id counts toward
``prefix`` only when the text before its number is exactly ``prefix + _id_sep(prefix)``,
case folded — ``_id_key``'s text.

- with only ``EXP012`` / ``exp012`` present (frontmatter, origin frontmatter, allocation
  records, local or on origin), the next ``EXP`` is 1 — RED for the frontmatter and
  allocation sources, GREEN (control) for the stem sources;
- likewise ``IDEA-R003`` is not in ``IDEA-R`` — RED for the same sources;
- a dashless id does not mask or inflate a dashed one: ``EXP-005`` plus ``EXP012`` gives 6;
- controls: ``EXP-012`` gives 13; ``IDEA-R-003`` gives ``IDEA-R`` 4 and ``IDEA`` 1;
  ``H130.2`` gives ``H130.`` 3 (the paper space has no separator); ``H1302`` is not in
  ``H130.``.

Reuses the #752 harness (``World``, ``seed``).
"""

from __future__ import annotations

import pytest

from tests.issues.test_585_create_push_loop import EXP_DIR, output_of
from tests.issues.test_603_push_failure_messages import invoke
from tests.issues.test_752_next_id_folds_case import (  # noqa: F401  (fixtures)
    ALL_SOURCES,
    HYP_DIR,
    IDEA_DIR,
    _clean_theme_cache,
    allocated_id,
    hdd,
    item_text,
    seed,
    service,
    world,
)
from yurtle_kanban.service import KanbanService

STEM_SOURCES = ["stem", "origin-stem"]
JUDGED_SOURCES = [s for s in ALL_SOURCES if s not in STEM_SOURCES]  # the #765 `_id_space` users


# --- RED: a dashless id is counted toward the dashed space -----------------------------------


@pytest.mark.parametrize("item_id", ["EXP012", "exp012"])
@pytest.mark.parametrize("source", JUDGED_SOURCES)
def test_dashless_id_not_counted_for_dashed_space(world, source, item_id) -> None:
    seed(world, source, item_id)
    assert service(world)._get_next_id_number("EXP") == 1, (
        f"dashless {item_id} ({source}) was counted in the EXP- space"
    )


@pytest.mark.parametrize("source", JUDGED_SOURCES)
def test_cli_next_id_ignores_dashless_id(world, monkeypatch, source) -> None:
    seed(world, source, "EXP012")
    result = invoke(world, monkeypatch, ["next-id", "EXP", "--no-sync", "--json"])
    out = output_of(result)
    assert result.exit_code == 0, out
    assert allocated_id(out) == "EXP-001", f"EXP012 ({source}) counted for EXP: {out}"


@pytest.mark.parametrize("source", JUDGED_SOURCES)
def test_dashless_id_does_not_inflate_a_dashed_one(world, source) -> None:
    seed(world, source, "EXP012")
    # a real dashed id, as a local stem (a source the dashless seed does not touch)
    (world.a / EXP_DIR / "EXP-005-other.md").write_text(item_text(None))
    assert service(world)._get_next_id_number("EXP") == 6, (
        f"dashless EXP012 ({source}) outranked EXP-005"
    )


@pytest.mark.parametrize("item_id", ["IDEA-R003", "idea-r003"])
@pytest.mark.parametrize("source", JUDGED_SOURCES)
def test_dashless_multi_segment_id_not_counted(world, source, item_id) -> None:
    hdd(world)
    seed(world, source, item_id, under=IDEA_DIR, item_type="idea")
    svc = service(world)
    assert (svc._get_next_id_number("IDEA-R"), svc._get_next_id_number("IDEA")) == (1, 1), (
        f"dashless {item_id} ({source}) was counted"
    )


@pytest.mark.parametrize("item_id", ["EXP012", "exp012"])
def test_number_in_space_needs_the_separator(item_id) -> None:
    assert KanbanService._number_in_space(item_id, "EXP") == 0


@pytest.mark.parametrize("item_id", ["EXP012", "exp012"])
def test_max_allocated_needs_the_separator(item_id) -> None:
    records = [{"id": item_id, "prefix": "EXP", "number": 12}]
    assert KanbanService._max_allocated(records, "EXP") == 0


# --- controls (GREEN before and after) -------------------------------------------------------


@pytest.mark.parametrize("item_id", ["EXP012", "exp012"])
@pytest.mark.parametrize("source", STEM_SOURCES)
def test_control_dashless_stem_not_counted(world, source, item_id) -> None:
    seed(world, source, item_id)
    assert service(world)._get_next_id_number("EXP") == 1


@pytest.mark.parametrize("item_id", ["EXP-012", "exp-012"])
@pytest.mark.parametrize("source", ALL_SOURCES)
def test_control_dashed_id_counts(world, source, item_id) -> None:
    seed(world, source, item_id)
    assert service(world)._get_next_id_number("EXP") == 13


@pytest.mark.parametrize("source", ALL_SOURCES)
def test_control_multi_segment_counts_in_its_own_space_only(world, source) -> None:
    hdd(world)
    seed(world, source, "IDEA-R-003", under=IDEA_DIR, item_type="idea")
    svc = service(world)
    assert (svc._get_next_id_number("IDEA-R"), svc._get_next_id_number("IDEA")) == (4, 1)


@pytest.mark.parametrize("paper_id", ["H130.2", "h130.2"])
@pytest.mark.parametrize("source", ALL_SOURCES)
def test_control_paper_space_has_no_separator(world, source, paper_id) -> None:
    hdd(world)
    seed(world, source, paper_id, under=HYP_DIR, item_type="hypothesis")
    assert service(world)._get_next_id_number("H130.") == 3


@pytest.mark.parametrize("source", ALL_SOURCES)
def test_control_run_on_digits_not_in_paper_space(world, source) -> None:
    hdd(world)
    seed(world, source, "H1302", under=HYP_DIR, item_type="hypothesis")
    assert service(world)._get_next_id_number("H130.") == 1


@pytest.mark.parametrize(
    ("item_id", "prefix", "expected"),
    [
        ("EXP-012", "EXP", 12),
        ("exp-012", "EXP", 12),
        ("IDEA-R-003", "IDEA-R", 3),
        ("IDEA-R-003", "IDEA", 0),
        ("H130.2", "H130.", 2),
        ("h130.2", "H130.", 2),
        ("H130.2", "H", 0),
        ("H1302", "H130.", 0),
        ("EXPR-050", "EXP", 0),
    ],
)
def test_control_number_in_space(item_id, prefix, expected) -> None:
    assert KanbanService._number_in_space(item_id, prefix) == expected
