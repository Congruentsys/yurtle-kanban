# ruff: noqa: F811  -- pytest fixtures imported from the #349/#413 test modules are re-bound as args
"""Issue #430 — ``_graph_iri`` hardening (follow-ups from the review of PR #427, #421).

Decided behaviour pinned here:

1. Registration never breaks parsing. If a graph can't be weakly referenced
   (``weakref.ref`` raises ``TypeError``, e.g. rdflib gains ``__slots__`` without
   ``__weakref__``), ``set_self_iri`` does not raise and records nothing; the merge
   falls back to the cwd. ``KanbanService._parse_graph`` still returns a graph, a
   scan still yields items with graphs, and ``UnifiedGraph`` maps ``<>`` to the item
   when the cwd is unchanged.
2. A stale weakref callback can't evict a newer entry: the callback removes the
   registry entry only if that entry still holds the callback's own weakref.
   Tested deterministically (no GC timing): both graphs are forced onto the same
   key by patching ``id`` in the module, and A's callback is invoked by hand.
3. ``KanbanService._parse_graph``'s docstring carries the "``+=`` keeps the
   parse-time IRI" note.

``weakref.ref`` is patched by swapping the module's ``weakref`` name for a
namespace, never by patching the real ``weakref`` module (rdflib uses it too).
"""

from __future__ import annotations

import weakref
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from rdflib import Graph

from tests.issues.test_349_sparql_distinct import repo  # noqa: F401 (fixture)
from tests.issues.test_404_block_base_iri import _file_facts
from tests.issues.test_413_phantom_at_parse import (  # noqa: F401 (fixtures)
    _BLOCK,
    _assert_tagged_on_items,
    _service,
    tagged_repo,
)
from yurtle_kanban import _graph_iri
from yurtle_kanban.query import QueryEngine, UnifiedGraph
from yurtle_kanban.service import KanbanService


def _no_weakref(*_a: Any, **_k: Any) -> Any:
    raise TypeError("cannot create weak reference to 'Graph' object")


@pytest.fixture
def unweakrefable(monkeypatch: pytest.MonkeyPatch) -> None:
    """``weakref.ref`` as seen by ``_graph_iri`` raises ``TypeError``."""
    monkeypatch.setattr(_graph_iri, "weakref", SimpleNamespace(ref=_no_weakref))


class _Slotted:
    __slots__ = ("x",)


# ---------------------------------------------------------------------------
# 1. Registration never breaks parsing (RED today: TypeError escapes / graph None)
# ---------------------------------------------------------------------------


def test_set_self_iri_on_slotted_object_does_not_raise() -> None:
    obj = _Slotted()
    _graph_iri.set_self_iri(obj, "file:///nowhere/")
    assert _graph_iri.self_iri(obj) is None


def test_set_self_iri_weakref_typeerror_does_not_raise(unweakrefable: None) -> None:
    g = Graph()
    _graph_iri.set_self_iri(g, "file:///nowhere/")
    assert _graph_iri.self_iri(g) is None


def test_parse_graph_survives_unweakrefable_graph(
    tagged_repo: Path, unweakrefable: None
) -> None:
    g = _service(tagged_repo)._parse_graph(_BLOCK.format(body='<> kb:tag "z" .'))
    assert g is not None
    assert len(g) > 0


def test_scan_survives_unweakrefable_graph(tagged_repo: Path, unweakrefable: None) -> None:
    items = _service(tagged_repo).scan()
    (item,) = [i for i in items if i.id == "PAPER-001"]
    assert item.graph is not None
    assert all(i.graph is not None for i in items if i.id in ("PAPER-001", "PAPER-002"))


def test_merge_falls_back_to_cwd_when_unregistered(
    tagged_repo: Path, unweakrefable: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tagged_repo)
    items = _service(tagged_repo).scan()
    ug = UnifiedGraph()
    ug.add_items(items)
    _assert_tagged_on_items(ug.graph)
    assert _file_facts(QueryEngine(unified_graph=ug)) == []


# ---------------------------------------------------------------------------
# 2. A stale callback can't evict a newer entry (RED today: pops by key blindly)
# ---------------------------------------------------------------------------

_KEY = 430430430


@pytest.fixture
def recorded_refs(monkeypatch: pytest.MonkeyPatch) -> list[weakref.ref]:
    """Every graph maps to one key; every weakref ``_graph_iri`` creates is recorded."""
    made: list[weakref.ref] = []

    def _ref(obj: Any, *cb: Any) -> weakref.ref:
        r = weakref.ref(obj, *cb)
        made.append(r)
        return r

    monkeypatch.setattr(_graph_iri, "weakref", SimpleNamespace(ref=_ref))
    monkeypatch.setattr(_graph_iri, "id", lambda _obj: _KEY, raising=False)
    yield made
    _graph_iri._self_iris.pop(_KEY, None)


def test_stale_callback_does_not_evict_newer_entry(recorded_refs: list[weakref.ref]) -> None:
    a, b = Graph(), Graph()  # both kept alive: only the hand-invoked callback fires
    _graph_iri.set_self_iri(a, "file:///a/")
    assert len(recorded_refs) == 1, "premise: registering A made one weakref"
    ref_a = recorded_refs[0]
    callback = ref_a.__callback__
    assert callback is not None, "premise: A's weakref has a callback"

    _graph_iri.set_self_iri(b, "file:///b/")
    assert _graph_iri.self_iri(b) == "file:///b/", "premise: B replaced A under one key"

    callback(ref_a)  # A's late (stale) callback

    assert _graph_iri.self_iri(b) == "file:///b/"
    assert a is not None and b is not None


def test_own_callback_still_removes_own_entry(recorded_refs: list[weakref.ref]) -> None:
    # control: a callback for the CURRENT entry still cleans it up
    a = Graph()
    _graph_iri.set_self_iri(a, "file:///a/")
    ref_a = recorded_refs[-1]
    assert ref_a.__callback__ is not None
    ref_a.__callback__(ref_a)
    assert _KEY not in _graph_iri._self_iris
    assert _graph_iri.self_iri(a) is None


def test_registration_roundtrip_control() -> None:
    g = Graph()
    _graph_iri.set_self_iri(g, "file:///rt/")
    assert _graph_iri.self_iri(g) == "file:///rt/"
    assert _graph_iri.self_iri(Graph()) is None


# ---------------------------------------------------------------------------
# 3. The "+=" note lives in _parse_graph's docstring (RED today: only a comment)
# ---------------------------------------------------------------------------


def test_parse_graph_docstring_mentions_plus_equals_iri() -> None:
    doc = KanbanService._parse_graph.__doc__ or ""
    assert "+=" in doc or "parse-time" in doc.lower()
    assert "IRI" in doc
