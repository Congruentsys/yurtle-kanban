# ruff: noqa: F811  (the `world` fixture is imported, then used as a parameter)
"""Issue #766 — `parent_link_state` and `link_parent` check in one order.

`_parent_link_edit` (behind `link_parent`) checks the child type's inverse relation
before it looks for the parent; `parent_link_state` looked for the parent first. So
an unknown child type under a missing parent was 'no-relation' from `link_parent`
but 'missing' from `parent_link_state`.

Decided behaviour ([steer] on #766, bucket 1): the relation is checked first on
both — an unknown child type is 'no-relation' whether or not the parent exists.
Controls: a known type under a missing parent is 'missing' on both; a known type
under a present parent with no turtle block is 'no-block' on both.

Reuses the #750 harness (`prepare` puts PAPER-130 in 'no-block' or 'missing').
"""

from __future__ import annotations

import pytest

from tests.issues.test_645_parent_in_cas import HYP
from tests.issues.test_674_parent_edges import (  # noqa: F401  (fixtures)
    _clean_theme_cache,
    world,
)
from tests.issues.test_750_parent_parse_once import CHILD, prepare, service

PARENT = "PAPER-130"

# (parent state set up by `prepare`, child type) -> the one answer both give
CASES = [
    pytest.param("missing", "widget", "no-relation", id="unknown-type-missing-parent"),
    pytest.param("no-block", "widget", "no-relation", id="unknown-type-present-parent"),
    pytest.param("missing", "hypothesis", "missing", id="known-type-missing-parent"),
    pytest.param("no-block", "hypothesis", "no-block", id="known-type-present-parent"),
]


@pytest.mark.parametrize(("parent_state", "child_type", "expected"), CASES)
def test_parent_link_state(world, monkeypatch, parent_state, child_type, expected) -> None:
    prepare(world, monkeypatch, HYP, parent_state)
    assert service(world).parent_link_state(PARENT, child_type, CHILD) == expected


@pytest.mark.parametrize(("parent_state", "child_type", "expected"), CASES)
def test_link_parent(world, monkeypatch, parent_state, child_type, expected) -> None:
    prepare(world, monkeypatch, HYP, parent_state)
    assert service(world).link_parent(PARENT, child_type, CHILD) == expected


@pytest.mark.parametrize(("parent_state", "child_type", "expected"), CASES)
def test_parent_link_state_and_link_parent_agree(
    world, monkeypatch, parent_state, child_type, expected
) -> None:
    prepare(world, monkeypatch, HYP, parent_state)
    svc = service(world)
    # neither writes in these states, so the order of the two calls doesn't matter
    state = svc.parent_link_state(PARENT, child_type, CHILD)
    linked = svc.link_parent(PARENT, child_type, CHILD)
    assert state == linked == expected, (state, linked)
