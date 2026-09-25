# ruff: noqa: F811  -- pytest fixtures imported from the #349/#404 test modules are re-bound as args
"""Issue #413 — the ``<>`` phantom is the cwd at PARSE time, not at merge time.

#404 made ``UnifiedGraph.add_item`` rewrite the IRI ``<>`` resolves to
(``file:///<cwd>/``) to the item's own IRI, but it computes that IRI from
``Path.cwd()`` when the item is MERGED. A library caller that scans in dir A and
builds the unified graph after ``os.chdir(B)`` gets the phantom ``file:///…/B/``,
matches nothing, and leaves every block fact (tags, ``rdf:type``, ``move``'s
``kb:statusChange`` history) on ``file:///…/A/``.

Decided: the phantom is captured when each file's graph is parsed (the process cwd
at parse time); ``add_item`` rewrites that captured IRI to ``ITEM[item.id]`` even if
the cwd changed since ``scan()``. An item built by hand (no scan) keeps the #404
behaviour: fall back to the cwd at merge time.

These tests do not pin WHERE the parse-time phantom is kept; they look only at the
unified graph and at query results.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yurtle_rdflib
from rdflib import RDF, Literal, URIRef

from tests.issues.test_349_sparql_distinct import repo  # noqa: F401 (fixture)
from tests.issues.test_395_frontmatter_owned_facts import _add_block
from tests.issues.test_404_block_base_iri import (  # noqa: F401 (fixtures)
    _TAGGED,
    _file_facts,
    _file_nodes,
    _move,
    nautical,
)
from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.models import WorkItem, WorkItemStatus, WorkItemType
from yurtle_kanban.query import ITEM, KB, ParsedQuery, QueryEngine, UnifiedGraph
from yurtle_kanban.service import KanbanService

_BLOCK = """
```yurtle
@prefix kb: <https://yurtle.dev/kanban/> .
@prefix item: <https://yurtle.dev/kanban/item/> .

{body}
```
"""


def _uri_of(path: Path) -> URIRef:
    """The IRI ``<>`` resolves to when the cwd is ``path``."""
    return URIRef(Path(path).as_uri() + "/")


def _service(repo_root: Path) -> KanbanService:
    config = KanbanConfig.load(repo_root / ".kanban" / "config.yaml")
    return KanbanService(config, repo_root)


def _ids(eng: QueryEngine, parsed: ParsedQuery) -> list[str]:
    return [i.id for i in eng.structured_query(parsed)]


def _assert_parsed_at(items: list, item_id: str, cwd: Path) -> None:
    """Premise: the item's per-file graph has the phantom of ``cwd`` as a subject."""
    (item,) = [i for i in items if i.id == item_id]
    assert item.graph is not None
    assert _uri_of(cwd) in set(item.graph.subjects()), (
        f"premise: {item_id} was not parsed with <> = {_uri_of(cwd)}"
    )


def _scan_then_chdir(
    repo_root: Path, dest: Path, monkeypatch: pytest.MonkeyPatch
) -> list:
    """Scan with the cwd at ``repo_root``; then chdir to ``dest``. Returns the items."""
    monkeypatch.chdir(repo_root)
    items = _service(repo_root).scan()
    monkeypatch.chdir(dest)
    assert Path.cwd().resolve() == dest.resolve()
    assert _uri_of(Path.cwd()) != _uri_of(repo_root), "premise: chdir did not move"
    return items


@pytest.fixture
def tagged_repo(repo: Path) -> Path:
    """#349 board with PAPER-001: ``<> kb:tag "t1" ; a kb:Expedition`` and
    PAPER-002: ``<> kb:tag "t2"`` (block-only tags); cwd = repo."""
    for item_id, body in _TAGGED.items():
        _add_block(repo, item_id, body)
    return repo


