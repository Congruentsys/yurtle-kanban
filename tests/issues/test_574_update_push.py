"""Issue #574, PR D: ``update ID ... --push``, #576's field-level edits through
``sync_and_push``.

The [steer] on #574 splits the build into four PRs, and this is D. Spec §5:
``update ID … --push`` applies #576's field-level edits through ``sync_and_push``, to
the FETCHED file, instead of making a local commit. It has the same outcomes and exit
codes as ``claim``: 0 won/local/noop, 1 refused, 3 lost, 4 unreachable, 5 busy,
6 push_refused. It is built on PR A (tests/issues/test_574_sync_and_push.py) and reuses
PR B's World/seam harness (tests/issues/test_574_claim.py).

API under test:

- CLI ``yurtle-kanban update ID [the flags update already has] --push`` prints the
  outcome's message and exits with ``Outcome.exit_code``. The flags are the ones
  ``update`` really has: ``--title``, ``--priority``, ``--tag``/``--untag``,
  ``--body``/``--body-file``, ``--depends-on``, ``--add-dep``/``--rm-dep``,
  ``--related``, ``--allow-unknown``. (``update`` has no ``--assignee``.)
- ``KanbanService.update_item_push(item_id, *, title=None, priority=None,
  description=None, add_tags=None, remove_tags=None, depends_on=None,
  add_depends_on=None, remove_depends_on=None, related=None, allow_unknown=False,
  sleep, jitter, seam=None) -> Outcome``: the keyword names of
  ``update_item_changes``, plus ``sync_and_push``'s ``sleep``/``jitter``/``seam``. The
  race tests go through it, since the CLI has no seam.

Rules pinned here, all judged against the FETCHED ``origin/<default>``:

- The edit is field-level, as #576/#583 require: every line the edit doesn't name
  stays byte-identical, including the status-history block, ``## Comments``, unknown
  frontmatter keys and the native status. The edit is applied to ORIGIN's text, so a
  rival's unrelated change there survives.
- Origin gets exactly one new commit, touching only the item file. Its subject is
  ``update``'s usual ``Update EXP-001: <changes>`` (#751).
- The local checkout is byte-identical afterwards (feature branch, dirty tree, staged
  file), unless HEAD is on the default branch and can fast-forward.
- Dependency edits are re-checked against the fetched board (#638/#576): a target only
  on origin is known, a target only in the local tree is "on no board", and a cycle
  that exists only on origin is refused.
- Refusals give exit 1 with nothing pushed: an item not on origin, an ID on more
  than one file on origin, a bad priority, and a dependency refusal.
- Races through the seam: an unrelated commit gives retry-and-win. A rival edit of
  the SAME field gives retry and re-apply on the new state, so the last writer (A)
  wins, deterministically. A rival that already wrote A's value gives ``noop``. A rival
  that closes a cycle before A's push gives ``refused`` on the retry. An unrelated
  commit on every attempt gives ``busy`` (5). An unreachable remote gives 4, and a
  declining ``update`` hook gives 6 after one push.
- No remote: ``local``, exit 0, a "no remote" note, one local commit of the item file
  only, and a staged unrelated file stays staged.
- Without ``--push``, ``update`` is unchanged: a local commit and nothing pushed.

Ambiguities resolved here (the test partner's reading; the driver may challenge):

a. The service entry point is named ``update_item_push``, mirroring ``claim_item``.
   If the driver prefers another shape (for example a ``push=True`` on
   ``update_item_changes``), only ``upd()`` below needs to change.
b. A refusal that is not a race (no holder) is ``refused`` (exit 1) even after a
   rejected push. ``update`` has no holder, so it is never ``lost``.
c. ``--push --no-commit`` contradict each other and are refused (exit 1) before any
   write or push.
d. A rival that wrote A's exact value between A's fetch and push makes A's retry a
   ``noop`` (exit 0). A does not push an empty commit.
e. Refusal wording is matched loosely: the item ID for "not found", "more than one"
   or "duplicate" for a duplicated ID, "no board" and "cycle" for the #576 refusals.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import CliRunner

from tests.issues.test_574_claim import frontmatter, output_of
from tests.issues.test_574_sync_and_push import (
    JITTER,
    Recorder,
    commit_files,
    dirty_feature_branch,
    rival,
    snapshot,
)
from tests.issues.test_576_cli_update import _assert_only_changed
from tests.issues.test_585_create_push_loop import EXP_DIR, World, git
from tests.issues.test_590_next_id_and_hdd_ids import b_push
from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import main
from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.service import KanbanService

ITEM_ID = "EXP-001"
ITEM = f"{EXP_DIR}/EXP-001-x.md"
DEP2 = f"{EXP_DIR}/EXP-002-two.md"
DEP3 = f"{EXP_DIR}/EXP-003-three.md"
HISTORY = (
    "```yurtle\n"
    "@prefix kb: <https://yurtle.dev/kanban/> .\n"
    "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .\n"
    "\n"
    "<> kb:statusChange [\n"
    "    kb:status kb:ready ;\n"
    '    kb:at "2026-09-01T00:00:00"^^xsd:dateTime ;\n'
    '    kb:by "seed" ;\n'
    "  ] .\n"
    "```\n"
)
COMMENTS = "## Comments\n\n### seed (2026-09-01 00:00)\n\nfirst comment\n"
BODY = "Original body paragraph."


# --- harness ---------------------------------------------------------------------


def rich(
    title: str = "X", priority: str = "medium", tags: str = "[alpha]", deps: str = "[]"
) -> str:
    """EXP-001 as a real item: native status, an unknown key, a status-history block
    and a comment, so a whole-file rewrite shows up (#583)."""
    return (
        f'---\nid: EXP-001\ntitle: "{title}"\ntype: expedition\nstatus: provisioning\n'
        f"priority: {priority}\ntags: {tags}\ndepends_on: {deps}\ncustom_key: keepme\n"
        f"---\n\n# {title}\n\n{BODY}\n\n{HISTORY}\n\n{COMMENTS}"
    )


def plain(item_id: str, title: str, deps: str = "[]") -> str:
    return (
        f'---\nid: {item_id}\ntitle: "{title}"\ntype: expedition\nstatus: backlog\n'
        f"depends_on: {deps}\n---\n\n# {title}\n\nA description long enough.\n"
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
    """Origin and both clones hold EXP-001 (rich), EXP-002 and EXP-003 (plain)."""
    w = World(tmp_path)
    push_from_a(
        w,
        {ITEM: rich(), DEP2: plain("EXP-002", "Two"), DEP3: plain("EXP-003", "Three")},
        "seed EXP-001..003",
    )
    return w


def service(clone: Path) -> KanbanService:
    return KanbanService(KanbanConfig.load(clone / ".kanban" / "config.yaml"), clone)


def upd(clone: Path, rec: Recorder | None = None, **fields: Any) -> Any:
    """`update --push` at the service level (ambiguity a)."""
    rec = rec or Recorder()
    return service(clone).update_item_push(
        ITEM_ID, sleep=rec.sleep, jitter=rec.jitter, seam=rec.seam, **fields
    )


def invoke(world: World, monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> Any:
    monkeypatch.chdir(world.a)
    return CliRunner().invoke(main, argv)


def remote_bytes(world: World, rel: str) -> bytes:
    return subprocess.run(
        ["git", "show", f"{world.default}:{rel}"],
        cwd=world.remote, capture_output=True, check=True,
    ).stdout


def assert_one_item_commit(world: World, base: str) -> str:
    """Origin's tip is exactly one commit on `base`, touching only the item."""
    tip = world.remote_sha()
    assert git(world.remote, "rev-list", f"{base}..{tip}").split() == [tip], (
        "origin must get exactly one new commit"
    )
    assert commit_files(world.remote, tip) == [ITEM], "the update commit touched other files"
    return tip


def subject(repo: Path, sha: str) -> str:
    return git(repo, "log", "-1", "--format=%s", sha).strip()


def history_block(data: bytes) -> bytes:
    start = data.index(b"```yurtle")
    return data[start : data.index(b"```", start + 3) + 3]


# --- field edits, won ---------------------------------------------------------------


def test_priority_push_is_one_field_level_commit_and_checkout_untouched(
    world, monkeypatch
) -> None:
    dirty_feature_branch(world)
    base = world.remote_sha()
    old = remote_bytes(world, ITEM)
    before = snapshot(world.a)

    result = invoke(world, monkeypatch, ["update", ITEM_ID, "--priority", "high", "--push"])

    assert result.exit_code == 0, output_of(result)
    tip = assert_one_item_commit(world, base)
    _assert_only_changed(old, remote_bytes(world, ITEM), {"priority": "high"})
    assert subject(world.remote, tip) == f"Update {ITEM_ID}: priority high"
    assert snapshot(world.a) == before, "A's checkout changed"


def test_edit_applies_to_origins_version_and_keeps_rivals_change(world, monkeypatch) -> None:
    b_push(world, {ITEM: rich(title="Rival title")})  # A still says title "X"
    assert frontmatter((world.a / ITEM).read_text())["title"] == "X"
    base = world.remote_sha()
    rivals = remote_bytes(world, ITEM)

    result = invoke(world, monkeypatch, ["update", ITEM_ID, "--priority", "high", "--push"])

    assert result.exit_code == 0, output_of(result)
    assert_one_item_commit(world, base)
    new = remote_bytes(world, ITEM)
    _assert_only_changed(rivals, new, {"priority": "high"})
    assert frontmatter(new.decode())["title"] == "Rival title", "the rival's title was lost"
    assert b"# Rival title\n" in new


def test_title_push_rewrites_key_and_h1_only(world, monkeypatch) -> None:
    base = world.remote_sha()
    old = remote_bytes(world, ITEM)

    result = invoke(world, monkeypatch, ["update", ITEM_ID, "--title", "New name", "--push"])

    assert result.exit_code == 0, output_of(result)
    assert_one_item_commit(world, base)
    _assert_only_changed(old, remote_bytes(world, ITEM), {"title": "New name"}, h1="New name")


def test_tag_and_untag_push_change_only_tags(world, monkeypatch) -> None:
    base = world.remote_sha()
    old = remote_bytes(world, ITEM)

    result = invoke(
        world, monkeypatch, ["update", ITEM_ID, "--tag", "beta", "--untag", "alpha", "--push"]
    )

    assert result.exit_code == 0, output_of(result)
    assert_one_item_commit(world, base)
    _assert_only_changed(old, remote_bytes(world, ITEM), {"tags": ["beta"]})


def test_body_push_replaces_only_the_body(world, monkeypatch) -> None:
    base = world.remote_sha()
    old = remote_bytes(world, ITEM)

    result = invoke(world, monkeypatch, ["update", ITEM_ID, "--body", "Brand new body.", "--push"])

    assert result.exit_code == 0, output_of(result)
    assert_one_item_commit(world, base)
    new = remote_bytes(world, ITEM)
    assert b"Brand new body." in new and BODY.encode() not in new, new.decode()
    fm_end = old.index(b"\n---\n") + 5
    assert new[:fm_end] == old[:fm_end], "the frontmatter changed"
    assert b"# X\n" in new
    assert history_block(new) == history_block(old), "the status history changed"
    assert new.endswith(COMMENTS.encode()), "the comments changed"


def test_won_on_clean_default_branch_fast_forwards_checkout(world, monkeypatch) -> None:
    assert git(world.a, "symbolic-ref", "--short", "HEAD").strip() == world.default

    result = invoke(world, monkeypatch, ["update", ITEM_ID, "--priority", "high", "--push"])

    assert result.exit_code == 0, output_of(result)
    assert git(world.a, "rev-parse", "HEAD").strip() == world.remote_sha()
    assert (world.a / ITEM).read_bytes() == remote_bytes(world, ITEM)
    assert git(world.a, "status", "--porcelain", "--untracked-files=all") == ""


def test_won_on_default_branch_ahead_leaves_checkout(world, monkeypatch) -> None:
    (world.a / "local.txt").write_text("unpushed\n")
    git(world.a, "add", "local.txt")
    git(world.a, "commit", "-m", "local, unpushed")
    base = world.remote_sha()
    before = snapshot(world.a)

    result = invoke(world, monkeypatch, ["update", ITEM_ID, "--priority", "high", "--push"])

    assert result.exit_code == 0, output_of(result)
    assert_one_item_commit(world, base)
    assert "local.txt" not in world.remote_files(), "the unpushed local commit was published"
    assert snapshot(world.a) == before


# --- dependency edits, checked against the fetched board -------------------------------


def test_add_dep_on_item_only_on_origin_is_known(world, monkeypatch) -> None:
    b_push(world, {f"{EXP_DIR}/EXP-004-four.md": plain("EXP-004", "Four")})
    assert not (world.a / EXP_DIR / "EXP-004-four.md").exists()
    base = world.remote_sha()
    old = remote_bytes(world, ITEM)

    result = invoke(world, monkeypatch, ["update", ITEM_ID, "--add-dep", "EXP-004", "--push"])

    assert result.exit_code == 0, output_of(result)
    assert_one_item_commit(world, base)
    _assert_only_changed(old, remote_bytes(world, ITEM), {"depends_on": ["EXP-004"]})


def test_add_dep_on_item_only_in_local_tree_is_on_no_board(world, monkeypatch) -> None:
    (world.a / EXP_DIR / "EXP-005-five.md").write_text(plain("EXP-005", "Five"))  # untracked
    base = world.remote_sha()

    result = invoke(world, monkeypatch, ["update", ITEM_ID, "--add-dep", "EXP-005", "--push"])

    out = output_of(result)
    assert result.exit_code == 1, out
    assert "no board" in out.lower(), out
    assert world.remote_sha() == base


def test_add_dep_closing_a_cycle_only_on_origin_is_refused(world, monkeypatch) -> None:
    b_push(world, {DEP2: plain("EXP-002", "Two", deps="[EXP-001]")})  # A: EXP-002 has none
    base = world.remote_sha()
    head = git(world.a, "rev-parse", "HEAD").strip()

    result = invoke(world, monkeypatch, ["update", ITEM_ID, "--add-dep", "EXP-002", "--push"])

    out = output_of(result)
    assert result.exit_code == 1, out
    assert "cycle" in out.lower(), out
    assert world.remote_sha() == base
    assert git(world.a, "rev-parse", "HEAD").strip() == head


def test_rm_dep_applies_to_origins_list(world, monkeypatch) -> None:
    push_from_a(world, {ITEM: rich(deps="[EXP-002]")}, "EXP-001 needs EXP-002")
    b_push(world, {ITEM: rich(deps="[EXP-002, EXP-003]")})  # A still says [EXP-002]
    base = world.remote_sha()
    rivals = remote_bytes(world, ITEM)

    result = invoke(world, monkeypatch, ["update", ITEM_ID, "--rm-dep", "EXP-002", "--push"])

    assert result.exit_code == 0, output_of(result)
    assert_one_item_commit(world, base)
    _assert_only_changed(rivals, remote_bytes(world, ITEM), {"depends_on": ["EXP-003"]})


# --- refusals, judged against the fetched item ------------------------------------------


def test_item_not_on_origin_is_refused(world, monkeypatch) -> None:
    rel = f"{EXP_DIR}/EXP-006-six.md"
    (world.a / rel).write_text(plain("EXP-006", "Six"))
    git(world.a, "add", rel)
    git(world.a, "commit", "-m", "EXP-006, local only")
    base = world.remote_sha()
    before = snapshot(world.a)

    result = invoke(world, monkeypatch, ["update", "EXP-006", "--priority", "high", "--push"])

    out = output_of(result)
    assert result.exit_code == 1, out
    assert "EXP-006" in out, out
    assert world.remote_sha() == base
    assert snapshot(world.a) == before


def test_id_duplicated_on_origin_is_refused(world, monkeypatch) -> None:
    b_push(world, {f"{EXP_DIR}/EXP-001-dup.md": plain("EXP-001", "Dup")})
    assert not (world.a / EXP_DIR / "EXP-001-dup.md").exists()
    base = world.remote_sha()

    result = invoke(world, monkeypatch, ["update", ITEM_ID, "--priority", "high", "--push"])

    out = output_of(result)
    assert result.exit_code == 1, out
    assert re.search(r"more than one|duplicate", out, re.I), out
    assert world.remote_sha() == base


def test_bad_priority_is_refused(world, monkeypatch) -> None:
    base = world.remote_sha()
    before = snapshot(world.a)

    result = invoke(world, monkeypatch, ["update", ITEM_ID, "--priority", "urgent", "--push"])

    assert result.exit_code == 1, output_of(result)
    assert world.remote_sha() == base
    assert snapshot(world.a) == before


def test_no_change_is_noop_nothing_pushed(world, monkeypatch) -> None:
    base = world.remote_sha()
    before = snapshot(world.a)

    result = invoke(world, monkeypatch, ["update", ITEM_ID, "--priority", "medium", "--push"])

    assert result.exit_code == 0, output_of(result)
    assert world.remote_sha() == base
    assert snapshot(world.a) == before


def test_push_with_no_commit_is_refused(world, monkeypatch) -> None:
    base = world.remote_sha()
    before = snapshot(world.a)

    result = invoke(
        world, monkeypatch, ["update", ITEM_ID, "--priority", "high", "--push", "--no-commit"]
    )

    assert result.exit_code == 1, output_of(result)
    assert world.remote_sha() == base
    assert snapshot(world.a) == before


# --- races, through the seam -------------------------------------------------------------


def test_unrelated_commit_retries_and_wins(world) -> None:
    base = world.remote_sha()
    rec = Recorder(lambda attempt: rival(world) if attempt == 0 else None)

    out = upd(world.a, rec, priority="high")

    assert out.kind == "won", out.message
    assert out.exit_code == 0
    assert out.sha == world.remote_sha()
    history = git(world.remote, "rev-list", f"{base}..{world.remote_sha()}").split()
    assert len(history) == 2, history
    ours, theirs = history
    assert commit_files(world.remote, theirs) == ["other-0.txt"]
    assert commit_files(world.remote, ours) == [ITEM]
    assert frontmatter(world.remote_show(ITEM))["priority"] == "high"
    assert rec.seams == [0, 1]
    assert rec.sleeps == [pytest.approx(JITTER * 1)]


def test_same_field_rival_is_reapplied_last_writer_wins(world) -> None:
    rivals = rich(title="Rival title", priority="low")
    rec = Recorder(lambda attempt: b_push(world, {ITEM: rivals}) if attempt == 0 else None)

    out = upd(world.a, rec, priority="high")

    assert out.kind == "won", out.message
    assert rec.seams == [0, 1], "A did not retry on the rival's state"
    theirs = git(world.remote, "rev-parse", f"{world.default}~1").strip()
    assert commit_files(world.remote, theirs) == [ITEM]
    new = remote_bytes(world, ITEM)
    _assert_only_changed(rivals.encode(), new, {"priority": "high"})
    assert frontmatter(new.decode())["title"] == "Rival title"


def test_rival_wrote_the_same_value_is_noop(world) -> None:
    rec = Recorder(
        lambda attempt: b_push(world, {ITEM: rich(priority="high")}) if attempt == 0 else None
    )

    out = upd(world.a, rec, priority="high")

    assert out.kind == "noop", out.message
    assert out.exit_code == 0
    assert out.sha is None
    assert commit_files(world.remote, world.remote_sha()) == [ITEM]
    assert subject(world.remote, world.remote_sha()) == "rival", "A pushed after the rival"
    assert rec.seams == [0]


def test_rival_closing_a_cycle_before_push_is_refused_on_retry(world) -> None:
    rec = Recorder(
        lambda attempt: b_push(world, {DEP2: plain("EXP-002", "Two", deps="[EXP-001]")})
        if attempt == 0 else None
    )

    out = upd(world.a, rec, add_depends_on=["EXP-002"])

    assert out.kind == "refused", out.message
    assert out.exit_code == 1
    assert "cycle" in out.message.lower(), out.message
    assert rec.seams == [0]
    assert subject(world.remote, world.remote_sha()) == "rival"
    assert frontmatter(world.remote_show(ITEM))["depends_on"] == []


def test_unrelated_commit_every_attempt_is_busy(world) -> None:
    rec = Recorder(lambda attempt: rival(world, attempt))

    out = upd(world.a, rec, priority="high")

    assert out.kind == "busy", out.message
    assert out.exit_code == 5
    assert rec.seams == [0, 1, 2, 3, 4]
    assert frontmatter(world.remote_show(ITEM))["priority"] == "medium"


def test_unreachable_remote_exits_4(world, monkeypatch) -> None:
    git(world.a, "remote", "set-url", "origin", str(world.a.parent / "missing.git"))
    dirty_feature_branch(world)
    remote_before = world.remote_sha()
    before = snapshot(world.a)

    result = invoke(world, monkeypatch, ["update", ITEM_ID, "--priority", "high", "--push"])

    assert result.exit_code == 4, output_of(result)
    assert world.remote_sha() == remote_before
    assert snapshot(world.a) == before, "an unreachable remote must not fall back to local"


def test_unreachable_remote_service_outcome(world) -> None:
    git(world.a, "remote", "set-url", "origin", str(world.a.parent / "missing.git"))
    rec = Recorder()

    out = upd(world.a, rec, priority="high")

    assert out.kind == "unreachable", out.message
    assert out.exit_code == 4
    assert rec.seams == []


def test_declining_update_hook_exits_6_after_one_push(world, monkeypatch, tmp_path) -> None:
    counter = tmp_path / "hook-runs"
    hook = world.remote / "hooks" / "update"
    hook.write_text(
        "#!/bin/sh\n"
        f"echo run >> '{counter}'\n"
        "echo 'kanban-guard: main is frozen for release' >&2\n"
        "exit 1\n"
    )
    hook.chmod(0o755)
    remote_before = world.remote_sha()

    result = invoke(world, monkeypatch, ["update", ITEM_ID, "--priority", "high", "--push"])

    out = output_of(result)
    assert result.exit_code == 6, out
    assert "main is frozen for release" in out, out
    assert counter.read_text().splitlines() == ["run"], "the refused push was retried"
    assert world.remote_sha() == remote_before


# --- no remote -----------------------------------------------------------------------------


def test_no_remote_commits_locally_with_note(world, monkeypatch) -> None:
    git(world.a, "remote", "remove", "origin")
    (world.a / "staged.txt").write_text("user staged work\n")
    git(world.a, "add", "staged.txt")
    head_before = git(world.a, "rev-parse", "HEAD").strip()
    old = (world.a / ITEM).read_bytes()

    result = invoke(world, monkeypatch, ["update", ITEM_ID, "--priority", "high", "--push"])

    out = output_of(result)
    assert result.exit_code == 0, out
    assert "no remote" in out.lower(), out
    head = git(world.a, "rev-parse", "HEAD").strip()
    assert git(world.a, "rev-list", f"{head_before}..{head}").split() == [head]
    assert commit_files(world.a, head) == [ITEM]
    _assert_only_changed(old, (world.a / ITEM).read_bytes(), {"priority": "high"})
    assert "staged.txt" in git(world.a, "diff", "--cached", "--name-only").split()


def test_no_remote_service_outcome_is_local(world) -> None:
    git(world.a, "remote", "remove", "origin")

    out = upd(world.a, priority="high")

    assert out.kind == "local", out.message
    assert out.exit_code == 0
    assert "no remote" in out.message.lower(), out.message


# --- control: `update` without --push is unchanged by PR D ------------------------------------


def test_control_update_without_push_commits_locally_only(world, monkeypatch) -> None:
    base = world.remote_sha()
    head_before = git(world.a, "rev-parse", "HEAD").strip()
    old = (world.a / ITEM).read_bytes()

    result = invoke(world, monkeypatch, ["update", ITEM_ID, "--priority", "high"])

    assert result.exit_code == 0, output_of(result)
    assert world.remote_sha() == base, "update without --push pushed"
    head = git(world.a, "rev-parse", "HEAD").strip()
    assert git(world.a, "rev-list", f"{head_before}..{head}").split() == [head]
    assert commit_files(world.a, head) == [ITEM]
    _assert_only_changed(old, (world.a / ITEM).read_bytes(), {"priority": "high"})


def test_control_rich_fixture_parses() -> None:
    """The fixture is a valid item: its frontmatter parses and keeps its extra key."""
    fm = yaml.safe_load(rich().split("---\n")[1])
    assert fm["custom_key"] == "keepme" and fm["status"] == "provisioning"
