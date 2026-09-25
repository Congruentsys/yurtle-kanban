"""#426: the indexer honours a YAML-only ``type:``; several statuses and the tie-break are pinned.

Follow-up from the review of PR #422 (#416).

yurtle_rdflib turns an item's YAML frontmatter into triples on ``urn:doc:<stem>``::

    <urn:doc:EXP-1> rdf:type          "expedition"      # a Literal, not kb:Expedition
    <urn:doc:EXP-1> <https://yurtle.dev/schema/id> "EXP-1"
    <urn:doc:EXP-1> <https://yurtle.dev/pm/status> "ready"
    <urn:doc:EXP-1> <https://yurtle.dev/schema/title> "…"

``WorkItemIndexer._parse_file`` (reached through the public ``WorkItemIndexer.scan()``)
only looked for ``a kb:<Type>``, so an item typed only in its frontmatter (every item
``KanbanService.create_item`` writes) was not indexed.

Decided behaviour ([steer] on #426):

1. A literal ``rdf:type`` on a non-blank subject that matches a ``WorkItemType`` value,
   compared case-insensitively, counts like ``a kb:<Type>``: a frontmatter-only item is
   indexed with that type, and its id and status come from that subject. An unknown
   literal (``type: widget``) does not make a work item.
2. An IRI type still wins over a literal one (on different subjects); the WorkItemType
   order and the smallest-subject-IRI tie-break stay as they are.
3. With several ``kb:status`` values on the chosen subject the status is the smallest in
   ``WorkItemStatus`` order.
4. Two non-blank subjects with the same IRI type: id and status come from the subject
   with the smallest IRI string.

Controls: the #407 and #416 tests stay green.

Graph iteration order is not something to rely on, so the order-sensitive cases are
repeated over many files, with the competing triples written in varying order.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.indexer import WorkItemIndexer
from yurtle_kanban.models import WorkItem, WorkItemStatus, WorkItemType
from yurtle_kanban.service import KanbanService

REPEATS = 40
PREFIX = "@prefix kb: <https://yurtle.dev/kanban/> ."
STATUS_ORDER = list(WorkItemStatus)


def _write_raw(root: Path, name: str, text: str) -> Path:
    work = root / "work"
    work.mkdir(parents=True, exist_ok=True)
    path = work / f"{name}.md"
    path.write_text(text)
    return path


def _frontmatter(fields: dict[str, str]) -> str:
    return "---\n" + "".join(f"{k}: {v}\n" for k, v in fields.items()) + "---\n"


def _turtle(body: str) -> str:
    return f"```turtle\n{PREFIX}\n{body}\n```\n"


def _write(root: Path, name: str, body: str) -> Path:
    return _write_raw(root, name, f"# {name}\n\n{_turtle(body)}")


def _scan(root: Path) -> dict[Path, WorkItem]:
    items = WorkItemIndexer(KanbanConfig(), root).scan()
    return {item.file_path: item for item in items}


def _both_orders(n: int, own: str, other: str) -> str:
    return f"{other}\n{own}" if n % 2 else f"{own}\n{other}"


# --- 1. frontmatter-only items are indexed -----------------------------------------------


@pytest.mark.parametrize("spelling", ["expedition", "Expedition", "EXPEDITION"])
def test_frontmatter_only_item_is_indexed(tmp_path: Path, spelling: str) -> None:
    path = _write_raw(
        tmp_path,
        "EXP-1",
        _frontmatter({"id": "EXP-1", "type": spelling, "status": "ready"}) + "\n# Exp one\n",
    )
    items = _scan(tmp_path)
    assert path in items, f"frontmatter-only item (type: {spelling}) was not indexed"
    item = items[path]
    assert item.item_type is WorkItemType.EXPEDITION
    assert item.id == "EXP-1"
    assert item.status is WorkItemStatus.READY


@pytest.mark.parametrize(
    ("type_value", "want"),
    [
        ("feature", WorkItemType.FEATURE),
        ("bug", WorkItemType.BUG),
        ("task", WorkItemType.TASK),
        ("voyage", WorkItemType.VOYAGE),
        ("hypothesis", WorkItemType.HYPOTHESIS),
    ],
)
def test_frontmatter_only_types(tmp_path: Path, type_value: str, want: WorkItemType) -> None:
    path = _write_raw(
        tmp_path,
        "item-1",
        _frontmatter({"id": "X-7", "type": type_value, "status": "in_progress"}) + "\n# t\n",
    )
    items = _scan(tmp_path)
    assert path in items, f"type: {type_value} was not indexed"
    assert items[path].item_type is want
    assert items[path].id == "X-7"
    assert items[path].status is WorkItemStatus.IN_PROGRESS


def test_frontmatter_only_item_every_status(tmp_path: Path) -> None:
    paths = {
        s: _write_raw(
            tmp_path,
            f"EXP-{n}",
            _frontmatter({"id": f"EXP-{n}", "type": "expedition", "status": s.value}) + "\n# t\n",
        )
        for n, s in enumerate(STATUS_ORDER)
    }
    items = _scan(tmp_path)
    got = {s: items[p].status for s, p in paths.items() if p in items}
    assert got == {s: s for s in STATUS_ORDER}, got


def test_frontmatter_only_item_without_id_or_status(tmp_path: Path) -> None:
    path = _write_raw(tmp_path, "exp-3", _frontmatter({"type": "expedition"}) + "\n# t\n")
    items = _scan(tmp_path)
    assert path in items
    assert items[path].item_type is WorkItemType.EXPEDITION
    assert items[path].id == "EXP-3"
    assert items[path].status is WorkItemStatus.BACKLOG


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    return tmp_path


CREATED = [
    (WorkItemType.FEATURE, "FEAT"),
    (WorkItemType.BUG, "BUG"),
    (WorkItemType.TASK, "TASK"),
    (WorkItemType.EXPEDITION, "EXP"),
]


def test_service_created_items_are_indexed(repo: Path) -> None:
    svc = KanbanService(KanbanConfig(), repo)
    created = [svc.create_item(t, f"Hello {t.value}", description="Body") for t, _ in CREATED]

    items = _scan(repo)
    got = {it.file_path: (it.item_type, it.id, it.status) for it in items.values()}
    want = {c.file_path: (c.item_type, c.id, WorkItemStatus.BACKLOG) for c in created}
    assert got == want, f"service-created items: got {got}, want {want}"


def test_service_moved_item_is_indexed_with_its_status(repo: Path) -> None:
    svc = KanbanService(KanbanConfig(), repo)
    created = svc.create_item(WorkItemType.EXPEDITION, "Moved", description="Body")
    svc.move_item(
        created.id,
        WorkItemStatus.IN_PROGRESS,
        commit=False,
        validate_workflow=False,
        skip_wip_check=True,
        skip_gates=True,
    )

    items = _scan(repo)
    assert created.file_path in items, "a moved service item was not indexed"
    item = items[created.file_path]
    assert (item.item_type, item.id, item.status) == (
        WorkItemType.EXPEDITION,
        created.id,
        WorkItemStatus.IN_PROGRESS,
    )


def test_unknown_literal_type_is_not_a_work_item(tmp_path: Path) -> None:
    path = _write_raw(
        tmp_path,
        "WID-1",
        _frontmatter({"id": "WID-1", "type": "widget", "status": "ready"}) + "\n# t\n",
    )
    items = _scan(tmp_path)
    assert path not in items, items.get(path)


def test_unknown_literal_type_with_iri_type_is_indexed_by_the_iri(tmp_path: Path) -> None:
    path = _write_raw(
        tmp_path,
        "FEAT-2",
        _frontmatter({"id": "WID-1", "type": "widget", "status": "ready"})
        + "\n# t\n\n"
        + _turtle('<> a kb:Feature ;\n  kb:id "FEAT-2" ;\n  kb:status kb:review .'),
    )
    items = _scan(tmp_path)
    assert path in items
    item = items[path]
    assert (item.item_type, item.id, item.status) == (
        WorkItemType.FEATURE,
        "FEAT-2",
        WorkItemStatus.REVIEW,
    )


def test_blank_node_literal_type_is_not_a_work_item(tmp_path: Path) -> None:
    path = _write(tmp_path, "notes", '<> kb:related [ a "expedition" ; kb:status kb:done ] .')
    items = _scan(tmp_path)
    assert path not in items, items.get(path)


# --- 2. an IRI type wins over a literal one ----------------------------------------------


def test_iri_type_wins_over_literal_type(tmp_path: Path) -> None:
    """``feature`` precedes ``expedition`` in WorkItemType, so a literal that competed on
    enum order would win; the IRI type must win instead, with its subject's id/status."""
    paths = []
    for n in range(REPEATS):
        fm = _frontmatter({"id": f"FEAT-{n}", "type": "feature", "status": "done"})
        turtle = _turtle(f'<> a kb:Expedition ;\n  kb:id "EXP-{n}" ;\n  kb:status kb:ready .')
        paths.append(_write_raw(tmp_path, f"item-{n}", f"{fm}\n# t\n\n{turtle}"))

    items = _scan(tmp_path)
    got = {p.name: (items[p].item_type, items[p].id, items[p].status) for p in paths if p in items}
    want = {
        f"item-{n}.md": (WorkItemType.EXPEDITION, f"EXP-{n}", WorkItemStatus.READY)
        for n in range(REPEATS)
    }
    assert got == want, got


