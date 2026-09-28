# ruff: noqa: F811  (fixtures are imported, then re-bound as parameters)
"""Issue #870 — the folder-twin guard (#834, #869): the right message when origin already
holds BOTH twins, and the allocation-only push is guarded too.

Found in the PR #863 (#834) review:

- If origin already has both ``Research/`` and ``research/``, a ``create --push`` into
  ``research/…`` is refused with a message saying it "would add a folder that differs
  only in case". That is false: the folder is already there, twice.
- ``next-id`` with a remote (``allocate_next_id`` → ``_cas_on_default_branch`` with
  ``_allocation_blob``) pushes ``.kanban/_ID_ALLOCATIONS.json``, which can be new on
  origin, and nothing checks its folders against case twins.

Decided fix ([steer] on #870, bucket 1):

1. When origin already holds both case twins of a folder, the refusal says so
   ("origin already has case-twin folders X and Y: …"), naming both, and does not say
   "would add a folder". Nothing is pushed.
2. The allocation push runs the same #834/#869 folder-twin guard (``_twin_key``) on the
   allocation file's folders: origin holding ``.Kanban/`` or ``.KANBAN/`` (but not
   ``.kanban/``) refuses, naming the twin; nothing is pushed.

APFS cannot hold both spellings in one checkout, so origin's tree is rewritten with git
plumbing in the bare remote (#834's ``_respell_on_origin``), never through a work tree.

RED today:
- (1) both twins on origin: service API (explicit id, auto id) and CLI — the message
  says "would add a folder" and does not name both.
- (2) ``next-id EXP`` (CLI) and ``allocate_next_id`` with a remote while origin spells
  ``.kanban/`` as ``.Kanban/`` or ``.KANBAN/``: allocated and pushed today.

Controls (green before and after): ``.kanban/`` spelled identically on origin allocates;
the #834 / #869 suites are unchanged.
"""

from __future__ import annotations

import json

import pytest

from tests.issues.test_585_create_push_loop import World, git, output_of
from tests.issues.test_603_push_failure_messages import invoke
from tests.issues.test_674_parent_edges import (  # noqa: F401  (fixtures)
    _clean_theme_cache,
    world,
)
from tests.issues.test_788_no_overwrite import EXPR_099, _board_with
from tests.issues.test_834_folder_case_guard import (
    _remote_dirs,
    _respell_on_origin,
    _twin_dirs,
)
from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.models import InputRefused, WorkItemType
from yurtle_kanban.service import KanbanService

NEW_ID = "EXPR-042"  # nothing on origin holds it; "b" builds EXPR-042-b.md
ALLOC = ".kanban/_ID_ALLOCATIONS.json"
EXPR_097 = EXPR_099.replace("099", "097").replace("Ninety-nine", "Ninety-seven")
WRONG = "would add a folder"


def _flat(text: str) -> str:
    return " ".join(text.split())


# --- (1) origin already holds BOTH Research/ and research/ ------------------------------


def _both_twins_board(world: World) -> KanbanService:
    """The lower-case HDD board (config says research/…) with an item on origin, then
    a rival commit that ADDS ``Research/…`` beside it: origin holds both spellings."""
    svc = _board_with(world, {"research/experiments/EXPR-001-a.md": EXPR_099})
    # respell nothing (no path starts with the sentinel); only add the twin
    _respell_on_origin(
        world,
        "no-such-folder/",
        "no-such-folder/",
        {"Research/experiments/EXPR-097-z.md": EXPR_097},
    )
    git(world.a, "fetch", "-q", "origin")
    dirs = _remote_dirs(world)
    assert {"Research", "research"} <= dirs, sorted(dirs)
    assert "research" in _twin_dirs(dirs), "fixture: origin should hold both twins"
    return svc


def _assert_both_named(message: str) -> None:
    flat = _flat(message)
    assert WRONG not in flat, f"says it would ADD a folder, but both exist: {flat!r}"
    assert "Research/" in flat and "research/" in flat, (
        f"the refusal does not name both twin folders: {flat!r}"
    )
    assert "twin" in flat.lower(), f"the refusal does not say case-twin: {flat!r}"


def _assert_nothing_pushed(world: World, base: str, before: list[str]) -> None:
    assert world.remote_sha() == base, "something was pushed"
    assert world.remote_files() == before


def test_both_twins_on_origin_explicit_id_message(world) -> None:
    svc = _both_twins_board(world)
    assert svc._holder_at("origin/main", NEW_ID) is None  # not the id guard
    before, base = world.remote_files(), world.remote_sha()
    result = svc.create_item_and_push(WorkItemType.EXPERIMENT, "b", item_id=NEW_ID)
    _assert_nothing_pushed(world, base, before)
    assert result["success"] is False, result
    _assert_both_named(result.get("message", ""))


