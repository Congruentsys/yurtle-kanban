"""#614: ``hdd registry --push`` publishes only the registry commit, and only when there is one.

Follow-up from the #584 review. ``hdd registry --push`` ran a bare ``git push``
after committing the registry:

1. With an unchanged registry there is nothing to commit, yet it still pushed
   and printed "Committed and pushed". Expected: it says the registry is
   unchanged, and makes no push (so an unrelated unpushed commit isn't
   published either).
2. With a changed registry and an unrelated unpushed local commit on the branch,
   the bare push published that commit too. Expected: it refuses the push
   (non-zero exit, no traceback), names the unpushed commit(s), and the remote
   gets nothing. What we assert about the registry change: only that it is not
   lost (the written registry is on disk); whether it was committed locally
   before the refusal is left to the implementation.
3. With a changed registry and no other unpushed commits, exactly the registry
   commit reaches the remote.

Real git: a repo whose ``origin`` is a bare remote, HDD board (#584 fixtures).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.issues.test_584_scoped_commit import (  # noqa: F401  (fixtures)
    _clear_theme_cache,
    _git,
    _head,
    _init,
    _remote_head,
    _run,
    _write_hdd,
)

REGISTRY = "research/REGISTRY.md"
ARGS = ["hdd", "registry", "--push"]
PUSHED = "Committed and pushed"
UNRELATED = "unrelated local work 614"


@pytest.fixture
def hdd_repo(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    """The #584 HDD repo: a committed, pushed PAPER-130; ``origin`` is a bare remote."""
    repo = tmp_path / "repo"
    remote = _init(repo, _write_hdd)
    result = _run(repo, monkeypatch, ["paper", "create", "130", "A paper"])
    assert result.exit_code == 0, result.output
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "hdd items")
    _git(repo, "push")
    return repo, remote


@pytest.fixture
def pushes(monkeypatch) -> list[list[str]]:
    """Record every ``git push`` any code runs (through subprocess.run)."""
    seen: list[list[str]] = []
    real = subprocess.run

    def spy(args, *a, **kw):
        if isinstance(args, (list, tuple)) and list(args[:1]) == ["git"] and "push" in args:
            seen.append(list(args))
        return real(args, *a, **kw)

    monkeypatch.setattr(subprocess, "run", spy)
    return seen


def _publish_registry(repo: Path, monkeypatch) -> None:
    """First run: the registry is new, so it is committed and pushed."""
    result = _run(repo, monkeypatch, ARGS)
    assert result.exit_code == 0, result.output
    assert (repo / REGISTRY).exists()
    assert _git(repo, "status", "--porcelain", "--", REGISTRY) == "", "registry not committed"
    assert _git(repo, "rev-list", "--count", "origin/main..HEAD").strip() == "0", (
        "control: first run left the registry unpushed"
    )


def _unpushed_commit(repo: Path) -> str:
    (repo / "other.txt").write_text("the user's own unpushed work\n")
    _git(repo, "add", "other.txt")
    _git(repo, "commit", "-m", UNRELATED)
    return _head(repo)


def _on_remote(remote: Path, sha: str) -> bool:
    done = subprocess.run(
        ["git", "merge-base", "--is-ancestor", sha, "main"],
        cwd=remote, capture_output=True,
    )
    return done.returncode == 0


def _change_registry(repo: Path, monkeypatch) -> None:
    """A new paper, committed and pushed, so the next registry run has a change."""
    result = _run(repo, monkeypatch, ["paper", "create", "131", "Another paper"])
    assert result.exit_code == 0, result.output
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "paper 131")
    _git(repo, "push")


# --- 1. unchanged registry: say so, push nothing -------------------------------


def test_unchanged_registry_says_so_and_does_not_push(hdd_repo, monkeypatch, pushes):
    repo, remote = hdd_repo
    _publish_registry(repo, monkeypatch)
    head, rhead = _head(repo), _remote_head(remote)
    pushes.clear()
    result = _run(repo, monkeypatch, ARGS)
    assert result.exit_code == 0, result.output
    assert _head(repo) == head, "a commit was made for an unchanged registry"
    assert "unchanged" in result.output.lower(), (
        f"no 'registry unchanged' note:\n{result.output}"
    )
    assert PUSHED not in result.output, result.output
    assert pushes == [], f"pushed with nothing to push: {pushes}"
    assert _remote_head(remote) == rhead


def test_unchanged_registry_does_not_publish_unrelated_commit(hdd_repo, monkeypatch, pushes):
    repo, remote = hdd_repo
    _publish_registry(repo, monkeypatch)
    sha = _unpushed_commit(repo)
    rhead = _remote_head(remote)
    pushes.clear()
    result = _run(repo, monkeypatch, ARGS)
    assert pushes == [], f"pushed with an unchanged registry: {pushes}\n{result.output}"
    assert _remote_head(remote) == rhead
    assert not _on_remote(remote, sha), "the unrelated local commit was published"


# --- 2. changed registry + unrelated unpushed commit: refuse -------------------


def test_changed_registry_refuses_with_unrelated_unpushed_commit(hdd_repo, monkeypatch, pushes):
    repo, remote = hdd_repo
    sha = _unpushed_commit(repo)
    rhead = _remote_head(remote)
    result = _run(repo, monkeypatch, ARGS)
    assert result.exit_code != 0, f"pushed alongside an unrelated commit:\n{result.output}"
    assert result.exception is None or isinstance(result.exception, SystemExit), (
        f"traceback instead of a message: {result.exception!r}"
    )
    out = " ".join(result.output.split())
    assert sha[:7] in out or UNRELATED in out, (
        f"refusal does not name the unpushed commit ({sha[:7]} {UNRELATED!r}):\n{out}"
    )
    assert PUSHED not in result.output, result.output
    assert pushes == [], f"pushed despite refusing: {pushes}"
    assert _remote_head(remote) == rhead, "the remote moved"
    assert not _on_remote(remote, sha), "the unrelated local commit was published"
    assert (repo / REGISTRY).exists(), "the registry change was lost"


# --- 3. changed registry, nothing else unpushed: exactly the registry commit ---


def test_changed_registry_pushes_exactly_its_commit(hdd_repo, monkeypatch, pushes):
    repo, remote = hdd_repo
    _publish_registry(repo, monkeypatch)
    _change_registry(repo, monkeypatch)
    rbase = _remote_head(remote)
    pushes.clear()
    result = _run(repo, monkeypatch, ARGS)
    assert result.exit_code == 0, result.output
    assert PUSHED in result.output, result.output
    assert len(pushes) == 1, pushes
    rnew = _remote_head(remote)
    assert rnew == _head(repo), "local HEAD not what the remote got"
    assert _git(remote, "rev-list", "--count", f"{rbase}..{rnew}").strip() == "1"
    touched = set(_git(remote, "show", "--name-only", "--format=", rnew).split())
    assert touched == {REGISTRY}, touched
    assert _git(remote, "log", "-1", "--format=%s", rnew).strip() == (
        "hdd: update research registry"
    )
