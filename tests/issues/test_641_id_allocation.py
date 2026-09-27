"""Issue #641 — id-allocation clean-ups after #634 (the [steer] decisions are the spec).

Reuses the #585/#590/#634 real-git harness: a bare remote, clone A under test, rival
clone B (``b_push``). Interleavings are deterministic: B pushes before A runs
("stale") or just before A's first push ("race").

1. ``--no-sync`` floor: once ``next-id EXP`` has landed EXP-001 on origin/main from a
   feature branch, ``next-id EXP --no-sync`` and a plain local ``create`` give EXP-002,
   reading the already-fetched ``refs/remotes/origin/main`` with no network (the
   remote is unreachable, and no fetch/ls-remote/pull/push is issued).
2. An allocation record's ``prefix`` is the id space, the id minus its number
   (``IDEA-R``, ``H130.``), for explicit and auto ids alike; records are counted by
   their id head only.
3. One allocator: local ``hypothesis create --paper 130`` sees an H130.N that exists
   only as a filename or an allocation record.
4. ``create_item`` no longer takes ``render=`` / ``id_prefix=`` (nothing in src calls
   them).
5. The base scan counts only frontmatter ``id:`` lines, not body or code-block lines.
6. Every per-base render in the compare-and-swap path goes through ``_check_text``: a
   render that is not valid UTF-8 is refused and nothing is pushed.
7. Explicit-id collision compares (id space, integer number): ``--id EXP-3`` is
   refused against EXP-003, ``measure create --id M-1`` against M-001.
"""

from __future__ import annotations

import inspect
import json
import re
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.issues.test_585_create_push_loop import (
    EXP_DIR,
    FEATURE,
    World,
    git,
    output_of,
)
from tests.issues.test_590_next_id_and_hdd_ids import ALLOC, allocated, b_push
from tests.issues.test_603_push_failure_messages import HDD_DIRS, invoke, reconfigure
from tests.test_634_explicit_ids_on_base import (
    TIMINGS,
    assert_untouched,
    on_timing,
    remote_ids,
    service,
    to_feature_branch,
)
from yurtle_kanban import config as config_mod
from yurtle_kanban.models import WorkItemType
from yurtle_kanban.service import KanbanService

TITLE_A = "Alpha From A"
_ORIG_RUN = subprocess.run


@pytest.fixture
def world(tmp_path, monkeypatch: pytest.MonkeyPatch) -> World:
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    return World(tmp_path)


@pytest.fixture(autouse=True)
def _clean_theme_cache() -> Iterator[None]:
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


def hdd(world: World) -> None:
    reconfigure(world, "hdd", "research/", [f"research/{d}/" for d in HDD_DIRS])


def remote_records(world: World) -> list[dict[str, Any]]:
    shown = _ORIG_RUN(
        ["git", "show", f"main:{ALLOC}"], cwd=world.remote, capture_output=True, text=True
    )
    return json.loads(shown.stdout) if shown.returncode == 0 else []


def record_for(world: World, item_id: str) -> dict[str, Any]:
    found = [r for r in remote_records(world) if r.get("id") == item_id]
    assert len(found) == 1, f"expected one record for {item_id}: {remote_records(world)}"
    return found[0]


# --- 1. `--no-sync` floor is the already-fetched origin/<default> -----------------------


class NetworkSpy:
    """Wrap subprocess.run and record every git command that talks to a remote."""

    NET = {"fetch", "ls-remote", "pull", "push", "clone", "remote-https"}

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, cmd: Any, *args: Any, **kwargs: Any):
        if isinstance(cmd, (list, tuple)) and cmd and Path(str(cmd[0])).name == "git":
            words = [str(c) for c in cmd[1:]]
            if self.NET & set(words):
                self.calls.append(words)
        return _ORIG_RUN(cmd, *args, **kwargs)


def _next_id_landed_on_main_from_feature(world: World, monkeypatch) -> None:
    """On a feature branch, `next-id EXP` claims EXP-001 on origin/main (CAS); the
    feature checkout does not have that record. Then the remote goes away."""
    to_feature_branch(world)
    result = invoke(world, monkeypatch, ["next-id", "EXP", "--json"])
    out = output_of(result)
    assert result.exit_code == 0, out
    assert allocated(out) == "EXP-001", out
    assert git(world.a, "rev-parse", "--abbrev-ref", "HEAD").strip() == FEATURE
    # precondition: the claim is in the fetched ref, not in the checkout
    fetched = git(world.a, "show", f"refs/remotes/origin/main:{ALLOC}")
    assert "EXP-001" in fetched, fetched
    lock = world.a / ALLOC
    assert not lock.exists() or "EXP-001" not in lock.read_text()
    git(world.a, "remote", "set-url", "origin", str(world.a.parent / "missing.git"))


