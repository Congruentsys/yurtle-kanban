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

Round 2 ([steer] refinement after the PR #401 review): the skip applies only to triples
whose subject is an IRI. Blank-node subjects are merged: ``move`` records history as
``<> kb:statusChange [ kb:status … ; kb:at … ; kb:by … ; kb:closedBy <PR> ]`` (README
"Graph provenance"), and that nested ``kb:status`` must stay in the unified graph. It
can't redefine any item: ``structured_query`` matches ``?item kb:status ?status`` with
``?item`` the item IRI bound through ``kb:id``, never the history blank node.

Board (the #349 fixture): PAPER-001..003 (numericId 1..3) and H-004 (numericId 4).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from rdflib import RDF, BNode, Literal, URIRef
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


# ---------------------------------------------------------------------------
# Round 2: blank-node subjects merge (move's kb:statusChange history)
# ---------------------------------------------------------------------------

PR_URL = "https://github.com/owner/repo/pull/42"

# the README "Graph provenance" block, on an item whose frontmatter stays backlog
_README_HISTORY = """<> kb:statusChange [
    kb:status kb:done ;
    kb:at "2026-03-03T12:34:56"^^xsd:dateTime ;
    kb:by "github-actions[bot]" ;
    kb:closedBy <https://github.com/owner/repo/pull/42> ;
] ."""

_HISTORY_SPARQL = (
    "SELECT ?c ?s ?at ?by ?pr WHERE {\n"
    "  ?x kb:statusChange ?c .\n"
    "  ?c kb:status ?s .\n"
    "  OPTIONAL { ?c kb:at ?at }\n"
    "  OPTIONAL { ?c kb:by ?by }\n"
    "  OPTIONAL { ?c kb:closedBy ?pr }\n"
    "}"
)


def _history(engine: QueryEngine) -> list[tuple[str, str, str]]:
    """(status, by, closedBy) of every statusChange node that has a kb:status."""
    return sorted(
        (row["s"], row["by"], row["pr"]) for row in engine.sparql(_HISTORY_SPARQL)
    )


def _move(*args: str) -> None:
    result = CliRunner().invoke(main, ["move", *args, "--force", "--no-commit"])
    assert result.exception is None, f"raised {result.exception!r}"
    assert result.exit_code == 0, result.output


@pytest.fixture
def readme_history(repo: Path) -> QueryEngine:
    """PAPER-001 (frontmatter backlog) carries the README's statusChange block."""
    eng = _engine_with(repo, {"PAPER-001": _README_HISTORY})
    item = eng._ug.get_item("PAPER-001")
    assert item is not None and item.graph is not None
    nodes = list(item.graph.subjects(KB.status, KB.done))
    assert len(nodes) == 1 and isinstance(nodes[0], BNode), (
        f"fixture: the history kb:status is not on one blank node: {nodes}"
    )
    return eng


@pytest.fixture
def moved(repo: Path) -> QueryEngine:
    """PAPER-002 moved to done (closed by a PR) and back to backlog by the real CLI."""
    _move("PAPER-002", "done", "--closed-by", PR_URL)
    _move("PAPER-002", "backlog")
    text = _item_file(repo, "PAPER-002").read_text(encoding="utf-8")
    assert "\nstatus: backlog\n" in text, "fixture: frontmatter not back to backlog"
    assert text.count("kb:status kb:") == 2, f"fixture: two history entries expected:\n{text}"
    eng = QueryEngine.from_service(get_service(), enable_semantic=False)
    item = eng._ug.get_item("PAPER-002")
    assert item is not None and item.graph is not None
    assert len(set(item.graph.subjects(KB.status, None))) == 2, "fixture: history not parsed"
    return eng


# --- (a) RED at 950c49e: the history blank node keeps its kb:status ---------


def test_readme_history_status_kept_in_unified_graph(readme_history: QueryEngine) -> None:
    item = readme_history._ug.get_item("PAPER-001")
    assert item is not None and item.graph is not None
    for triple in item.graph:
        if isinstance(triple[0], BNode):
            assert triple in readme_history._ug.graph, f"blank-node triple dropped: {triple}"


def test_readme_history_sparql(readme_history: QueryEngine) -> None:
    assert _history(readme_history) == [(str(KB.done), "github-actions[bot]", PR_URL)]


def test_real_move_history_sparql(moved: QueryEngine) -> None:
    rows = _history(moved)
    assert [s for s, _, _ in rows] == [str(KB.backlog), str(KB.done)], rows
    # the move to done carries its closedBy; the one back to backlog has none
    assert [pr for s, _, pr in rows if s == str(KB.done)] == [PR_URL], rows
    assert all(by for _, by, _ in rows), rows
    ats = moved.sparql(
        "SELECT ?at WHERE { ?x kb:statusChange ?c . ?c kb:status ?s ; kb:at ?at ; kb:by ?by }"
    )
    assert len(ats) == 2, ats


def test_blank_node_owned_predicates_merged(repo: Path) -> None:
    # blank-node subjects merge for every owned predicate, not only kb:status
    eng = _engine_with(
        repo,
        {
            "PAPER-003": (
                'item:PAPER-003 kb:note [ kb:id "NOTE-1" ; kb:title "A note" ;'
                ' kb:status kb:done ; kb:priority kb:critical ; kb:priorityRank 1 ;'
                ' kb:description "nested" ; kb:numericId 99 ] .'
            )
        },
    )
    (note,) = list(eng._ug.graph.objects(ITEM["PAPER-003"], KB.note))
    assert isinstance(note, BNode)
    got = {p: list(eng._ug.graph.objects(note, KB[p])) for p in OWNED if p != "created"}
    assert got == {
        "id": [Literal("NOTE-1")],
        "title": [Literal("A note")],
        "status": [KB.done],
        "priority": [KB.critical],
        "priorityRank": [Literal(1, datatype=XSD.integer)],
        "description": [Literal("nested")],
        "numericId": [Literal(99, datatype=XSD.integer)],
    }


# --- (b) a history kb:status never makes the item match a status filter -----
# GREEN at 950c49e (the blank node is dropped); must stay green once it merges.


def test_readme_history_does_not_match_done(readme_history: QueryEngine) -> None:
    assert _ids(readme_history, ParsedQuery(status_include=["done"])) == []
    assert _ids(readme_history, ParsedQuery(status_filter=["backlog"])) == []
    assert _ids(readme_history, ParsedQuery(status_include=["backlog"])) == NORMAL_ORDER
    assert _objects(readme_history, "PAPER-001", "status") == [KB.backlog]


def test_real_move_history_does_not_match_done(moved: QueryEngine) -> None:
    assert _ids(moved, ParsedQuery(status_include=["done"])) == []
    assert _ids(moved, ParsedQuery(status_filter=["backlog"])) == []
    assert _ids(moved, ParsedQuery(status_include=["backlog"])) == NORMAL_ORDER
    assert _objects(moved, "PAPER-002", "status") == [KB.backlog]


def test_cli_real_move_history_does_not_match(moved: QueryEngine) -> None:
    assert _cli_ids("backlog items") == NORMAL_ORDER


def test_blank_node_owned_predicates_do_not_skew_queries(repo: Path) -> None:
    eng = _engine_with(
        repo,
        {
            "PAPER-003": (
                'item:PAPER-003 kb:note [ kb:id "PAPER-001" ; kb:status kb:done ;'
                " kb:numericId 99 ] ."
            )
        },
    )
    assert _ids(eng, ParsedQuery(status_include=["done"])) == []
    assert _ids(eng, ParsedQuery(id_min=2)) == [DUAL, "PAPER-003"]
    ids = _ids(eng, ParsedQuery(status_include=["backlog"]))
    _assert_unique(ids)
    assert ids == NORMAL_ORDER


def test_moved_to_done_matches_done_via_frontmatter(repo: Path) -> None:
    # control: a real move to done is found through frontmatter, once
    _move("PAPER-002", "done", "--closed-by", PR_URL)
    eng = QueryEngine.from_service(get_service(), enable_semantic=False)
    assert _ids(eng, ParsedQuery(status_include=["done"])) == ["PAPER-002"]
    assert _ids(eng, ParsedQuery(status_include=["backlog"])) == [
        DUAL,
        "PAPER-003",
        "PAPER-001",
    ]
