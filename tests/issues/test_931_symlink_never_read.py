# ruff: noqa: F811  (the borrowed `world` fixture)
"""Issue #931: the symlink half of `_items_at`'s mode filter. A committed symlink
`.md` on the board (mode 120000) must never reach `_blobs_at`: read as a blob it
would be its target PATH, which the parse happens to drop today, so only a spy on
`_blobs_at`'s input pins the filter itself (test_880's control can't tell)."""

from __future__ import annotations

import os
from typing import Any

import pytest

from tests.issues.test_585_create_push_loop import EXP_DIR
from tests.issues.test_880_blobs_at_hardening import (  # noqa: F401 (fixtures)
    B,
    _env,
    item_text,
    push_from_a,
    service,
    world,
)
from yurtle_kanban.service import KanbanService


def test_a_symlinked_md_never_reaches_blobs_at(world, monkeypatch: pytest.MonkeyPatch) -> None:
    target = world.a / "notes" / "EXP-009.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(item_text("in_progress", B, "EXP-009", "Linked"))
    link = world.a / EXP_DIR / "EXP-009-link.md"
    os.symlink(os.path.relpath(target, link.parent), link)
    push_from_a(world, {}, "a symlinked item")
    asked: list[str] = []
    real = KanbanService._blobs_at

    def spy(self: KanbanService, rev: str, oids: dict[str, str]) -> Any:
        asked.extend(oids)
        return real(self, rev, oids)

    monkeypatch.setattr(KanbanService, "_blobs_at", spy)
    service(world.a)._items_at("HEAD", None)

    assert asked, "_blobs_at was never asked for anything"
    assert f"{EXP_DIR}/EXP-009-link.md" not in asked, asked
