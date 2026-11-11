"""Issue #1290 — an explicit id ignores allocation records: a number reserved in
`_ID_ALLOCATIONS.json` (by `next-id`, say) with no item file yet can be taken by `--id`.

The issue's Proposal and "Done when" are the spec: count allocation records as holders in the
explicit-id checks, the same-prefix one (#634) and the cross-prefix one (#1288's
`_refuse_global_holder`), on the local and the origin/CAS paths, and name the record in the
refusal (`reserved by <allocated_by> at <time>`).

Reuses the #1288 harness (bare origin, clone A under test, rival clone B; a two-board config).

Pinned readings of the issue (the partner's; see the report):

- "reserved" is judged as the existing checks judge ids: per_type, the same number in the same
  id space (`H-3` reserves `H-003`; a paper-scoped `H130.3` record reserves `H130.3`);
  global, the number in ANY dashed id space (a paper-scoped record never holds a global number);
- "locally" means the record is in the checkout's own `_ID_ALLOCATIONS.json` (the CLI without
  `--push`, `create_item(item_id=...)`, and `create_item_and_push` with no remote); "with
  --push" means the record is on origin's default branch, including one a rival pushed that
  A has not fetched ("stale") or that lands just before A's first push ("race");
- the explicit-id entry points are `hypothesis create --id`, `measure create --id`,
  `experiment create <EXPR-id>`, `paper create <number>`, `create_item(item_id=...)` and
  `create_item_and_push(item_id=...)` (the core `create` and MCP `create_item` take no id;
  `epic create` passes an id it auto-allocated past every record);
- "names the reservation": the refusal says `reserved` and carries the record's `allocated_by`
  (and, under global, the reserved id, as #1288's refusals name the holder);
- records never expire: the code has no TTL (`allocated_at` is written, never read), so an
  OLD record still reserves its number; a record PRUNED from the file (writers keep only the
  last 100) is gone, so its number is free like any other free number;
- the issue makes no exception for the actor that made the reservation, so these tests use a
  different actor for the reserver and do not pin what happens when the reserver itself
  passes `--id` for its own reserved number.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import pytest
from click.testing import CliRunner

from tests.issues.test_585_create_push_loop import git, output_of
from tests.issues.test_590_next_id_and_hdd_ids import b_push
from tests.issues.test_1288_id_numbering import (  # noqa: F401  (autouse fixture)
    Repo,
    _env,
    item_text,
    make,
    seed_path,
)
from tests.test_634_explicit_ids_on_base import TIMINGS, on_timing
from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import main
from yurtle_kanban.models import InputRefused, WorkItemType

RESERVER = "rival-agent/s-reserve1"
WHEN = "2026-11-01T09:30:00"
MODES = ["per_type", "global"]
PUSH = pytest.mark.parametrize("push", [False, True], ids=["local", "push"])


def record(item_id: str, *, by: str = RESERVER, at: str = WHEN) -> dict:
    """An allocation record as `_with_allocation` writes it."""
    head, _, num = item_id.rpartition("-") if "-" in item_id else item_id.rpartition(".")
    prefix = head if "-" in item_id else head + "."
    return {"id": item_id, "prefix": prefix, "number": int(num),
            "allocated_at": at, "allocated_by": by}


# Each CLI explicit-id entry point: argv builder, and the explicit id (number 3) it creates.
ENTRY: dict[str, tuple[Callable[[str], list[str]], str]] = {
    "hypothesis": (lambda i: ["hypothesis", "create", "Explicit hyp", "--id", i], "H-003"),
    "measure": (lambda i: ["measure", "create", "Explicit m", "--unit", "count",
                           "--category", "coverage", "--id", i], "M-003"),
    "experiment": (lambda i: ["experiment", "create", i, "--title", "Explicit expr"],
                   "EXPR-003"),
    "paper": (lambda i: ["paper", "create", i.rpartition("-")[2], "Explicit paper"], "PAPER-3"),
}


def reserved_for(mode: str, item_id: str) -> str:
    """The record-only reservation that should block `item_id` under `mode`: the same id
    (per_type), or another prefix's id with the same number (global)."""
    return item_id if mode == "per_type" else "CHORE-003"


def run(repo: Repo, monkeypatch, argv: list[str], push: bool):
    monkeypatch.setenv("YURTLE_AGENT", "creator-agent/s-create01")
    result = repo.cli(monkeypatch, *argv, *(["--push"] if push else []))
    return result, " ".join(output_of(result).split())