def test_both_twins_on_origin_auto_allocated_message(world) -> None:
    svc = _both_twins_board(world)
    before, base = world.remote_files(), world.remote_sha()
    result = svc.create_item_and_push(WorkItemType.EXPERIMENT, "b")
    _assert_nothing_pushed(world, base, before)
    assert result["success"] is False, result
    _assert_both_named(result.get("message", ""))


def test_cli_both_twins_on_origin_message(world, monkeypatch) -> None:
    _both_twins_board(world)
    before, base = world.remote_files(), world.remote_sha()
    result = invoke(world, monkeypatch, ["experiment", "create", "--title", "b", "--push"])
    out = output_of(result)
    _assert_nothing_pushed(world, base, before)
    assert result.exit_code != 0, out
    _assert_both_named(out)


# --- (2) the allocation push: origin spells .kanban/ in another case --------------------


def _service(world: World) -> KanbanService:
    return KanbanService(KanbanConfig.load(world.a / ".kanban" / "config.yaml"), world.a)


def _alloc_twin_origin(world: World, twin: str, alloc: bool) -> None:
    """Origin holds ``.kanban/`` ONLY as `twin` (same blobs), optionally with an
    allocation file under it; A's own checkout keeps ``.kanban/``."""
    earlier = [{"id": "EXP-001", "prefix": "EXP", "number": 1, "allocated_by": "earlier"}]
    extra = {f"{twin}_ID_ALLOCATIONS.json": json.dumps(earlier, indent=2)} if alloc else {}
    _respell_on_origin(world, ".kanban/", twin, extra)
    git(world.a, "fetch", "-q", "origin")
    dirs = _remote_dirs(world)
    assert twin.rstrip("/") in dirs and ".kanban" not in dirs, sorted(dirs)
    assert not _twin_dirs(dirs), "fixture: origin itself holds twin folders"


TWINS = [(".Kanban/", False), (".KANBAN/", True)]
TWIN_IDS = [".Kanban", ".KANBAN-with-alloc"]


@pytest.mark.parametrize(("twin", "alloc"), TWINS, ids=TWIN_IDS)
def test_next_id_cli_refuses_kanban_folder_twin(world, monkeypatch, twin, alloc) -> None:
    _alloc_twin_origin(world, twin, alloc)
    before, base = world.remote_files(), world.remote_sha()
    result = invoke(world, monkeypatch, ["next-id", "EXP", "--json"])
    out = output_of(result)
    assert world.remote_sha() == base, (
        f"allocation pushed; origin twin folders {_twin_dirs(_remote_dirs(world))}\n{out}"
    )
    assert world.remote_files() == before, out
    assert result.exit_code != 0, out
    assert twin in _flat(out), f"the refusal does not name {twin}: {_flat(out)!r}"


@pytest.mark.parametrize(("twin", "alloc"), TWINS, ids=TWIN_IDS)
def test_allocate_next_id_refuses_kanban_folder_twin(world, monkeypatch, twin, alloc) -> None:
    _alloc_twin_origin(world, twin, alloc)
    before, base = world.remote_files(), world.remote_sha()
    monkeypatch.chdir(world.a)
    svc = _service(world)
    assert svc._has_remote()
    try:
        result = svc.allocate_next_id("EXP", sync_remote=True, commit_allocation=True)
    except InputRefused as e:
        result = {"success": False, "message": str(e)}
    assert world.remote_sha() == base, (
        f"allocation pushed ({result}); origin twin folders {_twin_dirs(_remote_dirs(world))}"
    )
    assert world.remote_files() == before
    assert result.get("success") is False, result
    message = _flat(str(result.get("message", "")))
    assert twin in message, f"the refusal does not name {twin}: {message!r}"


# --- controls ------------------------------------------------------------------------


def test_control_next_id_same_spelling_allocates(world, monkeypatch) -> None:
    """``.kanban/`` on origin exactly as A spells it: no twin, the id is allocated."""
    assert ".kanban" in _remote_dirs(world)
    base = world.remote_sha()
    result = invoke(world, monkeypatch, ["next-id", "EXP", "--json"])
    out = output_of(result)
    assert result.exit_code == 0, out
    assert world.remote_sha() != base, f"nothing was pushed: {out}"
    assert ALLOC in world.remote_files()
    assert not _twin_dirs(_remote_dirs(world))


def test_control_allocate_next_id_same_spelling_allocates(world, monkeypatch) -> None:
    monkeypatch.chdir(world.a)
    result = _service(world).allocate_next_id("EXP", sync_remote=True, commit_allocation=True)
    assert result["success"] is True, result
    assert result["id"], result
    records = json.loads(world.remote_show(ALLOC))
    assert any(r.get("id") == result["id"] for r in records), records
    assert not _twin_dirs(_remote_dirs(world))
