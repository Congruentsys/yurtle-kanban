"""Issue #990: follow-ups to #575 (``list --pickable``, ``next``, ``claim --next``).

The [steer] on #990 is the spec for parts 1-2; parts 3-4 are as the issue body says.

1. The take-over hint ("to take it over use claim --take-over") appears in a claim's
   refusal only when ``--take-over`` would pass: the held item is ready or in
   progress. An item held by another agent in any other status (blocked, backlog,
   review) is refused without the hint.
2. ``claim --next`` stops at the FIRST WIP-limit refusal and exits with that
   refusal's message and code (a full column refuses every candidate alike). Other
   refusals, and lost races, still fall through to the next candidate. When the list
   runs out, the ``nothing pickable ... (N tried)`` line (exit 7) also names the
   last refusal's reason.
3. README.md's command table mentions ``next --json`` (exit 7),
   ``list --pickable [--explain]`` and ``claim --next``.
4. A claim of the actor's OWN blocked item is refused (not pickable, with or without
   ``--take-over``) and fires no hooks. This replaces test_814's
   ``test_claim_blocked_item_of_self_fires_status_change_only``, which after #575
   duplicated ``test_claim_pre_assigned_to_self_fires_no_assigned[take-over]``.

Harness: the #585 ``World`` with the #574 claim helpers (tests/issues/test_574_claim.py)
and #575's ``claim --next`` seam wrapper (tests/issues/test_575_pickable_next.py).

Readings the test partner chose (the driver may challenge them):

a. Part 1's "take-over would pass" is read as the steer's parenthesis: the status
   is ready or in progress. The hint is detected as the text ``--take-over`` anywhere
   in the refusal. The refusal must still name the holder and carry pickable's
   status reason (#575's agreement test pins the latter too).
b. Part 2's "only one attempt" is counted as calls to ``KanbanService.claim_item``
   during ``claim --next``. The WIP refusal's message is matched as the text
   ``WIP limit`` plus the column; exit code 1 (a refusal's).
c. Part 2's "names the last refusal's reason": the candidates are made to refuse on
   origin for DIFFERENT reasons (held by B, then a blocked status on the last), and
   the exit-7 output must carry the last one (``EXP-003`` and ``not a ready
   status``). For the all-lost fixture of #575, the last outcome is B's hold, so
   ``held by agent-B`` must be in the output.
d. Part 3 reads the table under ``## CLI Commands``, row by first cell: the
   ``next`` row carries ``--json`` and ``7``; the ``list`` row carries
   ``--pickable`` and ``--explain``; a ``claim`` row carries ``--next``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from click.testing import Result

from tests.issues.test_574_claim import (
    ITEM_ID,
    WIP_CONFIG,
    A,
    B,
    assert_claimed_by,
    claim,
    fired,
    install_hooks,
    invoke,
    item_text,
    output_of,
    push_from_a,
    seed,
)
from tests.issues.test_574_sync_and_push import Recorder, snapshot
from tests.issues.test_575_pickable_next import (
    NEXT_ITEMS,
    _b_claims,
    _claim_next,
    _seam_on_a,
    _seed_three,
    flat,
)
from tests.issues.test_585_create_push_loop import EXP_DIR, World
from tests.issues.test_590_next_id_and_hdd_ids import b_push
from yurtle_kanban.service import KanbanService

REPO = Path(__file__).resolve().parents[2]
HINT = "--take-over"

pytestmark = pytest.mark.usefixtures("claim_env")


# --- 1. the take-over hint only when a take-over would pass -------------------------------


@pytest.mark.parametrize("status", ["blocked", "backlog", "review"])
def test_held_by_other_not_ready_has_no_take_over_hint(world, status) -> None:
    seed(world, status, B)
    base = world.remote_sha()
    before = snapshot(world.a)

    out = claim(world.a, A)

    assert out.kind == "refused", f"{out.kind}: {out.message}"
    assert out.exit_code == 1
    assert "held by agent-b" in out.message.lower(), out.message
    assert "not a ready status" in out.message, out.message
    assert HINT not in out.message, f"hint offered, but a take-over refuses: {out.message}"
    assert world.remote_sha() == base
    assert snapshot(world.a) == before


@pytest.mark.parametrize("status", ["blocked", "backlog", "review"])
def test_take_over_of_held_not_ready_is_refused(world, status) -> None:
    """Why the hint must go: the take-over it would suggest refuses."""
    seed(world, status, B)
    base = world.remote_sha()

    out = claim(world.a, A, take_over=True)

    assert out.kind == "refused", f"{out.kind}: {out.message}"
    assert world.remote_sha() == base


def test_cli_held_by_other_blocked_has_no_take_over_hint(world, monkeypatch) -> None:
    seed(world, "blocked", B)
    base = world.remote_sha()

    result = invoke(world, monkeypatch, ["claim", ITEM_ID, "--agent", A])

    out = output_of(result)
    assert result.exit_code == 1, out
    assert "held by agent-b" in out.lower(), out
    assert HINT not in out, out
    assert world.remote_sha() == base


@pytest.mark.parametrize("status", ["ready", "in_progress"])
def test_control_held_by_other_ready_or_in_progress_hints_and_take_over_wins(
    world, status
) -> None:
    seed(world, status, B)
    base = world.remote_sha()

    out = claim(world.a, A)

    assert out.kind == "refused", f"{out.kind}: {out.message}"
    assert "held by agent-b" in out.message.lower(), out.message
    assert HINT in out.message, out.message

    won = claim(world.a, A, take_over=True)

    assert won.kind == "won", f"{won.kind}: {won.message}"
    assert_claimed_by(world, A, base)


# --- 2. claim --next stops on a WIP refusal; exit 7 names the last reason --------------------


def _count_claims(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record each `claim_item` call's item ID (A's `claim --next` only calls it)."""
    real = KanbanService.claim_item
    seen: list[str] = []

    def wrapped(self: KanbanService, item_id: str, **kw: Any) -> Any:
        seen.append(item_id)
        return real(self, item_id, **kw)

    monkeypatch.setattr(KanbanService, "claim_item", wrapped)
    return seen


