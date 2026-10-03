"""Issue #1251, part 4: ``move ID STATUS --push``, a move as one compare-and-swap.

``move`` commits locally only. Its legality check, its WIP count and its holder guard
all read the LOCAL copy, which can be stale (the issue's "stale-read gap"). With
``--push``, ``move`` goes through ``sync_and_push`` the way ``update --push`` (#574
PR D) and ``claim`` (#574 PR B, #865) do. It is built on their harness:
tests/issues/test_574_sync_and_push.py, test_574_claim.py and test_574_update_push.py.

API under test:

- CLI ``yurtle-kanban move ID STATUS --push`` with every existing ``move`` option
  (``--assign``, ``--agent``, ``-m``, ``--force``, ``--closed-by``, ``--resolution``,
  ``--superseded-by``, ``--skip-gates``, ``--self-reviewed``, ``--take-over``). It
  prints the outcome's message and exits as ``update --push`` does (``_print_outcome``):
  0 won/noop/local; 1 refused; 8 halted; 4 unreachable; 5 busy; 6 push refused.
  ``--push --no-commit`` and ``--push --export-board`` are refused (exit 1, or a
  usage exit 2) before anything else.
- ``KanbanService.move_item_push(item_id, new_status, *, message=None, assignee=None,
  actor=None, validate_workflow=True, skip_wip_check=False, skip_gates=False,
  gate_context=None, closed_by=None, take_over=False, resolution=None,
  superseded_by=None, sleep, jitter, seam) -> Outcome``. The race tests use it, since
  the CLI has no seam.

Rules pinned here, all judged on the item as ORIGIN's tree has it, by origin's config:

- the halt (a move to in progress only) is exit 8 and nothing pushed; a move to done
  still goes through;
- the holder guard: an item B holds in progress is refused for A, and
  ``--take-over --agent A`` passes and records ``kb:takenOverFrom "agent-B"``;
- resolution rules (#581): ``duplicate`` needs ``--superseded-by``;
- workflow legality unless ``--force``: A's copy says ``ready`` but origin's item is
  ``done``; nautical has no move out of done, so ``in_progress`` is refused;
- WIP limits counted in origin's tree unless ``--force``;
- gates on the proposed item unless ``--skip-gates`` (``--self-reviewed`` reaches
  the gate context);
- moving to the status origin already has is a ``noop``, exit 0, nothing pushed.

Origin gets exactly one new commit, touching only the item, with plain ``move``'s
subject (``Move EXP-001 to in_progress`` plus its suffixes, or ``-m``). The status is
written in the theme's native name (nautical ``underway``) with a history node whose
``kb:by`` is the actor. The checkout is byte-identical, unless it is a clean
default-branch checkout, which is fast-forwarded. Hooks fire once, after a won or
local outcome, never on a refusal. Without a remote the move is a local commit.

Ambiguities resolved here (the test partner's reading; the driver may challenge):

a. Refusal wording is matched loosely: "held by agent-B", "wip", the gate's own
   message, "board halted by", "superseded-by".
b. The stale-read legality case uses nautical ``done`` (``arrived``) on origin, from
   which no move is legal. A's local copy (``ready``) would allow ``in_progress``.
   With ``--force`` the same move passes and lands as ``underway``.
c. Two tests go beyond the listed cases because they follow from "judged on origin's
   tree": an item that exists ONLY on origin can be moved with ``--push`` (the CLI
   must not refuse it as unknown off the local scan), and a ``--superseded-by``
   target that exists only on origin is known. An item only in A's local tree is
   refused (as ``update --push`` refuses it).
d. Hooks are observed through the real shell hook of test_574_claim (``install_hooks``),
   an untracked file in A that never rides along in a commit.
e. A rival that moves the item to done in A's seam makes A's retry ``refused``
   (there is no holder to lose to), with only the rival's commit on origin.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from tests.issues.test_574_claim import (
    GATE_FAILING,
    ITEM,
    ITEM_ID,
    OTHER,
    WIP_CONFIG,
    A,
    B,
    fired,
    frontmatter,
    install_hooks,
    item_text,
    native,
    native_in_progress,
    nodes_by,
    output_of,
    push_from_a,
    seed,
    service,
    set_gates,
)
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
from yurtle_kanban.cli import main
from yurtle_kanban.models import WorkItemStatus

pytestmark = pytest.mark.usefixtures("claim_env")

HALTED = 8
CONTROL = ".kanban/control.yaml"
HALT_YAML = (
    'mode: halt\nreason: "Incident 1251: hold all new work"\nby: agent-B\n'
    "at: 2026-10-02T10:00:00-07:00\n"
)
OTHER_ID = "EXP-002"


# --- harness ---------------------------------------------------------------------


def invoke(world: World, monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> Any:
    monkeypatch.chdir(world.a)
    return CliRunner().invoke(main, argv)


def move_push(
    clone: Path, new_status: WorkItemStatus, rec: Recorder | None = None,
    item_id: str = ITEM_ID, **kw: Any,
) -> Any:
    """`move --push` at the service level."""
    rec = rec or Recorder()
    return service(clone).move_item_push(
        item_id, new_status, sleep=rec.sleep, jitter=rec.jitter, seam=rec.seam, **kw
    )


def subject(repo: Path, sha: str) -> str:
    return git(repo, "log", "-1", "--format=%s", sha).strip()


def assert_one_item_commit(world: World, base: str, rel: str = ITEM) -> str:
    """Origin's tip is exactly one commit on `base`, touching only `rel`."""
    tip = world.remote_sha()
    assert git(world.remote, "rev-list", f"{base}..{tip}").split() == [tip], (
        "origin must get exactly one new commit"
    )
    assert commit_files(world.remote, tip) == [rel], "the move commit touched other files"
    return tip


