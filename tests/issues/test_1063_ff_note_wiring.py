"""Issue #1063: `idea create --push` and `epic create --push` print the label-free
fast-forward-refused note.

PR #1060 (#1057) pinned the wording on the shared `_click.pull_note` with a unit
test; the command -> note wiring for the HDD and epic creates was only verified by
hand. Here each command runs as a real CLI process in a checkout whose `main` has
diverged from origin, so the push lands but the post-push fast-forward is refused:
the pull note must appear, say "fast-forward refused" with git's reason, and carry
none of git's `error:`/`fatal:` labels.

Harness: #585's bare remote + clone (`World`), #995's diverged main and CLI
subprocess, #603's board reconfiguration.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from tests.issues.test_585_create_push_loop import World
from tests.issues.test_603_push_failure_messages import (
    HDD_DIRS,
    nautical_with_voyages,
    reconfigure,
)
from tests.issues.test_995_post_push_leftovers import _cli, _diverge
from yurtle_kanban import config as config_mod

TITLE = "Wiring check"
REASON = "fast-forward refused: Not possible to fast-forward"


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


def _assert_label_free_note(world: World, before: str, code: int, out: str, err: str) -> None:
    both = " ".join((out + err).split())
    assert code == 0, both
    assert "Traceback" not in both, both
    assert world.remote_sha() != before, f"the create never landed on origin: {both!r}"
    assert "not in this checkout yet" in both, f"no pull note: {both!r}"
    low = both.lower()
    assert "error:" not in low, f"git's error: label reached the user: {both!r}"
    assert "fatal:" not in low, f"git's fatal: label reached the user: {both!r}"
    assert REASON in both, f"the note does not say why the fast-forward was refused: {both!r}"


def test_hdd_idea_create_push_on_diverged_main_prints_label_free_note(world) -> None:
    reconfigure(world, "hdd", "research/", [f"research/{d}/" for d in HDD_DIRS])
    _diverge(world)
    before = world.remote_sha()

    code, out, err = _cli(world, "idea", "create", TITLE, "--push")

    _assert_label_free_note(world, before, code, out, err)


def test_epic_create_push_on_diverged_main_prints_label_free_note(world) -> None:
    nautical_with_voyages(world)
    _diverge(world)
    before = world.remote_sha()

    code, out, err = _cli(world, "epic", "create", TITLE, "--push")

    _assert_label_free_note(world, before, code, out, err)
