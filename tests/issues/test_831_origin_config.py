"""Issue #831: ``claim`` and ``update --push`` judge by ORIGIN's config.

Since #574, ``claim`` and ``update --push`` read the items from origin's fetched tree.
But WIP limits, board paths and ignore patterns still come from the LOCAL
``.kanban/config.yaml``, so a limit changed on origin and not yet pulled is judged by
the old config.

Decided spec ([steer] on #831, bucket 2, the recommended default):

- Both commands read ``.kanban/config.yaml`` at the FETCHED rev, plus any theme files
  under ``.kanban/themes/`` that the config names. They judge WIP limits, board paths
  and ignore patterns by origin's config.
- If origin has no ``.kanban/config.yaml``, the local config is used.
- Gate checks that read other files still read the local working tree. The ``claim``
  help or docstring says so.

Acceptance with real git (a bare remote plus clones A and B, the #585 ``World``,
reusing #574's claim harness). A never pulls B's config change:

1. B lowers the WIP limit on origin to ``in_progress: 1`` while origin is full. A's
   local config has no such limit. A's claim is refused as WIP.
2. B raises or removes the limit on origin. A's local config still says 1 and is full,
   but origin isn't. A's claim wins.
3. B adds an ignore pattern on origin that hides the in-progress item that fills the
   WIP slot. A doesn't have the pattern. It isn't counted, so A's claim wins.
4. ``update --push --add-dep`` to an item under a board path that only origin's config
   names: the dependency resolves.
5. Origin has no ``.kanban/config.yaml`` but A has one: A's config is used (control).

Ambiguities resolved here (the test partner's reading; the driver may challenge):

a. The service is built with A's LOCAL config, as the CLI builds it
   (``KanbanConfig.load(A/.kanban/config.yaml)``). The switch to origin's config is
   the command's job, not the caller's.
b. The ignore pattern (acceptance 3) is pinned through the WIP count only. As the code
   stands, ``_items_at`` applies ignore patterns when it counts WIP, but
   ``_ids_at``/``_holders_at``, which find the claim's target at the fetched rev,
   apply none, local or origin. So whether an ignored item can be claimed is not
   pinned here.
c. Theme files: origin's ``.kanban/themes/nautical.yaml`` overrides the built-in
   theme that the config names (``theme: nautical``). With ``in_progress``
   ``wip_limit: 1`` and origin full, A's claim is refused.
d. The help text is pinned leniently. The ``claim`` command's own help (its
   docstring, without the option help) must say that gate checks read the local
   working tree, and must name the config.
e. Acceptance 2 is parametrised on "raised to 2" and "removed" (``wip_limits: null``,
   which explicitly disables limits on that board).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from tests.issues.test_574_claim import (
    ITEM_ID,
    OTHER,
    WIP_CONFIG,
    A,
    B,
    assert_claimed_by,
    claim,
    item_text,
    output_of,
)
from tests.issues.test_574_update_push import (
    ITEM as UPD_ITEM,
)
from tests.issues.test_574_update_push import (
    assert_one_item_commit,
    plain,
    remote_bytes,
    rich,
)
from tests.issues.test_576_cli_update import _assert_only_changed
from tests.issues.test_585_create_push_loop import EXP_DIR, World, git
from tests.issues.test_590_next_id_and_hdd_ids import b_push
from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import main
from yurtle_kanban.config import KanbanConfig, PathConfig

REPO = Path(__file__).resolve().parents[2]
CONFIG = ".kanban/config.yaml"
ITEM = f"{EXP_DIR}/EXP-001-x.md"

WIP_2_CONFIG = WIP_CONFIG.replace("in_progress: 1", "in_progress: 2")
NO_WIP_CONFIG = """\
version: "2.0"
boards:
  - name: development
    preset: nautical
    path: kanban-work/
    wip_limits: null
default_board: development
"""
PARKED_CONFIG = """\
version: "2.0"
boards:
  - name: development
    preset: nautical
    path: kanban-work/
    wip_limits:
      in_progress: 1
    ignore:
      - "**/archive/**"
      - "**/templates/**"
      - "**/parked/**"
default_board: development
"""
PARKED = f"{EXP_DIR}/parked/EXP-002-y.md"
EXTRA_DIR = "kanban-extra/expeditions"
EXTRA_ITEM = f"{EXTRA_DIR}/EXP-009-nine.md"


# --- harness ---------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    monkeypatch.delenv("YURTLE_AGENT", raising=False)
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


def push_from_a(world: World, files: dict[str, str], message: str) -> None:
    """Commit `files` on A's main and push them; B follows origin."""
    for rel, text in files.items():
        path = world.a / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    git(world.a, "add", "-A")
    git(world.a, "commit", "-m", message)
    git(world.a, "push", "origin", f"HEAD:refs/heads/{world.default}")
    git(world.b, "fetch", "origin")
    git(world.b, "reset", "--hard", f"origin/{world.default}")


