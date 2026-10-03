"""Issue #1255: a simultaneous push's ref-lock rejection is a lost race, not a refusal.

When two clones push to origin at the same instant, git may reject the loser with

    ! [remote rejected] main -> main (cannot lock ref 'refs/heads/main': is at X but expected Y)
    ! [remote rejected] main -> main (incorrect old value provided)

instead of ``! [rejected] ... (fetch first)`` / ``(non-fast-forward)``. Both
compare-and-swap loops in ``service.py`` (``sync_and_push`` and ``_race_to_branch``)
only retried the latter, so the former became ``push_refused`` (exit 6) and the
loser's write was dropped.

Decided shape: one shared predicate makes a ``[remote rejected]`` line naming
``cannot lock ref`` or ``incorrect old value provided`` a lost race; both loops
re-fetch and retry exactly as for a non-fast-forward (same sleep, jitter, attempt
count). Any other ``[remote rejected]`` (hook declined, protected branch, denied)
stays ``push_refused``.

Scenarios (real git: the #585 ``World``, a bare remote plus clones A and B):

1. ``sync_and_push`` through ``update_item_push``: the FIRST push is faked to fail
   with each ref-lock wording (no real push happens) -> retried, ``won``, attempts 2,
   one jittered sleep, the edit on origin.
2. ``_race_to_branch`` through ``create --push`` and ``allocate_next_id``: the same
   fake first push -> retried and created / allocated on origin.
3. Control: a ``[remote rejected] ... (pre-receive hook declined)`` stays
   ``push_refused`` (exit 6) after ONE push, no sleep; ``create --push`` and
   ``allocate_next_id`` fail after one push. (A real declining ``update`` hook is
   covered by test_574_sync_and_push.py::test_update_hook_refusal_is_push_refused_once.)
4. Real concurrency: clones A and B each run ``update_item_push`` on EXP-001 (A adds
   a tag, B sets the title) in two threads released together by a barrier just
   before attempt 0's push, for several rounds. Both edits land on origin every
   round. Jitter is tiny; attempts are the default.
"""

from __future__ import annotations

import random
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from tests.issues.test_574_sync_and_push import ITEM, JITTER, Recorder
from tests.issues.test_574_sync_and_push import world as world  # noqa: F401 (fixture)
from tests.issues.test_585_create_push_loop import (
    _ORIG_RUN,
    TITLE,
    World,
    _is_git_push,
    _items_titled,
    git,
    output_of,
    porcelain,
)
from yurtle_kanban.cli import main
from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.service import KanbanService

ITEM_ID = "EXP-001"

LOCK_REF = (
    "cannot lock ref 'refs/heads/main': is at "
    "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa but expected "
    "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
)
OLD_VALUE = "incorrect old value provided"
HOOK_DECLINED = "pre-receive hook declined"

# the third is what a local bare remote really printed in this test's concurrency run
REAL_LOCAL = "remote-error+" + OLD_VALUE
RACE_REASONS = {
    "cannot_lock_ref": LOCK_REF,
    "incorrect_old_value": OLD_VALUE,
    "real_local_output": REAL_LOCAL,
}


def _stderr(remote: Path, reason: str) -> str:
    lead = ""
    if reason == REAL_LOCAL:
        lead = f"remote: error: {LOCK_REF}        \n"
        reason = OLD_VALUE
    return (
        f"{lead}To {remote}\n"
        f" ! [remote rejected] 0123456789ab -> main ({reason})\n"
        f"error: failed to push some refs to '{remote}'\n"
    )


class FakeFirstPushes:
    """Wrap subprocess.run: the first ``fail`` git pushes return rc 1 with a
    ``[remote rejected] ... (reason)`` stderr WITHOUT pushing; later ones run."""

    def __init__(self, remote: Path, reason: str, fail: int = 1, limit: int = 25) -> None:
        self.remote = remote
        self.reason = reason
        self.fail = fail
        self.limit = limit
        self.pushes = 0

    def __call__(self, *args: Any, **kwargs: Any) -> subprocess.CompletedProcess:
        cmd = args[0] if args else kwargs.get("args")
        if _is_git_push(cmd):
            self.pushes += 1
            if self.pushes > self.limit:
                raise RuntimeError(f"unbounded push loop: {self.pushes} pushes")
            if self.pushes <= self.fail:
                err = _stderr(self.remote, self.reason)
                if not kwargs.get("text", False) and not kwargs.get("universal_newlines"):
                    return subprocess.CompletedProcess(cmd, 1, b"", err.encode())
                return subprocess.CompletedProcess(cmd, 1, "", err)
        return _ORIG_RUN(*args, **kwargs)


def service_at(clone: Path) -> KanbanService:
    return KanbanService(KanbanConfig.load(clone / ".kanban" / "config.yaml"), clone)


