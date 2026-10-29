"""Issue #1287: ``release``, give a claim back (the inverse of ``claim``).

Spec (the issue's Proposal and Done when):

- CLI ``yurtle-kanban release ID --agent HOLDER [--reason TEXT | --reason-file
  PATH|-] [--to STATUS] [--take-over]``.
- One compare-and-swap commit on origin's default branch, as ``claim`` and
  ``bounce`` make; the caller's worktree is untouched unless it is clean and on the
  default branch.
- The item moves from in progress to its theme's ready status (default), or to
  another non-terminal status given with ``--to``. ``assignee:`` becomes ``null``,
  the status-change block records ``kb:releasedBy`` / ``kb:releasedAt``, and the
  reason is added as a comment when one is given.
- No bounce lock: no ``bounce_sha``; the item is pickable at once by any agent.
- Only the holder may release, unless ``--take-over`` (recorded as
  ``kb:takenOverFrom``). A finished item is refused.
- Exit codes as ``claim``/``bounce``: 0 released or already unassigned, 1 refused,
  3 lost race, 4/5/6 remote, 8 halted.
- Done when: documented in ``--help``, the CLI reference (README's command table)
  and ``UPGRADING.md``; the stock ``/handoff`` skill uses it.

Readings the test partner chose (the driver may challenge them):

a. Service API: ``KanbanService.release_item(item_id, *, actor, reason=None,
   to=None, take_over=False, sleep, jitter, seam) -> Outcome``, mirroring
   ``claim_item``/``bounce_item``; ``to`` is a status name as the CLI takes it.
   Only the race tests and one direct test call it; everything else goes through
   the CLI.
b. ``kb:releasedBy "<actor>"`` and ``kb:releasedAt "<ISO-8601>"`` sit in ONE
   status-history ``[ ... ]`` node, the one that records ``kb:status kb:ready``
   (canonical, as every history node writes it). With ``--take-over``,
   ``kb:takenOverFrom "<old holder>"`` sits in that same node.
c. "``assignee:`` to ``null``": the frontmatter key is present and YAML-null.
d. "already unassigned" (exit 0, nothing written) is pinned for an item that is
   ready and unassigned. An unassigned item IN PROGRESS is not pinned.
e. A ready item that the actor holds (pre-assigned) is released too: assignee
   cleared, still ready, one commit.
f. The halt (driver's ruling): a halt stops new work, but a holder may still
   finish (``control --help``), so the HOLDER's release under a halt goes through
   (exit 0); ``release --take-over`` under a halt is refused with exit 8, as
   ``claim --take-over`` is.
g. ``--to`` is tested with ``blocked`` (legal from in progress in the nautical
   table) and refused with ``done``. Whether ``--to`` answers to the transition
   table otherwise is not pinned.
h. The reason comment: the reason text appears in the item's ``## Comments``
   section. Its prefix/format is not pinned. Without a reason no comment is added.
i. ``--reason`` with ``--reason-file`` is a usage error (exit 2), as for bounce
   (``read_text_option``'s contract).
j. The races (Done when: "a concurrent claim and release race resolves through CAS
   with no lost write"):
   - B's ``claim --take-over`` lands in A's release seam: A loses (exit 3, "lost to
     agent-B"), origin holds B's take-over only;
   - A's release lands in B's ``claim --take-over`` seam: B retries and wins on the
     released item; origin holds BOTH commits (release, then claim);
   - an unrelated commit in A's seam: A retries and wins, both commits on origin.
k. ``/handoff``: both stock handoff skills (software, nautical) contain a
   ``yurtle-kanban release <ID>`` line.
l. A CLI refusal must be a clean exit (``SystemExit``), never an uncaught exception,
   so a stub that raises can never pass a refusal test.
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner, Result

from tests.issues.test_574_claim import (
    ITEM,
    ITEM_ID,
    A,
    B,
    claim,
    frontmatter,
    history_nodes,
    seed,
    service,
)
from tests.issues.test_574_sync_and_push import (
    Recorder,
    commit_files,
    dirty_feature_branch,
    rival,
    snapshot,
)
from tests.issues.test_578_bounce import seam_on_a
from tests.issues.test_582_control_halt import hand_halt
from tests.issues.test_585_create_push_loop import World, git
from yurtle_kanban.cli import main
from yurtle_kanban.models import WorkItemStatus

pytestmark = pytest.mark.usefixtures("claim_env")

REPO = Path(__file__).resolve().parents[2]
REASON = "Needs a CUDA host; this session has none."
BOUNCE_KEYS = ("bounce_sha", "bounced_by", "bounced_at", "bounces")


# --- harness ---------------------------------------------------------------------


def flat(text: str | None) -> str:
    return " ".join((text or "").split())


def run_cli(cwd: Path, monkeypatch: pytest.MonkeyPatch, argv: list[str], **kw: Any) -> Result:
    monkeypatch.chdir(cwd)
    return CliRunner().invoke(main, argv, **kw)


def release(
    world: World, monkeypatch: pytest.MonkeyPatch, *extra: str, agent: str | None = A,
    clone: Path | None = None, **kw: Any,
) -> Result:
    argv = ["release", ITEM_ID, *extra]
    if agent is not None:
        argv += ["--agent", agent]
    return run_cli(clone or world.a, monkeypatch, argv, **kw)


def ok(result: Result) -> None:
    assert result.exit_code == 0, (
        f"exit {result.exit_code}: {result.exception!r}\n{result.output}"
    )


def clean_exit(result: Result, code: int) -> str:
    """Exit `code` through a clean exit, never an uncaught exception (reading l)."""
    out = flat(result.output)
    assert result.exception is None or isinstance(result.exception, SystemExit), (
        f"uncaught {result.exception!r}: {out}"
    )
    assert result.exit_code == code, f"exit {result.exit_code}, not {code}: {out}"
    return out


def native(clone: Path, status: WorkItemStatus) -> str:
    svc = service(clone)
    item = svc.get_item(ITEM_ID)
    assert item is not None
    return svc.status_label(replace(item, status=status))


def sync(clone: Path, world: World) -> None:
    git(clone, "fetch", "origin")
    git(clone, "reset", "--hard", f"origin/{world.default}")


def one_commit_of_the_item(world: World, base: str) -> str:
    tip = world.remote_sha()
    assert git(world.remote, "rev-list", f"{base}..{tip}").split() == [tip], (
        "the release must be exactly one new commit on origin's default branch"
    )
    assert commit_files(world.remote, tip) == [ITEM], "the release touched other files"
    return tip


def release_node(text: str, actor: str = A) -> str:
    """The one history node recording `actor`'s release (reading b)."""
    nodes = [n for n in history_nodes(text) if f'kb:releasedBy "{actor}"' in n]
    assert len(nodes) == 1, f'want one node with kb:releasedBy "{actor}", got {len(nodes)}:\n{text}'
    node = nodes[0]
    m = re.search(r'kb:releasedAt\s+"([^"]+)"', node)
    assert m, f"no kb:releasedAt in the release node:\n{node}"
    datetime.fromisoformat(m.group(1))  # an ISO-8601 stamp
    return node


