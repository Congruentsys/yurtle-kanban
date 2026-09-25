# ruff: noqa: F811  -- pytest fixtures imported from the #349/#404/#413 test modules are re-bound as args
"""Issue #421 — the parse-time self-IRI moves off the rdflib ``Graph`` object.

Follow-up from the review of PR #419 (#413). ``KanbanService._parse_graph`` kept
the IRI ``<>`` resolved to as ``graph.yurtle_self_iri``, an attribute set on a
third-party object (it breaks if rdflib adds ``__slots__``). And
``WorkItemIndexer`` graphs (``g.parse(file_path, format="yurtle")``) were never
recorded at all, so their ``<>`` facts stayed on a ``file:`` node when the cwd
changed between the indexer scan and ``UnifiedGraph.add_items``.

Decided ([steer] on #421): a module-level ``weakref.WeakKeyDictionary`` registry
with a small set/get helper. Behaviour pinned here:

1. A graph parsed by the service carries no extra attribute: ``vars(g)`` has the
   same keys as a freshly built plain ``Graph()``. #404/#413 behaviour is
   unchanged (scan in A, chdir to B, merge maps ``<>`` to the item; a hand-built
   graph falls back to the cwd at merge time).
2. ``WorkItemIndexer`` graphs: ``<>`` there resolves to the PROCESS CWD at parse
   time (``file:///<cwd>/``, probed; not the file's own URI). Through
   ``UnifiedGraph().add_items`` their ``<> kb:tag`` / ``rdf:type`` /
   ``kb:statusChange`` land on ``item:<ID>`` even if the cwd changed after the
   indexer scan.
3. A graph rebuilt from scratch (``Graph()`` + manual adds, even with the same
   identifier as a registered graph) is not mistaken for a registered one: it falls
   back to the cwd at merge time.

These tests do not pin the registry's name or location; they look at graph
attributes, the unified graph and query results.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from rdflib import RDF, Graph, Literal, URIRef

from tests.issues.test_349_sparql_distinct import repo  # noqa: F401 (fixture)
from tests.issues.test_404_block_base_iri import (  # noqa: F401 (fixtures)
    _file_facts,
    _file_nodes,
    nautical,
)
from tests.issues.test_413_phantom_at_parse import (  # noqa: F401 (fixtures)
    _BLOCK,
    _assert_parsed_at,
    _assert_tagged_on_items,
    _hand_item,
    _scan_then_chdir,
    _service,
    _uri_of,
    elsewhere,
    tagged_repo,
)
from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.indexer import WorkItemIndexer
from yurtle_kanban.models import WorkItem, WorkItemStatus, WorkItemType
from yurtle_kanban.query import ITEM, KB, QueryEngine, UnifiedGraph

_PLAIN_KEYS = frozenset(vars(Graph()))


def _extra_attrs(g: Graph) -> set[str]:
    return set(vars(g)) - _PLAIN_KEYS


# ---------------------------------------------------------------------------
# 1. No attribute on the rdflib Graph (RED today: yurtle_self_iri is set)
# ---------------------------------------------------------------------------


def test_scanned_item_graph_has_no_extra_attribute(tagged_repo: Path) -> None:
    items = _service(tagged_repo).scan()
    graphs = [i.graph for i in items if i.graph is not None]
    assert graphs, "premise: scan produced no graphs"
    for g in graphs:
        assert _extra_attrs(g) == set()


def test_scanned_item_graph_has_no_yurtle_self_iri(tagged_repo: Path) -> None:
    (item,) = [i for i in _service(tagged_repo).scan() if i.id == "PAPER-001"]
    assert item.graph is not None
    assert "yurtle_self_iri" not in vars(item.graph)
    assert not hasattr(item.graph, "yurtle_self_iri")


def test_parse_graph_result_has_plain_graph_keys(tagged_repo: Path) -> None:
    g = _service(tagged_repo)._parse_graph(_BLOCK.format(body='<> kb:tag "z" .'))
    assert g is not None
    assert set(vars(g)) == set(_PLAIN_KEYS)


def test_moved_item_graph_has_no_extra_attribute(nautical: Path) -> None:
    # `move` rewrites and re-parses the file; the rescanned graph is still plain
    items = _service(nautical).scan()
    (item,) = [i for i in items if i.id == "EXP-001"]
    assert item.graph is not None
    assert _extra_attrs(item.graph) == set()


# ---------------------------------------------------------------------------
# 1. #404 / #413 behaviour unchanged (GREEN before and after)
# ---------------------------------------------------------------------------


def test_scan_chdir_merge_still_maps_self(
    tagged_repo: Path, elsewhere: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    items = _scan_then_chdir(tagged_repo, elsewhere, monkeypatch)
    _assert_parsed_at(items, "PAPER-001", tagged_repo)
    ug = UnifiedGraph()
    ug.add_items(items)
    _assert_tagged_on_items(ug.graph)
    eng = QueryEngine(unified_graph=ug)
    assert _file_facts(eng) == []
    assert _file_nodes(eng) == []


def test_hand_built_graph_still_falls_back_to_merge_cwd(
    elsewhere: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(elsewhere)
    ug = UnifiedGraph()
    ug.add_item(_hand_item("HAND-1", '<> kb:tag "h1" ; a kb:Expedition .'))
    assert (ITEM["HAND-1"], KB.tag, Literal("h1")) in ug.graph
    assert (ITEM["HAND-1"], RDF.type, KB.Expedition) in ug.graph
    assert _file_nodes(QueryEngine(unified_graph=ug)) == []


# ---------------------------------------------------------------------------
# 2. Indexer graphs reach UnifiedGraph (RED today after a chdir)
# ---------------------------------------------------------------------------

_PREFIX = "@prefix kb: <https://yurtle.dev/kanban/> ."
_INDEXED = {
    "EXP-71": '<> a kb:Expedition ;\n  kb:id "EXP-71" ;\n  kb:status kb:ready ;\n'
    '  kb:tag "x71" ;\n  kb:statusChange [ kb:status kb:in_progress ] .',
    "EXP-72": '<> a kb:Expedition ;\n  kb:id "EXP-72" ;\n  kb:tag "x72" .',
}


@pytest.fixture
def indexed_root(tmp_path: Path) -> Path:
    """Two turtle-block-only items under ``work/`` (the default KanbanConfig path)."""
    work = tmp_path / "board" / "work"
    work.mkdir(parents=True)
    for item_id, body in _INDEXED.items():
        (work / f"{item_id}.md").write_text(f"# {item_id}\n\n```turtle\n{_PREFIX}\n{body}\n```\n")
    return tmp_path / "board"


def _index_at(root: Path, cwd: Path, monkeypatch: pytest.MonkeyPatch) -> list[WorkItem]:
    monkeypatch.chdir(cwd)
    items = WorkItemIndexer(KanbanConfig(), root).scan()
    assert sorted(i.id for i in items) == sorted(_INDEXED), "premise: indexer items"
    return items


def _assert_indexed_on_items(ug: UnifiedGraph) -> None:
    g = ug.graph
    assert (ITEM["EXP-71"], KB.tag, Literal("x71")) in g
    assert (ITEM["EXP-72"], KB.tag, Literal("x72")) in g
    assert (ITEM["EXP-71"], KB.tag, Literal("x72")) not in g
    assert (ITEM["EXP-71"], RDF.type, KB.Expedition) in g
    rows = QueryEngine(unified_graph=ug, embedding_index=None).sparql(
        "SELECT ?s WHERE { item:EXP-71 kb:statusChange ?c . ?c kb:status ?s }"
    )
    assert [r["s"] for r in rows] == [str(KB.in_progress)]


def test_indexer_self_iri_is_parse_cwd(
    indexed_root: Path, elsewhere: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # probe result pinned as a premise: `<>` resolves against the cwd, not the file
    items = _index_at(indexed_root, elsewhere, monkeypatch)
    for item in items:
        assert item.graph is not None
        subjects = {s for s in item.graph.subjects() if isinstance(s, URIRef)}
        assert _uri_of(elsewhere) in subjects
        assert URIRef(item.file_path.as_uri()) not in subjects


def test_indexer_graph_has_no_extra_attribute(
    indexed_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for item in _index_at(indexed_root, indexed_root, monkeypatch):
        assert item.graph is not None
        assert _extra_attrs(item.graph) == set()


def test_indexer_items_same_cwd_map_to_items(
    indexed_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # control: parse and merge share the cwd (GREEN today via the cwd fallback)
    items = _index_at(indexed_root, indexed_root, monkeypatch)
    ug = UnifiedGraph()
    ug.add_items(items)
    _assert_indexed_on_items(ug)
    assert _file_facts(QueryEngine(unified_graph=ug)) == []


def test_indexer_items_after_chdir_map_to_items(
    indexed_root: Path, elsewhere: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    items = _index_at(indexed_root, indexed_root, monkeypatch)
    monkeypatch.chdir(elsewhere)
    ug = UnifiedGraph()
    ug.add_items(items)
    _assert_indexed_on_items(ug)
    eng = QueryEngine(unified_graph=ug)
    assert _file_facts(eng) == []
    assert _file_nodes(eng) == []


def test_indexer_scanned_elsewhere_merged_in_root(
    indexed_root: Path, elsewhere: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    items = _index_at(indexed_root, elsewhere, monkeypatch)
    monkeypatch.chdir(indexed_root)
    ug = UnifiedGraph()
    for item in items:
        ug.add_item(item)
    _assert_indexed_on_items(ug)
    eng = QueryEngine(unified_graph=ug)
    assert _file_facts(eng) == []
    phantom = _uri_of(elsewhere)
    assert list(ug.graph.predicate_objects(phantom)) == []


# ---------------------------------------------------------------------------
# 3. A rebuilt graph is not mistaken for a registered one (GREEN guards)
# ---------------------------------------------------------------------------


def _rebuilt(identifier, body: str) -> Graph:
    """A graph built from scratch with Graph() + manual adds, holding the triples
    of ``body`` parsed in the CURRENT cwd (so ``<>`` is the current cwd's IRI)."""
    src = _hand_item("SRC", body).graph
    assert src is not None
    g = Graph() if identifier is None else Graph(identifier=identifier)
    for t in src:
        g.add(t)
    return g


def _item_with(item_id: str, graph: Graph) -> WorkItem:
    return WorkItem(
        id=item_id,
        title=item_id,
        item_type=WorkItemType.TASK,
        status=WorkItemStatus.BACKLOG,
        file_path=Path(f"{item_id}.md"),
        graph=graph,
    )


@pytest.mark.parametrize("same_identifier", [False, True])
def test_rebuilt_graph_falls_back_to_merge_cwd(
    tagged_repo: Path,
    elsewhere: Path,
    monkeypatch: pytest.MonkeyPatch,
    same_identifier: bool,
) -> None:
    # scanned (registered under tagged_repo's IRI) graphs stay alive throughout
    items = _scan_then_chdir(tagged_repo, elsewhere, monkeypatch)
    (scanned,) = [i for i in items if i.id == "PAPER-001"]
    assert scanned.graph is not None
    ident = scanned.graph.identifier if same_identifier else None
    rebuilt = _rebuilt(ident, '<> kb:tag "rb" ; a kb:Expedition .')
    assert rebuilt is not scanned.graph
    ug = UnifiedGraph()
    ug.add_items([*items, _item_with("RB-1", rebuilt)])
    assert (ITEM["RB-1"], KB.tag, Literal("rb")) in ug.graph
    assert (ITEM["RB-1"], RDF.type, KB.Expedition) in ug.graph
    _assert_tagged_on_items(ug.graph)
    assert (ITEM["PAPER-001"], KB.tag, Literal("rb")) not in ug.graph
    eng = QueryEngine(unified_graph=ug)
    assert _file_facts(eng) == []
    assert _file_nodes(eng) == []


def test_rebuilt_copy_of_scanned_graph_is_not_registered(
    tagged_repo: Path, elsewhere: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # copying a scanned graph's triples into Graph() does not copy its registration:
    # the copy falls back to the merge-time cwd, so the scan-cwd node is not mapped
    items = _scan_then_chdir(tagged_repo, elsewhere, monkeypatch)
    (scanned,) = [i for i in items if i.id == "PAPER-001"]
    assert scanned.graph is not None
    copy = Graph()
    for t in scanned.graph:
        copy.add(t)
    assert _extra_attrs(copy) == set()
    ug = UnifiedGraph()
    ug.add_item(_item_with("CP-1", copy))
    assert (ITEM["CP-1"], KB.tag, Literal("t1")) not in ug.graph
    assert (_uri_of(tagged_repo), KB.tag, Literal("t1")) in ug.graph
