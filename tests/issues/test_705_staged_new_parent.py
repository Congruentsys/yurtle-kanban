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
