"""Issue #1251, part 3: ``epic add EPIC ITEM --push`` and ``voyage add EPIC ITEM --push``.

``epic add`` / ``voyage add`` append the epic's own ID to the item's ``related:`` list
and stop at the working tree (3.3.0: no commit, no push). Every other board write that
matters goes through ``sync_and_push``, the compare-and-swap from #574; this gives the
link the same ``--push``, the way ``update --push`` (tests/issues/test_574_update_push.py)
does for field edits. That file's real-git harness is reused: a bare remote, clone A
under test and rival clone B, plus a seam that forces a lost race.

Decided shape (the driver implements it):

- CLI: ``yurtle-kanban epic add EPIC ITEM --push`` and ``voyage add EPIC ITEM --push``.
  Output and exit codes follow ``update --push`` (``_print_outcome``): 0 won, noop or
  local; 1 refused (an epic or item not on origin, an item ID held by two files on
  origin); 4 unreachable, 5 busy, 6 push refused.
- Service: ``KanbanService.link_related_push(item_id, target_id, *, sleep, jitter,
  seam) -> Outcome``, a ``sync_and_push`` whose mutate
    * checks that the epic exists ON ORIGIN and writes origin's spelling of its ID;
    * appends it to the item's ``related:`` as ORIGIN's tree has it, keeping every
      entry already there, including one a rival just added;
    * returns noop when origin's item already lists it (folded, as ``_links``).
  It never touches the worktree, index or branch.
- No remote: a local commit of the item file only, exit 0.
- Without ``--push`` nothing changes: the link is written to the working tree and
  nothing is committed or pushed.

Ambiguities resolved here (the test partner's reading; the driver may challenge):

a. The brief says the commit message "matches today's local link commit", but in
   3.3.0 ``_do_add`` does not commit at all. So the subject is matched loosely: it
   names the item and the epic by their origin IDs.
b. The happy path runs once per group (``epic`` and ``voyage``) on the nautical World,
   whose epics are ``VOY-`` voyages; ``_do_add`` itself is theme-blind.
c. "Already linked" is judged with the fold (``_links``): origin's ``voy-001`` counts.
"""

from __future__ import annotations

import re
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
from tests.issues.test_574_update_push import remote_bytes, subject
from tests.issues.test_576_cli_update import _assert_only_changed
from tests.issues.test_585_create_push_loop import EXP_DIR, World, git
from tests.issues.test_590_next_id_and_hdd_ids import b_push
from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import main
from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.service import KanbanService

ITEM_ID = "EXP-003"
ITEM = f"{EXP_DIR}/EXP-003-item.md"
EXP1 = f"{EXP_DIR}/EXP-001-one.md"
EPIC = "VOY-001"
EPIC2 = "VOY-002"
EPIC_FILE = f"{EXP_DIR}/VOY-001-first.md"
EPIC2_FILE = f"{EXP_DIR}/VOY-002-second.md"


# --- harness ---------------------------------------------------------------------


def item(related: str = "[EXP-001]", item_id: str = ITEM_ID, title: str = "Item") -> str:
    """An expedition with a `related:` list already holding one entry, a key after it
    and a body, so a whole-file rewrite shows up."""
    return (
        f'---\nid: {item_id}\ntitle: "{title}"\ntype: expedition\nstatus: backlog\n'
        f"priority: medium\nrelated: {related}\ncustom_key: keepme\n---\n\n"
        f"# {title}\n\nA description long enough to be a real item.\n"
    )


def voyage(item_id: str, title: str) -> str:
    return (
        f'---\nid: {item_id}\ntitle: "{title}"\ntype: voyage\nstatus: backlog\n'
        f"priority: high\n---\n\n# {title}\n\nA voyage grouping related work.\n"
    )


