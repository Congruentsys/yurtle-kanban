# ruff: noqa: F811  (fixtures are imported, then re-bound as parameters)
"""Issue #754, round 2 — ``--push`` refuses a parent whose ID is duplicated on origin.

The review of PR #778 (round 1): the local child creates, ``epic add``, ``voyage add``
and ``create --items`` refuse a duplicated parent or item, but with ``--push`` the link
is built against ORIGIN's copy in ``_parent_link_blob``, and ``_holder_at(base, id)``
returns only the first file holding the ID. The only duplicate check is the CLI's
``_refuse_duplicate_parent``, which reads the LOCAL board. So:

(1) Origin-only duplicate: a rival clone pushes a second file holding the parent's ID
    (exact case, and case-folded); clone A is one commit behind and does not have the
    copy. ``<kind> create … --push`` must be refused: non-zero exit, no traceback, the
    message says "is on more than one board" and "a parent link" and names both
    origin files; nothing pushed, no local commit, no child file, no
    ``_ID_ALLOCATIONS.json`` change.
(2) Non-CLI caller: the parent is duplicated on both the local board and origin;
    ``service.create_item_and_push(..., parent=<id>)`` with a remote returns
    ``success: False`` with that message (a raised ValueError is also accepted as a
    refusal), and nothing is written or pushed.
(3) The board is outside the git repo (the #174/#750 ``_outside_repo`` branch): a
    non-CLI ``create_item_and_push(parent=<duplicated>)`` is refused BEFORE the child
    file is written — no orphan child.

Controls (green before and after): a non-duplicated parent via ``--push`` still links
in one commit, also when A is a commit behind an unrelated rival push; the service
call shape used in (2) links a non-duplicated parent.

Harnesses: the #585/#645/#764 real-git World (bare origin, clone A, rival clone B),
``b_push``, ``seed_on_origin``; round 1's ``_dup_parent`` / ``_tree``.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.issues.test_585_create_push_loop import World, git, porcelain
from tests.issues.test_590_next_id_and_hdd_ids import b_push
from tests.issues.test_603_push_failure_messages import invoke
from tests.issues.test_645_parent_in_cas import (
    KINDS,
    Kind,
    assert_one_commit_with_link,
    seed_on_origin,
)
from tests.issues.test_674_parent_edges import (  # noqa: F401  (fixtures)
    _clean_theme_cache,
    flat,
    world,
)
from tests.issues.test_754_duplicate_parent_and_epic import (
    ALLOC,
    PARENT_ID,
    PARENT_NEEDLES,
    _dup_parent,
    _has,
    _service,
    _tree,
)
from yurtle_kanban import config as config_mod
from yurtle_kanban.models import WorkItemType
from yurtle_kanban.service import KanbanService

CHILD_TYPE = {
    "literature": WorkItemType.LITERATURE,
    "hypothesis": WorkItemType.HYPOTHESIS,
    "experiment": WorkItemType.EXPERIMENT,
}
CHILD_ID = {
    "literature": "LIT-754",
    "hypothesis": "H130.9",
    "experiment": "EXPR-130.9",
}


def _copy_rel(kind: Kind, *, case_folded: bool) -> str:
    """Where the rival's copy of the kind's parent goes: another directory of the same
    board (the ID, not the path, collides)."""
    parent_id = PARENT_ID[kind.name]
    other = "measures" if "/measures/" not in kind.parent_rel else "papers"
    name = parent_id.lower() if case_folded else parent_id
    return f"research/{other}/{name}-copy.md"


def _dup_on_origin_only(world: World, monkeypatch, kind: Kind, *, case_folded: bool) -> str:
    """Seed the parent on origin (A has it), then rival B pushes a second file holding
    the same ID. A is left one commit behind: its board has no copy. Returns the
    copy's repo-relative path."""
    seed_on_origin(world, monkeypatch, kind)
    parent_id = PARENT_ID[kind.name]
    text = (world.a / kind.parent_rel).read_text()
    assert f"id: {parent_id}" in text, text
    if case_folded:
        text = text.replace(f"id: {parent_id}", f"id: {parent_id.lower()}", 1)
    rel = _copy_rel(kind, case_folded=case_folded)
    b_push(world, {rel: text})
    assert rel in world.remote_files(), world.remote_files()
    assert not (world.a / rel).exists(), "A must be behind: it has the rival's copy"
    # sanity: A's own board does not see a duplicate (the local pre-check can't)
    assert parent_id.upper() not in {k.upper() for k in _service(world).duplicate_ids}
    config_mod._theme_cache.clear()
    return rel


def _snapshot(world: World) -> tuple[dict[str, bytes], str, str]:
    return _tree(world), git(world.a, "rev-parse", "HEAD"), world.remote_sha()


def _assert_nothing_written(world: World, snap: tuple[dict[str, bytes], str, str]) -> None:
    before, head, remote = snap
    after = _tree(world)
    new = sorted(set(after) - set(before))
    assert not new, f"files written by a refused create: {new}"
    assert after.get(ALLOC) == before.get(ALLOC), "the refused create allocated an ID"
    assert after == before, "a refused create changed a file"
    assert git(world.a, "rev-parse", "HEAD") == head, "a refused create committed"
    assert world.remote_sha() == remote, "a refused create pushed"
    assert porcelain(world.a) == [], porcelain(world.a)