def test_no_sync_next_id_floors_at_fetched_default(world, monkeypatch) -> None:
    _next_id_landed_on_main_from_feature(world, monkeypatch)
    spy = NetworkSpy()
    monkeypatch.setattr(subprocess, "run", spy)
    result = invoke(world, monkeypatch, ["next-id", "EXP", "--no-sync", "--json"])
    out = output_of(result)

    assert result.exit_code == 0, out
    assert spy.calls == [], f"--no-sync talked to the remote: {spy.calls}"
    assert allocated(out) == "EXP-002", f"re-issued an id already on origin: {out}"


def test_local_create_floors_at_fetched_default(world, monkeypatch) -> None:
    _next_id_landed_on_main_from_feature(world, monkeypatch)
    spy = NetworkSpy()
    monkeypatch.setattr(subprocess, "run", spy)
    result = invoke(world, monkeypatch, ["create", "expedition", TITLE_A])
    out = output_of(result)

    assert result.exit_code == 0, out
    assert spy.calls == [], f"a local create talked to the remote: {spy.calls}"
    made = sorted(p.name for p in (world.a / EXP_DIR).glob("EXP-*.md"))
    assert made and made[0].startswith("EXP-002"), f"re-issued an id on origin: {made} {out}"


# --- 2. an allocation record's prefix is its id space ----------------------------------------


def test_explicit_paper_scoped_id_record_prefix_is_id_space(world, monkeypatch) -> None:
    hdd(world)
    result = invoke(world, monkeypatch, ["hypothesis", "create", TITLE_A, "--id", "H130.1", "--push"])
    assert result.exit_code == 0, output_of(result)
    assert record_for(world, "H130.1")["prefix"] == "H130.", remote_records(world)


def test_auto_paper_scoped_id_record_prefix_is_id_space(world, monkeypatch) -> None:
    hdd(world)
    result = invoke(world, monkeypatch, ["hypothesis", "create", TITLE_A, "--paper", "130", "--push"])
    assert result.exit_code == 0, output_of(result)
    assert record_for(world, "H130.1")["prefix"] == "H130.", remote_records(world)


@pytest.mark.parametrize("space", ["IDEA-R", "IDEA-F"])
def test_explicit_idea_record_prefix_is_id_space(world, space) -> None:
    hdd(world)
    result = service(world).create_item_and_push(
        WorkItemType.IDEA, TITLE_A, item_id=f"{space}-004"
    )
    assert result["success"] is True, result["message"]
    assert record_for(world, f"{space}-004")["prefix"] == space, remote_records(world)


def test_auto_idea_r_record_prefix_is_id_space(world, monkeypatch) -> None:
    hdd(world)
    result = invoke(world, monkeypatch, ["idea", "create", TITLE_A, "--push"])
    assert result.exit_code == 0, output_of(result)
    assert record_for(world, "IDEA-R-001")["prefix"] == "IDEA-R", remote_records(world)


def test_records_are_counted_by_id_head_only(world, monkeypatch) -> None:
    """A record whose `prefix` field disagrees with its id counts in its id's space."""
    hdd(world)
    b_push(world, {}, [{"id": "H130.7", "prefix": "H", "number": 7}])
    result = invoke(world, monkeypatch, ["hypothesis", "create", TITLE_A, "--paper", "130", "--push"])
    out = output_of(result)

    assert result.exit_code == 0, out
    ids = sorted(i for i in remote_ids(world, "research/hypotheses/").values() if i)
    assert ids == ["H130.8"], f"allocated H130.7 again: {ids} {out}"


def test_paper_record_does_not_count_in_dashed_space(world, monkeypatch) -> None:
    """Control: H130.7 is not in the H- space."""
    hdd(world)
    b_push(world, {}, [{"id": "H130.7", "prefix": "H130.", "number": 7}])
    result = invoke(world, monkeypatch, ["hypothesis", "create", TITLE_A, "--push"])
    out = output_of(result)

    assert result.exit_code == 0, out
    ids = sorted(i for i in remote_ids(world, "research/hypotheses/").values() if i)
    assert ids == ["H-001"], ids


# --- 3. one allocator for H<paper>. ---------------------------------------------------------


def _local_hypothesis(world: World, monkeypatch) -> str:
    result = invoke(world, monkeypatch, ["hypothesis", "create", TITLE_A, "--paper", "130"])
    out = output_of(result)
    assert result.exit_code == 0, out
    m = re.search(r"Created (H130\.\d+)", out)
    assert m, out
    return m.group(1)


def test_local_paper_hypothesis_sees_filename_only_id(world, monkeypatch) -> None:
    hdd(world)
    # no frontmatter: not an item, but its filename holds H130.2
    (world.a / "research/hypotheses/H130.2-draft.md").write_text("# a draft, no frontmatter\n")
    assert _local_hypothesis(world, monkeypatch) == "H130.3"


def test_local_paper_hypothesis_sees_allocation_record(world, monkeypatch) -> None:
    hdd(world)
    lock = world.a / ALLOC
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(json.dumps([{"id": "H130.4", "prefix": "H130.", "number": 4}]))
    assert _local_hypothesis(world, monkeypatch) == "H130.5"


