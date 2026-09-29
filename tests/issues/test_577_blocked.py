"""Issue #577: a dependency-aware ``blocked``.

The spec is the revised issue body (Expected 1-5, Acceptance 1-10), written after
the adversarial review. #575 owns the predicate and the walk; this issue renders
them and adds no readiness logic of its own.

Public surface pinned here:

- ``yurtle-kanban blocked [--board B] [--all] [--json]`` lists each item **once**
  when it is either of these:
  * **status-blocked:** canonical ``blocked`` and not finished. hdd ``abandoned`` is
    finished, so it is excluded.
  * **dependency-blocked:** canonical ready, in_progress or review, and
    ``unmet_dependencies(item) != []``. With ``--all``, backlog items count too.
- Human output is a tree per item. The root line starts ``ID (status``. The unmet
  dependencies are printed below it, each nested deeper than its parent. A node
  already printed under the same root is printed again as ``ID (see above)``, with
  no children. A cycle prints ``↻ cycle: A → B → A`` and stops. A missing ID prints
  ``unknown ID`` and a dead one prints ``dead``.
- ``--json`` is ``{"items": [{id, board, status, canonical_status, assignee,
  status_blocked, unmet: [{id, status, assignee, state, children}]}]}``, where
  ``state`` is one of ``unfinished | unknown | dead | cycle``.
- ``--board B`` limits which items are listed. Dependencies are still resolved
  across every board.
- ``blocked --help`` names ``hdd critical-path --dev-blockers`` (Expected 5).
- The software and nautical ``blocked`` skills print ``update X --add-dep Y`` and
  ``comment X --body-file -``, both accepted by the CLI. They also carry a warning
  that on hdd ``blocked`` means abandoned (Expected 4, Acceptance 10).

Readings the test partner chose (the driver may challenge them):

a. JSON ``board`` is the board's configured name (``development``/``research``
   here; the spec's example says ``dev``). ``status``, in JSON and in the tree's
   ``ID (status`` root line, is the native name, i.e. ``status_label``, which
   #575's DepNode ``status`` already uses: nautical ``provisioning``/``underway``/
   ``harbor``/``stranded``, hdd ``active``/``abandoned``. ``canonical_status`` is
   the canonical value. The spec's example (``"status": "ready"``) is
   software-themed, where the two names coincide.
b. The JSON key sets are pinned exactly, as are the full entries for the chain
   items. The JSON gives #575's DepNodes as they are: whether the ``(see above)``
   dedup also applies in JSON is left open, so diamond/cycle JSON pins only
   ``state``.
c. The tree is read by column: a root is an unindented line that starts with an
   item ID, and a root's block runs until the next unindented line. Depth is the
   column at which a node's ID starts, so either plain indentation or tree guides
   (``├──``) pass. The rich console is widened so lines don't wrap.
d. A cycle line's path may start at either member. The test pins only that it
   appears in the root's block and that the command returns.
e. Acceptance 10 names tests/test_skill_commands_execute.py. That module already
   checks every skill line against the CLI; this file doesn't append to it. It
   imports its extractor and ``_rejection`` to pin that the two blocked skills
   print the new lines and that the CLI accepts them.

f. Expected 1 defines dependency-blocked for ready/in_progress/review only, yet
   Acceptance 6 wants a canonical ``blocked`` item that has unmet dependencies to
   show a non-empty ``unmet``. Read together: ``unmet`` is
   ``unmet_dependencies(item)`` for every LISTED item, including a status-blocked
   one. The ready/in_progress/review(/backlog with ``--all``) filter only decides
   whether an item that is NOT status-blocked gets listed.

Left open: the order of items, the header/empty-board wording, the root line's
text after ``ID (status``, whether ``blocked`` prints dependency-blocked items
differently from status-blocked ones, the exit code when nothing is blocked, and
the wording of the skill prose apart from the hdd warning's keywords.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner, Result

from tests.issues.test_575_pickable_next import Repo, make_repo
from tests.issues.test_581_resolution import no_hang
from tests.test_skill_commands_execute import _commands_in, _rejection
from yurtle_kanban import cli as cli_mod
from yurtle_kanban.cli import main

pytestmark = pytest.mark.usefixtures("claim_env")

SKILLS = Path(__file__).resolve().parents[2] / "skills"

# development (nautical, EXP-*) and research (hdd, H*), as #575's harness builds them
GRAPH: dict[str, dict[str, Any]] = {
    # A1 chain: EXP-1 <- EXP-2 (in_progress, Mini) <- EXP-3 (backlog)
    "EXP-1": {"status": "ready", "deps": ("EXP-2",)},
    "EXP-2": {"status": "in_progress", "deps": ("EXP-3",), "assignee": "Mini"},
    "EXP-3": {"status": "backlog"},
    # A2 diamond: EXP-10 -> {EXP-11, EXP-12} -> EXP-13 -> EXP-14
    "EXP-10": {"status": "ready", "deps": ("EXP-11", "EXP-12")},
    "EXP-11": {"status": "ready", "deps": ("EXP-13",)},
    "EXP-12": {"status": "ready", "deps": ("EXP-13",)},
    "EXP-13": {"status": "ready", "deps": ("EXP-14",)},
    "EXP-14": {"status": "ready"},
    # A3 missing ID; hand-made cycle
    "EXP-20": {"status": "ready", "deps": ("EXP-99",)},
    "EXP-21": {"status": "ready", "deps": ("EXP-22",)},
    "EXP-22": {"status": "ready", "deps": ("EXP-21",)},
    # A4 cross-board: dev EXP on an hdd abandoned item
    "EXP-30": {"status": "ready", "deps": ("H1.1",)},
    # status-blocked only; both (A6); review with unmet deps (Expected 1)
    "EXP-40": {"status": "blocked"},
    "EXP-41": {"status": "blocked", "deps": ("EXP-14",)},
    "EXP-42": {"status": "review", "deps": ("EXP-14",)},
    # A7 backlog with unmet deps
    "EXP-50": {"status": "backlog", "deps": ("EXP-14",)},
    # finished items are never listed, whatever their deps
    "EXP-60": {"status": "done", "deps": ("EXP-14",)},
    # A5 research board
    "H1.1": {"status": "abandoned"},
    "H1.2": {"status": "active"},
    "H1.3": {"status": "active", "deps": ("EXP-3",)},
    "H1.4": {"status": "abandoned", "deps": ("EXP-3",)},
}

DEFAULT_LISTED = {
    "EXP-1", "EXP-2", "EXP-10", "EXP-11", "EXP-12", "EXP-13", "EXP-20", "EXP-21",
    "EXP-22", "EXP-30", "EXP-40", "EXP-41", "EXP-42", "H1.3",
}
NEVER_LISTED = {"EXP-3", "EXP-14", "EXP-60", "H1.1", "H1.2", "H1.4"}

ITEM_KEYS = {
    "id", "board", "status", "canonical_status", "assignee", "status_blocked", "unmet",
}
NODE_KEYS = {"id", "status", "assignee", "state", "children"}
STATES = {"unfinished", "unknown", "dead", "cycle"}

ID_AT_START = re.compile(r"^(?:EXP-\d+|H\d+(?:\.\d+)*)\b")
ID_ANYWHERE = re.compile(r"(?<![\w-])(EXP-\d+|H\d+(?:\.\d+)*)(?![\w.-])")


@pytest.fixture
def graph(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Repo:
    # reading c: never let rich wrap a tree line
    monkeypatch.setattr(cli_mod.console, "_width", 1000)
    return make_repo(tmp_path, monkeypatch, GRAPH)


def invoke(args: list[str]) -> Result:
    with no_hang(20):
        return CliRunner().invoke(main, ["blocked", *args])


def ok(result: Result) -> str:
    assert result.exit_code == 0, (
        f"exit {result.exit_code}: {result.exception!r}\n{result.output}"
    )
    return result.output


def payload(args: list[str] = ()) -> dict[str, Any]:  # type: ignore[assignment]
    result = invoke([*args, "--json"])
    ok(result)
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise AssertionError(f"stdout is not JSON ({e}):\n{result.stdout}") from e
    assert isinstance(data, dict) and isinstance(data.get("items"), list), data
    return data


def entries(args: list[str] = ()) -> dict[str, dict[str, Any]]:  # type: ignore[assignment]
    items = payload(args)["items"]
    ids = [e["id"] for e in items]
    assert len(ids) == len(set(ids)), f"an item is listed twice: {ids}"
    return {e["id"]: e for e in items}


def roots(output: str) -> dict[str, list[str]]:
    """Each root's block (reading c): its root line and the lines under it."""
    blocks: dict[str, list[str]] = {}
    current: list[str] | None = None
    seen: list[str] = []
    for line in output.splitlines():
        if line and not line[0].isspace():
            m = ID_AT_START.match(line)
            if m:
                seen.append(m.group(0))
                current = blocks.setdefault(m.group(0), [])
                current.append(line)
            else:
                current = None
            continue
        if current is not None and line.strip():
            current.append(line)
    assert len(seen) == len(set(seen)), f"a root is printed twice: {seen}"
    return blocks


