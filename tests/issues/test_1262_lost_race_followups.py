"""Issue #1262: ``_lost_race`` follow-ups from the review of PR #1261 (#1255).

Items and the readings chosen here:

1. Older git (before ~2.50) words the simultaneous-push race as a
   ``remote: error: cannot lock ref '<ref>': is at <sha> but expected <sha>`` line
   plus ``! [remote rejected] main -> main (failed to update ref)``. Reading: that pair
   is a lost race. ``(failed to update ref)`` WITHOUT a ``cannot lock ref ... but
   expected`` line (alone, or beside a ``cannot lock ref`` line that says something
   else, e.g. ``Permission denied``) is NOT. Older git's ``File exists`` lock wording
   beside ``(failed to update ref)`` is left unpinned (the issue names only
   ``but expected``).
2. Tighten. Reading: only a line starting (after whitespace) with
   ``! [remote rejected]`` or ``! [rejected]`` can make a lost race, and the reason
   must be on that same line: ``(fetch first)`` / ``(non-fast-forward)`` on a
   ``[rejected]`` line; ``(incorrect old value provided)`` or a ``cannot lock ref``
   that says ``but expected`` or ``File exists`` on a ``[remote rejected]`` line.
   Text a hook echoes (inside a ``remote:`` line) is not a lost race, nor is a
   ``cannot lock ref`` with neither ``but expected`` nor ``File exists``. The only
   use of a ``remote:`` line is item 1's evidence (``cannot lock ref`` + ``but
   expected``) for a real ``(failed to update ref)`` line. Every #1255 sample (see
   test_1255_ref_lock_retry.py) stays a lost race.
3. The two-clones test in test_1255 must not pass vacuously. Pinned structurally
   (no seam exposes the collision count): the test's source must assert on
   ``ref_lock_rounds`` (e.g. ``assert ref_lock_rounds >= 1``) or skip on it
   (``if not ref_lock_rounds: pytest.skip(...)``), and a skip must come AFTER
   ``assert not failures`` so a skip can't hide a lost write.
4. Docstrings. The one at ~2726 that the issue calls ``_race_to_branch``'s is
   ``_cas_on_default_branch``'s (``_race_to_branch``'s own just points at it), and
   ``sync_and_push``'s. Reading: each must name the ref-lock / remote-rejected race
   (``_lost_race``, ``remote rejected``, ``cannot lock ref``, ``ref-lock`` or
   ``ref lock``), and ``_cas_on_default_branch``'s must no longer say "Only a lost
   race (a non-fast-forward rejection)". A bare "ref" is not enough: it already
   says ``refs/heads/<default>``.
5. ``_race_to_branch`` real concurrency: clones A and B each run
   ``allocate_next_id("EXP")`` in two threads whose first ``git push`` is released
   together by a barrier, for several rounds; both land every round with distinct
   ids. On this git (modern) it passes today: it guards the loop, it isn't red.
"""

from __future__ import annotations

import ast
import inspect
import subprocess
import threading
from pathlib import Path
from typing import Any

import pytest

from tests.issues import test_1255_ref_lock_retry as t1255
from tests.issues.test_585_create_push_loop import _ORIG_RUN, World, _is_git_push
from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.service import KanbanService, _lost_race

URL = "/srv/git/board.git"
IS_AT = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
EXPECTED = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
BUT_EXPECTED = f"cannot lock ref 'refs/heads/main': is at {IS_AT} but expected {EXPECTED}"
FILE_EXISTS = (
    "cannot lock ref 'refs/heads/main': Unable to create "
    f"'{URL}/refs/heads/main.lock': File exists."
)
PERMISSION = (
    "cannot lock ref 'refs/heads/main': Unable to create "
    f"'{URL}/refs/heads/main.lock': Permission denied"
)
TAIL = f"error: failed to push some refs to '{URL}'\n"


def _push_err(*lines: str) -> str:
    """A git push's stderr: the remote's lines, `To <url>`, the ref lines, the tail."""
    remote = [ln for ln in lines if ln.startswith("remote:")]
    refs = [ln for ln in lines if not ln.startswith("remote:")]
    return "".join(f"{ln}\n" for ln in remote) + f"To {URL}\n" + "".join(
        f"{ln}\n" for ln in refs
    ) + TAIL


def _remote_rejected(reason: str) -> str:
    return f" ! [remote rejected] 0123456789ab -> main ({reason})"


def _rejected(reason: str) -> str:
    return f" ! [rejected]        main -> main ({reason})"


HINT_FETCH_FIRST = (
    "hint: Updates were rejected because the remote contains work that you do not\n"
    "hint: have locally. This is usually caused by another repository pushing to\n"
    "hint: the same ref. If you want to integrate the remote changes, use\n"
    "hint: 'git pull' before pushing again."
)


# --- 1. older git: '(failed to update ref)' beside 'cannot lock ref ... but expected' ---

