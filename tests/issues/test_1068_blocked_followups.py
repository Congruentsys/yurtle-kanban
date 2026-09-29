# ruff: noqa: F811  -- the `graph` fixture imported from #577's module is re-bound as an arg
"""Issue #1068: #577 ``blocked`` follow-ups.

Spec: the issue body, with its [steer] as the spec for item 1.

1. ``blocked --board X`` and ``list --board X`` refuse a board name no config knows
   with ``boards``/``states``' own ``Unknown board:`` message: exit 1, and under
   ``--json`` one JSON refusal object on stdout. A single-board repo refuses
   ``--board`` with any name other than its own (``default``, as ``boards`` shows it).
   A configured board name still works.
2. A dependency whose state is ``cycle`` because of a **supersession** cycle (#581:
   finished items ``superseded_by`` each other) is marked as a cycle in the text tree,
   although the tree (which follows ``depends_on``) prints no ``↻ cycle`` line for it.
   JSON already says ``state: cycle``. A ``depends_on`` cycle keeps its ``↻`` line.
3. One ``blocked`` run builds the dependency index (``_dep_index``) and graph
   (``_dep_graph``) once, not once per candidate item.

Reuses #577's fixture (``graph``) and #581's harness (``make_boards``).
Left open: the wording of the cycle mark beyond containing ``cycle``.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner, Result

from tests.issues.test_575_pickable_next import Repo
from tests.issues.test_577_blocked import graph, human, roots  # noqa: F401
from tests.issues.test_581_resolution import make_boards, no_hang
from yurtle_kanban import cli as cli_mod
from yurtle_kanban.cli import main
from yurtle_kanban.service import KanbanService

pytestmark = pytest.mark.usefixtures("claim_env")


def run(args: list[str]) -> Result:
    with no_hang(20):
        return CliRunner().invoke(main, args)


def refused(result: Result, name: str, *, as_json: bool) -> None:
    assert result.exit_code == 1, f"exit {result.exit_code}:\n{result.output}"
    if as_json:
        data = json.loads(result.stdout)
        assert data == {"success": False, "error": f"Unknown board: {name}"}, data
    else:
        assert f"Unknown board: {name}" in " ".join(result.output.split()), result.output


# --- 1. an unknown board is refused --------------------------------------------------


@pytest.mark.parametrize("command", ["blocked", "list"])
@pytest.mark.parametrize("as_json", [False, True], ids=["text", "json"])
def test_unknown_board_is_refused(graph: Repo, command: str, as_json: bool) -> None:
    args = [command, "--board", "nosuch", *(["--json"] if as_json else [])]
    refused(run(args), "nosuch", as_json=as_json)


@pytest.mark.parametrize("command", ["blocked", "list"])
def test_known_board_still_works(graph: Repo, command: str) -> None:
    result = run([command, "--board", "research", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    items = data["items"] if isinstance(data, dict) else data
    ids = {e["id"] for e in items}
    assert ids and all(i.startswith("H") for i in ids), ids


SINGLE = """\
kanban:
  theme: software
  paths:
    root: "work/"
    scan_paths:
      - "work/"
"""


@pytest.fixture
def single(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "single"
    (root / ".kanban").mkdir(parents=True)
    (root / ".kanban" / "config.yaml").write_text(SINGLE)
    item = root / "work" / "features" / "FEAT-1-item.md"
    item.parent.mkdir(parents=True)
    item.write_text(
        "---\nid: FEAT-1\ntitle: \"Item FEAT-1\"\ntype: feature\nstatus: blocked\n"
        "priority: medium\n---\n\n# Item FEAT-1\n\nA description long enough.\n"
    )
    for cmd in (["init", "-b", "main"], ["config", "user.email", "t@t.com"],
                ["config", "user.name", "tester"], ["config", "commit.gpgsign", "false"],
                ["add", "-A"], ["commit", "-m", "fixture"]):
        subprocess.run(["git", *cmd], cwd=root, check=True, capture_output=True)
    monkeypatch.chdir(root)
    return root


@pytest.mark.parametrize("command", ["blocked", "list"])
@pytest.mark.parametrize("as_json", [False, True], ids=["text", "json"])
def test_single_board_refuses_another_name(single: Path, command: str, as_json: bool) -> None:
    args = [command, "--board", "other", *(["--json"] if as_json else [])]
    refused(run(args), "other", as_json=as_json)


@pytest.mark.parametrize("command", ["blocked", "list"])
def test_single_board_accepts_its_own_name(single: Path, command: str) -> None:
    result = run([command, "--board", "default"])
    assert result.exit_code == 0, result.output
    assert "FEAT-1" in result.output, result.output


# --- 2. a supersession cycle is marked in the tree -----------------------------------

SUPERSESSION: dict[str, dict[str, Any]] = {
    "EXP-12": {"status": "done", "resolution": "duplicate", "superseded_by": ("EXP-13",)},
    "EXP-13": {"status": "done", "resolution": "superseded", "superseded_by": ("EXP-12",)},
    "EXP-112": {"status": "ready", "deps": ("EXP-12",)},
}


@pytest.fixture
def superseded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Repo:
    monkeypatch.setattr(cli_mod.console, "_width", 1000)
    return make_boards(tmp_path, monkeypatch, SUPERSESSION)


def test_supersession_cycle_json_says_cycle(superseded: Repo) -> None:
    result = run(["blocked", "--json"])
    assert result.exit_code == 0, result.output
    items = {e["id"]: e for e in json.loads(result.stdout)["items"]}
    assert [(n["id"], n["state"]) for n in items["EXP-112"]["unmet"]] == [("EXP-12", "cycle")]


def test_supersession_cycle_is_marked_in_the_tree(superseded: Repo) -> None:
    result = run(["blocked"])
    assert result.exit_code == 0, result.output
    block = roots(result.output)["EXP-112"]
    lines = [ln for ln in block[1:] if "EXP-12" in ln]
    assert len(lines) == 1, block
    assert "cycle" in lines[0], f"EXP-12 is not marked as a cycle:\n{result.output}"


def test_depends_on_cycle_keeps_its_arrow_line(graph: Repo) -> None:
    block = human()["EXP-21"]
    arrows = [ln for ln in block if "↻ cycle" in ln]
    assert len(arrows) == 1, block
    marked = [ln for ln in block if "cycle" in ln and "↻" not in ln]
    assert not marked, "a depends_on cycle is marked twice:\n" + "\n".join(block)


# --- 3. one index per run -------------------------------------------------------------


@pytest.mark.parametrize("as_json", [False, True], ids=["text", "json"])
def test_blocked_builds_the_index_once(
    graph: Repo, monkeypatch: pytest.MonkeyPatch, as_json: bool
) -> None:
    calls = {"_dep_index": 0, "_dep_graph": 0}
    for name in calls:
        real = getattr(KanbanService, name)

        def spy(self: Any, *a: Any, _real: Any = real, _name: str = name, **kw: Any) -> Any:
            calls[_name] += 1
            return _real(self, *a, **kw)

        monkeypatch.setattr(KanbanService, name, spy)
    result = run(["blocked", *(["--json"] if as_json else [])])
    assert result.exit_code == 0, result.output
    assert calls == {"_dep_index": 1, "_dep_graph": 1}, calls
