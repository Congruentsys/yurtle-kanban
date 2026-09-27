"""Issue #634 — `create --push` checks explicit ids and paper-scoped `H<paper>.n` ids
against the fetched base.

Follow-up from #590. The [steer] says G1 (no duplicate ids on origin) outranks
convenience. Reuses the #585/#590 real-git harness (a bare remote, clone A under test,
rival clone B). Interleavings are deterministic: B pushes either before A runs
("stale": A's checkout lacks the rival) or just before A's first push ("race": A's
compare-and-swap push loses and A refetches).

Decided behaviour:

(a) An explicit id that the fetched base already holds is refused. The command fails,
    the message names the rival's file, nothing is pushed, and A's feature branch,
    index and tree are untouched. This is checked through the service API
    (``create_item_and_push(item_id=...)``, the path every ``--id`` flag uses; the core
    ``create`` command has no ``--id`` option) and through the CLI with
    ``measure create --id M-001 --push``.
(b) An explicit id that the base does not hold still lands (control).
(c) A rival pushed ``H130.1``. A local ``hypothesis create --paper 130 --push`` lands as
    ``H130.2``, never ``H130.1``, because the paper-scoped number is recomputed on each
    fetched base, including after a lost race.
(d) ``hypothesis create --id H130.1 --push``, when the base holds H130.1, is refused
    like (a).
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Iterator

import pytest

from tests.issues.test_585_create_push_loop import (
    FEATURE,
    PushHook,
    World,
    git,
    output_of,
    porcelain,
)
from tests.issues.test_590_next_id_and_hdd_ids import b_push, frontmatter_id
from tests.issues.test_603_push_failure_messages import HDD_DIRS, invoke, reconfigure
from yurtle_kanban import config as config_mod
from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.models import WorkItemType
from yurtle_kanban.service import KanbanService

TITLE_A = "Alpha From A"
TIMINGS = ["stale", "race"]


@pytest.fixture
def world(tmp_path, monkeypatch: pytest.MonkeyPatch) -> World:
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    return World(tmp_path)


@pytest.fixture(autouse=True)
def _clean_theme_cache() -> Iterator[None]:
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


def on_timing(world: World, monkeypatch, timing: str, rival) -> PushHook | None:
    """Run `rival` now ("stale") or just before A's first push ("race")."""
    if timing == "stale":
        rival()
        return None
    hook = PushHook(lambda n: rival() if n == 1 else None)
    monkeypatch.setattr(subprocess, "run", hook)
    return hook


def to_feature_branch(world: World) -> tuple[str, str]:
    git(world.a, "checkout", "-b", FEATURE)
    (world.a / "feature.txt").write_text("feature work\n")
    git(world.a, "add", "feature.txt")
    git(world.a, "commit", "-m", "feature work")
    git(world.a, "push", "-u", "origin", FEATURE)
    return git(world.a, "rev-parse", FEATURE).strip(), world.remote_sha(FEATURE)


def assert_untouched(world: World, feat: tuple[str, str]) -> None:
    """Nothing of A's reached origin: its tip is still the rival's commit."""
    tip = git(world.remote, "log", "-1", "--format=%s", "main").strip()
    assert tip == "rival", f"something was pushed to origin/main after the rival: {tip}"
    assert git(world.a, "rev-parse", FEATURE).strip() == feat[0], "feature branch moved"
    assert world.remote_sha(FEATURE) == feat[1], "the feature branch was pushed"
    assert git(world.a, "rev-parse", "--abbrev-ref", "HEAD").strip() == FEATURE
    assert porcelain(world.a) == []


def service(world: World) -> KanbanService:
    return KanbanService(KanbanConfig.load(world.a / ".kanban" / "config.yaml"), world.a)


def hdd(world: World) -> None:
    reconfigure(world, "hdd", "research/", [f"research/{d}/" for d in HDD_DIRS])


def remote_ids(world: World, under: str) -> dict[str, str | None]:
    return {
        p: frontmatter_id(world.remote_show(p))
        for p in world.remote_files()
        if p.startswith(under) and p.endswith(".md")
    }


# --- (a) an explicit id the base holds is refused ------------------------------------------

EXP_RIVAL = "kanban-work/expeditions/EXP-003-rival.md"


def _exp_rival(world: World) -> None:
    b_push(
        world,
        {EXP_RIVAL: '---\nid: EXP-003\ntitle: "Rival"\ntype: expedition\nstatus: backlog\n---\n'},
        [{"id": "EXP-003", "prefix": "EXP", "number": 3}],
    )