def node_lines(block: list[str]) -> list[tuple[int, str, str]]:
    """(column of the node's ID, the ID, the line) for each node line below the root."""
    out = []
    for line in block[1:]:
        m = ID_ANYWHERE.search(line)
        if m:
            out.append((m.start(), m.group(1), line))
    return out


class Blocks(dict):
    """`roots`, where a missing root is an assertion, not a KeyError."""

    def __init__(self, blocks: dict[str, list[str]], output: str) -> None:
        super().__init__(blocks)
        self.output = output

    def __missing__(self, key: str) -> list[str]:
        raise AssertionError(f"{key} is not a root of `blocked`'s tree:\n{self.output}")


def human(args: list[str] = ()) -> Blocks:  # type: ignore[assignment]
    output = ok(invoke(list(args)))
    return Blocks(roots(output), output)


# --- Expected 1 / surface ----------------------------------------------------------


@pytest.mark.parametrize("option", ["--board", "--all", "--json"])
def test_blocked_declares_option(option: str) -> None:
    params = {o for p in main.commands["blocked"].params for o in p.opts}
    assert option in params, f"`blocked` has no {option} option"


def test_default_listing(graph: Repo) -> None:
    listed = set(entries())
    assert listed == DEFAULT_LISTED, (
        f"missing {sorted(DEFAULT_LISTED - listed)}, extra {sorted(listed - DEFAULT_LISTED)}"
    )


