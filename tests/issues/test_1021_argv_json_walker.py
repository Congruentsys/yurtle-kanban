"""Issue #1021 — ``argv_requests_json`` must walk argv as click's parser does.

Two latent gaps left by #971 (PR #1016 review):

1. An optional-value option (``is_flag=False`` with a ``flag_value``) is treated
   as always taking the next argument. Click only takes it when the next
   argument does not look like an option; otherwise the option gets its
   ``flag_value``. So in ``c -o --json`` click sets ``as_json``, but the walker
   says ``--json`` is not requested.
2. An unknown letter in a short cluster is skipped, but click raises
   ``NoSuchOption`` at that letter, before any later value-taking letter can
   consume the next argument. In ``c -vxn --json`` click fails on ``-x``, and
   ``--json`` was never consumed, so it is still a request.

Decided ([steer] on #1021, bucket 1): match click's parser.

Each case runs a tiny click command with ``CliRunner`` as the oracle: the
walker's answer must equal whether click set ``as_json``, or ``True``
("requested") when click fails on an unknown option before reaching ``--json``.
"""

from __future__ import annotations

from typing import Any

import click
import pytest
from click.testing import CliRunner

from yurtle_kanban._click import argv_requests_json

_seen: dict[str, Any] = {}


@click.group()
def root() -> None:
    """A group, so the walker also steps into a subcommand."""


@root.command("c")
@click.option("-o", "--opt", is_flag=False, flag_value="x")  # optional value
@click.option("-v", "verbose", is_flag=True)
@click.option("-n", "top")  # always takes a value
@click.option("--json", "as_json", is_flag=True)
@click.argument("rest", nargs=-1)
def c(**kwargs: Any) -> None:
    _seen.clear()
    _seen.update(kwargs)


def click_requests_json(args: list[str]) -> bool:
    """What click itself does with `args`: whether it set `as_json`; when it fails
    on an unknown option (before reaching `--json`), the `--json` is a request."""
    _seen.clear()
    result = CliRunner().invoke(root, args)
    if result.exit_code == 0:
        return bool(_seen["as_json"])
    assert result.exit_code == 2 and "No such option" in result.output, result.output
    return "--json" in args


# ---------------------------------------------------------------------------
# 1. An optional-value option followed by an option-looking argument
# ---------------------------------------------------------------------------

OPTIONAL_VALUE_RED: dict[str, tuple[list[str], dict[str, Any]]] = {
    # -o gets its flag_value "x"; --json is the command's own flag
    "short-then-json": (["c", "-o", "--json"], {"opt": "x", "as_json": True}),
    "long-then-json": (["c", "--opt", "--json"], {"opt": "x", "as_json": True}),
    # a cluster ending in the optional-value letter: -v, then -o = "x"
    "cluster-ending-o": (["c", "-vo", "--json"], {"verbose": True, "opt": "x", "as_json": True}),
    # -o gets "x"; -n (always takes a value) consumes --json
    "o-then-n-eats-json": (
        ["c", "-o", "-n", "--json"],
        {"opt": "x", "top": "--json", "as_json": False},
    ),
}


@pytest.mark.parametrize("name", list(OPTIONAL_VALUE_RED))
def test_optional_value_option_matches_click(name: str) -> None:
    args, parsed = OPTIONAL_VALUE_RED[name]
    click_says = click_requests_json(args)
    for key, value in parsed.items():  # click read it as the case claims
        assert _seen[key] == value, (args, _seen)
    assert argv_requests_json(args, root) is click_says, (args, _seen)


# ---------------------------------------------------------------------------
# 2. An unknown letter before a value-taking letter in a short cluster
# ---------------------------------------------------------------------------

UNKNOWN_LETTER_RED: dict[str, list[str]] = {
    # -v flag, -x unknown: click fails at -x; -n never consumes --json
    "vxn": ["c", "-vxn", "--json"],
    # the unknown letter first
    "xn": ["c", "-xn", "--json"],
}


@pytest.mark.parametrize("name", list(UNKNOWN_LETTER_RED))
def test_unknown_cluster_letter_stops_the_walk(name: str) -> None:
    args = UNKNOWN_LETTER_RED[name]
    result = CliRunner().invoke(root, args)
    assert result.exit_code == 2, result.output
    assert "No such option '-x'" in result.output, result.output  # fails before -n
    assert click_requests_json(args) is True
    assert argv_requests_json(args, root) is True, args


# ---------------------------------------------------------------------------
# Controls: already agree with click today
# ---------------------------------------------------------------------------

CONTROLS: dict[str, tuple[list[str], bool]] = {
    # the optional-value option takes a plain value, then a real --json
    "o-plain-value": (["c", "-o", "val", "--json"], True),
    "opt-plain-value": (["c", "--opt", "val", "--json"], True),
    # -o takes the rest of the token ("v") as its value, then a real --json
    "o-attached": (["c", "-ov", "--json"], True),
    "opt-equals": (["c", "--opt=val", "--json"], True),
    # -o gets "x", -v is a flag, then a real --json
    "o-then-flag": (["c", "-o", "-v", "--json"], True),
    # #971: -n at the end of a cluster consumes --json
    "vn-eats-json": (["c", "-vn", "--json"], False),
    "n-eats-json": (["c", "-n", "--json"], False),
    # an unknown letter with no value letter after it: still a request
    "vx": (["c", "-vx", "--json"], True),
    # an unknown letter after the value letter is part of -n's value
    "nx": (["c", "-nx", "--json"], True),
    # no --json at all
    "o-alone": (["c", "-o"], False),
    "vxn-no-json": (["c", "-vxn", "val"], False),
}


@pytest.mark.parametrize("name", list(CONTROLS))
def test_control_matches_click(name: str) -> None:
    args, expected = CONTROLS[name]
    assert click_requests_json(args) is expected, (args, _seen)
    assert argv_requests_json(args, root) is expected, args
