"""Issue #1251, part 2: ``rank ID N [--summary TEXT] --push``.

``rank`` only committed locally. With ``--push`` it publishes the rank through
``sync_and_push``, the compare-and-swap that ``update --push`` uses
(tests/issues/test_574_update_push.py is the reference, and this file reuses its
harness: a bare remote, two clones A and B, and a lost race forced through the seam).

API under test:

- CLI ``yurtle-kanban rank ID N [--summary TEXT] --push`` prints the outcome and exits
  with ``Outcome.exit_code`` (``_print_outcome``): 0 won/noop/local, 1 refused,
  4 unreachable, 5 busy, 6 push refused. ``--push --no-commit`` is refused (exit 1).
- ``KanbanService.rank_item_push(item_id, rank, value_summary=None, *, sleep, jitter,
  seam) -> Outcome``. Its mutate sets ``priority_rank`` (and ``value_summary``) on the
  item as ORIGIN's tree has it, in a commit ``Rank <ID> as #N``. It is a noop when
  origin already has that rank and summary. It never touches A's worktree, index or
  branch, and it fast-forwards only a clean default-branch checkout. With no remote it
  commits locally.

Summary validation (test 8): plain ``rank`` refuses only a summary that isn't valid
UTF-8 (a lone surrogate, #172/#219). A newline or a control character is accepted and
written escaped by ``yaml_quote`` (#162). ``rank --push`` matches that. A lone
surrogate is refused before anything is fetched or pushed, and a newline or control
character round-trips onto origin exactly. The CLI can't carry a lone surrogate
(``_Main`` refuses undecodable argv, #193), so that case is tested at the service
level.
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
    snapshot,
)
from tests.issues.test_585_create_push_loop import EXP_DIR, World, git
from tests.issues.test_590_next_id_and_hdd_ids import b_push
from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import main
from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.service import KanbanService

ITEM_ID = "EXP-001"
ITEM = f"{EXP_DIR}/EXP-001-x.md"
LONE = "bad \udcff summary"  # what surrogateescape makes of an undecodable byte


# --- harness ---------------------------------------------------------------------


def item_text(title: str = "X", extra: str = "") -> str:
    """EXP-001 with an unknown key and a comment, so a whole-file rewrite shows up."""
    return (
        f'---\nid: EXP-001\ntitle: "{title}"\ntype: expedition\nstatus: provisioning\n'
        f"priority: medium\ncustom_key: keepme\n{extra}---\n\n# {title}\n\n"
        "Original body paragraph.\n\n## Comments\n\n### seed (2026-09-01 00:00)\n\nfirst\n"
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
    """Origin and both clones hold EXP-001, unranked."""
    w = World(tmp_path)
    push_from_a(w, {ITEM: item_text()}, "seed EXP-001")
    return w


def service(clone: Path) -> KanbanService:
    return KanbanService(KanbanConfig.load(clone / ".kanban" / "config.yaml"), clone)


def rank_push(clone: Path, rank: int, rec: Recorder | None = None, **kw: Any) -> Any:
    rec = rec or Recorder()
    return service(clone).rank_item_push(
        ITEM_ID, rank, sleep=rec.sleep, jitter=rec.jitter, seam=rec.seam, **kw
    )


def invoke(world: World, monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> Any:
    monkeypatch.chdir(world.a)
    return CliRunner().invoke(main, argv)


def remote_bytes(world: World, rel: str) -> bytes:
    return subprocess.run(
        ["git", "show", f"{world.default}:{rel}"],
        cwd=world.remote, capture_output=True, check=True,
    ).stdout


def subject(repo: Path, sha: str) -> str:
    return git(repo, "log", "-1", "--format=%s", sha).strip()


def origin_fm(world: World) -> dict[str, Any]:
    return frontmatter(world.remote_show(ITEM))


# --- 1. won: origin ranked, A's checkout untouched ------------------------------------


def test_rank_push_lands_on_origin_and_checkout_untouched(world, monkeypatch) -> None:
    dirty_feature_branch(world)
    base = world.remote_sha()
    old = remote_bytes(world, ITEM).decode()
    before = snapshot(world.a)

    result = invoke(world, monkeypatch, ["rank", ITEM_ID, "3", "--push"])

    assert result.exit_code == 0, output_of(result)
    tip = world.remote_sha()
    assert git(world.remote, "rev-list", f"{base}..{tip}").split() == [tip], (
        "origin must get exactly one new commit"
    )
    assert commit_files(world.remote, tip) == [ITEM]
    assert subject(world.remote, tip) == f"Rank {ITEM_ID} as #3"
    new = remote_bytes(world, ITEM).decode()
    fm = frontmatter(new)
    assert fm["priority_rank"] == 3
    assert fm["custom_key"] == "keepme" and fm["title"] == "X"
    # field-level: only the priority_rank line is new
    assert [ln for ln in new.splitlines() if ln not in old.splitlines()] == [
        "priority_rank: 3"
    ], new
    assert snapshot(world.a) == before, "A's worktree, index or branch changed"


def test_rank_push_with_summary_sets_both_fields(world, monkeypatch) -> None:
    result = invoke(
        world, monkeypatch, ["rank", ITEM_ID, "2", "--summary", "Unblocks Paper 127", "--push"]
    )

    assert result.exit_code == 0, output_of(result)
    fm = origin_fm(world)
    assert fm["priority_rank"] == 2
    assert fm["value_summary"] == "Unblocks Paper 127"
    assert subject(world.remote, world.remote_sha()) == f"Rank {ITEM_ID} as #2"


def test_won_on_clean_default_branch_fast_forwards_checkout(world, monkeypatch) -> None:
    assert git(world.a, "symbolic-ref", "--short", "HEAD").strip() == world.default

    result = invoke(world, monkeypatch, ["rank", ITEM_ID, "3", "--push"])

    assert result.exit_code == 0, output_of(result)
    assert git(world.a, "rev-parse", "HEAD").strip() == world.remote_sha()
    assert (world.a / ITEM).read_bytes() == remote_bytes(world, ITEM)
    assert git(world.a, "status", "--porcelain", "--untracked-files=all") == ""


def test_service_outcome_is_won(world) -> None:
    out = rank_push(world.a, 4)

    assert out.kind == "won", out.message
    assert out.exit_code == 0
    assert out.sha == world.remote_sha()
    assert origin_fm(world)["priority_rank"] == 4


# --- 2. a lost race: B's title edit lands first, A retries -------------------------------


def test_lost_race_to_title_update_retries_and_keeps_both(world) -> None:
    def b_updates_title(attempt: int) -> None:
        if attempt == 0:
            out = service(world.b).update_item_push(
                ITEM_ID, title="Rival title",
                sleep=lambda s: None, jitter=lambda lo, hi: 0.0,
            )
            assert out.kind == "won", out.message

    rec = Recorder(b_updates_title)
    base = world.remote_sha()

    out = rank_push(world.a, 5, rec)

    assert out.kind == "won", out.message
    assert out.attempts > 1, out
    assert rec.seams == [0, 1], "A did not retry on B's state"
    history = git(world.remote, "rev-list", f"{base}..{world.remote_sha()}").split()
    assert len(history) == 2, history
    ours, theirs = history
    # ruled edit (#1251): update --push names the fields it changed, not their values
    assert subject(world.remote, theirs) == f"Update {ITEM_ID}: title"
    assert subject(world.remote, ours) == f"Rank {ITEM_ID} as #5"
    fm = origin_fm(world)
    assert fm["title"] == "Rival title", "B's title was lost"
    assert fm["priority_rank"] == 5, "A's rank was lost"


def test_rank_applies_to_origins_version_not_local(world, monkeypatch) -> None:
    b_push(world, {ITEM: item_text(title="Rival title")})  # A still says "X"
    assert frontmatter((world.a / ITEM).read_text())["title"] == "X"
    git(world.a, "checkout", "-b", "feature")

    result = invoke(world, monkeypatch, ["rank", ITEM_ID, "1", "--push"])

    assert result.exit_code == 0, output_of(result)
    fm = origin_fm(world)
    assert fm["title"] == "Rival title" and fm["priority_rank"] == 1


# --- 3. same rank already on origin: noop ---------------------------------------------------


def test_same_rank_already_on_origin_is_noop(world, monkeypatch) -> None:
    push_from_a(world, {ITEM: item_text(extra="priority_rank: 3\n")}, "rank 3")
    base = world.remote_sha()
    before = snapshot(world.a)

    result = invoke(world, monkeypatch, ["rank", ITEM_ID, "3", "--push"])

    assert result.exit_code == 0, output_of(result)
    assert world.remote_sha() == base, "a noop pushed"
    assert snapshot(world.a) == before


def test_same_rank_and_summary_on_origin_is_noop_service(world) -> None:
    push_from_a(
        world,
        {ITEM: item_text(extra='priority_rank: 3\nvalue_summary: "Unblocks X"\n')},
        "rank 3",
    )
    base = world.remote_sha()

    out = rank_push(world.a, 3, value_summary="Unblocks X")

    assert out.kind == "noop", out.message
    assert out.exit_code == 0
    assert out.sha is None
    assert world.remote_sha() == base


def test_same_rank_new_summary_is_not_noop(world) -> None:
    push_from_a(
        world,
        {ITEM: item_text(extra='priority_rank: 3\nvalue_summary: "Old"\n')},
        "rank 3",
    )

    out = rank_push(world.a, 3, value_summary="New")

    assert out.kind == "won", out.message
    assert origin_fm(world)["value_summary"] == "New"


# --- 4. rank 0 ----------------------------------------------------------------------------


def test_rank_zero_is_refused_nothing_pushed(world, monkeypatch) -> None:
    base = world.remote_sha()
    before = snapshot(world.a)

    result = invoke(world, monkeypatch, ["rank", ITEM_ID, "0", "--push"])

    assert result.exit_code == 1, output_of(result)
    assert world.remote_sha() == base
    assert snapshot(world.a) == before


@pytest.mark.parametrize("bad", [0, -1])
def test_rank_below_one_service_refused_before_fetch(world, bad) -> None:
    rec = Recorder()

    out = rank_push(world.a, bad, rec)

    assert out.kind == "refused", out.message
    assert out.exit_code == 1
    assert rec.seams == [], "a bad rank must be refused before the CAS loop"


# --- 5. an item only local ------------------------------------------------------------------


def test_item_only_local_is_refused(world, monkeypatch) -> None:
    rel = f"{EXP_DIR}/EXP-006-six.md"
    (world.a / rel).write_text(item_text(title="Six").replace("EXP-001", "EXP-006"))
    git(world.a, "add", rel)
    git(world.a, "commit", "-m", "EXP-006, local only")
    base = world.remote_sha()
    before = snapshot(world.a)

    result = invoke(world, monkeypatch, ["rank", "EXP-006", "1", "--push"])

    out = output_of(result)
    assert result.exit_code == 1, out
    assert "EXP-006" in out, out
    assert world.remote_sha() == base
    assert snapshot(world.a) == before


# --- 6. no remote ---------------------------------------------------------------------------


def test_no_remote_commits_locally(world, monkeypatch) -> None:
    git(world.a, "remote", "remove", "origin")
    (world.a / "staged.txt").write_text("user staged work\n")
    git(world.a, "add", "staged.txt")
    head_before = git(world.a, "rev-parse", "HEAD").strip()

    result = invoke(world, monkeypatch, ["rank", ITEM_ID, "2", "--push"])

    out = output_of(result)
    assert result.exit_code == 0, out
    assert "no remote" in out.lower(), out
    head = git(world.a, "rev-parse", "HEAD").strip()
    assert git(world.a, "rev-list", f"{head_before}..{head}").split() == [head]
    assert commit_files(world.a, head) == [ITEM]
    assert subject(world.a, head) == f"Rank {ITEM_ID} as #2"
    assert frontmatter((world.a / ITEM).read_text())["priority_rank"] == 2
    assert "staged.txt" in git(world.a, "diff", "--cached", "--name-only").split()


def test_no_remote_service_outcome_is_local(world) -> None:
    git(world.a, "remote", "remove", "origin")

    out = rank_push(world.a, 2)

    assert out.kind == "local", out.message
    assert out.exit_code == 0


# --- 7. --push --no-commit ------------------------------------------------------------------


def test_push_with_no_commit_is_refused(world, monkeypatch) -> None:
    base = world.remote_sha()
    before = snapshot(world.a)

    result = invoke(world, monkeypatch, ["rank", ITEM_ID, "2", "--push", "--no-commit"])

    assert result.exit_code == 1, output_of(result)
    assert world.remote_sha() == base
    assert snapshot(world.a) == before


# --- 8. --summary validation, as plain rank's -----------------------------------------------


def test_invalid_utf8_summary_refused_before_anything_pushed(world) -> None:
    base = world.remote_sha()
    before = snapshot(world.a)
    rec = Recorder()

    out = rank_push(world.a, 2, rec, value_summary=LONE)

    assert out.kind == "refused", out.message
    assert out.exit_code == 1
    assert "value_summary" in out.message, out.message
    assert rec.seams == [], "the summary must be checked before the CAS loop"
    assert world.remote_sha() == base
    assert snapshot(world.a) == before


@pytest.mark.parametrize("summary", ["line one\nline two", "bell \x07 and esc \x1b[31m"])
def test_newline_or_control_summary_round_trips_as_plain_rank(
    world, monkeypatch, summary
) -> None:
    """Plain `rank` accepts these and writes them escaped (#162); `--push` matches."""
    result = invoke(world, monkeypatch, ["rank", ITEM_ID, "2", "--summary", summary, "--push"])

    assert result.exit_code == 0, output_of(result)
    text = world.remote_show(ITEM)
    assert frontmatter(text)["value_summary"] == summary
    fm_block = text.split("---\n")[1]
    assert "\x07" not in fm_block and "\x1b" not in fm_block, "control char written raw"
    assert len([ln for ln in fm_block.splitlines() if "value_summary" in ln]) == 1


def test_control_plain_rank_accepts_newline_summary(world, monkeypatch) -> None:
    """What test 8 matches: plain `rank` takes a newline summary, escaped."""
    result = invoke(world, monkeypatch, ["rank", ITEM_ID, "2", "--summary", "a\nb"])

    assert result.exit_code == 0, output_of(result)
    assert frontmatter((world.a / ITEM).read_text())["value_summary"] == "a\nb"


def test_control_plain_rank_refuses_invalid_utf8_summary(world) -> None:
    with pytest.raises(ValueError, match="value_summary"):
        service(world.a).rank_item(ITEM_ID, 2, value_summary=LONE)


# --- other outcomes, as update --push's ------------------------------------------------------


def test_unreachable_remote_exits_4(world, monkeypatch) -> None:
    git(world.a, "remote", "set-url", "origin", str(world.a.parent / "missing.git"))
    dirty_feature_branch(world)
    before = snapshot(world.a)

    result = invoke(world, monkeypatch, ["rank", ITEM_ID, "2", "--push"])

    assert result.exit_code == 4, output_of(result)
    assert snapshot(world.a) == before, "an unreachable remote must not fall back to local"


def test_unrelated_commit_every_attempt_is_busy(world) -> None:
    rec = Recorder(lambda attempt: b_push(world, {f"other-{attempt}.txt": f"{attempt}\n"}))

    out = rank_push(world.a, 2, rec)

    assert out.kind == "busy", out.message
    assert out.exit_code == 5
    assert "priority_rank" not in origin_fm(world)


def test_declining_update_hook_exits_6(world, monkeypatch) -> None:
    hook = world.remote / "hooks" / "update"
    hook.write_text("#!/bin/sh\necho 'kanban-guard: frozen' >&2\nexit 1\n")
    hook.chmod(0o755)
    base = world.remote_sha()

    result = invoke(world, monkeypatch, ["rank", ITEM_ID, "2", "--push"])

    out = output_of(result)
    assert result.exit_code == 6, out
    assert "frozen" in out, out
    assert world.remote_sha() == base


# --- 9. control: plain rank unchanged; --push documented -------------------------------------


def test_control_plain_rank_commits_locally_only(world, monkeypatch) -> None:
    base = world.remote_sha()
    head_before = git(world.a, "rev-parse", "HEAD").strip()

    # ruled edit (#1279): pushing is now the default with an origin; this pins the plain path
    result = invoke(world, monkeypatch, ["rank", ITEM_ID, "2", "--no-push"])

    assert result.exit_code == 0, output_of(result)
    assert world.remote_sha() == base, "rank without --push pushed"
    head = git(world.a, "rev-parse", "HEAD").strip()
    assert git(world.a, "rev-list", f"{head_before}..{head}").split() == [head]
    assert commit_files(world.a, head) == [ITEM]
    assert subject(world.a, head) == f"Rank {ITEM_ID} as #2"
    assert frontmatter((world.a / ITEM).read_text())["priority_rank"] == 2


def test_push_in_rank_help() -> None:
    result = CliRunner().invoke(main, ["rank", "--help"])

    assert result.exit_code == 0, result.output
    assert "--push" in result.output, result.output
