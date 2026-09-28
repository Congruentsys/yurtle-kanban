# ruff: noqa: F811  -- the `world` fixture imported from the claim tests is re-bound as an arg
"""Issue #823: one take-over rule for ``claim`` and ``move``.

#574 PR B (``claim --take-over``) and PR C (``move --take-over``) record
``kb:takenOverFrom`` differently. ``claim`` records ``""`` whenever there was no
holder, even for a ready unassigned item. ``move`` records nothing for an in-progress
item with no holder. The [steer] on #823 sets one rule:

1. ``--take-over`` records ``kb:takenOverFrom`` ONLY when it overrides something:
   - an item someone else holds: the value is that holder;
   - an in-progress item with no holder: the value is ``""``.
   A take-over of an item neither command would refuse (a ready unassigned item,
   or the actor's own item) records NO ``kb:takenOverFrom``, in both commands.
2. ``claim`` refuses an in-progress item with no holder; ``move`` lets it through
   without ``--take-over``. The difference is intended. Both are pinned here as
   controls, and both commands' ``--take-over`` help must say so ("no holder").

The grid is {claim, move} x {held by other, in progress with no holder, ready
unassigned, own item}, at the service and through the CLI. Each cell asserts the
presence and value of ``kb:takenOverFrom`` in the history node(s) the command adds.

Ambiguities resolved here (the test partner's reading; the driver may challenge):

a. "Held by other" is ``in_progress`` held by agent-B: the one state both commands
   refuse without ``--take-over``. A non-in-progress item held by someone else is
   refused by ``claim`` but not guarded by ``move``; that cell is not pinned here.
b. The claim's "own item" is ``ready`` held by agent-A, so the claim writes a node
   (own AND in progress is ``already yours``, a no-op: pinned separately, it must
   write nothing). The move's "own item" is ``in_progress`` held by agent-A.
c. Move targets: ``review`` from in progress, ``in_progress`` from ready. Claim
   always targets in progress.
d. "The new history node" is the multiset difference of the item's ``[ ... ]``
   blank nodes before and after the command. Exactly one node must be new, and it
   must carry ``kb:by "agent-A"``.
e. Who holds the item after a ``move --take-over`` without ``--assign`` is not
   pinned (as #574 C's ambiguity d). Neither is the claim's outcome message.
f. The help wording is pinned only as the phrase "no holder" in each command's
   ``--take-over`` option help, whitespace-normalised, and in ``--help`` output.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

import pytest
from click.testing import CliRunner

from tests.issues.test_574_claim import (
    ITEM,
    ITEM_ID,
    A,
    B,
    _env,  # noqa: F401  (autouse: clean env, theme cache)
    claim,
    frontmatter,
    history_nodes,
    seed,
    service,
    world,  # noqa: F401  (fixture)
)
from tests.issues.test_574_sync_and_push import snapshot
from tests.issues.test_585_create_push_loop import World
from yurtle_kanban.cli import main
from yurtle_kanban.models import WorkItemStatus

S = WorkItemStatus
TAKEN = re.compile(r'kb:takenOverFrom\s+"((?:[^"\\]|\\.)*)"')

# cell -> (seeded status, seeded assignee, expected kb:takenOverFrom or None)
CLAIM_CELLS = {
    "held_by_other": ("in_progress", B, B),
    "in_progress_no_holder": ("in_progress", None, ""),
    "ready_unassigned": ("ready", None, None),
    "own_item": ("ready", A, None),
}
MOVE_CELLS = {
    "held_by_other": ("in_progress", B, S.REVIEW, B),
    "in_progress_no_holder": ("in_progress", None, S.REVIEW, ""),
    "ready_unassigned": ("ready", None, S.IN_PROGRESS, None),
    "own_item": ("in_progress", A, S.REVIEW, None),
}


# --- harness ------------------------------------------------------------------------------


def seed_cell(world: World, status: str, assignee: str | None) -> None:
    if status == "ready" and assignee is None:
        return  # the `world` fixture already holds EXP-001 at ready, unassigned
    seed(world, status, assignee)


def new_node(before: str, after: str) -> str:
    """The one status-history node `after` has that `before` did not."""
    added = Counter(history_nodes(after)) - Counter(history_nodes(before))
    nodes = list(added.elements())
    assert len(nodes) == 1, f"expected exactly one new history node, got {len(nodes)}:\n{after}"
    node = nodes[0]
    assert f'kb:by "{A}"' in node, f'the new node is not kb:by "{A}":\n{node}'
    return node


def assert_taken_over(node: str, expected: str | None, cell: str) -> None:
    values = TAKEN.findall(node)
    if expected is None:
        assert "takenOverFrom" not in node, (
            f"{cell}: a take-over that overrode nothing recorded kb:takenOverFrom:\n{node}"
        )
    else:
        assert values == [expected], (
            f"{cell}: expected kb:takenOverFrom {expected!r}, got {values}:\n{node}"
        )


def invoke(world: World, monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> Any:
    monkeypatch.chdir(world.a)
    return CliRunner().invoke(main, argv)


def out_of(result: Any) -> str:
    return " ".join((result.output or "").split())


def status_of(world: World) -> S | None:
    svc = service(world.a)
    item = svc.get_item(ITEM_ID)
    assert item is not None
    return svc.resolve_status_name(item, str(frontmatter((world.a / ITEM).read_text())["status"]))


def take_over_help(command: str) -> str:
    param = next(p for p in main.commands[command].params if p.name == "take_over")
    return " ".join((getattr(param, "help", "") or "").split())


# --- claim --take-over: the grid, at the service ---------------------------------------------


@pytest.mark.parametrize("cell", list(CLAIM_CELLS))
def test_service_claim_take_over(world, cell) -> None:
    status, assignee, expected = CLAIM_CELLS[cell]
    seed_cell(world, status, assignee)
    before = world.remote_show(ITEM)

    out = claim(world.a, A, take_over=True)

    assert out.kind == "won", f"{cell}: {out.kind}: {out.message}"
    after = world.remote_show(ITEM)
    assert frontmatter(after)["assignee"] == A
    assert_taken_over(new_node(before, after), expected, cell)


# --- claim --take-over: the grid, through the CLI ---------------------------------------------


@pytest.mark.parametrize("cell", list(CLAIM_CELLS))
def test_cli_claim_take_over(world, monkeypatch, cell) -> None:
    status, assignee, expected = CLAIM_CELLS[cell]
    seed_cell(world, status, assignee)
    before = world.remote_show(ITEM)

    result = invoke(world, monkeypatch, ["claim", ITEM_ID, "--agent", A, "--take-over"])

    assert result.exit_code == 0, f"{cell}: {out_of(result)}"
    after = world.remote_show(ITEM)
    assert frontmatter(after)["assignee"] == A
    assert_taken_over(new_node(before, after), expected, cell)


def test_claim_take_over_of_own_in_progress_item_writes_nothing(world) -> None:
    """Own AND in progress is `already yours`: --take-over does not turn it into one."""
    seed(world, "in_progress", A)
    base = world.remote_sha()
    before = snapshot(world.a)

    out = claim(world.a, A, take_over=True)

    assert out.kind == "noop", out.message
    assert world.remote_sha() == base
    assert snapshot(world.a) == before
    assert "takenOverFrom" not in world.remote_show(ITEM)


# --- move --take-over: the grid, at the service -----------------------------------------------


@pytest.mark.parametrize("cell", list(MOVE_CELLS))
def test_service_move_take_over(world, cell) -> None:
    status, assignee, target, expected = MOVE_CELLS[cell]
    seed_cell(world, status, assignee)
    before = (world.a / ITEM).read_text()

    service(world.a).move_item(ITEM_ID, target, actor=A, take_over=True)

    assert status_of(world) == target
    after = (world.a / ITEM).read_text()
    assert_taken_over(new_node(before, after), expected, cell)


# --- move --take-over: the grid, through the CLI ----------------------------------------------


@pytest.mark.parametrize("cell", list(MOVE_CELLS))
def test_cli_move_take_over(world, monkeypatch, cell) -> None:
    status, assignee, target, expected = MOVE_CELLS[cell]
    seed_cell(world, status, assignee)
    before = (world.a / ITEM).read_text()

    result = invoke(
        world, monkeypatch, ["move", ITEM_ID, target.value, "--take-over", "--agent", A]
    )

    assert result.exit_code == 0, f"{cell}: {out_of(result)}"
    assert status_of(world) == target
    after = (world.a / ITEM).read_text()
    assert_taken_over(new_node(before, after), expected, cell)


# --- controls: the intended difference on an in-progress item with no holder -------------------


def test_control_service_claim_refuses_in_progress_with_no_holder(world) -> None:
    seed(world, "in_progress")
    base = world.remote_sha()

    out = claim(world.a, A)

    assert out.kind == "refused", out.message
    assert out.exit_code == 1
    assert "no holder" in out.message, out.message
    assert "--take-over" in out.message, out.message
    assert world.remote_sha() == base


def test_control_cli_claim_refuses_in_progress_with_no_holder(world, monkeypatch) -> None:
    seed(world, "in_progress")
    base = world.remote_sha()

    result = invoke(world, monkeypatch, ["claim", ITEM_ID, "--agent", A])

    assert result.exit_code == 1, out_of(result)
    assert "no holder" in out_of(result), out_of(result)
    assert world.remote_sha() == base


def test_control_service_move_allows_in_progress_with_no_holder(world) -> None:
    seed(world, "in_progress")
    before = (world.a / ITEM).read_text()

    service(world.a).move_item(ITEM_ID, S.REVIEW, actor=A)

    assert status_of(world) == S.REVIEW
    after = (world.a / ITEM).read_text()
    assert_taken_over(new_node(before, after), None, "move without --take-over")


def test_control_cli_move_allows_in_progress_with_no_holder(world, monkeypatch) -> None:
    seed(world, "in_progress")
    before = (world.a / ITEM).read_text()

    result = invoke(world, monkeypatch, ["move", ITEM_ID, "review", "--agent", A])

    assert result.exit_code == 0, out_of(result)
    assert status_of(world) == S.REVIEW
    after = (world.a / ITEM).read_text()
    assert_taken_over(new_node(before, after), None, "move without --take-over")


# --- the difference is documented in both commands' --take-over help ---------------------------


@pytest.mark.parametrize("command", ["claim", "move"])
def test_take_over_help_mentions_no_holder(command) -> None:
    text = take_over_help(command)
    assert "no holder" in text.lower(), f"{command} --take-over help: {text!r}"


@pytest.mark.parametrize("command", ["claim", "move"])
def test_help_output_mentions_no_holder(world, monkeypatch, command) -> None:
    result = invoke(world, monkeypatch, [command, "--help"])

    assert result.exit_code == 0, out_of(result)
    assert "--take-over" in result.output
    assert "no holder" in out_of(result).lower(), out_of(result)
