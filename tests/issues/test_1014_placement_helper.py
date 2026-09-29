"""Issue #1014: one helper, ``_scanned_placement_dirs()``, for "the placement dirs
inside ``repo_root``" — the ones ``_scan`` walks — used by ``_scan``, ``_board_loads``
and ``_rev_roots``.

``_rev_roots`` (#986) took every placement dir, filtered only to the git top, so the
fetched-tree readers (``_ids_at``, ``_items_at``) read a placement dir the scan skips.
[steer] (bucket 1): the helper returns the placement dirs inside ``repo_root``, with
no ``exists()`` check; ``_scan`` adds its own ``exists()`` on the working tree, while
``_board_loads`` and ``_rev_roots`` read a fetched tree and skip it. For ``_rev_roots``
this is a behaviour change: a placement dir outside ``repo_root`` but inside the git
top is no longer read at a rev, matching the scan (#954).

Layout (test_963's ``outside``): ``.kanban/`` in ``proj/``, absolute ``paths.root``
``<top>/shared/``, scan path ``<top>/shared/active/``; the placement dirs
(``shared/expeditions/`` ...) lie outside ``repo_root``. Control (``inside``): the
root at ``<top>/proj/work/``, whose placement dirs the scan reads.

Allocation (the steer's consequence): an id held only by a file in a placement dir
outside ``repo_root`` is seen by no scan, so ``next-id`` no longer counts it —
exactly as it never counted a file in any other folder the board doesn't walk.
"""

from __future__ import annotations

import ast
import inspect
import shutil
import textwrap
from pathlib import Path

import pytest

from tests.issues.test_891_board_loads_pins import (  # noqa: F401  (_env: autouse)
    Layout,
    Repo,
    _env,
    build,
    scanned,
)
from tests.issues.test_963_board_loads_placement import PLACED, layout_for, single_config
from yurtle_kanban.service import KanbanService

READERS = ("_scan", "_board_loads", "_rev_roots")


def _calls(method: object) -> set[str]:
    tree = ast.parse(textwrap.dedent(inspect.getsource(method)))
    return {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }


@pytest.fixture
def outside(tmp_path: Path) -> Repo:
    (tmp_path / "o").mkdir()
    return build(tmp_path / "o", layout_for("outside", tmp_path / "o" / "A"))


@pytest.fixture
def inside(tmp_path: Path) -> Repo:
    (tmp_path / "i").mkdir()
    return build(tmp_path / "i", layout_for("inside", tmp_path / "i" / "A"))


def _ids(items: list) -> set[str]:
    return {item.id for item in items}


# --- 1. behaviour: the rev readers skip what the scan skips -------------------------


def test_rev_roots_leave_out_placement_dirs_outside_repo_root(outside: Repo) -> None:
    svc = outside.service()
    placed = (outside.top / PLACED["outside"]).parent
    assert placed in svc._placement_dirs(), "precondition"
    assert not placed.is_relative_to(svc.repo_root), "precondition"

    roots = svc._rev_roots()

    assert "shared/expeditions" not in roots, roots
    # no placement dir at all: every one lies outside repo_root in this layout
    assert not [r for r in roots if r.startswith("shared/") and r != "shared/active"], roots
    # the configured work path, outside repo_root too, is still read (#963)
    assert "shared/active" in roots, roots


def test_ids_at_does_not_list_an_unscanned_placement_file(outside: Repo) -> None:
    rel = PLACED["outside"]
    assert rel not in scanned(outside), "precondition: the scan does not load it"
    svc = outside.service()

    names, ids = svc._ids_at("HEAD")

    assert rel not in names, names
    assert (rel, "EXP-002") not in ids, ids
    assert "shared/active/EXP-001-active.md" in names, "control: the work path is read"


def test_items_at_agrees_with_the_scan(outside: Repo) -> None:
    svc = outside.service()
    scan_ids = _ids(svc.scan())
    assert scan_ids == {"EXP-001"}, "precondition"

    got = _ids(outside.service()._items_at("HEAD", None))

    assert "EXP-002" not in got, got
    assert got == scan_ids


