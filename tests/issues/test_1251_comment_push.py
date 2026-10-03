"""Issue #1251, part 1: ``comment --push``, a comment published through ``sync_and_push``.

In 3.3.0 ``comment`` only commits locally. Two agents commenting on one item then
conflict when they publish: both comments append at the same spot, so the second
agent's ``git pull --rebase`` stops on ``CONFLICT (content)`` (the issue's repro).
``comment --push`` publishes the comment the way ``update --push`` publishes an edit
(tests/issues/test_574_update_push.py, the reference).

Decided shape (the driver implements it):

- CLI ``yurtle-kanban comment ID --body TEXT|--body-file PATH|- [--agent A] --push``.
  Exit codes and output follow ``update --push`` (``_print_outcome``): 0 won, noop or
  local; 1 refused (unknown item, empty body, no actor); 4 unreachable; 5 busy;
  6 push refused. A refusal goes to stderr.
- ``KanbanService.add_comment_push(item_id, content, author, *, sleep, jitter, seam)
  -> Outcome``: a ``sync_and_push`` whose mutate appends the comment to the item AS
  ORIGIN'S TREE HAS IT (``_item_target(read, item_id, "a comment")`` and
  ``_with_comment``). The comment, with its timestamp, is fixed once before the CAS
  loop, so a retry appends the same comment. The commit message is
  ``Add comment to <ID>``.
- It never touches the worktree, index or branch, and fast-forwards only a clean
  checkout on the default branch.
- With no remote it commits locally (outcome ``local``, exit 0).

The body, including ``--body-file -``, is read once, before the loop: a retried
attempt cannot re-read stdin.

Lost races through the CLI are forced by wrapping ``KanbanService.sync_and_push`` so
that one clone's call gets a ``Recorder`` seam (the CLI has no seam of its own).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from tests.issues.test_574_claim import frontmatter, output_of
from tests.issues.test_574_sync_and_push import (
    Recorder,
    commit_files,
    dirty_feature_branch,
    rival,
    snapshot,
)
from tests.issues.test_574_update_push import (
    COMMENTS,
    ITEM,
    ITEM_ID,
    assert_one_item_commit,
    plain,
    push_from_a,
    remote_bytes,
    rich,
    subject,
)
from tests.issues.test_585_create_push_loop import EXP_DIR, World, git
from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import main
from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.service import KanbanService

MESSAGE = f"Add comment to {ITEM_ID}"


# --- harness ---------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    monkeypatch.delenv("YURTLE_AGENT", raising=False)
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


@pytest.fixture
def world(tmp_path: Path) -> World:
    """Origin and both clones hold EXP-001 (rich: it already has a comment) and EXP-002."""
    w = World(tmp_path)
    push_from_a(w, {ITEM: rich(), f"{EXP_DIR}/EXP-002-two.md": plain("EXP-002", "Two")},
                "seed EXP-001..002")
    return w


def service(clone: Path) -> KanbanService:
    return KanbanService(KanbanConfig.load(clone / ".kanban" / "config.yaml"), clone)


def invoke(
    clone: Path, monkeypatch: pytest.MonkeyPatch, argv: list[str], stdin: str | None = None
) -> Any:
    monkeypatch.chdir(clone)
    return CliRunner().invoke(main, argv, input=stdin)


def seam_for(monkeypatch: pytest.MonkeyPatch, clone: Path, rec: Recorder) -> None:
    """Give `clone`'s `sync_and_push` calls `rec`'s seam, sleep and jitter; other
    clones' calls run as they are."""
    original = KanbanService.sync_and_push
    target = clone.resolve()

    def wrapped(self: KanbanService, mutate: Any, **kw: Any) -> Any:
        if Path(self.repo_root).resolve() == target:
            kw.update(sleep=rec.sleep, jitter=rec.jitter, seam=rec.seam)
        return original(self, mutate, **kw)

    monkeypatch.setattr(KanbanService, "sync_and_push", wrapped)


def comment_headings(text: str, author: str) -> int:
    return text.count(f"\n### {author} (")


def remote_text(world: World) -> str:
    return remote_bytes(world, ITEM).decode()


# --- 1. two clones comment on the same item: both land, in order ---------------------


