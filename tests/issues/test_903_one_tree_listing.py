# ruff: noqa: F811  (fixtures are imported, then re-bound as parameters)
"""Issue #903 — the #788 file-twin check reuses #834's whole-tree listing: one
``ls-tree`` of the base per ``create --push`` build attempt.

In ``_create_on_default_branch``'s ``build(base)`` the file-twin check (#788, #869)
runs its own ``git ls-tree --name-only`` of the new item's folder, and then
``_folder_case_twin`` (#834) lists the whole tree (``ls-tree -r``). Both checks read the
same base, so the file-twin check should use the whole-tree listing too: one subprocess
fewer per attempt (a lost race retries ``build``, so per retry as well), and the check
no longer depends on its folder pathspec matching origin's spelling.

What is counted: ``ls-tree … --name-only`` calls made inside ``build``, leaving out
the id allocator's and holder check's own listings (``_ids_at`` / ``_next_id_at`` /
``_holder_at``), which are not twin checks.

RED today:
- (1) one clean ``create --push``, one attempt: 2 twin-check listings where 1 is
  expected.
- (2) a lost CAS race (#585's rival push before A's first push), two attempts: 4
  where 2 are expected.

Controls (green before and after; the 788/834/869 modules already cover that the
refusals happen and name the file or folder — these pin which MESSAGE each gives,
since the two checks will now read one listing):
- a file twin in the item's folder (``exp-042-X.md`` vs ``EXP-042-x.md``; ``ÉXP-042``
  NFC vs NFD) is refused with "already exists on the default branch … would replace
  it", not the folder message;
- a folder spelled in another case (origin ``Research/…``, board ``research/…``) is
  refused with "already on the default branch in that spelling", not the file-twin
  message;
- a clean create lands (tests 1 and 2 assert it too).
"""

from __future__ import annotations

import subprocess
import sys
from typing import Any

import pytest

from tests.issues.test_585_create_push_loop import PushHook, git
from tests.issues.test_674_parent_edges import (  # noqa: F401  (fixtures)
    _clean_theme_cache,
    world,
)
from tests.issues.test_788_case_collision import CASE_REL
from tests.issues.test_788_no_overwrite import EXP_043, EXPR_099, _board_with
from tests.issues.test_834_folder_case_guard import TOP_EXTRA, _top_board
from tests.issues.test_869_twin_guard_nfc import (
    EXP_NFC,
    EXP_NFD,
    _board,
    _rewrite_origin,
)
from yurtle_kanban.models import WorkItemType
from yurtle_kanban.service import KanbanService

NEW_ID = "EXPR-042"
FILE_TWIN_MSG = ("already exists on the default branch", "would replace it")
FOLDER_TWIN_MSG = "already on the default branch in that spelling"

# listings inside `build` that are not twin checks: the id allocator's and the
# explicit-id holder check's (and the blob readers', should they run)
NOT_TWIN_CHECKS = {
    "_ids_at",
    "_next_id_at",
    "_next_id_number_at",
    "_holder_at",
    "_allocation_blob",
    "_parent_link_blob",
    "_items_at",
    "_blobs_at",
}


