"""Issue #814: the claim's WIP count on the fetched tree fails CLOSED, and the
ASSIGNED hook fires only when the assignee changes.

The [steer] on #814 keeps two of its four items (the config and gate-read items
moved to #831):

1. ``claim_item`` counts WIP in origin's fetched tree through ``_items_at`` /
   ``_blobs_at``. If that tree can't be read, the claim is REFUSED (exit 1) with a
   message naming the cause. It never counts zero. Nothing is pushed and nothing is
   written. (#832: ``_blobs_at`` reads the blobs with ``ls-tree`` + ``cat-file
   --batch``, no longer ``git archive``, so a ``.gitattributes`` ``export-ignore``
   no longer hides board files: the count sees them and judges WIP as usual.)
2. On a claim, ``ASSIGNED`` fires only when the assignee changes. ``STATUS_CHANGE``
   still fires when the status changes.

Harness: the #585 ``World`` and the #574 claim seam/hook helpers
(tests/issues/test_574_claim.py).

Ambiguities resolved here (the test partner's reading; the driver may challenge):

a. The read failure is injected by making ``git cat-file --batch`` exit 128 at
   BOTH places a service could run it: ``KanbanService._git_run`` and
   ``subprocess.run`` (where ``_blobs_at`` runs it today), so the test does not pin
   which of the two it routes through. A variant fails the ``ls-tree`` that
   lists the blobs (#832; ``_items_at``'s since #880).
b. "A message naming the cause": the message names the git command that failed
   (``cat-file`` or ``ls-tree``).
c. Fail-closed is pinned with WIP full on origin (the fail-open bug: a claim that
   must lose wins) AND with WIP not full (an unreadable tree never counts, so a
   limit that would have passed is still refused).
d. ``export-ignore`` anywhere (on the board or off it) changes nothing (#832):
   with WIP full on origin the claim is refused ON WIP, and with WIP not full it
   wins.
e. "The assignee changes" is compared as stored text. A claim of an item already
   assigned to the actor, spelled exactly as the actor, fires no ASSIGNED. A
   case-variant spelling (``Agent-a`` vs ``agent-A``) is NOT pinned here.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from tests.issues._git_shims import break_git_in
from tests.issues.test_574_claim import (
    ITEM_ID,
    OTHER,
    WIP_CONFIG,
    A,
    B,
    assert_claimed_by,
    claim,
    fired,
    install_hooks,
    invoke,
    item_text,
    output_of,
    push_from_a,
    seed,
)
from tests.issues.test_574_sync_and_push import Recorder, snapshot
from tests.issues.test_585_create_push_loop import EXP_DIR, World
from tests.issues.test_590_next_id_and_hdd_ids import b_push
from yurtle_kanban import config as config_mod
from yurtle_kanban.service import KanbanService


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    monkeypatch.delenv("YURTLE_AGENT", raising=False)
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


@pytest.fixture
def world(tmp_path: Path) -> World:
    """Origin and both clones hold EXP-001 at `ready`, unassigned."""
    w = World(tmp_path)
    push_from_a(w, {f"{EXP_DIR}/EXP-001-x.md": item_text("ready")}, "seed EXP-001")
    return w


def wip_limit_1(world: World) -> None:
    push_from_a(world, {".kanban/config.yaml": WIP_CONFIG}, "board: wip 1")


def wip_full_on_origin(world: World) -> None:
    """WIP limit 1 on origin, with B's EXP-002 in progress there (A never pulls it)."""
    wip_limit_1(world)
    b_push(world, {OTHER: item_text("in_progress", B, "EXP-002", "Y")})
    assert not (world.a / OTHER).exists(), "A must not see B's item locally"


def attributes_on_origin(world: World, text: str) -> None:
    """Commit `.gitattributes` on origin, in A's checkout too."""
    push_from_a(world, {".gitattributes": text}, "attributes")