def remote_status(world: World) -> str:
    return frontmatter(world.remote_show(ITEM))["status"]


def has_status_node(text: str, actor: str, status: str) -> bool:
    return any(
        re.search(rf"kb:status\s+kb:{status}\b", n) for n in nodes_by(text, actor)
    )


def b_seed(world: World, status: str, assignee: str | None = None) -> None:
    """B sets EXP-001 to `status`/`assignee` on origin; A's copy stays as it was."""
    b_push(world, {ITEM: item_text(status, assignee)})


# --- 1. basic: the move lands on origin --------------------------------------------------


def test_move_push_lands_on_origin_and_feature_checkout_untouched(world, monkeypatch) -> None:
    dirty_feature_branch(world)
    base = world.remote_sha()
    before = snapshot(world.a)

    result = invoke(
        world, monkeypatch, ["move", ITEM_ID, "in_progress", "--push", "--agent", A]
    )

    assert result.exit_code == 0, output_of(result)
    tip = assert_one_item_commit(world, base)
    text = world.remote_show(ITEM)
    assert frontmatter(text)["status"] == native_in_progress(world.a) == "underway"
    assert has_status_node(text, A, "in_progress"), text
    assert subject(world.remote, tip) == f"Move {ITEM_ID} to in_progress"
    assert snapshot(world.a) == before, "A's worktree, index or branch changed"


def test_move_push_on_clean_default_branch_fast_forwards(world, monkeypatch) -> None:
    assert git(world.a, "symbolic-ref", "--short", "HEAD").strip() == world.default

    result = invoke(
        world, monkeypatch, ["move", ITEM_ID, "in_progress", "--push", "--agent", A]
    )

    assert result.exit_code == 0, output_of(result)
    assert git(world.a, "rev-parse", "HEAD").strip() == world.remote_sha()
    assert (world.a / ITEM).read_text() == world.remote_show(ITEM)
    assert git(world.a, "status", "--porcelain", "--untracked-files=all") == ""


def test_move_push_assign_and_closed_by_land_with_suffix(world, monkeypatch) -> None:
    base = world.remote_sha()
    url = "https://github.com/x/y/pull/1251"

    result = invoke(world, monkeypatch, [
        "move", ITEM_ID, "in_progress", "--push", "--agent", A, "--assign", A,
        "--closed-by", url,
    ])

    assert result.exit_code == 0, output_of(result)
    tip = assert_one_item_commit(world, base)
    text = world.remote_show(ITEM)
    assert frontmatter(text)["assignee"] == A
    assert any(url in n and "kb:closedBy" in n for n in nodes_by(text, A)), text
    assert subject(world.remote, tip) == f"Move {ITEM_ID} to in_progress (assigned to {A})"


def test_move_push_custom_message(world, monkeypatch) -> None:
    result = invoke(world, monkeypatch, [
        "move", ITEM_ID, "in_progress", "--push", "--agent", A, "-m", "start EXP-001",
    ])

    assert result.exit_code == 0, output_of(result)
    assert subject(world.remote, world.remote_sha()) == "start EXP-001"


