"""Issue #1290, review r3 of PR #1296: the CI regression in
`tests/issues/test_705_staged_new_parent.py::test_already_linked_parent_names_the_child`
(a user creates H130.1, deletes the file, re-creates `--id H130.1` with no $YURTLE_AGENT, and
is refused by the record their own create wrote).

Driver ruling: #1290 is about RESERVATIONS made by `next-id` (a number handed out, no file
yet). Records gain a field: `next-id` writes `"reserved": true`; `create` (auto or explicit)
writes `"reserved": false` (the number was consumed by an item). Only `reserved: true` records,
and legacy records with no `reserved` field (conservative), block an explicit id.

Reading (partner's): a `reserved: false` record blocks in neither mode, the same id or (under
global) another id space; the "explicit id whose FILE exists" refusal is untouched.
"""

from __future__ import annotations

import json

import pytest

from tests.issues.test_585_create_push_loop import git, output_of
from tests.issues.test_1288_id_numbering import (  # noqa: F401  (autouse fixture)
    CREATE,
    Repo,
    _env,
    make,
)
from tests.issues.test_1290_explicit_id_reservations import (
    ENTRY,
    MODES,
    PUSH,
    RESERVER,
    assert_created,
    assert_refused,
    record,
)
from tests.issues.test_1290_review_r1 import cli_in

HYP = ENTRY["hypothesis"][0]
ALLOC = ".kanban/_ID_ALLOCATIONS.json"


def flag(push: bool) -> list[str]:
    return ["--push"] if push else []


def local_records(repo: Repo) -> list[dict]:
    path = repo.a / ALLOC
    return json.loads(path.read_text()) if path.exists() else []


def origin_records(repo: Repo) -> list[dict]:
    return json.loads(git(repo.remote, "show", f"main:{ALLOC}"))


def record_for(records: list[dict], item_id: str) -> dict:
    found = [r for r in records if isinstance(r, dict) and r.get("id") == item_id]
    assert found, f"no allocation record for {item_id}: {records}"
    return found[-1]


@pytest.fixture(autouse=True)
def _no_agent(monkeypatch) -> None:
    monkeypatch.delenv("YURTLE_AGENT", raising=False)


# --- 1. the field: next-id writes reserved: true, create writes reserved: false ---------------


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("sync", [True, False], ids=["pushed", "no-sync"])
def test_next_id_record_is_reserved_true(tmp_path, monkeypatch, mode, sync) -> None:
    repo = make(tmp_path, mode)
    argv = ["next-id", "H", "--json", *([] if sync else ["--no-sync"])]
    result, out = cli_in(repo.a, monkeypatch, *argv)
    assert result.exit_code == 0, out
    got = json.loads(output_of(result))["id"]
    rec = record_for(origin_records(repo) if sync else local_records(repo), got)
    assert rec.get("reserved") is True, f"next-id's record lacks reserved: true: {rec}"


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("explicit", [False, True], ids=["auto", "explicit"])
@pytest.mark.parametrize("remote", [True, False], ids=["push", "no-remote"])
def test_create_record_is_reserved_false(tmp_path, monkeypatch, mode, explicit, remote) -> None:
    """Every create that writes a record (`--push`, with or without a remote) marks it
    consumed. (A plain local create writes no record at all, before and after #1290.)"""
    repo = make(tmp_path, mode)
    if not remote:
        git(repo.a, "remote", "remove", "origin")
    argv = HYP("H-005") if explicit else CREATE["hypothesis"]
    result, out = cli_in(repo.a, monkeypatch, *argv, "--push")
    assert result.exit_code == 0, out
    made = "H-005" if explicit else "H-001"
    rec = record_for(origin_records(repo) if remote else local_records(repo), made)
    assert rec.get("reserved") is False, f"create's record lacks reserved: false: {rec}"


# --- 2. the regression: a create-written record does not block a re-create -------------------


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("where", ["push", "push-then-local", "no-remote"])
def test_recreate_after_delete_is_not_blocked_by_own_create_record(
    tmp_path, monkeypatch, mode, where
) -> None:
    """test_705's shape: create H-001 (`--push`, which writes the record), delete its file,
    `--id H-001` again; no $YURTLE_AGENT. `push-then-local` re-creates without `--push`."""
    repo = make(tmp_path, mode)
    if where == "no-remote":
        git(repo.a, "remote", "remove", "origin")
    result, out = cli_in(repo.a, monkeypatch, *CREATE["hypothesis"], "--push")
    assert result.exit_code == 0, out
    if where != "no-remote":
        git(repo.a, "pull", "-q", "--no-rebase", "origin", "main")
    child = next((repo.a / "research" / "hypotheses").glob("H-001*.md"))
    git(repo.a, "rm", "-q", str(child.relative_to(repo.a)))
    git(repo.a, "commit", "-qm", "drop H-001")
    if where != "no-remote":
        git(repo.a, "push", "-q", "origin", "main")
    again = where != "push-then-local"
    result, out = cli_in(repo.a, monkeypatch, *HYP("H-001"), *flag(again))
    assert result.exit_code == 0, f"the create-written record blocked the re-create: {out}"
    ids = repo.remote_ids() if where == "push" else repo.local_ids()
    assert "H-001" in ids, out


@pytest.mark.parametrize("mode", MODES)
@PUSH
def test_seeded_consumed_record_does_not_block(tmp_path, monkeypatch, mode, push) -> None:
    """A `reserved: false` record by ANOTHER actor, no file: not a reservation, not a block."""
    repo = make(tmp_path, mode)
    reserved = "H-003" if mode == "per_type" else "CHORE-003"
    repo.seed({}, [{**record(reserved), "reserved": False}])
    result, out = cli_in(repo.a, monkeypatch, *HYP("H-003"), *flag(push))
    assert_created(repo, result, out, "H-003", push)


# --- 3. what still blocks: reserved: true, and legacy records with no field -------------------


@pytest.mark.parametrize("mode", MODES)
@PUSH
@pytest.mark.parametrize("field", ["reserved-true", "legacy"])
def test_reservation_and_legacy_records_still_block(tmp_path, monkeypatch, mode, push,
                                                    field) -> None:
    repo = make(tmp_path, mode)
    reserved = "H-003" if mode == "per_type" else "CHORE-003"
    rec = record(reserved)  # the helper writes no `reserved` field: a legacy record
    if field == "reserved-true":
        rec = {**rec, "reserved": True}
    repo.seed({}, [rec])
    tip = repo.remote_tip()
    result, out = cli_in(repo.a, monkeypatch, *HYP("H-003"), *flag(push))
    assert_refused(repo, result, out, "H-003", reserved, tip, by=RESERVER)


@pytest.mark.parametrize("mode", MODES)
@PUSH
def test_next_id_reservation_from_other_clone_still_blocks(tmp_path, monkeypatch, mode,
                                                           push) -> None:
    repo = make(tmp_path, mode)
    result, out = cli_in(repo.b, monkeypatch, "next-id", "H", "--json")
    assert result.exit_code == 0, out
    assert record_for(origin_records(repo), "H-001").get("reserved") is True
    if not push:
        git(repo.a, "pull", "-q", "--no-rebase", "origin", "main")
    tip = repo.remote_tip()
    result, out = cli_in(repo.a, monkeypatch, *HYP("H-001"), *flag(push))
    assert result.exit_code != 0, out
    assert "reserved" in out, out
    assert repo.remote_tip() == tip
