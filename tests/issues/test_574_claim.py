"""Issue #574, PR B: ``claim``, the race-free claim.

The [steer] on #574 splits the build into four PRs, and this is B. It covers spec §3
(claim), §1 (the CLAUDE.md carve-out) and §6 (the work/handoff skills). It is built
on PR A's ``sync_and_push`` (tests/issues/test_574_sync_and_push.py).

API under test:

- ``KanbanService.claim_item(item_id, *, actor, take_over=False, sleep, jitter,
  seam=None) -> Outcome``. Its ``mutate`` reads the item from the fetched base on
  every attempt, applies the claim rules and writes the item.
- CLI ``yurtle-kanban claim ID [--agent A] [--take-over]`` prints the outcome's
  message and exits with ``Outcome.exit_code``. The actor comes from
  ``resolve_actor(--agent, allow_git_fallback=False)``.

Claim rules (§3), judged against the FETCHED item:

- held by the actor (``same_actor``) and canonical ``in_progress``: ``noop``
  "already yours", exit 0, no commit, no hooks;
- any OTHER non-empty assignee, in any status: ``refused`` "held by X" (exit 1) on
  the first attempt, or ``lost`` "lost to X" (exit 3) when it became held only
  after a rejected push;
- ``in_progress`` with no assignee: ``refused``, "EXP-001 is in progress with no
  holder; use claim --take-over";
- ``in_progress`` not in ``legal_next(from)``: ``refused``, listing the legal targets;
- otherwise the PROPOSED item (the theme's native in-progress status, assignee =
  actor) is checked against rules, gates (#586) and WIP, with WIP counted in the
  fetched tree. It is written with a status-history node whose ``kb:by`` is the
  actor. The result is ``won`` (exit 0) and the commit touches only the item file.
- ``--take-over`` overrides both holder refusals and records ``kb:takenOverFrom
  "<old holder>"`` (``""`` when there was none). A status change still answers to
  legality, gates and WIP. An item already in progress only changes assignee.
- ``STATUS_CHANGE`` and ``ASSIGNED`` hooks fire once, after ``won``/``local``, for
  the winner only.
- No remote: ``local``, exit 0, a "no remote" note, a local commit of the item only.

Acceptance with real git (a bare remote plus clones A and B, the #585 ``World``):

1. B has claimed EXP-001 before A fetches: A exits 1 "held by agent-B", the remote
   is unchanged by A, and A's checkout is byte-identical.
2. B's claim lands in A's seam on attempt 0: A exits 3 "lost to agent-B", the
   remote has only B's claim, and A fires no hooks.
3. An unrelated commit lands in A's seam on attempt 0: A retries, wins, and fires
   its hooks once.

Ambiguities resolved here (the test partner's reading; the driver may challenge):

a. Hooks are observed through a REAL shell hook in A's
   ``.kanban/hooks/kanban-hooks.yurtle.md``, which appends ``{event} {item_id}
   {new_status} {assignee}`` to a marker file outside the clone. It is an untracked
   file in A, so it never rides along in a commit. B has no hooks file, so B's own
   claim never writes the marker.
b. "Native in-progress status" is derived at test time as
   ``service.status_label(item at in_progress)`` (nautical's reverse map gives
   ``underway``), and is never pinned as a literal.
c. The status-history node is found as a ``[ ... ]`` blank node inside the item's
   ``yurtle`` block. The claim node must carry ``kb:by "<actor>"`` and
   ``kb:status kb:in_progress``. For ``--take-over``, ``kb:takenOverFrom`` must sit
   in a node carrying ``kb:by "<actor>"``. The order of predicates is not pinned.
d. Held/lost wording is matched case-insensitively ("held by agent-B",
   "lost to agent-B"), because PR A's ``lost`` outcome prefixes "Lost to X: ".
e. "WIP counted in the fetched tree" is pinned in both directions: full on origin
   but not locally refuses, and full locally (an uncommitted item in A's tree) but
   not on origin wins.
f. The legal-target listing in an illegal-move refusal must name every target in
   its native form (backlog -> provisioning, stranded), as #573's refusal does.
g. The no-actor refusal is pinned through the CLI only (the service takes
   ``actor`` as a required keyword). Clone A has a git ``user.name``, so the
   refusal proves the git fallback is off.
h. ``--take-over`` of an item already in progress: the frontmatter status still
   resolves to canonical ``in_progress``; rewriting ``in_progress`` as the native
   ``underway`` is allowed. Whether STATUS_CHANGE fires there, and whether the
   take-over node carries a ``kb:status``, is not pinned.
i. §6: the software and nautical ``work`` skills claim with ``yurtle-kanban claim``
   (with a ``YURTLE_AGENT=`` prefix or ``--agent``) instead of ``move ... in_progress``,
   and branch from ``origin/<default>``. The ``handoff`` skills have the receiver run
   ``yurtle-kanban claim ... --take-over --agent ...``.
"""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import CliRunner

