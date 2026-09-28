"""Issue #805, round 2 (the PR #820 review): a claim on an EXTERNAL board stays local.

With a multi-board config where one board lies outside the git repository, PR #820
first ran `mutate` against origin's tree. That tree can't hold an external file, so
`claim_item` of an external-board item was refused ("Item not found on origin"), or
"unreachable" when origin was down. origin/main gave `local` in both cases.

The decided fix: decide before fetching. When a board is outside the repo, run
`mutate` once against the working tree; if the Change names a file outside the
repo, go straight to local mode, without touching origin. Otherwise run the
compare-and-swap as now.

Pinned here:

1. `claim_item` of an external-board item with origin reachable: `local`, the item is
   written with the claim, origin is unchanged, and no fetch/push/ls-remote ran.
2. The same with origin pointing at a missing path: `local`, and no network git
   command ran (so no timeout can be waited out).
3. A generic `sync_and_push` whose `mutate` READS the external file (by its path
   relative to the work tree, as `claim` names it) and then writes it: `local` with a
   remote configured. This covers the gap in round 1's
   `test_external_board_change_goes_local`, whose mutate wrote without reading.
4. Control: an in-repo claim with the external board also configured is still `won`
   through the compare-and-swap, and origin gets a commit touching only that item.
"""

from __future__ import annotations

import importlib
import os
import re
import subprocess
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tests.issues.test_574_sync_and_push import (
    LINE,
    Recorder,
    _seed_item,
    commit_files,
    run,
    service,
)
from tests.issues.test_585_create_push_loop import EXP_DIR, World, git
from tests.issues.test_805_sync_polish import _multi_board_world
from yurtle_kanban.service import KanbanService

ACTOR = "agent-A"
EXT_TEXT = '---\nid: EXP-900\ntitle: "Out"\ntype: expedition\nstatus: ready\n---\n\n# Out\n'
IN_ITEM = f"{EXP_DIR}/EXP-002-in.md"
IN_TEXT = '---\nid: EXP-002\ntitle: "In"\ntype: expedition\nstatus: ready\n---\n\n# In\n'
NETWORK = {"fetch", "push", "ls-remote", "pull", "remote-show"}


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


def _ext_item(world: World) -> Path:
    """The multi-board world plus EXP-900 (ready) on the external board."""
    ext = _multi_board_world(world)
    (ext / "expeditions").mkdir(exist_ok=True)
    target = ext / "expeditions" / "EXP-900-out.md"
    target.write_text(EXT_TEXT)
    return target


def _record_git(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record the verb of every git command `KanbanService._git_run` runs."""
    real = KanbanService._git_run
    verbs: list[str] = []

    def spy(self: KanbanService, *args: str, **kw: Any) -> subprocess.CompletedProcess[str]:
        verbs.append(args[0] if args else "")
        return real(self, *args, **kw)

    monkeypatch.setattr(KanbanService, "_git_run", spy)
    return verbs


def _claimed(text: str) -> bool:
    """The item's frontmatter names ACTOR as assignee and left `ready`."""
    front = text.split("---", 2)[1]
    return bool(re.search(rf"^assignee:\s*['\"]?{ACTOR}['\"]?\s*$", front, re.M)) and not re.search(
        r"^status:\s*ready\s*$", front, re.M
    )


# --- 1. external-board claim, origin reachable ----------------------------------------


def test_claim_external_item_with_origin_reachable_is_local(world, monkeypatch) -> None:
    target = _ext_item(world)
    remote_before = world.remote_sha()
    verbs = _record_git(monkeypatch)

    out = service(world).claim_item("EXP-900", actor=ACTOR, sleep=lambda s: None)

    assert out.kind == "local", out.message
    assert out.exit_code == 0
    assert _claimed(target.read_text()), target.read_text()
    assert world.remote_sha() == remote_before, "origin changed"
    assert not NETWORK & set(verbs), f"origin was touched: {verbs}"
    assert "no remote" not in out.message.lower(), out.message


# --- 2. external-board claim, origin unreachable ----------------------------------------


def test_claim_external_item_with_origin_unreachable_is_local(world, monkeypatch) -> None:
    target = _ext_item(world)
    git(world.a, "remote", "set-url", "origin", str(world.a.parent / "missing.git"))
    verbs = _record_git(monkeypatch)

    out = service(world).claim_item("EXP-900", actor=ACTOR, sleep=lambda s: None)

    assert out.kind == "local", out.message
    assert out.exit_code == 0
    assert _claimed(target.read_text()), target.read_text()
    assert not NETWORK & set(verbs), f"origin was contacted: {verbs}"


# --- 3. a generic mutate that reads the external file, then writes it ------------------


def test_sync_and_push_reading_external_file_is_local(world, sync, monkeypatch) -> None:
    target = _ext_item(world)
    top = Path(git(world.a, "rev-parse", "--show-toplevel").strip())
    rel = Path(os.path.relpath(target, top)).as_posix()  # "../ext-board/...", as claim names it
    remote_before = world.remote_sha()
    verbs = _record_git(monkeypatch)
    rec = Recorder()

    def mutate(read: Any, attempt: int) -> Any:
        text = read(rel)
        if text is None:
            return sync.Refuse(f"{rel} not found")
        return sync.Change({rel: text + LINE + "\n"}, "append to EXP-900")

    out = run(world, mutate, rec)

    assert out.kind == "local", out.message
    assert out.exit_code == 0
    assert target.read_text() == EXT_TEXT + LINE + "\n"
    assert world.remote_sha() == remote_before
    assert rec.seams == [], "the compare-and-swap ran for an external file"
    assert not NETWORK & set(verbs), f"origin was touched: {verbs}"
    low = out.message.lower()
    assert "no remote" not in low, out.message
    assert "outside" in low and "repositor" in low, out.message


# --- 4. control: an in-repo claim with an external board configured is won ---------------


def test_claim_in_repo_item_with_external_board_is_won(world) -> None:
    _ext_item(world)
    (world.a / IN_ITEM).write_text(IN_TEXT)
    git(world.a, "add", IN_ITEM)
    git(world.a, "commit", "-m", "seed EXP-002")
    git(world.a, "push", "origin", f"HEAD:refs/heads/{world.default}")
    base = world.remote_sha()
    rec = Recorder()

    out = service(world).claim_item(
        "EXP-002", actor=ACTOR, sleep=rec.sleep, jitter=rec.jitter, seam=rec.seam
    )

    assert out.kind == "won", out.message
    assert out.exit_code == 0
    tip = world.remote_sha()
    assert out.sha == tip
    assert git(world.remote, "rev-parse", f"{tip}^").strip() == base
    assert commit_files(world.remote, tip) == [IN_ITEM]
    assert _claimed(world.remote_show(IN_ITEM)), world.remote_show(IN_ITEM)
    assert rec.seams == [0], "the claim did not go through the compare-and-swap"
