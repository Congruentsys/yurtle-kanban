"""Issue #814, round 2: a symlinked ``.md`` on a board is skipped, not "missing".

The PR #835 review found that ``_items_at`` takes its candidate names from
``ls-tree -r --name-only``, which also lists symlinks (mode 120000), while
``_blobs_at`` marks a tar member as seen only when ``member.isfile()``. So a tracked
symlink on a board lands in ``missing`` and the claim (or a dependency-editing
``update --push``) is refused with a misleading export-ignore message. main simply
skipped it. The fleet hits this: nusy-product-team tracks
``research/NuSy-Publication-Strategy/NAI-JOURNAL-SUBMISSION-GUIDE.md`` as a symlink.

Decided fix: only regular files (modes 100644/100755) count as board items on the
fetched tree; symlinks are skipped, as on main.

Ambiguities resolved here (the test partner's reading; the driver may challenge):

a. Symlinks are committed for real (``os.symlink`` in A, then ``git add -A``,
   commit and push), so origin holds mode-120000 entries.
b. "Skipped" is pinned by behaviour: a symlink to an in-progress item does not
   count toward WIP twice. With WIP limit 1 and one real in-progress item (B's
   EXP-002) plus a symlink to it, the refusal names WIP and never export-ignore,
   ``.gitattributes`` or "left out". Whether a symlink WOULD count once is not the
   point: the limit is already full by the real item.
c. The native in-progress label is read BEFORE the symlink is added, so A's local
   scan of a symlink never decides what the test expects.
d. The ``update --push --add-dep`` case uses the claim world plus a plain EXP-002
   on origin; it checks exit 0 and that origin's EXP-001 now depends on EXP-002.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import pytest

from tests.issues.test_574_claim import (
    ITEM,
    ITEM_ID,
    OTHER,
    WIP_CONFIG,
    A,
    B,
    claim,
    frontmatter,
    invoke,
    item_text,
    native_in_progress,
    nodes_by,
    output_of,
    push_from_a,
)
from tests.issues.test_574_sync_and_push import Recorder, commit_files, snapshot
from tests.issues.test_585_create_push_loop import EXP_DIR, World, git
from tests.issues.test_590_next_id_and_hdd_ids import b_push
from yurtle_kanban import config as config_mod

ALIAS = f"{EXP_DIR}/alias.md"
MISLEADING = ("export-ignore", ".gitattributes", "left out", "archive")


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    monkeypatch.delenv("YURTLE_AGENT", raising=False)
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


@pytest.fixture
def world(tmp_path: Path) -> World:
    """Origin and both clones hold EXP-001 at `ready`, unassigned, and a regular
    `docs/guide.md` outside the board; WIP limit 1 on in-progress."""
    w = World(tmp_path)
    push_from_a(
        w,
        {
            ITEM: item_text("ready"),
            "docs/guide.md": "# Guide\n\nNot a work item.\n",
            ".kanban/config.yaml": WIP_CONFIG,
        },
        "seed EXP-001, docs, wip 1",
    )
    return w


def push_symlink(world: World, rel: str, target: str) -> None:
    """Commit a symlink `rel -> target` on A's main and push it (mode 120000)."""
    link = world.a / rel
    link.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(target, link)
    push_from_a(world, {}, f"symlink {rel}")
    mode = git(world.remote, "ls-tree", world.default, "--", rel).split()[0]
    assert mode == "120000", f"{rel} is not a symlink on origin: mode {mode}"


def wip_full_on_origin(world: World) -> None:
    """B's EXP-002 is in progress on origin only (A never pulls it)."""
    b_push(world, {OTHER: item_text("in_progress", B, "EXP-002", "Y")})


def assert_won(world: World, out: Any, base: str, label: str) -> None:
    assert out.kind == "won", f"{out.kind}: {out.message}"
    assert out.exit_code == 0
    tip = world.remote_sha()
    assert git(world.remote, "rev-list", f"{base}..{tip}").split() == [tip]
    assert commit_files(world.remote, tip) == [ITEM]
    text = world.remote_show(ITEM)
    fm = frontmatter(text)
    assert fm["assignee"] == A
    assert fm["status"] == label
    assert any(re.search(r"kb:status\s+kb:in_progress\b", n) for n in nodes_by(text, A)), text


# --- a symlinked .md on the board ------------------------------------------------------------


