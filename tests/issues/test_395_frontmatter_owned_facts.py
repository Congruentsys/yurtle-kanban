# ruff: noqa: F811  -- pytest fixtures imported from the #349 test module are re-bound as args
"""Issue #395 — a yurtle block can't redefine the facts frontmatter owns.

``UnifiedGraph.add_item`` writes an item's single-valued facts from its frontmatter
(``kb:id``, ``kb:status``, ``kb:title``, ``kb:priority``, ``kb:created``,
``kb:priorityRank``, ``kb:description``, and ``kb:numericId`` from its ID) and then
merges every triple of the item's per-file graph (fenced yurtle/turtle blocks). A block
that writes one of those predicates gives the item a second value and skews queries:

* ``item:PAPER-002 kb:status kb:done`` makes ``status_include=["done"]`` return
  PAPER-002 (frontmatter: backlog), and it is still returned for backlog;
* ``item:PAPER-003 kb:id "PAPER-001"`` makes ``id_min=2`` return PAPER-001 (bound to
  PAPER-003's numericId 3), and moves PAPER-001 up the backlog order.

Decided ([steer], bucket 2; G1): the merge skips every block triple whose predicate is
one of the eight above, WHATEVER its subject (a block in item A writing item B's status
would skew B too). Additive predicates are still merged: ``rdf:type``, ``kb:tag``,
``kb:assignee``, relationships (``kb:related``, ``kb:dependsOn``) and every non-``kb:``
predicate (#349). The #373 seen-set in ``structured_query`` stays as a guard; it is
pinned here by injecting a second ``kb:numericId`` / ``kb:id`` straight into the unified
graph, since after the skip no block can reach it.

Board (the #349 fixture): PAPER-001..003 (numericId 1..3) and H-004 (numericId 4).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from rdflib import RDF, Literal, URIRef
from rdflib.namespace import XSD

from tests.issues.test_349_sparql_distinct import (  # noqa: F401 (fixtures)
    DUAL,
    engine,
    repo,
)
from yurtle_kanban.cli import get_service, main
from yurtle_kanban.models import WorkItem
from yurtle_kanban.query import ITEM, KB, ParsedQuery, QueryEngine

NORMAL_ORDER = [DUAL, "PAPER-003", "PAPER-002", "PAPER-001"]
ALL_IDS = set(NORMAL_ORDER)

OWNED = [
    "id",
    "status",
    "title",
    "priority",
    "created",
    "priorityRank",
    "description",
    "numericId",
]

DCT_SUBJECT = URIRef("http://purl.org/dc/terms/subject")

_BLOCK = """
```yurtle
@prefix kb: <https://yurtle.dev/kanban/> .
@prefix item: <https://yurtle.dev/kanban/item/> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@prefix dct: <http://purl.org/dc/terms/> .
{body}
```
"""


def _item_file(repo: Path, item_id: str) -> Path:
    (path,) = repo.rglob(f"{item_id}-*.md")
    return path


def _add_block(repo: Path, host_id: str, body: str) -> None:
    """Append a yurtle block holding ``body`` (full Turtle statements) to host's file."""
    path = _item_file(repo, host_id)
    path.write_text(
        path.read_text(encoding="utf-8") + _BLOCK.format(body=body), encoding="utf-8"
    )


def _engine_with(repo: Path, blocks: dict[str, str]) -> QueryEngine:
    """Engine over the #349 board plus one block per host item; checks each parsed."""
    for host_id, body in blocks.items():
        _add_block(repo, host_id, body)
    eng = QueryEngine.from_service(get_service(), enable_semantic=False)
    for host_id in blocks:
        item = eng._ug.get_item(host_id)
        assert item is not None, f"fixture: {host_id} not scanned"
        assert item.graph is not None and any(
            str(p).startswith(str(KB)) for p in item.graph.predicates()
        ), f"fixture: {host_id}'s block did not parse into its per-file graph"
    return eng