def assert_refused(repo: Repo, result, out: str, item_id: str, reserved: str, tip: str,
                   by: str = RESERVER) -> None:
    assert result.exit_code != 0, f"{item_id} was created over {reserved}'s reservation: {out}"
    assert "reserved" in out, f"the refusal does not say the number is reserved: {out}"
    assert by in out, f"the refusal does not name the reservation's allocated_by {by}: {out}"
    if reserved != item_id:
        assert reserved in out, f"the refusal does not name the reserved id {reserved}: {out}"
    assert item_id not in repo.local_ids(), f"{item_id} was written locally"
    assert item_id not in repo.remote_ids(), f"{item_id} reached origin"
    assert repo.remote_tip() == tip, "something was pushed"


def assert_created(repo: Repo, result, out: str, item_id: str, push: bool) -> None:
    assert result.exit_code == 0, out
    assert item_id in (repo.remote_ids() if push else repo.local_ids()), out


# --- 1. the issue's own reproduction ---------------------------------------------------------


def test_issue_reproduction_global(tmp_path, monkeypatch) -> None:
    """global: CHORE-003 = allocation record only; `hypothesis create --id H-003 --push`
    printed `EXIT 0 Created and pushed H-003`."""
    repo = make(tmp_path, "global")
    repo.seed({}, [record("CHORE-003")])
    tip = repo.remote_tip()
    result, out = run(repo, monkeypatch, ENTRY["hypothesis"][0]("H-003"), push=True)
    assert_refused(repo, result, out, "H-003", "CHORE-003", tip)


def test_issue_reproduction_per_type(tmp_path, monkeypatch) -> None:
    """per_type: H-003 = allocation record only; `hypothesis create --id H-003 --push`
    printed `Created and pushed H-003`."""
    repo = make(tmp_path, "per_type")
    repo.seed({}, [record("H-003")])
    tip = repo.remote_tip()
    result, out = run(repo, monkeypatch, ENTRY["hypothesis"][0]("H-003"), push=True)
    assert_refused(repo, result, out, "H-003", "H-003", tip)


# --- 2. every CLI explicit-id entry point, both modes, local and --push ----------------------


@pytest.mark.parametrize("entry", sorted(ENTRY))
@pytest.mark.parametrize("mode", MODES)
@PUSH
def test_record_only_reservation_refuses_explicit_id(tmp_path, monkeypatch, entry, mode,
                                                     push) -> None:
    repo = make(tmp_path, mode)
    argv, item_id = ENTRY[entry]
    reserved = reserved_for(mode, item_id)
    repo.seed({}, [record(reserved)])  # in A's checkout and on origin; no item file anywhere
    tip = repo.remote_tip()
    result, out = run(repo, monkeypatch, argv(item_id), push)
    assert_refused(repo, result, out, item_id, reserved, tip)


@pytest.mark.parametrize("mode", MODES)
@PUSH
def test_reservation_matches_by_number_not_spelling(tmp_path, monkeypatch, mode, push) -> None:
    """`H-3` / `CHORE-3` reserve number 3: `--id H-003` is refused (same id-space rule as the
    existing checks, #634/#765)."""
    repo = make(tmp_path, mode)
    reserved = "H-3" if mode == "per_type" else "CHORE-3"
    repo.seed({}, [record(reserved)])
    tip = repo.remote_tip()
    result, out = run(repo, monkeypatch, ENTRY["hypothesis"][0]("H-003"), push)
    assert_refused(repo, result, out, "H-003", reserved if mode == "global" else "H-003", tip)


@pytest.mark.parametrize("mode", MODES)
@PUSH
def test_paper_scoped_reservation_refuses_same_paper_scoped_id(tmp_path, monkeypatch, mode,
                                                               push) -> None:
    """A record for `H130.3` reserves `H130.3` (its own id space) in both modes."""
    repo = make(tmp_path, mode)
    repo.seed({}, [record("H130.3")])
    tip = repo.remote_tip()
    result, out = run(repo, monkeypatch, ENTRY["hypothesis"][0]("H130.3"), push)
    assert_refused(repo, result, out, "H130.3", "H130.3", tip)


# --- 3. --push: a rival's reservation on origin that A has not fetched, or that races ---------