# --- 1. sync_and_push: a ref-lock rejection is retried and wins --------------------------


@pytest.mark.parametrize("reason", list(RACE_REASONS))
def test_sync_and_push_retries_ref_lock_rejection(world, monkeypatch, reason) -> None:
    fake = FakeFirstPushes(world.remote, RACE_REASONS[reason])
    monkeypatch.setattr(subprocess, "run", fake)
    base = world.remote_sha()
    rec = Recorder()

    out = service_at(world.a).update_item_push(
        ITEM_ID, title="Raced", sleep=rec.sleep, jitter=rec.jitter, seam=rec.seam
    )

    assert out.kind == "won", f"{out.kind} (exit {out.exit_code}): {out.message}"
    assert out.exit_code == 0
    assert out.attempts >= 2, out.attempts
    assert fake.pushes == 2, f"expected one retry, got {fake.pushes} pushes"
    assert rec.seams == [0, 1]
    assert rec.jitters == [(0.1, 1.0)], "not retried like a non-fast-forward"
    assert rec.sleeps == [pytest.approx(JITTER * 1)]
    assert out.sha == world.remote_sha() != base
    assert 'title: "Raced"' in world.remote_show(ITEM) or "title: Raced" in world.remote_show(
        ITEM
    ), world.remote_show(ITEM)


@pytest.mark.parametrize("reason", list(RACE_REASONS))
def test_sync_and_push_ref_lock_every_attempt_is_busy(world, monkeypatch, reason) -> None:
    """A ref-lock rejection on every attempt exhausts the default attempts as a lost
    race does: ``busy`` (exit 5), never ``push_refused``."""
    fake = FakeFirstPushes(world.remote, RACE_REASONS[reason], fail=99)
    monkeypatch.setattr(subprocess, "run", fake)
    base = world.remote_sha()
    rec = Recorder()

    out = service_at(world.a).update_item_push(
        ITEM_ID, title="Raced", sleep=rec.sleep, jitter=rec.jitter, seam=rec.seam
    )

    assert out.kind == "busy", f"{out.kind} (exit {out.exit_code}): {out.message}"
    assert out.exit_code == 5
    assert fake.pushes == 5 and out.attempts == 5
    assert world.remote_sha() == base


# --- 2. _race_to_branch: create --push and next-id retry and win -------------------------


def _fresh_world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    return World(tmp_path)


@pytest.mark.parametrize("reason", list(RACE_REASONS))
def test_create_push_retries_ref_lock_rejection(tmp_path, monkeypatch, reason) -> None:
    w = _fresh_world(tmp_path, monkeypatch)
    fake = FakeFirstPushes(w.remote, RACE_REASONS[reason])
    monkeypatch.setattr(subprocess, "run", fake)
    monkeypatch.chdir(w.a)

    result = CliRunner().invoke(main, ["create", "expedition", TITLE, "--push"])
    out = output_of(result)

    assert result.exit_code == 0, f"exit {result.exit_code}: {out}"
    assert fake.pushes == 2, f"expected one retry, got {fake.pushes} pushes: {out}"
    assert len(_items_titled(w, TITLE)) == 1, w.remote_items()
    assert list(w.remote_items()) == ["EXP-001"]
    assert porcelain(w.a) == []


@pytest.mark.parametrize("reason", list(RACE_REASONS))
def test_allocate_next_id_retries_ref_lock_rejection(tmp_path, monkeypatch, reason) -> None:
    w = _fresh_world(tmp_path, monkeypatch)
    fake = FakeFirstPushes(w.remote, RACE_REASONS[reason])
    monkeypatch.setattr(subprocess, "run", fake)
    base = w.remote_sha()

    result = service_at(w.a).allocate_next_id("EXP")

    assert result["success"], result["message"]
    assert result["recorded"] == "pushed"
    assert result["id"] == "EXP-001"
    assert fake.pushes == 2, f"expected one retry, got {fake.pushes} pushes"
    assert w.remote_sha() != base
    assert "_ID_ALLOCATIONS.json" in git(
        w.remote, "diff-tree", "--no-commit-id", "--name-only", "-r", "main"
    )


# --- 3. control: another [remote rejected] stays a refusal, no retry -------------------


def test_sync_and_push_hook_declined_stays_push_refused(world, monkeypatch) -> None:
    fake = FakeFirstPushes(world.remote, HOOK_DECLINED, fail=99)
    monkeypatch.setattr(subprocess, "run", fake)
    base = world.remote_sha()
    rec = Recorder()

    out = service_at(world.a).update_item_push(
        ITEM_ID, title="Raced", sleep=rec.sleep, jitter=rec.jitter, seam=rec.seam
    )

    assert out.kind == "push_refused", f"{out.kind}: {out.message}"
    assert out.exit_code == 6
    assert fake.pushes == 1, "a hook refusal was retried"
    assert out.attempts == 1
    assert rec.sleeps == [] and rec.seams == [0]
    assert HOOK_DECLINED in out.message
    assert world.remote_sha() == base


