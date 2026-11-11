"""Issue #1290, review r1 of PR #1296 — partner tests for the driver's rulings on the
findings (the review's MAJOR 1, MINOR 2 and MINOR 3).

1. The reserver exemption resolves the creating actor WITHOUT the git `user.name` fallback
   (every session on a machine shares it): with no `$YURTLE_AGENT`, no record is "your own",
   so every record blocks an explicit id. Control: `$YURTLE_AGENT` equal to the record's
   `allocated_by` is still accepted.
2. A local (no `--push`) explicit-id create also checks the records on the already-fetched
   `origin/<default>`, as `_refuse_global_holder` reads it (no network: fetched, not merged).
3. Under `id_numbering: global`, the exemption covers only the actor's own record IN THE SAME
   ID SPACE as the requested id: the actor's own CHORE-003 record still blocks its H-003.

Reuses the #1288 harness and the first #1290 file's helpers; that file is not edited.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from tests.issues.test_585_create_push_loop import git, output_of
from tests.issues.test_590_next_id_and_hdd_ids import b_push
from tests.issues.test_1288_id_numbering import (  # noqa: F401  (autouse fixture)
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
from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import main

SHARED = "shared-host-user"  # one git user.name for both clones, as on one machine
HYP = ENTRY["hypothesis"][0]


def cli_in(clone, monkeypatch, *argv: str):
    monkeypatch.chdir(clone)
    config_mod._theme_cache.clear()
    result = CliRunner().invoke(main, list(argv))
    return result, " ".join(output_of(result).split())


def b_next_id(repo: Repo, monkeypatch, prefix: str) -> str:
    """Session 1 (clone B) reserves the next `prefix` number with `next-id` (pushed)."""
    result, out = cli_in(repo.b, monkeypatch, "next-id", prefix, "--json")
    assert result.exit_code == 0, out
    return json.loads(output_of(result))["id"]


# --- 1. no $YURTLE_AGENT: a shared git user.name never makes a record "your own" -------------


@pytest.mark.parametrize("mode", MODES)
@PUSH
def test_shared_user_name_without_agent_is_refused(tmp_path, monkeypatch, mode, push) -> None:
    """The review's probe: two sessions share git user.name, $YURTLE_AGENT unset; session 1
    runs next-id, session 2 passes that number to --id."""
    repo = make(tmp_path, mode)
    for clone in (repo.a, repo.b):
        git(clone, "config", "user.name", SHARED)
    monkeypatch.delenv("YURTLE_AGENT", raising=False)
    prefix = "H" if mode == "per_type" else "CHORE"
    reserved = b_next_id(repo, monkeypatch, prefix)
    assert reserved == f"{prefix}-001", reserved
    if not push:
        git(repo.a, "pull", "-q", "--no-rebase", "origin", "main")  # the record is in A's file
    tip = repo.remote_tip()
    result, out = cli_in(repo.a, monkeypatch, *HYP("H-001"), *(["--push"] if push else []))
    assert result.exit_code != 0, (
        f"session 2 took {reserved}'s number through the shared user.name {SHARED}: {out}"
    )
    assert "reserved" in out, out
    if mode == "global":
        assert reserved in out, out
    assert "H-001" not in repo.local_ids()
    assert "H-001" not in repo.remote_ids()
    assert repo.remote_tip() == tip


@pytest.mark.parametrize("mode", MODES)
@PUSH
def test_control_shared_user_name_with_matching_agent_is_accepted(
    tmp_path, monkeypatch, mode, push
) -> None:
    """Control: the same two clones, but both sessions set $YURTLE_AGENT to the same actor and
    the reservation is in the requested id space: accepted."""
    repo = make(tmp_path, mode)
    for clone in (repo.a, repo.b):
        git(clone, "config", "user.name", SHARED)
    monkeypatch.setenv("YURTLE_AGENT", RESERVER)
    assert b_next_id(repo, monkeypatch, "H") == "H-001"
    if not push:
        git(repo.a, "pull", "-q", "--no-rebase", "origin", "main")
    result, out = cli_in(repo.a, monkeypatch, *HYP("H-001"), *(["--push"] if push else []))
    assert_created(repo, result, out, "H-001", push)


# --- 2. local create reads the already-fetched origin/<default> -------------------------------


@pytest.mark.parametrize("mode", MODES)
def test_local_create_sees_record_on_fetched_origin(tmp_path, monkeypatch, mode) -> None:
    """B pushes a record; A fetches without merging; A's local `--id` is refused."""
    repo = make(tmp_path, mode)
    reserved = "H-003" if mode == "per_type" else "CHORE-003"
    b_push(repo, {}, [record(reserved)])
    git(repo.a, "fetch", "-q", "origin")
    assert not (repo.a / ".kanban" / "_ID_ALLOCATIONS.json").exists(), "A merged the record"
    monkeypatch.setenv("YURTLE_AGENT", "creator-agent/s-create01")
    tip = repo.remote_tip()
    result, out = cli_in(repo.a, monkeypatch, *HYP("H-003"))
    assert_refused(repo, result, out, "H-003", reserved, tip)


