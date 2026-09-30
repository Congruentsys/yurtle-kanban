"""Issue #1169: `allocate_next_id`'s local path reads the checkout's allocations
file ONCE. It read it twice (the #847 raise check, then again inside
`_get_next_id_number`), catching any `InputRefused` from the second read as
origin's, so a local file going bad between the reads came back as the dict.
"""
from __future__ import annotations

from collections.abc import Iterator

import pytest

from tests.issues.test_585_create_push_loop import World, git
from tests.issues.test_818_allocations_refuse_corrupt import service
from yurtle_kanban import config as config_mod
from yurtle_kanban.models import InputRefused
from yurtle_kanban.service import KanbanService

LOCAL_PATHS = {
    "no-sync": {"sync_remote": False},
    "no-commit": {"commit_allocation": False},
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


@pytest.mark.parametrize("kwargs", list(LOCAL_PATHS.values()), ids=list(LOCAL_PATHS))
def test_local_file_read_once_and_never_a_dict(world, monkeypatch, kwargs) -> None:
    git(world.a, "fetch", "origin")
    real = KanbanService._scanned_next_id_number
    calls: list[str] = []

    def goes_bad(self: KanbanService, prefix: str) -> int:
        calls.append(prefix)
        if len(calls) > 1:  # the local file went bad after the first read
            raise InputRefused("local _ID_ALLOCATIONS.json went bad; nothing was changed")
        return real(self, prefix)

    monkeypatch.setattr(KanbanService, "_scanned_next_id_number", goes_bad)
    monkeypatch.chdir(world.a)
    result = service(world).allocate_next_id("EXP", **kwargs)
    assert len(calls) == 1, f"the local file was read {len(calls)} times"
    assert result["success"] is True, result
