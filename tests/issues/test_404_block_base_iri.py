# ruff: noqa: F811  -- pytest fixtures imported from the #349 test module are re-bound as args
"""Issue #404 — ``<>`` in an item's fenced blocks means the item, in the unified graph.

``KanbanService._parse_graph`` calls ``yurtle_rdflib.parse_yurtle`` without a base, so
``<>`` in a fenced yurtle/turtle block resolves against the PROCESS CWD, as
``file:///<cwd>/`` (probed: ``file:///private/tmp/yk-404/`` from /tmp/yk-404 and
``file:///…/scratchpad/sub/`` from a subdirectory; a ``source_path`` does not change it).
Every item whose block writes ``<> …`` (the nautical/software/spec templates,
``move``'s ``<> kb:statusChange [ … ]`` history) puts its triples on that ONE phantom
node, shared by every file: tags, ``rdf:type`` and history pile up there and never
reach the item.

Decided ([steer], bucket 2): ``UnifiedGraph.add_item`` rewrites the phantom IRI (the
cwd's file URI at parse time) to the item's own IRI ``ITEM[item.id]`` wherever it is
the subject or object of a triple of the item's per-file graph, during the merge.

* Owned predicates are still skipped (#395), so a template's ``<> kb:status kb:harbor``
  or ``<> kb:id "VOY-1"`` never gives the item a second value.
* ``item.graph`` itself is unchanged: it keeps the ``file:`` subject.
* Other relative IRIs (``<#frag>``) and absolute IRIs are left as they are.

Boards: the #349 fixture (hdd; PAPER-001..003, H-004; cwd = repo) and a nautical board
made by the real CLI (``voyage create`` renders the voyage template's ``<>`` block).
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner
from rdflib import RDF, BNode, Literal, URIRef

from tests.issues.test_349_sparql_distinct import (  # noqa: F401 (fixtures)
    DUAL,
    engine,
    repo,
)
from tests.issues.test_395_frontmatter_owned_facts import (
    OWNED,
    _add_block,
    _engine_with,
    _expected,
    _item_file,
)
from yurtle_kanban.cli import get_service, main
from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.query import ITEM, KB, ParsedQuery, QueryEngine
from yurtle_kanban.service import KanbanService

NORMAL_ORDER = [DUAL, "PAPER-003", "PAPER-002", "PAPER-001"]
ALL_IDS = set(NORMAL_ORDER)
PR_URL = "https://github.com/owner/repo/pull/42"

# predicates that must never sit on a file: subject in the unified graph
ITEM_FACTS = {KB.tag, RDF.type, KB.statusChange}

_TAGGED = {
    "PAPER-001": '<> kb:tag "t1" ; a kb:Expedition .',
    "PAPER-002": '<> kb:tag "t2" .',
}


def _phantom() -> URIRef:
    """The IRI ``<>`` resolves to right now: the process cwd's file URI, with '/'."""
    return URIRef(Path(os.getcwd()).as_uri() + "/")


def _ids(eng: QueryEngine, parsed: ParsedQuery) -> list[str]:
    return [i.id for i in eng.structured_query(parsed)]


def _cli_ids(query: str) -> list[str]:
    result = CliRunner().invoke(main, ["query", "--no-semantic", "--json", query])
    assert result.exception is None, f"raised {result.exception!r}"
    assert result.exit_code == 0, result.output
    return [row["id"] for row in json.loads(result.output)]


def _file_facts(eng: QueryEngine) -> list[tuple[str, str, str]]:
    """Triples in the unified graph whose subject is a file: IRI and whose
    predicate is a per-item fact (kb:tag, rdf:type, kb:statusChange)."""
    return sorted(
        (str(s), str(p), str(o))
        for s, p, o in eng._ug.graph
        if isinstance(s, URIRef) and str(s).startswith("file:") and p in ITEM_FACTS
    )


