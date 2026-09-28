"""Fixtures shared by tests/issues modules (#892).

`world` is the #574 claim tests' world: origin and both clones hold EXP-001 at
`ready`, unassigned. A module that defines its own `world` still gets its own
(a module-level fixture overrides this one).

`claim_env` is the #574 claim tests' clean environment. It is NOT autouse here:
a module that wants it asks for it with
`pytestmark = pytest.mark.usefixtures("claim_env")`.

Only plain helpers are imported below: a fixture imported into a conftest is
registered for every module under it.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.issues.test_574_claim import ITEM, item_text, push_from_a
from tests.issues.test_585_create_push_loop import World
from yurtle_kanban import config as config_mod


@pytest.fixture
def claim_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """No prompting git, no inherited `YURTLE_AGENT`, an empty theme cache."""
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    monkeypatch.delenv("YURTLE_AGENT", raising=False)
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


@pytest.fixture
def world(tmp_path: Path) -> World:
    """Origin and both clones hold EXP-001 at `ready`, unassigned."""
    w = World(tmp_path)
    push_from_a(w, {ITEM: item_text("ready")}, "seed EXP-001")
    return w