def test_service_move_item_push_won(world) -> None:
    base = world.remote_sha()
    rec = Recorder()

    out = move_push(world.a, WorkItemStatus.IN_PROGRESS, rec, actor=A)

    assert out.kind == "won", out.message
    assert out.exit_code == 0
    assert out.sha == world.remote_sha()
    assert_one_item_commit(world, base)
    assert rec.seams == [0] and rec.sleeps == []


# --- 2. lost race: retried on the new base -------------------------------------------------


def test_lost_race_to_title_edit_retries_and_keeps_both(world) -> None:
    def b_edits_title(attempt: int) -> None:
        if attempt == 0:
            out = service(world.b).update_item_push(ITEM_ID, title="B's title")
            assert out.kind == "won", out.message

    base = world.remote_sha()
    rec = Recorder(b_edits_title)

    out = move_push(world.a, WorkItemStatus.IN_PROGRESS, rec, actor=A)

    assert out.kind == "won", out.message
    assert rec.seams == [0, 1], "A did not retry on B's state"
    assert rec.sleeps == [pytest.approx(JITTER * 1)]
    history = git(world.remote, "rev-list", f"{base}..{world.remote_sha()}").split()
    assert len(history) == 2, history
    fm = frontmatter(world.remote_show(ITEM))
    assert fm["title"] == "B's title", "B's title edit was lost"
    assert fm["status"] == "underway", "A's move was lost"


def test_rival_moves_to_done_in_seam_refuses_retry(world) -> None:
    rec = Recorder(lambda attempt: b_seed(world, "done") if attempt == 0 else None)

    out = move_push(world.a, WorkItemStatus.IN_PROGRESS, rec, actor=A)

    assert out.kind == "refused", out.message
    assert out.exit_code == 1
    assert subject(world.remote, world.remote_sha()) == "rival", "A pushed after the rival"
    assert canonical_done(world)


def test_unrelated_commit_every_attempt_is_busy(world) -> None:
    rec = Recorder(lambda attempt: rival(world, attempt))

    out = move_push(world.a, WorkItemStatus.IN_PROGRESS, rec, actor=A)

    assert out.kind == "busy", out.message
    assert out.exit_code == 5
    assert remote_status(world) == "ready"


def canonical_done(world: World) -> bool:
    return remote_status(world) in ("done", native(world.a, WorkItemStatus.DONE))


# --- 3. the stale-read gap: legality and WIP judged on origin -------------------------------


def test_illegal_on_origin_is_refused_though_legal_locally(world, monkeypatch) -> None:
    b_seed(world, "done")  # A's copy still says ready
    assert frontmatter((world.a / ITEM).read_text())["status"] == "ready"
    base = world.remote_sha()
    before = snapshot(world.a)

    result = invoke(
        world, monkeypatch, ["move", ITEM_ID, "in_progress", "--push", "--agent", A]
    )

    assert result.exit_code == 1, output_of(result)
    assert world.remote_sha() == base, "an illegal move was pushed"
    assert snapshot(world.a) == before


def test_illegal_on_origin_passes_with_force(world, monkeypatch) -> None:
    b_seed(world, "done")
    base = world.remote_sha()

    result = invoke(world, monkeypatch, [
        "move", ITEM_ID, "in_progress", "--push", "--agent", A, "--force",
    ])

    assert result.exit_code == 0, output_of(result)
    tip = assert_one_item_commit(world, base)
    text = world.remote_show(ITEM)
    assert frontmatter(text)["status"] == "underway"
    assert has_status_node(text, A, "in_progress"), text
    assert subject(world.remote, tip) == f"Move {ITEM_ID} to in_progress (forced)"


def test_legal_on_origin_passes_though_illegal_locally(world, monkeypatch) -> None:
    b_seed(world, "in_progress")  # unheld; A's copy says ready, and ready -> review is illegal
    base = world.remote_sha()

    result = invoke(world, monkeypatch, ["move", ITEM_ID, "review", "--push", "--agent", A])

    assert result.exit_code == 0, output_of(result)
    assert_one_item_commit(world, base)
    assert remote_status(world) == native(world.a, WorkItemStatus.REVIEW)


