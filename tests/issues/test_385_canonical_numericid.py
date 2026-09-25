# ruff: noqa: F811  -- pytest fixtures imported from the #349 test module are re-bound as args
"""Issue #385 — an item's ``kb:numericId`` is its own, and a yurtle block can't redefine it.

``UnifiedGraph.add_item`` adds ``kb:numericId`` (the integer derived from the item's ID)
and then merges every triple of the item's per-file graph (fenced yurtle/turtle blocks).
A block that adds its own ``kb:numericId`` gives the item a second value, so:

* a string value (``"abc"``, ``"999"``) sorts above every integer in
  ``ORDER BY DESC(?numId)`` and drags the item to the top;
* a second integer satisfies an id-range filter the item's own id does not
  (``item:PAPER-002 kb:numericId 999`` passes ``id_min=500``; ``0`` passes ``id_max``).

Decided ([steer], bucket 2; G1: an item's identity comes from its ID): the merge skips
block triples whose predicate is ``kb:numericId``. Each item has exactly one
``kb:numericId`` in the unified graph, an ``xsd:integer`` equal to ``item.numeric_id``.
All other block triples (a second ``rdf:type``, tags, assignees) are still merged, and
the #349 duplicates stay fixed. Supersedes #373's "ordered by its highest numericId".

Board (the #349 fixture): PAPER-001..003 (numericId 1..3) and H-004 (numericId 4).
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner
from rdflib import Literal
from rdflib.namespace import XSD

from tests.issues.test_349_sparql_distinct import (  # noqa: F401 (fixtures)
    DUAL,
    engine,
    repo,
)
from yurtle_kanban.cli import get_service, main
from yurtle_kanban.query import ITEM, KB, ParsedQuery, QueryEngine

NORMAL_ORDER = [DUAL, "PAPER-003", "PAPER-002", "PAPER-001"]
OWN_NUMID = {"PAPER-001": 1, "PAPER-002": 2, "PAPER-003": 3, DUAL: 4}

_BLOCK = """
```yurtle
@prefix kb: <https://yurtle.dev/kanban/> .
@prefix item: <https://yurtle.dev/kanban/item/> .
item:{item_id} {body} .
```
"""


def _add_block(repo: Path, item_id: str, body: str) -> None:
    """Append a yurtle block with ``item:<item_id> <body> .`` to the item's file."""
    (path,) = repo.rglob(f"{item_id}-*.md")
    path.write_text(
        path.read_text(encoding="utf-8") + _BLOCK.format(item_id=item_id, body=body),
        encoding="utf-8",
    )


def _engine_with(repo: Path, blocks: dict[str, str]) -> QueryEngine:
    for item_id, body in blocks.items():
        _add_block(repo, item_id, body)
    eng = QueryEngine.from_service(get_service(), enable_semantic=False)
    for item_id in blocks:
        item = eng._ug.get_item(item_id)
        assert item is not None, f"fixture: {item_id} not scanned"
        assert item.graph is not None and (ITEM[item_id], KB.numericId, None) in item.graph, (
            f"fixture: {item_id}'s block did not parse a kb:numericId triple"
        )
    return eng


def _ids(engine: QueryEngine, parsed: ParsedQuery) -> list[str]:
    return [i.id for i in engine.structured_query(parsed)]


def _cli_ids(query: str) -> list[str]:
    result = CliRunner().invoke(main, ["query", "--no-semantic", "--json", query])
    assert result.exception is None, f"raised {result.exception!r}"
    assert result.exit_code == 0, result.output
    return [row["id"] for row in json.loads(result.output)]


def _numids(engine: QueryEngine, item_id: str) -> list[Literal]:
    return sorted(engine._ug.graph.objects(ITEM[item_id], KB.numericId), key=str)


# ---------------------------------------------------------------------------
# RED before the fix
# ---------------------------------------------------------------------------

# --- a string kb:numericId sorts at the item's own position, not at the top ----


def test_string_abc_numericid_sorts_at_own_position(repo: Path) -> None:
    eng = _engine_with(repo, {"PAPER-002": 'kb:numericId "abc"'})
    assert _ids(eng, ParsedQuery(status_include=["backlog"])) == NORMAL_ORDER


def test_string_999_numericid_sorts_at_own_position(repo: Path) -> None:
    eng = _engine_with(repo, {"PAPER-001": 'kb:numericId "999"'})
    assert _ids(eng, ParsedQuery(status_include=["backlog"])) == NORMAL_ORDER


def test_cli_string_numericid_sorts_at_own_position(repo: Path) -> None:
    _engine_with(repo, {"PAPER-002": 'kb:numericId "abc"'})
    assert _cli_ids("backlog items") == NORMAL_ORDER


# --- a second integer kb:numericId doesn't satisfy an id-range filter ----------


