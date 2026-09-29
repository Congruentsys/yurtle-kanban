"""Issue #1043 — the post-push fast-forward warning, follow-ups from the PR #1017 (#995)
review.

1. The ``error:``/``fatal:`` filter is pinned: ``claim --push``/``update --push`` on a
   diverged ``main`` print none of git's ``hint:`` advice.
2. ``claim`` and ``update --push`` (``_won``) say "Your checkout does not show this yet"
   ONCE: ``_fast_forward_to`` is called with ``warn=False``, as ``create`` does since
   #995, so there is no second, logged warning.
3. The filter keeps git's tab-indented continuation lines after a kept
   ``error:``/``fatal:`` line, e.g. the file names after ``error: Your local changes to
   the following files would be overwritten by merge:``.

Harnesses: the #585 ``World`` and the #995 diverged-main CLI subprocess.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

import pytest

from tests.issues.test_585_create_push_loop import EXP_DIR, World, git
from tests.issues.test_995_post_push_leftovers import (
    _assert_git_failure_only_error_fatal,
    _assert_no_hint,
    _cli,
    _diverge,
    _note_count,
)
from tests.test_634_explicit_ids_on_base import service
from yurtle_kanban import config as config_mod

ITEM_ID = "EXP-001"
ITEM = f"{EXP_DIR}/EXP-001-x.md"


@pytest.fixture
def world(tmp_path, monkeypatch: pytest.MonkeyPatch) -> World:
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    monkeypatch.delenv("YURTLE_AGENT", raising=False)
    return World(tmp_path)


@pytest.fixture(autouse=True)
def _clean_theme_cache() -> Iterator[None]:
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


def _seed_ready(world: World) -> None:
    """EXP-001, status ready and unheld, on origin and in A's checkout."""
    (world.a / ITEM).write_text(
        f"---\nid: {ITEM_ID}\ntitle: \"X\"\ntype: expedition\nstatus: ready\n---\n\n"
        "# X\n\nA description long enough.\n"
    )
    git(world.a, "add", "-A")
    git(world.a, "commit", "-m", "EXP-001 ready")
    git(world.a, "push", "origin", "HEAD:refs/heads/main")


PUSHES = {
    "claim": ("claim", ITEM_ID, "--agent", "agent-A"),
    "update --push": ("update", ITEM_ID, "--priority", "high", "--push"),
}


# --- 1 + 2. claim / update --push on a diverged main -------------------------------


@pytest.mark.parametrize("name", list(PUSHES))
def test_push_on_diverged_main_prints_no_hint(world, name: str) -> None:
    _seed_ready(world)
    _diverge(world)
    before = world.remote_sha()

    code, out, err = _cli(world, *PUSHES[name])
    both = out + err

    assert code == 0, both
    assert "Traceback" not in both, both
    assert world.remote_sha() != before, f"{name} never landed on origin: {both!r}"
    _assert_no_hint(both)
    _assert_git_failure_only_error_fatal(both)


@pytest.mark.parametrize("name", list(PUSHES))
def test_push_on_diverged_main_notes_once(world, name: str) -> None:
    _seed_ready(world)
    _diverge(world)

    code, out, err = _cli(world, *PUSHES[name])
    both = out + err

    assert code == 0, both
    assert "does not show this yet" in " ".join(both.split()), both
    assert _note_count(both) == 1, (
        f"{name}: the checkout-not-updated note should appear once, its own "
        f"(_won passes warn=False); got {_note_count(both)}:\n"
        f"stdout={out!r}\nstderr={err!r}"
    )
    assert "local checkout was not updated" not in " ".join(err.split()), (
        f"{name}: a second, logged warning from _fast_forward_to: {err!r}"
    )


def test_filter_drops_hint_on_diverged_main(world, caplog) -> None:
    """Pins the error:/fatal: filter itself: with claim/update passing warn=False, a
    warn=True call is the only path that shows git's words."""
    _seed_ready(world)
    _diverge(world)
    git(world.b, "pull", "--quiet", "origin", "main")
    (world.b / "from-b.txt").write_text("b\n")
    git(world.b, "add", "from-b.txt")
    git(world.b, "commit", "-m", "from B")
    git(world.b, "push", "origin", "HEAD:refs/heads/main")
    git(world.a, "fetch", "origin")
    sha = world.remote_sha()

    with caplog.at_level(logging.WARNING):
        assert service(world)._fast_forward_to("main", sha, warn=True) is False

    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "fatal:" in logged.lower(), f"git's fatal: line is missing: {logged!r}"
    _assert_no_hint(logged)
    _assert_git_failure_only_error_fatal(logged)


# --- 3. continuation lines after a kept error: line --------------------------------


def test_warning_keeps_indented_continuation_lines(world, caplog) -> None:
    # origin changes README.md; A has an uncommitted edit to it: the ff-only merge
    # fails with "error: Your local changes ... would be overwritten by merge:" and
    # the file name on a tab-indented line after it
    git(world.b, "pull", "--quiet", "origin", "main")
    (world.b / "README.md").write_text("theirs\n")
    git(world.b, "commit", "-am", "readme from B")
    git(world.b, "push", "origin", "HEAD:refs/heads/main")
    git(world.a, "fetch", "origin")
    sha = world.remote_sha()
    (world.a / "README.md").write_text("mine, uncommitted\n")

    with caplog.at_level(logging.WARNING):
        assert service(world)._fast_forward_to("main", sha, warn=True) is False

    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "would be overwritten" in logged, (
        f"git's error: line is missing from the warning: {logged!r}"
    )
    assert "README.md" in logged, (
        "the tab-indented file name after git's kept error: line was dropped "
        f"(#1043 item 3): {logged!r}"
    )
    _assert_no_hint(logged)
    assert "Aborting" not in logged, f"a non-error, non-continuation line kept: {logged!r}"
