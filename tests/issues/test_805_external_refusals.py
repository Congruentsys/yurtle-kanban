"""Issue #805, round 3 (the PR #820 review, round 2): an external item's refusals and
no-ops stay local.

The round-2 fix goes local before fetching only when the working tree's `mutate`
answer is a `Change` naming an outside file. A `Refuse` or `NoOp` about an
external-board item falls through to the compare-and-swap. That re-asks `mutate`
against origin's tree, which can't hold the external file: a refusal becomes "Item
not found on origin", and with origin down an idempotent re-claim is `unreachable`.

The decided fix: decide on the paths `mutate` READ as well as wrote. If any path
read or written is outside the repo, return the working-tree answer locally,
whatever its kind.

Pinned here (every row of the reviewer's table). Each case spies on
`KanbanService._git_run` and asserts no fetch, ls-remote or push, plus the local
answer:

1. Claim of an external item held by agent-B, origin reachable and unreachable:
   `refused` "held by agent-B" (never "not found on origin", never `unreachable`).
2. Re-claim of an external item already agent-A's, origin unreachable: `noop`, exit 0.
3. `update_item_push` no-op on an external item (the title it already has), origin
   unreachable (and reachable): `noop`.
4. `update_item_push --add-depends-on EXP-777` (unknown) on an external item, origin
   reachable (and unreachable): `refused` "is on no board".

Controls: with the external board configured, an in-repo update is `won` through the
compare-and-swap, and an in-repo claim that origin holds for agent-B (the working
tree still says ready) is refused by origin's answer, after a fetch.
(Round 2's `test_claim_in_repo_item_with_external_board_is_won` covers an in-repo claim.)
"""

from __future__ import annotations

import importlib
from pathlib import Path
from types import ModuleType

import pytest

from tests.issues.test_574_sync_and_push import ITEM, Recorder, _seed_item, commit_files, service
from tests.issues.test_585_create_push_loop import World, git
from tests.issues.test_590_next_id_and_hdd_ids import b_push
from tests.issues.test_805_external_claim import (
    ACTOR,
    EXT_TEXT,
    IN_ITEM,
    IN_TEXT,
    NETWORK,
    _ext_item,
    _record_git,
)

HELD_BY_B = EXT_TEXT.replace("status: ready", "status: in_progress\nassignee: agent-B")
MINE = EXT_TEXT.replace("status: ready", "status: in_progress\nassignee: agent-A")
ORIGIN = ["reachable", "unreachable"]


@pytest.fixture
def sync() -> ModuleType:
    return importlib.import_module("yurtle_kanban.sync")


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    """The #574 PR A world (origin and both clones hold EXP-001), agent-A acting."""
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    monkeypatch.setenv("YURTLE_AGENT", ACTOR)
    w = World(tmp_path)
    _seed_item(w)
    return w


def _external(world: World, text: str, origin: str) -> Path:
    """EXP-900 with `text` on the external board; origin broken when "unreachable"."""
    target = _ext_item(world)
    target.write_text(text)
    if origin == "unreachable":
        git(world.a, "remote", "set-url", "origin", str(world.a.parent / "missing.git"))
    return target


def _no_network(verbs: list[str]) -> None:
    assert not NETWORK & set(verbs), f"origin was contacted: {verbs}"


def _local_answer(out, kind: str) -> None:
    assert out.kind == kind, out.message
    low = out.message.lower()
    assert "not found on origin" not in low, out.message
    assert "fetch" not in low, out.message


# --- 1. held by another: refused locally -------------------------------------------------


@pytest.mark.parametrize("origin", ORIGIN)
def test_claim_external_item_held_by_other_is_refused_locally(world, monkeypatch, origin) -> None:
    target = _external(world, HELD_BY_B, origin)
    remote_before = world.remote_sha()
    verbs = _record_git(monkeypatch)

    out = service(world).claim_item("EXP-900", actor=ACTOR, sleep=lambda s: None)

    _local_answer(out, "refused")
    assert out.exit_code == 1
    assert "held by agent-b" in out.message.lower(), out.message
    assert target.read_text() == HELD_BY_B, "a refused claim changed the item"
    assert world.remote_sha() == remote_before
    _no_network(verbs)


