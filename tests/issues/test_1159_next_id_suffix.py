"""Issue #1159: `next-id`'s success line said "(committed and pushed to remote)"
whenever `--no-sync` wasn't given, even under `--no-commit` or with no remote.

Expected ([steer]): the result names where the allocation went, `recorded`:
`pushed` (the compare-and-swap on origin), `committed` (a local commit only) or
`none` (`--no-commit`), and the text line says the same."""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest

from tests.issues.test_585_create_push_loop import World
from tests.issues.test_818_allocations_refuse_corrupt import drop_remote
from tests.issues.test_847_allocation_refusals import next_id
from yurtle_kanban import config as config_mod

PUSHED = "(committed and pushed to remote)"
COMMITTED = "(committed locally, not pushed)"
NONE = "(not recorded: --no-commit)"


@pytest.fixture
def world(tmp_path, monkeypatch: pytest.MonkeyPatch) -> World:
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    monkeypatch.delenv("YURTLE_AGENT", raising=False)
    return World(tmp_path)


@pytest.fixture(autouse=True)
def _clean_theme_cache() -> Iterator[None]:
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


def _only(out: str, want: str) -> None:
    for line in (PUSHED, COMMITTED, NONE):
        assert (line in out) == (line == want), out


def test_remote_allocation_says_pushed(world: World, monkeypatch) -> None:
    result = next_id(world, monkeypatch, "FEAT")
    assert result.exit_code == 0, result.output
    _only(result.output, PUSHED)
    data = json.loads(next_id(world, monkeypatch, "FEAT", "--json").stdout)
    assert data["recorded"] == "pushed", data


def test_no_commit_says_not_recorded(world: World, monkeypatch) -> None:
    result = next_id(world, monkeypatch, "FEAT", "--no-commit")
    assert result.exit_code == 0, result.output
    _only(result.output, NONE)
    data = json.loads(next_id(world, monkeypatch, "FEAT", "--no-commit", "--json").stdout)
    assert data["recorded"] == "none", data


def test_no_remote_says_committed_locally(world: World, monkeypatch) -> None:
    drop_remote(world)
    result = next_id(world, monkeypatch, "FEAT")
    assert result.exit_code == 0, result.output
    _only(result.output, COMMITTED)
    data = json.loads(next_id(world, monkeypatch, "FEAT", "--json").stdout)
    assert data["recorded"] == "committed", data


def test_no_sync_says_committed_locally(world: World, monkeypatch) -> None:
    """`--no-sync` commits locally, as before; it now says so rather than nothing."""
    result = next_id(world, monkeypatch, "FEAT", "--no-sync")
    assert result.exit_code == 0, result.output
    _only(result.output, COMMITTED)