def test_two_clones_comment_both_land_in_order(world, monkeypatch) -> None:
    """The issue's repro, made safe: B's push races after A's; B retries on A's
    commit and appends after A's comment, with no conflict."""
    base = world.remote_sha()
    a_outcomes: list[Any] = []

    def a_comments_first(attempt: int) -> None:
        if attempt == 0:
            a_outcomes.append(service(world.a).add_comment_push(ITEM_ID, "from A", "A"))

    rec = Recorder(a_comments_first)
    seam_for(monkeypatch, world.b, rec)

    result = invoke(
        world.b, monkeypatch, ["comment", ITEM_ID, "--agent", "B", "--body", "from B", "--push"]
    )

    assert result.exit_code == 0, output_of(result)
    assert len(a_outcomes) == 1 and a_outcomes[0].kind == "won", a_outcomes
    assert a_outcomes[0].exit_code == 0
    assert rec.seams == [0, 1], "B did not retry on A's commit"
    commits = git(world.remote, "rev-list", f"{base}..{world.remote_sha()}").split()
    assert len(commits) == 2, commits
    for sha in commits:
        assert commit_files(world.remote, sha) == [ITEM]
        assert subject(world.remote, sha) == MESSAGE
    text = remote_text(world)
    assert comment_headings(text, "A") == 1 and comment_headings(text, "B") == 1, text
    assert "from A" in text and "from B" in text, text
    assert text.index("### A (") < text.index("### B ("), "the comments are out of order"
    assert text.index("### seed (") < text.index("### A ("), "the old comment moved"
    assert "<<<<<<<" not in text and ">>>>>>>" not in text


def test_service_two_comments_second_retries(world) -> None:
    """Service level: B's outcome is won after more than one attempt."""
    rec = Recorder(
        lambda attempt: service(world.a).add_comment_push(ITEM_ID, "from A", "A")
        if attempt == 0 else None
    )

    out = service(world.b).add_comment_push(
        ITEM_ID, "from B", "B", sleep=rec.sleep, jitter=rec.jitter, seam=rec.seam
    )

    assert out.kind == "won", out.message
    assert out.exit_code == 0
    assert out.attempts > 1, out
    assert out.sha == world.remote_sha()
    text = remote_text(world)
    assert text.index("### A (") < text.index("### B ("), text


# --- 2. a rival's unrelated change to the same item survives -----------------------------


def test_rivals_title_edit_survives_the_comment(world, monkeypatch) -> None:
    rival_out = service(world.b).update_item_push(ITEM_ID, title="Rival title")
    assert rival_out.kind == "won", rival_out.message
    assert frontmatter((world.a / ITEM).read_text())["title"] == "X"  # A is stale
    base = world.remote_sha()
    rivals = remote_text(world)

    result = invoke(
        world.a, monkeypatch, ["comment", ITEM_ID, "--agent", "A", "--body", "noted", "--push"]
    )

    assert result.exit_code == 0, output_of(result)
    tip = assert_one_item_commit(world, base)
    assert subject(world.remote, tip) == MESSAGE
    new = remote_text(world)
    assert frontmatter(new)["title"] == "Rival title", "the rival's title was lost"
    assert new.startswith(rivals), "the comment must only append to origin's text"
    assert new[len(rivals):].startswith("\n### A ("), new[len(rivals):]
    assert "noted" in new[len(rivals):]


def test_rivals_title_edit_in_the_race_survives(world) -> None:
    """The rival's edit lands between A's fetch and A's push: A's retry appends to it."""
    rec = Recorder(
        lambda attempt: service(world.b).update_item_push(ITEM_ID, title="Rival title")
        if attempt == 0 else None
    )

    out = service(world.a).add_comment_push(
        ITEM_ID, "noted", "A", sleep=rec.sleep, jitter=rec.jitter, seam=rec.seam
    )

    assert out.kind == "won", out.message
    assert rec.seams == [0, 1]
    new = remote_text(world)
    assert frontmatter(new)["title"] == "Rival title"
    assert comment_headings(new, "A") == 1, new


# --- 3. the worktree is untouched --------------------------------------------------------


def test_feature_branch_checkout_untouched(world, monkeypatch) -> None:
    dirty_feature_branch(world)
    base = world.remote_sha()
    before = snapshot(world.a)

    result = invoke(
        world.a, monkeypatch, ["comment", ITEM_ID, "--agent", "A", "--body", "hi", "--push"]
    )

    assert result.exit_code == 0, output_of(result)
    assert_one_item_commit(world, base)
    assert "hi" in remote_text(world)
    assert snapshot(world.a) == before, "A's checkout changed"