def test_iri_type_wins_over_literal_type_in_turtle(tmp_path: Path) -> None:
    paths = []
    for n in range(REPEATS):
        own = f'<> a kb:Expedition ;\n  kb:id "EXP-{n}" ;\n  kb:status kb:ready .'
        other = f'<lit> a "feature" ;\n  kb:id "FEAT-{n}" ;\n  kb:status kb:done .'
        paths.append(_write(tmp_path, f"item-{n}", _both_orders(n, own, other)))

    items = _scan(tmp_path)
    got = {p.name: (items[p].item_type, items[p].id, items[p].status) for p in paths if p in items}
    want = {
        f"item-{n}.md": (WorkItemType.EXPEDITION, f"EXP-{n}", WorkItemStatus.READY)
        for n in range(REPEATS)
    }
    assert got == want, got


# --- 3. several statuses: the smallest in WorkItemStatus order ---------------------------


def test_several_statuses_pick_the_smallest(tmp_path: Path) -> None:
    paths = []
    want = {}
    for n in range(REPEATS):
        # A varying subset of 2..6 statuses, rotated, so the smallest is written at
        # different positions (never always first or always last).
        k = 2 + n % 5
        start = n % len(STATUS_ORDER)
        subset = [STATUS_ORDER[(start + i) % len(STATUS_ORDER)] for i in range(k)]
        rot = n % k
        written = subset[rot:] + subset[:rot]
        if n % 3 == 0:
            written.reverse()
        statuses = ", ".join(f"kb:{s.value}" for s in written)
        body = f'<> a kb:Expedition ;\n  kb:id "EXP-{n}" ;\n  kb:status {statuses} .'
        paths.append(_write(tmp_path, f"EXP-{n}", body))
        want[f"EXP-{n}.md"] = min(subset, key=STATUS_ORDER.index)

    items = _scan(tmp_path)
    got = {p.name: items[p].status for p in paths if p in items}
    assert len(got) == REPEATS, got
    wrong = {k: (v, want[k]) for k, v in got.items() if v is not want[k]}
    assert not wrong, f"(got, want) — not the smallest status in enum order: {wrong}"