def comments_section(text: str) -> str:
    m = re.search(r"^## Comments[ \t]*$(.*)\Z", text, re.S | re.M)
    assert m, f"no ## Comments section:\n{text}"
    return m.group(1)


def assert_released(world: World, base: str, status: WorkItemStatus = WorkItemStatus.READY,
                    actor: str = A) -> str:
    one_commit_of_the_item(world, base)
    text = world.remote_show(ITEM)
    fm = frontmatter(text)
    assert fm["status"] == native(world.a, status), fm
    assert "assignee" in fm and fm["assignee"] is None, f"assignee not null: {fm}"
    for key in BOUNCE_KEYS:
        assert key not in fm, f"a release wrote the bounce stamp {key}: {fm}"
    node = release_node(text, actor)
    assert re.search(rf"kb:status\s+kb:{status.value}\b", node), node
    return text


# --- the command exists and is documented ----------------------------------------------


def test_release_help_names_its_options() -> None:
    result = CliRunner().invoke(main, ["release", "--help"])
    ok(result)
    out = flat(result.output)
    for option in ("--agent", "--reason", "--reason-file", "--to", "--take-over"):
        assert option in out, f"{option} missing from release --help:\n{out}"


def test_cli_reference_lists_release() -> None:
    text = (REPO / "README.md").read_text(encoding="utf-8")
    assert re.search(r"^\|\s*`release`\s*\|", text, re.M), (
        "README's command table has no `release` row"
    )


def test_upgrading_mentions_release() -> None:
    text = (REPO / "UPGRADING.md").read_text(encoding="utf-8")
    assert re.search(r"`(?:yurtle-kanban )?release\b[^`]*`", text), (
        "UPGRADING.md does not document `release`"
    )


