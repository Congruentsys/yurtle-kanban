# ruff: noqa: F811  -- the `graph` fixture imported from #577's module is re-bound as an arg
"""Issue #1081: ``blocked``'s tree marks a node on a supersession cycle (#581) ``— cycle``
even when a ``depends_on`` ``↻ cycle`` line below it runs through it.

Spec: the issue body. The ``↻`` suppression (#1068, #1077) applies only to a node's
dependency cycle; a supersession cycle is marked on its own. Scenario: EXP-12⇄EXP-13
supersession, plus EXP-12 → EXP-30 → EXP-12 (``depends_on``). Controls: a pure
``depends_on`` cycle member is still not marked twice (#577's EXP-21/22, #1077's
EXP-30/31), and a pure supersession-cycle node is marked once.

Reuses #577's fixture (``graph``, ``roots``), #581's harness (``make_boards``) and
#1068's/#1077's fixtures.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.issues.test_575_pickable_next import Repo
from tests.issues.test_577_blocked import graph, roots  # noqa: F401
from tests.issues.test_581_resolution import make_boards
from tests.issues.test_1068_blocked_followups import run, superseded  # noqa: F401
from tests.issues.test_1077_blocked_edges import repro  # noqa: F401
from yurtle_kanban import cli as cli_mod

pytestmark = pytest.mark.usefixtures("claim_env")

BOTH: dict[str, dict[str, Any]] = {
    "EXP-12": {
        "status": "done", "resolution": "duplicate", "superseded_by": ("EXP-13",),
        "deps": ("EXP-30",),
    },
    "EXP-13": {"status": "done", "resolution": "superseded", "superseded_by": ("EXP-12",)},
    "EXP-30": {"status": "ready", "deps": ("EXP-12",)},
    "EXP-112": {"status": "ready", "deps": ("EXP-12",)},
}


@pytest.fixture
def both(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Repo:
    monkeypatch.setattr(cli_mod.console, "_width", 1000)
    return make_boards(tmp_path, monkeypatch, BOTH)


def _block(root: str) -> list[str]:
    result = run(["blocked"])
    assert result.exit_code == 0, result.output
    return roots(result.output)[root]


def _line(block: list[str], node: str) -> str:
    lines = [ln for ln in block[1:] if ln.strip().startswith(f"{node} ")]
    assert len(lines) == 1, "\n".join(block)
    return lines[0]


def test_supersession_cycle_marked_despite_its_own_arrow(both: Repo) -> None:
    block = _block("EXP-112")
    arrows = [ln for ln in block if "↻ cycle" in ln]
    assert len(arrows) == 1 and "EXP-12 → EXP-30 → EXP-12" in arrows[0], "\n".join(block)
    line = _line(block, "EXP-12")
    assert line.count("— cycle") == 1, "EXP-12's supersession cycle is unmarked:\n" + (
        "\n".join(block)
    )


def test_its_depends_on_partner_not_double_marked(both: Repo) -> None:
    block = _block("EXP-112")
    assert "cycle" not in _line(block, "EXP-30"), "\n".join(block)


def test_pure_supersession_node_marked_once(superseded: Repo) -> None:
    line = _line(_block("EXP-112"), "EXP-12")
    assert line.count("cycle") == 1, line


def test_1077_depends_on_cycle_not_double_marked(repro: Repo) -> None:
    block = _block("EXP-112")
    for node in ("EXP-30", "EXP-31"):
        for ln in block[1:]:
            if ln.strip().startswith(f"{node} "):
                assert "cycle" not in ln, "\n".join(block)
    assert _line(block, "EXP-12").count("— cycle") == 1, "\n".join(block)


def test_577_depends_on_cycle_not_double_marked(graph: Repo) -> None:
    block = _block("EXP-21")
    assert sum("↻ cycle" in ln for ln in block) == 1, block
    assert not [ln for ln in block if "cycle" in ln and "↻" not in ln], block
