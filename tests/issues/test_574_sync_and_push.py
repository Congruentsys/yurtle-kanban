"""Issue #574, PR A — ``sync_and_push``: the one kanban push helper.

The [steer] on #574 splits the build into four PRs; this is A. Spec §2 (design A of
the adversarial review): a worktree-free compare-and-swap onto origin's default branch.

API under test (``yurtle_kanban/sync.py`` + ``KanbanService.sync_and_push``):

- ``ExitCode``: OK=0, REFUSED=1, LOST=3, UNREACHABLE=4, BUSY=5, PUSH_REFUSED=6.
- ``Outcome(kind, message, sha=None, attempts=0, data=None)``; ``exit_code`` maps
  won/local/noop->0, refused->1, lost->3, unreachable->4, busy->5, push_refused->6.
- ``mutate(read, attempt) -> Change(files, message, data) | NoOp(message) |
  Refuse(message, holder=None)``; ``read(relpath)`` is the base's text
  (``origin/<default>``, or the working tree when there is no remote).
- ``sync_and_push(mutate, *, sleep, jitter, seam, attempts=5)``; ``seam(attempt)``
  runs after the commit is built and before the push.

Scenarios (real git: a bare remote plus clones, the #585 ``World``; interleavings
are deterministic through ``seam``, no real sleeps, no threads):

1. Accepted -> "won": one new commit on origin touching only the item; from a
   feature branch with a dirty tree and a staged unrelated file, the local HEAD,
   index, tree and branches are byte-identical afterwards.
2. An unrelated commit lands on attempt 0 only -> retry and win; origin history is
   rival then ours; mutate saw the new state on attempt 1; one jittered sleep.
3. An unrelated commit lands on every attempt -> "busy" (exit 5) after 5 attempts.
4. Origin points at a missing path -> "unreachable" (exit 4), nothing committed.
5. The remote's ``update`` hook exits 1 -> "push_refused" (exit 6) after ONE push,
   with the hook's stderr in the message.
6. No remote -> "local": the file is changed and committed alone; a staged
   unrelated file stays staged. NoOp/Refuse in local mode commit nothing.
7. Refuse on attempt 0 -> "refused" (exit 1); a lost attempt 0 followed by
   ``Refuse(holder=...)`` -> "lost" (exit 3) naming the holder; without a holder
   -> "refused".
8. NoOp -> "noop" (exit 0), nothing pushed.
9. "won" on the default branch with a clean tree fast-forwards the checkout; when
   it can't (local commit ahead), the checkout is untouched and the message says so.
10. The default branch is origin/HEAD's target: a ``master`` remote gets the commit.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tests.issues._snapshot import paths_outside_git
from tests.issues.test_585_create_push_loop import EXP_DIR, FEATURE, World, git
from tests.issues.test_590_next_id_and_hdd_ids import b_push
from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.service import KanbanService

ITEM = f"{EXP_DIR}/EXP-001-x.md"
ITEM_TEXT = (
    '---\nid: EXP-001\ntitle: "X"\ntype: expedition\nstatus: backlog\n---\n\n# X\n'
)
LINE = "appended by A"
JITTER = 0.37


# --- harness ---------------------------------------------------------------------


@pytest.fixture
def sync() -> ModuleType:
    """The module under test, imported per test so each test is red on its own."""
    return importlib.import_module("yurtle_kanban.sync")


def _seed_item(world: World) -> None:
    path = world.a / ITEM
    path.write_text(ITEM_TEXT)
    git(world.a, "add", ITEM)
    git(world.a, "commit", "-m", "seed EXP-001")
    git(world.a, "push", "origin", f"HEAD:refs/heads/{world.default}")
    git(world.b, "fetch", "origin")


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    w = World(tmp_path)
    _seed_item(w)
    return w


def service(world: World) -> KanbanService:
    return KanbanService(KanbanConfig.load(world.a / ".kanban" / "config.yaml"), world.a)


class Recorder:
    """Records sleep/jitter/seam calls; jitter returns a fixed value."""

    def __init__(self, seam_action: Any = None) -> None:
        self.sleeps: list[float] = []
        self.jitters: list[tuple[float, float]] = []
        self.seams: list[int] = []
        self.seam_action = seam_action

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)

    def jitter(self, lo: float, hi: float) -> float:
        self.jitters.append((lo, hi))
        return JITTER

    def seam(self, attempt: int) -> None:
        self.seams.append(attempt)
        if self.seam_action is not None:
            self.seam_action(attempt)


class Appender:
    """A generic mutate: append LINE to the item; records what it read per attempt."""

    def __init__(self, sync: ModuleType, also_read: str | None = None) -> None:
        self.sync = sync
        self.also_read = also_read
        self.seen: list[tuple[int, str | None, str | None]] = []

    def __call__(self, read: Any, attempt: int) -> Any:
        text = read(ITEM)
        other = read(self.also_read) if self.also_read else None
        self.seen.append((attempt, text, other))
        assert text is not None, f"read({ITEM!r}) returned None"
        return self.sync.Change({ITEM: text + LINE + "\n"}, f"append to EXP-001 ({attempt})")


def run(world: World, mutate: Any, rec: Recorder, **kw: Any) -> Any:
    return service(world).sync_and_push(
        mutate, sleep=rec.sleep, jitter=rec.jitter, seam=rec.seam, **kw
    )


def rival(world: World, n: int = 0) -> None:
    """B lands an unrelated commit on origin/main."""
    b_push(world, {f"other-{n}.txt": f"rival {n}\n"})


def snapshot(clone: Path) -> dict[str, Any]:
    """HEAD, current branch, local branches, index entries, status and every tree
    file's bytes (outside .git)."""
    files = {
        str(p.relative_to(clone)): p.read_bytes()
        for p in sorted(paths_outside_git(clone))
        if p.is_file()
    }
    return {
        "head": git(clone, "rev-parse", "HEAD").strip(),
        "branch": git(clone, "symbolic-ref", "--short", "HEAD", check=False).strip(),
        "heads": git(clone, "for-each-ref", "refs/heads"),
        "index": git(clone, "ls-files", "--stage"),
        "status": git(clone, "status", "--porcelain", "--untracked-files=all"),
        "files": files,
    }