def test_default_listing_human(graph: Repo) -> None:
    assert set(human()) == DEFAULT_LISTED


# --- Acceptance 1: chain -----------------------------------------------------------


def test_a1_chain_tree(graph: Repo) -> None:
    block = human()["EXP-1"]
    assert block[0].startswith("EXP-1 (provisioning"), block[0]
    nodes = node_lines(block)
    assert [n[1] for n in nodes] == ["EXP-2", "EXP-3"], block
    (col_y, _, line_y), (col_z, _, _) = nodes
    assert col_z > col_y, "EXP-3 is not nested under EXP-2:\n" + "\n".join(block)
    assert "underway" in line_y and "Mini" in line_y, line_y


def test_a1_chain_json(graph: Repo) -> None:
    exp3 = {"id": "EXP-3", "status": "harbor", "assignee": None,
            "state": "unfinished", "children": []}
    got = entries()
    assert got["EXP-1"]["unmet"] == [
        {"id": "EXP-2", "status": "underway", "assignee": "Mini",
         "state": "unfinished", "children": [exp3]},
    ]
    assert got["EXP-2"] == {
        "id": "EXP-2", "board": "development", "status": "underway",
        "canonical_status": "in_progress", "assignee": "Mini",
        "status_blocked": False, "unmet": [exp3],
    }


# --- Acceptance 2: diamond ---------------------------------------------------------


def test_a2_diamond_see_above(graph: Repo) -> None:
    block = human()["EXP-10"]
    nodes = node_lines(block)
    w = [i for i, n in enumerate(nodes) if n[1] == "EXP-13"]
    assert len(w) == 2, "EXP-13 should be printed twice under EXP-10:\n" + "\n".join(block)
    first, second = w
    assert "(see above)" not in nodes[first][2], nodes[first][2]
    assert nodes[first + 1][1] == "EXP-14" and nodes[first + 1][0] > nodes[first][0], (
        "W's subtree is not printed in full the first time:\n" + "\n".join(block)
    )
    assert "EXP-13 (see above)" in nodes[second][2], nodes[second][2]
    after = nodes[second + 1:]
    assert not after or after[0][0] <= nodes[second][0], (
        "a (see above) node has children:\n" + "\n".join(block)
    )
    assert [n[1] for n in nodes].count("EXP-14") == 1, "\n".join(block)


