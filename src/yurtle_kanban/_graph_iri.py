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
    _self_iris[key] = (weakref.ref(graph, lambda _ref, k=key: _self_iris.pop(k, None)), iri)


def self_iri(graph: Any) -> str | None:
    """The IRI `<>` meant when `graph` was parsed, or None if it wasn't recorded."""
    entry = _self_iris.get(id(graph))
    if entry is None or entry[0]() is not graph:
        return None
    return entry[1]
