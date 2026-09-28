# ruff: noqa: F811  -- the `world` fixture imported from the claim tests is re-bound as an arg
"""Issue #574, PR C: after ``move --take-over``, the actor holds the item.

``claim --take-over`` (PR B) makes the actor the assignee. ``move --take-over``
does the same, so the file doesn't keep the old holder while its history says
they were taken over, and the actor's next move isn't refused as held.
``--assign X`` still wins. MCP ``kanban_move_item`` has no take-over (§4 gives
it only ``agent``), so there is nothing to pin there.
"""

from __future__ import annotations

from typing import Any

import pytest
from click.testing import CliRunner

from tests.issues.test_574_claim import (
    ITEM,
    ITEM_ID,
    A,
    B,
    _env,  # noqa: F401  (autouse: clean env, theme cache)
    frontmatter,
    nodes_by,
    seed,
    service,
    world,  # noqa: F401  (fixture)
)
from tests.issues.test_585_create_push_loop import World
from yurtle_kanban.cli import main
from yurtle_kanban.models import WorkItemStatus

S = WorkItemStatus


def assignee_of(world: World) -> str:
    return str(frontmatter((world.a / ITEM).read_text()).get("assignee"))


def status_of(world: World) -> S | None:
    svc = service(world.a)
    item = svc.get_item(ITEM_ID)
    assert item is not None
    return svc.resolve_status_name(item, str(frontmatter((world.a / ITEM).read_text())["status"]))


def invoke(world: World, monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> Any:
    monkeypatch.chdir(world.a)
    return CliRunner().invoke(main, argv)


def out_of(result: Any) -> str:
    return " ".join((result.output or "").split())


# --- service ------------------------------------------------------------------------


def test_service_take_over_makes_the_actor_the_holder(world) -> None:
    seed(world, "in_progress", B)

    item = service(world.a).move_item(ITEM_ID, S.REVIEW, actor=A, take_over=True)

    assert assignee_of(world) == A
    assert item.assignee == A
    text = (world.a / ITEM).read_text()
    assert any(f'kb:takenOverFrom "{B}"' in n for n in nodes_by(text, A)), text


def test_service_take_over_within_in_progress_then_the_actor_moves_unguarded(world) -> None:
    """The point of it: after a take-over the actor's next move is not 'held by B'.
    in_progress -> in_progress is illegal, so the take-over is forced."""
    seed(world, "in_progress", B)
    svc = service(world.a)

    svc.move_item(
        ITEM_ID, S.IN_PROGRESS, actor=A, take_over=True,
        validate_workflow=False, skip_wip_check=True, skip_gates=True,
    )
    assert assignee_of(world) == A

    svc.move_item(ITEM_ID, S.REVIEW, actor=A)  # no take_over: A holds it now
    assert status_of(world) == S.REVIEW


def test_service_take_over_with_assign_keeps_the_given_holder(world) -> None:
    seed(world, "in_progress", B)

    service(world.a).move_item(
        ITEM_ID, S.REVIEW, actor=A, take_over=True, assignee="agent-C"
    )

    assert assignee_of(world) == "agent-C"
    text = (world.a / ITEM).read_text()
    assert any(f'kb:takenOverFrom "{B}"' in n for n in nodes_by(text, A)), text


def test_service_holder_move_does_not_change_the_holder(world) -> None:
    """Control: no take-over, no --assign -> the assignee is not defaulted (#580)."""
    seed(world, "in_progress", B)

    service(world.a).move_item(ITEM_ID, S.REVIEW, actor=B)

    assert assignee_of(world) == B


# --- CLI ----------------------------------------------------------------------------


def test_cli_take_over_makes_the_agent_the_holder(world, monkeypatch) -> None:
    seed(world, "in_progress", B)

    result = invoke(world, monkeypatch, ["move", ITEM_ID, "review", "--take-over", "--agent", A])

    assert result.exit_code == 0, out_of(result)
    assert assignee_of(world) == A


def test_cli_take_over_by_yurtle_agent_makes_it_the_holder(world, monkeypatch) -> None:
    seed(world, "in_progress", B)
    monkeypatch.setenv("YURTLE_AGENT", A)

    result = invoke(world, monkeypatch, ["move", ITEM_ID, "review", "--take-over"])

    assert result.exit_code == 0, out_of(result)
    assert assignee_of(world) == A


def test_cli_take_over_with_assign_keeps_the_given_holder(world, monkeypatch) -> None:
    seed(world, "in_progress", B)

    result = invoke(
        world, monkeypatch,
        ["move", ITEM_ID, "review", "--take-over", "--agent", A, "--assign", "agent-C"],
    )

    assert result.exit_code == 0, out_of(result)
    assert assignee_of(world) == "agent-C"


def test_cli_after_take_over_the_agents_next_move_is_not_refused(world, monkeypatch) -> None:
    seed(world, "in_progress", B)

    first = invoke(  # in_progress -> in_progress is illegal, so it is forced
        world, monkeypatch,
        ["move", ITEM_ID, "in_progress", "--force", "--take-over", "--agent", A],
    )
    assert first.exit_code == 0, out_of(first)

    second = invoke(world, monkeypatch, ["move", ITEM_ID, "review", "--agent", A])

    assert second.exit_code == 0, out_of(second)
    assert status_of(world) == S.REVIEW
