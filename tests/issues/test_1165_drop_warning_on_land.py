"""Issue #1165: the allocation drop warning says a rewrite happened, so it prints
only once the compare-and-swap push has landed: never for a command that created
nothing because every attempt lost the race, or because the remote refused it.
"""
from __future__ import annotations

import json
import logging

import pytest

from tests.issues.test_585_create_push_loop import output_of, scenario_exhausted
from tests.issues.test_603_push_failure_messages import reject_with_hook
from tests.issues.test_818_allocations_refuse_corrupt import origin_alloc_bytes, seed_origin
from tests.issues.test_1095_alloc_readers_refuse import MIXED
from tests.issues.test_1158_warn_once_hdd_pin import (  # noqa: F401  (fixtures)
    COMMANDS,
    _clean_theme_cache,
    drop_lines,
    world,
)


@pytest.mark.parametrize("cmd", list(COMMANDS))
def test_no_drop_warning_when_every_attempt_loses(world, monkeypatch, caplog, cmd) -> None:  # noqa: F811
    seed_origin(world, json.dumps(MIXED, indent=2))
    scenario_exhausted(world, monkeypatch)
    with caplog.at_level(logging.WARNING):
        result = COMMANDS[cmd](world, monkeypatch)
    assert result.exit_code != 0, output_of(result)
    assert drop_lines(result, caplog) == [], "warned of a rewrite that never landed"


@pytest.mark.parametrize("cmd", list(COMMANDS))
def test_no_drop_warning_when_the_remote_refuses(world, monkeypatch, caplog, cmd) -> None:  # noqa: F811
    seed_origin(world, json.dumps(MIXED, indent=2))
    before = origin_alloc_bytes(world)
    reject_with_hook(world)
    with caplog.at_level(logging.WARNING):
        result = COMMANDS[cmd](world, monkeypatch)
    assert result.exit_code != 0, output_of(result)
    assert origin_alloc_bytes(world) == before  # nothing was rewritten
    assert drop_lines(result, caplog) == [], "warned of a rewrite that never landed"