def test_a2_see_above_is_per_root(graph: Repo) -> None:
    """EXP-12 is its own root: EXP-13 was printed under EXP-10, not under it."""
    block = human()["EXP-12"]
    assert not any("(see above)" in line for line in block), "\n".join(block)
    assert [n[1] for n in node_lines(block)] == ["EXP-13", "EXP-14"], block


# --- Acceptance 3: missing ID, cycle ---------------------------------------------------


def test_a3_unknown_id(graph: Repo) -> None:
    block = human()["EXP-20"]
    nodes = node_lines(block)
    assert [n[1] for n in nodes] == ["EXP-99"], block
    assert "unknown ID" in nodes[0][2], nodes[0][2]
    assert entries()["EXP-20"]["unmet"] == [
        {"id": "EXP-99", "status": None, "assignee": None, "state": "unknown", "children": []}
    ]


CYCLE_LINE = re.compile(r"↻ cycle: (EXP-2[12]) → (EXP-2[12]) → (EXP-2[12])")


@pytest.mark.parametrize("root", ["EXP-21", "EXP-22"])
def test_a3_cycle_terminates(graph: Repo, root: str) -> None:
    block = human()[root]  # invoke() fails the test if it doesn't return
    cycles = [m for line in block if (m := CYCLE_LINE.search(line))]
    assert cycles, f"no `↻ cycle: A → B → A` line under {root}:\n" + "\n".join(block)
    a, b, c = cycles[0].groups()
    assert a == c != b, cycles[0].group(0)
    assert len(block) < 10, "the cycle is not cut short:\n" + "\n".join(block)


def test_a3_cycle_json_state(graph: Repo) -> None:
    unmet = entries()["EXP-21"]["unmet"]
    assert [n["id"] for n in unmet] == ["EXP-22"], unmet
    assert unmet[0]["state"] == "cycle", unmet


# --- Acceptance 4: cross-board dead ------------------------------------------------------


def test_a4_cross_board_dead(graph: Repo) -> None:
    assert entries()["EXP-30"]["unmet"] == [
        {"id": "H1.1", "status": "abandoned", "assignee": None, "state": "dead", "children": []}
    ]
    nodes = node_lines(human()["EXP-30"])
    assert [n[1] for n in nodes] == ["H1.1"], nodes
    assert "dead" in nodes[0][2], nodes[0][2]


# --- Acceptance 5: hdd abandoned / active ---------------------------------------------------


@pytest.mark.parametrize("item_id", ["H1.1", "H1.4"])
def test_a5_abandoned_is_not_listed(graph: Repo, item_id: str) -> None:
    assert item_id not in entries()
    assert item_id not in entries(["--all"])
    assert item_id not in human()


def test_a5_active_listed_only_with_unmet_deps(graph: Repo) -> None:
    got = entries()
    assert "H1.2" not in got
    assert got["H1.3"] == {
        "id": "H1.3", "board": "research", "status": "active",
        "canonical_status": "in_progress", "assignee": None, "status_blocked": False,
        "unmet": [{"id": "EXP-3", "status": "harbor", "assignee": None,
                   "state": "unfinished", "children": []}],
    }


def test_status_blocked_only(graph: Repo) -> None:
    assert entries()["EXP-40"] == {
        "id": "EXP-40", "board": "development", "status": "stranded",
        "canonical_status": "blocked", "assignee": None, "status_blocked": True, "unmet": [],
    }
    block = human()["EXP-40"]
    assert block[0].startswith("EXP-40 (stranded"), block
    assert node_lines(block) == [], block


# --- Acceptance 6: both ------------------------------------------------------------------------


def test_a6_both_once(graph: Repo) -> None:
    entry = entries()["EXP-41"]  # entries() fails on a duplicate
    assert entry["status_blocked"] is True
    assert [n["id"] for n in entry["unmet"]] == ["EXP-14"], entry
    block = human()["EXP-41"]  # roots() fails on a duplicate root
    assert [n[1] for n in node_lines(block)] == ["EXP-14"], block


# --- Acceptance 7: backlog ---------------------------------------------------------------------


def test_a7_backlog_hidden_by_default(graph: Repo) -> None:
    assert "EXP-50" not in entries()
    assert "EXP-50" not in human()


