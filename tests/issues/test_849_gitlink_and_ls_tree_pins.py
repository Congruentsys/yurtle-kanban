"""Issue #849: pin two #814 cases that were only checked with temporary tests.

1. A gitlink (a submodule entry, mode 160000) whose path ends ``.md`` sits in a
   board folder on origin. ``_items_at`` keeps only regular files (100644/100755),
   so the gitlink is skipped: it is never read as a blob, never counted, and never
   "missing". ``claim`` of another item and ``update --push --add-dep`` on an
   in-repo item both succeed.
2. ``_items_at``'s own ``git ls-tree`` fails. The fetched-tree read refuses: the
   claim is refused (exit 1), nothing is pushed, and the message names ``ls-tree``.
   (When this was written, tests/issues/test_814_wip_fails_closed.py's
   ``break_blobs_ls_tree`` broke only an ``ls-tree`` that ``_blobs_at`` ran. Since
   #880 ``_blobs_at`` runs none: both tests break ``_items_at``'s one listing,
   through tests/issues/_git_shims.py's ``break_git_in`` (#892).)

Harness: the #585 ``World`` and the #574 claim helpers, as in
tests/issues/test_814_symlink_on_board.py.

Ambiguities resolved here (the test partner's reading; the driver may challenge):

a. The gitlink is made in clone B with ``git update-index --add --cacheinfo
   160000,<B's HEAD sha>,<path>``, then committed and pushed. A does not pull it
   first, except in the variant where A pulls it (git checks it out as an empty
   directory whose name ends ``.md``) before claiming.
b. "Not counted" is pinned by behaviour: with WIP limit 1 and nothing in progress,
   the claim wins; with B's EXP-002 in progress on origin, the refusal names WIP
   and never a read failure (``ls-tree``, "does not list", "can't read").
c. The ls-tree failure is injected only in the ``ls-tree`` that ``_items_at``
   runs (``KanbanService._git_run``, caller frame ``_items_at``). It is pinned with
   WIP not full (an unreadable listing must not count as an empty board) and with
   WIP full.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from tests.issues._git_shims import break_git_in
from tests.issues.test_574_claim import (
    ITEM,
    ITEM_ID,
    OTHER,
    WIP_CONFIG,
    A,
    B,
    claim,
    frontmatter,
    invoke,
    item_text,
    native_in_progress,
    nodes_by,
    output_of,
    push_from_a,
)
from tests.issues.test_574_sync_and_push import Recorder, commit_files, snapshot
from tests.issues.test_585_create_push_loop import EXP_DIR, World, git
from tests.issues.test_590_next_id_and_hdd_ids import b_push
from yurtle_kanban import config as config_mod

GITLINKS = [f"{EXP_DIR}/vendored.md", f"{EXP_DIR}/EXP-003-sub.md"]
READ_FAILURE = ("ls-tree", "does not list", "can't read", "cat-file", "left out")


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    monkeypatch.delenv("YURTLE_AGENT", raising=False)
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


@pytest.fixture
def world(tmp_path: Path) -> World:
    """Origin and both clones hold EXP-001 at `ready`, unassigned; WIP limit 1 on
    in-progress."""
    w = World(tmp_path)
    push_from_a(
        w,
        {ITEM: item_text("ready"), ".kanban/config.yaml": WIP_CONFIG},
        "seed EXP-001, wip 1",
    )
    return w


def push_gitlink(world: World, rel: str) -> None:
    """B commits a gitlink at `rel` (mode 160000, pointing at B's HEAD) and pushes it."""
    git(world.b, "fetch", "origin")
    git(world.b, "reset", "--hard", f"origin/{world.default}")
    sha = git(world.b, "rev-parse", "HEAD").strip()
    git(world.b, "update-index", "--add", "--cacheinfo", f"160000,{sha},{rel}")
    git(world.b, "commit", "-m", f"gitlink {rel}")
    git(world.b, "push", "origin", f"HEAD:refs/heads/{world.default}")
    mode = git(world.remote, "ls-tree", world.default, "--", rel).split()[0]
    assert mode == "160000", f"{rel} is not a gitlink on origin: mode {mode}"


def assert_won(world: World, out: Any, base: str, label: str) -> None:
    assert out.kind == "won", f"{out.kind}: {out.message}"
    assert out.exit_code == 0
    tip = world.remote_sha()
    assert git(world.remote, "rev-list", f"{base}..{tip}").split() == [tip]
    assert commit_files(world.remote, tip) == [ITEM]
    text = world.remote_show(ITEM)
    fm = frontmatter(text)
    assert fm["assignee"] == A
    assert fm["status"] == label
    assert any(re.search(r"kb:status\s+kb:in_progress\b", n) for n in nodes_by(text, A)), text


def assert_refused_clean(
    world: World, out: Any, base: str, before: dict[str, Any], rec: Recorder
) -> str:
    assert out.kind == "refused", f"{out.kind}: {out.message}"
    assert out.exit_code == 1
    assert world.remote_sha() == base, "the refused claim changed origin"
    assert rec.seams == [], "a push was attempted"
    assert snapshot(world.a) == before, "the refused claim wrote to A's checkout"
    return out.message.lower()


# --- 1. a gitlink named *.md on a board is skipped ---------------------------------------


@pytest.mark.parametrize("rel", GITLINKS, ids=["vendored", "item-like"])
def test_gitlink_on_board_claim_wins(world, rel) -> None:
    label = native_in_progress(world.a)
    push_gitlink(world, rel)
    base = world.remote_sha()

    out = claim(world.a, A)

    assert_won(world, out, base, label)


def test_gitlink_on_board_pulled_by_a_claim_wins(world) -> None:
    """A has checked the gitlink out (an empty directory named `vendored.md`)."""
    label = native_in_progress(world.a)
    push_gitlink(world, GITLINKS[0])
    git(world.a, "pull", "--ff-only", "origin", world.default)
    assert (world.a / GITLINKS[0]).is_dir()
    base = world.remote_sha()

    out = claim(world.a, A)

    assert_won(world, out, base, label)


def test_gitlink_on_board_wip_full_refused_as_wip(world) -> None:
    push_gitlink(world, GITLINKS[0])
    b_push(world, {OTHER: item_text("in_progress", B, "EXP-002", "Y")})
    base = world.remote_sha()
    before = snapshot(world.a)
    rec = Recorder()

    out = claim(world.a, A, rec)

    msg = assert_refused_clean(world, out, base, before, rec)
    assert "wip" in msg, out.message
    assert not any(m in msg for m in READ_FAILURE), f"read failure, not WIP: {out.message}"


def test_cli_gitlink_on_board_claim_wins(world, monkeypatch) -> None:
    push_gitlink(world, GITLINKS[0])

    result = invoke(world, monkeypatch, ["claim", ITEM_ID, "--agent", A])

    assert result.exit_code == 0, output_of(result)
    assert frontmatter(world.remote_show(ITEM))["assignee"] == A


@pytest.mark.parametrize("pulled", [False, True], ids=["not-pulled", "pulled"])
def test_update_push_add_dep_with_gitlink_on_board(world, monkeypatch, pulled) -> None:
    push_from_a(world, {OTHER: item_text("ready", None, "EXP-002", "Y")}, "seed EXP-002")
    push_gitlink(world, GITLINKS[0])
    if pulled:
        git(world.a, "pull", "--ff-only", "origin", world.default)
    base = world.remote_sha()

    result = invoke(world, monkeypatch, ["update", ITEM_ID, "--add-dep", "EXP-002", "--push"])

    out = output_of(result)
    assert result.exit_code == 0, out
    assert not any(m in out.lower() for m in READ_FAILURE), out
    tip = world.remote_sha()
    assert git(world.remote, "rev-list", f"{base}..{tip}").split() == [tip]
    assert commit_files(world.remote, tip) == [ITEM]
    assert frontmatter(world.remote_show(ITEM)).get("depends_on") == ["EXP-002"]


# --- 2. `_items_at`'s own ls-tree fails: the read refuses -------------------------------


def break_items_ls_tree(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    """The `ls-tree` that `_items_at` runs exits 128; every other git call runs for
    real. Since #880 it is the only `ls-tree` of the board: `_blobs_at` reads the
    object ids it lists."""
    return break_git_in(monkeypatch, "_items_at", "ls-tree", "fatal: ls-tree failed (#849 test)")


@pytest.mark.parametrize("wip_full", [False, True], ids=["wip-not-full", "wip-full"])
def test_items_ls_tree_failure_claim_is_refused(world, monkeypatch, wip_full) -> None:
    if wip_full:
        b_push(world, {OTHER: item_text("in_progress", B, "EXP-002", "Y")})
    base = world.remote_sha()
    before = snapshot(world.a)
    seen = break_items_ls_tree(monkeypatch)
    rec = Recorder()

    out = claim(world.a, A, rec)

    assert seen, "`_items_at` ran no `git ls-tree`: WIP did not read the fetched tree"
    msg = assert_refused_clean(world, out, base, before, rec)
    assert "ls-tree" in msg, f"message does not name ls-tree: {out.message}"


def test_cli_items_ls_tree_failure_claim_exits_1(world, monkeypatch) -> None:
    base = world.remote_sha()
    seen = break_items_ls_tree(monkeypatch)

    result = invoke(world, monkeypatch, ["claim", ITEM_ID, "--agent", A])

    out = output_of(result)
    assert result.exception is None or isinstance(result.exception, SystemExit), (
        f"claim crashed instead of refusing: {result.exception!r}"
    )
    assert seen, "`_items_at` ran no `git ls-tree`"
    assert result.exit_code == 1, out
    assert "ls-tree" in out.lower(), out
    assert world.remote_sha() == base
    assert frontmatter(world.remote_show(ITEM)).get("assignee") is None


def test_items_ls_tree_failure_update_push_add_dep_is_refused(world, monkeypatch) -> None:
    push_from_a(world, {OTHER: item_text("ready", None, "EXP-002", "Y")}, "seed EXP-002")
    base = world.remote_sha()
    seen = break_items_ls_tree(monkeypatch)

    result = invoke(world, monkeypatch, ["update", ITEM_ID, "--add-dep", "EXP-002", "--push"])

    out = output_of(result)
    assert seen, "`_items_at` ran no `git ls-tree`"
    assert result.exit_code != 0, out
    assert "ls-tree" in out.lower(), out
    assert world.remote_sha() == base


# --- controls -------------------------------------------------------------------------------


def test_control_claim_wins_without_gitlink(world) -> None:
    label = native_in_progress(world.a)
    base = world.remote_sha()

    out = claim(world.a, A)

    assert_won(world, out, base, label)


def test_control_wip_full_refused_as_wip(world) -> None:
    b_push(world, {OTHER: item_text("in_progress", B, "EXP-002", "Y")})
    base = world.remote_sha()
    before = snapshot(world.a)
    rec = Recorder()

    out = claim(world.a, A, rec)

    msg = assert_refused_clean(world, out, base, before, rec)
    assert "wip" in msg, out.message
