"""#407: the indexer ignores blank-node subjects when it builds a WorkItem.

Follow-up from the review of PR #401 (#395).

``WorkItemIndexer._parse_file`` (reached through the public ``WorkItemIndexer.scan()``)
took the *first* ``kb:id`` / ``kb:status`` triple, and any ``(?, ?, kb:<Type>)``
triple, for any subject. A ``move`` history (README "Graph provenance") is written as
``<> kb:statusChange [ kb:status kb:done ; … ]``, so a history blank node could decide
the item's status, ID or type.

Decided behaviour:

1. A ``kb:status`` whose subject is a blank node never decides the status; the item's
   own (non-blank-node) ``kb:status`` does, whatever the graph's iteration order.
2. A ``kb:id`` whose subject is a blank node never decides the ID: the own ``kb:id``
   wins, and with no own ``kb:id`` the ID falls back to the filename stem.
3. A blank node typed ``kb:<Type>`` never decides the type: the own type wins, and a
   file whose only typed subject is a blank node is not a work item.

Controls: a file with only its own ``kb:status`` indexes as before; a file with no
status defaults to backlog.

rdflib graph iteration order is arbitrary (blank-node ids are random), so each case is
repeated over several files with several differently-statused history entries: a
first-triple-wins bug is caught on at least one ordering.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.indexer import WorkItemIndexer
from yurtle_kanban.models import WorkItem, WorkItemStatus, WorkItemType

# WorkItemIndexer is deprecated (#434); these tests still pin its behaviour.
pytestmark = pytest.mark.filterwarnings("ignore:WorkItemIndexer:DeprecationWarning")

REPEATS = 40
PREFIX = "@prefix kb: <https://yurtle.dev/kanban/> ."


def _write(root: Path, name: str, body: str) -> Path:
    work = root / "work"
    work.mkdir(parents=True, exist_ok=True)
    path = work / f"{name}.md"
    path.write_text(f"# {name}\n\n```turtle\n{PREFIX}\n{body}\n```\n")
    return path


def _scan(root: Path) -> dict[Path, WorkItem]:
    items = WorkItemIndexer(KanbanConfig(), root).scan()
    return {item.file_path: item for item in items}


def _history(statuses: list[str]) -> str:
    entries = [f'[ kb:status kb:{s}  ; kb:at "2026-0{i + 1}-01" ]' for i, s in enumerate(statuses)]
    return " ,\n    ".join(entries)


# --- 1. status -------------------------------------------------------------------------

HISTORY_STATUSES = ["done", "blocked", "review", "ready", "backlog"]


def test_history_status_never_decides_the_status(tmp_path: Path) -> None:
    paths = []
    for n in range(REPEATS):
        # Rotate the history so the text order differs per file too.
        hist = HISTORY_STATUSES[n % 5 :] + HISTORY_STATUSES[: n % 5]
        body = (
            f'<> a kb:Expedition ;\n  kb:id "EXP-{n}" ;\n  kb:status kb:in_progress ;\n'
            f"  kb:statusChange {_history(hist)} ."
        )
        paths.append(_write(tmp_path, f"EXP-{n}", body))

    items = _scan(tmp_path)
    got = {p.name: items[p].status for p in paths if p in items}
    assert len(got) == REPEATS, got
    wrong = {k: v for k, v in got.items() if v is not WorkItemStatus.IN_PROGRESS}
    assert not wrong, f"history kb:status decided the status: {wrong}"


def test_history_listed_before_own_status_never_decides(tmp_path: Path) -> None:
    paths = []
    for n in range(REPEATS):
        body = (
            f"<> kb:statusChange {_history(['done', 'blocked', 'review'])} ;\n"
            f'  a kb:Expedition ;\n  kb:id "EXP-{n}" ;\n  kb:status kb:ready .'
        )
        paths.append(_write(tmp_path, f"EXP-{n}", body))

    items = _scan(tmp_path)
    got = {p.name: items[p].status for p in paths if p in items}
    assert len(got) == REPEATS, got
    wrong = {k: v for k, v in got.items() if v is not WorkItemStatus.READY}
    assert not wrong, f"history kb:status decided the status: {wrong}"


def test_history_only_status_defaults_to_backlog(tmp_path: Path) -> None:
    """With no own kb:status, a history's kb:status does not stand in for it."""
    paths = []
    for n in range(REPEATS):
        body = (
            f'<> a kb:Expedition ;\n  kb:id "EXP-{n}" ;\n'
            f"  kb:statusChange {_history(['done', 'blocked', 'review'])} ."
        )
        paths.append(_write(tmp_path, f"EXP-{n}", body))

    items = _scan(tmp_path)
    got = {p.name: items[p].status for p in paths if p in items}
    assert len(got) == REPEATS, got
    wrong = {k: v for k, v in got.items() if v is not WorkItemStatus.BACKLOG}
    assert not wrong, f"history kb:status decided the status: {wrong}"


