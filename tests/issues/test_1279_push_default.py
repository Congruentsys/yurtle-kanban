"""Issue #1279: ``--push`` becomes the default for comment, rank, epic/voyage add and move.

Each of the five commands takes ``--push/--no-push``. With neither flag it pushes when
the repo has an ``origin`` remote, through the compare-and-swap path #1251 added, with
the same exit codes. ``--no-push``, or no ``origin`` remote, gives today's plain local
behaviour. ``rank --no-commit`` implies ``--no-push``. ``create``, ``update``,
``epic create`` and ``voyage create`` keep ``--push`` opt-in.

Harness: #1251's (tests/issues/test_1251_*_push.py), on the #585 ``World`` (a bare
origin, clone A under test and rival clone B). The CLI runs through CliRunner on
``main``, chdir'd into A.

Readings chosen by the test partner (the driver may challenge):

a. "No flag pushes" is pinned as #1251's explicit ``--push`` result on a clean default
   branch: origin gets exactly one new commit, touching only the item, with #1251's
   subject (``Add comment to``, ``Rank .. as #N``, ``Move .. to ..``; for a link the
   subject names the item and the epic), and A's checkout is fast-forwarded and clean.
b. ``--no-push`` is today's plain path, read from the 3.3 code: ``comment``, ``rank``
   and ``move`` each make ONE local commit touching only the item (subjects as above);
   ``epic add`` / ``voyage add`` write the working tree only, with no commit. Origin
   is unchanged in every case.
c. "No flag is the same as ``--push``" is pinned by cases where the two paths differ
   today: a rival's title edit on origin survives the comment/rank and the comment
   lands on origin; an item or epic that exists only in A's checkout is refused (exit
   1, nothing pushed) where the plain path would accept it; a move that is legal on A's
   stale copy but illegal on origin's (origin says done) is refused, exit 1. An
   unknown item exits 1 with nothing pushed.
d. No ``origin`` remote, no flag: the PLAIN path, not ``--push``'s ``local`` outcome.
   It is told apart by the plain command's own output (``Added comment to``, ``Moved
   .. to``, ``Linked``) and for ``epic add`` by not committing at all.
e. ``rank N --no-commit`` with no push flag, in a clone with an origin: exit 0, the
   file written but not committed, HEAD and origin unchanged.
f. ``--help``: the text names ``--no-push`` and says pushing is the default, matched
   loosely: a "default" near "push", once the existing "origin's default branch" and
   "default $YURTLE_AGENT" phrases are taken out.
g. Controls (green now and after): ``update``, ``create``, ``epic create`` and
   ``voyage create`` without ``--push`` leave origin unchanged.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from tests.issues.test_574_claim import frontmatter, item_text, output_of, push_from_a
from tests.issues.test_574_sync_and_push import commit_files
from tests.issues.test_585_create_push_loop import EXP_DIR, World, git
from tests.issues.test_590_next_id_and_hdd_ids import b_push
from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import main

ITEM_ID = "EXP-001"
ITEM = f"{EXP_DIR}/EXP-001-x.md"
EPIC = "VOY-001"
EPIC_FILE = f"{EXP_DIR}/VOY-001-first.md"
AGENT = "agent-A"
COMMANDS = ["comment", "rank", "move", "epic add", "voyage add"]


# --- harness ---------------------------------------------------------------------


def voyage(item_id: str, title: str) -> str:
    return (
        f'---\nid: {item_id}\ntitle: "{title}"\ntype: voyage\nstatus: backlog\n'
        f"priority: high\n---\n\n# {title}\n\nA voyage grouping related work.\n"
    )


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    monkeypatch.delenv("YURTLE_AGENT", raising=False)
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


@pytest.fixture
def world(tmp_path: Path) -> World:
    """Origin and both clones hold EXP-001 (ready, unranked, no related) and VOY-001."""
    w = World(tmp_path)
    push_from_a(
        w, {ITEM: item_text("ready"), EPIC_FILE: voyage(EPIC, "First")}, "seed EXP-001, VOY-001"
    )
    return w


def invoke(world: World, monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> Any:
    monkeypatch.chdir(world.a)
    return CliRunner().invoke(main, argv)


def argv_for(cmd: str, item_id: str = ITEM_ID, epic: str = EPIC) -> list[str]:
    """The command's plain argv, with no push flag."""
    if cmd == "comment":
        return ["comment", item_id, "--agent", AGENT, "--body", "a note"]
    if cmd == "rank":
        return ["rank", item_id, "2"]
    if cmd == "move":
        return ["move", item_id, "in_progress", "--agent", AGENT]
    group = cmd.split()[0]
    return [group, "add", epic, item_id]


def subject(repo: Path, sha: str) -> str:
    return git(repo, "log", "-1", "--format=%s", sha).strip()


def head(clone: Path) -> str:
    return git(clone, "rev-parse", "HEAD").strip()


