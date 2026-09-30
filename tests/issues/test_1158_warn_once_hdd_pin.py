"""Issue #1158 — #1095 follow-ups: the drop warning prints once; hdd refusal pinned.

From the PR #1156 review.

1. A ``create --push`` or ``next-id`` (synced, with a remote) whose compare-and-swap
   push loses a race once and then succeeds rewrites ``_ID_ALLOCATIONS.json`` once, so
   the "dropping N non-object record(s)" warning (#1095) prints EXACTLY ONCE per
   command, not once per attempt. The rival keeps origin's non-object records (it
   ``json.loads`` and appends), so the retried base still has them to drop. Control:
   the plain single-attempt command also warns exactly once.
2. Pin: every hdd create command (``hypothesis create --paper N``, ``hypothesis
   create`` without a paper, ``experiment create``, ``literature create``, ``measure
   create``) refuses a corrupt local allocation file as #818/#1095 do: non-zero exit,
   "is not a valid JSON list of allocations", no traceback, nothing written.

Reuses the #585/#818/#1095 real-git harness (a bare remote, clone A, rival B).
"""

from __future__ import annotations

import json
import logging
import subprocess
from collections.abc import Iterator
from typing import Any

import pytest

from tests.issues.test_585_create_push_loop import (
    PushHook,
    World,
    git,
    output_of,
    porcelain,
    run_create,
)
from tests.issues.test_603_push_failure_messages import HDD_DIRS, invoke, reconfigure
from tests.issues.test_818_allocations_refuse_corrupt import (
    drop_remote,
    origin_alloc_bytes,
    seed_local,
    seed_origin,
)
from tests.issues.test_1095_alloc_readers_refuse import BAD, MIXED, REFUSAL, said
from yurtle_kanban import config as config_mod

ALLOC = ".kanban/_ID_ALLOCATIONS.json"


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


def drop_lines(result: Any, caplog: pytest.LogCaptureFixture) -> list[str]:
    return [line for line in said(result, caplog) if "dropp" in line.lower()]


def lose_race_once(world: World, monkeypatch: pytest.MonkeyPatch) -> PushHook:
    """B pushes a rival item just before A's first push (the #585 lost race)."""
    hook = PushHook(lambda n: world.b_push_item() if n == 1 else None)
    monkeypatch.setattr(subprocess, "run", hook)
    return hook


def assert_rival_kept_non_objects(world: World) -> None:
    """The retried base still holds the non-object records, so it has some to drop."""
    assert world.b_pushes == 1
    rival = json.loads((world.b / ALLOC).read_text())
    assert rival[: len(MIXED)] == MIXED, f"the rival rewrote origin's records: {rival!r}"
    assert sum(not isinstance(r, dict) for r in rival) == 2, rival


COMMANDS = {
    "create-push": lambda world, monkeypatch: run_create(world, monkeypatch),
    "next-id": lambda world, monkeypatch: invoke(
        world, monkeypatch, ["next-id", "EXP", "--json"]
    ),
}


# --- 1. the drop warning prints once per command, even when the push retries ---------------


@pytest.mark.parametrize("cmd", list(COMMANDS))
def test_drop_warning_once_when_push_retries(world, monkeypatch, caplog, cmd) -> None:
    seed_origin(world, json.dumps(MIXED, indent=2))
    hook = lose_race_once(world, monkeypatch)
    with caplog.at_level(logging.WARNING):
        result = COMMANDS[cmd](world, monkeypatch)
    out = output_of(result)
    assert result.exit_code == 0, out
    assert hook.pushes >= 2, f"A never retried its push: {out}"
    assert_rival_kept_non_objects(world)
    records = json.loads(origin_alloc_bytes(world) or b"null")
    assert all(isinstance(r, dict) for r in records), f"origin kept non-objects: {records!r}"
    lines = drop_lines(result, caplog)
    assert len(lines) == 1, f"the drop warning printed {len(lines)} times: {lines!r}"


@pytest.mark.parametrize("cmd", list(COMMANDS))
def test_control_drop_warning_once_single_attempt(world, monkeypatch, caplog, cmd) -> None:
    seed_origin(world, json.dumps(MIXED, indent=2))
    with caplog.at_level(logging.WARNING):
        result = COMMANDS[cmd](world, monkeypatch)
    assert result.exit_code == 0, output_of(result)
    lines = drop_lines(result, caplog)
    assert len(lines) == 1, f"the drop warning printed {len(lines)} times: {lines!r}"


# --- 2. pin: every hdd create command refuses a corrupt local allocation file ---------------


HDD_CREATES = {
    "hypothesis-paper": ["hypothesis", "create", "A statement", "--paper", "130"],
    "hypothesis": ["hypothesis", "create", "A statement"],
    "experiment": ["experiment", "create", "--title", "A probe"],
    "literature": ["literature", "create", "A survey"],
    "measure": ["measure", "create", "Accuracy", "--unit", "percent", "--category", "accuracy"],
}


def hdd(world: World) -> None:
    reconfigure(world, "hdd", "research/", [f"research/{d}/" for d in HDD_DIRS])


def hdd_state(world: World) -> dict[str, Any]:
    lock = world.a / ALLOC
    return {
        "head": git(world.a, "rev-parse", "HEAD").strip(),
        "tree": porcelain(world.a),
        "items": sorted(p.relative_to(world.a).as_posix()
                        for p in (world.a / "research").rglob("*") if p.is_file()),
        "alloc": lock.read_bytes() if lock.exists() else None,
    }


@pytest.mark.parametrize("bad", list(BAD.values()), ids=list(BAD))
@pytest.mark.parametrize("name", list(HDD_CREATES))
def test_hdd_create_refuses_corrupt_local_file(world, monkeypatch, name, bad) -> None:
    hdd(world)
    drop_remote(world)
    seed_local(world, bad)
    before = hdd_state(world)
    result = invoke(world, monkeypatch, HDD_CREATES[name])
    out = output_of(result)
    assert result.exit_code != 0, f"not refused (exit 0) — handed out an id: {out}"
    assert isinstance(result.exception, SystemExit), (
        f"not a clean refusal: {result.exception!r}\n{out}"
    )
    assert "Traceback" not in out, out
    flat = " ".join(out.split())
    assert "_ID_ALLOCATIONS.json" in flat, f"the refusal does not name the file: {flat!r}"
    assert REFUSAL in flat, f"not #818's refusal: {flat!r}"
    assert hdd_state(world) == before, "something was created or committed"


@pytest.mark.parametrize("name", list(HDD_CREATES))
def test_control_hdd_create_missing_file(world, monkeypatch, name) -> None:
    hdd(world)
    drop_remote(world)
    assert not (world.a / ALLOC).exists()
    result = invoke(world, monkeypatch, HDD_CREATES[name])
    assert result.exit_code == 0, output_of(result)