@pytest.mark.parametrize("timing", TIMINGS)
@pytest.mark.parametrize("mode", MODES)
def test_rival_reservation_on_origin_refuses_push(tmp_path, monkeypatch, mode, timing) -> None:
    repo = make(tmp_path, mode)
    reserved = reserved_for(mode, "M-003")

    def rival() -> None:
        b_push(repo, {}, [record(reserved)])

    on_timing(repo, monkeypatch, timing, rival)
    result, out = run(repo, monkeypatch, ENTRY["measure"][0]("M-003"), push=True)
    monkeypatch.undo()  # restore subprocess.run before reading the remote
    config_mod._theme_cache.clear()
    assert result.exit_code != 0, f"M-003 was pushed over {reserved}'s reservation: {out}"
    assert "reserved" in out and RESERVER in out, out
    if mode == "global":
        assert reserved in out, out
    assert "M-003" not in repo.remote_ids()


@pytest.mark.parametrize("mode", MODES)
def test_next_id_reservation_from_another_clone_refuses_push(tmp_path, monkeypatch, mode) -> None:
    """End to end: B runs `next-id` (a record, no file) and A then passes that number to
    `--id ... --push`."""
    repo = make(tmp_path, mode)
    reserve_prefix = "H" if mode == "per_type" else "CHORE"
    monkeypatch.setenv("YURTLE_AGENT", RESERVER)
    monkeypatch.chdir(repo.b)
    config_mod._theme_cache.clear()
    got = CliRunner().invoke(main, ["next-id", reserve_prefix, "--json"])
    assert got.exit_code == 0, output_of(got)
    reserved = json.loads(output_of(got))["id"]
    assert reserved == f"{reserve_prefix}-001", reserved
    tip = repo.remote_tip()
    result, out = run(repo, monkeypatch, ENTRY["hypothesis"][0]("H-001"), push=True)
    assert result.exit_code != 0, f"H-001 was pushed over B's next-id {reserved}: {out}"
    assert "reserved" in out, out
    if mode == "global":
        assert reserved in out, out
    assert "H-001" not in repo.remote_ids()
    assert repo.remote_tip() == tip


# --- 4. the service entry points ------------------------------------------------------------


@pytest.mark.parametrize("mode", MODES)
def test_service_create_item_explicit_id_refused(tmp_path, monkeypatch, mode) -> None:
    repo = make(tmp_path, mode)
    reserved = reserved_for(mode, "EXP-003")
    repo.seed({}, [record(reserved)])
    monkeypatch.chdir(repo.a)
    service = repo.service()
    with pytest.raises(InputRefused) as caught:
        service.create_item(WorkItemType.EXPEDITION, "Explicit exp", item_id="EXP-003")
    message = str(caught.value)
    assert "reserved" in message and RESERVER in message, message
    assert "EXP-003" not in repo.local_ids()


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("remote", [True, False], ids=["origin", "no-remote"])
def test_service_create_item_and_push_explicit_id_refused(tmp_path, monkeypatch, mode,
                                                          remote) -> None:
    repo = make(tmp_path, mode)
    reserved = reserved_for(mode, "EXP-003")
    repo.seed({}, [record(reserved)])
    if not remote:
        git(repo.a, "remote", "remove", "origin")
    tip = repo.remote_tip()
    head = git(repo.a, "rev-parse", "HEAD").strip()
    monkeypatch.setenv("YURTLE_AGENT", "creator-agent/s-create01")
    monkeypatch.chdir(repo.a)
    result = repo.service().create_item_and_push(
        WorkItemType.EXPEDITION, "Explicit exp", item_id="EXP-003"
    )
    assert result["success"] is False, result
    message = result["message"]
    assert "reserved" in message and RESERVER in message, message
    if mode == "global":
        assert reserved in message, message
    assert repo.remote_tip() == tip
    assert git(repo.a, "rev-parse", "HEAD").strip() == head, "a commit was made"
    assert "EXP-003" not in repo.local_ids()


# --- 5. controls ----------------------------------------------------------------------------