@pytest.mark.parametrize(
    "target",
    ["EXP-001-x.md", "../../docs/guide.md", "OUTSIDE_REPO"],
    ids=["inside-board", "outside-board", "outside-repo"],
)
def test_symlink_on_board_wip_not_full_claim_wins(world, tmp_path, target) -> None:
    label = native_in_progress(world.a)
    if target == "OUTSIDE_REPO":
        outside = tmp_path / "outside-repo.md"
        outside.write_text(item_text("in_progress", B, "EXP-009", "Outside"))
        target = str(outside)
    push_symlink(world, ALIAS, target)
    base = world.remote_sha()

    out = claim(world.a, A)

    assert_won(world, out, base, label)


def test_symlink_to_in_progress_item_wip_full_refused_as_wip(world) -> None:
    push_from_a(world, {OTHER: item_text("in_progress", B, "EXP-002", "Y")}, "EXP-002 held")
    push_symlink(world, ALIAS, "EXP-002-y.md")
    base = world.remote_sha()
    before = snapshot(world.a)
    rec = Recorder()

    out = claim(world.a, A, rec)

    assert out.kind == "refused", f"{out.kind}: {out.message}"
    assert out.exit_code == 1
    msg = out.message.lower()
    assert "wip" in msg, out.message
    assert not any(m in msg for m in MISLEADING), f"misleading cause: {out.message}"
    assert world.remote_sha() == base
    assert rec.seams == []
    assert snapshot(world.a) == before


def test_cli_symlink_on_board_claim_wins(world, monkeypatch) -> None:
    push_symlink(world, ALIAS, "../../docs/guide.md")

    result = invoke(world, monkeypatch, ["claim", ITEM_ID, "--agent", A])

    assert result.exit_code == 0, output_of(result)
    assert frontmatter(world.remote_show(ITEM))["assignee"] == A


# --- update --push --add-dep on a board with a symlink ---------------------------------------


@pytest.mark.parametrize(
    "target", ["EXP-001-x.md", "../../docs/guide.md"], ids=["inside-board", "outside-board"]
)
def test_update_push_add_dep_with_symlink_on_board(world, monkeypatch, target) -> None:
    push_from_a(world, {OTHER: item_text("ready", None, "EXP-002", "Y")}, "seed EXP-002")
    push_symlink(world, ALIAS, target)
    base = world.remote_sha()

    result = invoke(world, monkeypatch, ["update", ITEM_ID, "--add-dep", "EXP-002", "--push"])

    out = output_of(result)
    assert result.exit_code == 0, out
    assert not any(m in out.lower() for m in MISLEADING), out
    tip = world.remote_sha()
    assert git(world.remote, "rev-list", f"{base}..{tip}").split() == [tip]
    assert commit_files(world.remote, tip) == [ITEM]
    assert frontmatter(world.remote_show(ITEM)).get("depends_on") == ["EXP-002"]


# --- controls ----------------------------------------------------------------------------------


def test_control_real_export_ignore_still_refused(world) -> None:
    push_from_a(world, {".gitattributes": f"{OTHER} export-ignore\n"}, "attributes")
    wip_full_on_origin(world)
    base = world.remote_sha()
    before = snapshot(world.a)
    rec = Recorder()

    out = claim(world.a, A, rec)

    assert out.kind == "refused", f"{out.kind}: {out.message}"
    assert out.exit_code == 1
    assert any(m in out.message.lower() for m in MISLEADING[:3] + ("archive",)), out.message
    assert world.remote_sha() == base
    assert rec.seams == []
    assert snapshot(world.a) == before


def test_control_real_export_ignore_refuses_update_push_add_dep(world, monkeypatch) -> None:
    push_from_a(
        world,
        {
            OTHER: item_text("ready", None, "EXP-002", "Y"),
            ".gitattributes": "kanban-work/expeditions/EXP-002-y.md export-ignore\n",
        },
        "seed EXP-002, attributes",
    )
    base = world.remote_sha()

    result = invoke(world, monkeypatch, ["update", ITEM_ID, "--add-dep", "EXP-002", "--push"])

    assert result.exit_code == 1, output_of(result)
    assert world.remote_sha() == base


def test_control_normal_claim_wins(world) -> None:
    label = native_in_progress(world.a)
    base = world.remote_sha()

    out = claim(world.a, A)

    assert_won(world, out, base, label)


def test_control_wip_full_without_symlink_refused_as_wip(world) -> None:
    wip_full_on_origin(world)
    base = world.remote_sha()

    out = claim(world.a, A)

    assert out.kind == "refused", f"{out.kind}: {out.message}"
    assert "wip" in out.message.lower(), out.message
    assert world.remote_sha() == base
