"""Issue #990, review round 1 of PR #1015: the reviewer's findings F1-F4.

F1. ``claim --next`` after a WIP refusal skips only the remaining candidates of the
    SAME item type (they meet the same limit) and keeps trying other types: a type
    exempt from the limit (``wip_exempt_types``) or under its own per-type limit can
    still be won. Nothing won: the WIP refusal, exit 1 (not exit 7).
F2. The WIP stop reads a structured marker (``Refuse.wip`` / ``Outcome.wip``), not
    the message's wording.
F3. The take-over hint is offered only where ``--take-over`` would pass the pickable
    clauses: a ready item held by another agent but waiting on an unfinished
    dependency gets no hint.
F4. The exit-7 line's ``last:`` is kept short: a long refusal is truncated.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.issues.test_574_claim import WIP_CONFIG, A, B, claim, item_text, push_from_a, seed
from tests.issues.test_575_pickable_next import NEXT_ITEMS, _claim_next, _seed_three, flat
from tests.issues.test_585_create_push_loop import EXP_DIR, World
from yurtle_kanban import cli
from yurtle_kanban.service import KanbanService
from yurtle_kanban.sync import Outcome

pytestmark = pytest.mark.usefixtures("claim_env")

EXEMPT_CONFIG = """\
version: "2.0"
boards:
  - name: development
    preset: nautical
    path: kanban-work/
    wip_limits:
      in_progress: 1
    wip_exempt_types: [chore]
default_board: development
"""

PER_TYPE_CONFIG = """\
version: "2.0"
boards:
  - name: development
    preset: nautical
    path: kanban-work/
    wip_limits:
      in_progress:
        expedition: 1
