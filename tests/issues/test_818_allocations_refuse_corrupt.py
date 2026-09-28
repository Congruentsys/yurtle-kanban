"""Issue #818 — a corrupt ``_ID_ALLOCATIONS.json`` is refused, never replaced.

Follow-up from the PR #810 round-2 review (#788). If origin's
``.kanban/_ID_ALLOCATIONS.json`` exists but is not a valid JSON list,
``_allocation_blob`` starts a fresh list and the push replaces the whole file with a
single record: every earlier allocation on origin is lost.

Decided behaviour ([steer] on #818, bucket 1):

1. Every push path that appends to origin's allocation file — ``create --push`` /
   ``create_item_and_push`` with a remote (#585), and ``next-id`` /
   ``allocate_next_id`` with a remote (#590) — refuses a file that exists but is not a
   valid JSON list (``{not json``, ``{}``, ``"x"``, ``42``, an empty file). The refusal
   names the file and says to fix or remove it. Nothing is created and nothing is
   pushed: origin's SHA and allocation bytes are unchanged, and A's HEAD and tree are
   untouched.
2. The same holds when the corruption reaches origin from a rival (B), and when it
   lands between A's fetch and A's push (the retry on the fresh base refuses).
3. The local paths (no remote, and ``next-id --no-sync``) read and rewrite the
   checkout's own allocation file the same way today (``except: allocations = []``),
   so they overwrite it too. Same rule: refused, file named, nothing written or
   committed.
4. Controls: a MISSING file still starts a fresh list, and a valid list is appended
   to, earlier records kept.

Reuses the #585/#590 real-git harness: a bare remote, clone A under test, rival B.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from tests.issues.test_585_create_push_loop import (
    EXP_DIR,
    PushHook,
    World,
    git,
    output_of,
    porcelain,
    run_create,
)
from tests.issues.test_590_next_id_and_hdd_ids import ALLOC
from tests.issues.test_603_push_failure_messages import invoke
from yurtle_kanban import config as config_mod
from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.models import InputRefused, WorkItemType
from yurtle_kanban.service import KanbanService

_ORIG_RUN = subprocess.run

BAD = {
    "not-json": "{not json",
    "object": "{}",
    "string": '"x"',
    "number": "42",
    "empty": "",
}

EARLIER = [
    {"id": "EXP-001", "prefix": "EXP", "number": 1, "allocated_by": "earlier"},
    {"id": "EXP-002", "prefix": "EXP", "number": 2, "allocated_by": "earlier"},
]


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


def seed_origin(world: World, text: str) -> None:
    """A commits `text` as the allocation file on main and pushes it."""
    lock = world.a / ALLOC
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(text)
    git(world.a, "add", "-A")
    git(world.a, "commit", "-m", "seed allocations")
    git(world.a, "push", "origin", "main")


def b_corrupts(world: World, text: str) -> None:
    """B writes `text` as the allocation file on a fresh origin/main and pushes it."""
    git(world.b, "fetch", "origin")
    git(world.b, "reset", "--hard", "origin/main")
    lock = world.b / ALLOC
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(text)
    git(world.b, "add", "-A")
    git(world.b, "commit", "-m", "rival corrupts allocations")
    git(world.b, "push", "origin", "HEAD:refs/heads/main")


def origin_alloc_bytes(world: World) -> bytes | None:
    shown = _ORIG_RUN(
        ["git", "show", f"main:{ALLOC}"], cwd=world.remote, capture_output=True
    )
    return shown.stdout if shown.returncode == 0 else None


def snapshot(world: World) -> dict[str, Any]:
    return {
        "origin": world.remote_sha(),
        "alloc": origin_alloc_bytes(world),
        "head": git(world.a, "rev-parse", "HEAD").strip(),
        "tree": porcelain(world.a),
        "items": sorted(p.name for p in (world.a / EXP_DIR).iterdir()),
        "local_alloc": (world.a / ALLOC).read_bytes() if (world.a / ALLOC).exists() else None,
    }


def assert_refused_names_file(message: str) -> None:
    flat = " ".join(message.split())
    assert "_ID_ALLOCATIONS.json" in flat, f"the refusal does not name the file: {flat!r}"
    low = flat.lower()
    assert "fix" in low and "remove" in low, (
        f"the refusal does not say to fix or remove the file: {flat!r}"
    )


def assert_nothing_happened(world: World, before: dict[str, Any]) -> None:
    after = snapshot(world)
    assert after["origin"] == before["origin"], "origin/main moved: something was pushed"
    assert after["alloc"] == before["alloc"], (
        f"origin's allocation file changed: {before['alloc']!r} -> {after['alloc']!r}"
    )
    assert after["head"] == before["head"], "A's HEAD moved: something was committed"
    assert after["tree"] == before["tree"], f"A's tree changed: {after['tree']}"
    assert after["items"] == before["items"], f"an item file was left: {after['items']}"
    assert after["local_alloc"] == before["local_alloc"], "A's allocation file was rewritten"


def service(world: World) -> KanbanService:
    return KanbanService(KanbanConfig.load(world.a / ".kanban" / "config.yaml"), world.a)


def service_refusal(call: Callable[[], dict[str, Any]]) -> str:
    """The refusal message from a service call: a failure dict or an InputRefused."""
    try:
        result = call()
    except InputRefused as e:
        return str(e)
    assert not result.get("success"), f"not refused: {result}"
    return str(result.get("message", ""))


def cli_refusal(result: Any) -> str:
    out = output_of(result)
    assert result.exit_code != 0, f"not refused (exit 0): {out}"
    return out


# --- 1. origin's file is corrupt: every push path refuses ------------------------------------


@pytest.mark.parametrize("bad", list(BAD.values()), ids=list(BAD))
def test_create_push_cli_refuses_corrupt_origin_file(world, monkeypatch, bad) -> None:
    seed_origin(world, bad)
    before = snapshot(world)
    message = cli_refusal(run_create(world, monkeypatch))
    assert_refused_names_file(message)
    assert_nothing_happened(world, before)


@pytest.mark.parametrize("bad", list(BAD.values()), ids=list(BAD))
def test_create_item_and_push_refuses_corrupt_origin_file(world, monkeypatch, bad) -> None:
    seed_origin(world, bad)
    before = snapshot(world)
    monkeypatch.chdir(world.a)
    message = service_refusal(
        lambda: service(world).create_item_and_push(WorkItemType.EXPEDITION, "Alpha")
    )
    assert_refused_names_file(message)
    assert_nothing_happened(world, before)


@pytest.mark.parametrize("bad", list(BAD.values()), ids=list(BAD))
def test_next_id_cli_refuses_corrupt_origin_file(world, monkeypatch, bad) -> None:
    seed_origin(world, bad)
    before = snapshot(world)
    message = cli_refusal(invoke(world, monkeypatch, ["next-id", "EXP", "--json"]))
    assert_refused_names_file(message)
    assert_nothing_happened(world, before)


@pytest.mark.parametrize("bad", list(BAD.values()), ids=list(BAD))
def test_allocate_next_id_with_sync_refuses_corrupt_origin_file(
    world, monkeypatch, bad
) -> None:
    seed_origin(world, bad)
    before = snapshot(world)
    monkeypatch.chdir(world.a)
    message = service_refusal(
        lambda: service(world).allocate_next_id(
            "EXP", sync_remote=True, commit_allocation=True
        )
    )
    assert_refused_names_file(message)
    assert_nothing_happened(world, before)


# --- 2. the corruption comes from a rival -----------------------------------------------------


def test_create_push_refuses_when_rival_corrupted_origin(world, monkeypatch) -> None:
    """A's own checkout has no allocation file; only origin's (from B) is corrupt."""
    b_corrupts(world, "{not json")
    before = snapshot(world)
    message = cli_refusal(run_create(world, monkeypatch))
    assert_refused_names_file(message)
    assert_nothing_happened(world, before)


def test_next_id_refuses_when_corruption_lands_mid_race(world, monkeypatch) -> None:
    """B corrupts origin's file just before A's first push: the retry on the fresh base
    refuses instead of replacing it."""
    seed_origin(world, json.dumps(EARLIER, indent=2))
    hook = PushHook(lambda n: b_corrupts(world, "{}") if n == 1 else None)
    monkeypatch.setattr(subprocess, "run", hook)
    result = invoke(world, monkeypatch, ["next-id", "EXP", "--json"])
    monkeypatch.setattr(subprocess, "run", _ORIG_RUN)
    message = cli_refusal(result)
    assert_refused_names_file(message)
    assert origin_alloc_bytes(world) == b"{}", "origin's corrupt file was replaced"


# --- 3. the local paths: the checkout's own file ----------------------------------------------


def drop_remote(world: World) -> None:
    git(world.a, "remote", "remove", "origin")


def seed_local(world: World, text: str) -> None:
    lock = world.a / ALLOC
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(text)
    git(world.a, "add", "-A")
    git(world.a, "commit", "-m", "seed local allocations")


def local_snapshot(world: World) -> dict[str, Any]:
    return {
        "head": git(world.a, "rev-parse", "HEAD").strip(),
        "tree": porcelain(world.a),
        "items": sorted(p.name for p in (world.a / EXP_DIR).iterdir()),
        "alloc": (world.a / ALLOC).read_bytes(),
    }


@pytest.mark.parametrize("bad", list(BAD.values()), ids=list(BAD))
def test_create_push_without_remote_refuses_corrupt_local_file(
    world, monkeypatch, bad
) -> None:
    drop_remote(world)
    seed_local(world, bad)
    before = local_snapshot(world)
    message = cli_refusal(run_create(world, monkeypatch))
    assert_refused_names_file(message)
    assert local_snapshot(world) == before


@pytest.mark.parametrize("bad", list(BAD.values()), ids=list(BAD))
def test_next_id_without_remote_refuses_corrupt_local_file(world, monkeypatch, bad) -> None:
    drop_remote(world)
    seed_local(world, bad)
    before = local_snapshot(world)
    message = cli_refusal(invoke(world, monkeypatch, ["next-id", "EXP", "--json"]))
    assert_refused_names_file(message)
    assert local_snapshot(world) == before


@pytest.mark.parametrize("bad", list(BAD.values()), ids=list(BAD))
def test_next_id_no_sync_refuses_corrupt_local_file(world, monkeypatch, bad) -> None:
    """`--no-sync` with a remote takes the local path: the checkout's file."""
    seed_origin(world, bad)
    before = snapshot(world)
    message = cli_refusal(
        invoke(world, monkeypatch, ["next-id", "EXP", "--no-sync", "--json"])
    )
    assert_refused_names_file(message)
    assert_nothing_happened(world, before)