def test_wip_full_on_origin_but_not_locally_is_refused(world, monkeypatch) -> None:
    push_from_a(world, {".kanban/config.yaml": WIP_CONFIG}, "board: wip 1")
    b_push(world, {OTHER: item_text("in_progress", B, OTHER_ID, "Y")})
    assert not (world.a / OTHER).exists(), "A must not see B's item locally"
    base = world.remote_sha()

    result = invoke(
        world, monkeypatch, ["move", ITEM_ID, "in_progress", "--push", "--agent", A]
    )

    out = output_of(result)
    assert result.exit_code == 1, out
    assert "wip" in out.lower(), out
    assert world.remote_sha() == base


def test_wip_limit_only_in_origins_config_is_refused(world, monkeypatch) -> None:
    b_push(world, {
        ".kanban/config.yaml": WIP_CONFIG,
        OTHER: item_text("in_progress", B, OTHER_ID, "Y"),
    })
    base = world.remote_sha()

    result = invoke(
        world, monkeypatch, ["move", ITEM_ID, "in_progress", "--push", "--agent", A]
    )

    out = output_of(result)
    assert result.exit_code == 1, out
    assert "wip" in out.lower(), out
    assert world.remote_sha() == base


def test_wip_full_on_origin_passes_with_force(world, monkeypatch) -> None:
    push_from_a(world, {".kanban/config.yaml": WIP_CONFIG}, "board: wip 1")
    b_push(world, {OTHER: item_text("in_progress", B, OTHER_ID, "Y")})
    base = world.remote_sha()

    result = invoke(world, monkeypatch, [
        "move", ITEM_ID, "in_progress", "--push", "--agent", A, "--force",
    ])

    assert result.exit_code == 0, output_of(result)
    assert_one_item_commit(world, base)
    assert remote_status(world) == "underway"


def test_wip_full_locally_but_not_on_origin_wins(world, monkeypatch) -> None:
    push_from_a(world, {".kanban/config.yaml": WIP_CONFIG}, "board: wip 1")
    (world.a / OTHER).write_text(item_text("in_progress", B, OTHER_ID, "Y"))  # untracked
    base = world.remote_sha()

    result = invoke(
        world, monkeypatch, ["move", ITEM_ID, "in_progress", "--push", "--agent", A]
    )

    assert result.exit_code == 0, output_of(result)
    assert_one_item_commit(world, base)


# --- 4. the holder guard, on origin's holder ------------------------------------------------


def test_held_by_b_on_origin_is_refused(world, monkeypatch) -> None:
    b_seed(world, "in_progress", B)  # A's copy: ready, unheld
    base = world.remote_sha()
    before = snapshot(world.a)

    result = invoke(world, monkeypatch, ["move", ITEM_ID, "review", "--push", "--agent", A])

    out = output_of(result)
    assert result.exit_code == 1, out
    assert re.search(r"held by agent-B", out, re.I), out
    assert world.remote_sha() == base
    assert snapshot(world.a) == before


def test_held_by_b_force_does_not_override(world, monkeypatch) -> None:
    b_seed(world, "in_progress", B)
    base = world.remote_sha()

    result = invoke(world, monkeypatch, [
        "move", ITEM_ID, "review", "--push", "--agent", A, "--force",
    ])

    assert result.exit_code == 1, output_of(result)
    assert world.remote_sha() == base


def test_take_over_on_origin_passes_and_records_it(world, monkeypatch) -> None:
    b_seed(world, "in_progress", B)
    base = world.remote_sha()

    result = invoke(world, monkeypatch, [
        "move", ITEM_ID, "review", "--push", "--agent", A, "--take-over",
    ])

    assert result.exit_code == 0, output_of(result)
    tip = assert_one_item_commit(world, base)
    text = world.remote_show(ITEM)
    fm = frontmatter(text)
    assert fm["assignee"] == A
    assert fm["status"] == native(world.a, WorkItemStatus.REVIEW)
    assert any(f'kb:takenOverFrom "{B}"' in n for n in nodes_by(text, A)), text
    assert subject(world.remote, tip) == (
        f"Move {ITEM_ID} to review (assigned to {A}) (taken over from {B})"
    )


def test_take_over_without_explicit_actor_is_refused(world, monkeypatch) -> None:
    b_seed(world, "in_progress", B)
    base = world.remote_sha()

    result = invoke(world, monkeypatch, ["move", ITEM_ID, "review", "--push", "--take-over"])

    assert result.exit_code == 1, output_of(result)
    assert world.remote_sha() == base


