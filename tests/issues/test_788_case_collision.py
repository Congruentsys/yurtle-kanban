# ruff: noqa: F811  (fixtures are imported, then re-bound as parameters)
"""Issue #788, round 3 — a ``--push`` create is refused on a case-only name collision.

Round-2 review of PR #810: the path guard in ``create_item_and_push``'s ``build`` uses
``git cat-file -e <base>:<item_rel>``, which is case-SENSITIVE. With origin holding
``research/experiments/exp-042-X.md`` (``id: EXP-043``), creating EXP-042 titled "x"
writes ``EXP-042-x.md`` beside it. Git keeps both blobs, but on a case-insensitive
filesystem (macOS, the fleet's platform) they are one file: every clone that pulls
has one item's content replace the other's, and a later ``commit -a`` pushes the
clobbered content back over the rival on origin. main (389c114) refused this by
accident, through the case-folded filename holder match that #788 retired for files
that carry an ``id:``.

Decided fix: a push create is refused when origin already has a file in the SAME
directory whose name equals the new file's name ignoring case. The message names the
existing file; nothing is created or pushed.

RED today:
- (1) service API: ``item_id="EXP-042"``, "x", beside origin's ``exp-042-X.md``.
- (1b) CLI: ``measure create x --id M-001 --push`` beside origin's ``m-001-X.md``.
- (2) auto-allocated: force the pick (``_next_id_at`` -> EXPR-001) beside origin's
  ``expr-001-A.md``; title "a" builds ``EXPR-001-a.md``.

Controls (green before and after): a different name in the same directory, and the
same case-folded name in a different directory, do not block.
"""

from __future__ import annotations

from tests.issues.test_585_create_push_loop import World, output_of
from tests.issues.test_603_push_failure_messages import invoke
from tests.issues.test_674_parent_edges import (  # noqa: F401  (fixtures)
    _clean_theme_cache,
    world,
)
from tests.issues.test_788_no_overwrite import EXP_043, EXPR_099, M_002, _board_with
from yurtle_kanban.models import WorkItemType

CASE_REL = "research/experiments/exp-042-X.md"  # EXP-042 "x" builds EXP-042-x.md
CASE_M_REL = "research/measures/m-001-X.md"  # M-001 "x" builds M-001-x.md
CASE_AUTO_REL = "research/experiments/expr-001-A.md"  # EXPR-001 "a" builds EXPR-001-a.md


def _folded(files: list[str]) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for f in files:
        found.setdefault(f.casefold(), []).append(f)
    return found


def _assert_no_case_twins(world: World) -> None:
    twins = {k: v for k, v in _folded(world.remote_files()).items() if len(v) > 1}
    assert not twins, f"origin holds case-only twins (one file on macOS): {twins}"


def _assert_refused_untouched(
    world: World, result: dict, rel: str, text: str, base: str, before: list[str]
) -> None:
    assert world.remote_show(rel) == text, f"origin's {rel} was replaced"
    assert world.remote_sha() == base, (
        f"something was pushed; origin now holds {world.remote_files()}"
    )
    assert world.remote_files() == before
    assert result["success"] is False, result
    assert rel.rsplit("/", 1)[-1] in result.get("message", ""), result


# --- (1) explicit id: the reviewer's round-2 probe -----------------------------------


def test_explicit_id_case_only_collision_refused(world) -> None:
    svc = _board_with(world, {CASE_REL: EXP_043})
    # nothing on origin has id EXP-042, and the exact path does not exist
    assert svc._holder_at("origin/main", "EXP-042") is None
    before = world.remote_files()
    assert "research/experiments/EXP-042-x.md" not in before
    base = world.remote_sha()
    result = svc.create_item_and_push(WorkItemType.EXPERIMENT, "x", item_id="EXP-042")
    _assert_refused_untouched(world, result, CASE_REL, EXP_043, base, before)


# --- (1b) the same hole through the CLI flag ---------------------------------------


def test_cli_explicit_id_case_only_collision_refused(world, monkeypatch) -> None:
    _board_with(world, {CASE_M_REL: M_002})
    before = world.remote_files()
    base = world.remote_sha()
    result = invoke(world, monkeypatch, [
        "measure", "create", "x", "--unit", "count", "--category", "coverage",
        "--id", "M-001", "--push",
    ])
    out = " ".join(output_of(result).split())
    assert world.remote_show(CASE_M_REL) == M_002, out
    assert world.remote_sha() == base, f"pushed; origin holds {world.remote_files()}\n{out}"
    assert world.remote_files() == before, out
    assert result.exit_code != 0, out
    assert "m-001-X.md" in out, out


# --- (2) auto-allocated id, forced onto a case-only twin ---------------------------


def test_auto_allocated_case_only_collision_never_shadows(world, monkeypatch) -> None:
    svc = _board_with(world, {CASE_AUTO_REL: EXPR_099})
    before = world.remote_files()
    base = world.remote_sha()
    monkeypatch.setattr(svc, "_next_id_at", lambda rev, prefix: "EXPR-001")
    result = svc.create_item_and_push(WorkItemType.EXPERIMENT, "a")
    assert world.remote_show(CASE_AUTO_REL) == EXPR_099
    _assert_no_case_twins(world)
    if result["success"]:
        # acceptable only if it landed on a path that no origin file folds to
        landed = svc._repo_relative(result["item"].file_path, svc._git_toplevel())
        assert landed is not None, result
        assert landed.as_posix().casefold() not in {f.casefold() for f in before}, result
    else:
        assert world.remote_sha() == base, "something was pushed"
        assert world.remote_files() == before
        assert "expr-001-A.md" in result.get("message", ""), result


# --- controls ------------------------------------------------------------------------


def test_control_other_name_same_dir_lands(world) -> None:
    rel = "research/experiments/exp-042-Y.md"
    svc = _board_with(world, {rel: EXP_043})
    result = svc.create_item_and_push(WorkItemType.EXPERIMENT, "x", item_id="EXP-042")
    assert result["success"] is True, result
    assert "research/experiments/EXP-042-x.md" in world.remote_files()
    assert world.remote_show(rel) == EXP_043


def test_control_same_name_other_dir_lands(world) -> None:
    rel = "research/notes/exp-042-X.md"
    svc = _board_with(world, {rel: EXP_043})
    result = svc.create_item_and_push(WorkItemType.EXPERIMENT, "x", item_id="EXP-042")
    assert result["success"] is True, result
    assert "research/experiments/EXP-042-x.md" in world.remote_files()
    assert world.remote_show(rel) == EXP_043