from tests.issues.test_574_sync_and_push import (
    JITTER,
    Recorder,
    commit_files,
    dirty_feature_branch,
    rival,
    snapshot,
)
from tests.issues.test_585_create_push_loop import EXP_DIR, World, git
from tests.issues.test_590_next_id_and_hdd_ids import b_push
from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import main
from yurtle_kanban.config import KanbanConfig, PathConfig
from yurtle_kanban.models import WorkItemStatus
from yurtle_kanban.service import KanbanService

REPO = Path(__file__).resolve().parents[2]
ITEM_ID = "EXP-001"
ITEM = f"{EXP_DIR}/EXP-001-x.md"
A = "agent-A"
B = "agent-B"
CARVE_OUT = (
    "Exception (Captain, 2026-09-27): kanban-only commits made by `claim`, `bounce`, "
    "`control` and `update --push` — touching only `.kanban/` and work-item files — "
    "are pushed directly to the default branch."
)


# --- harness ---------------------------------------------------------------------


def item_text(
    status: str, assignee: str | None = None, item_id: str = ITEM_ID, title: str = "X"
) -> str:
    who = f"assignee: {assignee}\n" if assignee else ""
    return (
        f"---\nid: {item_id}\ntitle: \"{title}\"\ntype: expedition\nstatus: {status}\n"
        f"{who}---\n\n# {title}\n\nA description long enough.\n"
    )


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


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> Any:
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


def seed(world: World, status: str, assignee: str | None = None) -> None:
    """Set EXP-001 to `status`/`assignee` on origin, in A's checkout too."""
    push_from_a(world, {ITEM: item_text(status, assignee)}, f"EXP-001 {status}")


def service(clone: Path) -> KanbanService:
    return KanbanService(KanbanConfig.load(clone / ".kanban" / "config.yaml"), clone)


def install_hooks(world: World, tmp_path: Path) -> Path:
    """A real shell hook in A (untracked) that appends each on_status_change and
    on_assign firing to a marker file outside the clone."""
    marker = tmp_path / "hook-marker.log"
    command = f"echo {{event}} {{item_id}} {{new_status}} {{assignee}} >> '{marker}'"
    action = f"      actions:\n        - type: shell\n          command: \"{command}\"\n"
    doc = (
        "---\ntype: kanban-hooks\nid: hooks-574b\nversion: 1\nhooks:\n"
        f"  on_status_change:\n    - item_types: [expedition]\n{action}"
        f"  on_assign:\n    - item_types: [expedition]\n{action}"
        "---\n# Hooks 574b\n"
    )
    hooks = world.a / ".kanban" / "hooks" / "kanban-hooks.yurtle.md"
    hooks.parent.mkdir(parents=True, exist_ok=True)
    hooks.write_text(doc)
    return marker


def fired(marker: Path) -> list[str]:
    return marker.read_text().splitlines() if marker.exists() else []


def claim(
    clone: Path, actor: str, rec: Recorder | None = None, *, take_over: bool = False
) -> Any:
    rec = rec or Recorder()
    return service(clone).claim_item(
        ITEM_ID, actor=actor, take_over=take_over,
        sleep=rec.sleep, jitter=rec.jitter, seam=rec.seam,
    )


def b_claims(world: World) -> None:
    """B claims EXP-001 through its own `claim_item` (B has no hooks file)."""
    out = claim(world.b, B)
    assert out.kind == "won", f"B's own claim did not win: {out.kind}: {out.message}"