# --- 2. already mine, offline: noop ----------------------------------------------------------


@pytest.mark.parametrize("origin", ORIGIN)
def test_reclaim_external_item_already_mine_is_noop_locally(world, monkeypatch, origin) -> None:
    target = _external(world, MINE, origin)
    verbs = _record_git(monkeypatch)

    out = service(world).claim_item("EXP-900", actor=ACTOR, sleep=lambda s: None)

    _local_answer(out, "noop")
    assert out.exit_code == 0
    assert target.read_text() == MINE
    _no_network(verbs)


# --- 3. update --push no-op, offline: noop -------------------------------------------------


@pytest.mark.parametrize("origin", ORIGIN)
def test_update_push_external_noop_is_noop_locally(world, monkeypatch, origin) -> None:
    target = _external(world, EXT_TEXT, origin)
    verbs = _record_git(monkeypatch)

    out = service(world).update_item_push("EXP-900", title="Out", sleep=lambda s: None)

    _local_answer(out, "noop")
    assert out.exit_code == 0
    assert target.read_text() == EXT_TEXT
    _no_network(verbs)


# --- 4. update --push with an unknown dependency: refused "is on no board" --------------------


@pytest.mark.parametrize("origin", ORIGIN)
def test_update_push_external_unknown_dep_is_refused_locally(world, monkeypatch, origin) -> None:
    target = _external(world, EXT_TEXT, origin)
    verbs = _record_git(monkeypatch)

    out = service(world).update_item_push(
        "EXP-900", add_depends_on=["EXP-777"], sleep=lambda s: None
    )

    _local_answer(out, "refused")
    assert out.exit_code == 1
    assert "is on no board" in out.message, out.message
    assert "EXP-777" in out.message, out.message
    assert target.read_text() == EXT_TEXT
    _no_network(verbs)


# --- controls: in-repo items keep the compare-and-swap ---------------------------------------


def test_update_push_in_repo_with_external_board_is_won(world, monkeypatch) -> None:
    _ext_item(world)
    base = world.remote_sha()
    verbs = _record_git(monkeypatch)
    rec = Recorder()

    out = service(world).update_item_push(
        "EXP-001", title="Renamed", sleep=rec.sleep, jitter=rec.jitter, seam=rec.seam
    )

    assert out.kind == "won", out.message
    tip = world.remote_sha()
    assert out.sha == tip
    assert git(world.remote, "rev-parse", f"{tip}^").strip() == base
    assert commit_files(world.remote, tip) == [ITEM]
    assert "Renamed" in world.remote_show(ITEM)
    assert rec.seams == [0]
    assert {"fetch", "push"} <= set(verbs), verbs


def test_claim_in_repo_refusal_with_external_board_comes_from_origin(world, monkeypatch) -> None:
    """The working tree says EXP-002 is ready; origin says agent-B holds it. An
    in-repo item is judged on origin's tree: refused 'held by agent-B', after a fetch."""
    _ext_item(world)
    (world.a / IN_ITEM).write_text(IN_TEXT)
    git(world.a, "add", IN_ITEM)
    git(world.a, "commit", "-m", "seed EXP-002")
    git(world.a, "push", "origin", f"HEAD:refs/heads/{world.default}")
    b_push(
        world, {IN_ITEM: IN_TEXT.replace("status: ready", "status: in_progress\nassignee: agent-B")}
    )
    b_tip = world.remote_sha()
    verbs = _record_git(monkeypatch)

    out = service(world).claim_item("EXP-002", actor=ACTOR, sleep=lambda s: None)

    assert out.kind == "refused", out.message
    assert "held by agent-b" in out.message.lower(), out.message
    assert "fetch" in verbs, verbs
    assert world.remote_sha() == b_tip
    assert (world.a / IN_ITEM).read_text() == IN_TEXT
