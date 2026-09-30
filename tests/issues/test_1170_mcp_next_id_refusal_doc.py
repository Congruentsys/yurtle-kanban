"""Issue #1170: MCP `kanban_next_id`'s description names its two refusal shapes, and
each refusal really comes back in the shape it names (#847, #1162): `{"error": ...}`
for bad input or a corrupt allocations file when allocating locally; the refusal
dict (`success` false) for everything else, including a corrupt local file when
syncing (the compare-and-swap path)."""
from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import pytest

from tests.issues.test_585_create_push_loop import World, git
from tests.issues.test_818_allocations_refuse_corrupt import (
    b_corrupts,
    drop_remote,
    seed_local,
)
from tests.issues.test_847_allocation_refusals import failing_pre_commit, no_actor
from yurtle_kanban import config as config_mod
from yurtle_kanban.mcp import server as mcp_server


def test_next_id_description_names_both_refusal_shapes() -> None:
    tools = {t["name"]: t for t in mcp_server.KanbanMCPServer().get_tools()}
    said = tools["kanban_next_id"]["description"]
    assert '"error"' in said, said
    assert '"success": false' in said, said


# --- the shapes it names, through the real handler (the #1176 review's table) --------------



@pytest.fixture
def world(tmp_path, monkeypatch: pytest.MonkeyPatch) -> World:
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    return World(tmp_path)


@pytest.fixture(autouse=True)
def _clean_theme_cache() -> Iterator[None]:
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


def _corrupt_origin(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    b_corrupts(world, "{}")


def _corrupt_local_no_remote(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    drop_remote(world)
    seed_local(world, "{}")


def _corrupt_local(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    seed_local(world, "{}")


def _corrupt_fetched_origin(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    b_corrupts(world, "{}")
    git(world.a, "fetch", "origin")


def _refused_local_commit(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    drop_remote(world)
    failing_pre_commit(world)  # the pre-commit hook says no (#1177)


def _nothing(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    pass


CASES: dict[str, tuple[Callable[[World, pytest.MonkeyPatch], None], dict[str, Any], str]] = {
    "malformed-prefix": (_nothing, {"prefix": "E X"}, "error"),
    "corrupt-local-no-remote": (_corrupt_local_no_remote, {}, "error"),
    "corrupt-local-sync-false": (_corrupt_local, {"sync_remote": False}, "error"),
    "corrupt-local-syncing": (_corrupt_local, {}, "dict"),
    "corrupt-origin-syncing": (_corrupt_origin, {}, "dict"),
    "corrupt-fetched-origin-sync-false": (_corrupt_fetched_origin, {"sync_remote": False}, "dict"),
    "no-actor": (no_actor, {}, "dict"),
    "refused-local-commit": (_refused_local_commit, {}, "dict"),
}


@pytest.mark.parametrize("case", list(CASES))
def test_next_id_refusal_shape_is_as_described(world, monkeypatch, case) -> None:
    setup, args, shape = CASES[case]
    setup(world, monkeypatch)
    monkeypatch.chdir(world.a)
    server = mcp_server.KanbanMCPServer(repo_root=world.a)
    out = server.handle_tool_call("kanban_next_id", {"prefix": "EXP", **args})
    if shape == "error":
        assert set(out) == {"error"}, out
    else:
        assert out.get("success") is False and out.get("id") is None, out
        assert "error" not in out, out