def break_cat_file(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    """`git cat-file --batch` exits 128 whether it runs through `_git_run` or
    `subprocess.run`. Returns the calls seen, so a test can check one was attempted."""
    seen: list[tuple[str, ...]] = []
    real_git_run = KanbanService._git_run
    real_run = subprocess.run
    err = "fatal: cat-file failed (#832 test)"

    def git_run(self: KanbanService, *args: str, **kwargs: Any) -> Any:
        if args[:2] == ("cat-file", "--batch"):
            seen.append(("_git_run", *args))
            return subprocess.CompletedProcess(["git", *args], 128, "", err)
        return real_git_run(self, *args, **kwargs)

    def run(cmd: Any, *args: Any, **kwargs: Any) -> Any:
        if isinstance(cmd, (list, tuple)) and list(cmd[:3]) == ["git", "cat-file", "--batch"]:
            seen.append(("subprocess", *map(str, cmd)))
            text = kwargs.get("text") or kwargs.get("universal_newlines")
            empty: Any = "" if text else b""
            return subprocess.CompletedProcess(cmd, 128, empty, err if text else err.encode())
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(KanbanService, "_git_run", git_run)
    monkeypatch.setattr(subprocess, "run", run)
    return seen


def break_items_ls_tree(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    """The `ls-tree` that lists the board's blobs exits 128. Since #880 that is
    `_items_at`'s one listing, whose object ids `_blobs_at` reads."""
    return break_git_in(monkeypatch, "_items_at", "ls-tree", "fatal: ls-tree failed (#832 test)")


def assert_refused_clean(
    world: World, out: Any, base: str, before: dict[str, Any], rec: Recorder,
    causes: tuple[str, ...],
) -> None:
    assert out.kind == "refused", f"{out.kind}: {out.message}"
    assert out.exit_code == 1
    msg = out.message.lower()
    assert any(c in msg for c in causes), f"message names none of {causes}: {out.message}"
    assert world.remote_sha() == base, "the refused claim changed origin"
    assert rec.seams == [], "a push was attempted"
    assert snapshot(world.a) == before, "the refused claim wrote to A's checkout"


# --- 1. a failed blob read fails closed ----------------------------------------------------


def test_cat_file_failure_with_wip_full_on_origin_is_refused(world, monkeypatch, tmp_path) -> None:
    wip_full_on_origin(world)
    marker = install_hooks(world, tmp_path)
    base = world.remote_sha()
    before = snapshot(world.a)
    seen = break_cat_file(monkeypatch)
    rec = Recorder()

    out = claim(world.a, A, rec)

    assert seen, "no `git cat-file --batch` was attempted: WIP did not read the fetched tree"
    assert_refused_clean(world, out, base, before, rec, ("cat-file",))
    assert fired(marker) == []


def test_cat_file_failure_with_wip_not_full_is_still_refused(world, monkeypatch) -> None:
    """An unreadable fetched tree never counts as zero, even when the real count
    would have passed."""
    wip_limit_1(world)
    base = world.remote_sha()
    before = snapshot(world.a)
    seen = break_cat_file(monkeypatch)
    rec = Recorder()

    out = claim(world.a, A, rec)

    assert seen, "no `git cat-file --batch` was attempted"
    assert_refused_clean(world, out, base, before, rec, ("cat-file",))


def test_items_ls_tree_failure_with_wip_limit_not_full_is_still_refused(world, monkeypatch) -> None:
    wip_limit_1(world)
    base = world.remote_sha()
    before = snapshot(world.a)
    seen = break_items_ls_tree(monkeypatch)
    rec = Recorder()

    out = claim(world.a, A, rec)

    assert seen, "`_items_at` ran no `git ls-tree`"
    assert_refused_clean(world, out, base, before, rec, ("ls-tree",))


# --- 1. export-ignore hides nothing from the count (#832) ------------------------------------

NOT_THE_CAUSE = ("export-ignore", ".gitattributes", "archive")


@pytest.mark.parametrize(
    "attributes",
    [
        "kanban-work/** export-ignore\n",
        "kanban-work/expeditions/** export-ignore\n",
        f"{OTHER} export-ignore\n",
        "*.md export-ignore\n",
    ],
    ids=["board-root", "board-folder", "one-item", "all-md"],
)
def test_export_ignore_does_not_hide_items_wip_full_refused_on_wip(
    world, tmp_path, attributes
) -> None:
    attributes_on_origin(world, attributes)
    wip_full_on_origin(world)
    marker = install_hooks(world, tmp_path)
    base = world.remote_sha()
    before = snapshot(world.a)
    rec = Recorder()

    out = claim(world.a, A, rec)

    assert_refused_clean(world, out, base, before, rec, ("wip",))
    assert not any(c in out.message.lower() for c in NOT_THE_CAUSE), out.message
    assert fired(marker) == []


def test_export_ignore_does_not_hide_items_wip_not_full_wins(world) -> None:
    attributes_on_origin(world, "kanban-work/** export-ignore\n")
    wip_limit_1(world)
    base = world.remote_sha()

    out = claim(world.a, A)

    assert out.kind == "won", out.message
    assert_claimed_by(world, A, base)


def test_cli_export_ignore_does_not_hide_items_exits_1_on_wip(world, monkeypatch) -> None:
    attributes_on_origin(world, "kanban-work/** export-ignore\n")
    wip_full_on_origin(world)
    base = world.remote_sha()

    result = invoke(world, monkeypatch, ["claim", ITEM_ID, "--agent", A])

    out = output_of(result)
    assert result.exception is None or isinstance(result.exception, SystemExit), (
        f"claim crashed instead of refusing: {result.exception!r}"
    )
    assert result.exit_code == 1, out
    assert "wip" in out.lower(), out
    assert not any(c in out.lower() for c in NOT_THE_CAUSE), out
    assert world.remote_sha() == base


# --- controls: WIP on the fetched tree ------------------------------------------------------------


def test_control_wip_full_on_origin_is_refused(world) -> None:
    wip_full_on_origin(world)
    base = world.remote_sha()
    before = snapshot(world.a)
    rec = Recorder()

    out = claim(world.a, A, rec)

    assert_refused_clean(world, out, base, before, rec, ("wip",))


def test_control_wip_not_full_wins(world) -> None:
    wip_limit_1(world)
    b_push(world, {OTHER: item_text("ready", B, "EXP-002", "Y")})
    base = world.remote_sha()

    out = claim(world.a, A)

    assert out.kind == "won", out.message
    assert_claimed_by(world, A, base)


@pytest.mark.parametrize(
    "attributes",
    [
        "docs/** export-ignore\n",
        "*.txt export-ignore\n",
        "kanban-work/expeditions/*.txt export-ignore\n",
    ],
    ids=["outside-board", "non-md-anywhere", "non-md-in-board"],
)
def test_control_export_ignore_off_the_board_md_set_wins(world, attributes) -> None:
    """export-ignore off the board changes nothing: WIP is counted as usual and the
    claim wins."""
    push_from_a(
        world,
        {
            ".gitattributes": attributes,
            "docs/readme.md": "# docs\n",
            f"{EXP_DIR}/notes.txt": "notes\n",
        },
        "attributes + extras",
    )
    wip_limit_1(world)
    base = world.remote_sha()

    out = claim(world.a, A)

    assert out.kind == "won", out.message
    assert_claimed_by(world, A, base)


def test_control_export_ignore_off_the_board_still_counts_wip(world) -> None:
    """The same harmless attributes do not switch WIP off: full on origin refuses."""
    attributes_on_origin(world, "docs/** export-ignore\n*.txt export-ignore\n")
    wip_full_on_origin(world)
    base = world.remote_sha()
    before = snapshot(world.a)
    rec = Recorder()

    out = claim(world.a, A, rec)

    assert_refused_clean(world, out, base, before, rec, ("wip",))


# --- 2. ASSIGNED only when the assignee changes ----------------------------------------------------


@pytest.mark.parametrize("take_over", [False, True], ids=["plain", "take-over"])
def test_claim_pre_assigned_to_self_fires_no_assigned(world, tmp_path, take_over) -> None:
    seed(world, "ready", A)
    marker = install_hooks(world, tmp_path)
    base = world.remote_sha()

    out = claim(world.a, A, take_over=take_over)

    assert out.kind == "won", out.message
    assert_claimed_by(world, A, base)
    assert fired(marker) == [f"on_status_change {ITEM_ID} in_progress {A}"], (
        f"assignee unchanged: STATUS_CHANGE once and no ASSIGNED: {fired(marker)}"
    )


# a claim of one's own blocked item (refused, no hooks): test_990_575_followups.py (#990)


# --- controls: hooks on a real change --------------------------------------------------------------


def test_control_plain_claim_fires_both_hooks_once(world, tmp_path) -> None:
    marker = install_hooks(world, tmp_path)
    base = world.remote_sha()

    out = claim(world.a, A)

    assert out.kind == "won", out.message
    assert_claimed_by(world, A, base)
    assert sorted(fired(marker)) == sorted([
        f"on_status_change {ITEM_ID} in_progress {A}",
        f"on_assign {ITEM_ID} in_progress {A}",
    ]), fired(marker)


def test_control_take_over_in_progress_fires_assigned(world, tmp_path) -> None:
    """The holder changes (B -> A) with the status unchanged: ASSIGNED once, no
    STATUS_CHANGE."""
    seed(world, "in_progress", B)
    marker = install_hooks(world, tmp_path)

    out = claim(world.a, A, take_over=True)

    assert out.kind == "won", out.message
    assert fired(marker) == [f"on_assign {ITEM_ID} in_progress {A}"], fired(marker)


def test_control_take_over_ready_from_other_fires_both(world, tmp_path) -> None:
    seed(world, "ready", B)
    marker = install_hooks(world, tmp_path)

    out = claim(world.a, A, take_over=True)

    assert out.kind == "won", out.message
    assert sorted(fired(marker)) == sorted([
        f"on_status_change {ITEM_ID} in_progress {A}",
        f"on_assign {ITEM_ID} in_progress {A}",
    ]), fired(marker)
