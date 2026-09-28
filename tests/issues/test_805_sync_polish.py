"""Issue #805: ``sync_and_push`` polish (follow-ups from the #798 review of #574 PR A).

The [steer] on #805 (bucket 1) settles six items; each has red tests here:

1. A ``Change`` whose files equal the base's text is ``noop`` (exit 0): nothing is
   pushed, origin is unchanged.
2. Local mode (no remote): when ``_commit_paths`` makes no commit, the outcome is
   ``noop``, not a "committed here" note. A pre-commit refusal is ``refused`` and
   says the edit is kept in the working tree.
3. A remote exists but a board is outside the repo: the note must not say
   "no remote"; it names the board being outside the repository.
4. Local mode is decided per Change: in a multi-board config with one external
   board, a Change touching only an in-repo file keeps the compare-and-swap
   (``won``, origin gets the commit); a Change touching the external board's file
   goes local.
5. ``_commit_on`` takes the refusal suffix: ``sync_and_push``'s pre-commit refusal
   ends "(nothing was changed)"; ``create --push`` keeps "(nothing was created)"
   (a control).
6. A timeout reports the real attempt count, not 0.

Harness: the World/seam harness of tests/issues/test_574_sync_and_push.py.

Readings (the test partner's; the driver may challenge):

a. Item 3 is pinned by monkeypatching ``_board_outside_repo`` to True with an
   in-repo Change. After the per-Change fix the outcome may be ``won`` (the file is
   in the repo); whatever it is, it must not say "no remote", and if it is ``local``
   it must name the repository ("outside" + "repositor").
b. A file on an external board is named in the ``Change`` by its absolute path
   (``top / rel`` keeps an absolute ``rel``).
c. World clones set ``core.hooksPath=/dev/null``; the hook tests point it at a
   directory holding a refusing ``pre-commit``.
"""

from __future__ import annotations

import importlib
import subprocess
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tests.issues.test_574_sync_and_push import (
    ITEM,
    ITEM_TEXT,
    LINE,
    Appender,
    Recorder,
    _seed_item,
    rival,
    run,
    service,
    snapshot,
)
from tests.issues.test_585_create_push_loop import World, git
from tests.issues.test_590_next_id_and_hdd_ids import b_push
from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.models import WorkItemType
from yurtle_kanban.service import KanbanService

HOOK_SAYS = "kanban-805-hook: not today"


@pytest.fixture
def sync() -> ModuleType:
    return importlib.import_module("yurtle_kanban.sync")


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    """The #574 PR A world: origin and both clones hold EXP-001."""
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    w = World(tmp_path)
    _seed_item(w)
    return w


def _refusing_hook(clone: Path) -> Path:
    """A pre-commit hook that refuses, in a hooks dir the clone uses; returns the dir."""
    hooks = clone.parent / f"{clone.name}-hooks"
    hooks.mkdir(exist_ok=True)
    hook = hooks / "pre-commit"
    hook.write_text(f"#!/bin/sh\necho '{HOOK_SAYS}' >&2\nexit 1\n")
    hook.chmod(0o755)
    git(clone, "config", "core.hooksPath", str(hooks))
    return hooks


def _no_remote(world: World) -> None:
    git(world.a, "remote", "remove", "origin")


def _flat(text: str) -> str:
    return " ".join(text.split())


# --- 1. a Change equal to the base is noop ----------------------------------------------


def test_change_equal_to_base_is_noop(world, sync) -> None:
    remote_before = world.remote_sha()
    before_local = snapshot(world.a)
    rec = Recorder()

    def mutate(read: Any, attempt: int) -> Any:
        text = read(ITEM)
        assert text == ITEM_TEXT
        return sync.Change({ITEM: text}, "rewrite EXP-001 unchanged")

    out = run(world, mutate, rec)

    assert out.kind == "noop", out.message
    assert out.exit_code == 0
    assert out.sha is None
    assert world.remote_sha() == remote_before, "an empty commit was pushed"
    assert snapshot(world.a) == before_local