def _ids(engine: QueryEngine, parsed: ParsedQuery) -> list[str]:
    return [i.id for i in engine.structured_query(parsed)]


def _cli_ids(query: str) -> list[str]:
    result = CliRunner().invoke(main, ["query", "--no-semantic", "--json", query])
    assert result.exception is None, f"raised {result.exception!r}"
    assert result.exit_code == 0, result.output
    return [row["id"] for row in json.loads(result.output)]


def _assert_unique(ids: list[str]) -> None:
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    assert not dupes, f"items returned more than once {dupes}: {ids}"


def _objects(engine: QueryEngine, item_id: str, pred: str) -> list:
    return sorted(engine._ug.graph.objects(ITEM[item_id], KB[pred]), key=str)


def _expected(item: WorkItem, pred: str) -> list:
    """The one value add_item writes from frontmatter (none when the field is unset)."""
    if pred == "id":
        return [Literal(item.id)]
    if pred == "status":
        return [KB[item.status.value]]
    if pred == "title":
        return [Literal(item.title)]
    if pred == "priority":
        return [KB[item.priority]] if item.priority else []
    if pred == "created":
        return (
            [Literal(item.created.isoformat(), datatype=XSD.date)] if item.created else []
        )
    if pred == "priorityRank":
        return (
            [Literal(item.priority_rank, datatype=XSD.integer)]
            if item.priority_rank is not None
            else []
        )
    if pred == "description":
        return [Literal(item.description)] if item.description else []
    if pred == "numericId":
        return [Literal(item.numeric_id, datatype=XSD.integer)]
    raise AssertionError(pred)


# a block per item that tries to override every owned predicate on its own item
_OVERRIDE_ALL = (
    "item:{item_id} kb:id \"PAPER-001\" ; kb:status kb:done ; kb:title \"Hijacked\" ;"
    " kb:priority kb:critical ; kb:created \"2001-01-01\"^^xsd:date ;"
    " kb:priorityRank 1 ; kb:description \"Injected\" ; kb:numericId 999 ."
)


@pytest.fixture
def override_all(repo: Path) -> QueryEngine:
    """Every item's block overrides all eight owned predicates on itself.

    PAPER-002 also gets a frontmatter ``priority_rank`` and a body description so
    every owned predicate has a frontmatter value on at least one item.
    """
    path = _item_file(repo, "PAPER-002")
    text = path.read_text(encoding="utf-8")
    assert "\npriority: medium\n" in text, "fixture: PAPER-002 frontmatter changed"
    text = text.replace("\npriority: medium\n", "\npriority: medium\npriority_rank: 7\n", 1)
    path.write_text(text + "\nOwn description.\n", encoding="utf-8")
    eng = _engine_with(
        repo, {item_id: _OVERRIDE_ALL.format(item_id=item_id) for item_id in NORMAL_ORDER}
    )
    p2 = eng._ug.get_item("PAPER-002")
    assert p2 is not None and p2.priority_rank == 7, "fixture: priority_rank not parsed"
    assert p2.description and "Own description." in p2.description, (
        f"fixture: description not parsed: {p2.description!r}"
    )
    return eng


# ---------------------------------------------------------------------------
# RED before the fix
# ---------------------------------------------------------------------------

# --- 1a. a block kb:status on its own item --------------------------------------


def test_block_status_done_not_returned_for_done(repo: Path) -> None:
    eng = _engine_with(repo, {"PAPER-002": "item:PAPER-002 kb:status kb:done ."})
    assert _ids(eng, ParsedQuery(status_include=["done"])) == []


def test_block_status_done_not_excluding_backlog(repo: Path) -> None:
    eng = _engine_with(repo, {"PAPER-002": "item:PAPER-002 kb:status kb:done ."})
    # everything is backlog: excluding backlog leaves nothing
    assert _ids(eng, ParsedQuery(status_filter=["backlog"])) == []


