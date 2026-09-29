# ruff: noqa: F811  (the borrowed `graph` fixture)
"""Issue #1075: `KanbanService.get_blocked_items()` lists status-blocked items, but
never a finished one: hdd `abandoned` maps to canonical blocked and used to be
listed as blocked (#1066 fixed the same for MCP)."""

from __future__ import annotations

from tests.issues.test_577_blocked import graph  # noqa: F401 (fixture)


def test_abandoned_hdd_items_are_not_blocked(graph) -> None:
    ids = {i.id for i in graph.service().get_blocked_items()}
    assert not ids & {"H1.1", "H1.4"}, ids


def test_control_a_status_blocked_item_is_listed(graph) -> None:
    """Hard-coded, so a wrong `is_finished` can't agree with itself (#1078 review)."""
    blocked = {i.id for i in graph.service().get_blocked_items()}
    assert blocked == {"EXP-40", "EXP-41"}, blocked
