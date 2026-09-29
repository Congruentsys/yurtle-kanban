"""Issue #866: running from another repo lets its ``.kanban/themes`` override
origin's built-in theme.

Since #831/#865, ``claim`` and ``update --push`` judge the fetched tree by ORIGIN's
config (``KanbanService._judge_at``), reading origin's ``.kanban/themes/`` from the
fetched rev. When origin holds no override, the lookup falls through to the theme
dirs — and ``_theme_dirs`` puts ``Path.cwd() / ".kanban" / "themes"`` ahead of the
built-ins. So when the process cwd is inside a DIFFERENT repo whose
``.kanban/themes/nautical.yaml`` overrides the built-in, that repo's theme judges
origin's board.

Expected (issue body): origin config reads resolve themes relative to the board being
judged, not the cwd repo.

Harness: the #585 ``World`` (bare origin, clones A and B) with the service built from
A's LOCAL config, rooted at A (``KanbanService(config, A)``), as #831's tests build it.
"Another repo" is a separate git repo ``other/`` with its own ``.kanban/config.yaml``
and a ``.kanban/themes/nautical.yaml`` whose ``in_progress`` ``wip_limit`` differs from
the built-in's 10. The CLI has no repo-selection option (``get_service`` roots at
``Path.cwd()``), so the service route is the only one where cwd != the judged repo.

Ambiguities resolved here (the test partner's reading; the driver may challenge):

a. Pinned through the claim's WIP outcome (the judge's theme sets the limit), in both
   directions: the other repo TIGHTENS the limit (origin not full by the built-in, the
   claim must win) and LOOSENS it (origin full at the built-in 10, the claim must be
   refused).
b. Each scenario runs with cwd = A (control, green today) and cwd = the other repo;
   the outcome must be the same and match origin's built-in theme.
c. Negative control: origin's OWN ``.kanban/themes/nautical.yaml`` (fetched from the
   rev) is honoured whatever the cwd, even when the other repo's override says
   otherwise.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from tests.issues.test_574_claim import (
    OTHER,
    A,
    B,
    assert_claimed_by,
    claim,
    item_text,
)
from tests.issues.test_585_create_push_loop import EXP_DIR, World, git
from tests.issues.test_590_next_id_and_hdd_ids import b_push
from yurtle_kanban import config as config_mod
from yurtle_kanban.config import KanbanConfig, PathConfig

REPO = Path(__file__).resolve().parents[2]
ITEM = f"{EXP_DIR}/EXP-001-x.md"
BUILTIN_LIMIT = 10


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    monkeypatch.delenv("YURTLE_AGENT", raising=False)
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


def nautical_with_limit(limit: int) -> str:
    theme = (REPO / "themes" / "nautical.yaml").read_text()
    text, n = re.subn(
        r"(in_progress:\n(?:    .*\n)*?    wip_limit: )10\b", rf"\g<1>{limit}", theme
    )
    assert n == 1, "could not find nautical's in_progress wip_limit"
    return text


def make_other_repo(root: Path, limit: int) -> Path:
    """A separate git repo whose `.kanban/themes/nautical.yaml` overrides the
    built-in with `in_progress` `wip_limit: limit`."""
    other = root / "other"
    other.mkdir()
    git(other, "init", "-q", "-b", "main")
    KanbanConfig(
        theme="nautical",
        paths=PathConfig(root="work/", scan_paths=["work/"]),
    ).save(other / ".kanban" / "config.yaml")
    themes = other / ".kanban" / "themes"
    themes.mkdir(parents=True, exist_ok=True)
    (themes / "nautical.yaml").write_text(nautical_with_limit(limit))
    return other


def in_progress_items(n: int) -> dict[str, str]:
    return {
        f"{EXP_DIR}/EXP-{i:03d}-w{i}.md": item_text("in_progress", B, f"EXP-{i:03d}", f"W{i}")
        for i in range(2, 2 + n)
    }


@pytest.fixture
def world(tmp_path: Path) -> World:
    """Origin and both clones hold EXP-001 at `ready`, unassigned, under World's
    single-board nautical config (built-in in_progress limit 10); no theme overrides
    anywhere in A or on origin."""
    w = World(tmp_path)
    (w.a / ITEM).write_text(item_text("ready"))
    git(w.a, "add", "-A")
    git(w.a, "commit", "-m", "seed EXP-001")
    git(w.a, "push", "origin", f"HEAD:refs/heads/{w.default}")
    git(w.b, "fetch", "origin")
    git(w.b, "reset", "--hard", f"origin/{w.default}")
    assert not (w.a / ".kanban" / "themes").exists()
    return w


def run_claim_from(where: str, world: World, other: Path, monkeypatch) -> Any:
    monkeypatch.chdir(world.a if where == "A" else other)
    return claim(world.a, A)


# --- the other repo tightens the limit: origin's built-in (10) must still judge -------------


@pytest.mark.parametrize("where", ["A", "other"])
def test_other_repos_tighter_theme_does_not_refuse_claim(
    world, tmp_path, monkeypatch, where
) -> None:
    other = make_other_repo(tmp_path, limit=1)
    b_push(world, {OTHER: item_text("in_progress", B, "EXP-002", "Y")})
    base = world.remote_sha()

    out = run_claim_from(where, world, other, monkeypatch)

    assert out.kind == "won", (
        f"cwd={where}: origin's built-in nautical allows {BUILTIN_LIMIT} in progress "
        f"(1 used); the cwd repo's .kanban/themes/nautical.yaml (limit 1) must not "
        f"judge origin's board: {out.kind}: {out.message}"
    )
    assert_claimed_by(world, A, base)


# --- the other repo loosens the limit: origin full at the built-in 10 must refuse ----------


@pytest.mark.parametrize("where", ["A", "other"])
def test_other_repos_looser_theme_does_not_lift_origins_limit(
    world, tmp_path, monkeypatch, where
) -> None:
    other = make_other_repo(tmp_path, limit=50)
    b_push(world, in_progress_items(BUILTIN_LIMIT))
    base = world.remote_sha()

    out = run_claim_from(where, world, other, monkeypatch)

    assert out.kind == "refused", (
        f"cwd={where}: origin is full at the built-in nautical limit {BUILTIN_LIMIT}; "
        f"the cwd repo's override (limit 50) must not lift it: {out.kind}: {out.message}"
    )
    assert "wip" in out.message.lower(), out.message
    assert world.remote_sha() == base


# --- negative control: origin's OWN theme override is honoured from any cwd -----------------


@pytest.mark.parametrize("where", ["A", "other"])
def test_origins_own_theme_override_is_honoured_from_any_cwd(
    world, tmp_path, monkeypatch, where
) -> None:
    other = make_other_repo(tmp_path, limit=50)
    b_push(world, {
        ".kanban/themes/nautical.yaml": nautical_with_limit(1),
        OTHER: item_text("in_progress", B, "EXP-002", "Y"),
    })
    assert not (world.a / ".kanban" / "themes" / "nautical.yaml").exists()
    base = world.remote_sha()

    out = run_claim_from(where, world, other, monkeypatch)

    assert out.kind == "refused", (
        f"cwd={where}: origin's own .kanban/themes/nautical.yaml limits in_progress to "
        f"1 and origin is full: {out.kind}: {out.message}"
    )
    assert "wip" in out.message.lower(), out.message
    assert world.remote_sha() == base


# --- #967 part 2: the SERVICE repo's working-tree override never judges origin either ------
#
# The judge drops both the cwd's and the service repo's (A's) working-tree
# `.kanban/themes`. With cwd = A the cwd half alone drops A's, so only cwd = the
# other repo pins the repo_root half: A holds an UNCOMMITTED override that loosens
# the limit to 50 while origin is full at the built-in 10.


@pytest.mark.parametrize("where", ["A", "other"])
def test_service_repos_uncommitted_theme_does_not_lift_origins_limit(
    world, tmp_path, monkeypatch, where
) -> None:
    other = make_other_repo(tmp_path, limit=BUILTIN_LIMIT)
    (other / ".kanban" / "themes" / "nautical.yaml").unlink()  # no override in the cwd repo
    a_themes = world.a / ".kanban" / "themes"
    a_themes.mkdir(parents=True)
    (a_themes / "nautical.yaml").write_text(nautical_with_limit(50))
    assert "nautical.yaml" in git(world.a, "status", "--porcelain", "--untracked-files=all")
    b_push(world, in_progress_items(BUILTIN_LIMIT))
    base = world.remote_sha()

    out = run_claim_from(where, world, other, monkeypatch)

    assert out.kind == "refused", (
        f"cwd={where}: origin is full at the built-in nautical limit {BUILTIN_LIMIT}; "
        f"A's uncommitted .kanban/themes/nautical.yaml (limit 50) must not lift it: "
        f"{out.kind}: {out.message}"
    )
    assert "wip" in out.message.lower(), out.message
    assert world.remote_sha() == base
