"""Issue #825: ``claim`` and ``update --push`` print a refusing sync ``Outcome`` as
``Error: <message>``, as every other refusal does (#666).

[steer] (bucket 1): every CLI command that prints a sync ``Outcome`` prints a
non-zero outcome as one ``Error: <message>`` line and keeps its exit code
(1 refused, 3 lost, 4 unreachable, 5 busy, 6 push_refused). Zero outcomes (won,
local, noop) print as before, with no ``Error:``. The unreachable
``except InputRefused`` around ``update_item_push`` in ``cli.update`` is removed.

Harness: the #574 real-git ``World`` (a bare remote plus clones A and B). The CLI
cannot pass a seam, so ``lost`` and ``busy`` are driven by wrapping the real service
method so that it runs with a test ``Recorder`` seam: still real git, real races.
``update --push`` never yields ``lost`` from its own mutate (it has no holder), so
that one case monkeypatches ``update_item_push`` to return an ``Outcome("lost")``.

Test partner's readings (the driver may challenge):

a. "One line" is pinned as: the output has exactly one non-blank line, and it
   starts with ``Error: ``. The outcome's own message still follows the prefix.
b. The prefix is exactly ``Error: `` (capital E, colon, space), as ``_refuse``
   prints it; no doubled ``Error: Error:``.
c. The line is counted on stderr, where ``refuse()`` prints every refusal
   (#1080; ruled edit for #1086), and stdout stays empty. ``sync_and_push``
   logs each rejected push as a WARNING; a log record is not the outcome's line
   and is not pinned here.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from tests.issues.test_574_claim import ITEM, b_claims, item_text, push_from_a, seed
from tests.issues.test_574_sync_and_push import Recorder, rival
from tests.issues.test_585_create_push_loop import World, git
from yurtle_kanban import cli
from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import main
from yurtle_kanban.service import KanbanService
from yurtle_kanban.sync import Outcome

ITEM_ID = "EXP-001"
A = "agent-A"
B = "agent-B"
PREFIX = "Error: "


# --- harness ---------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    monkeypatch.delenv("YURTLE_AGENT", raising=False)
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


@pytest.fixture
def world(tmp_path: Path) -> World:
    """Origin and both clones hold EXP-001 at `ready`, unassigned, priority unset."""
    w = World(tmp_path)
    push_from_a(w, {ITEM: item_text("ready")}, "seed EXP-001")
    return w


def invoke(world: World, monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> Any:
    monkeypatch.chdir(world.a)
    return CliRunner().invoke(main, argv)


def lines(result: Any) -> list[str]:
    """Stderr's non-blank lines, less the retry warnings logged there, which are
    not the outcome's line (reading c); stdout carries nothing (#1080)."""
    assert not (result.stdout or "").strip(), f"refusal on stdout: {result.stdout!r}"
    return [
        ln
        for ln in (result.stderr or "").splitlines()
        if ln.strip() and not ln.startswith(("WARNING", "INFO", "DEBUG"))
    ]


def assert_error_line(result: Any, code: int, must_say: str | None = None) -> None:
    got = lines(result)
    assert result.exit_code == code, f"exit {result.exit_code}, output: {got}"
    assert len(got) == 1, f"a refusing outcome must print ONE line: {got}"
    assert got[0].startswith(PREFIX), f"no {PREFIX!r} prefix: {got[0]!r}"
    assert not got[0].startswith(PREFIX + PREFIX), f"doubled prefix: {got[0]!r}"
    if must_say is not None:
        assert re.search(must_say, got[0], re.I), got[0]


def assert_no_error(result: Any) -> None:
    out = result.output or ""
    assert result.exit_code == 0, out
    assert "Error:" not in out, out


def with_seam(monkeypatch: pytest.MonkeyPatch, name: str, rec: Recorder) -> None:
    """Run the real `KanbanService.<name>` with `rec`'s seam, sleep and jitter."""
    real = getattr(KanbanService, name)

    def wrapped(self: KanbanService, *args: Any, **kwargs: Any) -> Any:
        # a caller's own seam wins: B's claim inside A's seam runs as it asked
        kwargs.setdefault("sleep", rec.sleep)
        kwargs.setdefault("jitter", rec.jitter)
        kwargs.setdefault("seam", rec.seam)
        return real(self, *args, **kwargs)

    monkeypatch.setattr(KanbanService, name, wrapped)


def install_declining_update_hook(world: World) -> None:
    hook = world.remote / "hooks" / "update"
    hook.write_text(
        "#!/bin/sh\necho 'kanban-guard: main is frozen for release' >&2\nexit 1\n"
    )
    hook.chmod(0o755)


def break_remote(world: World) -> None:
    git(world.a, "remote", "set-url", "origin", str(world.a.parent / "missing.git"))


CLAIM = ["claim", ITEM_ID, "--agent", A]
UPDATE_PUSH = ["update", ITEM_ID, "--priority", "high", "--push"]