def _file_nodes(eng: QueryEngine) -> list[str]:
    """Every file: IRI used as subject or object anywhere in the unified graph."""
    nodes = set()
    for s, _, o in eng._ug.graph:
        for n in (s, o):
            if isinstance(n, URIRef) and str(n).startswith("file:"):
                nodes.add(str(n))
    return sorted(nodes)


def _objects(eng: QueryEngine, item_id: str, pred: URIRef) -> list:
    return sorted(eng._ug.graph.objects(ITEM[item_id], pred), key=str)


def _assert_premise(eng: QueryEngine, item_id: str) -> None:
    """The per-file graph still holds the phantom subject (item.graph unchanged)."""
    item = eng._ug.get_item(item_id)
    assert item is not None and item.graph is not None
    subjects = set(item.graph.subjects())
    assert _phantom() in subjects, (
        f"premise: {item_id}'s per-file graph has no {_phantom()} subject: {subjects}"
    )


def _move(*args: str) -> None:
    result = CliRunner().invoke(main, ["move", *args, "--force", "--no-commit"])
    assert result.exception is None, f"raised {result.exception!r}"
    assert result.exit_code == 0, result.output


@pytest.fixture
def tagged(repo: Path) -> QueryEngine:
    """PAPER-001: ``<> kb:tag "t1" ; a kb:Expedition``; PAPER-002: ``<> kb:tag "t2"``."""
    eng = _engine_with(repo, _TAGGED)
    for item_id in _TAGGED:
        _assert_premise(eng, item_id)
    return eng


# ---------------------------------------------------------------------------
# nautical board built by the real CLI from the real templates
# ---------------------------------------------------------------------------


