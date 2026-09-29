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
    assert "fast-forward refused:" in both, both
    assert "fatal:" in both.lower(), both
    assert "hint:" not in both.lower(), both  # git's advice stays out (#995)


def test_up_to_date_checkout_has_no_reason(world) -> None:  # noqa: F811
    """Control: a checkout that fast-forwards gets no note and no reason."""
    _seed_ready(world)
    code, out, err = _cli(world, *PUSHES["claim"])
    both = " ".join((out + err).split())
    assert code == 0, both
    assert "fast-forward refused" not in both, both
