"""Issue #1157: #846's skip, end to end on the two paths the unit pins miss.

1. The mixed `_ID_ALLOCATIONS.json` exists only on origin/main (clone B pushed it);
   clone A has fetched it but its own tree has no such file, so `next-id --no-sync`
   reaches it only through `_get_next_id_number` -> `_fetched_default()`.
2. `create --push` with no remote rewrites the checkout's own file through
   `_local_allocations`: the rewrite keeps only object records.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.issues.test_585_create_push_loop import World, git
from tests.issues.test_603_push_failure_messages import invoke
from yurtle_kanban import config as config_mod

ALLOC = ".kanban/_ID_ALLOCATIONS.json"
MIXED = [1, {"id": "EXP-005", "prefix": "EXP", "number": 5, "allocated_by": "B"}, None]


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    return World(tmp_path)


@pytest.fixture(autouse=True)
def _clean_theme_cache() -> Iterator[None]:
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


def test_next_id_no_sync_reads_mixed_file_only_on_fetched_origin(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    (world.b / ALLOC).write_text(json.dumps(MIXED))
    git(world.b, "add", "-A")
    git(world.b, "commit", "-m", "mixed allocation records")
    git(world.b, "push", "origin", "HEAD:refs/heads/main")
    git(world.a, "fetch", "origin")
    assert not (world.a / ALLOC).exists()  # only origin/main holds it

    result = invoke(world, monkeypatch, ["next-id", "EXP", "--no-sync", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["id"] == "EXP-006"


def test_create_push_without_remote_rewrites_only_object_records(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    git(world.a, "remote", "remove", "origin")
    lock = world.a / ALLOC
    lock.write_text(json.dumps(MIXED))

    result = invoke(world, monkeypatch, ["create", "expedition", "Local push", "--push"])
    assert result.exit_code == 0, result.output
    records = json.loads(lock.read_text())
    assert all(isinstance(record, dict) for record in records), records
    assert [record["id"] for record in records] == ["EXP-005", "EXP-006"]