@pytest.mark.parametrize("theme", ["software", "nautical"])
def test_handoff_skill_uses_release(theme) -> None:
    text = (REPO / "skills" / theme / "handoff" / "SKILL.md").read_text(encoding="utf-8")
    assert re.search(r"yurtle-kanban release \S+", text), (
        f"{theme} handoff skill does not use `yurtle-kanban release`"
    )


# --- 1. the holder's release ---------------------------------------------------------------


def test_holder_releases_in_progress_item(world, monkeypatch) -> None:
    seed(world, "in_progress", A)
    base = world.remote_sha()

    ok(release(world, monkeypatch))

    text = assert_released(world, base)
    assert "takenOverFrom" not in text, "a plain release recorded a take-over"
    assert "## Comments" not in text, "a release without a reason added a comment"


def test_release_with_reason_adds_comment(world, monkeypatch) -> None:
    seed(world, "in_progress", A)
    base = world.remote_sha()

    ok(release(world, monkeypatch, "--reason", REASON))

    text = assert_released(world, base)
    assert REASON in comments_section(text), text


def test_release_reason_file_from_stdin(world, monkeypatch) -> None:
    seed(world, "in_progress", A)
    base = world.remote_sha()

    ok(release(world, monkeypatch, "--reason-file", "-", input="From stdin: waits on input.\n"))

    text = assert_released(world, base)
    assert "From stdin: waits on input." in comments_section(text), text


def test_release_reason_file_from_path(world, monkeypatch, tmp_path) -> None:
    seed(world, "in_progress", A)
    path = tmp_path / "reason.txt"
    path.write_text("From a file: needs a GPU.\n")

    ok(release(world, monkeypatch, "--reason-file", str(path)))

    assert "From a file: needs a GPU." in comments_section(world.remote_show(ITEM))


def test_both_reason_options_is_a_usage_error(world, monkeypatch) -> None:
    seed(world, "in_progress", A)
    base = world.remote_sha()

    result = release(world, monkeypatch, "--reason", "x", "--reason-file", "-", input="y\n")

    out = clean_exit(result, 2)
    assert "--reason" in out, out
    assert world.remote_sha() == base


def test_holder_releases_preassigned_ready_item(world, monkeypatch) -> None:
    seed(world, "ready", A)
    base = world.remote_sha()

    ok(release(world, monkeypatch))

    assert_released(world, base)


def test_release_from_feature_branch_leaves_worktree_alone(world, monkeypatch) -> None:
    seed(world, "in_progress", A)
    dirty_feature_branch(world)
    base = world.remote_sha()
    before = snapshot(world.a)

    ok(release(world, monkeypatch))

    assert_released(world, base)
    assert snapshot(world.a) == before, "release touched a dirty feature-branch checkout"
    assert git(world.remote, "for-each-ref", "--format=%(refname)", "refs/heads").split() == [
        f"refs/heads/{world.default}"
    ], "the release went to a branch other than origin's default"


def test_service_release_item_wins(world) -> None:
    seed(world, "in_progress", A)
    base = world.remote_sha()
    rec = Recorder()

    out = service(world.a).release_item(
        ITEM_ID, actor=A, sleep=rec.sleep, jitter=rec.jitter, seam=rec.seam,
    )

    assert out.kind == "won", out.message
    assert out.exit_code == 0
    assert out.sha == world.remote_sha()
    assert_released(world, base)
    assert rec.seams == [0]


# --- 2. pickable and claimable by another agent at once ---------------------------------------


def test_released_item_is_pickable_and_claimable_by_another(world, monkeypatch) -> None:
    seed(world, "in_progress", A)
    sync(world.b, world)
    listed = run_cli(world.b, monkeypatch, ["list", "--pickable", "--agent", B])
    assert ITEM_ID not in flat(listed.output), (
        f"fixture: B must not see A's item as pickable before the release: {flat(listed.output)}"
    )

    ok(release(world, monkeypatch, "--reason", REASON))

    sync(world.b, world)
    listed = run_cli(world.b, monkeypatch, ["list", "--pickable", "--agent", B])
    ok(listed)
    assert ITEM_ID in flat(listed.output), (
        f"the released item is not pickable for {B}: {flat(listed.output)}"
    )
    svc = service(world.b)
    okay, why = svc.pickable(svc.get_item(ITEM_ID), B)
    assert okay, why

    claimed = run_cli(world.b, monkeypatch, ["claim", ITEM_ID, "--agent", B])
    ok(claimed)
    assert frontmatter(world.remote_show(ITEM))["assignee"] == B