@pytest.mark.parametrize("timing", TIMINGS)
def test_explicit_id_held_by_base_is_refused(world, monkeypatch, timing) -> None:
    feat = to_feature_branch(world)
    svc = service(world)
    on_timing(world, monkeypatch, timing, lambda: _exp_rival(world))
    result = svc.create_item_and_push(WorkItemType.EXPEDITION, TITLE_A, item_id="EXP-003")

    assert result["success"] is False, result
    assert "EXP-003-rival.md" in result["message"], result["message"]
    assert list(remote_ids(world, "kanban-work/expeditions/")) == [EXP_RIVAL]
    assert_untouched(world, feat)


@pytest.mark.parametrize("timing", TIMINGS)
def test_cli_explicit_measure_id_held_by_base_is_refused(world, monkeypatch, timing) -> None:
    hdd(world)
    feat = to_feature_branch(world)

    def rival() -> None:
        b_push(
            world,
            {"research/measures/M-001-rival.md": '---\nid: M-001\ntitle: "Rival"\n---\n'},
            [{"id": "M-001", "prefix": "M", "number": 1}],
        )

    on_timing(world, monkeypatch, timing, rival)
    result = invoke(world, monkeypatch, [
        "measure", "create", TITLE_A, "--unit", "count", "--category", "coverage",
        "--id", "M-001", "--push",
    ])
    out = " ".join(output_of(result).split())

    assert result.exit_code != 0, out
    assert "M-001-rival.md" in out, out
    assert list(remote_ids(world, "research/measures/")) == ["research/measures/M-001-rival.md"]
    assert_untouched(world, feat)


# --- (b) an explicit id free on the base lands ---------------------------------------------


def test_explicit_id_free_on_base_lands(world, monkeypatch) -> None:
    feat = to_feature_branch(world)
    _exp_rival(world)
    result = service(world).create_item_and_push(
        WorkItemType.EXPEDITION, TITLE_A, item_id="EXP-005"
    )

    assert result["success"] is True, result
    ids = sorted(i for i in remote_ids(world, "kanban-work/expeditions/").values() if i)
    assert ids == ["EXP-003", "EXP-005"], ids
    assert git(world.a, "rev-parse", FEATURE).strip() == feat[0]
    assert porcelain(world.a) == []


# --- (c) a paper-scoped id is recomputed on the fetched base ------------------------------


def _h_rival(world: World) -> None:
    b_push(world, {
        "research/hypotheses/H130.1-rival.md":
            '---\nid: H130.1\ntitle: "Rival"\ntype: hypothesis\npaper: PAPER-130\n---\n'
    })


@pytest.mark.parametrize("timing", TIMINGS)
def test_paper_scoped_id_recomputed_on_base(world, monkeypatch, timing) -> None:
    hdd(world)
    on_timing(world, monkeypatch, timing, lambda: _h_rival(world))
    result = invoke(world, monkeypatch, ["hypothesis", "create", TITLE_A, "--paper", "130", "--push"])
    out = output_of(result)

    assert result.exit_code == 0, out
    ids = remote_ids(world, "research/hypotheses/")
    assert sorted(i for i in ids.values() if i) == ["H130.1", "H130.2"], ids
    mine = [p for p, i in ids.items() if i == "H130.2"]
    assert mine and "rival" not in mine[0] and TITLE_A in world.remote_show(mine[0]), ids
    assert re.search(r"H130\.2\b", out), out
    assert porcelain(world.a) == []


# --- (d) an explicit paper-scoped id the base holds is refused ------------------------------


@pytest.mark.parametrize("timing", TIMINGS)
def test_explicit_paper_scoped_id_held_by_base_is_refused(world, monkeypatch, timing) -> None:
    hdd(world)
    feat = to_feature_branch(world)
    on_timing(world, monkeypatch, timing, lambda: _h_rival(world))
    result = invoke(world, monkeypatch, ["hypothesis", "create", TITLE_A, "--id", "H130.1", "--push"])
    out = " ".join(output_of(result).split())

    assert result.exit_code != 0, out
    assert "H130.1-rival.md" in out, out
    assert list(remote_ids(world, "research/hypotheses/")) == [
        "research/hypotheses/H130.1-rival.md"
    ]
    assert_untouched(world, feat)

