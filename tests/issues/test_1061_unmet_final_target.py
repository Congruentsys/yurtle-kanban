"""Issue #1061: follow-ups to #1053 (PR #1058).

1. A finished → finished move that drops ``completed`` (hdd ``complete`` →
   ``abandoned``) records ``kb:clearedResolution "completed"`` on its history node
   (a pin: #1053 wrote it, nothing asserted it).
2. ``unmet_dependencies`` names a redirect's missing FINAL target (state
   ``unknown``), as ``pickable`` does since #1053 — not the existing item that
   redirects. (#577's ``blocked`` tree is not on main yet: no ``blocked --json``
   assertion here.)

Fixtures are #581's (tests/issues/test_581_resolution.py) and #1053's.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.issues.test_581_resolution import invoke, make_boards, moved
from tests.issues.test_1053_resolution_followups import (
    Boards,
    unknown,  # noqa: F401 (fixture)
)

pytestmark = pytest.mark.usefixtures("claim_env")


# --- 1: the dropped `completed` is recorded -------------------------------------------


def test_1_dropped_completed_is_recorded_in_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_boards(tmp_path, monkeypatch, {
        "H1": {"status": "complete", "resolution": "completed"},
    })
    moved(invoke(["move", "H1", "abandoned", "--force"]))  # hdd: complete has no legal move
    node = repo.last_node("H1")
    assert 'kb:clearedResolution "completed"' in node, f"history node:\n{node}"


# --- 2: unmet_dependencies names the missing final target -----------------------------


@pytest.mark.parametrize("item_id,missing,present", [
    ("EXP-102", "EXP-404", ("EXP-2",)),
    ("EXP-103", "EXP-405", ("EXP-3", "EXP-4")),
])
def test_2_unmet_names_the_missing_final_target(
    unknown: Boards, item_id: str, missing: str, present: tuple[str, ...],  # noqa: F811
) -> None:
    svc = unknown.service()
    nodes = list(svc.unmet_dependencies(unknown.item(item_id)))
    assert len(nodes) == 1, nodes
    node = nodes[0]
    assert node.id == missing, node
    assert node.state == "unknown", node
    assert node.status is None and node.assignee is None, node
    assert node.id not in present, node
    # agrees with pickable's reason
    ok, reason = svc.pickable(unknown.item(item_id), "agent-A")
    assert not ok and reason.startswith(f"waiting on {node.id} "), reason