def all_refs(clone: Path) -> str:
    return git(clone, "for-each-ref")


def dirty_feature_branch(world: World) -> None:
    """A on a feature branch, an unstaged edit, a staged unrelated file, an untracked one."""
    git(world.a, "checkout", "-b", FEATURE)
    (world.a / "README.md").write_text("readme\nuser edit, unstaged\n")
    (world.a / "staged.txt").write_text("user staged work\n")
    git(world.a, "add", "staged.txt")
    (world.a / "scratch.txt").write_text("user untracked\n")


def commit_files(repo: Path, sha: str) -> list[str]:
    return git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", sha).split()


# --- 0. the enum and the exit-code mapping ------------------------------------------


def test_exit_code_values(sync) -> None:
    assert {m.name: int(m) for m in sync.ExitCode} == {
        "OK": 0, "REFUSED": 1, "LOST": 3, "UNREACHABLE": 4, "BUSY": 5, "PUSH_REFUSED": 6,
    }


@pytest.mark.parametrize(
    "kind,code",
    [("won", 0), ("local", 0), ("noop", 0), ("refused", 1), ("lost", 3),
     ("unreachable", 4), ("busy", 5), ("push_refused", 6)],
)
def test_outcome_exit_code(sync, kind, code) -> None:
    out = sync.Outcome(kind=kind, message="m")
    assert out.exit_code == code
    assert out.sha is None and out.attempts == 0 and out.data is None


# --- 1. accepted: won, one commit, local checkout untouched ----------------------------