def test_several_statuses_ignore_unknown_values(tmp_path: Path) -> None:
    """An unknown status value is not a status; the smallest known one is picked."""
    paths = []
    for n in range(REPEATS):
        vals = ["kb:done", "kb:zzz_unknown", "kb:review"]
        rot = n % 3
        written = ", ".join(vals[rot:] + vals[:rot])
        body = f'<> a kb:Expedition ;\n  kb:id "EXP-{n}" ;\n  kb:status {written} .'
        paths.append(_write(tmp_path, f"EXP-{n}", body))

    items = _scan(tmp_path)
    got = {p.name: items[p].status for p in paths if p in items}
    assert len(got) == REPEATS, got
    wrong = {k: v for k, v in got.items() if v is not WorkItemStatus.REVIEW}
    assert not wrong, wrong


# --- 4. same IRI type on two subjects: the smallest subject IRI --------------------------


def test_same_type_tie_break_is_smallest_subject_iri(tmp_path: Path) -> None:
    """Which subject is smaller, and which is written first, both vary per file, so
    neither insertion order nor largest-IRI passes."""
    paths = []
    want = {}
    for n in range(REPEATS):
        small, large = f"urn:x:{n:03d}-a", f"urn:x:{n:03d}-b"
        # alternate which subject carries READY vs DONE, and which is written first
        small_status, large_status = ("ready", "done") if n % 4 < 2 else ("done", "ready")
        s = f'<{small}> a kb:Expedition ;\n  kb:id "SMALL-{n}" ;\n  kb:status kb:{small_status} .'
        lg = f'<{large}> a kb:Expedition ;\n  kb:id "LARGE-{n}" ;\n  kb:status kb:{large_status} .'
        paths.append(_write(tmp_path, f"item-{n}", _both_orders(n, s, lg)))
        want[f"item-{n}.md"] = (f"SMALL-{n}", WorkItemStatus(small_status))

    items = _scan(tmp_path)
    got = {p.name: (items[p].id, items[p].status) for p in paths if p in items}
    assert got == want, {k: v for k, v in got.items() if v != want[k]}
