"""#620 — `allocated_by` and HDD `run_by` record the actor through `resolve_actor`.

Follow-up from #580. Decision ([steer], bucket 2):

- `allocated_by` (ID allocation records, on the compare-and-swap path with a remote
  and on the local path without one) and HDD experiment `run_by` use `resolve_actor`:
  explicit, then `$YURTLE_AGENT`, then git `user.name`.
- With no identity from any source the command is REFUSED with #580's no-actor
  message (the same as `move`), and nothing is written. Never `unknown`.
- `experiment run` gets `--agent`, an alias of its existing `--run-by`.
- `next-id` / `create` get no new flag: the env var and git config cover them.

"No identity" here means: global/system git config hidden, `$YURTLE_AGENT` unset,
no repo-local `user.name`. git's own commit identity is supplied through the
`GIT_AUTHOR_*` / `GIT_COMMITTER_*` environment variables, which `resolve_actor`
does not read: so a commit could still be made, and only the missing ACTOR can
stop the command (not a git "Author identity unknown" failure).
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import CliRunner

from yurtle_kanban.cli import main
from yurtle_kanban.config import KanbanConfig, PathConfig

ALLOC = ".kanban/_ID_ALLOCATIONS.json"
GIT_USER = "GitUser"
ENV_USER = "EnvUser"
NO_ACTOR = "No actor"  # #580's message: "No actor: set --agent or YURTLE_AGENT ..."


# ---------------------------------------------------------------------------
# helpers and fixtures
# ---------------------------------------------------------------------------


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, stdin=subprocess.DEVNULL
    )
    assert result.returncode == 0, f"git {' '.join(args)} failed in {cwd}: {result.stderr}"
    return result.stdout


def _invoke(args: list[str]):
    return CliRunner().invoke(main, args)


def _flat(text: str) -> str:
    return " ".join(text.split())


def _ok(args: list[str]):
    result = _invoke(args)
    assert result.exit_code == 0, (
        f"{args} exited {result.exit_code}: {result.exception!r}\n{result.output}"
    )
    return result


@pytest.fixture(autouse=True)
def _no_env_actor(monkeypatch: pytest.MonkeyPatch) -> None:
    """The caller's shell may export YURTLE_AGENT; each test sets it explicitly."""
    monkeypatch.delenv("YURTLE_AGENT", raising=False)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A git repo on `main` with a software-theme board, repo-local git user
    `GitUser`, the machine's global/system git config hidden; cwd is the repo."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    work = tmp_path / "work"
    work.mkdir()
    _git(work, "init", "-q", "-b", "main")
    _git(work, "config", "user.name", GIT_USER)
    _git(work, "config", "user.email", "git@test.com")
    _git(work, "config", "commit.gpgsign", "false")
    (work / ".kanban").mkdir()
    (work / "kanban-work" / "features").mkdir(parents=True)
    (work / "kanban-work" / "bugs").mkdir(parents=True)
    KanbanConfig(
        theme="software",
        paths=PathConfig(
            root="kanban-work/",
            scan_paths=["kanban-work/features/", "kanban-work/bugs/"],
        ),
    ).save(work / ".kanban" / "config.yaml")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "init")
    monkeypatch.chdir(work)
    return work


@pytest.fixture
def remote(repo: Path, tmp_path: Path) -> Path:
    """A bare `origin` for `repo`, holding `main`."""
    bare = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(bare))
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "push", "-q", "-u", "origin", "main")
    return bare


def _set_source(source: str, monkeypatch: pytest.MonkeyPatch) -> str:
    """Make `source` ("env" or "git") the winning identity; return the expected actor."""
    if source == "env":
        monkeypatch.setenv("YURTLE_AGENT", ENV_USER)
        return ENV_USER
    return GIT_USER