@pytest.mark.parametrize("entry", sorted(ENTRY))
@pytest.mark.parametrize("mode", MODES)
@PUSH
def test_control_free_number_is_accepted(tmp_path, monkeypatch, entry, mode, push) -> None:
    """No record and no file holds the number: the explicit id lands (Done when 2). Other
    numbers are reserved, so the check is not just "any record refuses"."""
    repo = make(tmp_path, mode)
    argv, item_id = ENTRY[entry]
    repo.seed({}, [record("CHORE-007"), record("H-008"), record("H130.3")])
    result, out = run(repo, monkeypatch, argv(item_id), push)
    assert_created(repo, result, out, item_id, push)


@PUSH
def test_control_per_type_other_prefix_record_does_not_reserve(tmp_path, monkeypatch,
                                                                push) -> None:
    """per_type: a CHORE-003 record does not hold H-003's number."""
    repo = make(tmp_path, "per_type")
    repo.seed({}, [record("CHORE-003")])
    result, out = run(repo, monkeypatch, ENTRY["hypothesis"][0]("H-003"), push)
    assert_created(repo, result, out, "H-003", push)


@PUSH
def test_control_global_paper_scoped_record_does_not_reserve(tmp_path, monkeypatch,
                                                             push) -> None:
    """global: an `H130.3` record holds no global number, so `H-003` is free."""
    repo = make(tmp_path, "global")
    repo.seed({}, [record("H130.3")])
    result, out = run(repo, monkeypatch, ENTRY["hypothesis"][0]("H-003"), push)
    assert_created(repo, result, out, "H-003", push)


@pytest.mark.parametrize("mode", MODES)
@PUSH
def test_control_existing_file_still_refused(tmp_path, monkeypatch, mode, push) -> None:
    """Today's behaviour stays: an id whose number an item file holds is refused."""
    repo = make(tmp_path, mode)
    holder = reserved_for(mode, "H-003")
    repo.seed({seed_path(holder): item_text(holder)})
    tip = repo.remote_tip()
    result, out = run(repo, monkeypatch, ENTRY["hypothesis"][0]("H-003"), push)
    assert result.exit_code != 0, out
    assert repo.remote_tip() == tip
    if mode == "global":
        assert "CHORE-003" in out, out
        assert "H-003" not in repo.local_ids()


@pytest.mark.parametrize("mode", MODES)
@PUSH
def test_old_record_still_reserves(tmp_path, monkeypatch, mode, push) -> None:
    """Records never expire (no TTL in the code): a years-old reservation still holds."""
    repo = make(tmp_path, mode)
    reserved = reserved_for(mode, "H-003")
    repo.seed({}, [record(reserved, at="2019-01-01T00:00:00")])
    tip = repo.remote_tip()
    result, out = run(repo, monkeypatch, ENTRY["hypothesis"][0]("H-003"), push)
    assert_refused(repo, result, out, "H-003", reserved, tip)


@pytest.mark.parametrize("mode", MODES)
@PUSH
def test_pruned_record_no_longer_reserves(tmp_path, monkeypatch, mode, push) -> None:
    """A writer keeps the last 100 records: once H-003's (or CHORE-003's) record has been
    pruned from the file, nothing holds number 3, so the explicit id lands."""
    repo = make(tmp_path, mode)
    reserved = reserved_for(mode, "H-003")
    later = [record(f"EXP-{n:03d}") for n in range(101, 201)]
    repo.seed({}, [record(reserved), *later])
    lock = repo.a / ".kanban" / "_ID_ALLOCATIONS.json"
    pruned = json.loads(lock.read_text())[-100:]  # as `_with_allocation` keeps them
    assert all(r["id"] != reserved for r in pruned)
    lock.write_text(json.dumps(pruned, indent=2))
    git(repo.a, "add", "-A")
    git(repo.a, "commit", "-m", "prune allocations")
    git(repo.a, "push", "origin", "main")
    result, out = run(repo, monkeypatch, ENTRY["hypothesis"][0]("H-003"), push)
    assert_created(repo, result, out, "H-003", push)


def test_refusal_names_who_and_when(tmp_path, monkeypatch) -> None:
    """The proposal's wording: `reserved by <allocated_by> at <time>`."""
    repo = make(tmp_path, "global")
    repo.seed({}, [record("CHORE-003")])
    result, out = run(repo, monkeypatch, ENTRY["hypothesis"][0]("H-003"), push=False)
    assert result.exit_code != 0, out
    assert f"reserved by {RESERVER}" in out, out
    assert "2026-11-01" in out, f"the refusal does not say when it was reserved: {out}"


