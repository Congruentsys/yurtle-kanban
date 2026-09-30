"""Issue #1161 — a non-UTF-8 / unreadable ``_ID_ALLOCATIONS.json`` is a clean refusal.

Decided shape: one helper reads the allocation file (the checkout's, or the one committed
at a git rev) and turns a decode/read error into ``InputRefused`` naming the file, the
#818 way. Its four callers: ``_scanned_next_id_number`` and ``_local_allocations``
(``read_text``), ``_next_id_number_at`` and ``_allocation_blob`` (``git show``; the latter's
refusal becomes ``_CasRefusedError``). Every command then exits non-zero with a one-line
``Error:`` naming ``_ID_ALLOCATIONS.json``, no traceback, and nothing written — instead of
dying with ``UnicodeDecodeError`` / ``PermissionError``.

A valid UTF-8 file with non-ASCII text in a record keeps working.

Reuses the #585/#818/#1095 real-git harness: a bare remote, clone A, rival B.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterator
from typing import Any

import pytest

from tests.issues.test_585_create_push_loop import World, git, output_of, run_create
from tests.issues.test_603_push_failure_messages import invoke
from tests.issues.test_818_allocations_refuse_corrupt import drop_remote, origin_alloc_bytes
from tests.issues.test_1095_alloc_readers_refuse import ALLOC, state
from yurtle_kanban import config as config_mod

# not valid UTF-8: a stray 0xff inside an otherwise fine record, and a UTF-16 BOM
NON_UTF8 = {
    "stray-ff": b'[{"id": "EXP-005\xff", "prefix": "EXP", "number": 5}]',
    "utf16-bom": b"\xff\xfe[]",
}


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


def seed_local_bytes(world: World, data: bytes) -> None:
    """A commits `data` as its allocation file."""
    lock = world.a / ALLOC
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_bytes(data)
    git(world.a, "add", "-A")
    git(world.a, "commit", "-m", "seed local allocations")


def b_pushes_bytes(world: World, data: bytes) -> None:
    """B commits `data` as the allocation file on a fresh origin/main and pushes it."""
    git(world.b, "fetch", "origin")
    git(world.b, "reset", "--hard", "origin/main")
    lock = world.b / ALLOC
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_bytes(data)
    git(world.b, "add", "-A")
    git(world.b, "commit", "-m", "rival writes non-UTF-8 allocations")
    git(world.b, "push", "origin", "HEAD:refs/heads/main")


def fetch_bad_origin(world: World, data: bytes) -> None:
    """The non-UTF-8 file is on origin/main only: A fetches it, has no file of its own."""
    b_pushes_bytes(world, data)
    git(world.a, "fetch", "origin")
    assert not (world.a / ALLOC).exists()


def assert_clean_refusal(result: Any, world: World, before: dict[str, Any]) -> None:
    out = output_of(result)
    assert result.exit_code != 0, f"not refused (exit 0) — handed out an id: {out}"
    assert isinstance(result.exception, SystemExit), (
        f"not a clean refusal: {result.exception!r}\n{out}"
    )
    assert "Traceback" not in out, out
    flat = " ".join(out.split())
    assert "_ID_ALLOCATIONS.json" in flat, f"the refusal does not name the file: {flat!r}"
    assert "UTF-8" in flat or "could not be read" in flat, (
        f"the refusal does not say what is wrong with the file: {flat!r}"
    )
    assert state(world) == before, "something was created, committed or pushed"


# --- 1. a non-UTF-8 file in the checkout, no remote ---------------------------------------

LOCAL_COMMANDS = {
    "next-id-no-commit": ["next-id", "EXP", "--no-sync", "--no-commit"],
    "plain-create": ["create", "expedition", "x"],
    "create-push": ["create", "expedition", "x", "--push"],
    "next-id-no-sync-commit": ["next-id", "EXP", "--no-sync"],
}


@pytest.mark.parametrize("data", list(NON_UTF8.values()), ids=list(NON_UTF8))
@pytest.mark.parametrize("argv", list(LOCAL_COMMANDS.values()), ids=list(LOCAL_COMMANDS))
def test_local_non_utf8_file_is_refused(world, monkeypatch, argv, data) -> None:
    drop_remote(world)
    seed_local_bytes(world, data)
    before = state(world)
    result = invoke(world, monkeypatch, argv)
    assert_clean_refusal(result, world, before)


# --- 2. a non-UTF-8 file committed on origin/main only ------------------------------------


@pytest.mark.parametrize("data", list(NON_UTF8.values()), ids=list(NON_UTF8))
def test_next_id_no_sync_refuses_non_utf8_fetched_origin(world, monkeypatch, data) -> None:
    """`_next_id_number_at` reads the fetched origin/main's file with `git show`."""
    fetch_bad_origin(world, data)
    before = state(world)
    result = invoke(world, monkeypatch, ["next-id", "EXP", "--no-sync"])
    assert_clean_refusal(result, world, before)


@pytest.mark.parametrize("data", list(NON_UTF8.values()), ids=list(NON_UTF8))
def test_create_push_refuses_non_utf8_origin(world, monkeypatch, data) -> None:
    """The compare-and-swap path: `_allocation_blob` on the remote base; nothing pushed."""
    b_pushes_bytes(world, data)
    before = state(world)
    result = run_create(world, monkeypatch, "x")
    assert_clean_refusal(result, world, before)
    assert origin_alloc_bytes(world) == data


@pytest.mark.parametrize("data", list(NON_UTF8.values()), ids=list(NON_UTF8))
def test_synced_next_id_refuses_non_utf8_origin(world, monkeypatch, data) -> None:
    """`next-id` with sync: the remote compare-and-swap reads origin's file."""
    b_pushes_bytes(world, data)
    before = state(world)
    result = invoke(world, monkeypatch, ["next-id", "EXP"])
    assert_clean_refusal(result, world, before)
    assert origin_alloc_bytes(world) == data


# --- 3. an unreadable file in the checkout ------------------------------------------------


def _chmod_is_enforced(path: Any) -> bool:
    if sys.platform.startswith("win") or (hasattr(os, "geteuid") and os.geteuid() == 0):
        return False
    try:
        path.read_bytes()
    except PermissionError:
        return True
    return False


def test_plain_create_refuses_unreadable_local_file(world, monkeypatch) -> None:
    drop_remote(world)
    seed_local_bytes(world, b"[]")
    lock = world.a / ALLOC
    before = state(world)  # snapshotted while readable; compared after restoring the mode
    lock.chmod(0)
    try:
        if not _chmod_is_enforced(lock):
            pytest.skip("chmod 000 does not stop reads here (root or platform)")
        result = invoke(world, monkeypatch, ["create", "expedition", "x"])
    finally:
        lock.chmod(0o644)
    assert_clean_refusal(result, world, before)


# --- 4. control: valid UTF-8 with non-ASCII text still works ------------------------------


def test_control_valid_utf8_non_ascii_record_works(world, monkeypatch) -> None:
    drop_remote(world)
    records = [{"id": "EXP-004", "prefix": "EXP", "number": 4, "allocated_by": "Zoë"}]
    seed_local_bytes(world, json.dumps(records, ensure_ascii=False).encode("utf-8"))
    result = invoke(world, monkeypatch, ["next-id", "EXP", "--no-sync", "--json"])
    assert result.exit_code == 0, output_of(result)
    assert json.loads(result.output)["id"] == "EXP-005"
