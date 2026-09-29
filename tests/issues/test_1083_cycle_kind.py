# ruff: noqa: F811  -- fixtures imported from #577's/#1068's/#1077's/#1081's modules are re-bound as args
"""Issue #1083: ``DepNode`` carries its cycle kind instead of the CLI re-deriving it.

Spec: the issue body. ``unmet_dependencies``' nodes gain ``cycle_kind``:
``"supersession"`` for a node whose ``cycle`` state comes from its ``superseded_by``
walk (#581), ``"depends_on"`` for one on a dependency cycle (#576), ``None`` for
every other node (unfinished, dead, unknown). It is set where ``_dep_state``
decides the state; ``blocked``'s renderer reads it, and ``blocked --json`` (so MCP
``kanban_get_blocked``, #1066) reports it. The text tree's marking is unchanged
(#1081's expectations, re-run here against the same fixtures).

Reuses #577's fixture (``graph``), #1068's (``superseded``), #1077's (``repro``) and
#1081's (``both``).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests.issues.test_575_pickable_next import Repo
from tests.issues.test_577_blocked import entries, graph  # noqa: F401
from tests.issues.test_1066_mcp_blocked import call
from tests.issues.test_1068_blocked_followups import run, superseded  # noqa: F401
from tests.issues.test_1077_blocked_edges import repro  # noqa: F401
from tests.issues.test_1081_supersession_mark import (  # noqa: F401
    _block,
    _line,
    both,
)
from yurtle_kanban.service import DepNode

pytestmark = pytest.mark.usefixtures("claim_env")


def _kinds(nodes: list[Any]) -> dict[str, set[Any]]:
    """Every node's id -> the set of cycle_kinds it carries anywhere in the tree."""
    out: dict[str, set[Any]] = {}

    def walk(ns: list[Any]) -> None:
        for n in ns:
            if isinstance(n, dict):
                out.setdefault(n["id"], set()).add(n.get("cycle_kind", "<missing>"))
                walk(n["children"])
            else:
                out.setdefault(n.id, set()).add(getattr(n, "cycle_kind", "<missing>"))
                walk(n.children)

    walk(nodes)
    return out


def _unmet(repo: Repo, item_id: str) -> list[DepNode]:
    return list(repo.service().unmet_dependencies(repo.item(item_id)))


# --- the service: DepNode.cycle_kind ----------------------------------------------------


def test_depnode_has_cycle_kind_field() -> None:
    node = DepNode(id="EXP-1", status="ready", assignee=None, state="unfinished")
    assert node.cycle_kind is None


def test_supersession_cycle_node(superseded: Repo) -> None:
    nodes = _unmet(superseded, "EXP-112")
    assert [(n.id, n.state) for n in nodes] == [("EXP-12", "cycle")], nodes
    assert nodes[0].cycle_kind == "supersession", nodes


def test_depends_on_cycle_node(graph: Repo) -> None:
    nodes = _unmet(graph, "EXP-21")
    assert [(n.id, n.state) for n in nodes] == [("EXP-22", "cycle")], nodes
    assert nodes[0].cycle_kind == "depends_on", nodes


def test_both_kinds_in_one_tree(both: Repo) -> None:
    kinds = _kinds(_unmet(both, "EXP-112"))
    assert kinds["EXP-12"] == {"supersession"}, kinds
    assert kinds["EXP-30"] == {"depends_on"}, kinds


def test_repro_kinds(repro: Repo) -> None:
    kinds = _kinds(_unmet(repro, "EXP-112"))
    assert kinds["EXP-12"] == {"supersession"}, kinds
    assert kinds["EXP-30"] == {"depends_on"}, kinds
    assert kinds["EXP-31"] == {"depends_on"}, kinds


@pytest.mark.parametrize(
    ("root", "node", "state"),
    [("EXP-1", "EXP-2", "unfinished"), ("EXP-20", "EXP-99", "unknown"),
     ("EXP-30", "H1.1", "dead")],
)
def test_non_cycle_nodes_have_none(graph: Repo, root: str, node: str, state: str) -> None:
    nodes = _unmet(graph, root)
    found = [n for n in nodes if n.id == node]
    assert len(found) == 1 and found[0].state == state, nodes
    assert found[0].cycle_kind is None, found[0]
    for kinds in _kinds(nodes).values():
        assert kinds <= {None, "depends_on", "supersession"}, kinds


# --- blocked --json and MCP kanban_get_blocked carry the key ---------------------------


def test_json_carries_cycle_kind(graph: Repo) -> None:
    got = entries()
    assert got["EXP-21"]["unmet"][0]["cycle_kind"] == "depends_on", got["EXP-21"]
    assert got["EXP-1"]["unmet"][0]["cycle_kind"] is None, got["EXP-1"]
    assert got["EXP-1"]["unmet"][0]["children"][0]["cycle_kind"] is None, got["EXP-1"]
    for entry in got.values():
        for kinds in _kinds(entry["unmet"]).values():
            assert "<missing>" not in kinds, entry


def test_json_both_kinds(both: Repo) -> None:
    result = run(["blocked", "--json"])
    assert result.exit_code == 0, result.output
    items = {e["id"]: e for e in json.loads(result.stdout)["items"]}
    kinds = _kinds(items["EXP-112"]["unmet"])
    assert kinds["EXP-12"] == {"supersession"}, kinds
    assert kinds["EXP-30"] == {"depends_on"}, kinds


def test_mcp_carries_cycle_kind(both: Repo) -> None:
    items = {e["id"]: e for e in call(both)["items"]}
    kinds = _kinds(items["EXP-112"]["unmet"])
    assert kinds["EXP-12"] == {"supersession"}, kinds
    assert kinds["EXP-30"] == {"depends_on"}, kinds


# --- the text tree's marking is unchanged (#1081's expectations) ------------------------


def test_text_supersession_marked_despite_its_own_arrow(both: Repo) -> None:
    block = _block("EXP-112")
    assert _line(block, "EXP-12").count("— cycle") == 1, "\n".join(block)
    assert "cycle" not in _line(block, "EXP-30"), "\n".join(block)


def test_text_pure_supersession_marked_once(superseded: Repo) -> None:
    assert _line(_block("EXP-112"), "EXP-12").count("cycle") == 1


def test_text_depends_on_cycle_not_double_marked(graph: Repo) -> None:
    block = _block("EXP-21")
    assert sum("↻ cycle" in ln for ln in block) == 1, block
    assert not [ln for ln in block if "cycle" in ln and "↻" not in ln], block


def test_renderer_no_longer_rederives() -> None:
    from yurtle_kanban import cli as cli_mod

    assert not hasattr(cli_mod, "_on_supersession_cycle"), (
        "the CLI still re-derives the cycle kind; it should read DepNode.cycle_kind"
    )