default_board: development
"""


def typed(item_type: str, status: str, assignee: str | None, item_id: str, title: str) -> str:
    return item_text(status, assignee, item_id, title).replace(
        "type: expedition", f"type: {item_type}"
    )


def _count_claims(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    real = KanbanService.claim_item
    seen: list[str] = []

    def wrapped(self: KanbanService, item_id: str, **kw: Any) -> Any:
        seen.append(item_id)
        return real(self, item_id, **kw)

    monkeypatch.setattr(KanbanService, "claim_item", wrapped)
    return seen


def _full_column_with_a_chore(world: World, config: str) -> None:
    """B's EXP-004 (an expedition) holds the one in-progress slot; EXP-001 is a
    ready expedition and EXP-002 a ready chore, both unassigned."""
    # the world's EXP-001: a ready, unassigned expedition
    push_from_a(world, {
        ".kanban/config.yaml": config,
        NEXT_ITEMS["EXP-002"]: typed("chore", "ready", None, "EXP-002", "Y"),
        f"{EXP_DIR}/EXP-004-w.md": item_text("in_progress", B, "EXP-004", "W"),
    }, "board: full column, a ready chore")


# --- F1 ---------------------------------------------------------------------------------


@pytest.mark.parametrize("config", [EXEMPT_CONFIG, PER_TYPE_CONFIG], ids=["exempt", "per-type"])
def test_f1_claim_next_passes_a_wip_refusal_to_another_type(
    world, monkeypatch, config
) -> None:
    _full_column_with_a_chore(world, config)
    seen = _count_claims(monkeypatch)

    result = _claim_next(world, monkeypatch, "--agent", A)

    out = flat(result.output)
    assert result.exit_code == 0, f"exit {result.exit_code}: {out}"
    assert "EXP-002" in out, out
    assert seen == ["EXP-001", "EXP-002"], seen


def test_f1_same_type_skipped_after_a_wip_refusal(world, monkeypatch) -> None:
    """Two ready expeditions and a ready chore behind a per-type limit on
    expeditions: the second expedition is not fetched; the chore is won."""
    _full_column_with_a_chore(world, PER_TYPE_CONFIG)
    push_from_a(world, {
        NEXT_ITEMS["EXP-003"]: item_text("ready", None, "EXP-003", "Z"),
    }, "seed EXP-003")
    seen = _count_claims(monkeypatch)

    result = _claim_next(world, monkeypatch, "--agent", A)

    assert result.exit_code == 0, flat(result.output)
    assert "EXP-003" not in seen, f"a second expedition was fetched: {seen}"
    assert seen[-1] == "EXP-002", seen


def test_f1_nothing_won_after_a_wip_refusal_exits_with_it(world, monkeypatch) -> None:
    """The chore, the other type, is refused for another reason (held by B on
    origin): nothing is won, so the WIP refusal is the answer (exit 1, not 7)."""
    _full_column_with_a_chore(world, EXEMPT_CONFIG)
    push_from_a(world, {
        NEXT_ITEMS["EXP-002"]: typed("chore", "blocked", None, "EXP-002", "Y"),
    }, "EXP-002 blocked")
    base = world.remote_sha()
    # A's own view still offers EXP-002: only origin's tree has it blocked
    seen = _count_claims(monkeypatch)
    real_pick = KanbanService.pick_report

    def pick_report(self: KanbanService, actor: str, *a: Any, **kw: Any) -> Any:
        picks, rest = real_pick(self, actor, *a, **kw)
        chore = self.get_item("EXP-002")
        assert chore is not None
        return [*picks, chore], rest

    monkeypatch.setattr(KanbanService, "pick_report", pick_report)

    result = _claim_next(world, monkeypatch, "--agent", A)

    out = flat(result.output)
    assert result.exit_code == 1, f"exit {result.exit_code}: {out}"
    assert "wip limit" in out.lower(), out
    assert "nothing pickable" not in out.lower(), out
    assert seen == ["EXP-001", "EXP-002"], seen
    assert world.remote_sha() == base


# --- F2 ---------------------------------------------------------------------------------


def test_f2_wip_refusal_carries_the_marker(world) -> None:
    _seed_three(world)
    push_from_a(world, {
        ".kanban/config.yaml": WIP_CONFIG,
        f"{EXP_DIR}/EXP-004-w.md": item_text("in_progress", B, "EXP-004", "W"),
    }, "board: wip 1, full")

    out = claim(world.a, A)

    assert out.kind == "refused", out.message
    assert out.wip is True, out


def test_f2_per_type_wip_refusal_carries_the_marker(world) -> None:
    push_from_a(world, {
        ".kanban/config.yaml": PER_TYPE_CONFIG,
        f"{EXP_DIR}/EXP-004-w.md": item_text("in_progress", B, "EXP-004", "W"),
    }, "board: per-type wip 1, full")

    out = claim(world.a, A)

    assert out.kind == "refused", out.message
    assert out.wip is True, out


def test_f2_non_wip_refusal_has_no_marker(world) -> None:
    seed(world, "blocked", B)

    out = claim(world.a, A)

    assert out.kind == "refused", out.message
    assert out.wip is False, out


def test_f2_reworded_wip_message_still_stops(world, monkeypatch) -> None:
    _seed_three(world)
    push_from_a(world, {
        ".kanban/config.yaml": WIP_CONFIG,
        f"{EXP_DIR}/EXP-004-w.md": item_text("in_progress", B, "EXP-004", "W"),
    }, "board: wip 1, full")
    real = KanbanService._wip_refusal

    def reworded(self: KanbanService, *a: Any, **kw: Any) -> str | None:
        refusal = real(self, *a, **kw)
        return None if refusal is None else "Column full: " + refusal.split("(")[-1]

    monkeypatch.setattr(KanbanService, "_wip_refusal", reworded)
    seen = _count_claims(monkeypatch)

    result = _claim_next(world, monkeypatch, "--agent", A)

    out = flat(result.output)
    assert result.exit_code == 1, f"exit {result.exit_code}: {out}"
    assert "Column full" in out, out
    assert seen == ["EXP-001"], seen


# --- F3 ---------------------------------------------------------------------------------


def test_f3_held_ready_item_with_unfinished_dependency_has_no_hint(world) -> None:
    push_from_a(world, {
        NEXT_ITEMS["EXP-002"]: item_text("backlog", None, "EXP-002", "Y"),
    }, "seed EXP-002 unfinished")
    seed(world, "ready", B)
    path = world.a / NEXT_ITEMS["EXP-001"]
    text = path.read_text().replace("status: ready\n", "status: ready\ndepends_on: [EXP-002]\n")
    push_from_a(world, {NEXT_ITEMS["EXP-001"]: text}, "EXP-001 depends on EXP-002")

    out = claim(world.a, A)

    assert out.kind == "refused", out.message
    assert "held by agent-b" in out.message.lower(), out.message
    assert "--take-over" not in out.message, out.message
    # and the take-over the hint would have suggested does refuse
    assert claim(world.a, A, take_over=True).kind == "refused"


# --- F4 ---------------------------------------------------------------------------------


def test_f4_exit_7_last_is_truncated(world, monkeypatch) -> None:
    _seed_three(world)
    long = "Can't claim EXP-003: " + "x" * 400

    def claim_item(self: KanbanService, item_id: str, **kw: Any) -> Outcome:
        return Outcome("refused", long)  # every candidate refuses, at length

    monkeypatch.setattr(KanbanService, "claim_item", claim_item)
    monkeypatch.setattr(cli.console, "width", 1000)

    result = _claim_next(world, monkeypatch, "--agent", A)

    out = flat(result.output)
    assert result.exit_code == 7, out
    assert "last: Can't claim EXP-003" in out, out
    assert "x" * 200 not in out, f"last: repeats the whole message: {out}"
    assert "…" in out, out