@pytest.fixture
def nautical(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """VOY-001 and VOY-002 from the voyage template (``<> a kb:Voyage ; kb:id
    "VOY-n" ; kb:status kb:harbor ; …``), EXP-001 moved to in_progress (``move``
    writes ``<> kb:statusChange [ … ]``) and EXP-002 left alone."""
    for args in (
        ["git", "init", "-b", "main"],
        ["git", "config", "user.email", "test@test.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(args, cwd=tmp_path, capture_output=True, check=True)
    from yurtle_kanban import config as config_mod

    config_mod._theme_cache.clear()
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    for args in (
        ["init", "--theme", "nautical"],
        ["voyage", "create", "Voy A"],
        ["voyage", "create", "Voy B"],
        ["create", "expedition", "One", "--tags", "fm1"],
        ["create", "expedition", "Two"],
    ):
        result = runner.invoke(main, args)
        assert result.exit_code == 0, f"fixture step {args} failed: {result.output}"
    for voy in ("VOY-001", "VOY-002"):
        text = _item_file(tmp_path, voy).read_text(encoding="utf-8")
        assert "\n<> a kb:Voyage ;" in text and "kb:status kb:harbor" in text, (
            f"fixture: {voy} not rendered from the voyage template:\n{text}"
        )
    _move("EXP-001", "in_progress")
    text = _item_file(tmp_path, "EXP-001").read_text(encoding="utf-8")
    assert "<> kb:statusChange [" in text, f"fixture: no history written:\n{text}"
    return tmp_path


@pytest.fixture
def naut_engine(nautical: Path) -> QueryEngine:
    eng = QueryEngine.from_service(get_service(), enable_semantic=False)
    for item_id in ("VOY-001", "VOY-002", "EXP-001"):
        _assert_premise(eng, item_id)
    return eng


# ---------------------------------------------------------------------------
# RED before the fix
# ---------------------------------------------------------------------------

# --- 1. hand-written <> blocks: facts hang off each item's own IRI -----------


def test_block_tags_on_own_items(tagged: QueryEngine) -> None:
    g = tagged._ug.graph
    assert (ITEM["PAPER-001"], KB.tag, Literal("t1")) in g
    assert (ITEM["PAPER-001"], RDF.type, KB.Expedition) in g
    assert (ITEM["PAPER-002"], KB.tag, Literal("t2")) in g


def test_block_tags_not_crossed(tagged: QueryEngine) -> None:
    g = tagged._ug.graph
    assert (ITEM["PAPER-001"], KB.tag, Literal("t2")) not in g
    assert (ITEM["PAPER-002"], KB.tag, Literal("t1")) not in g
    assert (ITEM["PAPER-002"], RDF.type, KB.Expedition) not in g


def test_no_file_subject_carries_item_facts(tagged: QueryEngine) -> None:
    assert _file_facts(tagged) == []


# --- 2. a tag query finds the item whose block tags it -----------------------
# (block-only tags: none of t1/t2 is in any frontmatter, so frontmatter can't mask it)


def test_tag_query_block_only(tagged: QueryEngine) -> None:
    assert _ids(tagged, ParsedQuery(tag="t1")) == ["PAPER-001"]
    assert _ids(tagged, ParsedQuery(tag="t2")) == ["PAPER-002"]


def test_cli_tag_query_block_only(tagged: QueryEngine) -> None:
    assert _cli_ids("items tagged t1") == ["PAPER-001"]
    assert _cli_ids("items tagged t2") == ["PAPER-002"]


# --- <> as an object ---------------------------------------------------------


def test_block_self_reference_as_object(repo: Path) -> None:
    eng = _engine_with(repo, {"PAPER-002": "item:PAPER-001 kb:related <> ."})
    assert (ITEM["PAPER-001"], KB.related, ITEM["PAPER-002"]) in eng._ug.graph
    assert _file_nodes(eng) == []


# --- 1. real templates (nautical voyage) --------------------------------------


def test_template_type_on_own_item(naut_engine: QueryEngine) -> None:
    g = naut_engine._ug.graph
    assert _file_facts(naut_engine) == []
    for voy in ("VOY-001", "VOY-002"):
        assert (ITEM[voy], RDF.type, KB.Voyage) in g
    # no phantom node joins the voyages: ?x a kb:Voyage is exactly the two items
    assert sorted(str(s) for s in g.subjects(RDF.type, KB.Voyage)) == [
        str(ITEM["VOY-001"]),
        str(ITEM["VOY-002"]),
    ]


def test_template_plus_block_tag(nautical: Path) -> None:
    _add_block(nautical, "VOY-001", '<> kb:tag "voy-a" .')
    _add_block(nautical, "VOY-002", '<> kb:tag "voy-b" .')
    eng = QueryEngine.from_service(get_service(), enable_semantic=False)
    assert _ids(eng, ParsedQuery(tag="voy-a")) == ["VOY-001"]
    assert _ids(eng, ParsedQuery(tag="voy-b")) == ["VOY-002"]
    assert _file_facts(eng) == []


# --- 3. move's history hangs off the item ------------------------------------


def test_move_history_on_item_iri(naut_engine: QueryEngine) -> None:
    rows = naut_engine.sparql(
        "SELECT ?s WHERE { item:EXP-001 kb:statusChange ?c . ?c kb:status ?s }"
    )
    assert [r["s"] for r in rows] == [str(KB.in_progress)]
    assert _file_facts(naut_engine) == []


def test_move_history_per_item(repo: Path) -> None:
    # two items moved: each item's history on its own IRI, none on a shared node
    _move("PAPER-002", "done", "--closed-by", PR_URL)
    _move("PAPER-001", "in_progress")
    eng = QueryEngine.from_service(get_service(), enable_semantic=False)
    q = "SELECT ?s ?pr WHERE {{ item:{i} kb:statusChange ?c . ?c kb:status ?s . OPTIONAL {{ ?c kb:closedBy ?pr }} }}"
    p2 = eng.sparql(q.format(i="PAPER-002"))
    p1 = eng.sparql(q.format(i="PAPER-001"))
    assert [(r["s"], r["pr"]) for r in p2] == [(str(KB.done), PR_URL)]
    assert [(r["s"], r["pr"]) for r in p1] == [(str(KB.in_progress), "")]
    assert _file_facts(eng) == []


# --- 5. the rewrite follows the cwd at parse time ------------------------------


def _service_at(repo_root: Path) -> QueryEngine:
    config = KanbanConfig.load(repo_root / ".kanban" / "config.yaml")
    return QueryEngine.from_service(KanbanService(config, repo_root), enable_semantic=False)


def test_cwd_subdirectory(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for item_id, body in _TAGGED.items():
        _add_block(repo, item_id, body)
    sub = repo / "research"
    assert sub.is_dir(), "fixture: hdd board has no research/ directory"
    monkeypatch.chdir(sub)
    eng = _service_at(repo)
    _assert_premise(eng, "PAPER-001")
    assert _phantom() != URIRef(repo.as_uri() + "/"), "premise: phantom did not follow cwd"
    assert (ITEM["PAPER-001"], KB.tag, Literal("t1")) in eng._ug.graph
    assert (ITEM["PAPER-002"], KB.tag, Literal("t2")) in eng._ug.graph
    assert _file_facts(eng) == []
    assert _ids(eng, ParsedQuery(tag="t1")) == ["PAPER-001"]


def test_cwd_outside_repo(
    repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    for item_id, body in _TAGGED.items():
        _add_block(repo, item_id, body)
    monkeypatch.chdir(tmp_path_factory.mktemp("elsewhere"))
    eng = _service_at(repo)
    _assert_premise(eng, "PAPER-001")
    assert (ITEM["PAPER-001"], KB.tag, Literal("t1")) in eng._ug.graph
    assert (ITEM["PAPER-002"], KB.tag, Literal("t2")) in eng._ug.graph
    assert _file_facts(eng) == []


# ---------------------------------------------------------------------------
# GREEN before and after (controls / guards)
# ---------------------------------------------------------------------------

# --- premise: <> resolves to the cwd's file URI --------------------------------


def test_premise_phantom_is_cwd(tagged: QueryEngine, repo: Path) -> None:
    assert _phantom() == URIRef(Path(os.getcwd()).as_uri() + "/")
    item = tagged._ug.get_item("PAPER-001")
    assert item is not None and item.graph is not None
    assert (_phantom(), KB.tag, Literal("t1")) in item.graph


# --- 6. item.graph is unchanged ------------------------------------------------


def test_item_graph_keeps_file_subject(tagged: QueryEngine) -> None:
    p1 = tagged._ug.get_item("PAPER-001")
    p2 = tagged._ug.get_item("PAPER-002")
    assert p1 is not None and p1.graph is not None
    assert p2 is not None and p2.graph is not None
    assert (_phantom(), KB.tag, Literal("t1")) in p1.graph
    assert (_phantom(), RDF.type, KB.Expedition) in p1.graph
    assert (_phantom(), KB.tag, Literal("t2")) in p2.graph
    assert (ITEM["PAPER-001"], KB.tag, Literal("t1")) not in p1.graph


# --- 4. owned predicates from <> are still skipped (#395) ---------------------


def test_template_owned_predicates_frontmatter_only(naut_engine: QueryEngine) -> None:
    for voy in ("VOY-001", "VOY-002"):
        item = naut_engine._ug.get_item(voy)
        assert item is not None
        for pred in OWNED:
            got = _objects(naut_engine, voy, KB[pred])
            assert got == _expected(item, pred), (voy, pred, got)
    # the template's kb:status kb:harbor / kb:id "VOY-1" never reach the item
    assert _objects(naut_engine, "VOY-001", KB.status) == [KB.backlog]
    assert _objects(naut_engine, "VOY-001", KB.id) == [Literal("VOY-001")]


def test_hand_block_owned_predicates_skipped(repo: Path) -> None:
    eng = _engine_with(
        repo,
        {
            "PAPER-002": (
                '<> kb:status kb:done ; kb:id "PAPER-009" ; kb:title "Hijacked" ;'
                ' kb:numericId 999 ; kb:tag "keep" .'
            )
        },
    )
    item = eng._ug.get_item("PAPER-002")
    assert item is not None
    for pred in OWNED:
        assert _objects(eng, "PAPER-002", KB[pred]) == _expected(item, pred), pred
    assert _ids(eng, ParsedQuery(status_include=["done"])) == []
    assert _ids(eng, ParsedQuery(id_min=500)) == []
    assert _ids(eng, ParsedQuery(status_include=["backlog"])) == NORMAL_ORDER


def test_history_status_does_not_match_status_filter(naut_engine: QueryEngine) -> None:
    # EXP-001 is in_progress by frontmatter; its history blank node's kb:status is not
    assert _objects(naut_engine, "EXP-001", KB.status) == [KB.in_progress]
    assert _ids(naut_engine, ParsedQuery(status_include=["in_progress"])) == ["EXP-001"]
    assert "EXP-001" not in _ids(naut_engine, ParsedQuery(status_include=["backlog"]))


def test_sparql_ids_are_items_only(naut_engine: QueryEngine) -> None:
    # the README's query: every ?item with a kb:id is an item
    rows = naut_engine.sparql("SELECT ?item ?id WHERE { ?item kb:id ?id }")
    assert sorted((r["item"], r["id"]) for r in rows) == sorted(
        (str(ITEM[i]), i) for i in ("VOY-001", "VOY-002", "EXP-001", "EXP-002")
    )


# --- 6. other relative IRIs and absolute IRIs are untouched --------------------


def test_fragment_and_absolute_iris_untouched(repo: Path) -> None:
    eng = _engine_with(
        repo,
        {
            "PAPER-002": (
                '<#frag> kb:tag "frag" .\n'
                '<https://example.org/thing> kb:tag "abs" ; kb:related <#frag> .'
            )
        },
    )
    frag = URIRef(_phantom() + "#frag")
    g = eng._ug.graph
    assert (frag, KB.tag, Literal("frag")) in g
    assert (URIRef("https://example.org/thing"), KB.tag, Literal("abs")) in g
    assert (URIRef("https://example.org/thing"), KB.related, frag) in g
    assert (ITEM["PAPER-002"], KB.tag, Literal("frag")) not in g
    assert (ITEM["PAPER-002"], KB.tag, Literal("abs")) not in g


def test_explicit_item_iri_blocks_unchanged(repo: Path) -> None:
    # #395's form (item:X subjects) is not affected by the rewrite
    eng = _engine_with(repo, {"PAPER-002": 'item:PAPER-001 kb:tag "cross" .'})
    assert _ids(eng, ParsedQuery(tag="cross")) == ["PAPER-001"]
    assert (ITEM["PAPER-002"], KB.tag, Literal("cross")) not in eng._ug.graph


def test_blank_node_history_merged(naut_engine: QueryEngine) -> None:
    item = naut_engine._ug.get_item("EXP-001")
    assert item is not None and item.graph is not None
    for triple in item.graph:
        if isinstance(triple[0], BNode):
            assert triple in naut_engine._ug.graph, triple


# --- #349 / #385 behaviour holds ---------------------------------------------


def test_control_349_queries(engine: QueryEngine) -> None:
    assert _ids(engine, ParsedQuery(type_filter=["paper", "hypothesis"])) == NORMAL_ORDER
    assert _ids(engine, ParsedQuery(tag="brain")) == [DUAL, "PAPER-002"]
    assert _ids(engine, ParsedQuery(assignee="mini")) == [DUAL, "PAPER-001"]


def test_control_385_numericid_with_self_block(tagged: QueryEngine) -> None:
    for item_id, n in (("PAPER-001", 1), ("PAPER-002", 2), ("PAPER-003", 3), (DUAL, 4)):
        item = tagged._ug.get_item(item_id)
        assert item is not None
        assert _objects(tagged, item_id, KB.numericId) == _expected(item, "numericId")
        assert item.numeric_id == n
    assert _ids(tagged, ParsedQuery(status_include=["backlog"])) == NORMAL_ORDER
