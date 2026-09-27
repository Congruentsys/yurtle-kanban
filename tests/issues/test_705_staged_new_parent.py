"""#705: a parent added to the index but never committed ('A ' in porcelain) is
'not committed', like an untracked one, not 'has uncommitted edits'."""

from __future__ import annotations

from tests.issues.test_585_create_push_loop import git
from tests.issues.test_603_push_failure_messages import invoke
from tests.issues.test_645_parent_in_cas import HYP, PAPER, seed_on_origin
from tests.issues.test_674_parent_edges import (  # noqa: F401  (fixtures)
    _clean_theme_cache,
    drop_remote,
    flat,
    world,
)


def test_staged_new_parent_is_not_committed(world, monkeypatch) -> None:  # noqa: F811
    seed_on_origin(world, monkeypatch, HYP)
    drop_remote(world)
    git(world.a, "rm", "-q", "--cached", PAPER)
    git(world.a, "commit", "-q", "-m", "untrack the paper")
    git(world.a, "add", PAPER)  # staged as new: 'A '
    result = invoke(world, monkeypatch, HYP.argv)
    out = flat(result)
    assert result.exit_code != 0, out
    assert "not committed" in out, out
    assert "uncommitted edits" not in out, out


def test_staged_rename_of_a_committed_parent_is_uncommitted_edits(world, monkeypatch) -> None:  # noqa: F811
    """#705 review: `git mv` shows as `A  new` under a pathspec; the parent is
    committed (under its old path), so it's an uncommitted edit, not 'not committed'."""
    seed_on_origin(world, monkeypatch, HYP)
    drop_remote(world)
    moved = PAPER.replace(".md", "-moved.md")
    git(world.a, "mv", PAPER, moved)
    result = invoke(world, monkeypatch, HYP.argv)
    out = flat(result)
    assert result.exit_code != 0, out
    assert "uncommitted edits" in out, out
    assert "not committed" not in out, out


def test_intent_to_add_parent_is_not_committed(world, monkeypatch) -> None:  # noqa: F811
    seed_on_origin(world, monkeypatch, HYP)
    drop_remote(world)
    git(world.a, "rm", "-q", "--cached", PAPER)
    git(world.a, "commit", "-q", "-m", "untrack the paper")
    git(world.a, "add", "-N", PAPER)  # intent to add: ' A'
    result = invoke(world, monkeypatch, HYP.argv)
    out = flat(result)
    assert result.exit_code != 0, out
    assert "not committed" in out, out


def test_staged_rename_to_a_quoted_path_is_uncommitted_edits(world, monkeypatch) -> None:  # noqa: F811
    """#718: git C-quotes non-ASCII paths in --name-status; -z keeps them raw."""
    seed_on_origin(world, monkeypatch, HYP)
    drop_remote(world)
    moved = PAPER.replace(".md", "-café.md")
    git(world.a, "mv", PAPER, moved)
    result = invoke(world, monkeypatch, HYP.argv)
    out = flat(result)
    assert result.exit_code != 0, out
    assert "uncommitted edits" in out, out


def test_parent_moved_off_the_board_says_it_was_skipped(world, monkeypatch) -> None:  # noqa: F811
    """#718: a parent moved out of every board path is not found; the create says
    so instead of silently leaving it unlinked."""
    seed_on_origin(world, monkeypatch, HYP)
    drop_remote(world)
    elsewhere = "notes/PAPER-130-A-paper.md"
    (world.a / "notes").mkdir(exist_ok=True)
    git(world.a, "mv", PAPER, elsewhere)
    git(world.a, "commit", "-q", "-m", "move the paper off the board")
    result = invoke(world, monkeypatch, HYP.argv)
    out = flat(result)
    assert "PAPER-130" in out and "not found" in out.lower(), out


def test_parent_without_turtle_block_says_so(world, monkeypatch) -> None:  # noqa: F811
    """#724 review: no turtle block is not 'already linked'."""
    import re

    seed_on_origin(world, monkeypatch, HYP)
    drop_remote(world)
    path = world.a / PAPER
    path.write_text(re.sub(r"```turtle\n.*?```\n", "", path.read_text(), flags=re.S))
    git(world.a, "commit", "-qam", "drop the paper's turtle block")
    result = invoke(world, monkeypatch, HYP.argv)
    out = flat(result)
    assert result.exit_code == 0, out
    assert "PAPER-130" in out and "no turtle block" in out, out
    assert "already" not in out, out


def test_already_linked_parent_names_the_child(world, monkeypatch) -> None:  # noqa: F811
    """#724: a parent whose turtle block already links the child says so, by name."""
    seed_on_origin(world, monkeypatch, HYP)
    drop_remote(world)
    first = invoke(world, monkeypatch, HYP.argv)  # links H130.1 into the paper, committed
    assert first.exit_code == 0, flat(first)
    child = next((world.a / "research" / "hypotheses").glob("H130.1-*.md"))
    git(world.a, "rm", "-q", str(child.relative_to(world.a)))
    git(world.a, "commit", "-qm", "drop the child, keep the paper's link")
    again = invoke(world, monkeypatch, [*HYP.argv, "--id", "H130.1"])
    out = flat(again)
    assert again.exit_code == 0, out
    assert "PAPER-130 already links to H130.1" in out, out


def test_missing_parent_is_said_once(world, monkeypatch, caplog) -> None:  # noqa: F811
    """#724: the CLI line says it; the service no longer warns as well."""
    import logging

    logging.getLogger("yurtle-kanban").addHandler(caplog.handler)
    caplog.set_level(logging.WARNING)
    try:
        seed_on_origin(world, monkeypatch, HYP)
        drop_remote(world)
        elsewhere = "notes/PAPER-130-A-paper.md"
        (world.a / "notes").mkdir(exist_ok=True)
        git(world.a, "mv", PAPER, elsewhere)
        git(world.a, "commit", "-q", "-m", "move the paper off the board")
        result = invoke(world, monkeypatch, HYP.argv)
        out = flat(result)
        assert out.lower().count("not found") == 1, out
    finally:
        logging.getLogger("yurtle-kanban").removeHandler(caplog.handler)
    warned = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert not [w for w in warned if "not found" in w], warned


def test_push_off_checkout_reports_origins_parent_state(world, monkeypatch) -> None:  # noqa: F811
    """#724 round 2: with HEAD off main, `--push` builds the link against
    origin's copy of the parent; the line must describe THAT copy, not the
    local one (origin's paper has no turtle block; the local one does)."""
    import re

    seed_on_origin(world, monkeypatch, HYP)
    git(world.b, "fetch", "origin")
    git(world.b, "reset", "--hard", "origin/main")
    rpath = world.b / PAPER
    rpath.write_text(re.sub(r"```turtle\n.*?```\n", "", rpath.read_text(), flags=re.S))
    git(world.b, "commit", "-qam", "origin: drop the paper's turtle block")
    git(world.b, "push", "-q", "origin", "HEAD:refs/heads/main")
    git(world.a, "checkout", "-q", "-b", "feat")
    result = invoke(world, monkeypatch, HYP.argv)
    out = flat(result)
    assert result.exit_code == 0, out
    assert "PAPER-130" in out and "no turtle block" in out, out
    assert "already" not in out and "Updated PAPER-130" not in out, out