def test_block_status_done_item_still_backlog(repo: Path) -> None:
    eng = _engine_with(repo, {"PAPER-002": "item:PAPER-002 kb:status kb:done ."})
    assert _objects(eng, "PAPER-002", "status") == [KB.backlog]
    assert _ids(eng, ParsedQuery(status_include=["backlog"])) == NORMAL_ORDER


# --- 1b. a block kb:id on its own item ---------------------------------------


def test_block_id_override_does_not_pass_id_min(repo: Path) -> None:
    eng = _engine_with(repo, {"PAPER-003": 'item:PAPER-003 kb:id "PAPER-001" .'})
    assert _ids(eng, ParsedQuery(id_min=2)) == [DUAL, "PAPER-003"]


def test_block_id_override_backlog_order(repo: Path) -> None:
    eng = _engine_with(repo, {"PAPER-003": 'item:PAPER-003 kb:id "PAPER-001" .'})
    assert _ids(eng, ParsedQuery(status_include=["backlog"])) == NORMAL_ORDER


def test_cli_block_id_override(repo: Path) -> None:
    _engine_with(repo, {"PAPER-003": 'item:PAPER-003 kb:id "PAPER-001" .'})
    assert _cli_ids("items above 2") == [DUAL, "PAPER-003"]
    assert _cli_ids("backlog items") == NORMAL_ORDER


def test_block_id_override_item_keeps_own_id(repo: Path) -> None:
    eng = _engine_with(repo, {"PAPER-003": 'item:PAPER-003 kb:id "PAPER-001" .'})
    assert _objects(eng, "PAPER-003", "id") == [Literal("PAPER-003")]


# --- 2. a block in item A writing item B's status / title ---------------------


def test_cross_item_status_has_no_effect(repo: Path) -> None:
    eng = _engine_with(repo, {"PAPER-002": "item:PAPER-001 kb:status kb:done ."})
    assert _ids(eng, ParsedQuery(status_include=["done"])) == []
    assert _ids(eng, ParsedQuery(status_filter=["backlog"])) == []
    assert _objects(eng, "PAPER-001", "status") == [KB.backlog]


def test_cross_item_title_has_no_effect(repo: Path) -> None:
    eng = _engine_with(repo, {"PAPER-002": 'item:PAPER-001 kb:title "Hijacked" .'})
    assert _objects(eng, "PAPER-001", "title") == [Literal("First paper")]


def test_cross_item_status_and_title_from_hypothesis(repo: Path) -> None:
    # H-004's block already carries the #349 triples; add a cross-item override too
    eng = _engine_with(
        repo, {DUAL: 'item:PAPER-003 kb:status kb:done ; kb:title "From H-004" .'}
    )
    assert _objects(eng, "PAPER-003", "status") == [KB.backlog]
    assert _objects(eng, "PAPER-003", "title") == [Literal("Third paper")]
    assert _ids(eng, ParsedQuery(status_include=["done"])) == []
    assert _ids(eng, ParsedQuery(status_include=["backlog"])) == NORMAL_ORDER


def test_owned_predicates_skipped_for_any_subject(repo: Path) -> None:
    # subjects that are no item at all: neither a stray item: IRI nor a urn:
    eng = _engine_with(
        repo,
        {
            "PAPER-002": (
                'item:GHOST-9 kb:id "GHOST-9" ; kb:status kb:backlog ; kb:title "Ghost" .\n'
                '<urn:ghost> kb:id "PAPER-001" ; kb:status kb:done ; kb:priorityRank 1 .'
            )
        },
    )
    owned = {KB[p] for p in OWNED}
    items = {ITEM[i] for i in ALL_IDS}
    strays = sorted(
        (str(s), str(p), str(o))
        for s, p, o in eng._ug.graph
        if p in owned and s not in items
    )
    assert strays == [], f"block triples on a non-item subject were merged: {strays}"


# --- 3. each owned predicate present exactly once, from frontmatter ----------