def test_clean_default_branch_is_fast_forwarded(world, monkeypatch) -> None:
    assert git(world.a, "symbolic-ref", "--short", "HEAD").strip() == world.default

    result = invoke(
        world.a, monkeypatch, ["comment", ITEM_ID, "--agent", "A", "--body", "hi", "--push"]
    )

    assert result.exit_code == 0, output_of(result)
    assert git(world.a, "rev-parse", "HEAD").strip() == world.remote_sha()
    assert (world.a / ITEM).read_bytes() == remote_bytes(world, ITEM)
    assert git(world.a, "status", "--porcelain", "--untracked-files=all") == ""


# --- 4. the body is read once -------------------------------------------------------------


def test_stdin_body_read_once_across_a_lost_race(world, monkeypatch) -> None:
    rec = Recorder(lambda attempt: rival(world) if attempt == 0 else None)
    seam_for(monkeypatch, world.a, rec)
    base = world.remote_sha()

    result = invoke(
        world.a, monkeypatch,
        ["comment", ITEM_ID, "--agent", "A", "--body-file", "-", "--push"],
        stdin="the stdin body\n",
    )

    assert result.exit_code == 0, output_of(result)
    assert rec.seams == [0, 1], "the race was not forced"
    commits = git(world.remote, "rev-list", f"{base}..{world.remote_sha()}").split()
    assert len(commits) == 2, commits  # the rival's, then A's comment
    text = remote_text(world)
    assert comment_headings(text, "A") == 1, text
    assert text.count("the stdin body") == 1, text


def test_retry_appends_the_same_comment(world, monkeypatch) -> None:
    """The comment (timestamp included) is fixed before the loop: what a lost attempt
    built is what the retry pushes. The service's clock moves a minute per reading, so a
    timestamp taken per attempt would differ."""
    import yurtle_kanban.service as service_mod

    real = service_mod.datetime

    class Ticking(real):  # type: ignore[misc, valid-type]
        calls = 0

        @classmethod
        def now(cls, tz: Any = None) -> Any:
            cls.calls += 1
            return real(2026, 10, 2, 9, 0, tzinfo=tz) + service_mod.timedelta(minutes=cls.calls)

    monkeypatch.setattr(service_mod, "datetime", Ticking)
    built: list[str] = []
    rec = Recorder(lambda attempt: rival(world) if attempt == 0 else None)
    original = KanbanService.sync_and_push

    def spy(self: KanbanService, mutate: Any, **kw: Any) -> Any:
        def recording(read: Any, attempt: int) -> Any:
            change = mutate(read, attempt)
            files = getattr(change, "files", None) or {}
            built.append(files.get(ITEM, ""))
            return change
        return original(self, recording, **kw)

    svc = service(world.a)
    svc.sync_and_push = spy.__get__(svc)  # type: ignore[method-assign]

    out = svc.add_comment_push(
        ITEM_ID, "same text", "A", sleep=rec.sleep, jitter=rec.jitter, seam=rec.seam
    )

    assert out.kind == "won", out.message
    assert len(built) >= 2, built
    tails = [b[b.index("\n### A ("):] for b in built if "\n### A (" in b]
    assert len(tails) >= 2 and len(set(tails)) == 1, tails


# --- 5. unknown item ----------------------------------------------------------------------


def test_unknown_item_is_refused_nothing_pushed(world, monkeypatch) -> None:
    base = world.remote_sha()
    before = snapshot(world.a)

    result = invoke(
        world.a, monkeypatch, ["comment", "EXP-999", "--agent", "A", "--body", "x", "--push"]
    )

    out = output_of(result)
    assert result.exit_code == 1, out
    assert "EXP-999" in out, out
    assert world.remote_sha() == base
    assert snapshot(world.a) == before


