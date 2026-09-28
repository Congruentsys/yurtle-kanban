# ruff: noqa: F811  -- the `world` fixture imported from the claim tests is re-bound as an arg
"""Issue #574, PR C: ``move``'s holder guard and ``--take-over`` (spec §4).

The [steer] on #574 splits the build into four PRs; this is C. It is local and
advisory: ``move`` never fetches. ``claim`` (PR B, tests/issues/test_574_claim.py)
stays the only authoritative gate.

API under test:

- ``KanbanService.move_item(item_id, new_status, ..., actor=None, take_over=False)``.
  ``actor`` already exists (#580) and is resolved through ``resolve_actor``.
- CLI ``yurtle-kanban move ID STATUS [--agent A] [--take-over] [--force]``.
- MCP ``kanban_move_item`` gains an optional string ``agent`` argument, resolved
  through ``resolve_actor``.

The guard (§4):

- The item's CANONICAL status is ``in_progress`` (nautical ``underway`` counts),
  and its assignee is someone else: ``same_actor(assignee, resolve_actor(--agent))``
  is false. The move is refused with exit 1 and exactly
  ``EXP-001 is held by agent-B; if you are agent-B pass --agent agent-B or set
  YURTLE_AGENT; to take it over use --take-over``. Nothing is written or committed.
- Other statuses are not guarded. Neither is an unassigned in-progress item.
- ``--take-over`` needs an explicit actor (``--agent`` or ``$YURTLE_AGENT``). It
  records ``kb:takenOverFrom "<old holder>"`` in the move's status-history node,
  and gates, WIP and legality still apply.
- ``--force`` keeps its meaning (skip WIP, workflow and gates; ``kb:forcedMove``).
  It does NOT override the holder guard.

Ambiguities resolved here (the test partner's reading; the driver may challenge):

a. Guard refusals are ``InputRefused`` at the service layer (MCP then answers
   with an error result and no traceback, as #728/#786 require). The message is
   pinned EXACTLY at the service and in MCP. The CLI output is whitespace-
   normalised (Rich may wrap it) and must contain it.
b. The exact wording is the spec's template with the real ID and holder,
   ``EXP-001`` and ``agent-B``. The holder is named as the item's file stores it.
c. "Nothing written or committed" is PR A's ``snapshot`` of clone A: HEAD, branch,
   index, status and every file's bytes are identical.
d. Who holds the item after ``--take-over``, when ``--assign`` is not given, is NOT
   pinned (§4 does not say). Only the ``kb:takenOverFrom "agent-B"`` in a node with
   ``kb:by "agent-A"`` is pinned.
e. "Requires an explicit actor" is pinned at both the CLI and the service. The
   service is called with ``actor=None``, ``$YURTLE_AGENT`` unset, and clone A's git
   ``user.name`` set to ``A``. It is refused (``InputRefused``), and nothing is
   written. So the git fallback must not satisfy ``--take-over``.
f. ``--force --take-over`` is allowed. The node then carries both
   ``kb:forcedMove`` and ``kb:takenOverFrom``.
g. ``--take-over`` of an item that is not held by someone else is not pinned: it
   is neither required to record a take-over nor to refuse.
h. The failing gate is the claim tests' ``context.self_reviewed`` check, keyed
   ``* -> review``. The WIP check is a multi-board ``wip_limits: {review: 1}``
   with another expedition already in review.
i. The MCP ``agent`` argument is typed ``string`` in the schema and is not required.
   That gives it #801's "agent must be a string" refusal for free.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from click.testing import CliRunner

from tests.issues.test_574_claim import (
    EXP_DIR,
    GATE_FAILING,
    ITEM,
    ITEM_ID,
    A,
    B,
    _env,  # noqa: F401  (autouse: clean env, theme cache)
    frontmatter,
    item_text,
    native_in_progress,
    nodes_by,
    push_from_a,
    seed,
    service,
    set_gates,
    world,  # noqa: F401  (fixture)
)
from tests.issues.test_574_sync_and_push import snapshot
from tests.issues.test_585_create_push_loop import World, git
from yurtle_kanban.cli import main
from yurtle_kanban.mcp import server as mcp_server
from yurtle_kanban.models import InputRefused, WorkItemStatus

S = WorkItemStatus


def held(holder: str = B, item_id: str = ITEM_ID) -> str:
    return (
        f"{item_id} is held by {holder}; if you are {holder} pass --agent {holder} "
        "or set YURTLE_AGENT; to take it over use --take-over"
    )


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


def mcp(world: World) -> Any:
    return mcp_server.KanbanMCPServer(repo_root=world.a)


def mcp_move(world: World, caplog: pytest.LogCaptureFixture, **args: Any) -> dict[str, Any]:
    """Call kanban_move_item on A, asserting nothing was logged with a traceback."""
    log = logging.getLogger("yurtle-kanban")
    log.addHandler(caplog.handler)
    try:
        out = mcp(world).handle_tool_call("kanban_move_item", {"item_id": ITEM_ID, **args})
    finally:
        log.removeHandler(caplog.handler)
    tracebacks = [r.getMessage() for r in caplog.records if r.exc_info is not None]
    assert not tracebacks, f"logged with a traceback: {tracebacks}"
    return out


WIP_REVIEW_CONFIG = """\
version: "2.0"
boards:
  - name: development
    preset: nautical
    path: kanban-work/
    wip_limits:
      review: 1
