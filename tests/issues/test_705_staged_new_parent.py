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
