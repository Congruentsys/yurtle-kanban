"""Issue #925 — the compare-and-swap pushes keep the 30 s timeout though they run the
user's pre-push hook.

``KanbanService._race_to_branch`` (``create --push``, through
``_cas_on_default_branch``) and ``KanbanService.sync_and_push`` (``claim``,
``update --push``) run ``git push origin <sha>:refs/heads/<default>`` through
``_git_run`` with its default ``timeout=30``. That push runs the user's pre-push hook,
so a hook slower than 30 s (a test gate) kills a push the hook would have passed.

Decided spec (the [steer] on #925, bucket 1):

1. both pushes pass ``timeout=None`` (#584's rule; #888 did the same for ``hdd --push``);
2. both stay under LC_ALL=C — they do not pass ``user_locale`` — because their
   ``[rejected]`` text is matched.

Control: the ``fetch`` of ``origin/<default>`` runs no user hook and keeps a finite
timeout.

Nothing here sleeps: ``KanbanService._git_run`` is spied on, as in
tests/issues/test_888_hdd_push_timeout.py, over real git (a bare origin and clones,
the #585 ``World``).
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from tests.issues.test_574_claim import ITEM as ITEM_PATH
from tests.issues.test_574_claim import ITEM_ID, A, item_text, push_from_a
from tests.issues.test_585_create_push_loop import World
from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import main
from yurtle_kanban.service import KanbanService

_REAL_GIT_RUN = KanbanService._git_run
_SIG = inspect.signature(_REAL_GIT_RUN)

COMMANDS: dict[str, list[str]] = {
    "claim": ["claim", ITEM_ID, "--agent", A],
    "create-push": ["create", "expedition", "A new expedition for 925", "--push"],
    "update-push": ["update", ITEM_ID, "--priority", "high", "--push"],
}


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    monkeypatch.delenv("YURTLE_AGENT", raising=False)
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


@pytest.fixture
def world(tmp_path: Path) -> World:
    """Origin and both clones hold EXP-001 at `ready`, unassigned."""
    w = World(tmp_path)
    push_from_a(w, {ITEM_PATH: item_text("ready")}, "seed EXP-001")
    return w


@pytest.fixture
def git_calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[tuple[str, ...], dict[str, Any]]]:
    """Every ``KanbanService._git_run`` call: (git args, every bound argument with
    its default applied)."""
    seen: list[tuple[tuple[str, ...], dict[str, Any]]] = []

    def spy(self: KanbanService, *args: str, **kw: Any) -> Any:
        bound = _SIG.bind(self, *args, **kw)
        bound.apply_defaults()
        seen.append((args, dict(bound.arguments)))
        return _REAL_GIT_RUN(self, *args, **kw)

    monkeypatch.setattr(KanbanService, "_git_run", spy)
    return seen


def _run(world: World, monkeypatch: pytest.MonkeyPatch, cmd: str) -> Any:
    monkeypatch.chdir(world.a)
    return CliRunner().invoke(main, COMMANDS[cmd])


def _calls(
    git_calls: list[tuple[tuple[str, ...], dict[str, Any]]], verb: str
) -> list[tuple[tuple[str, ...], dict[str, Any]]]:
    return [(args, bound) for args, bound in git_calls if args and args[0] == verb]


# --- 1. the CAS push runs the pre-push hook with no timeout, under C ----------------


@pytest.mark.parametrize("cmd", list(COMMANDS))
def test_cas_push_runs_with_no_timeout(world, monkeypatch, git_calls, cmd) -> None:
    before = world.remote_sha()

    result = _run(world, monkeypatch, cmd)

    assert result.exit_code == 0, result.output
    assert world.remote_sha() != before, f"{cmd} pushed nothing:\n{result.output}"
    pushes = _calls(git_calls, "push")
    assert pushes, f"no `git push` through _git_run: {[a for a, _ in git_calls]}"
    for args, bound in pushes:
        assert "timeout" in bound, f"`git {' '.join(args)}`: _git_run has no timeout argument"
        assert bound["timeout"] is None, (
            f"`git {' '.join(args)}` runs the user's pre-push hook with "
            f"timeout={bound['timeout']!r}; #584: commands that run user hooks pass "
            "timeout=None"
        )
        assert not bound.get("user_locale"), (
            f"`git {' '.join(args)}` left LC_ALL=C; its `[rejected]` text is matched (#806)"
        )


# --- 2. control: the fetch runs no hook and keeps a finite timeout -----------------


@pytest.mark.parametrize("cmd", list(COMMANDS))
def test_control_fetch_keeps_a_finite_timeout(world, monkeypatch, git_calls, cmd) -> None:
    result = _run(world, monkeypatch, cmd)

    assert result.exit_code == 0, result.output
    fetches = _calls(git_calls, "fetch")
    assert fetches, f"no `git fetch` through _git_run: {[a for a, _ in git_calls]}"
    for args, bound in fetches:
        assert bound["timeout"] is not None, (
            f"`git {' '.join(args)}` runs no user hook and must keep a finite timeout"
        )