def assert_changed(cmd: str, text: str) -> None:
    """`text` (an item file) carries `cmd`'s change."""
    fm = frontmatter(text)
    if cmd == "comment":
        assert f"### {AGENT} (" in text and "a note" in text, text
    elif cmd == "rank":
        assert fm.get("priority_rank") == 2, fm
    elif cmd == "move":
        assert fm["status"] == "underway", fm
    else:
        assert EPIC in [str(r) for r in fm.get("related") or []], fm


def assert_subject(cmd: str, msg: str) -> None:
    if cmd == "comment":
        assert msg == f"Add comment to {ITEM_ID}", msg
    elif cmd == "rank":
        assert msg == f"Rank {ITEM_ID} as #2", msg
    elif cmd == "move":
        assert msg == f"Move {ITEM_ID} to in_progress", msg
    else:
        assert ITEM_ID in msg and EPIC in msg, msg


def only_local(world: World, rel: str, text: str) -> None:
    """A commits `rel` on its own branch tip; origin never sees it."""
    (world.a / rel).write_text(text)
    git(world.a, "add", rel)
    git(world.a, "commit", "-m", f"{rel}, local only")


# --- a. no flag pushes, in a clone with an origin -------------------------------------


@pytest.mark.parametrize("cmd", COMMANDS)
def test_no_flag_pushes_one_commit_and_fast_forwards(world, monkeypatch, cmd) -> None:
    base = world.remote_sha()

    result = invoke(world, monkeypatch, argv_for(cmd))

    assert result.exit_code == 0, output_of(result)
    tip = world.remote_sha()
    assert git(world.remote, "rev-list", f"{base}..{tip}").split() == [tip], (
        f"{cmd} with no flag did not push exactly one commit to origin"
    )
    assert commit_files(world.remote, tip) == [ITEM]
    assert_subject(cmd, subject(world.remote, tip))
    assert_changed(cmd, world.remote_show(ITEM))
    assert head(world.a) == tip, "A's checkout was not fast-forwarded"
    assert (world.a / ITEM).read_text() == world.remote_show(ITEM)
    assert git(world.a, "status", "--porcelain", "--untracked-files=all") == ""


# --- b. --no-push is today's plain path ---------------------------------------------------


@pytest.mark.parametrize("cmd", ["comment", "rank", "move"])
def test_no_push_commits_locally_only(world, monkeypatch, cmd) -> None:
    base = world.remote_sha()
    before = head(world.a)

    result = invoke(world, monkeypatch, [*argv_for(cmd), "--no-push"])

    assert result.exit_code == 0, output_of(result)
    assert world.remote_sha() == base, f"{cmd} --no-push pushed"
    tip = head(world.a)
    assert git(world.a, "rev-list", f"{before}..{tip}").split() == [tip]
    assert commit_files(world.a, tip) == [ITEM]
    assert_subject(cmd, subject(world.a, tip))
    assert_changed(cmd, (world.a / ITEM).read_text())


@pytest.mark.parametrize("cmd", ["epic add", "voyage add"])
def test_add_no_push_writes_worktree_only(world, monkeypatch, cmd) -> None:
    base = world.remote_sha()
    before = head(world.a)

    result = invoke(world, monkeypatch, [*argv_for(cmd), "--no-push"])

    assert result.exit_code == 0, output_of(result)
    assert world.remote_sha() == base, f"{cmd} --no-push pushed"
    assert head(world.a) == before, f"{cmd} --no-push committed"
    assert_changed(cmd, (world.a / ITEM).read_text())


# --- c. no flag is the same as --push: origin's tree judges, refusals exit 1 ----------------


def rival_title(world: World) -> None:
    """B retitles EXP-001 on origin; A's copy stays stale."""
    b_push(world, {ITEM: item_text("ready", title="Rival")})
    assert frontmatter((world.a / ITEM).read_text())["title"] == "X"


@pytest.mark.parametrize("cmd", ["comment", "rank"])
def test_no_flag_applies_to_origins_item_keeping_rivals_edit(world, monkeypatch, cmd) -> None:
    rival_title(world)
    base = world.remote_sha()

    result = invoke(world, monkeypatch, argv_for(cmd))

    assert result.exit_code == 0, output_of(result)
    tip = world.remote_sha()
    assert git(world.remote, "rev-list", f"{base}..{tip}").split() == [tip], "nothing pushed"
    text = world.remote_show(ITEM)
    assert frontmatter(text)["title"] == "Rival", "the rival's title was lost"
    assert_changed(cmd, text)


@pytest.mark.parametrize("cmd", COMMANDS)
def test_no_flag_unknown_item_exits_1_nothing_pushed(world, monkeypatch, cmd) -> None:
    base = world.remote_sha()
    before = head(world.a)

    result = invoke(world, monkeypatch, argv_for(cmd, item_id="EXP-999"))

    assert result.exit_code == 1, output_of(result)
    assert world.remote_sha() == base
    assert head(world.a) == before


@pytest.mark.parametrize("cmd", ["comment", "rank", "move", "epic add"])
def test_no_flag_item_only_in_local_checkout_is_refused(world, monkeypatch, cmd) -> None:
    """--push judges origin's tree: an item A alone has is unknown there."""
    rel = f"{EXP_DIR}/EXP-006-six.md"
    only_local(world, rel, item_text("ready", item_id="EXP-006", title="Six"))
    base = world.remote_sha()

    result = invoke(world, monkeypatch, argv_for(cmd, item_id="EXP-006"))

    out = output_of(result)
    assert result.exit_code == 1, out
    assert "EXP-006" in out, out
    assert world.remote_sha() == base


