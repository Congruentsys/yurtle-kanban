"""What `<>` resolved to when a graph was parsed (#404, #413, #421).

yurtle_rdflib resolves `<>` in fenced blocks against the process cwd. The unified
graph maps that IRI to the item, so it must know the one in force at parse time.
The registry is keyed by object identity, never stored on the rdflib Graph (a
third-party object) and never by Graph equality: rdflib Graphs compare and hash by
identifier, so a rebuilt `Graph(identifier=...)` must not inherit another's IRI.
"""

from __future__ import annotations

import weakref
from typing import Any

_self_iris: dict[int, tuple[weakref.ref, str]] = {}


def set_self_iri(graph: Any, iri: str) -> None:
    """Record that `<>` meant `iri` when `graph` was parsed."""
    key = id(graph)

    def forget(ref: weakref.ref, k: int = key) -> None:
        # only this graph's own entry: a late callback must not evict a newer graph
        # that got the same id (#430)
        entry = _self_iris.get(k)
        if entry is not None and entry[0] is ref:
            del _self_iris[k]

    try:
        ref = weakref.ref(graph, forget)
    except TypeError:
        return  # can't be weakly referenced: record nothing; merge falls back (#430)
    _self_iris[key] = (ref, iri)


def self_iri(graph: Any) -> str | None:
    """The IRI `<>` meant when `graph` was parsed, or None if it wasn't recorded."""
    entry = _self_iris.get(id(graph))
    if entry is None or entry[0]() is not graph:
        return None
    return entry[1]
