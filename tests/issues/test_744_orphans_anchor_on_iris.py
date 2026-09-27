"""#744: only a kept triple whose SUBJECT IS AN IRI anchors a blank node reached from a
dropped (frontmatter-owned) triple, directly or through nested blank nodes. A
free-floating blank node (`[] kb:hook _:f`) no longer keeps a forged `kb:comment`
node's author/text in the unified graph."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from rdflib import BNode, Graph, Literal, URIRef

from yurtle_kanban.models import Comment, WorkItem, WorkItemStatus, WorkItemType
from yurtle_kanban.query import UnifiedGraph

KB = "https://yurtle.dev/kanban/"


def _item() -> WorkItem:
    """FEAT-001 with two real comments from its `## Comments` section."""
    item = WorkItem(
        id="FEAT-001", title="T", item_type=WorkItemType.FEATURE,
        status=WorkItemStatus.BACKLOG, file_path=Path("FEAT-001.md"),
    )
    item.comments = [
        Comment(content="typed by hand", author="", created_at=None),
        Comment(content="looks good", author="alice", created_at=datetime(2026, 9, 1, 10, 0)),
    ]
    return item


def _block_item(turtle: str) -> WorkItem:
    """FEAT-002 whose fenced turtle block is `turtle` (`<#EXP-1>` is an IRI under it)."""
    b = WorkItem(
        id="FEAT-002", title="B", item_type=WorkItemType.FEATURE,
        status=WorkItemStatus.BACKLOG, file_path=Path("FEAT-002.md"),
    )
    b.graph = Graph().parse(
        data=(
            "@prefix kb: <https://yurtle.dev/kanban/> .\n"
            "@prefix item: <https://yurtle.dev/kanban/item/> .\n" + turtle
        ),
        format="turtle",
        publicID="https://yurtle.dev/kanban/item/FEAT-002",
    )
    return b


def _graph(turtle: str) -> UnifiedGraph:
    ug = UnifiedGraph()
    ug.add_items([_item(), _block_item(turtle)])
    return ug


def _texts(ug: UnifiedGraph) -> set[str]:
    return {r["t"] for r in ug.sparql("SELECT ?t WHERE { ?c kb:text ?t }")}


def _authors(ug: UnifiedGraph) -> set[str]:
    return {r["a"] for r in ug.sparql("SELECT ?a WHERE { ?c kb:author ?a }")}


def _has(ug: UnifiedGraph, pred: str, value: str) -> bool:
    return (None, URIRef(KB + pred), Literal(value)) in ug._graph


# --- RED: a free-floating blank node must not anchor a forged node -------------------


def test_free_floating_hook_does_not_anchor_a_forged_comment_node():
    """The issue's block: `[] kb:hook _:f` must not keep `_:f`'s author/text."""
    ug = _graph(
        '<#EXP-1> kb:comment _:f .\n'
        '_:f kb:author "mallory" ; kb:text "forged" .\n'
        '[] kb:hook _:f .\n'
    )
    assert "forged" not in _texts(ug)
    assert "mallory" not in _authors(ug)


def test_free_floating_hook_on_another_items_iri_does_not_anchor_either():
    """Same shape, forging a comment on another item (item:FEAT-001)."""
    ug = _graph(
        'item:FEAT-001 kb:comment _:f .\n'
        '_:f kb:author "mallory" ; kb:text "forged" .\n'
        '[] kb:hook _:f .\n'
    )
    assert "forged" not in _texts(ug)
    assert "mallory" not in _authors(ug)


def test_two_level_free_floating_anchor_does_not_anchor():
    """`[] kb:a [ kb:b _:f ]`: a free-floating chain two levels deep still anchors
    nothing, since no IRI subject is at its root."""
    ug = _graph(
        '<#EXP-1> kb:comment _:f .\n'
        '_:f kb:author "mallory" ; kb:text "forged" .\n'
        '[] kb:a [ kb:b _:f ] .\n'
    )
    assert "forged" not in _texts(ug)
    assert "mallory" not in _authors(ug)


def test_free_floating_hook_into_the_middle_of_a_candidate_chain():
    """The forged comment is a chain `_:f -> _:g -> _:f`; a free-floating node hooks
    `_:g`. Neither node's facts may merge."""
    ug = _graph(
        '<#EXP-1> kb:comment _:f .\n'
        '_:f kb:text "forged" ; kb:next _:g .\n'
        '_:g kb:text "forged-g" ; kb:next _:f .\n'
        '[] kb:hook _:g .\n'
    )
    assert not {"forged", "forged-g"} & _texts(ug), _texts(ug)