OLDER_GIT_RACES = {
    "remote_error_line": _push_err(
        f"remote: error: {BUT_EXPECTED}        ",
        _remote_rejected("failed to update ref"),
    ),
    "remote_error_line_no_padding": _push_err(
        f"remote: error: {BUT_EXPECTED}",
        _remote_rejected("failed to update ref"),
    ),
}


@pytest.mark.parametrize("name", list(OLDER_GIT_RACES))
def test_older_git_failed_to_update_ref_with_but_expected_is_a_lost_race(name: str) -> None:
    err = OLDER_GIT_RACES[name]
    assert _lost_race(err), f"older git's ref-lock race not treated as lost:\n{err}"


OLDER_GIT_NOT_RACES = {
    "failed_to_update_ref_alone": _push_err(_remote_rejected("failed to update ref")),
    "failed_to_update_ref_permission_denied": _push_err(
        f"remote: error: {PERMISSION}",
        _remote_rejected("failed to update ref"),
    ),
}


@pytest.mark.parametrize("name", list(OLDER_GIT_NOT_RACES))
def test_failed_to_update_ref_without_but_expected_is_not_a_lost_race(name: str) -> None:
    err = OLDER_GIT_NOT_RACES[name]
    assert not _lost_race(err), f"treated as a lost race:\n{err}"


# --- 2. tightening: only real ref lines, reason on that line ----------------------------

STILL_RACES = {
    "fetch_first": _push_err(_rejected("fetch first")) + HINT_FETCH_FIRST,
    "non_fast_forward": _push_err(_rejected("non-fast-forward")),
    "incorrect_old_value": _push_err(_remote_rejected("incorrect old value provided")),
    "cannot_lock_ref_but_expected": _push_err(_remote_rejected(BUT_EXPECTED)),
    "cannot_lock_ref_file_exists": _push_err(_remote_rejected(FILE_EXISTS)),
    "1255_real_local_output": _push_err(
        f"remote: error: {BUT_EXPECTED}        ",
        _remote_rejected("incorrect old value provided"),
    ),
    "no_leading_space": f"To {URL}\n! [rejected] main -> main (fetch first)\n" + TAIL,
}


@pytest.mark.parametrize("name", list(STILL_RACES))
def test_existing_lost_races_still_hold(name: str) -> None:
    err = STILL_RACES[name]
    assert _lost_race(err), f"no longer a lost race:\n{err}"


@pytest.mark.parametrize("reason", list(t1255.RACE_REASONS))
def test_1255_samples_still_lost_races(reason: str) -> None:
    err = t1255._stderr(Path(URL), t1255.RACE_REASONS[reason])
    assert _lost_race(err), err


NOT_RACES = {
    "hook_echo_remote_rejected_cannot_lock_ref": _push_err(
        "remote: ! [remote rejected] main -> main (cannot lock ref 'refs/heads/main')",
        _remote_rejected("pre-receive hook declined"),
    ),
    "hook_echo_but_expected_inside_remote_line": _push_err(
        f"remote: policy: ! [remote rejected] main -> main ({BUT_EXPECTED})",
        _remote_rejected("pre-receive hook declined"),
    ),
    "hook_echo_rejected_fetch_first": _push_err(
        "remote: ! [rejected]        main -> main (fetch first)",
        _remote_rejected("pre-receive hook declined"),
    ),
    "hook_echo_incorrect_old_value": _push_err(
        "remote: ! [remote rejected] main -> main (incorrect old value provided)",
        _remote_rejected("hook declined"),
    ),
    "cannot_lock_ref_permission_denied": _push_err(_remote_rejected(PERMISSION)),
    "cannot_lock_ref_bare": _push_err(_remote_rejected("cannot lock ref 'refs/heads/main'")),
    "rejected_reason_only_in_hint": _push_err(_rejected("stale info"))
    + "hint: the remote is not a fast-forward of yours: fetch first\n",
    "hook_declined_plain": _push_err(_remote_rejected("pre-receive hook declined")),
    "protected_branch": _push_err(
        "remote: error: GH006: Protected branch update failed for refs/heads/main.",
        _remote_rejected("protected branch hook declined"),
    ),
}


@pytest.mark.parametrize("name", list(NOT_RACES))
def test_echoed_or_unqualified_text_is_not_a_lost_race(name: str) -> None:
    err = NOT_RACES[name]
    assert not _lost_race(err), f"treated as a lost race:\n{err}"


# --- 3. the two-clones test must not pass vacuously -------------------------------------


