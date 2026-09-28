"""Issue #574, PR B: `claim` refuses a duplicated ID, as every writer does (#742, #754).

When the fetched tree has more than one file whose frontmatter `id:` is the item
(`_holders_at`), the claim is ambiguous: it is refused, and nothing is pushed. With
no remote, a locally duplicated ID is refused through `refuse_duplicate`. A file
that is only NAMED after the item (an outline with no `id:`) is no copy of it (#754).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.issues.test_574_claim import (
    ITEM,
    ITEM_ID,
    A,
    claim,
    frontmatter,
    item_text,
    push_from_a,
)
from tests.issues.test_574_sync_and_push import Recorder, commit_files, snapshot
from tests.issues.test_585_create_push_loop import EXP_DIR, World, git
from tests.issues.test_590_next_id_and_hdd_ids import b_push
from yurtle_kanban import config as config_mod


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """As test_574_claim's: no prompts, no $YURTLE_AGENT, a fresh theme cache."""
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


COPY = f"{EXP_DIR}/EXP-001-copy.md"
AMBIGUOUS = "a claim to it is ambiguous; fix the duplicate ID first"


def test_duplicate_on_origin_is_refused_without_a_push(world: World) -> None:
    b_push(world, {COPY: item_text("ready", title="Copy")})
    assert not (world.a / COPY).exists(), "A must not see the copy locally"
    base = world.remote_sha()
    before = snapshot(world.a)
    rec = Recorder()

    out = claim(world.a, A, rec)

    assert out.kind == "refused", out.message
    assert out.exit_code == 1
    assert f"{ITEM_ID} is on more than one board" in out.message, out.message
    assert AMBIGUOUS in out.message, out.message
    assert ITEM in out.message and COPY in out.message, out.message
    assert rec.seams == [], "a push was attempted"
    assert world.remote_sha() == base
    assert snapshot(world.a) == before


def test_duplicate_locally_without_remote_is_refused(world: World) -> None:
    git(world.a, "remote", "remove", "origin")
    (world.a / COPY).write_text(item_text("ready", title="Copy"))
    head = git(world.a, "rev-parse", "HEAD").strip()

    out = claim(world.a, A)

    assert out.kind == "refused", out.message
    assert out.exit_code == 1
    assert f"{ITEM_ID} is on more than one board" in out.message, out.message
    assert AMBIGUOUS in out.message, out.message
    assert git(world.a, "rev-parse", "HEAD").strip() == head
    assert frontmatter((world.a / ITEM).read_text()).get("assignee") is None


@pytest.mark.parametrize("outline", ["# Outline for EXP-001\n", "notes, no frontmatter\n"])
def test_file_only_named_after_the_item_is_no_duplicate(world: World, outline: str) -> None:
    b_push(world, {f"{EXP_DIR}/EXP-001-outline.md": outline})
    base = world.remote_sha()

    out = claim(world.a, A)

    assert out.kind == "won", out.message
    tip = world.remote_sha()
    assert git(world.remote, "rev-list", f"{base}..{tip}").split() == [tip]
    assert commit_files(world.remote, tip) == [ITEM]
    assert frontmatter(world.remote_show(ITEM))["assignee"] == A
