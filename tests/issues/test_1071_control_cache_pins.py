"""Issue #1071: pin the ``control_state`` cache invalidations (#1067, PR #1070).

#1067 memoised ``control_state()`` per scan. Besides a scan, three paths drop or
bypass the memo, and removing any one of them kept every related test green:

a. ``set_control`` (``control halt|resume``) clears it in its ``finally``: a
   long-lived service that halts the board and then asks ``pickable()`` in the
   SAME scan (no rescan) must see the halt, not the memoised ``running``. Pinned
   with NO remote (``_sync_locally``): with one, ``sync_and_push``'s own
   ``_fetch_default`` already clears the memo, so only the no-remote path depends
   on the ``finally`` (a mutation that drops it stays green on a remote).
b. ``_fetch_default`` clears it: when the service's own fetch (here the public
   ``sync_and_push`` every kanban push goes through, with a no-op mutation, so
   nothing else is written or rescanned) brings a halt landed out of band on
   origin, ``pickable()`` must see it.
c. ``move_item(..., in_progress)`` reads ``control_state(fresh=True)``: after an
   out-of-band ``git fetch`` of a halt (no scan, no service fetch), a service that
   memoised ``running`` must still refuse the move.

Each test first memoises ``running`` with a ``pickable()`` call, so it only
passes through the invalidation it pins.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.issues.test_574_claim import ITEM_ID, A, B
from tests.issues.test_582_control_halt import (
    CONTROL,
    REASON,
    control_yaml,
    hand_halt,
    local_halted_repo,
    publish_from_a,
)
from tests.issues.test_585_create_push_loop import World
from yurtle_kanban.cli import get_service
from yurtle_kanban.models import WorkItem, WorkItemStatus
from yurtle_kanban.service import BoardHalted, KanbanService
from yurtle_kanban.sync import NoOp

pytestmark = pytest.mark.usefixtures("claim_env")


def memoised(clone: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[KanbanService, WorkItem]:
    """A service on `clone` whose memo holds `running`, and EXP-001 from its scan."""
    monkeypatch.chdir(clone)
    svc = get_service()
    item = svc.get_item(ITEM_ID)
    assert item is not None, "fixture: EXP-001 did not parse"
    assert svc.pickable(item, B) == (True, "pickable"), "fixture: the board is not running"
    return svc, item


def test_a_set_control_halt_invalidates_the_memo(tmp_path: Path, monkeypatch) -> None:
    repo = local_halted_repo(tmp_path, monkeypatch, None)  # no remote, no control file
    svc = repo.service()
    item = svc.get_item("EXP-1")
    assert item is not None, "fixture: EXP-1 did not parse"
    assert svc.pickable(item, B) == (True, "pickable"), "fixture: the board is not running"

    outcome = svc.set_control("halt", REASON, B)
    assert outcome.kind == "local", outcome

    okay, reason = svc.pickable(item, B)  # the same item, the same scan
    assert okay is False, "pickable() after `control halt` served the memoised running"
    assert reason.startswith(f"board halted by {B}"), reason


def test_b_the_services_own_fetch_invalidates_the_memo(world: World, monkeypatch) -> None:
    svc, item = memoised(world.b, monkeypatch)
    publish_from_a(world, CONTROL, control_yaml(), "halt (by hand, not fetched by B)")
    assert svc.pickable(item, B) == (True, "pickable"), "fixture: B has not fetched yet"

    outcome = svc.sync_and_push(lambda read, attempt: NoOp("nothing to write"))
    assert outcome.kind == "noop", outcome

    okay, reason = svc.pickable(item, B)
    assert okay is False, "pickable() after the service's fetch served the memoised running"
    assert reason.startswith(f"board halted by {A}"), reason


def test_c_move_to_in_progress_reads_the_state_fresh(world: World, monkeypatch) -> None:
    svc, _ = memoised(world.b, monkeypatch)
    hand_halt(world)  # on origin, fetched by B out of band: no scan, no service fetch

    with pytest.raises(BoardHalted):
        svc.move_item(ITEM_ID, WorkItemStatus.IN_PROGRESS, actor=B, assignee=B)