@pytest.mark.parametrize("pred", OWNED)
def test_owned_predicate_exactly_frontmatter_value(
    override_all: QueryEngine, pred: str
) -> None:
    got = {i: _objects(override_all, i, pred) for i in NORMAL_ORDER}
    want = {i: _expected(override_all._ug.get_item(i), pred) for i in NORMAL_ORDER}
    assert got == want


def test_override_all_queries_unskewed(override_all: QueryEngine) -> None:
    assert _ids(override_all, ParsedQuery(status_include=["backlog"])) == NORMAL_ORDER
    assert _ids(override_all, ParsedQuery(status_include=["done"])) == []
    assert _ids(override_all, ParsedQuery(id_min=2)) == [DUAL, "PAPER-003"]
    assert _ids(override_all, ParsedQuery(id_max=2)) == ["PAPER-001"]


# --- 4. kb:id override: each item once (the #373 seen-set's case) ------------


def test_block_id_override_each_item_once(repo: Path) -> None:
    eng = _engine_with(
        repo,
        {
            "PAPER-003": 'item:PAPER-003 kb:id "PAPER-001" .',
            DUAL: 'item:H-004 kb:id "PAPER-002" .',
        },
    )
    for parsed in (
        ParsedQuery(status_include=["backlog"]),
        ParsedQuery(type_filter=["paper", "hypothesis"]),
        ParsedQuery(id_min=0),
    ):
        ids = _ids(eng, parsed)
        _assert_unique(ids)
        assert ids == NORMAL_ORDER, parsed


# --- 5. additive block triples still merged alongside skipped owned ones -----


def test_additive_merged_owned_skipped_in_same_block(repo: Path) -> None:
    eng = _engine_with(
        repo,
        {
            "PAPER-002": (
                'item:PAPER-002 a kb:Hypothesis ; kb:tag "zeta" ; kb:assignee "Blocky" ;'
                " kb:status kb:done ; kb:id \"PAPER-009\" ."
            )
        },
    )
    g = eng._ug.graph
    p2 = ITEM["PAPER-002"]
    assert (p2, RDF.type, KB.Hypothesis) in g
    assert (p2, KB.tag, Literal("zeta")) in g
    assert (p2, KB.assignee, Literal("Blocky")) in g
    assert _ids(eng, ParsedQuery(type_filter=["hypothesis"])) == [DUAL, "PAPER-002"]
    assert _ids(eng, ParsedQuery(status_include=["done"])) == []
    assert _objects(eng, "PAPER-002", "id") == [Literal("PAPER-002")]


# ---------------------------------------------------------------------------
# GREEN before and after
# ---------------------------------------------------------------------------

# --- 4. the #373 seen-set guard, reached by writing the unified graph directly -
# After the skip no block can put a second kb:numericId / kb:id on an item, so these
# inject one into UnifiedGraph.graph; deleting the seen-set turns them red.


def test_seen_set_guard_second_numericid_in_graph(engine: QueryEngine) -> None:
    engine._ug.graph.add(
        (ITEM["PAPER-002"], KB.numericId, Literal(999, datatype=XSD.integer))
    )
    ids = _ids(engine, ParsedQuery(status_include=["backlog"]))
    _assert_unique(ids)
    assert set(ids) == ALL_IDS
    ids = _ids(engine, ParsedQuery(type_filter=["paper", "hypothesis"]))
    _assert_unique(ids)
    assert set(ids) == ALL_IDS


def test_seen_set_guard_second_id_in_graph(engine: QueryEngine) -> None:
    engine._ug.graph.add((ITEM["PAPER-003"], KB.id, Literal("PAPER-001")))
    ids = _ids(engine, ParsedQuery(status_include=["backlog"]))
    _assert_unique(ids)
    assert set(ids) == ALL_IDS


# --- 5. controls: additive predicates from blocks -----------------------------