@pytest.fixture
def elsewhere(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("elsewhere")


def _assert_tagged_on_items(g) -> None:
    assert (ITEM["PAPER-001"], KB.tag, Literal("t1")) in g
    assert (ITEM["PAPER-001"], RDF.type, KB.Expedition) in g
    assert (ITEM["PAPER-002"], KB.tag, Literal("t2")) in g


# ---------------------------------------------------------------------------
# RED before the fix: scan in A, chdir to B, then merge
# ---------------------------------------------------------------------------


def test_add_items_after_chdir_outside_repo(
    tagged_repo: Path, elsewhere: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    items = _scan_then_chdir(tagged_repo, elsewhere, monkeypatch)
    _assert_parsed_at(items, "PAPER-001", tagged_repo)
    ug = UnifiedGraph()
    ug.add_items(items)
    eng = QueryEngine(unified_graph=ug)
    _assert_tagged_on_items(ug.graph)
    assert _file_facts(eng) == []


def test_add_items_after_chdir_to_subdirectory(
    tagged_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sub = tagged_repo / "research"
    assert sub.is_dir(), "fixture: hdd board has no research/ directory"
    items = _scan_then_chdir(tagged_repo, sub, monkeypatch)
    ug = UnifiedGraph()
    ug.add_items(items)
    _assert_tagged_on_items(ug.graph)
    assert _file_facts(QueryEngine(unified_graph=ug)) == []


def test_add_item_one_by_one_after_chdir(
    tagged_repo: Path, elsewhere: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    items = _scan_then_chdir(tagged_repo, elsewhere, monkeypatch)
    ug = UnifiedGraph()
    for item in items:
        ug.add_item(item)
    _assert_tagged_on_items(ug.graph)


def test_no_file_node_left_after_chdir(
    tagged_repo: Path, elsewhere: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # neither the parse-time cwd (A) nor the merge-time cwd (B) shows up as a node
    items = _scan_then_chdir(tagged_repo, elsewhere, monkeypatch)
    ug = UnifiedGraph()
    ug.add_items(items)
    assert _file_nodes(QueryEngine(unified_graph=ug)) == []


def test_tags_not_on_phantom_of_scan_cwd(
    tagged_repo: Path, elsewhere: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    items = _scan_then_chdir(tagged_repo, elsewhere, monkeypatch)
    ug = UnifiedGraph()
    ug.add_items(items)
    phantom_a = _uri_of(tagged_repo)
    assert list(ug.graph.predicate_objects(phantom_a)) == []
    assert list(ug.graph.subject_predicates(phantom_a)) == []


def test_query_engine_after_chdir_block_only_tags(
    tagged_repo: Path, elsewhere: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    items = _scan_then_chdir(tagged_repo, elsewhere, monkeypatch)
    ug = UnifiedGraph()
    ug.add_items(items)
    eng = QueryEngine(unified_graph=ug, embedding_index=None)
    assert _ids(eng, ParsedQuery(tag="t1")) == ["PAPER-001"]
    assert _ids(eng, ParsedQuery(tag="t2")) == ["PAPER-002"]


def test_query_engine_after_chdir_sparql_tags(
    tagged_repo: Path, elsewhere: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    items = _scan_then_chdir(tagged_repo, elsewhere, monkeypatch)
    ug = UnifiedGraph()
    ug.add_items(items)
    eng = QueryEngine(unified_graph=ug, embedding_index=None)
    rows = eng.sparql('SELECT ?i WHERE { ?i kb:tag "t1" }')
    assert [r["i"] for r in rows] == [str(ITEM["PAPER-001"])]


def test_move_history_on_item_after_chdir(
    nautical: Path, elsewhere: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    items = _scan_then_chdir(nautical, elsewhere, monkeypatch)
    _assert_parsed_at(items, "EXP-001", nautical)
    ug = UnifiedGraph()
    ug.add_items(items)
    eng = QueryEngine(unified_graph=ug, embedding_index=None)
    rows = eng.sparql(
        "SELECT ?s WHERE { item:EXP-001 kb:statusChange ?c . ?c kb:status ?s }"
    )
    assert [r["s"] for r in rows] == [str(KB.in_progress)]
    assert _file_facts(eng) == []


def test_template_type_on_items_after_chdir(
    nautical: Path, elsewhere: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    items = _scan_then_chdir(nautical, elsewhere, monkeypatch)
    ug = UnifiedGraph()
    ug.add_items(items)
    assert sorted(str(s) for s in ug.graph.subjects(RDF.type, KB.Voyage)) == [
        str(ITEM["VOY-001"]),
        str(ITEM["VOY-002"]),
    ]
    assert _file_facts(QueryEngine(unified_graph=ug)) == []


def test_items_parsed_in_different_cwds(
    tagged_repo: Path, elsewhere: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # PAPER-001 parsed with cwd = repo, PAPER-002 parsed with cwd = repo/research,
    # merged from a third cwd: each item's <> goes to that item, whatever cwd its
    # file was parsed in
    sub = tagged_repo / "research"
    monkeypatch.chdir(tagged_repo)
    first = {i.id: i for i in _service(tagged_repo).scan()}
    monkeypatch.chdir(sub)
    second = {i.id: i for i in _service(tagged_repo).scan()}
    _assert_parsed_at([first["PAPER-001"]], "PAPER-001", tagged_repo)
    _assert_parsed_at([second["PAPER-002"]], "PAPER-002", sub)
    monkeypatch.chdir(elsewhere)
    ug = UnifiedGraph()
    ug.add_items([first["PAPER-001"], second["PAPER-002"]])
    _assert_tagged_on_items(ug.graph)
    assert (ITEM["PAPER-001"], KB.tag, Literal("t2")) not in ug.graph
    assert (ITEM["PAPER-002"], KB.tag, Literal("t1")) not in ug.graph
    assert _file_facts(QueryEngine(unified_graph=ug)) == []


def test_chdir_between_scan_and_merge_of_moved_items(
    repo: Path, elsewhere: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # #404's two-item history case, merged after a chdir
    _move("PAPER-002", "done", "--closed-by", "https://github.com/owner/repo/pull/42")
    _move("PAPER-001", "in_progress")
    items = _scan_then_chdir(repo, elsewhere, monkeypatch)
    ug = UnifiedGraph()
    ug.add_items(items)
    eng = QueryEngine(unified_graph=ug)
    q = "SELECT ?s WHERE {{ item:{i} kb:statusChange ?c . ?c kb:status ?s }}"
    assert [r["s"] for r in eng.sparql(q.format(i="PAPER-002"))] == [str(KB.done)]
    assert [r["s"] for r in eng.sparql(q.format(i="PAPER-001"))] == [str(KB.in_progress)]
    assert _file_facts(eng) == []


def test_mixed_scanned_and_hand_built_after_chdir(
    tagged_repo: Path, elsewhere: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # scanned items (parsed in A) and a hand-built item (parsed in B, no scan),
    # merged in B: both kinds land on their own IRIs
    items = _scan_then_chdir(tagged_repo, elsewhere, monkeypatch)
    hand = _hand_item("HAND-1", '<> kb:tag "hand" .')
    ug = UnifiedGraph()
    ug.add_items([*items, hand])
    _assert_tagged_on_items(ug.graph)
    assert (ITEM["HAND-1"], KB.tag, Literal("hand")) in ug.graph
    assert _file_facts(QueryEngine(unified_graph=ug)) == []


# ---------------------------------------------------------------------------
# GREEN before and after (controls)
# ---------------------------------------------------------------------------


def _hand_item(item_id: str, body: str, file_path: Path | None = None) -> WorkItem:
    """A WorkItem built without a scan, its graph parsed in the CURRENT cwd."""
    graph = yurtle_rdflib.parse_yurtle(_BLOCK.format(body=body)).graph
    phantom = _uri_of(Path.cwd())
    assert phantom in set(graph.subjects()), f"premise: <> did not parse to {phantom}"
    return WorkItem(
        id=item_id,
        title=f"Hand {item_id}",
        item_type=WorkItemType.TASK,
        status=WorkItemStatus.BACKLOG,
        file_path=file_path or Path(f"{item_id}.md"),
        graph=graph,
    )


def test_hand_built_item_falls_back_to_merge_cwd(
    elsewhere: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(elsewhere)
    ug = UnifiedGraph()
    ug.add_items(
        [
            _hand_item("HAND-1", '<> kb:tag "h1" ; a kb:Expedition .'),
            _hand_item("HAND-2", '<> kb:tag "h2" .\nitem:HAND-1 kb:related <> .'),
        ]
    )
    g = ug.graph
    assert (ITEM["HAND-1"], KB.tag, Literal("h1")) in g
    assert (ITEM["HAND-1"], RDF.type, KB.Expedition) in g
    assert (ITEM["HAND-2"], KB.tag, Literal("h2")) in g
    assert (ITEM["HAND-1"], KB.related, ITEM["HAND-2"]) in g
    assert (ITEM["HAND-1"], KB.tag, Literal("h2")) not in g
    eng = QueryEngine(unified_graph=ug)
    assert _file_nodes(eng) == []
    assert _ids(eng, ParsedQuery(tag="h2")) == ["HAND-2"]


def test_hand_built_item_file_path_elsewhere(
    tagged_repo: Path, elsewhere: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # the item's file_path does not matter; only the cwd its graph was parsed in
    monkeypatch.chdir(elsewhere)
    item = _hand_item("HAND-3", '<> kb:tag "h3" .', file_path=tagged_repo / "x.md")
    ug = UnifiedGraph()
    ug.add_item(item)
    assert (ITEM["HAND-3"], KB.tag, Literal("h3")) in ug.graph
    assert _file_facts(QueryEngine(unified_graph=ug)) == []


def test_from_service_after_chdir_rescans(
    tagged_repo: Path, elsewhere: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # from_service scans itself, so parse and merge share the cwd
    svc = _service(tagged_repo)
    svc.scan()
    monkeypatch.chdir(elsewhere)
    ug = UnifiedGraph.from_service(svc)
    _assert_tagged_on_items(ug.graph)
    eng = QueryEngine.from_service(svc, enable_semantic=False)
    assert _ids(eng, ParsedQuery(tag="t1")) == ["PAPER-001"]
    assert _file_facts(eng) == []


def test_no_chdir_unchanged(tagged_repo: Path) -> None:
    items = _service(tagged_repo).scan()
    ug = UnifiedGraph()
    ug.add_items(items)
    _assert_tagged_on_items(ug.graph)
    assert _file_facts(QueryEngine(unified_graph=ug)) == []


def test_item_graph_keeps_parse_time_file_subject(
    tagged_repo: Path, elsewhere: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # #404: item.graph itself is not rewritten; its subject is the SCAN cwd's IRI
    items = _scan_then_chdir(tagged_repo, elsewhere, monkeypatch)
    ug = UnifiedGraph()
    ug.add_items(items)
    item = ug.get_item("PAPER-001")
    assert item is not None and item.graph is not None
    assert (_uri_of(tagged_repo), KB.tag, Literal("t1")) in item.graph
    assert (ITEM["PAPER-001"], KB.tag, Literal("t1")) not in item.graph
