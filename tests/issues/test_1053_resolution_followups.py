"""Issue #1053: follow-ups to #581 (``move --resolution``, PR #1044).

1. A finished → finished move without ``--resolution`` must not keep a
   ``completed`` resolution on a target that is not canonical done (hdd ``complete``
   ``completed`` → ``abandoned`` left the forbidden pair ``abandoned`` + ``completed``,
   and ``list --resolution completed`` then listed an abandoned item).
2. Refusing a ``--superseded-by`` target that already sits on a hand-made cycle
   says it "leads into a supersession cycle", not "would make a cycle" naming the
   item being moved (which is not on it).
3. When a redirect's FINAL target is on no board, ``pickable`` (and ``claim``'s
   refusal) name that missing final target, not the existing item that redirects.
4. ``validate``'s ``bad_supersession`` for "superseded but no superseded_by" and
   "superseded_by on no board" (untested branches; may already be green — a pin).

Fixtures are #581's (tests/issues/test_581_resolution.py).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.issues.test_581_resolution import (
    Boards,
    assert_refused,
    invoke,
    list_ids,
    make_boards,
    moved,
    out,
    val,
)

pytestmark = pytest.mark.usefixtures("claim_env")


# --- 1: a finished -> finished move drops a `completed` it can't keep -----------------


def test_1_complete_to_abandoned_drops_completed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_boards(tmp_path, monkeypatch, {
        "H1": {"status": "complete", "resolution": "completed"},
    })
    moved(invoke(["move", "H1", "abandoned", "--force"]))  # hdd: complete has no legal move
    assert repo.service().status_label(repo.item("H1")) == "abandoned"
    fm = repo.fm("H1")
    assert fm.get("resolution") != "completed", f"abandoned kept completed: {fm}"
    assert repo.item("H1").resolution != "completed"
    assert "H1" not in list_ids(["--resolution", "completed"])


def test_1_other_resolutions_survive_a_finished_move(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Control: only `completed` is tied to canonical done; `wont_do` on
    `complete` → `abandoned` stays."""
    repo = make_boards(tmp_path, monkeypatch, {
        "H1": {"status": "complete", "resolution": "wont_do"},
    })
    moved(invoke(["move", "H1", "abandoned", "--force"]))  # hdd: complete has no legal move
    assert repo.fm("H1").get("resolution") == "wont_do"


# --- 2: the cycle refusal's wording ---------------------------------------------------

CYCLE_ITEMS: dict[str, dict[str, Any]] = {
    "EXP-1": {"status": "review"},
    # a supersession cycle entered by hand; EXP-1 is not on it
    "EXP-12": {"status": "done", "resolution": "duplicate", "superseded_by": ("EXP-13",)},
    "EXP-13": {"status": "done", "resolution": "superseded", "superseded_by": ("EXP-12",)},
}


def test_2_target_on_a_hand_made_cycle_leads_into_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_boards(tmp_path, monkeypatch, CYCLE_ITEMS)
    result = assert_refused(repo, "EXP-1", ["move", "EXP-1", "done", "--resolution",
                                            "duplicate", "--superseded-by", "EXP-12"])
    text = out(result)
    assert "leads into a supersession cycle" in text, text
    assert "would make a cycle" not in text, text


# --- 3: an unknown final target is the one named --------------------------------------

UNKNOWN_ITEMS: dict[str, dict[str, Any]] = {
    # EXP-2 is a duplicate of EXP-404, which is on no board
    "EXP-2": {"status": "done", "resolution": "duplicate", "superseded_by": ("EXP-404",)},
    "EXP-102": {"status": "ready", "deps": ("EXP-2",)},
    # a chain: EXP-3 superseded by EXP-4, a duplicate of EXP-405 (on no board)
    "EXP-3": {"status": "done", "resolution": "superseded", "superseded_by": ("EXP-4",)},
    "EXP-4": {"status": "done", "resolution": "duplicate", "superseded_by": ("EXP-405",)},
    "EXP-103": {"status": "ready", "deps": ("EXP-3",)},
}


@pytest.fixture
def unknown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Boards:
    return make_boards(tmp_path, monkeypatch, UNKNOWN_ITEMS)


@pytest.mark.parametrize("item_id,missing,present", [
    ("EXP-102", "EXP-404", ("EXP-2",)),
    ("EXP-103", "EXP-405", ("EXP-3", "EXP-4")),
])
def test_3_pickable_names_the_missing_final_target(
    unknown: Boards, item_id: str, missing: str, present: tuple[str, ...],
) -> None:
    ok, reason = unknown.service().pickable(unknown.item(item_id), "agent-A")
    assert not ok
    assert reason.startswith(f"waiting on {missing}"), reason
    assert "unknown" in reason, reason
    for existing in present:
        assert f"waiting on {existing} " not in reason, reason


def test_3_dependency_state_is_unknown(unknown: Boards) -> None:
    assert val(unknown.service().dependency_state("EXP-2")) == "unknown"
    assert val(unknown.service().dependency_state("EXP-3")) == "unknown"


def test_3_claim_names_the_missing_final_target(unknown: Boards) -> None:
    before = unknown.text("EXP-102")
    result = invoke(["claim", "EXP-102", "--agent", "agent-A"])
    assert result.exit_code != 0, result.output
    assert "EXP-404" in out(result), result.output
    assert unknown.text("EXP-102") == before


# --- 4: validate's bad_supersession ---------------------------------------------------


def bad_supersessions() -> dict[str, str]:
    result = invoke(["validate", "--json"])
    payload = json.loads(result.stdout)
    return {
        i["id"]: i["message"] for i in payload["issues"] if i["type"] == "bad_supersession"
    }


def test_4_validate_bad_supersession(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make_boards(tmp_path, monkeypatch, {
        "EXP-1": {"status": "done", "resolution": "superseded"},
        "EXP-2": {"status": "done", "resolution": "duplicate", "superseded_by": ("EXP-404",)},
        "EXP-3": {"status": "done", "resolution": "duplicate", "superseded_by": ("EXP-4",)},
        "EXP-4": {"status": "done"},
    })
    got = bad_supersessions()
    assert set(got) == {"EXP-1", "EXP-2"}, got
    assert "no superseded_by" in got["EXP-1"], got
    assert "EXP-404" in got["EXP-2"] and "on no board" in got["EXP-2"], got