def frontmatter(text: str) -> dict[str, Any]:
    m = re.match(r"\A---\n(.*?)^---", text, re.S | re.M)
    assert m, f"no frontmatter in:\n{text}"
    data = yaml.safe_load(m.group(1))
    assert isinstance(data, dict)
    return data


def history_nodes(text: str) -> list[str]:
    """The `[ ... ]` blank nodes of the item's yurtle status-history block."""
    blocks = re.findall(r"```yurtle\n(.*?)```", text, re.S)
    return [node for block in blocks for node in re.findall(r"\[(.*?)\]", block, re.S)]


def nodes_by(text: str, actor: str) -> list[str]:
    return [n for n in history_nodes(text) if f'kb:by "{actor}"' in n]


def native_in_progress(clone: Path) -> str:
    svc = service(clone)
    item = svc.get_item(ITEM_ID)
    assert item is not None
    return svc.status_label(replace(item, status=WorkItemStatus.IN_PROGRESS))


def native(clone: Path, status: WorkItemStatus) -> str:
    svc = service(clone)
    item = svc.get_item(ITEM_ID)
    assert item is not None
    return svc.status_label(replace(item, status=status))


def canonical(clone: Path, name: str) -> WorkItemStatus | None:
    """`name` as a status of EXP-001's theme (`in_progress` and `underway` alike)."""
    svc = service(clone)
    item = svc.get_item(ITEM_ID)
    assert item is not None
    return svc.resolve_status_name(item, name)


def assert_claimed_by(world: World, actor: str, base: str) -> str:
    """Origin's tip is one commit on `base` touching only the item, which now shows
    `actor` holding it at the native in-progress status with a history node."""
    tip = world.remote_sha()
    assert git(world.remote, "rev-list", f"{base}..{tip}").split() == [tip], (
        "origin must get exactly one new commit"
    )
    assert commit_files(world.remote, tip) == [ITEM], "the claim commit touched other files"
    text = world.remote_show(ITEM)
    fm = frontmatter(text)
    assert fm["assignee"] == actor
    assert fm["status"] == native_in_progress(world.a)
    mine = nodes_by(text, actor)
    assert any(re.search(r"kb:status\s+kb:in_progress\b", n) for n in mine), (
        f"no status-history node with kb:by {actor!r} and kb:status kb:in_progress:\n{text}"
    )
    return text


