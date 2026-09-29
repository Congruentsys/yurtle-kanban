# ruff: noqa: F811  (fixtures are imported, then re-bound as parameters)
"""#1139: the dependency graph joins a stored alternate spelling to its node.

#641 ruled that `EXP-5` is `EXP-05` is `EXP-005`; #795 applied it to duplicate ids and
#1125 to `get_item` and to the edges `update --add-dep` writes. [steer] on #1139: graph
edges resolve each STORED dependency by `_id_key` to the node on the board, and an
exact spelling wins. So a file that already says `depends_on: [EXP-05]` points at
`EXP-5`'s node, and:

- `validate` reports the cycle EXP-5 → EXP-006 → EXP-5 (EXP-006's file says `EXP-05`);
- `blocked` and `pickable` see EXP-5 waiting on EXP-006 AND EXP-006 waiting on EXP-5,
  both on a `depends_on` cycle (not EXP-006 waiting on an unknown `EXP-05`);
- the push side's graph (`_dependency_board_at`) joins the edge the same way;
- `update EXP-5 --add-dep EXP-006`, closing a cycle through the stored `EXP-05`, is
  refused as a cycle.

Controls: `EXP5` is not `EXP-5` (#661), so it stays unknown and closes no cycle; a
target on no board stays dangling; with both `EXP-3` and `EXP-003` on the board, an
edge spelled `EXP-3` goes to `EXP-3`, not `EXP-003` (#795).

Fixture: #576's two-board repo (EXP-1..EXP-5, H1.1).
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.issues.test_576_cli_update_deps import (  # noqa: F401
    Repo,
    _flat,
    _ok,
    _refused,
    invoke,
    repo,
)
from tests.issues.test_795_duplicate_ids_id_key import padded  # noqa: F401
from tests.issues.test_1125_id_key_lookup import _write


def _loop(repo: Repo, back: str = "EXP-05") -> None:
    """EXP-5 depends on EXP-006; EXP-006's file depends on `back`; committed."""
    repo.write("EXP-5", ["EXP-006"])
    _write(repo, "work/expeditions/EXP-006-padded.md", "EXP-006", [back])
    repo.commit(f"EXP-5 -> EXP-006 -> {back}")


def _cycles(repo: Repo) -> list[list[str]]:
    result = invoke(["validate", "--json"])
    data = json.loads(result.output)
    return [i["ids"] for i in data["issues"] if i["type"] == "dependency_cycle"]


def _unmet(repo: Repo, item_id: str) -> dict[str, str]:
    """`blocked`'s unmet dependencies of `item_id`: {id: state}."""
    for item, _, unmet in repo.service().blocked():
        if item.id == item_id:
            return {n.id: n.state for n in unmet}
    return {}


# --- the cycle through a stored alternate spelling ----------------------------------------


def test_validate_reports_cycle_through_stored_spelling(repo: Repo) -> None:
    _loop(repo)
    result = invoke(["validate"])
    out = _flat(result.output)
    assert result.exit_code == 1, result.output
    assert "DEPENDENCY CYCLE" in out, out
    cycles = _cycles(repo)
    assert len(cycles) == 1, cycles
    assert set(cycles[0]) == {"EXP-5", "EXP-006"}, cycles


def test_graph_edge_names_the_node(repo: Repo) -> None:
    _loop(repo)
    service = repo.service()
    assert service.dependency_graph()["EXP-006"] == ["EXP-5"]
    assert service.find_cycle("EXP-5") is not None
    assert ("EXP-006", "EXP-05") not in service.dangling_dependencies()


def test_push_side_graph_joins_stored_spelling(repo: Repo) -> None:
    _loop(repo)
    graph, _ = repo.service()._dependency_board_at(repo.head())
    assert graph["EXP-006"] == ["EXP-5"], graph


def test_blocked_sees_both_waiting_on_the_cycle(repo: Repo) -> None:
    _loop(repo)
    assert _unmet(repo, "EXP-5") == {"EXP-006": "cycle"}
    assert _unmet(repo, "EXP-006") == {"EXP-5": "cycle"}


def test_pickable_sees_both_waiting_on_the_cycle(repo: Repo) -> None:
    _loop(repo)
    service = repo.service()
    five, six = service.get_item("EXP-5"), service.get_item("EXP-006")
    assert five is not None and six is not None
    ok5, why5 = service.pickable(five, None)
    ok6, why6 = service.pickable(six, None)
    assert not ok5 and why5.startswith("waiting on EXP-006 (cycle:"), why5
    assert not ok6 and why6.startswith("waiting on EXP-5 (cycle:"), why6


def test_add_dep_closing_cycle_through_stored_spelling_is_refused(repo: Repo) -> None:
    """EXP-006's file says EXP-05; `EXP-5 --add-dep EXP-006` closes the cycle."""
    _write(repo, "work/expeditions/EXP-006-padded.md", "EXP-006", ["EXP-05"])
    repo.commit("EXP-006 -> EXP-05")
    _refused(repo, ["update", "EXP-5", "--add-dep", "EXP-006"], "cycle")


# --- controls ----------------------------------------------------------------------------


def test_control_no_separator_is_not_joined(repo: Repo) -> None:
    """`EXP5` is not `EXP-5` (#661): no cycle, and it stays unknown."""
    _loop(repo, back="EXP5")
    assert _cycles(repo) == []
    assert _unmet(repo, "EXP-006") == {"EXP5": "unknown"}
    assert ("EXP-006", "EXP5") in repo.service().dangling_dependencies()
    _ok(["update", "EXP-5", "--add-dep", "EXP-1"])  # no cycle refused


def test_control_unknown_target_stays_unknown(repo: Repo) -> None:
    _loop(repo, back="EXP-050")
    assert _cycles(repo) == []
    assert _unmet(repo, "EXP-006") == {"EXP-050": "unknown"}
    service = repo.service()
    assert service.dependency_graph()["EXP-006"] == ["EXP-050"]
    assert ("EXP-006", "EXP-050") in service.dangling_dependencies()


def test_control_exact_spelling_wins_on_a_duplicate(repo: Repo, padded: Path) -> None:
    """EXP-3 and EXP-003 are both on the board. EXP-003 depends on EXP-4, EXP-4 on
    EXP-3 (the fixture's): the edge EXP-4 → EXP-3 is EXP-3's, so there is no cycle."""
    _write(repo, "work/expeditions/EXP-003-padded.md", "EXP-003", ["EXP-4"])
    repo.commit("EXP-003 -> EXP-4")
    service = repo.service()
    graph = service.dependency_graph()
    assert graph["EXP-4"] == ["EXP-3"] and graph["EXP-003"] == ["EXP-4"], graph
    assert _cycles(repo) == []


def test_blocked_tree_stops_at_the_node_either_way(repo: Repo) -> None:
    """The tree walks a node once per root, however an edge spells it: each side
    shows the other, then itself again, and stops."""
    _loop(repo)
    for item, _, unmet in repo.service().blocked():
        if item.id in ("EXP-5", "EXP-006"):
            (other,) = unmet
            (back,) = other.children
            assert back.id == item.id and back.children == [], (item.id, unmet)
