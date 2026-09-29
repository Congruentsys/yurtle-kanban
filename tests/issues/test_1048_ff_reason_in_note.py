"""Issue #1048: why the checkout wasn't fast-forwarded reaches the user.

After #995 the callers (`claim`, `update --push`, `create --push`) passed
`warn=False` and printed their own note, so git's reason (`fatal: Not possible to
fast-forward`, `Your local changes to … would be overwritten`) never reached the
user. The note now carries it: "… (fast-forward refused: <reason>)".
"""

from __future__ import annotations

import pytest

from tests.issues.test_1043_ff_warning import (  # noqa: F401  (fixtures)
    PUSHES,
    _clean_theme_cache,
    _cli,
    _diverge,
    _seed_ready,
    world,
)


@pytest.mark.parametrize("name", list(PUSHES))
def test_diverged_main_note_says_why(world, name: str) -> None:  # noqa: F811
    _seed_ready(world)
    _diverge(world)

    code, out, err = _cli(world, *PUSHES[name])
    both = " ".join((out + err).split())

    assert code == 0, both
    assert "does not show this yet" in both, both
    assert "fast-forward refused: Not possible to fast-forward" in both, both
    assert "fatal:" not in both.lower(), both  # git's own label dropped (#1057)
    assert "hint:" not in both.lower(), both  # git's advice stays out (#995)


def test_up_to_date_checkout_has_no_reason(world) -> None:  # noqa: F811
    """Control: a checkout that fast-forwards gets no note and no reason."""
    _seed_ready(world)
    code, out, err = _cli(world, *PUSHES["claim"])
    both = " ".join((out + err).split())
    assert code == 0, both
    assert "fast-forward refused" not in both, both


def test_create_push_note_says_why(world) -> None:  # noqa: F811
    """`create --push` prints its pull note from the result: the reason rides in it
    too (PR #1052 review)."""
    _seed_ready(world)
    _diverge(world)

    code, out, err = _cli(world, "create", "expedition", "Why not updated", "--push")
    both = " ".join((out + err).split())

    assert code == 0, both
    assert "not in this checkout yet" in both, both
    assert "fast-forward refused: Not possible to fast-forward" in both, both
    assert "fatal:" not in both.lower(), both
    assert "hint:" not in both.lower(), both


def test_pull_note_carries_the_reason_without_git_labels() -> None:
    """The one line `create`, `hdd … --push` and `epic create --push` all print
    (`_click.pull_note`) carries the reason, git's `error:`/`fatal:` dropped (#1057)."""
    from yurtle_kanban._click import pull_note

    line = pull_note({
        "branch": "main", "dirty_parent": None,
        "ff_why": "error: Your local changes to the following files would be "
                  "overwritten by merge: README.md",
    })
    assert "fast-forward refused: Your local changes" in line, line
    assert "error:" not in line.lower(), line