def test_change_equal_to_base_after_lost_attempt_is_noop(world, sync) -> None:
    """Attempt 0 loses to B writing the very text we want; attempt 1 sees it there."""
    target = ITEM_TEXT + LINE + "\n"
    rec = Recorder(lambda attempt: b_push(world, {ITEM: target}) if attempt == 0 else None)

    out = run(world, lambda read, attempt: sync.Change({ITEM: target}, "set EXP-001"), rec)

    b_tip = world.remote_sha()
    assert out.kind == "noop", out.message
    assert out.exit_code == 0
    assert world.remote_sha() == b_tip, "an empty commit was pushed on top of B's"


# --- 2. local mode: no commit made is noop; a hook refusal keeps the edit -------------------


def test_local_no_commit_is_noop(world, sync) -> None:
    _no_remote(world)
    head_before = git(world.a, "rev-parse", "HEAD").strip()
    before_local = snapshot(world.a)

    out = run(world, lambda read, attempt: sync.Change({ITEM: ITEM_TEXT}, "no change"), Recorder())

    assert out.kind == "noop", out.message
    assert out.exit_code == 0
    assert out.sha is None
    assert "committed here" not in out.message, out.message
    assert git(world.a, "rev-parse", "HEAD").strip() == head_before
    assert snapshot(world.a) == before_local


def test_local_hook_refusal_says_edit_kept_in_working_tree(world, sync) -> None:
    _no_remote(world)
    _refusing_hook(world.a)
    head_before = git(world.a, "rev-parse", "HEAD").strip()

    out = run(world, Appender(sync), Recorder())

    assert out.kind == "refused", out.message
    assert out.exit_code == 1
    assert git(world.a, "rev-parse", "HEAD").strip() == head_before
    assert (world.a / ITEM).read_text() == ITEM_TEXT + LINE + "\n", "the edit was not kept"
    flat = _flat(out.message).lower()
    assert "kept" in flat and "working tree" in flat, out.message
    assert HOOK_SAYS in out.message, out.message


# --- 3. remote configured, a board outside the repo: the note names the real reason ---------


def test_board_outside_repo_with_remote_does_not_say_no_remote(world, sync, monkeypatch) -> None:
    monkeypatch.setattr(KanbanService, "_board_outside_repo", lambda self: True)

    out = run(world, Appender(sync), Recorder())

    assert out.exit_code == 0, out.message
    assert "no remote" not in out.message.lower(), out.message
    if out.kind == "local":
        low = out.message.lower()
        assert "outside" in low and "repositor" in low, out.message


# --- 4. local mode is decided per Change ------------------------------------------------------


def _multi_board_world(world: World) -> Path:
    """A's config becomes multi-board: the in-repo nautical board plus an external
    one outside the clone; committed and pushed. Returns the external root."""
    ext = (world.a.parent / "ext-board").resolve()
    ext.mkdir()
    (world.a / ".kanban" / "config.yaml").write_text(
        'version: "2.0"\n'
        "boards:\n"
        "  - name: main\n"
        "    preset: nautical\n"
        '    path: "kanban-work/"\n'
        "  - name: outside\n"
        "    preset: nautical\n"
        f'    path: "{ext}/"\n'
        "default_board: main\n"
    )
    git(world.a, "add", "-A")
    git(world.a, "commit", "-m", "multi-board config")
    git(world.a, "push", "origin", f"HEAD:refs/heads/{world.default}")
    cfg = KanbanConfig.load(world.a / ".kanban" / "config.yaml")
    assert cfg.is_multi_board, "the multi-board config did not load as multi-board"
    return ext


def test_in_repo_change_keeps_cas_with_an_external_board(world, sync) -> None:
    _multi_board_world(world)
    base = world.remote_sha()
    rec = Recorder()

    out = run(world, Appender(sync), rec)

    assert out.kind == "won", out.message
    assert out.exit_code == 0
    tip = world.remote_sha()
    assert out.sha == tip
    assert git(world.remote, "rev-parse", f"{tip}^").strip() == base
    assert world.remote_show(ITEM) == ITEM_TEXT + LINE + "\n"
    assert rec.seams == [0], "the compare-and-swap's seam never ran"


def test_in_repo_change_with_external_board_races_and_retries(world, sync) -> None:
    """Race protection kept: a rival on attempt 0 makes it retry on a fresh base."""
    _multi_board_world(world)
    rec = Recorder(lambda attempt: rival(world) if attempt == 0 else None)

    out = run(world, Appender(sync), rec)

    assert out.kind == "won", out.message
    assert rec.seams == [0, 1]
    assert "other-0.txt" in world.remote_files()
    assert world.remote_show(ITEM) == ITEM_TEXT + LINE + "\n"