@pytest.mark.parametrize("cmd", ["epic add", "voyage add"])
def test_no_flag_epic_only_on_origin_is_found(world, monkeypatch, cmd) -> None:
    b_push(world, {f"{EXP_DIR}/VOY-002-second.md": voyage("VOY-002", "Second")})
    assert not (world.a / EXP_DIR / "VOY-002-second.md").exists()
    base = world.remote_sha()

    result = invoke(world, monkeypatch, argv_for(cmd, epic="VOY-002"))

    assert result.exit_code == 0, output_of(result)
    tip = world.remote_sha()
    assert git(world.remote, "rev-list", f"{base}..{tip}").split() == [tip]
    related = [str(r) for r in frontmatter(world.remote_show(ITEM)).get("related") or []]
    assert "VOY-002" in related, related


def test_no_flag_move_illegal_on_origin_is_refused(world, monkeypatch) -> None:
    """A's copy says ready (in_progress is legal); origin's says done (nothing is)."""
    b_push(world, {ITEM: item_text("done")})
    base = world.remote_sha()
    before = head(world.a)

    result = invoke(world, monkeypatch, argv_for("move"))

    assert result.exit_code == 1, output_of(result)
    assert world.remote_sha() == base, "an illegal move was pushed"
    assert head(world.a) == before, "an illegal move was committed locally"


# --- d. no origin remote, no flag: today's plain path ----------------------------------------


@pytest.mark.parametrize("cmd,said", [
    ("comment", f"Added comment to {ITEM_ID}"),
    ("move", f"Moved {ITEM_ID} to"),
    ("rank", f"Ranked {ITEM_ID} as #2"),
])
def test_no_origin_no_flag_is_plain_local_commit(world, monkeypatch, cmd, said) -> None:
    git(world.a, "remote", "remove", "origin")
    before = head(world.a)

    result = invoke(world, monkeypatch, argv_for(cmd))

    out = output_of(result)
    assert result.exit_code == 0, out
    assert said in out, out
    tip = head(world.a)
    assert git(world.a, "rev-list", f"{before}..{tip}").split() == [tip]
    assert commit_files(world.a, tip) == [ITEM]
    assert_subject(cmd, subject(world.a, tip))
    assert_changed(cmd, (world.a / ITEM).read_text())


def test_no_origin_no_flag_epic_add_writes_worktree_only(world, monkeypatch) -> None:
    git(world.a, "remote", "remove", "origin")
    before = head(world.a)

    result = invoke(world, monkeypatch, argv_for("epic add"))

    assert result.exit_code == 0, output_of(result)
    assert head(world.a) == before, "plain epic add commits nothing"
    assert_changed("epic add", (world.a / ITEM).read_text())


# --- e. rank --no-commit implies --no-push ------------------------------------------------


def test_rank_no_commit_no_flag_writes_uncommitted(world, monkeypatch) -> None:
    base = world.remote_sha()
    before = head(world.a)

    result = invoke(world, monkeypatch, ["rank", ITEM_ID, "2", "--no-commit"])

    assert result.exit_code == 0, output_of(result)
    assert world.remote_sha() == base
    assert head(world.a) == before
    assert_changed("rank", (world.a / ITEM).read_text())
    assert ITEM in git(world.a, "status", "--porcelain"), "the rank was not left uncommitted"


# --- f. help ---------------------------------------------------------------------------------


@pytest.mark.parametrize("cmd", COMMANDS)
def test_help_names_no_push_and_push_default(cmd) -> None:
    result = CliRunner().invoke(main, [*cmd.split(), "--help"])

    assert result.exit_code == 0, result.output
    text = " ".join(result.output.split())
    assert "--no-push" in text, text
    text = re.sub(r"default branch|default \$YURTLE_AGENT", "", text)
    assert re.search(r"push[^.]{0,80}\bdefault\b|\bdefault\b[^.]{0,80}push", text, re.I), text


# --- g. controls: create, update, epic/voyage create stay opt-in -----------------------------


@pytest.mark.parametrize("argv", [
    ["update", ITEM_ID, "--title", "Retitled"],
    ["create", "expedition", "A new one"],
    ["epic", "create", "A new epic"],
    ["voyage", "create", "A new voyage"],
], ids=["update", "create", "epic-create", "voyage-create"])
def test_control_opt_in_commands_do_not_push(world, monkeypatch, argv) -> None:
    base = world.remote_sha()

    result = invoke(world, monkeypatch, argv)

    assert result.exit_code == 0, output_of(result)
    assert world.remote_sha() == base, f"{argv[0]} pushed without --push"


def test_control_fixture_seeds_origin(world) -> None:
    assert frontmatter(world.remote_show(ITEM))["status"] == "ready"
    assert frontmatter(world.remote_show(EPIC_FILE))["type"] == "voyage"