# --- 3. who may release ----------------------------------------------------------------------


def test_non_holder_release_is_refused(world, monkeypatch) -> None:
    seed(world, "in_progress", B)
    base = world.remote_sha()
    before = snapshot(world.a)

    result = release(world, monkeypatch)

    out = clean_exit(result, 1)
    assert re.search(r"held by agent-B", out, re.I), out
    assert world.remote_sha() == base
    assert snapshot(world.a) == before
    assert frontmatter(world.remote_show(ITEM))["assignee"] == B


def test_non_holder_release_with_take_over(world, monkeypatch) -> None:
    seed(world, "in_progress", B)
    base = world.remote_sha()

    ok(release(world, monkeypatch, "--take-over"))

    text = assert_released(world, base)
    node = release_node(text)
    assert f'kb:takenOverFrom "{B}"' in node, (
        f'the release node does not record kb:takenOverFrom "{B}":\n{node}'
    )


def test_release_without_actor_is_refused(world, monkeypatch) -> None:
    seed(world, "in_progress", A)
    assert git(world.a, "config", "user.name").strip(), "fixture: A has a git user.name"
    base = world.remote_sha()

    result = release(world, monkeypatch, agent=None)

    out = clean_exit(result, 1)
    assert "--agent" in out and "YURTLE_AGENT" in out, out
    assert world.remote_sha() == base


# --- 4. finished items; already unassigned ---------------------------------------------------


@pytest.mark.parametrize("take_over", [False, True])
def test_release_of_done_item_is_refused(world, monkeypatch, take_over) -> None:
    seed(world, "done", A)
    base = world.remote_sha()

    result = release(world, monkeypatch, *(["--take-over"] if take_over else []))

    out = clean_exit(result, 1)
    assert ITEM_ID in out, out
    assert world.remote_sha() == base
    assert frontmatter(world.remote_show(ITEM))["assignee"] == A


def test_release_of_unassigned_ready_item_changes_nothing(world, monkeypatch) -> None:
    # the `world` fixture: EXP-001 ready and unassigned on origin and in A
    base = world.remote_sha()
    before = snapshot(world.a)
    seen = seam_on_a(monkeypatch, world, lambda attempt: None)

    result = release(world, monkeypatch)

    clean_exit(result, 0)
    assert world.remote_sha() == base, "releasing an unassigned item made a commit"
    assert seen == [], "a push was attempted for a no-op release"
    assert snapshot(world.a) == before


# --- 5. --to ----------------------------------------------------------------------------------


def test_release_to_another_non_terminal_status(world, monkeypatch) -> None:
    seed(world, "in_progress", A)
    base = world.remote_sha()

    ok(release(world, monkeypatch, "--to", "blocked"))

    assert_released(world, base, status=WorkItemStatus.BLOCKED)


def test_release_to_a_native_status_name(world, monkeypatch) -> None:
    seed(world, "in_progress", A)
    base = world.remote_sha()

    ok(release(world, monkeypatch, "--to", native(world.a, WorkItemStatus.BLOCKED)))

    assert_released(world, base, status=WorkItemStatus.BLOCKED)


def test_release_to_terminal_status_is_refused(world, monkeypatch) -> None:
    seed(world, "in_progress", A)
    base = world.remote_sha()

    result = release(world, monkeypatch, "--to", "done")

    clean_exit(result, 1)
    assert world.remote_sha() == base
    fm = frontmatter(world.remote_show(ITEM))
    assert fm["assignee"] == A and fm["status"] == "in_progress", fm


# --- 7. races through CAS ------------------------------------------------------------------------


def test_release_loses_to_a_take_over_in_its_seam(world, monkeypatch) -> None:
    seed(world, "in_progress", A)
    base = world.remote_sha()

    def b_takes_over(attempt: int) -> None:
        if attempt == 0:
            out = claim(world.b, B, take_over=True)
            assert out.kind == "won", f"B's take-over: {out.kind}: {out.message}"

    seen = seam_on_a(monkeypatch, world, b_takes_over)
    result = release(world, monkeypatch)

    out = clean_exit(result, 3)
    assert re.search(r"lost to agent-B", out, re.I), out
    assert seen == [0], f"A pushed again after reading B's take-over: {seen}"
    tip = world.remote_sha()
    assert git(world.remote, "rev-list", f"{base}..{tip}").split() == [tip], (
        "origin must hold B's take-over and nothing else"
    )
    text = world.remote_show(ITEM)
    assert frontmatter(text)["assignee"] == B
    assert "kb:releasedBy" not in text, "A's losing release reached origin"