def b_remove(world: World, rels: list[str], files: dict[str, str] | None = None) -> None:
    """B deletes `rels` (and writes `files`) on a fresh origin/main and pushes."""
    git(world.b, "fetch", "origin")
    git(world.b, "reset", "--hard", f"origin/{world.default}")
    for rel, text in (files or {}).items():
        path = world.b / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    git(world.b, "rm", "-q", *rels)
    git(world.b, "add", "-A")
    git(world.b, "commit", "-m", "rival: remove")
    git(world.b, "push", "origin", f"HEAD:refs/heads/{world.default}")


def local_config(world: World) -> str:
    return (world.a / CONFIG).read_text()


def origin_config(world: World) -> str:
    return world.remote_show(CONFIG)


def b_in_progress(item_id: str = "EXP-002", title: str = "Y") -> str:
    return item_text("in_progress", B, item_id, title)


def invoke(world: World, monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> Any:
    monkeypatch.chdir(world.a)
    return CliRunner().invoke(main, argv)


@pytest.fixture
def world(tmp_path: Path) -> World:
    """Origin and both clones hold EXP-001 at `ready`, unassigned, under World's
    single-board nautical config (the theme's in_progress limit is 10)."""
    w = World(tmp_path)
    push_from_a(w, {ITEM: item_text("ready")}, "seed EXP-001")
    return w


# --- 1: a limit lowered on origin binds A ---------------------------------------------


def test_wip_lowered_on_origin_refuses_claim(world) -> None:
    b_push(world, {CONFIG: WIP_CONFIG, OTHER: b_in_progress()})
    assert "in_progress: 1" not in local_config(world), "A must not have the new limit"
    assert not (world.a / OTHER).exists()
    base = world.remote_sha()

    out = claim(world.a, A)

    assert out.kind == "refused", (
        f"origin's WIP limit (1, full) must refuse the claim: {out.kind}: {out.message}"
    )
    assert out.exit_code == 1
    assert "wip" in out.message.lower(), out.message
    assert world.remote_sha() == base


def test_cli_wip_lowered_on_origin_refuses_claim(world, monkeypatch) -> None:
    b_push(world, {CONFIG: WIP_CONFIG, OTHER: b_in_progress()})
    base = world.remote_sha()

    result = invoke(world, monkeypatch, ["claim", ITEM_ID, "--agent", A])

    out = output_of(result)
    assert result.exit_code == 1, out
    assert "wip" in out.lower(), out
    assert world.remote_sha() == base


# --- 2: a limit raised or removed on origin frees A --------------------------------------


@pytest.mark.parametrize("origin_cfg", [WIP_2_CONFIG, NO_WIP_CONFIG], ids=["raised", "removed"])
def test_wip_raised_on_origin_lets_claim_win(world, origin_cfg) -> None:
    push_from_a(world, {CONFIG: WIP_CONFIG}, "board: wip 1")
    b_push(world, {OTHER: b_in_progress()})
    b_push(world, {CONFIG: origin_cfg})
    (world.a / OTHER).write_text(b_in_progress())  # full locally too (untracked)
    assert "in_progress: 1" in local_config(world), "A must still have limit 1"
    base = world.remote_sha()

    out = claim(world.a, A)

    assert out.kind == "won", (
        f"origin's config lifts the limit; the local limit 1 must not refuse: "
        f"{out.kind}: {out.message}"
    )
    assert_claimed_by(world, A, base)


# --- 3: an ignore pattern added on origin hides an item from the WIP count ----------------


def test_ignore_added_on_origin_hides_item_from_wip(world) -> None:
    push_from_a(world, {CONFIG: WIP_CONFIG}, "board: wip 1")
    b_push(world, {CONFIG: PARKED_CONFIG, PARKED: b_in_progress()})
    assert "parked" not in local_config(world), "A must not have the pattern"
    assert "parked" in origin_config(world)
    base = world.remote_sha()

    out = claim(world.a, A)

    assert out.kind == "won", (
        f"origin ignores {PARKED}, so the WIP slot is free: {out.kind}: {out.message}"
    )
    assert_claimed_by(world, A, base)


# --- 4: a board path only origin's config names ---------------------------------------------


def _extra_config() -> KanbanConfig:
    return KanbanConfig(
        theme="nautical",
        paths=PathConfig(
            root="kanban-work/",
            scan_paths=["kanban-work/expeditions/", "kanban-work/signals/", "kanban-extra/"],
        ),
    )


def _yaml_of(config: KanbanConfig, tmp_path: Path) -> str:
    path = tmp_path / "extra-config.yaml"
    config.save(path)
    return path.read_text()


@pytest.fixture
def upd_world(world: World) -> World:
    """EXP-001 as #574 PR D's rich item, plus EXP-002 and EXP-003."""
    push_from_a(
        world,
        {
            UPD_ITEM: rich(),
            f"{EXP_DIR}/EXP-002-two.md": plain("EXP-002", "Two"),
            f"{EXP_DIR}/EXP-003-three.md": plain("EXP-003", "Three"),
        },
        "seed EXP-001..003 (rich)",
    )
    return world


def test_update_push_add_dep_on_board_path_only_in_origin_config(
    upd_world, monkeypatch, tmp_path
) -> None:
    world = upd_world
    b_push(world, {
        CONFIG: _yaml_of(_extra_config(), tmp_path),
        EXTRA_ITEM: plain("EXP-009", "Nine"),
    })
    assert "kanban-extra" not in local_config(world), "A must not know the path"
    assert "kanban-extra" in origin_config(world)
    base = world.remote_sha()
    old = remote_bytes(world, UPD_ITEM)

    result = invoke(world, monkeypatch, ["update", ITEM_ID, "--add-dep", "EXP-009", "--push"])

    out = output_of(result)
    assert result.exit_code == 0, (
        f"EXP-009 is on a board path origin's config names: {out}"
    )
    assert_one_item_commit(world, base)
    _assert_only_changed(old, remote_bytes(world, UPD_ITEM), {"depends_on": ["EXP-009"]})


# --- theme files the config names --------------------------------------------------------------


def test_theme_override_on_origin_sets_the_wip_limit(world) -> None:
    theme = (REPO / "themes" / "nautical.yaml").read_text()
    tight, n = re.subn(
        r"(in_progress:\n(?:    .*\n)*?    wip_limit: )10", r"\g<1>1", theme
    )
    assert n == 1, "could not find nautical's in_progress wip_limit to tighten"
    b_push(world, {".kanban/themes/nautical.yaml": tight, OTHER: b_in_progress()})
    assert not (world.a / ".kanban" / "themes" / "nautical.yaml").exists()
    base = world.remote_sha()

    out = claim(world.a, A)

    assert out.kind == "refused", (
        f"origin's .kanban/themes/nautical.yaml limits in_progress to 1, and origin is "
        f"full: {out.kind}: {out.message}"
    )
    assert "wip" in out.message.lower(), out.message
    assert world.remote_sha() == base


# --- 5: origin has no config, so A's is used (control) ---------------------------------------


def test_no_config_on_origin_falls_back_to_local(world) -> None:
    push_from_a(world, {CONFIG: WIP_CONFIG}, "board: wip 1")
    b_remove(world, [CONFIG], {OTHER: b_in_progress()})
    assert CONFIG not in world.remote_files(), "origin must have no config"
    assert "in_progress: 1" in local_config(world)
    base = world.remote_sha()

    out = claim(world.a, A)

    assert out.kind == "refused", (
        f"no config on origin: A's limit 1 applies, and origin is full: "
        f"{out.kind}: {out.message}"
    )
    assert "wip" in out.message.lower(), out.message
    assert world.remote_sha() == base


def test_no_config_on_origin_local_config_lets_claim_win(world) -> None:
    b_remove(world, [CONFIG])
    assert CONFIG not in world.remote_files()
    # off the default branch, so the won claim can't fast-forward A's checkout to
    # origin's config-less tree (the assertions load A's config)
    git(world.a, "checkout", "-q", "-b", "feat-831")
    base = world.remote_sha()

    out = claim(world.a, A)

    assert out.kind == "won", out.message
    assert_claimed_by(world, A, base)


# --- controls: the same config on both sides, behaviour unchanged ----------------------------


def test_same_config_full_on_origin_still_refused(world) -> None:
    push_from_a(world, {CONFIG: WIP_CONFIG}, "board: wip 1")
    b_push(world, {OTHER: b_in_progress()})
    assert origin_config(world) == local_config(world)
    base = world.remote_sha()

    out = claim(world.a, A)

    assert out.kind == "refused", out.message
    assert "wip" in out.message.lower(), out.message
    assert world.remote_sha() == base


def test_same_config_not_full_still_wins(world) -> None:
    push_from_a(world, {CONFIG: WIP_CONFIG}, "board: wip 1")
    assert origin_config(world) == local_config(world)
    base = world.remote_sha()

    out = claim(world.a, A)

    assert out.kind == "won", out.message
    assert_claimed_by(world, A, base)


def test_same_config_add_dep_on_origin_item_still_resolves(upd_world, monkeypatch) -> None:
    world = upd_world
    b_push(world, {f"{EXP_DIR}/EXP-004-four.md": plain("EXP-004", "Four")})
    assert origin_config(world) == local_config(world)
    base = world.remote_sha()

    result = invoke(world, monkeypatch, ["update", ITEM_ID, "--add-dep", "EXP-004", "--push"])

    assert result.exit_code == 0, output_of(result)
    assert_one_item_commit(world, base)


# --- gates are origin's too (#1260 retired the "gate reads stay local" carve-out) ---------


def test_claim_help_says_gates_are_origins() -> None:
    # ruled edit (#1260): this pinned "gate checks read the local working tree"; #1260
    # judges claim's gates by origin's config, as move --push does (#1257)
    doc = " ".join((main.commands["claim"].help or "").split()).lower()
    assert "config" in doc, f"claim's help doesn't mention the config: {doc!r}"
    assert "gates" in doc and "#1260" in doc, f"claim's help doesn't name gates: {doc!r}"
    assert "local working tree" not in doc, f"claim's help keeps the old carve-out: {doc!r}"