def test_int_999_does_not_pass_id_min(repo: Path) -> None:
    eng = _engine_with(repo, {"PAPER-002": "kb:numericId 999"})
    assert _ids(eng, ParsedQuery(id_min=500)) == []
    assert _ids(eng, ParsedQuery(id_min=3)) == [DUAL]


def test_cli_int_999_does_not_pass_above(repo: Path) -> None:
    _engine_with(repo, {"PAPER-002": "kb:numericId 999"})
    assert _cli_ids("items above 3") == [DUAL]


def test_int_0_does_not_pass_id_max(repo: Path) -> None:
    eng = _engine_with(repo, {"PAPER-003": "kb:numericId 0"})
    assert _ids(eng, ParsedQuery(id_max=2)) == ["PAPER-001"]


def test_int_999_moves_nothing_in_order(repo: Path) -> None:
    eng = _engine_with(repo, {"PAPER-002": "kb:numericId 999"})
    assert _ids(eng, ParsedQuery(status_include=["backlog"])) == NORMAL_ORDER


# --- exactly one kb:numericId per item in the unified graph -------------------


def test_every_item_has_exactly_its_own_numericid(repo: Path) -> None:
    eng = _engine_with(
        repo,
        {
            "PAPER-001": 'kb:numericId "abc"',
            "PAPER-002": "kb:numericId 999",
            "PAPER-003": "kb:numericId 0",
            DUAL: 'kb:numericId "999"',
        },
    )
    got = {item_id: _numids(eng, item_id) for item_id in OWN_NUMID}
    want = {
        item_id: [Literal(num, datatype=XSD.integer)] for item_id, num in OWN_NUMID.items()
    }
    assert got == want


def test_other_block_triples_still_merged(repo: Path) -> None:
    eng = _engine_with(
        repo,
        {
            "PAPER-002": (
                'a kb:Hypothesis ; kb:tag "zeta" ; kb:assignee "Blocky" ; kb:numericId 999'
            ),
        },
    )
    # the fix skips only the kb:numericId predicate, not the block
    g = eng._ug.graph
    p2 = ITEM["PAPER-002"]
    assert (p2, KB.tag, Literal("zeta")) in g
    assert (p2, KB.assignee, Literal("Blocky")) in g
    # red today only through ordering: the block's 999 puts PAPER-002 above H-004
    assert _ids(eng, ParsedQuery(type_filter=["hypothesis"])) == [DUAL, "PAPER-002"]
    assert _ids(eng, ParsedQuery(tag="zeta")) == ["PAPER-002"]
    assert _ids(eng, ParsedQuery(assignee="blocky")) == ["PAPER-002"]
    # H-004's own #349 block (a kb:Paper; kb:assignee "Mini-2") is merged too
    assert _ids(eng, ParsedQuery(type_filter=["paper"])) == NORMAL_ORDER


# ---------------------------------------------------------------------------
# GREEN before and after
# ---------------------------------------------------------------------------


def test_int_0_not_excluded_by_id_min(repo: Path) -> None:
    eng = _engine_with(repo, {"PAPER-003": "kb:numericId 0"})
    assert _ids(eng, ParsedQuery(id_min=2)) == [DUAL, "PAPER-003"]


def test_control_349_duplicates_stay_fixed(engine: QueryEngine) -> None:
    assert _ids(engine, ParsedQuery(type_filter=["paper", "hypothesis"])) == NORMAL_ORDER
    assert _ids(engine, ParsedQuery(tag="brain")) == [DUAL, "PAPER-002"]
    assert _ids(engine, ParsedQuery(assignee="mini")) == [DUAL, "PAPER-001"]
    parsed = ParsedQuery(type_filter=["paper", "hypothesis"], tag="brain", assignee="mini")
    assert _ids(engine, parsed) == [DUAL]


def test_control_normal_board_one_numericid_each(engine: QueryEngine) -> None:
    for item_id, num in OWN_NUMID.items():
        assert _numids(engine, item_id) == [Literal(num, datatype=XSD.integer)]


def test_control_normal_order(engine: QueryEngine) -> None:
    assert _ids(engine, ParsedQuery(status_include=["backlog"])) == NORMAL_ORDER


def test_control_normal_ranges(engine: QueryEngine) -> None:
    assert _ids(engine, ParsedQuery(id_min=2)) == [DUAL, "PAPER-003"]
    assert _ids(engine, ParsedQuery(id_max=3)) == ["PAPER-002", "PAPER-001"]
    assert _ids(engine, ParsedQuery(id_min=1, id_max=4)) == ["PAPER-003", "PAPER-002"]
    assert _ids(engine, ParsedQuery(id_min=500)) == []


def test_control_cli_normal_order_and_range(repo: Path) -> None:
    assert _cli_ids("backlog items") == NORMAL_ORDER
    assert _cli_ids("items above 2") == [DUAL, "PAPER-003"]
