# ruff: noqa: F811  (fixtures are imported, then re-bound as parameters)
"""Issue #788, round 2 — a ``--push`` create never replaces another file on origin.

Round-1 review of PR #810: ``_holder_at`` now skips filename matches for a file that
carries an ``id:``. That is right for finding a parent, but ``_holder_at`` is also the
only guard in ``create_item_and_push``'s explicit-ID check (``build`` in
``_create_on_default_branch``). With origin holding ``EXP-042-x.md`` whose ``id:`` is
EXP-043, ``create_item_and_push(..., "x", item_id="EXP-042")`` passes the guard,
``_new_item`` builds the same path ``EXP-042-x.md``, and the CAS commit silently
replaces EXP-043's file on origin.

Decided fix: on the push path a create is refused when the new item's own file path
already exists on origin, whatever the IDs say. The message names the existing file;
nothing is created or pushed. The explicit-ID holder check stays.

RED today:
- (1) service API: ``item_id="EXP-042"``, title "x", onto origin's ``EXP-042-x.md``
  (``id: EXP-043``) — succeeds and overwrites.
- (1b) CLI: ``measure create x --id M-001 --push`` onto origin's ``M-001-x.md``
  (``id: M-002``) — same hole through the flag.
- (2) auto-allocated path: the allocator counts filename stems (``_next_id_number_at``),
  so a real board cannot make it pick the colliding id; the test forces the pick with
  ``_next_id_at`` returning EXPR-001 onto origin's ``EXPR-001-a.md`` (``id: EXPR-099``,
  no allocation record). The path guard must hold whatever the allocator says.

Controls (green before and after): a create whose path is free on origin lands, with
an explicit id and without one; the explicit-id holder check still refuses.
"""

from __future__ import annotations

from tests.issues.test_585_create_push_loop import World, git, output_of
from tests.issues.test_590_next_id_and_hdd_ids import b_push
from tests.issues.test_603_push_failure_messages import invoke
from tests.issues.test_645_parent_in_cas import hdd
from tests.issues.test_674_parent_edges import (  # noqa: F401  (fixtures)
    _clean_theme_cache,
    world,
)
from tests.issues.test_754_duplicate_parent_and_epic import _service
from yurtle_kanban.models import WorkItemType
from yurtle_kanban.service import KanbanService

EXP_REL = "research/experiments/EXP-042-x.md"
EXP_043 = '---\nid: EXP-043\ntitle: "Someone else"\ntype: experiment\nstatus: backlog\n---\n\n# EXP-043\n'

AUTO_REL = "research/experiments/EXPR-001-a.md"  # HDD experiments are EXPR-
EXPR_099 = '---\nid: EXPR-099\ntitle: "Ninety-nine"\ntype: experiment\nstatus: backlog\n---\n\n# EXPR-099\n'

M_REL = "research/measures/M-001-x.md"
M_002 = '---\nid: M-002\ntitle: "Other measure"\ntype: measure\nstatus: backlog\n---\n\n# M-002\n'


def _board_with(world: World, files: dict[str, str]) -> KanbanService:
    """An HDD board on origin holding just `files`, fetched by A."""
    hdd(world)
    b_push(world, files)
    git(world.a, "fetch", "-q", "origin")
    return _service(world)


def _assert_refused_untouched(world: World, result: dict, rel: str, text: str, base: str) -> None:
    assert world.remote_show(rel) == text, (
        f"origin's {rel} was replaced:\n{world.remote_show(rel)}"
    )
    assert world.remote_sha() == base, "something was pushed"
    assert result["success"] is False, result
    assert rel.rsplit("/", 1)[-1] in result.get("message", ""), result


# --- (1) explicit id: the reviewer's probe ------------------------------------------


def test_explicit_id_never_overwrites_other_items_file(world) -> None:
    svc = _board_with(world, {EXP_REL: EXP_043})
    # the id guard alone cannot save it: nothing on origin has id EXP-042 (#788)
    assert svc._holder_at("origin/main", "EXP-042") is None
    base = world.remote_sha()
    result = svc.create_item_and_push(WorkItemType.EXPERIMENT, "x", item_id="EXP-042")
    _assert_refused_untouched(world, result, EXP_REL, EXP_043, base)


# --- (1b) the same hole through the CLI flag ---------------------------------------


def test_cli_explicit_id_never_overwrites(world, monkeypatch) -> None:
    _board_with(world, {M_REL: M_002})
    base = world.remote_sha()
    result = invoke(world, monkeypatch, [
        "measure", "create", "x", "--unit", "count", "--category", "coverage",
        "--id", "M-001", "--push",
    ])
    out = " ".join(output_of(result).split())
    assert world.remote_show(M_REL) == M_002, f"origin's {M_REL} was replaced:\n{out}"
    assert world.remote_sha() == base, out
    assert result.exit_code != 0, out
    assert "M-001-x.md" in out, out


# --- (2) auto-allocated id: the path guard holds whatever the allocator picks ------


def test_auto_allocated_never_overwrites(world, monkeypatch) -> None:
    svc = _board_with(world, {AUTO_REL: EXPR_099})
    base = world.remote_sha()
    # the allocator counts stems, so it would say EXPR-100; force the colliding pick
    assert svc._next_id_at("origin/main", "EXPR") == "EXPR-100"
    monkeypatch.setattr(svc, "_next_id_at", lambda rev, prefix: "EXPR-001")
    result = svc.create_item_and_push(WorkItemType.EXPERIMENT, "a")
    assert world.remote_show(AUTO_REL) == EXPR_099, (
        f"origin's {AUTO_REL} was replaced:\n{world.remote_show(AUTO_REL)}"
    )
    if result["success"]:
        # acceptable only if it landed on a path that did not exist
        landed = svc._repo_relative(result["item"].file_path, svc._git_toplevel())
        assert landed is not None and landed.as_posix() != AUTO_REL, result
    else:
        assert world.remote_sha() == base, "something was pushed"
        assert "EXPR-001-a.md" in result.get("message", ""), result


def test_control_auto_allocated_natural_pick_lands(world) -> None:
    svc = _board_with(world, {AUTO_REL: EXPR_099})
    result = svc.create_item_and_push(WorkItemType.EXPERIMENT, "a")
    assert result["success"] is True, result
    assert result["id"] == "EXPR-100", result
    assert world.remote_show(AUTO_REL) == EXPR_099


# --- controls ------------------------------------------------------------------------


def test_control_free_path_lands(world) -> None:
    svc = _board_with(world, {EXP_REL: EXP_043})
    result = svc.create_item_and_push(WorkItemType.EXPERIMENT, "y", item_id="EXP-042")
    assert result["success"] is True, result
    assert "research/experiments/EXP-042-y.md" in world.remote_files()
    assert world.remote_show(EXP_REL) == EXP_043


def test_control_explicit_id_holder_still_refused(world) -> None:
    rel = "research/experiments/notes.md"
    text = '---\nid: EXP-042\ntitle: "Holder"\ntype: experiment\n---\n\n# Holder\n'
    svc = _board_with(world, {rel: text})
    base = world.remote_sha()
    result = svc.create_item_and_push(WorkItemType.EXPERIMENT, "x", item_id="EXP-042")
    assert result["success"] is False, result
    assert "notes.md" in result.get("message", ""), result
    assert world.remote_sha() == base
