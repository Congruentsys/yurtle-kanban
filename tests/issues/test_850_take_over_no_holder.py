# ruff: noqa: F811  -- the `world` fixture imported from the claim tests is re-bound as an arg
"""Issue #850: ``move --take-over`` of an in-progress item with no holder names
"no holder" in its commit message, as ``claim`` does.

Before: ``Move EXP-001 to review (assigned to agent-A) (taken over from )``.
Spec ([steer] on #850, bucket 1):

- the local commit's subject ends ``(taken over from no holder)``;
- the item's ``kb:takenOverFrom ""`` record is unchanged (``""`` means no holder, #823);
- control: with a real holder the subject ends ``(taken over from agent-B)``;
- parity control: ``claim --take-over`` already says ``no holder`` on origin.

``move`` has no ``--push`` path (it commits locally only), so the local commit is
the only move message to cover.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from tests.issues.test_574_claim import (
    ITEM,
    ITEM_ID,
    A,
    B,
    _env,  # noqa: F401  (autouse: clean env, theme cache)
    nodes_by,
    seed,
    world,  # noqa: F401  (fixture)
)
from tests.issues.test_585_create_push_loop import World
from yurtle_kanban.cli import main


def invoke(world: World, monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> Any:
    monkeypatch.chdir(world.a)
    return CliRunner().invoke(main, argv)


def out_of(result: Any) -> str:
    return " ".join((result.output or "").split())


def subject(repo: Path, ref: str = "HEAD") -> str:
    return subprocess.run(
        ["git", "log", "-1", "--format=%s", ref],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def move_take_over(world: World, monkeypatch: pytest.MonkeyPatch) -> Any:
    result = invoke(world, monkeypatch, ["move", ITEM_ID, "review", "--take-over", "--agent", A])
    assert result.exit_code == 0, out_of(result)
    return result


# --- the fix ------------------------------------------------------------------------


def test_move_take_over_with_no_holder_names_no_holder(world, monkeypatch) -> None:
    seed(world, "in_progress", None)

    move_take_over(world, monkeypatch)

    msg = subject(world.a)
    assert msg.startswith(f"Move {ITEM_ID} to review"), msg
    assert msg.endswith("(taken over from no holder)"), msg


def test_move_take_over_with_no_holder_has_no_empty_name(world, monkeypatch) -> None:
    seed(world, "in_progress", None)

    move_take_over(world, monkeypatch)

    msg = subject(world.a)
    assert "(taken over from )" not in msg, msg


# --- controls -----------------------------------------------------------------------


def test_control_move_take_over_with_no_holder_records_empty_taken_over_from(
    world, monkeypatch
) -> None:
    """The record is unchanged: `""` is how the file says there was no holder (#823)."""
    seed(world, "in_progress", None)

    move_take_over(world, monkeypatch)

    text = (world.a / ITEM).read_text()
    assert any('kb:takenOverFrom ""' in n for n in nodes_by(text, A)), text
    assert "no holder" not in text, text


def test_control_move_take_over_from_a_holder_names_the_holder(world, monkeypatch) -> None:
    seed(world, "in_progress", B)

    move_take_over(world, monkeypatch)

    msg = subject(world.a)
    assert msg.endswith(f"(taken over from {B})"), msg


def test_control_claim_take_over_with_no_holder_names_no_holder(world, monkeypatch) -> None:
    """Parity: `claim` already says `no holder`; its commit is on origin's default branch."""
    seed(world, "in_progress", None)

    result = invoke(world, monkeypatch, ["claim", ITEM_ID, "--take-over", "--agent", A])
    assert result.exit_code == 0, out_of(result)

    msg = subject(world.remote, world.default)
    assert msg.endswith("(taken over from no holder)"), msg
