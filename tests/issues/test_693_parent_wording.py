"""#693: an untracked parent is 'not committed' (not 'uncommitted edits'), and a
link that landed only on origin isn't reported as updated in this checkout."""

from __future__ import annotations

from tests.issues.test_585_create_push_loop import git
from tests.issues.test_603_push_failure_messages import invoke
from tests.issues.test_645_parent_in_cas import HYP, PAPER, seed_on_origin, spy
from tests.issues.test_674_parent_edges import (  # noqa: F401  (fixtures)
    _clean_theme_cache,
    dirty_parent,
    drop_remote,
    flat,
    world,
)


def test_untracked_parent_is_not_committed(world, monkeypatch) -> None:  # noqa: F811
    seed_on_origin(world, monkeypatch, HYP)
    drop_remote(world)
    git(world.a, "rm", "-q", "--cached", PAPER)
    git(world.a, "commit", "-q", "-m", "untrack the paper")
    result = invoke(world, monkeypatch, HYP.argv)
    out = flat(result)
    assert result.exit_code != 0, out
    assert "not committed" in out, out
    assert "uncommitted edits" not in out, out


def test_link_only_on_origin_is_not_reported_as_updated_here(world, monkeypatch) -> None:  # noqa: F811
    seed_on_origin(world, monkeypatch, HYP)
    dirty_parent(world)  # the fast-forward can't happen: the link stays on origin
    spy(monkeypatch)
    result = invoke(world, monkeypatch, HYP.argv)
    out = flat(result)
    assert result.exit_code == 0, out
    assert "with inverse reference" not in out, out
    assert "origin/main" in out and "PAPER-130" in out, out