def count_twin_listings(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    """Record every `ls-tree … --name-only` issued from inside `build` (the create's
    CAS build step), past the id helpers' own listings."""
    seen: list[tuple[str, ...]] = []
    real_git_run = KanbanService._git_run

    def git_run(self: KanbanService, *args: str, **kwargs: Any) -> Any:
        if args[:1] == ("ls-tree",) and "--name-only" in args:
            stack: list[str] = []
            frame = sys._getframe(1)
            while frame is not None:
                stack.append(frame.f_code.co_name)
                if frame.f_code.co_name == "build":
                    break
                frame = frame.f_back
            if frame is not None and not NOT_TWIN_CHECKS.intersection(stack):
                seen.append(args)
        return real_git_run(self, *args, **kwargs)

    monkeypatch.setattr(KanbanService, "_git_run", git_run)
    return seen


def _show(seen: list[tuple[str, ...]]) -> str:
    return "\n".join(" ".join(a) for a in seen)


# --- (1) one attempt, one listing ------------------------------------------------------


def test_one_attempt_lists_the_base_tree_once(world, monkeypatch) -> None:
    svc = _board_with(world, {"research/experiments/EXPR-001-a.md": EXPR_099})
    seen = count_twin_listings(monkeypatch)
    pushes = PushHook(lambda n: None)
    monkeypatch.setattr(subprocess, "run", pushes)
    result = svc.create_item_and_push(WorkItemType.EXPERIMENT, "b")
    assert result["success"] is True, result
    assert pushes.pushes == 1, f"expected one attempt, saw {pushes.pushes} pushes"
    assert len(seen) == 1, (
        f"{len(seen)} twin-check ls-tree listings in one build attempt, expected 1:\n" + _show(seen)
    )


def test_one_attempt_explicit_id_lists_the_base_tree_once(world, monkeypatch) -> None:
    svc = _board_with(world, {"research/experiments/EXPR-001-a.md": EXPR_099})
    seen = count_twin_listings(monkeypatch)
    result = svc.create_item_and_push(WorkItemType.EXPERIMENT, "b", item_id=NEW_ID)
    assert result["success"] is True, result
    assert f"research/experiments/{NEW_ID}-b.md" in world.remote_files()
    assert len(seen) == 1, (
        f"{len(seen)} twin-check ls-tree listings in one build attempt, expected 1:\n" + _show(seen)
    )


# --- (2) a lost race: two attempts, two listings ----------------------------------------


def test_lost_race_retry_lists_once_per_attempt(world, monkeypatch) -> None:
    svc = _board_with(world, {"research/experiments/EXPR-001-a.md": EXPR_099})
    seen = count_twin_listings(monkeypatch)
    # B pushes just ahead of A's first push: A's push is rejected, A refetches and
    # builds again (#585)
    race = PushHook(lambda n: world.b_push_item() if n == 1 else None)
    monkeypatch.setattr(subprocess, "run", race)
    result = svc.create_item_and_push(WorkItemType.EXPERIMENT, "b")
    assert result["success"] is True, result
    assert world.b_pushes == 1
    assert race.pushes == 2, f"expected exactly one retry, saw {race.pushes} pushes"
    bases = {a[a.index("--full-tree") + 1] for a in seen if "--full-tree" in a}
    assert len(bases) == 2, f"the retry did not build on a fresh base:\n{_show(seen)}"
    assert len(seen) == 2, (
        f"{len(seen)} twin-check ls-tree listings over two build attempts, expected 2:\n"
        + _show(seen)
    )


# --- controls: which refusal each twin gets ----------------------------------------------


def _assert_file_twin_refusal(result: dict, name: str) -> None:
    msg = result.get("message", "")
    assert result["success"] is False, result
    assert name in msg, result
    for needle in FILE_TWIN_MSG:
        assert needle in msg, result
    assert FOLDER_TWIN_MSG not in msg, result


def test_control_case_file_twin_gets_file_message(world) -> None:
    svc = _board_with(world, {CASE_REL: EXP_043})
    base = world.remote_sha()
    result = svc.create_item_and_push(WorkItemType.EXPERIMENT, "x", item_id="EXP-042")
    assert world.remote_sha() == base, "something was pushed"
    _assert_file_twin_refusal(result, "exp-042-X.md")


@pytest.mark.parametrize(
    ("origin_id", "new_id"),
    [(EXP_NFD, EXP_NFC), (EXP_NFC, EXP_NFD)],
    ids=["origin-nfd-new-nfc", "origin-nfc-new-nfd"],
)
def test_control_nfc_nfd_file_twin_gets_file_message(world, origin_id, new_id) -> None:
    svc = _board(world, "research/")
    _rewrite_origin(world, files={f"research/experiments/{origin_id}-x.md": EXP_043})
    git(world.a, "fetch", "-q", "origin")
    base = world.remote_sha()
    result = svc.create_item_and_push(WorkItemType.EXPERIMENT, "x", item_id=new_id)
    assert world.remote_sha() == base, "something was pushed"
    _assert_file_twin_refusal(result, f"{origin_id}-x.md")


def test_control_folder_twin_gets_folder_message(world) -> None:
    svc = _top_board(world, "research/", "Research/", TOP_EXTRA)
    base = world.remote_sha()
    result = svc.create_item_and_push(WorkItemType.EXPERIMENT, "b", item_id=NEW_ID)
    assert world.remote_sha() == base, "something was pushed"
    msg = result.get("message", "")
    assert result["success"] is False, result
    assert "Research/" in msg, result
    assert FOLDER_TWIN_MSG in msg, result
    for needle in FILE_TWIN_MSG:
        assert needle not in msg, result
