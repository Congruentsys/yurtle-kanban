# ruff: noqa: F811  -- the `graph` fixture imported from #577's module is re-bound as an arg
"""Issue #1077: #1068 (PR #1076) follow-ups.

Spec: the issue body.

1. ``blocked``'s text tree marks a supersession-cycle node (#581) ``— cycle`` unless a
   ``↻ cycle`` line below it closes a cycle through that node; an unrelated
   ``depends_on`` cycle's ``↻`` line further down doesn't suppress the mark. A node on
   a ``depends_on`` cycle is still not marked twice.
2. ``blocked``, ``list`` and ``states`` refuse an unknown ``--board`` with one wording.
   The wording is the codebase's refusal convention, the shared ``refuse``'s
   ``Error: <message>`` line (#580, #962) that most refusals print (and ``blocked``
   already does): ``Error: Unknown board: nosuch``.
3. ``export --board nosuch`` refuses the same way, exit 1, and writes no export.

Reuses #577's fixture (``graph``, ``roots``) and #581's harness (``make_boards``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from click.testing import Result

from tests.issues.test_575_pickable_next import Repo
from tests.issues.test_577_blocked import graph, roots  # noqa: F401
from tests.issues.test_581_resolution import make_boards
from tests.issues.test_1068_blocked_followups import run
from yurtle_kanban import cli as cli_mod

pytestmark = pytest.mark.usefixtures("claim_env")

WORDING = "Error: Unknown board: nosuch"


# --- 1. an unrelated ↻ line doesn't hide a supersession-cycle mark --------------------

REPRO: dict[str, dict[str, Any]] = {
    "EXP-12": {
        "status": "done", "resolution": "duplicate", "superseded_by": ("EXP-13",),
        "deps": ("EXP-30",),
    },
    "EXP-13": {"status": "done", "resolution": "superseded", "superseded_by": ("EXP-12",)},
    "EXP-30": {"status": "ready", "deps": ("EXP-31",)},
    "EXP-31": {"status": "ready", "deps": ("EXP-30",)},
    "EXP-112": {"status": "ready", "deps": ("EXP-12",)},
}


@pytest.fixture
def repro(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Repo:
    monkeypatch.setattr(cli_mod.console, "_width", 1000)
    return make_boards(tmp_path, monkeypatch, REPRO)


def _block(root: str) -> list[str]:
    result = run(["blocked"])
    assert result.exit_code == 0, result.output
    return roots(result.output)[root]


def test_supersession_cycle_marked_despite_unrelated_arrow(repro: Repo) -> None:
    block = _block("EXP-112")
    assert any("↻ cycle" in ln for ln in block), f"the fixture needs a ↻ line:\n{block}"
    lines = [ln for ln in block[1:] if ln.strip().startswith("EXP-12 ")]
    assert len(lines) == 1, block
    assert "— cycle" in lines[0], "EXP-12 is not marked as a cycle:\n" + "\n".join(block)


def test_depends_on_cycle_nodes_not_double_marked(repro: Repo) -> None:
    block = _block("EXP-112")
    marked = [
        ln for ln in block[1:]
        if "cycle" in ln and "↻" not in ln and not ln.strip().startswith("EXP-12 ")
    ]
    assert not marked, "a depends_on cycle is marked twice:\n" + "\n".join(block)


def test_depends_on_cycle_control_in_577_graph(graph: Repo) -> None:
    block = _block("EXP-21")
    assert sum("↻ cycle" in ln for ln in block) == 1, block
    assert not [ln for ln in block if "cycle" in ln and "↻" not in ln], block


# --- 2./3. one wording for an unknown --board; export refuses it -----------------------


def _refusal(result: Result) -> str:
    assert result.exit_code == 1, f"exit {result.exit_code}:\n{result.output}"
    return " ".join(result.output.split())


@pytest.mark.parametrize("command", ["blocked", "list", "states"])
def test_unknown_board_one_wording(graph: Repo, command: str) -> None:
    assert _refusal(run([command, "--board", "nosuch"])) == WORDING


@pytest.mark.parametrize("fmt", ["json", "markdown"])
def test_export_refuses_unknown_board(graph: Repo, fmt: str) -> None:
    assert _refusal(run(["export", "--format", fmt, "--board", "nosuch"])) == WORDING


def test_export_refuses_before_writing(graph: Repo, tmp_path: Path) -> None:
    target = tmp_path / "out.json"
    result = run(["export", "-f", "json", "-o", str(target), "--board", "nosuch"])
    assert _refusal(result) == WORDING
    assert not target.exists()


def test_export_known_board_still_works(graph: Repo) -> None:
    result = run(["export", "--format", "json", "--board", "research"])
    assert result.exit_code == 0, result.output
    assert "H1.2" in result.output, result.output
