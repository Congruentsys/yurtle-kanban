"""Issue #986: one `_rev_roots()` for every fetched-tree reader.

`_ids_at` and `_items_at` computed "the folders a scan walks" separately; the two
drifted apart once (#954). Both now call `_rev_roots`, so they can't again.
"""

from __future__ import annotations

import ast
import inspect
import textwrap

from yurtle_kanban.service import KanbanService


def _calls(method: object) -> set[str]:
    tree = ast.parse(textwrap.dedent(inspect.getsource(method)))
    return {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }


def test_ids_at_and_items_at_share_rev_roots() -> None:
    for reader in (KanbanService._ids_at, KanbanService._items_at):
        called = _calls(reader)
        assert "_rev_roots" in called, reader.__name__
        # the roots are never recomputed inline: no direct placement-dir walk
        assert "_placement_dirs" not in called, reader.__name__
        assert "get_work_paths" not in called, reader.__name__