def test_take_over_retries_after_a_release_in_its_seam(world, monkeypatch) -> None:
    seed(world, "in_progress", A)
    base = world.remote_sha()
    rec = Recorder(lambda attempt: ok(release(world, monkeypatch)) if attempt == 0 else None)

    out = claim(world.b, B, rec, take_over=True)

    assert out.kind == "won", f"{out.kind}: {out.message}"
    history = git(world.remote, "rev-list", f"{base}..{world.remote_sha()}").split()
    assert len(history) == 2, f"want A's release and B's claim on origin: {history}"
    claim_sha, release_sha = history
    released = git(world.remote, "show", f"{release_sha}:{ITEM}")
    assert 'kb:releasedBy "agent-A"' in released, "A's release is not the first commit"
    assert frontmatter(released)["assignee"] is None
    text = world.remote_show(ITEM)
    assert frontmatter(text)["assignee"] == B
    release_node(text)  # the release record survives B's claim: no lost write
    assert rec.seams == [0, 1], rec.seams


def test_release_retries_past_an_unrelated_commit(world, monkeypatch) -> None:
    seed(world, "in_progress", A)
    base = world.remote_sha()
    seen = seam_on_a(monkeypatch, world, lambda attempt: rival(world) if attempt == 0 else None)

    ok(release(world, monkeypatch))

    history = git(world.remote, "rev-list", f"{base}..{world.remote_sha()}").split()
    assert len(history) == 2, history
    ours, theirs = history
    assert commit_files(world.remote, theirs) == ["other-0.txt"]
    assert commit_files(world.remote, ours) == [ITEM]
    assert seen == [0, 1], seen
    fm = frontmatter(world.remote_show(ITEM))
    assert fm["assignee"] is None and fm["status"] == native(world.a, WorkItemStatus.READY), fm


# --- 8. exit codes: halted, unreachable, no remote ------------------------------------------------


def test_holder_release_on_a_halted_board_is_allowed(world, monkeypatch) -> None:
    """Driver's ruling: a halt stops new work, but a holder may still finish
    (control --help: "move its item to ... ready"), so the holder's release goes
    through as normal."""
    seed(world, "in_progress", A)
    hand_halt(world)
    sync(world.a, world)
    base = world.remote_sha()

    result = release(world, monkeypatch)

    clean_exit(result, 0)
    assert_released(world, base)


def test_take_over_release_on_a_halted_board_exits_8(world, monkeypatch) -> None:
    """Driver's ruling: --take-over under a halt is refused, exit 8, as
    claim --take-over is."""
    seed(world, "in_progress", B)
    hand_halt(world)
    sync(world.a, world)
    base = world.remote_sha()

    result = release(world, monkeypatch, "--take-over")

    out = clean_exit(result, 8)
    assert "halted" in out.lower(), out
    assert world.remote_sha() == base
    assert frontmatter(world.remote_show(ITEM))["assignee"] == B


def test_release_with_unreachable_origin_exits_4(world, monkeypatch, tmp_path) -> None:
    seed(world, "in_progress", A)
    git(world.a, "remote", "set-url", "origin", str(tmp_path / "missing.git"))
    before = snapshot(world.a)

    result = release(world, monkeypatch)

    clean_exit(result, 4)
    assert snapshot(world.a) == before


def test_release_with_no_remote_commits_locally(world, monkeypatch) -> None:
    seed(world, "in_progress", A)
    git(world.a, "remote", "remove", "origin")
    head_before = git(world.a, "rev-parse", "HEAD").strip()

    result = release(world, monkeypatch)

    out = clean_exit(result, 0)
    assert "no remote" in out.lower(), out
    head = git(world.a, "rev-parse", "HEAD").strip()
    assert git(world.a, "rev-list", f"{head_before}..{head}").split() == [head]
    assert commit_files(world.a, head) == [ITEM]
    fm = frontmatter(git(world.a, "show", f"HEAD:{ITEM}"))
    assert fm["assignee"] is None, fm