def test_won_one_commit_and_local_checkout_untouched(world, sync) -> None:
    dirty_feature_branch(world)
    before_local = snapshot(world.a)
    base = world.remote_sha()
    rec = Recorder()

    out = run(world, Appender(sync), rec)

    assert out.kind == "won", out.message
    assert out.exit_code == 0
    tip = world.remote_sha()
    assert out.sha == tip
    assert git(world.remote, "rev-list", f"{base}..{tip}").split() == [tip], (
        "origin must get exactly one new commit"
    )
    assert git(world.remote, "rev-parse", f"{tip}^").strip() == base
    assert commit_files(world.remote, tip) == [ITEM]
    assert world.remote_show(ITEM) == ITEM_TEXT + LINE + "\n"
    assert rec.sleeps == [] and rec.seams == [0]
    assert snapshot(world.a) == before_local, "the local checkout was touched"
    assert re.search(r"checkout|pull", out.message, re.I), (
        f"on a feature branch the message must say the checkout doesn't show it: {out.message}"
    )


# --- 2. an unrelated commit on attempt 0 only: retry and win ----------------------------


def test_lost_attempt_zero_retries_on_fresh_state_and_wins(world, sync) -> None:
    base = world.remote_sha()
    rec = Recorder(lambda attempt: rival(world) if attempt == 0 else None)
    mutate = Appender(sync, also_read="other-0.txt")

    out = run(world, mutate, rec)

    assert out.kind == "won", out.message
    assert out.exit_code == 0
    tip = world.remote_sha()
    assert out.sha == tip
    history = git(world.remote, "rev-list", f"{base}..{tip}").split()
    assert len(history) == 2, history
    ours, theirs = history
    assert ours == tip
    assert commit_files(world.remote, theirs) == ["other-0.txt"]
    assert commit_files(world.remote, ours) == [ITEM]
    assert git(world.remote, "rev-parse", f"{ours}^").strip() == theirs
    assert [a for a, _, _ in mutate.seen] == [0, 1]
    assert mutate.seen[0][2] is None, "attempt 0 already saw the rival's file"
    assert mutate.seen[1][2] == "rival 0\n", "attempt 1 did not re-read the fetched state"
    assert rec.jitters == [(0.1, 1.0)]
    assert rec.sleeps == [pytest.approx(JITTER * 1)]
    assert world.remote_show(ITEM) == ITEM_TEXT + LINE + "\n"


# --- 3. an unrelated commit on every attempt: busy ---------------------------------------


def test_rival_every_attempt_is_busy(world, sync) -> None:
    before_local = snapshot(world.a)
    rec = Recorder(lambda attempt: rival(world, attempt))
    mutate = Appender(sync)

    out = run(world, mutate, rec)

    assert out.kind == "busy", out.message
    assert out.exit_code == 5 == sync.ExitCode.BUSY
    assert out.attempts == 5
    assert out.sha is None
    assert [a for a, _, _ in mutate.seen] == [0, 1, 2, 3, 4]
    assert rec.seams == [0, 1, 2, 3, 4]
    assert len(rec.sleeps) in (4, 5), rec.sleeps
    for n, s in enumerate(rec.sleeps):
        assert s == pytest.approx(JITTER * (n + 1)), rec.sleeps
    assert world.remote_show(ITEM) == ITEM_TEXT, "our change reached origin"
    assert snapshot(world.a) == before_local


# --- 4. unreachable remote -----------------------------------------------------------------


def test_unreachable_remote(world, sync) -> None:
    git(world.a, "remote", "set-url", "origin", str(world.a.parent / "missing.git"))
    dirty_feature_branch(world)
    before_local = snapshot(world.a)
    refs_before = all_refs(world.a)
    remote_before = world.remote_sha()
    rec = Recorder()

    out = run(world, Appender(sync), rec)

    assert out.kind == "unreachable", out.message
    assert out.exit_code == 4 == sync.ExitCode.UNREACHABLE
    assert out.sha is None
    assert rec.seams == [] and rec.sleeps == []
    assert all_refs(world.a) == refs_before, "a ref moved: something was committed"
    assert snapshot(world.a) == before_local
    assert world.remote_sha() == remote_before