# --- 4. unused create_item arguments are gone ------------------------------------------------


def test_create_item_takes_no_render_or_id_prefix() -> None:
    params = inspect.signature(KanbanService.create_item).parameters
    assert "render" not in params, list(params)
    assert "id_prefix" not in params, list(params)


# --- 5. only frontmatter ids count on the base ----------------------------------------------

_BODY_IDS = {
    f"{EXP_DIR}/notes.md": (
        '---\nid: EXP-005\ntitle: "Notes"\ntype: expedition\nstatus: backlog\n---\n\n'
        "# Notes\n\nid: EXP-099\n\n```yaml\nid: EXP-098\n```\n"
    ),
    f"{EXP_DIR}/scratch.md": "# scratch, no frontmatter\n\nid: EXP-097\n",
}


def test_create_push_ignores_body_ids_on_base(world, monkeypatch) -> None:
    b_push(world, _BODY_IDS)
    result = invoke(world, monkeypatch, ["create", "expedition", TITLE_A, "--push"])
    out = output_of(result)

    assert result.exit_code == 0, out
    assert "EXP-006" in out, f"a body/code-block `id:` line counted: {out}"


def test_next_id_ignores_body_ids_on_base(world, monkeypatch) -> None:
    b_push(world, _BODY_IDS)
    result = invoke(world, monkeypatch, ["next-id", "EXP", "--json"])
    out = output_of(result)

    assert result.exit_code == 0, out
    assert allocated(out) == "EXP-006", f"a body/code-block `id:` line counted: {out}"


def test_explicit_id_named_only_in_a_body_is_not_refused(world) -> None:
    b_push(world, _BODY_IDS)
    result = service(world).create_item_and_push(
        WorkItemType.EXPEDITION, TITLE_A, item_id="EXP-099"
    )
    assert result["success"] is True, result["message"]


# --- 6. every per-base render is checked -----------------------------------------------------


def test_per_base_render_with_invalid_text_is_refused(world) -> None:
    """The local scan picks EXP-001, which renders cleanly; the fetched base holds
    EXP-001, so the id becomes EXP-002, whose render is not valid UTF-8 (a lone
    surrogate). That render is refused like any other bad text; nothing is pushed."""
    feat = to_feature_branch(world)
    b_push(world, {
        f"{EXP_DIR}/EXP-001-rival.md":
            '---\nid: EXP-001\ntitle: "Rival"\ntype: expedition\nstatus: backlog\n---\n'
    })

    def render(new_id: str) -> str:
        bad = "" if new_id == "EXP-001" else " \udc80"
        return f'---\nid: {new_id}\ntitle: "{TITLE_A}{bad}"\ntype: expedition\n---\n'

    result = service(world).create_item_and_push(
        WorkItemType.EXPEDITION, TITLE_A, render=render, id_prefix="EXP"
    )

    assert result["success"] is False, result
    assert "invalid UTF-8" in result["message"], result["message"]
    assert_untouched(world, feat)


# --- 7. same number, same id -----------------------------------------------------------------

_HOLDERS = {
    "filename": (f"{EXP_DIR}/EXP-003-rival.md", "EXP-003-rival.md"),
    "frontmatter": (f"{EXP_DIR}/rival-notes.md", "rival-notes.md"),
}


@pytest.mark.parametrize("holder", list(_HOLDERS))
@pytest.mark.parametrize("timing", TIMINGS)
def test_unpadded_explicit_id_is_refused_against_padded(world, monkeypatch, timing, holder) -> None:
    path, name = _HOLDERS[holder]
    feat = to_feature_branch(world)

    def rival() -> None:
        b_push(world, {
            path: '---\nid: EXP-003\ntitle: "Rival"\ntype: expedition\nstatus: backlog\n---\n'
        })

    on_timing(world, monkeypatch, timing, rival)
    result = service(world).create_item_and_push(
        WorkItemType.EXPEDITION, TITLE_A, item_id="EXP-3"
    )

    assert result["success"] is False, result
    assert name in result["message"], result["message"]
    assert list(remote_ids(world, f"{EXP_DIR}/")) == [path]
    assert_untouched(world, feat)


@pytest.mark.parametrize("timing", TIMINGS)
def test_cli_unpadded_measure_id_is_refused_against_padded(world, monkeypatch, timing) -> None:
    hdd(world)
    feat = to_feature_branch(world)

    def rival() -> None:
        b_push(world, {"research/measures/M-001-rival.md": '---\nid: M-001\ntitle: "Rival"\n---\n'})

    on_timing(world, monkeypatch, timing, rival)
    result = invoke(world, monkeypatch, [
        "measure", "create", TITLE_A, "--unit", "count", "--category", "coverage",
        "--id", "M-1", "--push",
    ])
    out = " ".join(output_of(result).split())

    assert result.exit_code != 0, out
    assert "M-001-rival.md" in out, out
    assert list(remote_ids(world, "research/measures/")) == ["research/measures/M-001-rival.md"]
    assert_untouched(world, feat)
