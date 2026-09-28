"""Issue #888 — the ``hdd`` push keeps the 30 s timeout though it runs the user's pre-push hook.

``_push_only_head_or_exit`` (hdd_commands.py; reached from ``hdd registry --push`` and
``experiment run --push``) runs ``git push`` — and with it the user's pre-push hook —
through ``KanbanService._git_run`` with its default ``timeout=30``. A slower hook raises
``subprocess.TimeoutExpired`` as a traceback, after the commit is already made.

Expected (the issue):

1. the push passes ``timeout=None``, per the #584 rule for commands that run user hooks;
2. any timeout in that push path reports cleanly — a message and a clean exit, never a
   traceback.

Control: a normal push with a fast pre-push hook still prints "Committed and pushed".

Nothing here sleeps: ``KanbanService._git_run`` is spied on (1) or made to raise
``TimeoutExpired`` for one call (2).
"""

from __future__ import annotations

import inspect
import subprocess
from pathlib import Path
from typing import Any

import pytest

from tests.issues.test_584_scoped_commit import (  # noqa: F401  (fixtures)
    _clear_theme_cache,
    _git,
    _head,
    _remote_head,
    _run,
)
from tests.issues.test_614_registry_push import PUSHED, hdd_repo  # noqa: F401  (fixture)
from yurtle_kanban.service import KanbanService

COMMANDS: dict[str, list[str]] = {
    "registry-push": ["hdd", "registry", "--push"],
    "experiment-run-push": ["experiment", "run", "EXPR-130", "--being", "b-v1", "--push"],
}

_REAL_GIT_RUN = KanbanService._git_run
_SIG = inspect.signature(_REAL_GIT_RUN)


def _effective_timeout(self: KanbanService, args: tuple[str, ...], kw: dict[str, Any]) -> Any:
    bound = _SIG.bind(self, *args, **kw)
    bound.apply_defaults()
    return bound.arguments["timeout"]


def _fast_pre_push_hook(repo: Path) -> Path:
    """A pre-push hook that records that it ran and passes at once."""
    hooks = repo.parent / "hooks-888"
    hooks.mkdir(exist_ok=True)
    ran = repo.parent / "pre-push-ran-888.txt"
    hook = hooks / "pre-push"
    hook.write_text(f'#!/bin/sh\necho ran >> "{ran}"\nexit 0\n')
    hook.chmod(0o755)
    _git(repo, "config", "core.hooksPath", str(hooks))
    return ran


@pytest.fixture
def world(hdd_repo) -> tuple[Path, Path]:  # noqa: F811  (the imported fixture)
    """The #614 HDD repo (a committed, pushed PAPER-130) and its bare remote."""
    return hdd_repo


@pytest.fixture
def git_calls(monkeypatch) -> list[tuple[tuple[str, ...], Any]]:
    """Every ``KanbanService._git_run`` call: (git args, effective timeout)."""
    seen: list[tuple[tuple[str, ...], Any]] = []

    def spy(self, *args, **kw):
        seen.append((args, _effective_timeout(self, args, kw)))
        return _REAL_GIT_RUN(self, *args, **kw)

    monkeypatch.setattr(KanbanService, "_git_run", spy)
    return seen


def _assert_clean(result) -> None:
    assert result.exception is None or isinstance(result.exception, SystemExit), (
        f"traceback instead of a message: {result.exception!r}\n{result.output}"
    )
    assert "Traceback" not in result.output, result.output
    assert "TimeoutExpired" not in result.output, result.output


# --- 1. the push that runs the pre-push hook has no timeout ------------------------


@pytest.mark.parametrize("cmd", list(COMMANDS))
def test_push_runs_with_no_timeout(world, monkeypatch, git_calls, cmd) -> None:
    repo, remote = world
    _fast_pre_push_hook(repo)

    result = _run(repo, monkeypatch, COMMANDS[cmd])

    assert result.exit_code == 0, result.output
    push_calls = [(args, t) for args, t in git_calls if args and args[0] == "push"]
    assert push_calls, f"no `git push` through _git_run: {[a for a, _ in git_calls]}"
    for args, timeout in push_calls:
        assert timeout is None, (
            f"`git {' '.join(args)}` runs the user's pre-push hook with timeout={timeout!r};"
            " #584: commands that run user hooks pass timeout=None"
        )


# --- 2. a timeout anywhere in the push path is reported cleanly --------------------


def _raise_timeout_on(monkeypatch, match) -> list[tuple[str, ...]]:
    """Make the `_git_run` calls for which `match(args)` holds raise TimeoutExpired."""
    raised: list[tuple[str, ...]] = []

    def fake(self, *args, **kw):
        if match(args):
            raised.append(args)
            raise subprocess.TimeoutExpired(["git", *args], _effective_timeout(self, args, kw))
        return _REAL_GIT_RUN(self, *args, **kw)

    monkeypatch.setattr(KanbanService, "_git_run", fake)
    return raised


TIMEOUTS = {
    "push": lambda args: bool(args) and args[0] == "push",
    "upstream-log": lambda args: bool(args) and args[0] == "log" and "@{u}..HEAD" in args,
    "upstream-rev-parse": lambda args: (
        len(args) >= 2 and args[0] == "rev-parse" and "@{u}" in args
    ),
}


@pytest.mark.parametrize("where", list(TIMEOUTS))
@pytest.mark.parametrize("cmd", list(COMMANDS))
def test_timeout_reports_cleanly(world, monkeypatch, cmd, where) -> None:
    repo, remote = world
    rhead = _remote_head(remote)
    before = _head(repo)
    raised = _raise_timeout_on(monkeypatch, TIMEOUTS[where])

    result = _run(repo, monkeypatch, COMMANDS[cmd])

    assert raised, f"the {where} git call was never made"
    _assert_clean(result)
    assert PUSHED not in result.output, result.output
    assert "Error" in result.output or "Warning" in result.output, (
        f"a timed-out push was not reported:\n{result.output}"
    )
    # the commit is made and kept; nothing reached the remote
    assert _head(repo) != before, "the commit was not kept locally"
    assert _remote_head(remote) == rhead


# --- 3. control: a normal push with a fast hook still succeeds ---------------------


@pytest.mark.parametrize("cmd", list(COMMANDS))
def test_control_fast_hook_push_succeeds(world, monkeypatch, cmd) -> None:
    repo, remote = world
    ran = _fast_pre_push_hook(repo)

    result = _run(repo, monkeypatch, COMMANDS[cmd])

    assert result.exit_code == 0, result.output
    _assert_clean(result)
    assert PUSHED in result.output, result.output
    assert ran.exists(), "the pre-push hook never ran"
    assert _head(repo) == _remote_head(remote), "HEAD was not pushed"