def test_own_hold_on_origin_moves(world, monkeypatch) -> None:
    b_seed(world, "in_progress", A)
    base = world.remote_sha()

    result = invoke(world, monkeypatch, ["move", ITEM_ID, "review", "--push", "--agent", A])

    assert result.exit_code == 0, output_of(result)
    assert_one_item_commit(world, base)


# --- 5. the halt, read from origin ------------------------------------------------------------


def test_halt_on_origin_refuses_move_to_in_progress(world, monkeypatch) -> None:
    b_push(world, {CONTROL: HALT_YAML})  # A has not fetched it
    base = world.remote_sha()
    before = snapshot(world.a)

    result = invoke(
        world, monkeypatch, ["move", ITEM_ID, "in_progress", "--push", "--agent", A]
    )

    out = output_of(result)
    assert result.exit_code == HALTED, out
    assert "board halted by agent-B" in out, out
    assert world.remote_sha() == base, "a halted move was pushed"
    assert snapshot(world.a) == before


def test_halt_service_outcome_is_halted(world) -> None:
    b_push(world, {CONTROL: HALT_YAML})

    out = move_push(world.a, WorkItemStatus.IN_PROGRESS, actor=A)

    assert out.kind == "refused", out.message
    assert out.halted is True


def test_halt_still_allows_move_to_done(world, monkeypatch) -> None:
    b_push(world, {ITEM: item_text("in_progress", A), CONTROL: HALT_YAML})
    base = world.remote_sha()

    result = invoke(world, monkeypatch, ["move", ITEM_ID, "done", "--push", "--agent", A])

    assert result.exit_code == 0, output_of(result)
    assert_one_item_commit(world, base)
    assert canonical_done(world)


# --- 6. resolution ---------------------------------------------------------------------------


def test_done_with_duplicate_resolution_lands(world, monkeypatch) -> None:
    push_from_a(world, {OTHER: item_text("ready", None, OTHER_ID, "Y")}, "EXP-002")
    b_seed(world, "in_progress", A)
    base = world.remote_sha()

    result = invoke(world, monkeypatch, [
        "move", ITEM_ID, "done", "--push", "--agent", A,
        "--resolution", "duplicate", "--superseded-by", OTHER_ID,
    ])

    assert result.exit_code == 0, output_of(result)
    assert_one_item_commit(world, base)
    text = world.remote_show(ITEM)
    fm = frontmatter(text)
    assert fm["resolution"] == "duplicate"
    assert fm["superseded_by"] == [OTHER_ID]
    assert any("kb:resolution" in n for n in nodes_by(text, A)), text


def test_superseded_by_target_only_on_origin_is_known(world, monkeypatch) -> None:
    b_push(world, {
        OTHER: item_text("ready", None, OTHER_ID, "Y"),
        ITEM: item_text("in_progress", A),
    })
    assert not (world.a / OTHER).exists()
    base = world.remote_sha()

    result = invoke(world, monkeypatch, [
        "move", ITEM_ID, "done", "--push", "--agent", A,
        "--resolution", "duplicate", "--superseded-by", OTHER_ID,
    ])

    assert result.exit_code == 0, output_of(result)
    assert_one_item_commit(world, base)
    assert frontmatter(world.remote_show(ITEM))["superseded_by"] == [OTHER_ID]


def test_duplicate_without_superseded_by_is_refused(world, monkeypatch) -> None:
    push_from_a(world, {OTHER: item_text("ready", None, OTHER_ID, "Y")}, "EXP-002")
    b_seed(world, "in_progress", A)
    base = world.remote_sha()

    result = invoke(world, monkeypatch, [
        "move", ITEM_ID, "done", "--push", "--agent", A, "--resolution", "duplicate",
    ])

    out = output_of(result)
    assert result.exit_code == 1, out
    assert "superseded-by" in out.lower(), out
    assert world.remote_sha() == base


# --- 7. gates ----------------------------------------------------------------------------------


def test_blocking_gate_refuses(world, monkeypatch) -> None:
    set_gates(world, GATE_FAILING)
    base = world.remote_sha()

    result = invoke(
        world, monkeypatch, ["move", ITEM_ID, "in_progress", "--push", "--agent", A]
    )

    out = output_of(result)
    assert result.exit_code == 1, out
    assert "Self-review gate 574b not satisfied" in out, out
    assert world.remote_sha() == base


