"""Issue #1067: follow-ups to #582 (``control halt|resume|status``, PR #1064).

1. ``control halt`` on a clean default-branch checkout: the old test_582 a1 check
   only asked that A's HEAD be unchanged or at origin's tip. Tightened here: A stays
   ON its branch (not detached), and among A's local branches only the default one
   moves, to origin's tip. The basis is #574's pinned contract (test_574_sync_and_push
   §9: "won" on the default branch with a clean tree fast-forwards the checkout).
2. ``pickable()`` read ``control_state()`` (2-3 git subprocesses) on every call.
   The state is now cached per scan: a ``list --pickable`` reads the control file
   from git ONCE, however many items are ready, and a caller looping ``pickable()``
   over the items of one scan pays the git reads once. A new scan reads it afresh.
3. ``control status --json`` on a bad control file carries an ``error`` key that
   explains the halt (a good file keeps #582's exact key set).

Readings:

a. "reads the control state once" is counted at git: the ``git ls-tree`` that
   ``control_state`` runs for ``.kanban/control.yaml`` on origin's last-fetched
   default branch (the only git call that names the file).
b. "constant in N" is pinned as: the git calls of ``list --pickable`` over 10
   ready items equal those over 1.
c. The cache must not go stale across scans: after a fetch that brings a halt and
   a new ``scan()``, ``pickable`` refuses; a long-lived MCP server's
   ``kanban_suggest_next`` sees a halt fetched after it started.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.issues.test_574_claim import ITEM_ID, A, B, item_text, push_from_a
from tests.issues.test_574_sync_and_push import snapshot
from tests.issues.test_582_control_halt import (
    CONTROL,
    REASON,
    STATUS_KEYS,
    GitSpy,
    control_yaml,
    hand_halt,
    local_halted_repo,
    mcp,
    ok,
    run_cli,
    status_json,
)
from tests.issues.test_585_create_push_loop import World, git
from yurtle_kanban.cli import get_service
from yurtle_kanban.service import KanbanService

pytestmark = pytest.mark.usefixtures("claim_env")


def heads(clone: Path) -> dict[str, str]:
    """Local branch name → sha."""
    out = git(clone, "for-each-ref", "--format=%(refname:short) %(objectname)", "refs/heads")
    return dict(line.split(" ", 1) for line in out.splitlines() if line.strip())


def add_ready_items(world: World, n: int) -> list[str]:
    """EXP-002 .. EXP-(n+1) ready and unassigned, on origin and both clones."""
    ids = [f"EXP-{i:03d}" for i in range(2, n + 2)]
    push_from_a(world, {
        f"kanban-work/expeditions/{i}-y.md": item_text("ready", item_id=i, title=f"Y {i}")
        for i in ids
    }, f"{n} more ready items")
    return ids


def control_reads(spy: GitSpy) -> list[list[str]]:
    """The git calls that read the control file (reading a)."""
    return [argv for argv in spy.calls if any(CONTROL in arg for arg in argv)]


def service_at(clone: Path, monkeypatch: pytest.MonkeyPatch) -> KanbanService:
    monkeypatch.chdir(clone)
    return get_service()


# --- 1. the halt fast-forwards A's default branch, nothing else ----------------------------


def test_halt_on_clean_default_branch_keeps_a_on_its_branch(world, monkeypatch) -> None:
    before = snapshot(world.a)
    heads_before = heads(world.a)
    assert before["branch"] == world.default, "fixture: A is on the default branch"

    ok(run_cli(world.a, monkeypatch, ["control", "halt", "--reason", REASON, "--agent", A]))

    tip = world.remote_sha()
    after = snapshot(world.a)
    assert after["branch"] == before["branch"], (
        f"the halt left A detached or on another branch: {after['branch']!r}"
    )
    assert after["head"] == tip, "A's clean default-branch checkout was not fast-forwarded"
    heads_after = heads(world.a)
    assert set(heads_after) == set(heads_before), "the halt created or deleted a branch"
    moved = {name for name in heads_after if heads_after[name] != heads_before[name]}
    assert moved == {world.default}, f"branches moved: {sorted(moved)}"
    assert heads_after[world.default] == tip
    assert after["status"] == "", "the halt dirtied A's checkout"
    added = set(after["files"]) - set(before["files"])
    assert added == {CONTROL}, f"files added to A's tree: {sorted(added)}"
    assert {k: v for k, v in after["files"].items() if k != CONTROL} == before["files"]


# --- 2. the control state is read once per scan --------------------------------------------


def test_list_pickable_reads_the_control_file_once(world, monkeypatch) -> None:
    add_ready_items(world, 9)
    spy = GitSpy(monkeypatch)

    result = run_cli(world.b, monkeypatch, ["list", "--pickable", "--agent", B])

    ok(result)
    reads = control_reads(spy)
    assert len(reads) == 1, f"list --pickable read the control file {len(reads)}x: {reads}"


def test_list_pickable_git_calls_are_constant_in_items(world, monkeypatch) -> None:
    spy = GitSpy(monkeypatch)
    ok(run_cli(world.b, monkeypatch, ["list", "--pickable", "--agent", B]))
    one = len(spy.calls)

    add_ready_items(world, 9)
    spy.calls.clear()
    ok(run_cli(world.b, monkeypatch, ["list", "--pickable", "--agent", B]))

    assert len(spy.calls) == one, f"git calls: {one} for 1 ready item, {len(spy.calls)} for 10"


def test_pickable_in_a_loop_reads_git_once_per_scan(world, monkeypatch) -> None:
    ids = [ITEM_ID, *add_ready_items(world, 9)]
    svc = service_at(world.b, monkeypatch)
    items = [svc.get_item(i) for i in ids]
    assert all(items), "fixture: the ready items did not parse"
    spy = GitSpy(monkeypatch)

    first = svc.pickable(items[0], B)
    after_first = len(spy.calls)
    rest = [svc.pickable(item, B) for item in items[1:]]

    assert first == (True, "pickable"), first
    assert all(r == (True, "pickable") for r in rest), rest
    assert len(control_reads(spy)) == 1, control_reads(spy)
    assert len(spy.calls) == after_first, (
        f"9 more pickable() calls ran {len(spy.calls) - after_first} more git commands"
    )


def test_a_new_scan_reads_the_control_state_afresh(world, monkeypatch) -> None:
    svc = service_at(world.b, monkeypatch)
    item = svc.get_item(ITEM_ID)
    assert item is not None
    assert svc.pickable(item, B) == (True, "pickable")

    hand_halt(world)  # on origin, and fetched by B
    svc.scan()

    okay, reason = svc.pickable(svc.get_item(ITEM_ID), B)
    assert okay is False, "a scan after the fetch still saw the board running"
    assert reason.startswith(f"board halted by {A}"), reason


def test_long_lived_mcp_sees_a_halt_fetched_after_it_started(world, monkeypatch) -> None:
    monkeypatch.chdir(world.b)
    server = mcp(world.b)
    first: dict[str, Any] = server._suggest_next({"assignee": B})
    assert first.get("suggestion"), first

    hand_halt(world)

    second: dict[str, Any] = server._suggest_next({"assignee": B})
    assert second.get("suggestion") is None, second
    assert second.get("halted") is True, second


# --- 3. control status --json explains a bad control file ----------------------------------


BAD = {
    "unknown-mode": control_yaml(mode="pause"),
    "broken-yaml": "mode: [halt\nreason: \"unterminated\n",
}


@pytest.mark.parametrize("case", list(BAD), ids=list(BAD))
def test_status_json_on_a_bad_file_carries_error(tmp_path, monkeypatch, case) -> None:
    repo = local_halted_repo(tmp_path, monkeypatch, BAD[case])

    data = status_json(repo.root, monkeypatch)

    assert data["mode"] == "halt", data
    assert "error" in data, f"no error key: {data}"
    assert isinstance(data["error"], str) and "control.yaml" in data["error"], data
    if case == "unknown-mode":
        assert "pause" in data["error"], data


def test_status_json_on_a_bad_origin_file_carries_error(world, monkeypatch) -> None:
    hand_halt(world, mode="pause")

    data = status_json(world.b, monkeypatch)

    assert data["mode"] == "halt" and data["source"] == "remote", data
    assert "pause" in str(data.get("error")), data


def test_status_json_on_a_good_file_keeps_the_582_keys(tmp_path, monkeypatch) -> None:
    repo = local_halted_repo(tmp_path, monkeypatch, control_yaml(by=B))
    assert set(status_json(repo.root, monkeypatch)) == STATUS_KEYS