def test_create_push_hook_declined_is_not_retried(tmp_path, monkeypatch) -> None:
    w = _fresh_world(tmp_path, monkeypatch)
    fake = FakeFirstPushes(w.remote, HOOK_DECLINED, fail=99)
    monkeypatch.setattr(subprocess, "run", fake)
    monkeypatch.chdir(w.a)

    result = CliRunner().invoke(main, ["create", "expedition", TITLE, "--push"])
    out = output_of(result)

    assert result.exit_code != 0, out
    assert fake.pushes == 1, "a hook refusal was retried"
    assert HOOK_DECLINED in out
    assert w.remote_items() == {}


def test_allocate_next_id_hook_declined_is_not_retried(tmp_path, monkeypatch) -> None:
    w = _fresh_world(tmp_path, monkeypatch)
    fake = FakeFirstPushes(w.remote, HOOK_DECLINED, fail=99)
    monkeypatch.setattr(subprocess, "run", fake)
    base = w.remote_sha()

    result = service_at(w.a).allocate_next_id("EXP")

    assert not result["success"], result
    assert fake.pushes == 1, "a hook refusal was retried"
    assert HOOK_DECLINED in result["message"]
    assert w.remote_sha() == base


# --- 4. real concurrency: two clones push at once, both land ---------------------------

ROUNDS = 8


class PushLog:
    """Wrap subprocess.run (thread-safe): record every git push's rc and output."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.pushes: list[tuple[int, str]] = []

    def __call__(self, *args: Any, **kwargs: Any) -> subprocess.CompletedProcess:
        done = _ORIG_RUN(*args, **kwargs)
        cmd = args[0] if args else kwargs.get("args")
        if _is_git_push(cmd):
            said = done.stderr if isinstance(done.stderr, str) else (done.stderr or b"").decode()
            with self.lock:
                self.pushes.append((done.returncode, said))
        return done


def _tiny_jitter(lo: float, hi: float) -> float:
    return random.uniform(0.0, 0.01)


def test_two_clones_pushing_at_once_both_land(world, monkeypatch, capsys) -> None:
    log = PushLog()
    monkeypatch.setattr(subprocess, "run", log)
    ref_lock_rounds = 0
    nff_rounds = 0
    failures: list[str] = []

    for n in range(ROUNDS):
        barrier = threading.Barrier(2, timeout=30)
        results: dict[str, Any] = {}
        start = len(log.pushes)

        def seam(attempt: int) -> None:
            if attempt == 0:
                barrier.wait()  # both commits are built: push together

        def a_edit(n: int = n) -> None:
            results["A"] = service_at(world.a).update_item_push(
                ITEM_ID, add_tags=[f"round-{n}-a"],
                sleep=time.sleep, jitter=_tiny_jitter, seam=seam,
            )

        def b_edit(n: int = n) -> None:
            results["B"] = service_at(world.b).update_item_push(
                ITEM_ID, title=f"Round {n} B",
                sleep=time.sleep, jitter=_tiny_jitter, seam=seam,
            )

        threads = [threading.Thread(target=a_edit), threading.Thread(target=b_edit)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=120)
        assert not any(t.is_alive() for t in threads), "a writer hung"

        said = "\n".join(err for _, err in log.pushes[start:])
        if "cannot lock ref" in said or OLD_VALUE in said:
            ref_lock_rounds += 1
        elif "[rejected]" in said:
            nff_rounds += 1

        text = world.remote_show(ITEM)
        for who, out in sorted(results.items()):
            if out.kind != "won":
                failures.append(f"round {n}: {who} {out.kind} (exit {out.exit_code}): "
                                f"{out.message}")
        if f"round-{n}-a" not in text:
            failures.append(f"round {n}: A's tag is not on origin")
        if f"Round {n} B" not in text:
            failures.append(f"round {n}: B's title is not on origin")

    with capsys.disabled():
        print(
            f"\n[#1255 concurrency] {ROUNDS} rounds: ref-lock rejection in "
            f"{ref_lock_rounds}, non-fast-forward rejection in {nff_rounds}, "
            f"failures {len(failures)}"
        )
    assert not failures, "\n".join(failures)
    # ruled edit (#1262): a runner whose pushes never collided proved nothing about
    # the ref-lock race; say so instead of passing vacuously
    if ref_lock_rounds == 0:
        pytest.skip(f"no ref-lock collision in {ROUNDS} rounds on this runner (#1262)")