def test_a7_backlog_shown_with_all(graph: Repo) -> None:
    got = entries(["--all"])
    assert set(got) == DEFAULT_LISTED | {"EXP-50"}
    assert got["EXP-50"]["canonical_status"] == "backlog"
    assert [n["id"] for n in got["EXP-50"]["unmet"]] == ["EXP-14"]
    assert "EXP-50" in human(["--all"])


# --- Acceptance 8: --board ----------------------------------------------------------------------


def test_a8_board_research(graph: Repo) -> None:
    got = entries(["--board", "research"])
    assert set(got) == {"H1.3"}
    # resolved across boards: EXP-3 is unfinished, not unknown
    assert got["H1.3"]["unmet"][0]["state"] == "unfinished"
    assert set(human(["--board", "research"])) == {"H1.3"}


def test_a8_board_development(graph: Repo) -> None:
    got = entries(["--board", "development"])
    assert set(got) == DEFAULT_LISTED - {"H1.3"}
    assert got["EXP-30"]["unmet"][0]["state"] == "dead"  # H1.1 still found


# --- Acceptance 9: JSON schema -------------------------------------------------------------------


def _walk(nodes: list[Any], where: str) -> None:
    assert isinstance(nodes, list), f"{where}: unmet/children is not a list"
    for node in nodes:
        assert isinstance(node, dict) and set(node) == NODE_KEYS, f"{where}: {node!r}"
        assert node["state"] in STATES, f"{where}: {node!r}"
        _walk(node["children"], f"{where} > {node['id']}")


@pytest.mark.parametrize("args", [[], ["--all"]])
def test_a9_schema(graph: Repo, args: list[str]) -> None:
    data = payload(args)
    assert set(data) == {"items"}, data.keys()
    for entry in data["items"]:
        assert set(entry) == ITEM_KEYS, entry
        assert isinstance(entry["status_blocked"], bool), entry
        _walk(entry["unmet"], entry["id"])
        assert entry["status_blocked"] or entry["unmet"], (
            f"{entry['id']} is listed but neither status- nor dependency-blocked"
        )


# --- Expected 5: help -----------------------------------------------------------------------------


def test_e5_help_cross_references_critical_path() -> None:
    result = CliRunner().invoke(main, ["blocked", "--help"], terminal_width=10_000)
    assert result.exit_code == 0, result.output
    assert "hdd critical-path --dev-blockers" in " ".join(result.output.split())


# --- Acceptance 10 / Expected 4: skills -------------------------------------------------------------

SKILL_FILES = [SKILLS / "software" / "blocked" / "SKILL.md",
               SKILLS / "nautical" / "blocked" / "SKILL.md"]


def _skill_commands(path: Path) -> list[tuple[str, ...]]:
    return [w for line in path.read_text().splitlines() for w in _commands_in(line)]


@pytest.mark.parametrize("path", SKILL_FILES, ids=["software", "nautical"])
def test_a10_skill_prints_add_dep(path: Path) -> None:
    hits = [w for w in _skill_commands(path) if w[:1] == ("update",) and "--add-dep" in w]
    assert hits, f"{path.relative_to(SKILLS.parent)} prints no `update X --add-dep Y`"
    for words in hits:
        assert _rejection(*words) is None, (words, _rejection(*words))


@pytest.mark.parametrize("path", SKILL_FILES, ids=["software", "nautical"])
def test_a10_skill_prints_comment_body_file_stdin(path: Path) -> None:
    hits = [
        w for w in _skill_commands(path)
        if w[:1] == ("comment",) and any(
            a == "--body-file" and b == "-" for a, b in zip(w, w[1:])
        )
    ]
    assert hits, f"{path.relative_to(SKILLS.parent)} prints no `comment X --body-file -`"
    for words in hits:
        assert _rejection(*words) is None, (words, _rejection(*words))


@pytest.mark.parametrize("path", SKILL_FILES, ids=["software", "nautical"])
def test_e4_skill_warns_hdd_blocked_means_abandoned(path: Path) -> None:
    text = path.read_text().lower()
    assert any(
        "hdd" in para and "abandon" in para for para in re.split(r"\n\s*\n", text)
    ), f"{path.relative_to(SKILLS.parent)} has no hdd warning that blocked means abandoned"