@pytest.mark.parametrize("flag", ["--skip-gates", "--self-reviewed"])
def test_gate_passes_with_skip_or_self_review(world, monkeypatch, flag) -> None:
    set_gates(world, GATE_FAILING)
    base = world.remote_sha()

    result = invoke(
        world, monkeypatch, ["move", ITEM_ID, "in_progress", "--push", "--agent", A, flag]
    )

    assert result.exit_code == 0, output_of(result)
    assert_one_item_commit(world, base)
    assert remote_status(world) == "underway"


# --- 8. noop -------------------------------------------------------------------------------------


def test_move_to_origins_current_status_is_noop(world, monkeypatch) -> None:
    b_seed(world, "review")  # A's copy says ready
    base = world.remote_sha()
    before = snapshot(world.a)

    result = invoke(world, monkeypatch, ["move", ITEM_ID, "review", "--push", "--agent", A])

    assert result.exit_code == 0, output_of(result)
    assert world.remote_sha() == base, "a noop pushed"
    assert snapshot(world.a) == before


def test_noop_service_outcome(world) -> None:
    b_seed(world, "review")

    out = move_push(world.a, WorkItemStatus.REVIEW, actor=A)

    assert out.kind == "noop", out.message
    assert out.exit_code == 0
    assert out.sha is None


# --- items: only on origin, only local, unknown ----------------------------------------------------


def test_item_only_on_origin_can_be_moved(world, monkeypatch) -> None:
    b_push(world, {OTHER: item_text("ready", None, OTHER_ID, "Y")})
    assert not (world.a / OTHER).exists()
    base = world.remote_sha()

    result = invoke(
        world, monkeypatch, ["move", OTHER_ID, "in_progress", "--push", "--agent", A]
    )

    assert result.exit_code == 0, output_of(result)
    assert_one_item_commit(world, base, OTHER)
    assert frontmatter(world.remote_show(OTHER))["status"] == "underway"


def test_item_only_in_local_tree_is_refused(world, monkeypatch) -> None:
    rel = f"{EXP_DIR}/EXP-006-six.md"
    (world.a / rel).write_text(item_text("ready", None, "EXP-006", "Six"))
    git(world.a, "add", rel)
    git(world.a, "commit", "-m", "EXP-006, local only")
    base = world.remote_sha()

    result = invoke(world, monkeypatch, ["move", "EXP-006", "in_progress", "--push", "--agent", A])

    out = output_of(result)
    assert result.exit_code == 1, out
    assert "EXP-006" in out, out
    assert world.remote_sha() == base


def test_unknown_item_is_refused(world, monkeypatch) -> None:
    base = world.remote_sha()

    result = invoke(world, monkeypatch, ["move", "EXP-099", "in_progress", "--push", "--agent", A])

    assert result.exit_code == 1, output_of(result)
    assert world.remote_sha() == base


# --- 9. contradictory flags ------------------------------------------------------------------------


@pytest.mark.parametrize("extra", [
    ["--no-commit"],
    ["--export-board", "kanban-work/KANBAN-BOARD.md"],
])
def test_push_with_no_commit_or_export_board_is_refused(world, monkeypatch, extra) -> None:
    base = world.remote_sha()
    before = snapshot(world.a)

    result = invoke(world, monkeypatch, [
        "move", ITEM_ID, "in_progress", "--push", "--agent", A, *extra,
    ])

    out = output_of(result)
    assert result.exit_code in (1, 2), out
    assert "no such option" not in out.lower(), f"move has no --push yet: {out}"
    assert "--push" in out and extra[0] in out, f"the refusal must name both flags: {out}"
    assert world.remote_sha() == base
    assert snapshot(world.a) == before


# --- exit codes 4 and 6 ----------------------------------------------------------------------------


def test_unreachable_remote_exits_4(world, monkeypatch) -> None:
    git(world.a, "remote", "set-url", "origin", str(world.a.parent / "missing.git"))
    dirty_feature_branch(world)
    before = snapshot(world.a)

    result = invoke(
        world, monkeypatch, ["move", ITEM_ID, "in_progress", "--push", "--agent", A]
    )

    assert result.exit_code == 4, output_of(result)
    assert snapshot(world.a) == before, "an unreachable remote must not fall back to local"


