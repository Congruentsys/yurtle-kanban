"""Issue #1153: ``create`` and ``create --push`` skip non-object allocation records.

PR #855 (#846) made ``_parse_allocations`` skip non-object records; only ``next-id``
had a regression test. Both ``create`` paths read the same file: seeded with
``[1, EXP-007 record, null]`` they must allocate EXP-008 (not reuse an id).
``create --push`` also writes the file back to origin, holding only object records.
Local ``create`` reads the file but does not rewrite it, so only its id is asserted.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.issues.test_585_create_push_loop import EXP_DIR, World, git
from tests.issues.test_603_push_failure_messages import invoke
from yurtle_kanban import config as config_mod

ALLOC = ".kanban/_ID_ALLOCATIONS.json"
MIXED_ENTRIES = [
    1,
    {"id": "EXP-007", "prefix": "EXP", "number": 7, "allocated_by": "earlier"},
    None,
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


def _seed(world: World) -> None:
    lock_file = world.a / ALLOC
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    lock_file.write_text(json.dumps(MIXED_ENTRIES, indent=2))


def _assert_only_dicts_ending_with_exp_008(records: list[object]) -> None:
    assert all(isinstance(record, dict) for record in records), records
    ids = [record["id"] for record in records]  # type: ignore[index]
    assert ids[-1] == "EXP-008", ids
    assert ids.count("EXP-008") == 1, ids
    assert "EXP-007" in ids, ids


def test_create_local_skips_non_object_records(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(world)
    git(world.a, "remote", "remove", "origin")

    result = invoke(world, monkeypatch, ["create", "expedition", "Local mixed alloc"])
    assert result.exit_code == 0, result.output
    assert "EXP-008" in result.output, result.output

    created = sorted(p.name for p in (world.a / EXP_DIR).glob("EXP-*.md"))
    assert len(created) == 1 and created[0].startswith("EXP-008"), created


def test_create_push_skips_non_object_records(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(world)
    git(world.a, "add", ALLOC)
    git(world.a, "commit", "-m", "seed mixed allocation records")
    git(world.a, "push", "origin", "main")

    result = invoke(
        world, monkeypatch, ["create", "expedition", "Remote mixed alloc", "--push"]
    )
    assert result.exit_code == 0, result.output

    assert list(world.remote_items()) == ["EXP-008"], world.remote_items()
    _assert_only_dicts_ending_with_exp_008(json.loads(world.remote_show(ALLOC)))