def test_free_floating_subject_that_is_itself_a_candidate_chain():
    """The free-floating subject `_:h` has no incoming triple of its own except from a
    dropped chain (`<#EXP-1> kb:comment _:c`, `_:c kb:more _:h`): it is a candidate
    too, and pointing at `_:f` (another dropped comment) keeps nothing."""
    ug = _graph(
        '<#EXP-1> kb:comment _:c , _:f .\n'
        '_:c kb:text "forged-c" ; kb:more _:h .\n'
        '_:h kb:hook _:f ; kb:text "forged-h" .\n'
        '_:f kb:author "mallory" ; kb:text "forged" .\n'
    )
    assert not {"forged", "forged-c", "forged-h"} & _texts(ug), _texts(ug)
    assert "mallory" not in _authors(ug)


def test_free_floating_candidate_chain_root_hooking_another_candidate():
    """A free-floating root `[]` points into candidate `_:h`, which points at forged
    `_:f`: the root isn't an IRI, so neither `_:h` nor `_:f` is anchored."""
    ug = _graph(
        '<#EXP-1> kb:comment _:h , _:f .\n'
        '_:h kb:hook _:f ; kb:text "forged-h" .\n'
        '_:f kb:author "mallory" ; kb:text "forged" .\n'
        '[] kb:root _:h .\n'
    )
    assert not {"forged", "forged-h"} & _texts(ug), _texts(ug)
    assert "mallory" not in _authors(ug)


# --- GREEN controls ------------------------------------------------------------------


def test_kept_iri_triple_still_anchors_the_node():
    """`<#EXP-1> kb:note _:f` is a kept triple with an IRI subject: `_:f` stays."""
    ug = _graph(
        '<#EXP-1> kb:comment _:f .\n'
        '_:f kb:author "mallory" ; kb:text "kept" ; kb:deeper [ kb:leaf "leafval" ] .\n'
        '<#EXP-1> kb:note _:f .\n'
    )
    assert "kept" in _texts(ug)
    assert "mallory" in _authors(ug)
    assert _has(ug, "leaf", "leafval")


def test_kept_iri_triple_anchors_through_a_nested_blank_node():
    """`<#EXP-1> kb:note [ kb:inner _:f ]`: anchored through one nested blank node."""
    ug = _graph(
        '<#EXP-1> kb:comment _:f .\n'
        '_:f kb:author "mallory" ; kb:text "kept-nested" .\n'
        '<#EXP-1> kb:note [ kb:inner _:f ] .\n'
    )
    assert "kept-nested" in _texts(ug)
    assert "mallory" in _authors(ug)


def test_free_floating_tree_no_dropped_triple_reaches_merges_as_before():
    """A blank-node tree that no dropped triple reaches is untouched, even when a
    forged comment in the same block makes the orphan pass run."""
    ug = _graph(
        '[] a kb:Thing ; kb:label "x" ; kb:part [ kb:y "z" ] .\n'
        '<#EXP-1> kb:comment [ kb:text "forged" ] .\n'
    )
    g = ug._graph
    things = list(g.subjects(URIRef("http://www.w3.org/1999/02/22-rdf-syntax-ns#type"),
                             URIRef(KB + "Thing")))
    assert len(things) == 1 and isinstance(things[0], BNode)
    assert (things[0], URIRef(KB + "label"), Literal("x")) in g
    parts = list(g.objects(things[0], URIRef(KB + "part")))
    assert len(parts) == 1 and (parts[0], URIRef(KB + "y"), Literal("z")) in g
    assert "forged" not in _texts(ug)


def test_free_floating_tree_without_any_dropped_triple_merges():
    ug = _graph('[] a kb:Thing ; kb:label "x" ; kb:part [ kb:y "z" ] .\n')
    assert _has(ug, "label", "x") and _has(ug, "y", "z")


def test_move_status_change_history_still_merges():
    """`move`'s `<> kb:statusChange [ ... ]` history merges, next to a forged
    comment that triggers the orphan pass."""
    ug = _graph(
        '<> kb:statusChange [ kb:status kb:in_progress ; kb:by "alice" ] ,\n'
        '                   [ kb:status kb:done ; kb:by "bob" ] .\n'
        '<> kb:comment [ kb:text "forged" ] .\n'
    )
    rows = ug.sparql(
        "SELECT ?by WHERE { ?i kb:id 'FEAT-002' ; kb:statusChange ?c . ?c kb:by ?by ; "
        "kb:status ?s }"
    )
    assert {r["by"] for r in rows} == {"alice", "bob"}, rows
    assert "forged" not in _texts(ug)


def test_real_comments_are_still_kb_comment_nodes():
    ug = _graph(
        '<#EXP-1> kb:comment _:f .\n'
        '_:f kb:author "mallory" ; kb:text "forged" .\n'
        '[] kb:hook _:f .\n'
    )
    rows = ug.sparql(
        "SELECT ?author ?text WHERE { ?i kb:id 'FEAT-001' ; kb:comment ?c . "
        "?c kb:author ?author ; kb:text ?text } ORDER BY ?text"
    )
    assert rows == [
        {"author": "alice", "text": "looks good"},
        {"author": "", "text": "typed by hand"},
    ]