def test_declining_update_hook_exits_6(world, monkeypatch) -> None:
    hook = world.remote / "hooks" / "update"
    hook.write_text("#!/bin/sh\necho 'kanban-guard: frozen' >&2\nexit 1\n")
    hook.chmod(0o755)
    base = world.remote_sha()

    result = invoke(
        world, monkeypatch, ["move", ITEM_ID, "in_progress", "--push", "--agent", A]
    )

    assert result.exit_code == 6, output_of(result)
    assert world.remote_sha() == base


# --- 10. no remote -------------------------------------------------------------------------------------


def test_no_remote_commits_locally(world, monkeypatch) -> None:
    git(world.a, "remote", "remove", "origin")
    (world.a / "staged.txt").write_text("user staged work\n")
    git(world.a, "add", "staged.txt")
    head_before = git(world.a, "rev-parse", "HEAD").strip()

    result = invoke(
        world, monkeypatch, ["move", ITEM_ID, "in_progress", "--push", "--agent", A]
    )

    out = output_of(result)
    assert result.exit_code == 0, out
    assert "no remote" in out.lower(), out
    head = git(world.a, "rev-parse", "HEAD").strip()
    assert git(world.a, "rev-list", f"{head_before}..{head}").split() == [head]
    assert commit_files(world.a, head) == [ITEM]
    assert frontmatter(git(world.a, "show", f"HEAD:{ITEM}"))["status"] == "underway"
    assert "staged.txt" in git(world.a, "diff", "--cached", "--name-only").split()


def test_no_remote_service_outcome_is_local(world) -> None:
    git(world.a, "remote", "remove", "origin")

    out = move_push(world.a, WorkItemStatus.IN_PROGRESS, actor=A)

    assert out.kind == "local", out.message
    assert out.exit_code == 0


# --- 11. hooks -------------------------------------------------------------------------------------------


def status_changes(marker: Path) -> list[str]:
    return [line for line in fired(marker) if line.startswith("on_status_change ")]


def test_hooks_fire_once_after_won_push(world, monkeypatch, tmp_path) -> None:
    marker = install_hooks(world, tmp_path)

    result = invoke(world, monkeypatch, [
        "move", ITEM_ID, "in_progress", "--push", "--agent", A, "--assign", A,
    ])

    assert result.exit_code == 0, output_of(result)
    assert sorted(fired(marker)) == sorted([
        f"on_status_change {ITEM_ID} in_progress {A}",
        f"on_assign {ITEM_ID} in_progress {A}",
    ]), fired(marker)


def test_hooks_fire_once_after_a_retried_win(world, tmp_path) -> None:
    marker = install_hooks(world, tmp_path)
    rec = Recorder(lambda attempt: rival(world) if attempt == 0 else None)

    out = move_push(world.a, WorkItemStatus.IN_PROGRESS, rec, actor=A)

    assert out.kind == "won", out.message
    assert len(status_changes(marker)) == 1, fired(marker)


def test_hooks_do_not_fire_on_refusal(world, monkeypatch, tmp_path) -> None:
    b_seed(world, "done")
    marker = install_hooks(world, tmp_path)

    result = invoke(
        world, monkeypatch, ["move", ITEM_ID, "in_progress", "--push", "--agent", A]
    )

    assert result.exit_code == 1, output_of(result)
    assert fired(marker) == []


# --- 12. control and help ---------------------------------------------------------------------------------


def test_control_plain_move_commits_locally_without_pushing(world, monkeypatch) -> None:
    base = world.remote_sha()

    result = invoke(world, monkeypatch, ["move", ITEM_ID, "in_progress", "--agent", A])

    assert result.exit_code == 0, output_of(result)
    assert world.remote_sha() == base, "move without --push pushed"
    assert commit_files(world.a, "HEAD") == [ITEM]
    assert subject(world.a, "HEAD") == f"Move {ITEM_ID} to in_progress"


def test_move_help_lists_push() -> None:
    result = CliRunner().invoke(main, ["move", "--help"])

    assert result.exit_code == 0
    assert "--push" in result.output, result.output


def test_control_seed_helper_sets_origin(world) -> None:
    """`seed` (A pushes) and `b_seed` (B pushes) both reach origin."""
    seed(world, "in_progress", A)
    assert frontmatter(world.remote_show(ITEM))["assignee"] == A
    b_seed(world, "review")
    assert remote_status(world) == "review"
    assert frontmatter((world.a / ITEM).read_text())["status"] == "in_progress"