@pytest.mark.parametrize("mode", MODES)
def test_control_local_create_free_number_with_fetched_records(tmp_path, monkeypatch,
                                                               mode) -> None:
    """Control: the fetched origin's record does not block a free number."""
    repo = make(tmp_path, mode)
    b_push(repo, {}, [record("H-003" if mode == "per_type" else "CHORE-003")])
    git(repo.a, "fetch", "-q", "origin")
    monkeypatch.setenv("YURTLE_AGENT", "creator-agent/s-create01")
    result, out = cli_in(repo.a, monkeypatch, *HYP("H-004"))
    assert_created(repo, result, out, "H-004", push=False)


# --- 3. global: the exemption is for the actor's own record in the SAME id space --------------


@PUSH
def test_global_own_record_in_other_prefix_still_refuses(tmp_path, monkeypatch, push) -> None:
    """X holds CHORE-003; X's `--id H-003` is refused (X may later write CHORE-003)."""
    repo = make(tmp_path, "global")
    repo.seed({}, [record("CHORE-003", by=RESERVER)])
    monkeypatch.setenv("YURTLE_AGENT", RESERVER)
    tip = repo.remote_tip()
    result, out = cli_in(repo.a, monkeypatch, *HYP("H-003"), *(["--push"] if push else []))
    assert_refused(repo, result, out, "H-003", "CHORE-003", tip)


@PUSH
def test_global_own_next_id_in_other_prefix_still_refuses(tmp_path, monkeypatch, push) -> None:
    """End to end: X runs `next-id CHORE` (CHORE-001), then `--id H-001`: refused."""
    repo = make(tmp_path, "global")
    monkeypatch.setenv("YURTLE_AGENT", RESERVER)
    result, out = cli_in(repo.a, monkeypatch, "next-id", "CHORE", "--json")
    assert result.exit_code == 0, out
    assert json.loads(output_of(result))["id"] == "CHORE-001", out
    tip = repo.remote_tip()
    result, out = cli_in(repo.a, monkeypatch, *HYP("H-001"), *(["--push"] if push else []))
    assert_refused(repo, result, out, "H-001", "CHORE-001", tip)


@PUSH
def test_control_global_own_record_same_prefix_is_accepted(tmp_path, monkeypatch,
                                                           push) -> None:
    """Control: X's own H-003 record lets X create H-003 under global."""
    repo = make(tmp_path, "global")
    repo.seed({}, [record("H-003", by=RESERVER)])
    monkeypatch.setenv("YURTLE_AGENT", RESERVER)
    result, out = cli_in(repo.a, monkeypatch, *HYP("H-003"), *(["--push"] if push else []))
    assert_created(repo, result, out, "H-003", push)
