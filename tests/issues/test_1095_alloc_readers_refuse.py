"""Issue #1095 — #846 follow-ups: dropped allocation records are said; readers refuse.

Decided shape ([steer] on #1095, bucket 2, basis #818's "refuse, never replace"):

1. A REWRITE of ``.kanban/_ID_ALLOCATIONS.json`` that drops non-object records (#846,
   e.g. ``[1, {...}, null, {...}]``) logs a warning naming how many were dropped and
   the file. The rewriters: a local ``create --push`` (no remote), ``next-id`` with its
   allocation commit (local ``--no-sync`` and the remote compare-and-swap), and
   ``create --push``'s ``_allocation_blob`` on the remote base. A read that rewrites
   nothing (``next-id --no-commit``) does not warn, nor does a rewrite that drops none.
2. The READERS — ``_scanned_next_id_number`` (the checkout's file) and
   ``_next_id_number_at`` (the file committed at a rev, e.g. the fetched origin/main) —
   refuse a file that isn't a JSON list exactly as the writers do (#818: "is not a valid
   JSON list of allocations"), instead of ``except Exception: pass`` flooring at 0 and
   handing out an id that may be taken. Every command that allocates through them
   (``next-id --no-commit``, plain ``create``, ``idea create``, ``epic create``,
   ``next-id --no-sync`` against a corrupt fetched origin/main) exits non-zero with a
   clean refusal naming the file, no traceback, and nothing created. A MISSING file
   stays fine.

Reuses the #585/#590/#818/#846 real-git harness: a bare remote, clone A, rival B.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator
from typing import Any

import pytest

from tests.issues.test_585_create_push_loop import (
    EXP_DIR,
    World,
    git,
    output_of,
    porcelain,
    run_create,
)
from tests.issues.test_603_push_failure_messages import invoke
from tests.issues.test_818_allocations_refuse_corrupt import (
    b_corrupts,
    drop_remote,
    origin_alloc_bytes,
    seed_local,
    seed_origin,
)
from yurtle_kanban import config as config_mod

ALLOC = ".kanban/_ID_ALLOCATIONS.json"

# two non-object records (1 and None) among two valid ones
MIXED = [
    1,
    {"id": "EXP-007", "prefix": "EXP", "number": 7, "allocated_by": "earlier"},
    None,
    {"id": "EXP-003", "prefix": "EXP", "number": 3, "allocated_by": "earlier"},
]
VALID = [
    {"id": "EXP-001", "prefix": "EXP", "number": 1, "allocated_by": "earlier"},
    {"id": "EXP-002", "prefix": "EXP", "number": 2, "allocated_by": "earlier"},
]

BAD = {
    "not-json": "not json",
    "object": '{"not": "a list"}',
}
REFUSAL = "is not a valid JSON list of allocations"


@pytest.fixture
def world(tmp_path, monkeypatch: pytest.MonkeyPatch) -> World:
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    return World(tmp_path)


@pytest.fixture(autouse=True)
def _clean_theme_cache() -> Iterator[None]:
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


# --- helpers ------------------------------------------------------------------------------


def said(result: Any, caplog: pytest.LogCaptureFixture) -> list[str]:
    """Everything the command said, one entry per logged warning or output line."""
    logged = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    return logged + output_of(result).splitlines()


def drop_warning(lines: list[str]) -> bool:
    """One warning that 2 records were dropped from the allocation file (loosely)."""
    return any(
        "_ID_ALLOCATIONS.json" in line and "dropp" in line.lower() and re.search(r"\b2\b", line)
        for line in lines
    )


def assert_drop_warned(result: Any, caplog: pytest.LogCaptureFixture) -> None:
    assert result.exit_code == 0, output_of(result)
    lines = said(result, caplog)
    assert drop_warning(lines), f"the rewrite dropped 2 non-object records silently: {lines!r}"


def assert_no_drop_warning(result: Any, caplog: pytest.LogCaptureFixture) -> None:
    assert result.exit_code == 0, output_of(result)
    lines = said(result, caplog)
    assert not any("dropp" in line.lower() for line in lines), (
        f"a drop warning where nothing was rewritten or dropped: {lines!r}"
    )


def state(world: World) -> dict[str, Any]:
    lock = world.a / ALLOC
    return {
        "head": git(world.a, "rev-parse", "HEAD").strip(),
        "tree": porcelain(world.a),
        "items": sorted(p.relative_to(world.a).as_posix()
                        for p in (world.a / "kanban-work").rglob("*") if p.is_file()),
        "alloc": lock.read_bytes() if lock.exists() else None,
        "origin": git(world.remote, "rev-parse", world.default).strip(),
        "origin_alloc": origin_alloc_bytes(world) if _has_origin_branch(world) else None,
    }


def _has_origin_branch(world: World) -> bool:
    return bool(git(world.remote, "branch", "--list", world.default).strip())


def assert_refused(result: Any, world: World, before: dict[str, Any]) -> None:
    out = output_of(result)
    assert result.exit_code != 0, f"not refused (exit 0) — handed out an id: {out}"
    assert isinstance(result.exception, SystemExit), (
        f"not a clean refusal: {result.exception!r}\n{out}"
    )
    assert "Traceback" not in out, out
    flat = " ".join(out.split())
    assert "_ID_ALLOCATIONS.json" in flat, f"the refusal does not name the file: {flat!r}"
    assert REFUSAL in flat, f"not #818's refusal: {flat!r}"
    assert state(world) == before, "something was created, committed or pushed"


def fetch_corrupt_origin(world: World, text: str) -> None:
    """B commits a corrupt allocation file on origin/main; A fetches it but its own
    checkout has no allocation file."""
    b_corrupts(world, text)
    git(world.a, "fetch", "origin")
    assert not (world.a / ALLOC).exists()


# --- 1. a rewrite that drops non-object records says so -----------------------------------


def test_local_create_push_rewrite_warns_dropped(world, monkeypatch, caplog) -> None:
    drop_remote(world)
    seed_local(world, json.dumps(MIXED, indent=2))
    with caplog.at_level(logging.WARNING):
        result = run_create(world, monkeypatch)
    assert_drop_warned(result, caplog)
    records = json.loads((world.a / ALLOC).read_text())
    assert [r["id"] for r in records] == ["EXP-007", "EXP-003", "EXP-008"]


def test_next_id_no_sync_rewrite_warns_dropped(world, monkeypatch, caplog) -> None:
    drop_remote(world)
    seed_local(world, json.dumps(MIXED, indent=2))
    with caplog.at_level(logging.WARNING):
        result = invoke(world, monkeypatch, ["next-id", "EXP", "--no-sync", "--json"])
    assert_drop_warned(result, caplog)


def test_next_id_remote_rewrite_warns_dropped(world, monkeypatch, caplog) -> None:
    seed_origin(world, json.dumps(MIXED, indent=2))
    with caplog.at_level(logging.WARNING):
        result = invoke(world, monkeypatch, ["next-id", "EXP", "--json"])
    assert_drop_warned(result, caplog)
    records = json.loads(origin_alloc_bytes(world) or b"null")
    assert [r["id"] for r in records] == ["EXP-007", "EXP-003", "EXP-008"]


def test_create_push_remote_rewrite_warns_dropped(world, monkeypatch, caplog) -> None:
    seed_origin(world, json.dumps(MIXED, indent=2))
    with caplog.at_level(logging.WARNING):
        result = run_create(world, monkeypatch)
    assert_drop_warned(result, caplog)


# controls: a read that rewrites nothing, and a rewrite that drops nothing, stay quiet


@pytest.mark.parametrize("sync", [False, True], ids=["no-sync", "sync"])
def test_control_next_id_no_commit_does_not_warn(world, monkeypatch, caplog, sync) -> None:
    seed_origin(world, json.dumps(MIXED, indent=2))
    args = ["next-id", "EXP", "--no-commit", "--json"] + ([] if sync else ["--no-sync"])
    with caplog.at_level(logging.WARNING):
        result = invoke(world, monkeypatch, args)
    assert_no_drop_warning(result, caplog)
    assert json.loads(result.output)["id"] == "EXP-008"


def test_control_rewrite_of_valid_list_does_not_warn(world, monkeypatch, caplog) -> None:
    drop_remote(world)
    seed_local(world, json.dumps(VALID, indent=2))
    with caplog.at_level(logging.WARNING):
        result = invoke(world, monkeypatch, ["next-id", "EXP", "--no-sync", "--json"])
    assert_no_drop_warning(result, caplog)


# --- 2. the readers refuse a file that isn't a JSON list -----------------------------------


@pytest.mark.parametrize("bad", list(BAD.values()), ids=list(BAD))
def test_next_id_no_commit_refuses_corrupt_local_file(world, monkeypatch, bad) -> None:
    drop_remote(world)
    seed_local(world, bad)
    before = state(world)
    result = invoke(world, monkeypatch, ["next-id", "EXP", "--no-sync", "--no-commit"])
    assert_refused(result, world, before)


@pytest.mark.parametrize("bad", list(BAD.values()), ids=list(BAD))
def test_plain_create_refuses_corrupt_local_file(world, monkeypatch, bad) -> None:
    drop_remote(world)
    seed_local(world, bad)
    before = state(world)
    result = invoke(world, monkeypatch, ["create", "expedition", "Alpha"])
    assert_refused(result, world, before)


@pytest.mark.parametrize("bad", list(BAD.values()), ids=list(BAD))
def test_idea_create_refuses_corrupt_local_file(world, monkeypatch, bad) -> None:
    drop_remote(world)
    seed_local(world, bad)
    before = state(world)
    result = invoke(world, monkeypatch, ["idea", "create", "An idea"])
    assert_refused(result, world, before)


@pytest.mark.parametrize("bad", list(BAD.values()), ids=list(BAD))
def test_epic_create_refuses_corrupt_local_file(world, monkeypatch, bad) -> None:
    drop_remote(world)
    seed_local(world, bad)
    before = state(world)
    result = invoke(world, monkeypatch, ["epic", "create", "An epic"])
    assert_refused(result, world, before)


# the file committed at a rev: the fetched origin/main (`_next_id_number_at`)


@pytest.mark.parametrize("bad", list(BAD.values()), ids=list(BAD))
@pytest.mark.parametrize("commit", [True, False], ids=["commit", "no-commit"])
def test_next_id_no_sync_refuses_corrupt_fetched_origin(
    world, monkeypatch, bad, commit
) -> None:
    fetch_corrupt_origin(world, bad)
    before = state(world)
    args = ["next-id", "EXP", "--no-sync"] + ([] if commit else ["--no-commit"])
    result = invoke(world, monkeypatch, args)
    assert_refused(result, world, before)


@pytest.mark.parametrize("bad", list(BAD.values()), ids=list(BAD))
def test_plain_create_refuses_corrupt_fetched_origin(world, monkeypatch, bad) -> None:
    fetch_corrupt_origin(world, bad)
    before = state(world)
    result = invoke(world, monkeypatch, ["create", "expedition", "Alpha"])
    assert_refused(result, world, before)


@pytest.mark.parametrize("bad", list(BAD.values()), ids=list(BAD))
def test_create_push_refuses_corrupt_origin_only(world, monkeypatch, bad) -> None:
    """Guard: already refused through `_allocation_blob` (#818); nothing is pushed."""
    b_corrupts(world, bad)
    before = state(world)
    result = run_create(world, monkeypatch)
    assert_refused(result, world, before)


# --- controls: a MISSING file is fine ------------------------------------------------------


def test_control_missing_file_next_id_no_commit(world, monkeypatch) -> None:
    drop_remote(world)
    assert not (world.a / ALLOC).exists()
    result = invoke(world, monkeypatch, ["next-id", "EXP", "--no-sync", "--no-commit", "--json"])
    assert result.exit_code == 0, output_of(result)
    assert json.loads(result.output)["id"] == "EXP-001"


def test_control_missing_file_plain_create(world, monkeypatch) -> None:
    drop_remote(world)
    result = invoke(world, monkeypatch, ["create", "expedition", "Alpha"])
    assert result.exit_code == 0, output_of(result)
    assert any(p.name.startswith("EXP-001") for p in (world.a / EXP_DIR).iterdir())


def test_control_missing_file_idea_and_epic_create(world, monkeypatch) -> None:
    drop_remote(world)
    idea = invoke(world, monkeypatch, ["idea", "create", "An idea"])
    assert idea.exit_code == 0, output_of(idea)
    epic = invoke(world, monkeypatch, ["epic", "create", "An epic"])
    assert epic.exit_code == 0, output_of(epic)