# --- 6. driver ruling: the reserver may take its own reserved number --------------------------
# A record blocks an explicit id only when the creating actor (resolved as `allocated_by` is
# written: --agent / $YURTLE_AGENT, then git user.name) differs from the record's
# `allocated_by`. Section 2's tests are the different-actor controls.


def run_as(repo: Repo, monkeypatch, actor: str, argv: list[str], push: bool):
    monkeypatch.setenv("YURTLE_AGENT", actor)
    result = repo.cli(monkeypatch, *argv, *(["--push"] if push else []))
    return result, " ".join(output_of(result).split())


@pytest.mark.parametrize("mode", MODES)
@PUSH
def test_reserver_may_use_its_own_record(tmp_path, monkeypatch, mode, push) -> None:
    repo = make(tmp_path, mode)
    # ruled edit (driver, #1290 review r1 ruling 3): the exemption is per id space under global
    reserved = "H-003"
    repo.seed({}, [record(reserved, by=RESERVER)])
    result, out = run_as(repo, monkeypatch, RESERVER, ENTRY["hypothesis"][0]("H-003"), push)
    assert_created(repo, result, out, "H-003", push)


@pytest.mark.parametrize("mode", MODES)
@PUSH
def test_reserver_may_use_its_own_next_id(tmp_path, monkeypatch, mode, push) -> None:
    """End to end: A runs `next-id` as RESERVER, then creates that number with `--id` as the
    same actor."""
    repo = make(tmp_path, mode)
    # ruled edit (driver, #1290 review r1 ruling 3): the exemption is per id space under global
    reserve_prefix = "H"
    monkeypatch.setenv("YURTLE_AGENT", RESERVER)
    got = repo.cli(monkeypatch, "next-id", reserve_prefix, "--json")
    assert got.exit_code == 0, output_of(got)
    assert json.loads(output_of(got))["id"] == f"{reserve_prefix}-001", output_of(got)
    result, out = run_as(repo, monkeypatch, RESERVER, ENTRY["hypothesis"][0]("H-001"), push)
    assert_created(repo, result, out, "H-001", push)


@pytest.mark.parametrize("mode", MODES)
@PUSH
def test_other_actor_still_refused_after_own_next_id(tmp_path, monkeypatch, mode, push) -> None:
    """Control for the test above: the same next-id record refuses a DIFFERENT actor."""
    repo = make(tmp_path, mode)
    reserve_prefix = "H" if mode == "per_type" else "CHORE"
    monkeypatch.setenv("YURTLE_AGENT", RESERVER)
    got = repo.cli(monkeypatch, "next-id", reserve_prefix, "--json")
    assert got.exit_code == 0, output_of(got)
    tip = repo.remote_tip()
    result, out = run_as(
        repo, monkeypatch, "creator-agent/s-create01", ENTRY["hypothesis"][0]("H-001"), push
    )
    assert_refused(repo, result, out, "H-001", f"{reserve_prefix}-001", tip)


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("remote", [True, False], ids=["origin", "no-remote"])
def test_service_reserver_may_use_its_own_record(tmp_path, monkeypatch, mode, remote) -> None:
    repo = make(tmp_path, mode)
    # ruled edit (driver, #1290 review r1 ruling 3): the exemption is per id space under global
    repo.seed({}, [record("EXP-003", by=RESERVER)])
    if not remote:
        git(repo.a, "remote", "remove", "origin")
    monkeypatch.setenv("YURTLE_AGENT", RESERVER)
    monkeypatch.chdir(repo.a)
    result = repo.service().create_item_and_push(
        WorkItemType.EXPEDITION, "Explicit exp", item_id="EXP-003"
    )
    assert result["success"] is True, result
    assert "EXP-003" in (repo.remote_ids() if remote else repo.local_ids())


@pytest.mark.parametrize("mode", MODES)
def test_service_create_item_reserver_may_use_its_own_record(tmp_path, monkeypatch, mode) -> None:
    repo = make(tmp_path, mode)
    # ruled edit (driver, #1290 review r1 ruling 3): the exemption is per id space under global
    repo.seed({}, [record("EXP-003", by=RESERVER)])
    monkeypatch.setenv("YURTLE_AGENT", RESERVER)
    monkeypatch.chdir(repo.a)
    repo.service().create_item(WorkItemType.EXPEDITION, "Explicit exp", item_id="EXP-003")
    assert "EXP-003" in repo.local_ids()