def plain(item_id: str, title: str) -> str:
    return (
        f'---\nid: {item_id}\ntitle: "{title}"\ntype: expedition\nstatus: backlog\n'
        f"---\n\n# {title}\n\nA description long enough.\n"
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
    """Origin and both clones hold EXP-001, EXP-003 (related: [EXP-001]), VOY-001 and
    VOY-002, on the nautical theme."""
    w = World(tmp_path)
    push_from_a(
        w,
        {
            EXP1: plain("EXP-001", "One"),
            ITEM: item(),
            EPIC_FILE: voyage(EPIC, "First voyage"),
            EPIC2_FILE: voyage(EPIC2, "Second voyage"),
        },
        "seed EXP-001, EXP-003, VOY-001, VOY-002",
    )
    return w


def service(clone: Path) -> KanbanService:
    return KanbanService(KanbanConfig.load(clone / ".kanban" / "config.yaml"), clone)


def link(clone: Path, target: str = EPIC, rec: Recorder | None = None,
         item_id: str = ITEM_ID) -> Any:
    """`epic add TARGET ITEM --push` at the service level (the CLI has no seam)."""
    rec = rec or Recorder()
    return service(clone).link_related_push(
        item_id, target, sleep=rec.sleep, jitter=rec.jitter, seam=rec.seam
    )


def invoke(world: World, monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> Any:
    monkeypatch.chdir(world.a)
    return CliRunner().invoke(main, argv)


def origin_related(world: World) -> list[str]:
    return [str(r) for r in frontmatter(world.remote_show(ITEM)).get("related") or []]


def assert_one_item_commit(world: World, base: str) -> str:
    """Origin's tip is exactly one commit on `base`, touching only the item."""
    tip = world.remote_sha()
    assert git(world.remote, "rev-list", f"{base}..{tip}").split() == [tip], (
        "origin must get exactly one new commit"
    )
    assert commit_files(world.remote, tip) == [ITEM], "the link commit touched other files"
    return tip


# --- 1 / 6. the link lands on origin; A's checkout is untouched ------------------------


@pytest.mark.parametrize("group", ["epic", "voyage"])
def test_add_push_links_on_origin_and_leaves_checkout(world, monkeypatch, group) -> None:
    dirty_feature_branch(world)
    base = world.remote_sha()
    old = remote_bytes(world, ITEM)
    before = snapshot(world.a)

    result = invoke(world, monkeypatch, [group, "add", EPIC, ITEM_ID, "--push"])

    assert result.exit_code == 0, output_of(result)
    tip = assert_one_item_commit(world, base)
    _assert_only_changed(old, remote_bytes(world, ITEM), {"related": ["EXP-001", EPIC]})
    msg = subject(world.remote, tip)
    assert ITEM_ID in msg and EPIC in msg, msg
    assert snapshot(world.a) == before, "A's checkout changed"


def test_won_on_clean_default_branch_fast_forwards_checkout(world, monkeypatch) -> None:
    result = invoke(world, monkeypatch, ["epic", "add", EPIC, ITEM_ID, "--push"])

    assert result.exit_code == 0, output_of(result)
    assert git(world.a, "rev-parse", "HEAD").strip() == world.remote_sha()
    assert (world.a / ITEM).read_bytes() == remote_bytes(world, ITEM)
    assert git(world.a, "status", "--porcelain", "--untracked-files=all") == ""


def test_link_applies_to_origins_related_and_keeps_rivals_entry(world, monkeypatch) -> None:
    """A rival's entry that A has never seen survives: the append is to origin's list."""
    b_push(world, {ITEM: item(related="[EXP-001, EXP-009]")})
    assert "EXP-009" not in (world.a / ITEM).read_text()
    base = world.remote_sha()

    result = invoke(world, monkeypatch, ["epic", "add", EPIC, ITEM_ID, "--push"])

    assert result.exit_code == 0, output_of(result)
    assert_one_item_commit(world, base)
    assert origin_related(world) == ["EXP-001", "EXP-009", EPIC]


def test_epic_only_on_origin_is_found(world, monkeypatch) -> None:
    """The epic is judged on origin: one A has never pulled still links."""
    b_push(world, {f"{EXP_DIR}/VOY-004-fresh.md": voyage("VOY-004", "Fresh voyage")})
    assert not (world.a / EXP_DIR / "VOY-004-fresh.md").exists()
    base = world.remote_sha()

    result = invoke(world, monkeypatch, ["epic", "add", "VOY-004", ITEM_ID, "--push"])

    assert result.exit_code == 0, output_of(result)
    assert_one_item_commit(world, base)
    assert origin_related(world) == ["EXP-001", "VOY-004"]


# --- 2. a lost race: both links survive -------------------------------------------------


def test_lost_race_to_rival_link_retries_and_keeps_both(world) -> None:
    """B links the item to VOY-002 (by its own `link_related_push`) just before A's
    push; A retries on the new base and origin lists both."""
    rec = Recorder(
        lambda attempt: link(world.b, EPIC2) if attempt == 0 else None
    )

    out = link(world.a, EPIC, rec)

    assert out.kind == "won", out.message
    assert out.exit_code == 0
    assert rec.seams == [0, 1], "A did not retry on the rival's state"
    assert out.sha == world.remote_sha()
    theirs = git(world.remote, "rev-parse", f"{world.default}~1").strip()
    assert commit_files(world.remote, theirs) == [ITEM]
    assert origin_related(world) == ["EXP-001", EPIC2, EPIC]


def test_lost_race_to_raw_rival_edit_keeps_both(world) -> None:
    """The same race against a rival that rewrote `related:` by hand."""
    rec = Recorder(
        lambda attempt: b_push(world, {ITEM: item(related=f"[EXP-001, {EPIC2}]")})
        if attempt == 0 else None
    )

    out = link(world.a, EPIC, rec)

    assert out.kind == "won", out.message
    assert rec.seams == [0, 1]
    assert origin_related(world) == ["EXP-001", EPIC2, EPIC]
    assert frontmatter(world.remote_show(ITEM))["custom_key"] == "keepme"


def test_unrelated_commit_retries_and_wins(world) -> None:
    base = world.remote_sha()
    rec = Recorder(lambda attempt: rival(world) if attempt == 0 else None)

    out = link(world.a, EPIC, rec)

    assert out.kind == "won", out.message
    history = git(world.remote, "rev-list", f"{base}..{world.remote_sha()}").split()
    assert len(history) == 2, history
    ours, theirs = history
    assert commit_files(world.remote, theirs) == ["other-0.txt"]
    assert commit_files(world.remote, ours) == [ITEM]
    assert origin_related(world) == ["EXP-001", EPIC]


def test_unrelated_commit_every_attempt_is_busy(world) -> None:
    rec = Recorder(lambda attempt: rival(world, attempt))

    out = link(world.a, EPIC, rec)

    assert out.kind == "busy", out.message
    assert out.exit_code == 5
    assert origin_related(world) == ["EXP-001"]


# --- 3. already linked on origin: noop ------------------------------------------------


@pytest.mark.parametrize("spelling", [EPIC, "voy-001"])
def test_already_linked_on_origin_is_noop(world, monkeypatch, spelling) -> None:
    b_push(world, {ITEM: item(related=f"[EXP-001, {spelling}]")})  # A doesn't know
    base = world.remote_sha()
    dirty_feature_branch(world)
    before = snapshot(world.a)

    result = invoke(world, monkeypatch, ["epic", "add", EPIC, ITEM_ID, "--push"])

    assert result.exit_code == 0, output_of(result)
    assert world.remote_sha() == base, "a noop pushed"
    assert snapshot(world.a) == before


def test_rival_wrote_the_same_link_is_noop(world) -> None:
    rec = Recorder(
        lambda attempt: b_push(world, {ITEM: item(related=f"[EXP-001, {EPIC}]")})
        if attempt == 0 else None
    )

    out = link(world.a, EPIC, rec)

    assert out.kind == "noop", out.message
    assert out.exit_code == 0
    assert out.sha is None
    assert subject(world.remote, world.remote_sha()) == "rival", "A pushed after the rival"
    assert rec.seams == [0]


# --- 4. refusals, judged on origin -----------------------------------------------------


def _commit_local_only(world: World, rel: str, text: str) -> None:
    (world.a / rel).write_text(text)
    git(world.a, "add", rel)
    git(world.a, "commit", "-m", f"{rel}, local only")


def test_epic_only_in_local_checkout_is_refused(world, monkeypatch) -> None:
    _commit_local_only(world, f"{EXP_DIR}/VOY-005-local.md", voyage("VOY-005", "Local"))
    base = world.remote_sha()
    before = snapshot(world.a)

    result = invoke(world, monkeypatch, ["epic", "add", "VOY-005", ITEM_ID, "--push"])

    out = output_of(result)
    assert result.exit_code == 1, out
    assert "VOY-005" in out, out
    assert world.remote_sha() == base
    assert snapshot(world.a) == before


def test_item_only_in_local_checkout_is_refused(world, monkeypatch) -> None:
    _commit_local_only(world, f"{EXP_DIR}/EXP-006-local.md", item(item_id="EXP-006"))
    base = world.remote_sha()
    before = snapshot(world.a)

    result = invoke(world, monkeypatch, ["epic", "add", EPIC, "EXP-006", "--push"])

    out = output_of(result)
    assert result.exit_code == 1, out
    assert "EXP-006" in out, out
    assert world.remote_sha() == base
    assert snapshot(world.a) == before


def test_unknown_epic_service_outcome_is_refused(world) -> None:
    out = link(world.a, "VOY-077")

    assert out.kind == "refused", out.message
    assert out.exit_code == 1
    assert "VOY-077" in out.message, out.message


def test_item_id_duplicated_on_origin_is_refused(world, monkeypatch) -> None:
    b_push(world, {f"{EXP_DIR}/EXP-003-dup.md": item(title="Dup")})
    assert not (world.a / EXP_DIR / "EXP-003-dup.md").exists()
    base = world.remote_sha()

    result = invoke(world, monkeypatch, ["epic", "add", EPIC, ITEM_ID, "--push"])

    out = output_of(result)
    assert result.exit_code == 1, out
    assert re.search(r"more than one|duplicate", out, re.I), out
    assert world.remote_sha() == base


def test_unreachable_remote_exits_4(world, monkeypatch) -> None:
    git(world.a, "remote", "set-url", "origin", str(world.a.parent / "missing.git"))
    dirty_feature_branch(world)
    before = snapshot(world.a)

    result = invoke(world, monkeypatch, ["epic", "add", EPIC, ITEM_ID, "--push"])

    assert result.exit_code == 4, output_of(result)
    assert snapshot(world.a) == before, "an unreachable remote must not fall back to local"


# --- 5. ID spelling: origin's epic ID is written ------------------------------------


def test_folded_spellings_write_origins_epic_id(world, monkeypatch) -> None:
    base = world.remote_sha()

    result = invoke(world, monkeypatch, ["epic", "add", "voy-1", "exp-3", "--push"])

    assert result.exit_code == 0, output_of(result)
    assert_one_item_commit(world, base)
    assert origin_related(world) == ["EXP-001", EPIC]


# --- 7. no remote ------------------------------------------------------------------------


def test_no_remote_commits_locally(world, monkeypatch) -> None:
    git(world.a, "remote", "remove", "origin")
    (world.a / "staged.txt").write_text("user staged work\n")
    git(world.a, "add", "staged.txt")
    head_before = git(world.a, "rev-parse", "HEAD").strip()
    old = (world.a / ITEM).read_bytes()

    result = invoke(world, monkeypatch, ["epic", "add", EPIC, ITEM_ID, "--push"])

    out = output_of(result)
    assert result.exit_code == 0, out
    assert "no remote" in out.lower(), out
    head = git(world.a, "rev-parse", "HEAD").strip()
    assert git(world.a, "rev-list", f"{head_before}..{head}").split() == [head]
    assert commit_files(world.a, head) == [ITEM]
    _assert_only_changed(old, (world.a / ITEM).read_bytes(), {"related": ["EXP-001", EPIC]})
    assert "staged.txt" in git(world.a, "diff", "--cached", "--name-only").split()


def test_no_remote_service_outcome_is_local(world) -> None:
    git(world.a, "remote", "remove", "origin")

    out = link(world.a, EPIC)

    assert out.kind == "local", out.message
    assert out.exit_code == 0


# --- 8. control: without --push nothing changes; --help lists it ----------------------


@pytest.mark.parametrize("group", ["epic", "voyage"])
def test_control_add_without_push_writes_worktree_only(world, monkeypatch, group) -> None:
    base = world.remote_sha()
    head_before = git(world.a, "rev-parse", "HEAD").strip()
    old = (world.a / ITEM).read_bytes()

    result = invoke(world, monkeypatch, [group, "add", EPIC, ITEM_ID])

    assert result.exit_code == 0, output_of(result)
    assert world.remote_sha() == base, f"{group} add without --push pushed"
    assert git(world.a, "rev-parse", "HEAD").strip() == head_before, "it committed"
    _assert_only_changed(old, (world.a / ITEM).read_bytes(), {"related": ["EXP-001", EPIC]})


@pytest.mark.parametrize("group", ["epic", "voyage"])
def test_help_lists_push(group) -> None:
    result = CliRunner().invoke(main, [group, "add", "--help"])

    assert result.exit_code == 0, result.output
    assert "--push" in result.output, result.output


# --- r1 (review of 47010adc): related: as written, as plain `epic add` -----------------------


@pytest.mark.parametrize(
    "written,expected",
    [
        ('[exp-001, "M-V17-002a", "https://x.org/A"]',
         ["exp-001", "M-V17-002a", "https://x.org/A", "VOY-001"]),
        ('["a, b"]', ["a, b", "VOY-001"]),
        ("[EXP-001, exp-001]", ["EXP-001", "exp-001", "VOY-001"]),
    ],
    ids=["spelling-kept", "comma-element-kept", "case-twins-kept"],
)
def test_existing_related_entries_are_kept_as_written(world, monkeypatch, written, expected):
    """r1 B2: only the link is added; every entry keeps its spelling, as plain
    `epic add` keeps it (no folding, no dedupe)."""
    b_push(world, {ITEM: item(related=written)})

    result = invoke(world, monkeypatch, ["epic", "add", EPIC, ITEM_ID, "--push"])

    assert result.exit_code == 0, output_of(result)
    assert frontmatter(remote_bytes(world, ITEM).decode())["related"] == expected


@pytest.mark.parametrize("written", ["{EXP-001: parent}", "5"], ids=["mapping", "number"])
def test_non_list_related_is_refused_nothing_pushed(world, monkeypatch, written) -> None:
    """r1 B1 (#188): a mapping or a number isn't a list of IDs; plain `epic add`
    refuses it, and so does `--push`, rather than overwriting it."""
    b_push(world, {ITEM: item(related=written)})
    base = world.remote_sha()

    result = invoke(world, monkeypatch, ["epic", "add", EPIC, ITEM_ID, "--push"])

    assert result.exit_code == 1, output_of(result)
    assert "not a list of IDs" in output_of(result)
    assert world.remote_sha() == base, "something was pushed"