# --- 2. id -----------------------------------------------------------------------------


def test_blank_node_id_never_decides_the_id(tmp_path: Path) -> None:
    paths = []
    for n in range(REPEATS):
        hist = ", ".join(f'[ kb:status kb:done ; kb:id "BOGUS-{n}-{k}" ]' for k in range(4))
        body = (
            f'<> a kb:Expedition ;\n  kb:id "EXP-{n}" ;\n  kb:status kb:ready ;\n'
            f"  kb:statusChange {hist} ."
        )
        paths.append(_write(tmp_path, f"exp-file-{n}", body))

    items = _scan(tmp_path)
    got = {p.name: items[p].id for p in paths if p in items}
    assert len(got) == REPEATS, got
    wrong = {k: v for k, v in got.items() if not v.startswith("EXP-")}
    assert not wrong, f"blank-node kb:id decided the ID: {wrong}"
    assert got == {f"exp-file-{n}.md": f"EXP-{n}" for n in range(REPEATS)}


def test_blank_node_id_only_falls_back_to_filename(tmp_path: Path) -> None:
    paths = []
    for n in range(REPEATS):
        body = (
            "<> a kb:Expedition ;\n  kb:status kb:ready ;\n"
            f'  kb:statusChange [ kb:status kb:done ; kb:id "BOGUS-{n}" ] .'
        )
        paths.append(_write(tmp_path, f"exp-{n}", body))

    items = _scan(tmp_path)
    got = {p.name: items[p].id for p in paths if p in items}
    assert got == {f"exp-{n}.md": f"EXP-{n}" for n in range(REPEATS)}, got


# --- 3. type ---------------------------------------------------------------------------


def test_blank_node_type_never_decides_the_type(tmp_path: Path) -> None:
    """kb:Feature is checked before kb:Expedition, so a nested Feature would win."""
    path = _write(
        tmp_path,
        "EXP-1",
        '<> a kb:Expedition ;\n  kb:id "EXP-1" ;\n  kb:status kb:ready ;\n'
        "  kb:statusChange [ a kb:Feature ; kb:status kb:done ] .",
    )
    items = _scan(tmp_path)
    assert path in items
    assert items[path].item_type is WorkItemType.EXPEDITION


def test_blank_node_typed_only_is_not_a_work_item(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "notes",
        '<> kb:related [ a kb:Expedition ; kb:id "EXP-9" ; kb:status kb:done ] .',
    )
    items = _scan(tmp_path)
    assert path not in items, items.get(path)


# --- controls --------------------------------------------------------------------------


def test_control_own_status_only(tmp_path: Path) -> None:
    paths = [
        _write(
            tmp_path,
            f"EXP-{n}",
            f'<> a kb:Expedition ;\n  kb:id "EXP-{n}" ;\n  kb:status kb:review .',
        )
        for n in range(3)
    ]
    items = _scan(tmp_path)
    for n, p in enumerate(paths):
        assert items[p].id == f"EXP-{n}"
        assert items[p].status is WorkItemStatus.REVIEW
        assert items[p].item_type is WorkItemType.EXPEDITION


def test_control_no_status_defaults_to_backlog(tmp_path: Path) -> None:
    path = _write(tmp_path, "EXP-1", '<> a kb:Expedition ;\n  kb:id "EXP-1" .')
    items = _scan(tmp_path)
    assert items[path].status is WorkItemStatus.BACKLOG
    assert items[path].id == "EXP-1"