default_board: development
"""
OTHER = f"{EXP_DIR}/EXP-002-y.md"
GATE_REVIEW = {"* -> review": GATE_FAILING["* -> in_progress"]}


# --- the guard, at the service -------------------------------------------------------


def test_service_refuses_moving_someone_elses_in_progress_item(world) -> None:
    seed(world, "in_progress", B)
    before = snapshot(world.a)

    with pytest.raises(InputRefused) as exc:
        service(world.a).move_item(ITEM_ID, S.REVIEW, actor=A)

    assert str(exc.value) == held()
    assert snapshot(world.a) == before, "a refused move wrote or committed something"


def test_service_guard_reads_the_canonical_status(world) -> None:
    """The file says nautical `underway`; that is canonical in_progress, so guarded."""
    seed(world, native_in_progress(world.a), B)
    assert frontmatter((world.a / ITEM).read_text())["status"] == "underway"
    before = snapshot(world.a)

    with pytest.raises(InputRefused) as exc:
        service(world.a).move_item(ITEM_ID, S.REVIEW, actor=A)

    assert str(exc.value) == held()
    assert snapshot(world.a) == before


def test_service_guard_uses_yurtle_agent_when_actor_is_none(world, monkeypatch) -> None:
    seed(world, "in_progress", B)
    monkeypatch.setenv("YURTLE_AGENT", A)
    before = snapshot(world.a)

    with pytest.raises(InputRefused) as exc:
        service(world.a).move_item(ITEM_ID, S.REVIEW)

    assert str(exc.value) == held()
    assert snapshot(world.a) == before


@pytest.mark.parametrize("target", [S.REVIEW, S.DONE, S.BLOCKED, S.READY])
def test_service_guards_every_target(world, target) -> None:
    seed(world, "in_progress", B)
    before = snapshot(world.a)

    with pytest.raises(InputRefused) as exc:
        service(world.a).move_item(ITEM_ID, target, actor=A)

    assert str(exc.value) == held()
    assert snapshot(world.a) == before


def test_service_force_alone_does_not_override_the_holder(world) -> None:
    """`--force` is validate_workflow=False, skip_wip_check=True, skip_gates=True."""
    seed(world, "in_progress", B)
    before = snapshot(world.a)

    with pytest.raises(InputRefused) as exc:
        service(world.a).move_item(
            ITEM_ID, S.REVIEW, actor=A,
            validate_workflow=False, skip_wip_check=True, skip_gates=True,
        )

    assert str(exc.value) == held()
    assert snapshot(world.a) == before


# --- --take-over, at the service ------------------------------------------------------


def test_service_take_over_moves_and_records_the_old_holder(world) -> None:
    seed(world, "in_progress", B)

    service(world.a).move_item(ITEM_ID, S.REVIEW, actor=A, take_over=True)

    assert status_of(world) == S.REVIEW
    text = (world.a / ITEM).read_text()
    assert any(f'kb:takenOverFrom "{B}"' in n for n in nodes_by(text, A)), (
        f'no node with kb:by "{A}" recording kb:takenOverFrom "{B}":\n{text}'
    )
    assert git(world.a, "status", "--porcelain").strip() == "", "the move was not committed"


def test_service_take_over_with_force_records_both(world) -> None:
    seed(world, "in_progress", B)

    service(world.a).move_item(
        ITEM_ID, S.REVIEW, actor=A, take_over=True,
        validate_workflow=False, skip_wip_check=True, skip_gates=True,
    )

    text = (world.a / ITEM).read_text()
    mine = [n for n in nodes_by(text, A) if f'kb:takenOverFrom "{B}"' in n]
    assert mine, text
    assert any("kb:forcedMove" in n for n in mine), f"the forced take-over lost kb:forcedMove:\n{text}"


def test_service_take_over_needs_an_explicit_actor(world) -> None:
    """actor=None, no $YURTLE_AGENT: git user.name (`A`) must not do."""
    assert git(world.a, "config", "user.name").strip() == "A"
    seed(world, "in_progress", B)
    before = snapshot(world.a)

    with pytest.raises(InputRefused):
        service(world.a).move_item(ITEM_ID, S.REVIEW, take_over=True)

    assert snapshot(world.a) == before


def test_service_take_over_still_answers_to_gates(world) -> None:
    set_gates(world, GATE_REVIEW)
    seed(world, "in_progress", B)
    before = snapshot(world.a)

    with pytest.raises(InputRefused) as exc:
        service(world.a).move_item(ITEM_ID, S.REVIEW, actor=A, take_over=True)

    assert "Self-review gate 574b not satisfied" in str(exc.value)
    assert snapshot(world.a) == before


def test_service_take_over_still_answers_to_wip(world) -> None:
    push_from_a(
        world,
        {".kanban/config.yaml": WIP_REVIEW_CONFIG, OTHER: item_text("review", B, "EXP-002", "Y")},
        "board: review wip 1",
    )
    seed(world, "in_progress", B)
    before = snapshot(world.a)

    with pytest.raises(InputRefused) as exc:
        service(world.a).move_item(ITEM_ID, S.REVIEW, actor=A, take_over=True)

    assert "wip" in str(exc.value).lower(), str(exc.value)
    assert snapshot(world.a) == before


def test_service_take_over_still_answers_to_legality(world) -> None:
    seed(world, "in_progress", B)
    before = snapshot(world.a)

    with pytest.raises(InputRefused) as exc:
        service(world.a).move_item(ITEM_ID, S.BACKLOG, actor=A, take_over=True)

    assert "illegal move" in str(exc.value).lower(), str(exc.value)
    assert snapshot(world.a) == before


# --- controls, at the service (green on the parent) ----------------------------------------


@pytest.mark.parametrize("as_actor", [B, "AGENT-b"])
def test_service_holder_moves_own_item(world, as_actor) -> None:
    seed(world, "in_progress", B)

    service(world.a).move_item(ITEM_ID, S.REVIEW, actor=as_actor)

    assert status_of(world) == S.REVIEW
    assert "takenOverFrom" not in (world.a / ITEM).read_text()


def test_service_unassigned_in_progress_is_not_guarded(world) -> None:
    seed(world, "in_progress")

    service(world.a).move_item(ITEM_ID, S.REVIEW, actor=A)

    assert status_of(world) == S.REVIEW


@pytest.mark.parametrize(("frm", "to"), [
    ("review", S.DONE),  # the reviewer's review -> done
    ("ready", S.IN_PROGRESS),
    ("blocked", S.IN_PROGRESS),
])
def test_service_other_statuses_are_not_guarded(world, frm, to) -> None:
    seed(world, frm, B)

    service(world.a).move_item(ITEM_ID, to, actor=A)

    assert status_of(world) == to


# --- CLI -------------------------------------------------------------------------------


def test_cli_refuses_someone_elses_in_progress_item(world, monkeypatch) -> None:
    seed(world, "in_progress", B)
    before = snapshot(world.a)

    result = invoke(world, monkeypatch, ["move", ITEM_ID, "review", "--agent", A])

    assert result.exit_code == 1, out_of(result)
    assert held() in out_of(result), out_of(result)
    assert snapshot(world.a) == before


def test_cli_refuses_by_yurtle_agent(world, monkeypatch) -> None:
    seed(world, "in_progress", B)
    monkeypatch.setenv("YURTLE_AGENT", A)
    before = snapshot(world.a)

    result = invoke(world, monkeypatch, ["move", ITEM_ID, "review"])

    assert result.exit_code == 1, out_of(result)
    assert held() in out_of(result), out_of(result)
    assert snapshot(world.a) == before


def test_cli_force_alone_is_still_refused(world, monkeypatch) -> None:
    seed(world, "in_progress", B)
    before = snapshot(world.a)

    result = invoke(world, monkeypatch, ["move", ITEM_ID, "review", "--force", "--agent", A])

    assert result.exit_code == 1, out_of(result)
    assert held() in out_of(result), out_of(result)
    assert snapshot(world.a) == before


def test_cli_take_over_with_agent_succeeds_and_records_it(world, monkeypatch) -> None:
    seed(world, "in_progress", B)

    result = invoke(
        world, monkeypatch, ["move", ITEM_ID, "review", "--take-over", "--agent", A]
    )

    assert result.exit_code == 0, out_of(result)
    assert status_of(world) == S.REVIEW
    text = (world.a / ITEM).read_text()
    assert any(f'kb:takenOverFrom "{B}"' in n for n in nodes_by(text, A)), text


def test_cli_take_over_with_yurtle_agent_succeeds(world, monkeypatch) -> None:
    seed(world, "in_progress", B)
    monkeypatch.setenv("YURTLE_AGENT", A)

    result = invoke(world, monkeypatch, ["move", ITEM_ID, "review", "--take-over"])

    assert result.exit_code == 0, out_of(result)
    text = (world.a / ITEM).read_text()
    assert any(f'kb:takenOverFrom "{B}"' in n for n in nodes_by(text, A)), text


def test_cli_take_over_without_an_explicit_actor_is_refused(world, monkeypatch) -> None:
    assert git(world.a, "config", "user.name").strip(), "A must have a git user.name"
    seed(world, "in_progress", B)
    before = snapshot(world.a)

    result = invoke(world, monkeypatch, ["move", ITEM_ID, "review", "--take-over"])

    out = out_of(result)
    assert result.exit_code == 1, out
    assert "--agent" in out and "YURTLE_AGENT" in out, out
    assert snapshot(world.a) == before


def test_cli_force_with_take_over_succeeds(world, monkeypatch) -> None:
    seed(world, "in_progress", B)

    result = invoke(
        world, monkeypatch,
        ["move", ITEM_ID, "review", "--force", "--take-over", "--agent", A],
    )

    assert result.exit_code == 0, out_of(result)
    text = (world.a / ITEM).read_text()
    assert any(f'kb:takenOverFrom "{B}"' in n for n in nodes_by(text, A)), text


def test_cli_take_over_still_answers_to_gates(world, monkeypatch) -> None:
    set_gates(world, GATE_REVIEW)
    seed(world, "in_progress", B)
    before = snapshot(world.a)

    result = invoke(
        world, monkeypatch, ["move", ITEM_ID, "review", "--take-over", "--agent", A]
    )

    assert result.exit_code == 1, out_of(result)
    assert "Self-review gate 574b not satisfied" in out_of(result)
    assert snapshot(world.a) == before


def test_cli_help_lists_take_over(world, monkeypatch) -> None:
    result = invoke(world, monkeypatch, ["move", "--help"])

    assert result.exit_code == 0, out_of(result)
    assert "--take-over" in result.output


# --- CLI controls (green on the parent) --------------------------------------------------------


@pytest.mark.parametrize("as_actor", [B, "Agent-B"])
def test_cli_holder_moves_own_item(world, monkeypatch, as_actor) -> None:
    seed(world, "in_progress", B)

    result = invoke(world, monkeypatch, ["move", ITEM_ID, "review", "--agent", as_actor])

    assert result.exit_code == 0, out_of(result)
    assert status_of(world) == S.REVIEW


def test_cli_holder_by_yurtle_agent(world, monkeypatch) -> None:
    seed(world, "in_progress", B)
    monkeypatch.setenv("YURTLE_AGENT", B)

    result = invoke(world, monkeypatch, ["move", ITEM_ID, "review"])

    assert result.exit_code == 0, out_of(result)
    assert status_of(world) == S.REVIEW


def test_cli_reviewer_review_to_done_is_not_guarded(world, monkeypatch) -> None:
    seed(world, "review", B)

    result = invoke(world, monkeypatch, ["move", ITEM_ID, "done", "--agent", A])

    assert result.exit_code == 0, out_of(result)
    assert status_of(world) == S.DONE


def test_cli_unassigned_item_moves_as_before(world, monkeypatch) -> None:
    result = invoke(world, monkeypatch, ["move", ITEM_ID, "in_progress", "--agent", A])

    assert result.exit_code == 0, out_of(result)
    assert status_of(world) == S.IN_PROGRESS


# --- MCP ---------------------------------------------------------------------------------------


def test_mcp_schema_has_an_optional_string_agent(world) -> None:
    tool = next(t for t in mcp(world).get_tools() if t["name"] == "kanban_move_item")
    schema = tool["inputSchema"]
    assert schema["properties"].get("agent", {}).get("type") == "string", schema
    assert "agent" not in schema.get("required", []), schema


def test_mcp_refuses_a_held_item_without_traceback(world, caplog) -> None:
    seed(world, "in_progress", B)
    before = snapshot(world.a)

    out = mcp_move(world, caplog, new_status="review", agent=A)

    assert out == {"error": held()}, out
    assert snapshot(world.a) == before


def test_mcp_refuses_a_held_item_by_the_servers_git_user(world, caplog) -> None:
    """No `agent`, no $YURTLE_AGENT: the actor is git user.name `A`, not the holder."""
    seed(world, "in_progress", B)
    before = snapshot(world.a)

    out = mcp_move(world, caplog, new_status="review")

    assert out == {"error": held()}, out
    assert snapshot(world.a) == before


@pytest.mark.parametrize("as_actor", [B, "AGENT-B"])
def test_mcp_holder_as_agent_moves_and_is_recorded(world, caplog, as_actor) -> None:
    seed(world, "in_progress", B)

    out = mcp_move(world, caplog, new_status="review", agent=as_actor)

    assert out.get("success"), out
    assert status_of(world) == S.REVIEW
    text = (world.a / ITEM).read_text()
    assert nodes_by(text, as_actor), f'the move was not recorded as kb:by "{as_actor}":\n{text}'


def test_mcp_agent_is_recorded_on_an_unguarded_move(world, caplog) -> None:
    """#580 follow-up: MCP moves record kb:by from `agent`, not the server's git user."""
    out = mcp_move(world, caplog, new_status="in_progress", agent="agent-M")

    assert out.get("success"), out
    text = (world.a / ITEM).read_text()
    assert nodes_by(text, "agent-M"), text
    assert not nodes_by(text, "A"), f"kb:by fell back to the server's git user:\n{text}"


def test_mcp_non_string_agent_is_refused(world, caplog) -> None:
    before = snapshot(world.a)

    out = mcp_move(world, caplog, new_status="in_progress", agent=5)

    assert out == {"error": "agent must be a string"}, out
    assert snapshot(world.a) == before


def test_mcp_blank_agent_is_refused(world, caplog) -> None:
    """A blank identity is refused as resolve_actor refuses it, not treated as absent."""
    before = snapshot(world.a)

    out = mcp_move(world, caplog, new_status="in_progress", agent="  ")

    assert "error" in out, out
    assert snapshot(world.a) == before


# --- MCP controls (green on the parent) ---------------------------------------------------------------


def test_mcp_holder_by_env_moves(world, caplog, monkeypatch) -> None:
    seed(world, "in_progress", B)
    monkeypatch.setenv("YURTLE_AGENT", B)

    out = mcp_move(world, caplog, new_status="review")

    assert out.get("success"), out
    assert status_of(world) == S.REVIEW


def test_mcp_unassigned_item_moves_as_before(world, caplog) -> None:
    out = mcp_move(world, caplog, new_status="in_progress")

    assert out.get("success"), out
    assert status_of(world) == S.IN_PROGRESS

