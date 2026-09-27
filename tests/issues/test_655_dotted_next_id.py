"""Issue #655 — `next-id` with a remote in a dotted id space (`H130.`).

With a remote, `allocate_next_id` claims the id by compare-and-swap on the default
branch; its `landed` callback read the number with `rsplit("-", 1)`, which breaks
for a dotted id such as `H130.2`. The number must come from `_id_space` (#641).

Reuses the #585/#590/#641 real-git harness: a bare remote, clone A under test, rival
clone B (``b_push``).

(a) `allocate_next_id("H130.")` and the CLI `next-id H130.` succeed with H130.1 /
    number 1 on an empty space, and H130.5 / number 5 once a rival pushed H130.4.
(b) Control: the dashed `EXP` still gives EXP-001 / number 1.
(c) The allocation record on origin/main has `id: H130.N` and `prefix: H130.`.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Any

import pytest

from tests.issues.test_585_create_push_loop import World, output_of
from tests.issues.test_590_next_id_and_hdd_ids import b_push
from tests.issues.test_603_push_failure_messages import invoke
from tests.issues.test_641_id_allocation import record_for
from tests.test_634_explicit_ids_on_base import service
from yurtle_kanban import config as config_mod

H4 = [{"id": "H130.4", "prefix": "H130.", "number": 4}]


@pytest.fixture
def world(tmp_path, monkeypatch: pytest.MonkeyPatch) -> World:
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    return World(tmp_path)


@pytest.fixture(autouse=True)
def _clean_theme_cache() -> Iterator[None]:
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


def cli_result(out: str) -> dict[str, Any]:
    """The JSON object `next-id --json` printed."""
    m = re.search(r"\{.*\}", out, re.S)
    assert m, f"no JSON in the output: {out!r}"
    return json.loads(m.group(0))


# --- (a) the service -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rival", "want_id", "want_num"),
    [(None, "H130.1", 1), (H4, "H130.5", 5)],
    ids=["empty", "rival_h130_4"],
)
def test_service_dotted_next_id_with_remote(world, rival, want_id, want_num) -> None:
    if rival:
        b_push(world, {}, rival)
    result = service(world).allocate_next_id("H130.")

    assert result["success"] is True, result
    assert result["id"] == want_id, result
    assert result["number"] == want_num, result
    assert result["prefix"] == "H130.", result


# --- (a) the CLI -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rival", "want_id", "want_num"),
    [(None, "H130.1", 1), (H4, "H130.5", 5)],
    ids=["empty", "rival_h130_4"],
)
def test_cli_dotted_next_id_with_remote(world, monkeypatch, rival, want_id, want_num) -> None:
    if rival:
        b_push(world, {}, rival)
    result = invoke(world, monkeypatch, ["next-id", "H130.", "--json"])
    out = output_of(result)

    assert result.exit_code == 0, f"{out!r} {result.exception!r}"
    made = cli_result(out)
    assert made["success"] is True, made
    assert made["id"] == want_id, made
    assert made["number"] == want_num, made


def test_cli_dotted_next_id_plain_output(world, monkeypatch) -> None:
    result = invoke(world, monkeypatch, ["next-id", "H130."])
    out = output_of(result)

    assert result.exit_code == 0, f"{out!r} {result.exception!r}"
    assert "Allocated: H130.1" in out, out
    assert "Number: 1" in out, out


# --- (b) dashed control ----------------------------------------------------------------------


def test_service_dashed_next_id_control(world) -> None:
    result = service(world).allocate_next_id("EXP")

    assert result["success"] is True, result
    assert result["id"] == "EXP-001", result
    assert result["number"] == 1, result


def test_cli_dashed_next_id_control(world, monkeypatch) -> None:
    result = invoke(world, monkeypatch, ["next-id", "EXP", "--json"])
    out = output_of(result)

    assert result.exit_code == 0, out
    made = cli_result(out)
    assert (made["id"], made["number"]) == ("EXP-001", 1), made


# --- (c) the record on origin/main -----------------------------------------------------------


@pytest.mark.parametrize(
    ("rival", "want_id", "want_num"),
    [(None, "H130.1", 1), (H4, "H130.5", 5)],
    ids=["empty", "rival_h130_4"],
)
def test_dotted_allocation_record_on_origin(world, rival, want_id, want_num) -> None:
    if rival:
        b_push(world, {}, rival)
    try:  # the record is checked on origin whatever the call returned or raised
        service(world).allocate_next_id("H130.")
    except Exception:
        pass
    record = record_for(world, want_id)
    assert record["id"] == want_id, record
    assert record["prefix"] == "H130.", record
    assert record["number"] == want_num, record