# --- 4. controls: a missing file starts fresh, a valid list is appended to ----------------------


def origin_records(world: World) -> list[dict[str, Any]]:
    raw = origin_alloc_bytes(world)
    assert raw is not None, "origin has no allocation file"
    records = json.loads(raw)
    assert isinstance(records, list), records
    return records


def test_control_create_push_missing_file_starts_fresh(world, monkeypatch) -> None:
    assert origin_alloc_bytes(world) is None
    result = run_create(world, monkeypatch)
    assert result.exit_code == 0, output_of(result)
    assert [r["id"] for r in origin_records(world)] == ["EXP-001"]


def test_control_next_id_missing_file_starts_fresh(world, monkeypatch) -> None:
    result = invoke(world, monkeypatch, ["next-id", "EXP", "--json"])
    assert result.exit_code == 0, output_of(result)
    assert [r["id"] for r in origin_records(world)] == ["EXP-001"]


def test_control_create_push_valid_list_is_appended(world, monkeypatch) -> None:
    seed_origin(world, json.dumps(EARLIER, indent=2))
    result = run_create(world, monkeypatch)
    assert result.exit_code == 0, output_of(result)
    assert [r["id"] for r in origin_records(world)] == ["EXP-001", "EXP-002", "EXP-003"]


def test_control_next_id_valid_list_is_appended(world, monkeypatch) -> None:
    seed_origin(world, json.dumps(EARLIER, indent=2))
    result = invoke(world, monkeypatch, ["next-id", "EXP", "--json"])
    assert result.exit_code == 0, output_of(result)
    assert [r["id"] for r in origin_records(world)] == ["EXP-001", "EXP-002", "EXP-003"]


def test_control_local_create_missing_file_starts_fresh(world, monkeypatch) -> None:
    drop_remote(world)
    result = run_create(world, monkeypatch)
    assert result.exit_code == 0, output_of(result)
    records = json.loads((world.a / ALLOC).read_text())
    assert [r["id"] for r in records] == ["EXP-001"]


def test_control_local_next_id_valid_list_is_appended(world, monkeypatch) -> None:
    drop_remote(world)
    seed_local(world, json.dumps(EARLIER, indent=2))
    result = invoke(world, monkeypatch, ["next-id", "EXP", "--json"])
    assert result.exit_code == 0, output_of(result)
    records = json.loads((world.a / ALLOC).read_text())
    assert [r["id"] for r in records] == ["EXP-001", "EXP-002", "EXP-003"]