def test_item_only_local_is_refused(world, monkeypatch) -> None:
    rel = f"{EXP_DIR}/EXP-006-six.md"
    (world.a / rel).write_text(plain("EXP-006", "Six"))
    git(world.a, "add", rel)
    git(world.a, "commit", "-m", "EXP-006, local only")
    base = world.remote_sha()
    before = snapshot(world.a)

    result = invoke(
        world.a, monkeypatch, ["comment", "EXP-006", "--agent", "A", "--body", "x", "--push"]
    )

    out = output_of(result)
    assert result.exit_code == 1, out
    assert "EXP-006" in out, out
    assert world.remote_sha() == base
    assert snapshot(world.a) == before


def test_service_unknown_item_outcome_is_refused(world) -> None:
    base = world.remote_sha()

    out = service(world.a).add_comment_push("EXP-999", "x", "A")

    assert out.kind == "refused", out.message
    assert out.exit_code == 1
    assert world.remote_sha() == base


def test_empty_body_is_refused_nothing_pushed(world, monkeypatch) -> None:
    base = world.remote_sha()

    result = invoke(
        world.a, monkeypatch, ["comment", ITEM_ID, "--agent", "A", "--body", "", "--push"]
    )

    assert result.exit_code == 1, output_of(result)
    assert world.remote_sha() == base


# --- 6. no remote ---------------------------------------------------------------------------


def test_no_remote_commits_locally(world, monkeypatch) -> None:
    git(world.a, "remote", "remove", "origin")
    (world.a / "staged.txt").write_text("user staged work\n")
    git(world.a, "add", "staged.txt")
    head_before = git(world.a, "rev-parse", "HEAD").strip()

    result = invoke(
        world.a, monkeypatch, ["comment", ITEM_ID, "--agent", "A", "--body", "local one", "--push"]
    )

    out = output_of(result)
    assert result.exit_code == 0, out
    head = git(world.a, "rev-parse", "HEAD").strip()
    assert git(world.a, "rev-list", f"{head_before}..{head}").split() == [head]
    assert commit_files(world.a, head) == [ITEM]
    assert subject(world.a, head) == MESSAGE
    text = (world.a / ITEM).read_text()
    assert comment_headings(text, "A") == 1 and "local one" in text, text
    assert "staged.txt" in git(world.a, "diff", "--cached", "--name-only").split()


def test_no_remote_service_outcome_is_local(world) -> None:
    git(world.a, "remote", "remove", "origin")

    out = service(world.a).add_comment_push(ITEM_ID, "local one", "A")

    assert out.kind == "local", out.message
    assert out.exit_code == 0
    assert "local one" in (world.a / ITEM).read_text()


# --- 7. no actor ----------------------------------------------------------------------------


def test_no_actor_is_refused_before_any_push(world, monkeypatch) -> None:
    git(world.a, "config", "--unset", "user.name")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    base = world.remote_sha()
    before = snapshot(world.a)

    result = invoke(world.a, monkeypatch, ["comment", ITEM_ID, "--body", "x", "--push"])

    out = output_of(result)
    assert result.exit_code == 1, out
    assert "actor" in out.lower(), out
    assert world.remote_sha() == base
    assert snapshot(world.a) == before


# --- 8. control: plain `comment` is unchanged ---------------------------------------------


def test_control_comment_without_push_commits_locally_only(world, monkeypatch) -> None:
    base = world.remote_sha()
    head_before = git(world.a, "rev-parse", "HEAD").strip()

    result = invoke(world.a, monkeypatch, ["comment", ITEM_ID, "--agent", "A", "--body", "here"])

    assert result.exit_code == 0, output_of(result)
    assert world.remote_sha() == base, "comment without --push pushed"
    head = git(world.a, "rev-parse", "HEAD").strip()
    assert git(world.a, "rev-list", f"{head_before}..{head}").split() == [head]
    assert commit_files(world.a, head) == [ITEM]
    assert subject(world.a, head) == MESSAGE
    text = (world.a / ITEM).read_text()
    assert text.startswith(rich().removesuffix(COMMENTS)) and "here" in text


# --- 9. help ----------------------------------------------------------------------------------


def test_push_is_in_comment_help() -> None:
    result = CliRunner().invoke(main, ["comment", "--help"])

    assert result.exit_code == 0, result.output
    assert "--push" in result.output, result.output


def test_remote_bytes_helper_reads_origin(world) -> None:
    """Harness check: origin holds the seeded item."""
    assert subprocess.run(
        ["git", "cat-file", "-e", f"{world.default}:{ITEM}"], cwd=world.remote
    ).returncode == 0