def _wip_full_with_three_ready(world: World) -> None:
    """WIP limit 1 on in_progress, with B's EXP-004 in progress, and three ready,
    unassigned candidates for A: EXP-001 (the fixture's), EXP-002, EXP-003. A sees
    all of it, so `pick_report` offers all three."""
    _seed_three(world)
    push_from_a(world, {
        ".kanban/config.yaml": WIP_CONFIG,
        f"{EXP_DIR}/EXP-004-w.md": item_text("in_progress", B, "EXP-004", "W"),
    }, "board: wip 1, full")


def test_claim_next_stops_at_the_first_wip_refusal(world, monkeypatch) -> None:
    _wip_full_with_three_ready(world)
    base = world.remote_sha()
    seen = _count_claims(monkeypatch)
    calls = _seam_on_a(monkeypatch, world, lambda call, attempt: None)

    result = _claim_next(world, monkeypatch, "--agent", A)

    out = flat(result.output)
    assert result.exit_code == 1, f"exit {result.exit_code} (not the WIP refusal's 1): {out}"
    assert "wip limit" in out.lower(), f"the WIP refusal's reason is lost: {out}"
    assert "nothing pickable" not in out.lower(), out
    assert seen == ["EXP-001"], f"claim --next tried {seen} against a full column"
    assert len(calls) == 1, f"A fetched {len(calls)} times against a full column"
    assert world.remote_sha() == base


def test_control_claim_next_wip_not_full_wins_the_first(world, monkeypatch) -> None:
    _seed_three(world)
    push_from_a(world, {".kanban/config.yaml": WIP_CONFIG}, "board: wip 1")
    seen = _count_claims(monkeypatch)

    result = _claim_next(world, monkeypatch, "--agent", A)

    assert result.exit_code == 0, flat(result.output)
    assert seen == ["EXP-001"], seen