def invoke(world: World, monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> Any:
    monkeypatch.chdir(world.a)
    return CliRunner().invoke(main, argv)


def output_of(result: Any) -> str:
    return " ".join((result.output or "").split())


# --- the claim goes through -------------------------------------------------------------


def test_claim_ready_item_wins(world, tmp_path) -> None:
    marker = install_hooks(world, tmp_path)
    base = world.remote_sha()
    rec = Recorder()

    out = claim(world.a, A, rec)

    assert out.kind == "won", out.message
    assert out.exit_code == 0
    assert out.sha == world.remote_sha()
    text = assert_claimed_by(world, A, base)
    assert "takenOverFrom" not in text, "a plain claim recorded a take-over"
    assert rec.seams == [0] and rec.sleeps == []
    assert sorted(fired(marker)) == sorted([
        f"on_status_change {ITEM_ID} in_progress {A}",
        f"on_assign {ITEM_ID} in_progress {A}",
    ]), fired(marker)


# --- already yours -------------------------------------------------------------------------


@pytest.mark.parametrize("held_as", [A, "Agent-a"])
def test_own_in_progress_item_is_already_yours(world, tmp_path, held_as) -> None:
    seed(world, "in_progress", held_as)
    marker = install_hooks(world, tmp_path)
    base = world.remote_sha()
    before = snapshot(world.a)
    rec = Recorder()

    out = claim(world.a, A, rec)

    assert out.kind == "noop", out.message
    assert out.exit_code == 0
    assert "already yours" in out.message.lower(), out.message
    assert out.sha is None
    assert world.remote_sha() == base
    assert rec.seams == []
    assert snapshot(world.a) == before
    assert fired(marker) == []


# --- held by someone else ---------------------------------------------------------------------


@pytest.mark.parametrize("status", ["ready", "in_progress", "blocked", "backlog"])
def test_held_by_other_is_refused_in_any_status(world, tmp_path, status) -> None:
    seed(world, status, B)
    marker = install_hooks(world, tmp_path)
    base = world.remote_sha()
    before = snapshot(world.a)
    rec = Recorder()

    out = claim(world.a, A, rec)

    assert out.kind == "refused", out.message
    assert out.exit_code == 1
    assert re.search(r"held by agent-B", out.message, re.I), out.message
    assert world.remote_sha() == base
    assert rec.seams == [], "a push was attempted"
    assert snapshot(world.a) == before
    assert fired(marker) == []


# --- in progress with no holder -----------------------------------------------------------------


def test_in_progress_without_holder_suggests_take_over(world) -> None:
    seed(world, "in_progress")
    base = world.remote_sha()

    out = claim(world.a, A)

    assert out.kind == "refused", out.message
    assert out.exit_code == 1
    assert f"{ITEM_ID} is in progress with no holder; use claim --take-over" in out.message
    assert world.remote_sha() == base


# --- illegal move -----------------------------------------------------------------------------------


def test_illegal_move_lists_legal_targets(world) -> None:
    seed(world, "backlog")
    base = world.remote_sha()

    out = claim(world.a, A)

    assert out.kind == "refused", out.message
    assert out.exit_code == 1
    for target in (WorkItemStatus.READY, WorkItemStatus.BLOCKED):
        name = native(world.a, target)
        assert name in out.message, f"legal target {name!r} not listed: {out.message}"
    assert world.remote_sha() == base


def test_claim_of_done_item_is_refused(world) -> None:
    seed(world, "done")
    base = world.remote_sha()

    out = claim(world.a, A)

    assert out.kind == "refused", out.message
    assert out.exit_code == 1
    assert world.remote_sha() == base


# --- gates and WIP on the proposed item, counted in the fetched tree ---------------------------------


GATE_ASSIGNEE = {
    "* -> in_progress": [
        {"id": "require_assignee", "check": "item.assignee",
         "message": "Assignee required (use --assign)"},
    ],
}
GATE_FAILING = {
    "* -> in_progress": [
        {"id": "self_review", "check": "context.self_reviewed",
         "message": "Self-review gate 574b not satisfied"},
    ],
}


def set_gates(world: World, gates: dict[str, list[dict[str, str]]]) -> None:
    config = KanbanConfig(
        theme="nautical",
        paths=PathConfig(
            root="kanban-work/",
            scan_paths=["kanban-work/expeditions/", "kanban-work/signals/"],
        ),
        gates=gates,
    )
    config.save(world.a / ".kanban" / "config.yaml")
    push_from_a(world, {}, "board: gates")


def test_require_assignee_gate_passes_on_proposed_item(world) -> None:
    set_gates(world, GATE_ASSIGNEE)
    base = world.remote_sha()

    out = claim(world.a, A)

    assert out.kind == "won", out.message
    assert_claimed_by(world, A, base)


def test_failing_gate_refuses_claim(world) -> None:
    set_gates(world, GATE_FAILING)
    base = world.remote_sha()

    out = claim(world.a, A)

    assert out.kind == "refused", out.message
    assert out.exit_code == 1
    assert "Self-review gate 574b not satisfied" in out.message
    assert world.remote_sha() == base


WIP_CONFIG = """\
version: "2.0"
boards:
  - name: development
    preset: nautical
    path: kanban-work/
    wip_limits:
      in_progress: 1
default_board: development
"""
OTHER = f"{EXP_DIR}/EXP-002-y.md"


def test_wip_full_on_origin_but_not_locally_is_refused(world) -> None:
    push_from_a(world, {".kanban/config.yaml": WIP_CONFIG}, "board: wip 1")
    b_push(world, {OTHER: item_text("in_progress", B, "EXP-002", "Y")})
    assert not (world.a / OTHER).exists(), "A must not see B's item locally"
    base = world.remote_sha()

    out = claim(world.a, A)

    assert out.kind == "refused", out.message
    assert out.exit_code == 1
    assert "wip" in out.message.lower(), out.message
    assert world.remote_sha() == base


def test_wip_full_locally_but_not_on_origin_wins(world) -> None:
    push_from_a(world, {".kanban/config.yaml": WIP_CONFIG}, "board: wip 1")
    (world.a / OTHER).write_text(item_text("in_progress", B, "EXP-002", "Y"))  # untracked
    base = world.remote_sha()

    out = claim(world.a, A)

    assert out.kind == "won", out.message
    assert_claimed_by(world, A, base)


# --- --take-over ---------------------------------------------------------------------------------------


def test_take_over_in_progress_item_changes_only_assignee(world) -> None:
    seed(world, "in_progress", B)
    base = world.remote_sha()

    out = claim(world.a, A, take_over=True)

    assert out.kind == "won", out.message
    assert out.exit_code == 0
    tip = world.remote_sha()
    assert git(world.remote, "rev-list", f"{base}..{tip}").split() == [tip]
    assert commit_files(world.remote, tip) == [ITEM]
    text = world.remote_show(ITEM)
    fm = frontmatter(text)
    assert fm["assignee"] == A
    assert canonical(world.a, fm["status"]) == WorkItemStatus.IN_PROGRESS
    assert any(f'kb:takenOverFrom "{B}"' in n for n in nodes_by(text, A)), (
        f'no node with kb:by "{A}" recording kb:takenOverFrom "{B}":\n{text}'
    )


def test_take_over_pre_assigned_ready_item_moves_it(world) -> None:
    seed(world, "ready", B)
    base = world.remote_sha()

    out = claim(world.a, A, take_over=True)

    assert out.kind == "won", out.message
    text = assert_claimed_by(world, A, base)
    assert any(f'kb:takenOverFrom "{B}"' in n for n in nodes_by(text, A)), text


def test_take_over_without_holder_records_empty_string(world) -> None:
    seed(world, "in_progress")

    out = claim(world.a, A, take_over=True)

    assert out.kind == "won", out.message
    text = world.remote_show(ITEM)
    assert frontmatter(text)["assignee"] == A
    assert any('kb:takenOverFrom ""' in n for n in nodes_by(text, A)), text


def test_take_over_still_answers_to_gates(world) -> None:
    set_gates(world, GATE_FAILING)
    seed(world, "ready", B)
    base = world.remote_sha()

    out = claim(world.a, A, take_over=True)

    assert out.kind == "refused", out.message
    assert out.exit_code == 1
    assert "Self-review gate 574b not satisfied" in out.message
    assert world.remote_sha() == base


def test_take_over_still_answers_to_legality(world) -> None:
    seed(world, "done", B)
    base = world.remote_sha()

    out = claim(world.a, A, take_over=True)

    assert out.kind == "refused", out.message
    assert out.exit_code == 1
    assert world.remote_sha() == base


# --- no remote ---------------------------------------------------------------------------------------------


def test_no_remote_claims_locally(world, tmp_path) -> None:
    git(world.a, "remote", "remove", "origin")
    (world.a / "staged.txt").write_text("user staged work\n")
    git(world.a, "add", "staged.txt")
    marker = install_hooks(world, tmp_path)
    head_before = git(world.a, "rev-parse", "HEAD").strip()

    out = claim(world.a, A)

    assert out.kind == "local", out.message
    assert out.exit_code == 0
    assert "no remote" in out.message.lower(), out.message
    head = git(world.a, "rev-parse", "HEAD").strip()
    assert git(world.a, "rev-list", f"{head_before}..{head}").split() == [head]
    assert commit_files(world.a, head) == [ITEM]
    fm = frontmatter(git(world.a, "show", f"HEAD:{ITEM}"))
    assert fm["assignee"] == A
    assert fm["status"] == native_in_progress(world.a)
    assert "staged.txt" in git(world.a, "diff", "--cached", "--name-only").split()
    assert sorted(fired(marker)) == sorted([
        f"on_status_change {ITEM_ID} in_progress {A}",
        f"on_assign {ITEM_ID} in_progress {A}",
    ]), fired(marker)


# --- acceptance: real races ------------------------------------------------------------------------------------


def test_acceptance_1_b_claimed_before_a_fetches(world, tmp_path) -> None:
    marker = install_hooks(world, tmp_path)
    b_claims(world)
    b_tip = world.remote_sha()
    before = snapshot(world.a)
    rec = Recorder()

    out = claim(world.a, A, rec)

    assert out.kind == "refused", out.message
    assert out.exit_code == 1
    assert re.search(r"held by agent-B", out.message, re.I), out.message
    assert world.remote_sha() == b_tip, "A changed the remote"
    assert rec.seams == []
    assert snapshot(world.a) == before, "A's checkout changed"
    assert fired(marker) == []


def test_acceptance_2_b_claims_between_a_fetch_and_push(world, tmp_path) -> None:
    dirty_feature_branch(world)
    marker = install_hooks(world, tmp_path)
    base = world.remote_sha()
    before = snapshot(world.a)
    rec = Recorder(lambda attempt: b_claims(world) if attempt == 0 else None)

    out = claim(world.a, A, rec)

    assert out.kind == "lost", out.message
    assert out.exit_code == 3
    assert re.search(r"lost to agent-B", out.message, re.I), out.message
    assert out.sha is None
    tip = world.remote_sha()
    assert git(world.remote, "rev-list", f"{base}..{tip}").split() == [tip], (
        "origin must hold B's claim and nothing else"
    )
    text = world.remote_show(ITEM)
    assert frontmatter(text)["assignee"] == B
    assert A not in text, "A's claim reached origin"
    assert rec.seams == [0], "A pushed again after reading B's claim"
    assert snapshot(world.a) == before, "A's checkout changed"
    assert fired(marker) == [], "the loser fired hooks"


def test_acceptance_3_unrelated_commit_retries_and_wins(world, tmp_path) -> None:
    marker = install_hooks(world, tmp_path)
    base = world.remote_sha()
    rec = Recorder(lambda attempt: rival(world) if attempt == 0 else None)

    out = claim(world.a, A, rec)

    assert out.kind == "won", out.message
    assert out.exit_code == 0
    tip = world.remote_sha()
    history = git(world.remote, "rev-list", f"{base}..{tip}").split()
    assert len(history) == 2, history
    ours, theirs = history
    assert commit_files(world.remote, theirs) == ["other-0.txt"]
    assert commit_files(world.remote, ours) == [ITEM]
    assert frontmatter(world.remote_show(ITEM))["assignee"] == A
    assert rec.seams == [0, 1]
    assert rec.sleeps == [pytest.approx(JITTER * 1)]
    assert sorted(fired(marker)) == sorted([
        f"on_status_change {ITEM_ID} in_progress {A}",
        f"on_assign {ITEM_ID} in_progress {A}",
    ]), f"hooks must fire exactly once each: {fired(marker)}"


# --- CLI ----------------------------------------------------------------------------------------------------------


def test_cli_without_actor_is_refused(world, monkeypatch) -> None:
    assert git(world.a, "config", "user.name").strip(), "A must have a git user.name"
    base = world.remote_sha()
    head = git(world.a, "rev-parse", "HEAD").strip()

    result = invoke(world, monkeypatch, ["claim", ITEM_ID])

    out = output_of(result)
    assert result.exit_code == 1, out
    assert "--agent" in out and "YURTLE_AGENT" in out, out
    assert world.remote_sha() == base
    assert git(world.a, "rev-parse", "HEAD").strip() == head


def test_cli_take_over_without_actor_is_refused(world, monkeypatch) -> None:
    seed(world, "in_progress", B)
    base = world.remote_sha()

    result = invoke(world, monkeypatch, ["claim", ITEM_ID, "--take-over"])

    assert result.exit_code == 1, output_of(result)
    assert world.remote_sha() == base


def test_cli_claim_with_agent_wins(world, monkeypatch) -> None:
    base = world.remote_sha()

    result = invoke(world, monkeypatch, ["claim", ITEM_ID, "--agent", A])

    assert result.exit_code == 0, output_of(result)
    assert_claimed_by(world, A, base)


def test_cli_claim_takes_actor_from_env(world, monkeypatch) -> None:
    monkeypatch.setenv("YURTLE_AGENT", A)
    base = world.remote_sha()

    result = invoke(world, monkeypatch, ["claim", ITEM_ID])

    assert result.exit_code == 0, output_of(result)
    assert_claimed_by(world, A, base)


def test_cli_acceptance_1_held_by_b(world, monkeypatch) -> None:
    b_claims(world)
    b_tip = world.remote_sha()
    before = snapshot(world.a)

    result = invoke(world, monkeypatch, ["claim", ITEM_ID, "--agent", A])

    out = output_of(result)
    assert result.exit_code == 1, out
    assert re.search(r"held by agent-B", out, re.I), out
    assert world.remote_sha() == b_tip
    assert snapshot(world.a) == before


def test_cli_already_yours_exits_zero(world, monkeypatch) -> None:
    seed(world, "in_progress", A)
    base = world.remote_sha()

    result = invoke(world, monkeypatch, ["claim", ITEM_ID, "--agent", A])

    out = output_of(result)
    assert result.exit_code == 0, out
    assert "already yours" in out.lower(), out
    assert world.remote_sha() == base


def test_cli_take_over(world, monkeypatch) -> None:
    seed(world, "in_progress", B)

    result = invoke(world, monkeypatch, ["claim", ITEM_ID, "--agent", A, "--take-over"])

    assert result.exit_code == 0, output_of(result)
    text = world.remote_show(ITEM)
    assert frontmatter(text)["assignee"] == A
    assert any(f'kb:takenOverFrom "{B}"' in n for n in nodes_by(text, A)), text


def test_cli_no_remote_is_local_with_note(world, monkeypatch) -> None:
    git(world.a, "remote", "remove", "origin")

    result = invoke(world, monkeypatch, ["claim", ITEM_ID, "--agent", A])

    out = output_of(result)
    assert result.exit_code == 0, out
    assert "no remote" in out.lower(), out
    assert commit_files(world.a, "HEAD") == [ITEM]


# --- control: `move` is unchanged by PR B -------------------------------------------------------------------------------


def test_control_move_still_commits_locally_without_pushing(world, monkeypatch) -> None:
    base = world.remote_sha()

    result = invoke(
        world, monkeypatch, ["move", ITEM_ID, "in_progress", "--assign", A, "--agent", A]
    )

    assert result.exit_code == 0, output_of(result)
    assert world.remote_sha() == base, "move pushed"
    assert commit_files(world.a, "HEAD") == [ITEM]
    assert frontmatter((world.a / ITEM).read_text())["assignee"] == A


# --- §1: the CLAUDE.md carve-out ------------------------------------------------------------------------------------------


def test_claude_md_has_the_kanban_only_carve_out() -> None:
    text = (REPO / "CLAUDE.md").read_text(encoding="utf-8")
    m = re.search(r"^### Branch \+ PR Pattern.*?(?=^#{1,3} )", text, re.S | re.M)
    assert m, "no '### Branch + PR Pattern' section in CLAUDE.md"
    section = " ".join(m.group(0).split())
    assert CARVE_OUT in section, (
        "the Branch + PR section lacks the carve-out sentence from #574 §1"
    )


# --- §6: skills ------------------------------------------------------------------------------------------------------------


CLAIM_LINE = re.compile(
    r"^\s*(?:YURTLE_AGENT=\S+\s+yurtle-kanban claim \S+|yurtle-kanban claim \S+.*--agent)",
    re.M,
)


@pytest.mark.parametrize("theme,prefix", [("software", "FEAT"), ("nautical", "EXP")])
def test_work_skill_claims_and_branches_from_origin(theme, prefix) -> None:
    text = (REPO / "skills" / theme / "work" / "SKILL.md").read_text(encoding="utf-8")
    assert CLAIM_LINE.search(text), f"{theme} work skill does not run `yurtle-kanban claim`"
    assert not re.search(rf"^\s*yurtle-kanban move {prefix}-\S+ in_progress", text, re.M), (
        f"{theme} work skill still starts work with `move ... in_progress`"
    )
    assert re.search(r"^\s*git (?:checkout -b|switch -c) \S+ origin/", text, re.M), (
        f"{theme} work skill does not branch from origin/<default>"
    )


@pytest.mark.parametrize("theme", ["software", "nautical"])
def test_handoff_skill_receiver_takes_over(theme) -> None:
    text = (REPO / "skills" / theme / "handoff" / "SKILL.md").read_text(encoding="utf-8")
    assert re.search(
        r"yurtle-kanban claim \S+(?=[^\n]*--take-over)(?=[^\n]*--agent)", text
    ), f"{theme} handoff skill lacks `yurtle-kanban claim ID --take-over --agent <receiver>`"