def test_external_board_change_goes_local(world, sync) -> None:
    ext = _multi_board_world(world)
    remote_before = world.remote_sha()
    target = ext / "EXP-900-outside.md"
    text = '---\nid: EXP-900\ntitle: "Out"\ntype: expedition\nstatus: backlog\n---\n'
    rec = Recorder()

    out = run(world, lambda read, attempt: sync.Change({str(target): text}, "write EXP-900"), rec)

    assert out.kind == "local", out.message
    assert out.exit_code == 0
    assert target.read_text() == text
    assert world.remote_sha() == remote_before, "an external board's file reached origin"
    assert rec.seams == []
    low = out.message.lower()
    assert "no remote" not in low, out.message
    assert "outside" in low and "repositor" in low, out.message


# --- 5. the refusal suffix -----------------------------------------------------------------------


def test_sync_and_push_hook_refusal_says_nothing_was_changed(world, sync) -> None:
    _refusing_hook(world.a)
    remote_before = world.remote_sha()
    before_local = snapshot(world.a)

    out = run(world, Appender(sync), Recorder())

    assert out.kind == "refused", out.message
    assert out.exit_code == 1
    assert HOOK_SAYS in out.message, out.message
    assert out.message.rstrip().endswith("(nothing was changed)"), out.message
    assert "nothing was created" not in out.message, out.message
    assert world.remote_sha() == remote_before
    assert snapshot(world.a) == before_local


def test_create_push_hook_refusal_still_says_nothing_was_created(world, monkeypatch) -> None:
    """Control: create --push keeps its own wording."""
    _refusing_hook(world.a)
    monkeypatch.setenv("YURTLE_AGENT", "agent-A")
    remote_before = world.remote_sha()

    result = service(world).create_item_and_push(WorkItemType.EXPEDITION, "Hooked")

    assert not result["success"], result
    assert HOOK_SAYS in result["message"], result["message"]
    assert result["message"].rstrip().endswith("(nothing was created)"), result["message"]
    assert world.remote_sha() == remote_before


# --- 6. timeouts report the real attempt count -----------------------------------------------------


def _timeout_on(monkeypatch: pytest.MonkeyPatch, verb: str, from_call: int = 1) -> list[int]:
    """Make KanbanService._git_run raise TimeoutExpired on the `from_call`-th `verb`
    and after; returns the running count of `verb` calls."""
    real = KanbanService._git_run
    calls = [0]

    def fake(self, *args: str, **kw: Any) -> subprocess.CompletedProcess[str]:
        if args and args[0] == verb:
            calls[0] += 1
            if calls[0] >= from_call:
                raise subprocess.TimeoutExpired(["git", *args], 30)
        return real(self, *args, **kw)

    monkeypatch.setattr(KanbanService, "_git_run", fake)
    return calls


def test_push_timeout_on_first_attempt_reports_one_attempt(world, sync, monkeypatch) -> None:
    _timeout_on(monkeypatch, "push")

    out = run(world, Appender(sync), Recorder())

    assert out.kind == "unreachable", out.message
    assert out.attempts == 1, f"attempts={out.attempts}: {out.message}"


def test_push_timeout_after_a_lost_attempt_reports_two(world, sync, monkeypatch) -> None:
    """Attempt 0's push is rejected (a rival in the seam), attempt 1's push times out."""
    _timeout_on(monkeypatch, "push", from_call=2)
    rec = Recorder(lambda attempt: rival(world) if attempt == 0 else None)

    out = run(world, Appender(sync), rec)

    assert rec.seams == [0, 1]
    assert out.kind == "unreachable", out.message
    assert out.attempts == 2, f"attempts={out.attempts}: {out.message}"


def test_fetch_timeout_reports_one_attempt(world, sync, monkeypatch) -> None:
    _timeout_on(monkeypatch, "fetch")

    out = run(world, Appender(sync), Recorder())

    assert out.kind == "unreachable", out.message
    assert out.attempts == 1, f"attempts={out.attempts}: {out.message}"