def test_claim_next_non_wip_refusals_fall_through_and_exit_7_names_the_last(
    world, monkeypatch
) -> None:
    """Origin (unseen by A) has EXP-001 and EXP-002 held by B and EXP-003 blocked:
    each candidate is refused for a non-WIP reason, all three are tried, and the
    exit-7 line names EXP-003's refusal."""
    _seed_three(world)
    b_push(world, {
        NEXT_ITEMS["EXP-001"]: item_text("ready", B, "EXP-001", "X"),
        NEXT_ITEMS["EXP-002"]: item_text("ready", B, "EXP-002", "Y"),
        NEXT_ITEMS["EXP-003"]: item_text("blocked", None, "EXP-003", "Z"),
    })
    base = world.remote_sha()
    seen = _count_claims(monkeypatch)

    result = _claim_next(world, monkeypatch, "--agent", A)

    out = flat(result.output)
    assert result.exit_code == 7, f"exit {result.exit_code}: {out}"
    assert "nothing pickable" in out.lower(), out
    assert seen == ["EXP-001", "EXP-002", "EXP-003"], f"non-WIP refusals must fall through: {seen}"
    assert "EXP-003" in out and "not a ready status" in out, (
        f"the exit-7 line does not name the last refusal's reason: {out}"
    )
    assert world.remote_sha() == base


def test_claim_next_all_lost_exit_7_names_the_last_reason(world, monkeypatch) -> None:
    """#575's all-lost fixture: B claims all three in A's first seam. The first is
    lost, the next two are refused as held by B; the exit-7 line names that."""
    _seed_three(world)

    def action(call: int, attempt: int) -> None:
        if call == 0 and attempt == 0:
            for item_id in NEXT_ITEMS:
                _b_claims(world, item_id)

    _seam_on_a(monkeypatch, world, action)

    result: Result = _claim_next(world, monkeypatch, "--agent", A)

    out = flat(result.output)
    assert result.exit_code == 7, f"exit {result.exit_code}: {out}"
    assert "nothing pickable" in out.lower(), out
    assert "held by agent-b" in out.lower(), (
        f"the exit-7 line does not name the last refusal's reason: {out}"
    )


# --- 3. README's command table ---------------------------------------------------------------


def _command_rows() -> dict[str, list[str]]:
    text = (REPO / "README.md").read_text(encoding="utf-8")
    m = re.search(r"^## CLI Commands\n(.*?)(?=^#)", text, re.S | re.M)
    assert m, "README has no `## CLI Commands` section"
    rows: dict[str, list[str]] = {}
    for line in m.group(1).splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2 or not cells[0].startswith("`"):
            continue
        rows.setdefault(cells[0].strip("`").split()[0], []).append(line)
    return rows


def test_readme_table_next_json_exit_7() -> None:
    row = " ".join(_command_rows().get("next", []))
    assert "--json" in row and "7" in row, f"`next` row: {row!r}"


def test_readme_table_list_pickable_explain() -> None:
    row = " ".join(_command_rows().get("list", []))
    assert "--pickable" in row and "--explain" in row, f"`list` row: {row!r}"


def test_readme_table_claim_next() -> None:
    row = " ".join(_command_rows().get("claim", []))
    assert "--next" in row, f"`claim` row: {row!r}"


# --- 4. a claim of one's own blocked item is refused and fires no hooks ------------------------


@pytest.mark.parametrize("take_over", [False, True], ids=["plain", "take-over"])
def test_claim_blocked_item_of_self_is_refused_and_fires_no_hooks(
    world, tmp_path, take_over
) -> None:
    seed(world, "blocked", A)
    marker = install_hooks(world, tmp_path)
    base = world.remote_sha()
    before = snapshot(world.a)
    rec = Recorder()

    out = claim(world.a, A, rec, take_over=take_over)

    assert out.kind == "refused", f"{out.kind}: {out.message}"
    assert out.exit_code == 1
    assert "not a ready status" in out.message, out.message
    assert fired(marker) == [], f"a refused claim fired hooks: {fired(marker)}"
    assert rec.seams == [], "a push was attempted"
    assert world.remote_sha() == base
    assert snapshot(world.a) == before
