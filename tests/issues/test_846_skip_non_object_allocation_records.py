"""Issue #846: non-object allocation records must not hide valid IDs."""
from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.issues.test_585_create_push_loop import World, git
from tests.issues.test_603_push_failure_messages import invoke
from yurtle_kanban import config as config_mod

ALLOC = ".kanban/_ID_ALLOCATIONS.json"
MALFORMED_ENTRIES = [
    1,
    {"id": "EXP-007", "prefix": "EXP", "number": 7, "allocated_by": "earlier"},
    None,
    {"id": "EXP-003", "prefix": "EXP", "number": 3, "allocated_by": "earlier"},
]


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    return World(tmp_path)


@pytest.fixture(autouse=True)
def _clean_theme_cache() -> Iterator[None]:
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


@pytest.mark.parametrize("sync_remote", [False, True], ids=["local", "remote"])
def test_next_id_skips_non_object_records_and_allocates_unique_ids(
    world: World, monkeypatch: pytest.MonkeyPatch, sync_remote: bool
) -> None:
    lock_file = world.a / ALLOC
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    lock_file.write_text(json.dumps(MALFORMED_ENTRIES, indent=2))

    if sync_remote:
        git(world.a, "add", ALLOC)
        git(world.a, "commit", "-m", "seed mixed allocation records")
        git(world.a, "push", "origin", "main")
    else:
        git(world.a, "remote", "remove", "origin")

    args = ["next-id", "EXP", "--json"]
    if not sync_remote:
        args.append("--no-sync")

    for expected_id in ("EXP-008", "EXP-009"):
        result = invoke(world, monkeypatch, args)
        assert result.exit_code == 0, result.output
        assert json.loads(result.output)["id"] == expected_id

    if sync_remote:
        records = json.loads(git(world.remote, "show", f"{world.default}:{ALLOC}"))
    else:
        records = json.loads(lock_file.read_text())

    assert all(isinstance(record, dict) for record in records)
    assert [record["id"] for record in records] == [
        "EXP-007",
        "EXP-003",
        "EXP-008",
        "EXP-009",
    ]