# --- 5. the remote refuses for another reason: push_refused, not retried --------------------


def test_update_hook_refusal_is_push_refused_once(world, sync, tmp_path) -> None:
    counter = tmp_path / "hook-runs"
    hook = world.remote / "hooks" / "update"
    hook.write_text(
        "#!/bin/sh\n"
        f"echo run >> '{counter}'\n"
        "echo 'kanban-guard: main is frozen for release' >&2\n"
        "exit 1\n"
    )
    hook.chmod(0o755)
    dirty_feature_branch(world)
    before_local = snapshot(world.a)
    remote_before = world.remote_sha()
    rec = Recorder()

    out = run(world, Appender(sync), rec)

    assert out.kind == "push_refused", out.message
    assert out.exit_code == 6 == sync.ExitCode.PUSH_REFUSED
    assert counter.read_text().splitlines() == ["run"], "the push was retried"
    assert "main is frozen for release" in out.message
    assert rec.sleeps == [] and rec.seams == [0]
    assert world.remote_sha() == remote_before
    assert snapshot(world.a) == before_local


# --- 6. no remote: a local commit of only the changed file ----------------------------------


def _no_remote(world: World) -> None:
    git(world.a, "remote", "remove", "origin")
    (world.a / "staged.txt").write_text("user staged work\n")
    git(world.a, "add", "staged.txt")


def test_no_remote_commits_only_the_file_locally(world, sync) -> None:
    _no_remote(world)
    # an uncommitted edit to the item: local mode reads the working tree
    (world.a / ITEM).write_text(ITEM_TEXT + "local edit\n")
    head_before = git(world.a, "rev-parse", "HEAD").strip()
    rec = Recorder()
    mutate = Appender(sync)

    out = run(world, mutate, rec)

    assert out.kind == "local", out.message
    assert out.exit_code == 0
    assert "no remote" in out.message.lower(), out.message
    assert mutate.seen[0][1] == ITEM_TEXT + "local edit\n", "read() did not see the working tree"
    expected = ITEM_TEXT + "local edit\n" + LINE + "\n"
    assert (world.a / ITEM).read_text() == expected
    head = git(world.a, "rev-parse", "HEAD").strip()
    assert git(world.a, "rev-list", f"{head_before}..{head}").split() == [head]
    assert out.sha in (None, head)
    assert commit_files(world.a, head) == [ITEM]
    assert git(world.a, "show", f"HEAD:{ITEM}") == expected
    assert "staged.txt" in git(world.a, "diff", "--cached", "--name-only").split(), (
        "the staged unrelated file is no longer staged"
    )
    assert "staged.txt" not in git(world.a, "ls-tree", "-r", "--name-only", "HEAD").split()
    assert git(world.a, "status", "--porcelain", "--", ITEM) == ""


@pytest.mark.parametrize("kind", ["noop", "refused"])
def test_no_remote_noop_and_refuse_commit_nothing(world, sync, kind) -> None:
    _no_remote(world)
    before_local = snapshot(world.a)

    def mutate(read: Any, attempt: int) -> Any:
        if kind == "noop":
            return sync.NoOp("already so")
        return sync.Refuse("held by someone", holder="agent-B")

    out = run(world, mutate, Recorder())

    assert out.kind == kind, out.message
    assert out.exit_code == (0 if kind == "noop" else 1)
    assert snapshot(world.a) == before_local


# --- 7. refused / lost ---------------------------------------------------------------------------


def test_refuse_on_attempt_zero_is_refused(world, sync) -> None:
    remote_before = world.remote_sha()
    before_local = snapshot(world.a)
    rec = Recorder()

    out = run(world, lambda read, attempt: sync.Refuse("held by agent-B", holder="agent-B"), rec)

    assert out.kind == "refused", out.message
    assert out.exit_code == 1 == sync.ExitCode.REFUSED
    assert world.remote_sha() == remote_before
    assert rec.seams == [] and rec.sleeps == []
    assert snapshot(world.a) == before_local