# --- claim: non-zero outcomes print as Error: ---------------------------------------


def test_claim_refused_held_by_other_prints_error(world, monkeypatch) -> None:
    b_claims(world)

    result = invoke(world, monkeypatch, CLAIM)

    assert_error_line(result, 1, r"held by agent-B")


def test_claim_lost_prints_error(world, monkeypatch) -> None:
    rec = Recorder(lambda attempt: b_claims(world) if attempt == 0 else None)
    with_seam(monkeypatch, "claim_item", rec)

    result = invoke(world, monkeypatch, CLAIM)

    assert_error_line(result, 3, r"lost to agent-B")
    assert rec.seams == [0]


def test_claim_unreachable_prints_error(world, monkeypatch) -> None:
    break_remote(world)

    result = invoke(world, monkeypatch, CLAIM)

    assert_error_line(result, 4)


def test_claim_busy_prints_error(world, monkeypatch) -> None:
    rec = Recorder(lambda attempt: rival(world, attempt))
    with_seam(monkeypatch, "claim_item", rec)

    result = invoke(world, monkeypatch, CLAIM)

    assert_error_line(result, 5)


def test_claim_push_refused_prints_error(world, monkeypatch) -> None:
    install_declining_update_hook(world)

    result = invoke(world, monkeypatch, CLAIM)

    assert_error_line(result, 6, r"main is frozen for release")


# --- update --push: non-zero outcomes print as Error: -------------------------------


def test_update_push_refused_prints_error(world, monkeypatch) -> None:
    result = invoke(world, monkeypatch, ["update", ITEM_ID, "--add-dep", "EXP-001", "--push"])

    assert_error_line(result, 1)


def test_update_push_lost_prints_error(world, monkeypatch) -> None:
    """`update --push` has no holder to lose to; the CLI still owes the prefix."""
    monkeypatch.setattr(
        KanbanService,
        "update_item_push",
        lambda self, *a, **k: Outcome(kind="lost", message="Lost to agent-B: EXP-001"),
    )

    result = invoke(world, monkeypatch, UPDATE_PUSH)

    assert_error_line(result, 3, r"lost to agent-B")


def test_update_push_unreachable_prints_error(world, monkeypatch) -> None:
    break_remote(world)

    result = invoke(world, monkeypatch, UPDATE_PUSH)

    assert_error_line(result, 4)


def test_update_push_busy_prints_error(world, monkeypatch) -> None:
    rec = Recorder(lambda attempt: rival(world, attempt))
    with_seam(monkeypatch, "update_item_push", rec)

    result = invoke(world, monkeypatch, UPDATE_PUSH)

    assert_error_line(result, 5)


def test_update_push_push_refused_prints_error(world, monkeypatch) -> None:
    install_declining_update_hook(world)

    result = invoke(world, monkeypatch, UPDATE_PUSH)

    assert_error_line(result, 6, r"main is frozen for release")


# --- controls: zero outcomes print as before ----------------------------------------


def test_control_claim_won_has_no_error(world, monkeypatch) -> None:
    assert_no_error(invoke(world, monkeypatch, CLAIM))


def test_control_claim_noop_has_no_error(world, monkeypatch) -> None:
    seed(world, "in_progress", A)

    result = invoke(world, monkeypatch, CLAIM)

    assert_no_error(result)
    assert "already yours" in result.output.lower(), result.output


def test_control_claim_local_has_no_error(world, monkeypatch) -> None:
    git(world.a, "remote", "remove", "origin")

    result = invoke(world, monkeypatch, CLAIM)

    assert_no_error(result)
    assert "no remote" in result.output.lower(), result.output


def test_control_update_push_won_has_no_error(world, monkeypatch) -> None:
    assert_no_error(invoke(world, monkeypatch, UPDATE_PUSH))


def test_control_update_push_noop_has_no_error(world, monkeypatch) -> None:
    assert_no_error(invoke(world, monkeypatch, UPDATE_PUSH))  # now high on origin
    base = world.remote_sha()

    result = invoke(world, monkeypatch, UPDATE_PUSH)

    assert_no_error(result)
    assert world.remote_sha() == base, "the second, identical edit must be a noop"


def test_control_update_push_local_has_no_error(world, monkeypatch) -> None:
    git(world.a, "remote", "remove", "origin")

    result = invoke(world, monkeypatch, UPDATE_PUSH)

    assert_no_error(result)
    assert "no remote" in result.output.lower(), result.output


# --- structural: the dead except around update_item_push is gone -----------------------


def test_update_has_no_except_input_refused_around_update_item_push() -> None:
    source = inspect.getsource(cli.update.callback)
    assert "update_item_push" in source, "update --push no longer calls update_item_push?"
    start = source.index("update_item_push")
    window = source[max(0, start - 200) : start + 300]
    assert "except InputRefused" not in window, (
        "the except InputRefused around update_item_push never runs (#825): remove it\n"
        + window
    )
