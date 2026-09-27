"""#630 — resolve the actor once per move.

Spec ([steer] on #630, bucket 2, no behaviour change): `move` resolves the actor
exactly once, in `KanbanService.move_item`, and passes the resolved string down.
`_update_item_file_with_history` takes `actor: str` and doesn't call
`resolve_actor`.

- (a) one `move_item` call -> `resolve_actor` called exactly once, both with an
  explicit actor and with `actor=None` (env / git fallback);
- (b) `_update_item_file_with_history` records a pre-resolved actor verbatim
  without touching `resolve_actor` (patched to raise);
- (c) control: the history block's `kb:by` is unchanged for `--agent`,
  `$YURTLE_AGENT` and git `user.name` (the conftest's hermetic identity, #580).
"""

from __future__ import annotations

import inspect
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

import yurtle_kanban.service as service_mod
from yurtle_kanban.cli import main
from yurtle_kanban.config import KanbanConfig, PathConfig
from yurtle_kanban.inputs import resolve_actor as real_resolve_actor
from yurtle_kanban.models import WorkItemStatus, WorkItemType
from yurtle_kanban.service import KanbanService

REPO_GIT_USER = "Repo Git User"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, check=True)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A git repo with a software-theme board and a local git user; cwd is the repo."""
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.email", "repo@example.invalid")
    _git(tmp_path, "config", "user.name", REPO_GIT_USER)
    _git(tmp_path, "config", "commit.gpgsign", "false")
    (tmp_path / ".kanban").mkdir()
    (tmp_path / "kanban-work" / "features").mkdir(parents=True)
    KanbanConfig(
        theme="software",
        paths=PathConfig(root="kanban-work/", scan_paths=["kanban-work/features/"]),
    ).save(tmp_path / ".kanban" / "config.yaml")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "init")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _service(repo: Path) -> KanbanService:
    return KanbanService(KanbanConfig.load(repo / ".kanban" / "config.yaml"), repo)


def _new_item(repo: Path) -> str:
    item = _service(repo).create_item(WorkItemType.FEATURE, "Actor once")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "add item")
    return item.id


def _by_values(path: Path) -> list[str]:
    """Every `kb:by "..."` in the item's status history, in file order."""
    return re.findall(r'kb:by "([^"]*)"', path.read_text())


class _Counter:
    """Wraps the real resolver and counts calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    def __call__(self, explicit: str | None, *args: Any, **kwargs: Any) -> str:
        self.calls.append((explicit, args, kwargs))
        return real_resolve_actor(explicit, *args, **kwargs)


# ---------------------------------------------------------------------------
# (a) one move_item call resolves the actor exactly once
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "actor, env",
    [
        pytest.param("Explicit-Agent", None, id="explicit"),
        pytest.param(None, "Env-Agent", id="env"),
        pytest.param(None, None, id="git-user-name"),
    ],
)
def test_move_item_resolves_actor_once(
    repo: Path, monkeypatch: pytest.MonkeyPatch, actor: str | None, env: str | None
) -> None:
    item_id = _new_item(repo)
    if env is not None:
        monkeypatch.setenv("YURTLE_AGENT", env)
    counter = _Counter()
    monkeypatch.setattr(service_mod, "resolve_actor", counter)

    _service(repo).move_item(item_id, WorkItemStatus.READY, commit=False, actor=actor)

    assert len(counter.calls) == 1, (
        f"resolve_actor called {len(counter.calls)} times for one move_item: "
        f"{counter.calls}"
    )


# ---------------------------------------------------------------------------
# (b) _update_item_file_with_history takes the resolved str, never resolves
# ---------------------------------------------------------------------------


def test_update_history_uses_given_actor_without_resolving(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    item_id = _new_item(repo)
    service = _service(repo)
    item = service.get_item(item_id)
    assert item is not None and item.file_path is not None

    def _boom(*args: Any, **kwargs: Any) -> str:
        raise AssertionError(
            f"_update_item_file_with_history called resolve_actor{args!r} {kwargs!r}"
        )

    monkeypatch.setattr(service_mod, "resolve_actor", _boom)
    service._update_item_file_with_history(
        item, item.status, WorkItemStatus.READY, actor="Pre-Resolved Agent"
    )

    assert _by_values(item.file_path) == ["Pre-Resolved Agent"]


def test_update_history_actor_annotated_plain_str() -> None:
    """The signature says what the spec says: `actor: str`, not optional."""
    param = inspect.signature(
        KanbanService._update_item_file_with_history
    ).parameters["actor"]
    assert param.annotation in ("str", str), (
        f"actor is annotated {param.annotation!r}, expected plain str"
    )


# ---------------------------------------------------------------------------
# (c) control: kb:by is unchanged for --agent, $YURTLE_AGENT and git user.name
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "args, env, expected",
    [
        pytest.param(["--agent", "Flag-Agent"], "Env-Agent", "Flag-Agent", id="--agent"),
        pytest.param([], "Env-Agent", "Env-Agent", id="YURTLE_AGENT"),
        pytest.param([], None, REPO_GIT_USER, id="git-user-name"),
    ],
)
def test_cli_move_records_same_actor(
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    args: list[str],
    env: str | None,
    expected: str,
) -> None:
    item_id = _new_item(repo)
    if env is not None:
        monkeypatch.setenv("YURTLE_AGENT", env)

    result = CliRunner().invoke(main, ["move", item_id, "ready", "--no-commit", *args])
    assert result.exit_code == 0, f"{result.exception!r}\n{result.output}"

    item = _service(repo).get_item(item_id)
    assert item is not None and item.file_path is not None
    assert _by_values(item.file_path) == [expected]


@pytest.mark.parametrize(
    "actor, env, expected",
    [
        pytest.param("Explicit-Agent", "Env-Agent", "Explicit-Agent", id="explicit"),
        pytest.param(None, "Env-Agent", "Env-Agent", id="env"),
        pytest.param(None, None, REPO_GIT_USER, id="git-user-name"),
    ],
)
def test_move_item_records_same_actor(
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    actor: str | None,
    env: str | None,
    expected: str,
) -> None:
    item_id = _new_item(repo)
    if env is not None:
        monkeypatch.setenv("YURTLE_AGENT", env)

    moved = _service(repo).move_item(
        item_id, WorkItemStatus.READY, commit=False, actor=actor
    )

    assert moved.file_path is not None
    assert _by_values(moved.file_path) == [expected]


def test_move_item_no_actor_still_refused(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Control: no --agent, no $YURTLE_AGENT, no git user.name -> ValueError."""
    item_id = _new_item(repo)
    _git(repo, "config", "--unset", "user.name")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")

    with pytest.raises(ValueError, match="No actor"):
        _service(repo).move_item(item_id, WorkItemStatus.READY, commit=False)
