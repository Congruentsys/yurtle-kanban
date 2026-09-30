"""Issue #932: pin #846's non-object skip in `_next_id_number_at`.

The #846 tests stay green with raw `json.loads` put back there, because the
checkout's own copy of `_ID_ALLOCATIONS.json` already sets the floor. Here the mixed
file exists only at the commit read, never in the working tree.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.issues.test_585_create_push_loop import World, git
from tests.test_634_explicit_ids_on_base import service
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


def test_committed_mixed_file_sets_the_floor_without_a_local_copy(world: World) -> None:
    lock = world.a / ALLOC
    lock.write_text(json.dumps(MIXED))
    git(world.a, "add", "-A")
    git(world.a, "commit", "-qm", "mixed allocation records")
    rev = git(world.a, "rev-parse", "HEAD").strip()
    lock.unlink()  # only the commit holds it now
    assert service(world)._next_id_number_at(rev, "EXP") == 6
