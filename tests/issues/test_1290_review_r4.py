"""Issue #1290, review r3 of PR #1296, MINOR 1. Driver ruling: a reservation is CONSUMED when
a LATER `reserved: false` record for the same id key is in the list; a consumed reservation
no longer blocks.

Scenario: A (`$YURTLE_AGENT=A`) runs `next-id H` (H-001 reserved), creates `--id H-001 --push`
(a later `reserved: false` record), deletes the item and pushes; B then creates `--id H-001`,
locally and with `--push`, in both modes: accepted. Controls: a reservation with no later
consuming record still blocks B, and a consuming record EARLIER than the reservation does not
consume it (partner's reading of "later").
"""

from __future__ import annotations

import json

import pytest

from tests.issues.test_585_create_push_loop import git, output_of
from tests.issues.test_1288_id_numbering import Repo, _env, make  # noqa: F401  (autouse fixture)
from tests.issues.test_1290_explicit_id_reservations import (
    ENTRY,
    MODES,
    PUSH,
    record,
)
from tests.issues.test_1290_review_r1 import cli_in

HYP = ENTRY["hypothesis"][0]


def flag(push: bool) -> list[str]:
    return ["--push"] if push else []


def as_agent(monkeypatch, agent: str) -> None:
    monkeypatch.setenv("YURTLE_AGENT", agent)


def a_reserves(repo: Repo, monkeypatch) -> None:
    as_agent(monkeypatch, "A")
    result, out = cli_in(repo.a, monkeypatch, "next-id", "H", "--json")
    assert result.exit_code == 0, out
    assert json.loads(output_of(result))["id"] == "H-001", out


def b_creates(repo: Repo, monkeypatch, push: bool):
    as_agent(monkeypatch, "B")
    git(repo.b, "pull", "-q", "--no-rebase", "origin", "main")
    result, out = cli_in(repo.b, monkeypatch, *HYP("H-001"), *flag(push))
    return result, out


def b_ids(repo: Repo, push: bool) -> set[str]:
    if push:
        return repo.remote_ids()
    return {p.name.split("-", 2)[0] + "-" + p.name.split("-", 2)[1]
            for p in (repo.b / "research" / "hypotheses").glob("H-*.md")}


@pytest.mark.parametrize("mode", MODES)
@PUSH
def test_consumed_reservation_no_longer_blocks(tmp_path, monkeypatch, mode, push) -> None:
    repo = make(tmp_path, mode)
    a_reserves(repo, monkeypatch)
    result, out = cli_in(repo.a, monkeypatch, *HYP("H-001"), "--push")  # consumes it
    assert result.exit_code == 0, out
    git(repo.a, "pull", "-q", "--no-rebase", "origin", "main")
    child = next((repo.a / "research" / "hypotheses").glob("H-001*.md"))
    git(repo.a, "rm", "-q", str(child.relative_to(repo.a)))
    git(repo.a, "commit", "-qm", "drop H-001")
    git(repo.a, "push", "-q", "origin", "main")
    result, out = b_creates(repo, monkeypatch, push)
    assert result.exit_code == 0, f"A's consumed reservation still blocked B: {out}"
    assert "H-001" in b_ids(repo, push), out


@pytest.mark.parametrize("mode", MODES)
@PUSH
def test_control_unconsumed_reservation_still_blocks(tmp_path, monkeypatch, mode, push) -> None:
    repo = make(tmp_path, mode)
    a_reserves(repo, monkeypatch)
    tip = repo.remote_tip()
    result, out = b_creates(repo, monkeypatch, push)
    assert result.exit_code != 0, f"B took A's live reservation: {out}"
    assert "reserved" in out, out
    assert "H-001" not in b_ids(repo, push)
    assert repo.remote_tip() == tip


@pytest.mark.parametrize("mode", MODES)
@PUSH
def test_control_earlier_consuming_record_does_not_consume(tmp_path, monkeypatch, mode,
                                                           push) -> None:
    """[consumed H-001, then reserved H-001]: the reservation is the later one, so it blocks."""
    repo = make(tmp_path, mode)
    repo.seed({}, [{**record("H-001", by="A"), "reserved": False},
                   {**record("H-001", by="A"), "reserved": True}])
    tip = repo.remote_tip()
    result, out = b_creates(repo, monkeypatch, push)
    assert result.exit_code != 0, f"an earlier consumed record cancelled a later reservation: {out}"
    assert "reserved" in out, out
    assert repo.remote_tip() == tip


@pytest.mark.parametrize("mode", MODES)
@PUSH
def test_seeded_later_consuming_record_consumes(tmp_path, monkeypatch, mode, push) -> None:
    """[reserved H-1, then consumed H-001]: same id key, later, so the reservation is spent."""
    repo = make(tmp_path, mode)
    repo.seed({}, [{**record("H-1", by="A"), "reserved": True},
                   {**record("H-001", by="A"), "reserved": False}])
    result, out = b_creates(repo, monkeypatch, push)
    assert result.exit_code == 0, f"a consumed reservation still blocked B: {out}"
    assert "H-001" in b_ids(repo, push), out