def _no_identity(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No actor from any source; git can still commit via GIT_AUTHOR/COMMITTER env."""
    _git(repo, "config", "--unset", "user.name")
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "Committer Only")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "committer@test.com")


def _records_local(repo: Path) -> list[dict[str, Any]]:
    """The allocation records in the checkout's working tree."""
    path = repo / ALLOC
    assert path.exists(), f"no {ALLOC} written in the checkout"
    return json.loads(path.read_text())


def _records_on_remote(remote: Path) -> list[dict[str, Any]]:
    """The allocation records on origin/main."""
    return json.loads(_git(remote, "show", f"main:{ALLOC}"))


def _allocated_by(records: list[dict[str, Any]], item_id: str) -> Any:
    matches = [r for r in records if r.get("id") == item_id]
    assert matches, f"no allocation record for {item_id}: {records}"
    return matches[-1].get("allocated_by")


def _snapshot(repo: Path) -> dict[str, Any]:
    """HEAD, the status and every file under the checkout (except .git)."""
    files = sorted(
        p.relative_to(repo).as_posix()
        for p in repo.rglob("*")
        if p.is_file() and ".git" not in p.relative_to(repo).parts
    )
    return {
        "head": _git(repo, "rev-parse", "HEAD").strip(),
        "status": _git(repo, "status", "--porcelain", "--untracked-files=all"),
        "files": files,
    }


def _no_unknown_in(repo: Path, remote: Path | None, output: str) -> None:
    assert "unknown" not in output.lower(), f"'unknown' in the output: {output!r}"
    for p in repo.rglob("*"):
        if p.is_file() and ".git" not in p.relative_to(repo).parts:
            assert "unknown" not in p.read_text(errors="replace"), f"'unknown' in {p}"
    history = _git(repo, "log", "--all", "-p")
    assert '"unknown"' not in history, "'unknown' committed locally"
    if remote is not None:
        assert '"unknown"' not in _git(remote, "log", "--all", "-p"), (
            "'unknown' pushed to origin"
        )


def _refused_no_actor(args: list[str]):
    result = _invoke(args)
    out = _flat(result.output)
    assert result.exit_code == 1, (
        f"{args} with no identity should be refused (exit 1), got {result.exit_code}: {out}"
    )
    assert result.exception is None or isinstance(result.exception, SystemExit), (
        f"{args} crashed instead of refusing: {result.exception!r}"
    )
    assert NO_ACTOR in out and "YURTLE_AGENT" in out, (
        f"{args}: expected #580's no-actor message naming YURTLE_AGENT: {out!r}"
    )
    return result


# ---------------------------------------------------------------------------
# (a) allocated_by = the resolved actor
# ---------------------------------------------------------------------------

SOURCES = pytest.mark.parametrize("source", ["env", "git"])


class TestAllocatedByIsTheActor:
    @SOURCES
    def test_next_id_local(self, repo, monkeypatch, source):
        """No remote: next-id writes and commits the record locally."""
        expected = _set_source(source, monkeypatch)
        _ok(["next-id", "FEAT"])
        assert _allocated_by(_records_local(repo), "FEAT-001") == expected

    @SOURCES
    def test_next_id_cas(self, repo, remote, monkeypatch, source):
        """With a remote: the record lands on origin/main by compare-and-swap."""
        expected = _set_source(source, monkeypatch)
        _ok(["next-id", "FEAT"])
        assert _allocated_by(_records_on_remote(remote), "FEAT-001") == expected

    @SOURCES
    def test_create_push_local(self, repo, monkeypatch, source):
        """No remote: create --push allocates, creates and commits locally."""
        expected = _set_source(source, monkeypatch)
        _ok(["create", "feature", "probe", "--push"])
        assert _allocated_by(_records_local(repo), "FEAT-001") == expected

    @SOURCES
    def test_create_push_cas(self, repo, remote, monkeypatch, source):
        expected = _set_source(source, monkeypatch)
        _ok(["create", "feature", "probe", "--push"])
        assert _allocated_by(_records_on_remote(remote), "FEAT-001") == expected


# ---------------------------------------------------------------------------
# (b) no identity: refused, nothing written or pushed, never "unknown"
# ---------------------------------------------------------------------------


class TestNoIdentityIsRefused:
    @pytest.mark.parametrize(
        "args",
        [["next-id", "FEAT"], ["create", "feature", "probe", "--push"]],
        ids=["next-id", "create-push"],
    )
    def test_local(self, repo, monkeypatch, args):
        _no_identity(repo, monkeypatch)
        before = _snapshot(repo)
        result = _refused_no_actor(args)
        assert _snapshot(repo) == before, "something was written or committed"
        assert not (repo / ALLOC).exists()
        _no_unknown_in(repo, None, result.output)

    @pytest.mark.parametrize(
        "args",
        [["next-id", "FEAT"], ["create", "feature", "probe", "--push"]],
        ids=["next-id", "create-push"],
    )
    def test_cas(self, repo, remote, monkeypatch, args):
        _no_identity(repo, monkeypatch)
        before = _snapshot(repo)
        remote_before = _git(remote, "rev-parse", "main").strip()
        result = _refused_no_actor(args)
        assert _git(remote, "rev-parse", "main").strip() == remote_before, (
            "something was pushed to origin/main"
        )
        assert _snapshot(repo) == before, "something was written or committed"
        _no_unknown_in(repo, remote, result.output)


# ---------------------------------------------------------------------------
# (c) experiment run: run_by
# ---------------------------------------------------------------------------

RUN = ["experiment", "run", "EXPR-130", "--being", "b-v1"]
RUNS = Path("research") / "runs" / "EXPR-130"


def _run_by(repo: Path) -> Any:
    configs = sorted((repo / RUNS).glob("*/config.yaml"))
    assert len(configs) == 1, f"expected one run config, got {configs}"
    return yaml.safe_load(configs[0].read_text())["run_by"]


class TestExperimentRunBy:
    def test_agent_flag(self, repo, monkeypatch):
        monkeypatch.setenv("YURTLE_AGENT", ENV_USER)
        _ok([*RUN, "--agent", "FlagUser"])
        assert _run_by(repo) == "FlagUser"

    def test_run_by_flag_control(self, repo, monkeypatch):
        """Control: the existing --run-by (now an alias of --agent) beats the env."""
        monkeypatch.setenv("YURTLE_AGENT", ENV_USER)
        _ok([*RUN, "--run-by", "FlagUser"])
        assert _run_by(repo) == "FlagUser"

    def test_agent_flag_is_stripped(self, repo):
        _ok([*RUN, "--agent", "  FlagUser  "])
        assert _run_by(repo) == "FlagUser"

    def test_env_beats_git(self, repo, monkeypatch):
        monkeypatch.setenv("YURTLE_AGENT", ENV_USER)
        _ok(RUN)
        assert _run_by(repo) == ENV_USER

    def test_git_fallback_control(self, repo):
        _ok(RUN)
        assert _run_by(repo) == GIT_USER

    def test_blank_env_is_refused(self, repo, monkeypatch):
        """$YURTLE_AGENT set but blank is an error (resolve_actor), not git's name."""
        monkeypatch.setenv("YURTLE_AGENT", "  ")
        result = _invoke(RUN)
        assert result.exit_code == 1, f"exit {result.exit_code}: {result.output}"
        assert "YURTLE_AGENT" in _flat(result.output), result.output
        assert not (repo / RUNS).exists(), "a run folder was created"

    def test_no_identity_is_refused(self, repo, monkeypatch):
        _no_identity(repo, monkeypatch)
        before = _snapshot(repo)
        result = _refused_no_actor(RUN)
        assert not (repo / RUNS).exists(), "a run folder was created"
        assert _snapshot(repo) == before, "something was written or committed"
        _no_unknown_in(repo, None, result.output)

    def test_no_identity_push_is_refused(self, repo, remote, monkeypatch):
        _no_identity(repo, monkeypatch)
        before = _snapshot(repo)
        remote_before = _git(remote, "rev-parse", "main").strip()
        result = _refused_no_actor([*RUN, "--push"])
        assert not (repo / RUNS).exists(), "a run folder was created"
        assert _snapshot(repo) == before, "something was written or committed"
        assert _git(remote, "rev-parse", "main").strip() == remote_before
        _no_unknown_in(repo, remote, result.output)
