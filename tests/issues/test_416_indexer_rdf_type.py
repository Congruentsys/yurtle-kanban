"""#416: the indexer's type comes from ``rdf:type`` only; id/status from the typed subject.

Follow-up from the review of PR #414 (#407).

``WorkItemIndexer._parse_file`` (reached through the public ``WorkItemIndexer.scan()``)
decided the type from *any* ``(?, ?, kb:<Type>)`` triple, so ``kb:related kb:Feature``
made an Expedition a FEATURE and ``<> kb:mentions kb:Expedition .`` alone made a work
item. Among IRI subjects, the first ``kb:id`` / ``kb:status`` triple won, so a second
IRI subject (``<other> kb:status kb:done``) could decide the status.

Decided behaviour:

1. The type comes only from ``rdf:type`` (``a kb:X``) on a non-blank-node subject;
   ``kb:related kb:Feature`` / ``kb:mentions kb:Expedition`` never decide it.
2. A file whose only link to a type IRI is a non-``rdf:type`` predicate is not a work
   item.
3. The item's subject is the non-blank subject carrying the chosen ``rdf:type``; its
   ``kb:id`` and ``kb:status`` are read from that subject only. Another IRI subject's
   ``kb:id`` / ``kb:status`` never decide them: no own status → backlog, no own id →
   filename.
4. With several typed non-blank subjects, the type follows ``WorkItemType`` enum order
   and the chosen subject is the one whose type won (its id and status are used).

Controls: single-subject files index as before; the #407 tests stay green.

Graph iteration order is not something to rely on, so each case is repeated over many
files, with the competing subject both before and after the item's own triples.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.indexer import WorkItemIndexer
from yurtle_kanban.models import WorkItem, WorkItemStatus, WorkItemType

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


def _both_orders(n: int, own: str, other: str) -> str:
    """Put the competing subject's triples before the item's own on odd n."""
    return f"{other}\n{own}" if n % 2 else f"{own}\n{other}"


# --- 1. type only from rdf:type ----------------------------------------------------------

NON_TYPE_PREDICATES = ["kb:related", "kb:mentions", "kb:blocks", "kb:dependsOn"]


@pytest.mark.parametrize("pred", NON_TYPE_PREDICATES)
def test_non_rdf_type_link_never_decides_the_type(tmp_path: Path, pred: str) -> None:
    """kb:Feature precedes kb:Expedition in WorkItemType, so a linked Feature would win."""
    path = _write(
        tmp_path,
        "EXP-5",
        f'<> a kb:Expedition ;\n  kb:id "EXP-5" ;\n  kb:status kb:ready ;\n  {pred} kb:Feature .',
    )
    items = _scan(tmp_path)
    assert path in items
    assert items[path].item_type is WorkItemType.EXPEDITION
    assert items[path].id == "EXP-5"
    assert items[path].status is WorkItemStatus.READY


def test_other_subject_linking_a_type_iri_never_decides_the_type(tmp_path: Path) -> None:
    paths = []
    for n in range(REPEATS):
        own = f'<> a kb:Expedition ;\n  kb:id "EXP-{n}" ;\n  kb:status kb:ready .'
        other = "<other> kb:related kb:Feature ; kb:status kb:done ."
        paths.append(_write(tmp_path, f"EXP-{n}", _both_orders(n, own, other)))

    items = _scan(tmp_path)
    got = {p.name: (items[p].item_type, items[p].status) for p in paths if p in items}
    assert len(got) == REPEATS, got
    wrong = {k: v for k, v in got.items() if v != (WorkItemType.EXPEDITION, WorkItemStatus.READY)}
    assert not wrong, f"a non-rdf:type link / other subject decided type or status: {wrong}"


# --- 2. no rdf:type → not a work item ----------------------------------------------------


@pytest.mark.parametrize("pred", NON_TYPE_PREDICATES)
def test_non_rdf_type_link_only_is_not_a_work_item(tmp_path: Path, pred: str) -> None:
    path = _write(tmp_path, "notes", f"<> {pred} kb:Expedition .")
    items = _scan(tmp_path)
    assert path not in items, items.get(path)


def test_non_rdf_type_link_with_id_and_status_is_not_a_work_item(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "notes",
        '<> kb:mentions kb:Expedition ;\n  kb:id "EXP-9" ;\n  kb:status kb:done .',
    )
    items = _scan(tmp_path)
    assert path not in items, items.get(path)


# --- 3. id / status from the typed subject only ------------------------------------------


def test_other_iri_subject_status_never_decides_the_status(tmp_path: Path) -> None:
    paths = []
    for n in range(REPEATS):
        own = f'<> a kb:Expedition ;\n  kb:id "EXP-{n}" ;\n  kb:status kb:in_progress .'
        other = "<other> kb:status kb:done ."
        paths.append(_write(tmp_path, f"EXP-{n}", _both_orders(n, own, other)))

    items = _scan(tmp_path)
    got = {p.name: items[p].status for p in paths if p in items}
    assert len(got) == REPEATS, got
    wrong = {k: v for k, v in got.items() if v is not WorkItemStatus.IN_PROGRESS}
    assert not wrong, f"another IRI subject's kb:status decided the status: {wrong}"


def test_other_iri_subject_status_only_defaults_to_backlog(tmp_path: Path) -> None:
    paths = []
    for n in range(REPEATS):
        own = f'<> a kb:Expedition ;\n  kb:id "EXP-{n}" .'
        other = "<other> kb:status kb:done ."
        paths.append(_write(tmp_path, f"EXP-{n}", _both_orders(n, own, other)))

    items = _scan(tmp_path)
    got = {p.name: items[p].status for p in paths if p in items}
    assert len(got) == REPEATS, got
    wrong = {k: v for k, v in got.items() if v is not WorkItemStatus.BACKLOG}
    assert not wrong, f"another IRI subject's kb:status stood in for the item's: {wrong}"


def test_other_iri_subject_id_never_decides_the_id(tmp_path: Path) -> None:
    paths = []
    for n in range(REPEATS):
        own = f'<> a kb:Expedition ;\n  kb:id "EXP-{n}" ;\n  kb:status kb:ready .'
        other = f'<other> kb:id "BOGUS-{n}" .'
        paths.append(_write(tmp_path, f"exp-file-{n}", _both_orders(n, own, other)))

    items = _scan(tmp_path)
    got = {p.name: items[p].id for p in paths if p in items}
    assert got == {f"exp-file-{n}.md": f"EXP-{n}" for n in range(REPEATS)}, got


def test_other_iri_subject_id_only_falls_back_to_filename(tmp_path: Path) -> None:
    paths = []
    for n in range(REPEATS):
        own = "<> a kb:Expedition ;\n  kb:status kb:ready ."
        other = f'<other> kb:id "BOGUS-{n}" .'
        paths.append(_write(tmp_path, f"exp-{n}", _both_orders(n, own, other)))

    items = _scan(tmp_path)
    got = {p.name: items[p].id for p in paths if p in items}
    assert got == {f"exp-{n}.md": f"EXP-{n}" for n in range(REPEATS)}, got


# --- 4. several typed subjects: enum order, subject follows the winning type -------------


def test_several_typed_subjects_type_follows_enum_order(tmp_path: Path) -> None:
    """Feature precedes Expedition in WorkItemType; the Feature subject is the item."""
    paths = []
    for n in range(REPEATS):
        exp = f'<> a kb:Expedition ;\n  kb:id "EXP-{n}" ;\n  kb:status kb:ready .'
        feat = f'<feat> a kb:Feature ;\n  kb:id "FEAT-{n}" ;\n  kb:status kb:done .'
        paths.append(_write(tmp_path, f"item-{n}", _both_orders(n, exp, feat)))

    items = _scan(tmp_path)
    got = {p.name: (items[p].item_type, items[p].id, items[p].status) for p in paths if p in items}
    assert len(got) == REPEATS, got
    want = {
        f"item-{n}.md": (WorkItemType.FEATURE, f"FEAT-{n}", WorkItemStatus.DONE)
        for n in range(REPEATS)
    }
    wrong = {k: v for k, v in got.items() if v != want[k]}
    assert not wrong, f"id/status not taken from the subject whose type won: {wrong}"


def test_winning_subject_without_status_defaults_to_backlog(tmp_path: Path) -> None:
    """The losing typed subject's status/id never stand in for the winner's."""
    paths = []
    for n in range(REPEATS):
        exp = f'<> a kb:Expedition ;\n  kb:id "EXP-{n}" ;\n  kb:status kb:ready .'
        feat = "<feat> a kb:Feature ."
        paths.append(_write(tmp_path, f"item-{n}", _both_orders(n, exp, feat)))

    items = _scan(tmp_path)
    got = {p.name: (items[p].item_type, items[p].id, items[p].status) for p in paths if p in items}
    assert len(got) == REPEATS, got
    want = {
        f"item-{n}.md": (WorkItemType.FEATURE, f"ITEM-{n}", WorkItemStatus.BACKLOG)
        for n in range(REPEATS)
    }
    wrong = {k: v for k, v in got.items() if v != want[k]}
    assert not wrong, f"the losing subject's id/status decided the item's: {wrong}"


def test_same_type_subjects_pick_one_subject_coherently(tmp_path: Path) -> None:
    """Two subjects of the same type: whichever is chosen, id and status both come from it."""
    paths = []
    for n in range(REPEATS):
        a = f'<a> a kb:Expedition ;\n  kb:id "A-{n}" ;\n  kb:status kb:ready .'
        b = f'<b> a kb:Expedition ;\n  kb:id "B-{n}" ;\n  kb:status kb:done .'
        paths.append(_write(tmp_path, f"item-{n}", _both_orders(n, a, b)))

    items = _scan(tmp_path)
    got = {p.name: (items[p].id, items[p].status) for p in paths if p in items}
    assert len(got) == REPEATS, got
    coherent = {("A", WorkItemStatus.READY), ("B", WorkItemStatus.DONE)}
    wrong = {k: v for k, v in got.items() if (v[0].split("-")[0], v[1]) not in coherent}
    assert not wrong, f"id and status came from different subjects: {wrong}"


# --- controls ------------------------------------------------------------------------------


def test_control_single_subject(tmp_path: Path) -> None:
    paths = [
        _write(
            tmp_path,
            f"FEAT-{n}",
            f'<> a kb:Feature ;\n  kb:id "FEAT-{n}" ;\n  kb:status kb:review ;\n'
            "  kb:related <https://example.org/x> .",
        )
        for n in range(3)
    ]
    items = _scan(tmp_path)
    for n, p in enumerate(paths):
        assert items[p].id == f"FEAT-{n}"
        assert items[p].status is WorkItemStatus.REVIEW
        assert items[p].item_type is WorkItemType.FEATURE


def test_control_single_subject_defaults(tmp_path: Path) -> None:
    path = _write(tmp_path, "exp-7", "<> a kb:Expedition .")
    items = _scan(tmp_path)
    assert items[path].item_type is WorkItemType.EXPEDITION
    assert items[path].status is WorkItemStatus.BACKLOG
    assert items[path].id == "EXP-7"