def test_control_placement_dir_inside_repo_root_is_read(inside: Repo) -> None:
    svc = inside.service()
    rel = PLACED["inside"]
    assert rel in scanned(inside), "precondition: the scan loads it"

    assert "proj/work/expeditions" in svc._rev_roots()
    names, ids = svc._ids_at("HEAD")
    assert rel in names
    assert (rel, "EXP-002") in ids
    assert "EXP-002" in _ids(svc._items_at("HEAD", None))
    assert _ids(svc._items_at("HEAD", None)) == _ids(inside.service().scan())


def test_rev_readers_do_not_check_the_working_tree(inside: Repo) -> None:
    """A placement dir that exists only in the fetched tree (removed from the working
    tree) is still a rev root, and `_board_loads` still loads its file: no `exists()`
    outside `_scan` (the steer)."""
    shutil.rmtree(inside.top / "proj" / "work" / "expeditions")
    svc = inside.service()

    assert "proj/work/expeditions" in svc._rev_roots()
    assert svc._board_loads(PLACED["inside"]) is True
    assert "EXP-002" in _ids(svc._items_at("HEAD", None))


# --- 2. structural: one helper ------------------------------------------------------


def test_helper_exists() -> None:
    assert callable(getattr(KanbanService, "_scanned_placement_dirs", None))


@pytest.mark.parametrize("reader", READERS)
def test_readers_call_the_helper_not_placement_dirs(reader: str) -> None:
    called = _calls(getattr(KanbanService, reader))
    assert "_scanned_placement_dirs" in called, reader
    assert "_placement_dirs" not in called, reader


def test_helper_is_placement_dirs_inside_repo_root_without_exists(
    outside: Repo, inside: Repo
) -> None:
    for repo in (outside, inside):
        svc = repo.service()
        want = {d for d in svc._placement_dirs() if d.is_relative_to(svc.repo_root)}
        assert set(svc._scanned_placement_dirs()) == want, repo.layout.name
    # outside: none lies inside repo_root
    assert set(outside.service()._scanned_placement_dirs()) == set()
    # inside: all of them, including folders the working tree does not have
    svc = inside.service()
    missing = {d for d in svc._placement_dirs() if not d.exists()}
    assert missing, "precondition: some placement dir is not in the working tree"
    assert missing <= set(svc._scanned_placement_dirs())


# --- 3. allocation: an id only in an outside placement dir is not counted -----------


def _alloc_layout(kind: str, top: Path) -> Layout:
    root = top / ("shared" if kind == "outside" else "proj/work")
    base = root.relative_to(top).as_posix()
    return Layout(
        f"alloc-{kind}",
        "proj",
        single_config(root, root / "active"),
        {
            f"{base}/active/EXP-001-active.md": True,
            f"{base}/expeditions/EXP-007-placed.md": kind == "inside",
        },
        ignored="",
        loaded=f"{base}/active/EXP-001-active.md",
    )


@pytest.mark.parametrize("kind, want", [("outside", 2), ("inside", 8)])
def test_next_id_counts_only_what_the_scan_walks(
    tmp_path: Path, kind: str, want: int
) -> None:
    """Outside repo_root, EXP-007 is seen by no scan, so the next id is EXP-002 (as
    for any file outside the board's folders); inside, it is counted: EXP-008."""
    repo = build(tmp_path, _alloc_layout(kind, tmp_path / "A"))
    svc = repo.service()
    assert _ids(svc.scan()) == ({"EXP-001", "EXP-007"} if kind == "inside" else {"EXP-001"})

    assert svc._next_id_number_at("HEAD", "EXP") == want

    got = repo.service().allocate_next_id("EXP", sync_remote=False, commit_allocation=False)
    assert got["success"], got
    assert got["id"] == f"EXP-{want:03d}", got