def _b_takes_item(world: World) -> None:
    b_push(world, {ITEM: ITEM_TEXT + "claimed by agent-B\n"})


@pytest.mark.parametrize("holder", ["agent-B", None])
def test_lost_attempt_then_refuse(world, sync, holder) -> None:
    rec = Recorder(lambda attempt: _b_takes_item(world) if attempt == 0 else None)
    seen: list[int] = []

    def mutate(read: Any, attempt: int) -> Any:
        seen.append(attempt)
        text = read(ITEM)
        if "claimed by agent-B" in text:
            return sync.Refuse("the item is taken", holder=holder)
        return sync.Change({ITEM: text + LINE + "\n"}, "take EXP-001")

    out = run(world, mutate, rec)

    assert seen == [0, 1]
    b_tip = world.remote_sha()
    assert world.remote_show(ITEM) == ITEM_TEXT + "claimed by agent-B\n"
    assert out.sha is None
    if holder:
        assert out.kind == "lost", out.message
        assert out.exit_code == 3 == sync.ExitCode.LOST
        assert "agent-B" in out.message
    else:
        assert out.kind == "refused", out.message
        assert out.exit_code == 1
    assert world.remote_sha() == b_tip


# --- 8. NoOp -------------------------------------------------------------------------------------


def test_noop_pushes_nothing(world, sync) -> None:
    remote_before = world.remote_sha()
    before_local = snapshot(world.a)
    rec = Recorder()

    out = run(world, lambda read, attempt: sync.NoOp("already appended"), rec)

    assert out.kind == "noop", out.message
    assert out.exit_code == 0
    assert out.sha is None
    assert world.remote_sha() == remote_before
    assert rec.seams == [] and rec.sleeps == []
    assert snapshot(world.a) == before_local


# --- 9. on the default branch: fast-forward the checkout ------------------------------------------


def test_won_on_default_branch_fast_forwards_checkout(world, sync) -> None:
    assert git(world.a, "symbolic-ref", "--short", "HEAD").strip() == "main"

    out = run(world, Appender(sync), Recorder())

    assert out.kind == "won", out.message
    assert git(world.a, "rev-parse", "HEAD").strip() == out.sha == world.remote_sha()
    assert (world.a / ITEM).read_text() == ITEM_TEXT + LINE + "\n"
    assert git(world.a, "status", "--porcelain", "--untracked-files=all") == ""


def test_won_on_default_branch_ahead_leaves_checkout_and_says_so(world, sync) -> None:
    (world.a / "local.txt").write_text("unpushed\n")
    git(world.a, "add", "local.txt")
    git(world.a, "commit", "-m", "local, unpushed")
    before_local = snapshot(world.a)

    out = run(world, Appender(sync), Recorder())

    assert out.kind == "won", out.message
    assert out.sha == world.remote_sha()
    assert "local.txt" not in world.remote_files(), "the unpushed local commit was published"
    assert snapshot(world.a) == before_local
    assert re.search(r"checkout|pull", out.message, re.I), out.message


# --- 10. the default branch is origin/HEAD's target ------------------------------------------------


def test_default_branch_follows_origin_head(tmp_path, monkeypatch, sync) -> None:
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    world = World(tmp_path, default="master")
    _seed_item(world)
    git(world.a, "remote", "set-head", "origin", "master")  # an empty clone has none
    assert git(world.a, "symbolic-ref", "refs/remotes/origin/HEAD").strip().endswith("/master")
    base = world.remote_sha("master")

    out = run(world, Appender(sync), Recorder())

    assert out.kind == "won", out.message
    assert out.sha == world.remote_sha("master")
    assert git(world.remote, "rev-parse", f"{out.sha}^").strip() == base
    assert "main" not in git(world.remote, "branch", "--list").split()