def _names(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def _is_skip(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "skip"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "pytest"
    )


def test_two_clones_test_asserts_a_collision_or_skips() -> None:
    src = inspect.getsource(t1255.test_two_clones_pushing_at_once_both_land)
    fn = ast.parse(src).body[0]
    failures_line = next(
        (
            n.lineno
            for n in ast.walk(fn)
            if isinstance(n, ast.Assert) and "failures" in _names(n.test)
        ),
        None,
    )
    asserted = any(
        isinstance(n, ast.Assert) and "ref_lock_rounds" in _names(n.test) for n in ast.walk(fn)
    )
    skips = [
        n
        for n in ast.walk(fn)
        if isinstance(n, ast.If)
        and "ref_lock_rounds" in _names(n.test)
        and any(_is_skip(c) for c in ast.walk(n))
    ]
    assert asserted or skips, (
        "test_two_clones_pushing_at_once_both_land neither asserts nor skips on "
        "ref_lock_rounds: a runner that never collides passes vacuously"
    )
    if skips and not asserted:
        assert failures_line is not None and all(
            s.lineno > failures_line for s in skips
        ), "the no-collision skip comes before `assert not failures`: it can hide a lost write"


# --- 4. docstrings name the ref-lock race ------------------------------------------------

RACE_WORDS = ("_lost_race", "remote rejected", "cannot lock ref", "ref-lock", "ref lock")


@pytest.mark.parametrize("method", ["_cas_on_default_branch", "sync_and_push"])
def test_docstring_names_the_ref_lock_race(method: str) -> None:
    doc = " ".join((getattr(KanbanService, method).__doc__ or "").split())
    assert any(w in doc for w in RACE_WORDS), (
        f"{method}'s docstring says only a non-fast-forward is retried: {doc}"
    )


def test_cas_docstring_no_longer_says_only_non_fast_forward() -> None:
    doc = " ".join((KanbanService._cas_on_default_branch.__doc__ or "").split())
    assert "Only a lost race (a non-fast-forward rejection)" not in doc, doc


# --- 5. _race_to_branch: two clones allocate at once, both land -------------------------

ROUNDS = 4


class FirstPushBarrier:
    """Wrap subprocess.run (thread-safe): each thread's FIRST git push waits on the
    barrier so both clones push together; every push's stderr is recorded."""

    def __init__(self, barrier: threading.Barrier) -> None:
        self.barrier = barrier
        self.local = threading.local()
        self.lock = threading.Lock()
        self.said: list[str] = []

    def __call__(self, *args: Any, **kwargs: Any) -> subprocess.CompletedProcess:
        cmd = args[0] if args else kwargs.get("args")
        if _is_git_push(cmd) and threading.current_thread().name.startswith("yk1262-"):
            if not getattr(self.local, "pushed", False):
                self.local.pushed = True
                self.barrier.wait()
            done = _ORIG_RUN(*args, **kwargs)
            err = done.stderr if isinstance(done.stderr, str) else (done.stderr or b"").decode()
            with self.lock:
                self.said.append(err)
            return done
        return _ORIG_RUN(*args, **kwargs)


def _service(clone: Path) -> KanbanService:
    return KanbanService(KanbanConfig.load(clone / ".kanban" / "config.yaml"), clone)


def test_two_clones_allocating_at_once_both_land(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    w = World(tmp_path)
    collided = 0
    failures: list[str] = []
    seen: list[str] = []

    for n in range(ROUNDS):
        gate = FirstPushBarrier(threading.Barrier(2, timeout=30))
        monkeypatch.setattr(subprocess, "run", gate)
        results: dict[str, dict[str, Any]] = {}

        def allocate(who: str, clone: Path) -> None:
            results[who] = _service(clone).allocate_next_id("EXP")

        threads = [
            threading.Thread(target=allocate, args=("A", w.a), name=f"yk1262-A-{n}"),
            threading.Thread(target=allocate, args=("B", w.b), name=f"yk1262-B-{n}"),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=120)
        assert not any(t.is_alive() for t in threads), "an allocator hung"
        monkeypatch.setattr(subprocess, "run", _ORIG_RUN)

        if any(e for e in gate.said):
            collided += 1
        for who in ("A", "B"):
            got = results.get(who)
            if got is None or not got.get("success"):
                failures.append(f"round {n}: {who} failed: {got}")
                continue
            if got.get("recorded") != "pushed":
                failures.append(f"round {n}: {who} recorded {got.get('recorded')}")
            seen.append(got["id"])

    with capsys.disabled():
        print(
            f"\n[#1262 _race_to_branch concurrency] {ROUNDS} rounds: a rejected push in "
            f"{collided}, failures {len(failures)}"
        )
    assert not failures, "\n".join(failures)
    assert len(seen) == len(set(seen)) == 2 * ROUNDS, f"duplicate ids handed out: {seen}"
    allocations = subprocess.run(
        ["git", "show", "main:.kanban/_ID_ALLOCATIONS.json"],
        cwd=w.remote, capture_output=True, text=True, check=False,
    ).stdout
    missing = [i for i in seen if i not in allocations]
    assert not missing, f"allocated ids not recorded on origin: {missing}\n{allocations}"
