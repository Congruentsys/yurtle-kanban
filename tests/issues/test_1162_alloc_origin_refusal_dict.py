"""Issue #1162: `allocate_next_id`'s refusal contract is by WHICH file is corrupt.

[steer] on #1162, from #847's amended steer: a corrupt LOCAL allocations file
raises `InputRefused` (the #786 design MCP relies on); a corrupt ORIGIN comes back
as the refusal dict. Since #1095 the local path (`sync_remote=False`, or
`commit_allocation=False`) reads the fetched origin/<default> and raised there.
"""
from __future__ import annotations

from collections.abc import Iterator

import pytest

from tests.issues.test_585_create_push_loop import World, git
from tests.issues.test_818_allocations_refuse_corrupt import b_corrupts, seed_local, service
from tests.issues.test_847_allocation_refusals import assert_refusal_dict, service_result
from yurtle_kanban import config as config_mod
from yurtle_kanban.models import InputRefused

LOCAL_PATHS = {
    "no-sync": {"sync_remote": False},
    "no-commit": {"commit_allocation": False},
    "no-sync-no-commit": {"sync_remote": False, "commit_allocation": False},
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
def test_local_path_corrupt_fetched_origin_returns_dict(world, monkeypatch, kwargs) -> None:
    b_corrupts(world, "{}")
    git(world.a, "fetch", "origin")  # the local path reads what is fetched
    monkeypatch.chdir(world.a)
    head = git(world.a, "rev-parse", "HEAD")
    result = service_result(lambda: service(world).allocate_next_id("EXP", **kwargs))
    assert_refusal_dict(result)
    assert "not a valid JSON list" in result["message"], result
    assert git(world.a, "rev-parse", "HEAD") == head  # nothing committed


@pytest.mark.parametrize("kwargs", list(LOCAL_PATHS.values()), ids=list(LOCAL_PATHS))
def test_control_local_path_corrupt_local_file_still_raises(world, monkeypatch, kwargs) -> None:
    git(world.a, "remote", "remove", "origin")
    seed_local(world, "{}")
    monkeypatch.chdir(world.a)
    with pytest.raises(InputRefused, match="not a valid JSON list"):
        service(world).allocate_next_id("EXP", **kwargs)