def test_control_additive_block_triples_merged(repo: Path) -> None:
    eng = _engine_with(
        repo,
        {
            "PAPER-002": (
                'item:PAPER-002 a kb:Hypothesis ; kb:tag "zeta" ; kb:assignee "Blocky" ;'
                ' kb:related item:PAPER-001 ; kb:dependsOn item:PAPER-003 ;'
                ' dct:subject "graphs" .'
            )
        },
    )
    g = eng._ug.graph
    p2 = ITEM["PAPER-002"]
    assert (p2, RDF.type, KB.Hypothesis) in g
    assert (p2, RDF.type, KB.Paper) in g
    assert (p2, KB.tag, Literal("zeta")) in g
    assert (p2, KB.tag, Literal("brainstorm")) in g
    assert (p2, KB.assignee, Literal("Blocky")) in g
    assert (p2, KB.related, ITEM["PAPER-001"]) in g
    assert (p2, KB.dependsOn, ITEM["PAPER-003"]) in g
    assert (p2, DCT_SUBJECT, Literal("graphs")) in g
    assert _ids(eng, ParsedQuery(type_filter=["hypothesis"])) == [DUAL, "PAPER-002"]
    assert _ids(eng, ParsedQuery(tag="zeta")) == ["PAPER-002"]
    assert _ids(eng, ParsedQuery(assignee="blocky")) == ["PAPER-002"]
    assert _ids(eng, ParsedQuery(status_include=["backlog"])) == NORMAL_ORDER


def test_control_cross_item_additive_merged(repo: Path) -> None:
    eng = _engine_with(
        repo, {"PAPER-002": 'item:PAPER-001 kb:tag "cross" ; kb:assignee "Other" .'}
    )
    assert (ITEM["PAPER-001"], KB.tag, Literal("cross")) in eng._ug.graph
    assert _ids(eng, ParsedQuery(tag="cross")) == ["PAPER-001"]
    assert _ids(eng, ParsedQuery(assignee="other")) == ["PAPER-001"]


def test_control_349_duplicates_stay_fixed(engine: QueryEngine) -> None:
    assert _ids(engine, ParsedQuery(type_filter=["paper", "hypothesis"])) == NORMAL_ORDER
    assert _ids(engine, ParsedQuery(type_filter=["paper"])) == NORMAL_ORDER
    assert _ids(engine, ParsedQuery(tag="brain")) == [DUAL, "PAPER-002"]
    assert _ids(engine, ParsedQuery(assignee="mini")) == [DUAL, "PAPER-001"]
    assert _ids(engine, ParsedQuery(assignee="mini-2")) == [DUAL]
    parsed = ParsedQuery(type_filter=["paper", "hypothesis"], tag="brain", assignee="mini")
    assert _ids(engine, parsed) == [DUAL]


# --- 5. controls: a normal board is unchanged ---------------------------------


@pytest.mark.parametrize("pred", OWNED)
def test_control_normal_board_owned_predicates(engine: QueryEngine, pred: str) -> None:
    got = {i: _objects(engine, i, pred) for i in NORMAL_ORDER}
    want = {i: _expected(engine._ug.get_item(i), pred) for i in NORMAL_ORDER}
    assert got == want


def test_control_normal_board_frontmatter_graph_merged(engine: QueryEngine) -> None:
    # the per-file graph's own frontmatter triples use non-kb: predicates: still merged
    item = engine._ug.get_item("PAPER-001")
    assert item is not None and item.graph is not None and len(item.graph) > 0
    for triple in item.graph:
        assert triple in engine._ug.graph, triple


def test_control_normal_board_queries(engine: QueryEngine) -> None:
    assert _ids(engine, ParsedQuery(status_include=["backlog"])) == NORMAL_ORDER
    assert _ids(engine, ParsedQuery(status_include=["done"])) == []
    assert _ids(engine, ParsedQuery(id_min=2)) == [DUAL, "PAPER-003"]
    assert _ids(engine, ParsedQuery(id_max=3)) == ["PAPER-002", "PAPER-001"]


def test_control_cli_normal_board(repo: Path) -> None:
    assert _cli_ids("backlog items") == NORMAL_ORDER
    assert _cli_ids("items above 2") == [DUAL, "PAPER-003"]