def _assert_message(msg: str, kind: Kind, dup_rel: str) -> None:
    for needle in (*PARENT_NEEDLES, kind.parent_rel, dup_rel):
        assert _has(needle, msg), f"{needle!r} not in:\n{msg}"
    assert PARENT_ID[kind.name].upper() in msg.upper(), msg


FOLDS = [False, True]
FOLD_IDS = ["exact-case", "case-folded"]


# --- (1) CLI --push: a duplicate only origin has --------------------------------------


@pytest.mark.parametrize("case_folded", FOLDS, ids=FOLD_IDS)
@pytest.mark.parametrize("kind", KINDS, ids=repr)
def test_push_refuses_parent_duplicated_on_origin_only(
    world, monkeypatch, kind: Kind, case_folded: bool
) -> None:
    dup_rel = _dup_on_origin_only(world, monkeypatch, kind, case_folded=case_folded)
    snap = _snapshot(world)

    result = invoke(world, monkeypatch, list(kind.argv))
    out = flat(result)

    assert result.exit_code != 0, (
        f"--push linked ONE copy of a parent duplicated on origin:\n{out}"
    )
    assert "Traceback" not in out, out
    _assert_message(out, kind, dup_rel)
    _assert_nothing_written(world, snap)


# --- (2) non-CLI caller, with a remote ------------------------------------------------


def _call(svc: KanbanService, kind: Kind) -> dict[str, Any] | ValueError:
    try:
        return svc.create_item_and_push(
            item_type=CHILD_TYPE[kind.name],
            title=f"Child 754 {kind.name}",
            item_id=CHILD_ID[kind.name],
            parent=PARENT_ID[kind.name],
        )
    except ValueError as e:
        return e


@pytest.mark.parametrize("case_folded", FOLDS, ids=FOLD_IDS)
@pytest.mark.parametrize("kind", KINDS, ids=repr)
def test_service_push_refuses_duplicated_parent(
    world, monkeypatch, kind: Kind, case_folded: bool
) -> None:
    """Duplicated on both boards; the service is called directly (no CLI pre-check).
    Preferred shape: `success: False` with the message (a ValueError is accepted)."""
    dup_rel = _dup_parent(world, monkeypatch, kind, case_folded=case_folded)
    snap = _snapshot(world)

    got = _call(_service(world), kind)

    if isinstance(got, ValueError):
        msg = " ".join(str(got).split())
    else:
        assert got["success"] is False, (
            f"create_item_and_push linked one copy of a duplicated parent: {got}"
        )
        msg = " ".join(str(got["message"]).split())
    _assert_message(msg, kind, dup_rel)
    _assert_nothing_written(world, snap)


# --- (3) the board is outside the repo: refused before the child is written ----------


@pytest.mark.parametrize("case_folded", FOLDS, ids=FOLD_IDS)
@pytest.mark.parametrize("kind", KINDS, ids=repr)
def test_service_outside_repo_refuses_before_child_written(
    world, monkeypatch, kind: Kind, case_folded: bool
) -> None:
    dup_rel = _dup_parent(world, monkeypatch, kind, case_folded=case_folded)
    monkeypatch.setattr(KanbanService, "_outside_repo", lambda self, *paths: True)
    snap = _snapshot(world)

    got = _call(_service(world), kind)

    new = sorted(set(_tree(world)) - set(snap[0]))
    assert not new, f"an orphan child was left by the refused create: {new} ({got!r})"
    if isinstance(got, ValueError):
        msg = " ".join(str(got).split())
    else:
        assert got["success"] is False, f"the outside-repo create was not refused: {got}"
        msg = " ".join(str(got["message"]).split())
    _assert_message(msg, kind, dup_rel)
    _assert_nothing_written(world, snap)


# --- controls (green before and after) ------------------------------------------------


@pytest.mark.parametrize("kind", KINDS, ids=repr)
def test_control_push_non_duplicated_parent_links_in_one_commit(
    world, monkeypatch, kind: Kind
) -> None:
    seed_on_origin(world, monkeypatch, kind)
    base = world.remote_sha()
    result = invoke(world, monkeypatch, list(kind.argv))
    assert result.exit_code == 0, flat(result)
    assert_one_commit_with_link(world, kind, base)


@pytest.mark.parametrize("kind", KINDS, ids=repr)
def test_control_push_behind_unrelated_rival_still_links(
    world, monkeypatch, kind: Kind
) -> None:
    """A one commit behind an unrelated rival push (the (1) setup minus the copy)."""
    seed_on_origin(world, monkeypatch, kind)
    b_push(world, {"research/measures/M-900-unrelated.md": (
        '---\nid: M-900\ntitle: "Unrelated"\ntype: measure\nstatus: backlog\n---\n\n# U\n'
    )})
    base = world.remote_sha()
    result = invoke(world, monkeypatch, list(kind.argv))
    assert result.exit_code == 0, flat(result)
    assert_one_commit_with_link(world, kind, base)


@pytest.mark.parametrize("kind", KINDS, ids=repr)
def test_control_service_push_non_duplicated_parent_links(
    world, monkeypatch, kind: Kind
) -> None:
    """The call shape of (2) links a parent that is not duplicated."""
    seed_on_origin(world, monkeypatch, kind)
    base = world.remote_sha()
    got = _call(_service(world), kind)
    assert not isinstance(got, ValueError), got
    assert got["success"] is True and got["parent_linked"] is True, got
    assert_one_commit_with_link(world, kind, base)
