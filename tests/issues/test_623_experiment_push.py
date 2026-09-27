"""#623: ``experiment run --push`` publishes only its own commit (the #614 bug, again).

``experiment run --push`` committed the run's ``config.yaml`` and then ran a bare
``git push``, publishing every unpushed commit on the branch. Like
``hdd registry --push`` after #614 it must:

1. refuse (exit 1) when the branch has another unpushed commit, naming it, and
   push nothing;
2. with nothing else unpushed, push exactly the run's commit;
3. with no upstream, warn, exit 0, and push nothing (the #614 contract).

Scope addition (#626 review): a rejected push, of the registry and of the
experiment run, is reported with git's multi-line stderr folded onto one line,
as #603 does for ``create --push``, never with a literal ``\\n``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.issues.test_584_scoped_commit import (  # noqa: F401  (fixtures)
    _clear_theme_cache,
    _git,
    _head,
    _remote_head,
    _run,
)
from tests.issues.test_614_registry_push import (  # noqa: F401  (fixtures)
    PUSHED,
    UNRELATED,
    _on_remote,
    _unpushed_commit,
    hdd_repo,
    pushes,
)

ARGS = ["experiment", "run", "EXPR-130", "--being", "b-v1", "--push"]
RUNS = "research/runs/EXPR-130/"
HOOK_LINE_1 = "protected branch: main is locked 623"
HOOK_LINE_2 = "ask an admin to unlock it 623"


@pytest.fixture
def world(hdd_repo) -> tuple[Path, Path]:  # noqa: F811  (the imported fixture)
    """The #614 HDD repo and its bare remote, under a local name."""
    return hdd_repo


@pytest.fixture
def git_pushes(pushes) -> list[list[str]]:  # noqa: F811  (the imported fixture)
    """Every ``git push`` run (the #614 spy)."""
    return pushes


def _reject_pushes(remote: Path) -> None:
    """The remote refuses every push, saying two lines (as in #603)."""
    hook = remote / "hooks" / "pre-receive"
    hook.write_text(f"#!/bin/sh\necho '{HOOK_LINE_1}' >&2\necho '{HOOK_LINE_2}' >&2\nexit 1\n")
    hook.chmod(0o755)


# --- 1. another unpushed commit: refuse, name it, push nothing ------------------


def test_refuses_with_unrelated_unpushed_commit(world, monkeypatch, git_pushes):
    repo, remote = world
    sha = _unpushed_commit(repo)
    rhead = _remote_head(remote)
    result = _run(repo, monkeypatch, ARGS)
    assert result.exit_code == 1, f"pushed alongside an unrelated commit:\n{result.output}"
    assert result.exception is None or isinstance(result.exception, SystemExit), (
        f"traceback instead of a message: {result.exception!r}"
    )
    out = " ".join(result.output.split())
    assert sha[:7] in out or UNRELATED in out, (
        f"refusal does not name the unpushed commit ({sha[:7]} {UNRELATED!r}):\n{out}"
    )
    assert PUSHED not in result.output, result.output
    assert git_pushes == [], f"pushed despite refusing: {git_pushes}"
    assert _remote_head(remote) == rhead, "the remote moved"
    assert not _on_remote(remote, sha), "the unrelated local commit was published"


# --- 2. nothing else unpushed: exactly the run's commit -------------------------


def test_pushes_exactly_the_run_commit(world, monkeypatch, git_pushes):
    repo, remote = world
    rbase = _remote_head(remote)
    result = _run(repo, monkeypatch, ARGS)
    assert result.exit_code == 0, result.output
    assert PUSHED in result.output, result.output
    assert len(git_pushes) == 1, git_pushes
    rnew = _remote_head(remote)
    assert rnew == _head(repo), "local HEAD not what the remote got"
    assert _git(remote, "rev-list", "--count", f"{rbase}..{rnew}").strip() == "1"
    touched = set(_git(remote, "show", "--name-only", "--format=", rnew).split())
    assert len(touched) == 1 and next(iter(touched)).startswith(RUNS), touched
    assert next(iter(touched)).endswith("/config.yaml"), touched
    subject = _git(remote, "log", "-1", "--format=%s", rnew).strip()
    assert subject.startswith("experiment run: EXPR-130 ("), subject


# --- 3. no upstream: warn, exit 0, push nothing ---------------------------------


def test_no_upstream_warns_and_does_not_push(world, monkeypatch, git_pushes):
    repo, remote = world
    _git(repo, "branch", "--unset-upstream")
    rhead = _remote_head(remote)
    result = _run(repo, monkeypatch, ARGS)
    assert result.exit_code == 0, result.output
    out = " ".join(result.output.split())
    assert "not pushed" in out and "upstream" in out, f"no 'no upstream' warning:\n{out}"
    assert PUSHED not in result.output, result.output
    assert git_pushes == [], f"pushed with no upstream: {git_pushes}"
    assert _remote_head(remote) == rhead
    assert _git(repo, "log", "-1", "--format=%s").startswith("experiment run: EXPR-130 ("), (
        "the run's commit is kept locally"
    )


# --- 4. a rejected push: git's stderr on one clean line (#603) ------------------


def _check_rejection_folded(repo: Path, remote: Path, monkeypatch, args: list[str]) -> None:
    _reject_pushes(remote)
    rhead = _remote_head(remote)
    result = _run(repo, monkeypatch, args)
    assert result.exit_code == 0, result.output
    assert PUSHED not in result.output, result.output
    assert _remote_head(remote) == rhead
    assert "\\n" not in result.output, f"literal \\n in the warning:\n{result.output}"
    warning = [ln for ln in result.output.splitlines() if "git push failed" in ln]
    assert len(warning) == 1, f"no single 'git push failed' line:\n{result.output}"
    assert HOOK_LINE_1 in warning[0] and HOOK_LINE_2 in warning[0], (
        f"git's stderr not folded onto the warning line:\n{result.output}"
    )


def test_registry_rejected_push_folds_stderr(world, monkeypatch):
    repo, remote = world
    _check_rejection_folded(repo, remote, monkeypatch, ["hdd", "registry", "--push"])


def test_experiment_run_rejected_push_folds_stderr(world, monkeypatch):
    repo, remote = world
    _check_rejection_folded(repo, remote, monkeypatch, ARGS)
