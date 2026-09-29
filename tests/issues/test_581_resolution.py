"""Issue #581: ``move ID STATUS --resolution R [--superseded-by ID]``.

The spec is the revised issue body (Expected 1-7, Acceptance 1-8), rewritten after
the adversarial review. #586 (the proposed item is judged by rules and gates) has
landed; #575 (``is_finished``, ``dependency_state``, ``pickable``) and #578 (the
bounce clause) are the base.

Public surface pinned here:

- ``yurtle_kanban.models.RESOLUTIONS == ("completed", "superseded", "duplicate",
  "wont_do")``. The parser, ``move --resolution`` (a ``click.Choice``), MCP
  ``kanban_move_item`` and ``list --resolution`` all use it. The parser still warns
  ("Unknown resolution") on anything else, including the removed ``obsolete`` and
  ``merged``.
- ``move --resolution R`` is accepted only on a **finished** target (canonical
  ``done``, or a ``closed: true`` column such as hdd ``abandoned``). ``completed`` is
  accepted only on canonical ``done``. Anything else exits 1.
- ``superseded`` and ``duplicate`` need exactly one ``--superseded-by ID``, and
  ``--superseded-by`` needs one of them. The target is upper-cased and looked up on
  every board. It is refused (exit 1) when it is unknown, the item itself, itself
  ``superseded``/``duplicate`` (the message names the final target), or when it
  would make a supersession cycle.
- Rules and gates judge the proposed item, which carries the resolution.
- The frontmatter gets ``resolution`` and ``superseded_by``. The status-change node
  gets ``kb:resolution "R"`` and ``kb:supersededBy``.
- A plain ``move X done`` writes no ``resolution``. ``list --resolution completed``
  matches an absent resolution as well as an explicit one.
- A move to a status that is not finished removes both keys and records
  ``kb:clearedResolution "<old>"``.
- Human ``show`` gets a ``Resolution`` row and a ``Superseded by`` row.
- ``list --resolution bogus`` exits 1.
- ``dependency_state``/``pickable``: a ``wont_do`` dependency is ``dead``. A
  ``superseded``/``duplicate`` one follows ``superseded_by`` (cycle-safe) and takes
  the final target's state. A closed column with no resolution stays ``dead``.

Readings the test partner chose (the driver may challenge them):

a. Acceptance 1's "any resolution on ``ready``" can't be run on hdd, which has no
   ready status. The hdd non-finished case is ``draft`` instead (and ``active``
   → ``draft`` is a legal hdd move). On hdd, "done" is its canonical-done column
   ``complete``. nautical ``blocked`` (stranded) is not a closed column, so a
   resolution there is refused too (Expected 2: "done or a closed column").
b. A refused move leaves the item's file byte-identical and makes no commit. The
   refusal exit code is 1, except for an unknown ``--resolution`` value on
   ``move``: the spec calls for a ``click.Choice``, which is a usage error, so only
   "non-zero" is pinned there.
c. "Not repeatable" is read as: two ``--superseded-by`` options are refused
   (non-zero, nothing written). By default click would take the last one silently.
d. ``superseded_by`` is checked on the parsed item (``["EXP-9"]``). Whether the
   frontmatter writes it as a flow list or a scalar is left open. ``kb:supersededBy``
   must name the target, but the node's form (literal or IRI) is open.
e. The chained-target message must contain the final target's ID. Its wording is
   open. A cycle refusal is pinned only as exit 1 with nothing written.
f. The ``show`` rows are matched as ``Resolution`` / ``Superseded by`` (the colon is
   optional, since today's ``show`` is a table without colons), each on one line with
   its value.
g. Acceptance 7's reasons: for ``wont_do``, the reason starts
   ``waiting on X (dead``. For a duplicate whose target is unfinished, it starts
   ``waiting on `` and names the target. For a cycle, it contains ``cycle``.
   ``dependency_state`` returns ``dead`` / ``unfinished`` / ``met`` / ``cycle`` for
   the same fixtures. Nothing else in the wording is pinned.
h. Acceptance 8's "the same object" is pinned as identity for ``click.Choice``
   (click keeps a tuple as the same tuple) and as equality for MCP's JSON ``enum``.
   The parser is pinned by behaviour, through its warning.
i. MCP's move argument is named ``resolution`` (the field's name). The name of its
   target argument is left open.
"""

from __future__ import annotations

import json
import logging
import re
import signal
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import click
import pytest
from click.testing import CliRunner, Result

from tests.issues.test_574_claim import frontmatter, history_nodes
from tests.issues.test_575_pickable_next import Repo, _git
from yurtle_kanban.cli import main
from yurtle_kanban.mcp.server import KanbanMCPServer

pytestmark = pytest.mark.usefixtures("claim_env")

SPEC_RESOLUTIONS = ("completed", "superseded", "duplicate", "wont_do")

CONFIG = """\
version: "2.0"
boards:
  - name: development
    preset: nautical
    path: "work/"
{gates}  - name: software
    preset: software
    path: "soft/"
  - name: research
    preset: hdd
    path: "research/"
default_board: development
"""

DONE_GATE = """\
    gates:
      "* -> done":
        - id: require_resolution
          check: item.resolution
          message: "Resolution required"
"""


# --- harness ---------------------------------------------------------------------


def item_md(
    item_id: str, status: str, *, deps: tuple[str, ...] = (),
    resolution: str | None = None, superseded_by: tuple[str, ...] = (),
) -> str:
    kind = (
        "hypothesis" if item_id.startswith("H")
        else "feature" if item_id.startswith("FEAT") else "expedition"
    )
    lines = [
        f"id: {item_id}",
        f'title: "Item {item_id}"',
        f"type: {kind}",
        f"status: {status}",
        "priority: medium",
        f"depends_on: [{', '.join(deps)}]",
    ]
    if resolution:
        lines.append(f"resolution: {resolution}")
    if superseded_by:
        lines.append(f"superseded_by: [{', '.join(superseded_by)}]")
    return f"---\n{chr(10).join(lines)}\n---\n\n# Item {item_id}\n\nA description long enough.\n"


class Boards(Repo):
    """development (nautical, EXP- under work/), software (FEAT- under soft/) and
    research (hdd, H under research/), with NO remote."""

    def path(self, item_id: str) -> Path:
        if item_id.startswith("H"):
            return self.root / "research" / "hypotheses" / f"{item_id}-item.md"
        if item_id.startswith("FEAT"):
            return self.root / "soft" / "features" / f"{item_id}-item.md"
        return self.root / "work" / "expeditions" / f"{item_id}-item.md"

    def write(self, item_id: str, status: str, **kw: Any) -> Path:
        p = self.path(item_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(item_md(item_id, status, **kw), encoding="utf-8")
        return p

    def text(self, item_id: str) -> str:
        return self.path(item_id).read_text(encoding="utf-8")

    def fm(self, item_id: str) -> dict[str, Any]:
        return frontmatter(self.text(item_id))

    def last_node(self, item_id: str) -> str:
        nodes = history_nodes(self.text(item_id))
        assert nodes, f"{item_id} has no status history:\n{self.text(item_id)}"
        return nodes[-1]


def make_boards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, items: dict[str, dict[str, Any]],
    *, gates: str = "",
) -> Boards:
    root = tmp_path / "repo"
    (root / ".kanban").mkdir(parents=True)
    (root / ".kanban" / "config.yaml").write_text(CONFIG.format(gates=gates))
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "t@t.com")
    _git(root, "config", "user.name", "tester")
    _git(root, "config", "commit.gpgsign", "false")
    _git(root, "config", "core.hooksPath", "/dev/null")
    repo = Boards(root)
    for item_id, spec in items.items():
        spec = dict(spec)
        repo.write(item_id, spec.pop("status"), **spec)
    repo.commit("fixture")
    monkeypatch.chdir(root)
    ids = {i.id for i in repo.service().get_items()}
    assert ids == set(items), f"fixture scan mismatch: {sorted(ids ^ set(items))}"
    return repo


def invoke(args: list[str]) -> Result:
    return CliRunner().invoke(main, args)


def out(result: Result) -> str:
    return " ".join((result.output or "").split())


def moved(result: Result) -> None:
    assert result.exit_code == 0, (
        f"exit {result.exit_code}: {result.exception!r}\n{result.output}"
    )


def assert_refused(repo: Boards, item_id: str, args: list[str], *, code: int | None = 1) -> Result:
    """Run `args`; assert it is refused and writes nothing (reading b)."""
    before, head = repo.text(item_id), repo.head()
    result = invoke(args)
    if code is None:
        assert result.exit_code != 0, f"accepted: {args}\n{result.output}"
    else:
        assert result.exit_code == code, (
            f"{args}: exit {result.exit_code} (want {code}): {result.exception!r}\n"
            f"{result.output}"
        )
    assert result.exception is None or isinstance(result.exception, SystemExit), (
        f"{args} crashed: {result.exception!r}"
    )
    assert repo.text(item_id) == before, f"{args} was refused but wrote {item_id}"
    assert repo.head() == head, f"{args} was refused but committed"
    return result


def assert_resolved(
    repo: Boards, item_id: str, resolution: str, target: str | None = None,
) -> None:
    fm = repo.fm(item_id)
    assert fm.get("resolution") == resolution, f"frontmatter: {fm}"
    node = repo.last_node(item_id)
    assert f'kb:resolution "{resolution}"' in node, f"history node lacks it:\n{node}"
    item = repo.item(item_id)
    assert item.resolution == resolution
    if target is None:
        assert "superseded_by" not in fm, f"frontmatter: {fm}"
        assert item.superseded_by == []
    else:
        assert item.superseded_by == [target], f"superseded_by: {item.superseded_by!r}"
        assert re.search(rf"kb:supersededBy\s+\S*{re.escape(target)}", node), (
            f"history node lacks kb:supersededBy {target}:\n{node}"
        )


@contextmanager
def no_hang(seconds: int = 10) -> Iterator[None]:
    def boom(*_: Any) -> None:
        raise AssertionError(f"did not return within {seconds}s")

    old = signal.signal(signal.SIGALRM, boom)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)


def val(x: Any) -> Any:
    return getattr(x, "value", x)


# --- Acceptance 1: each value on software, nautical and hdd boards ------------------

# (board prefix, the starting status, canonical done, the non-finished target)
BOARDS = {
    "software": ("FEAT", "review", "done", "ready"),
    "nautical": ("EXP", "review", "done", "ready"),
    "hdd": ("H", "active", "complete", "draft"),  # reading a
}


def a1_items(prefix: str, start: str) -> dict[str, dict[str, Any]]:
    sep = "" if prefix == "H" else "-"
    return {
        f"{prefix}{sep}1": {"status": start},
        f"{prefix}{sep}9": {"status": start},  # a neutral --superseded-by target
    }


def ids(prefix: str) -> tuple[str, str]:
    sep = "" if prefix == "H" else "-"
    return f"{prefix}{sep}1", f"{prefix}{sep}9"


def target_args(resolution: str, target: str) -> list[str]:
    return ["--superseded-by", target] if resolution in ("superseded", "duplicate") else []


@pytest.mark.parametrize("resolution", SPEC_RESOLUTIONS)
@pytest.mark.parametrize("board", sorted(BOARDS))
def test_a1_every_resolution_on_canonical_done(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, board: str, resolution: str,
) -> None:
    prefix, start, done, _ = BOARDS[board]
    repo = make_boards(tmp_path, monkeypatch, a1_items(prefix, start))
    item, target = ids(prefix)
    moved(invoke(["move", item, done, "--resolution", resolution,
                  *target_args(resolution, target)]))
    assert repo.service().status_label(repo.item(item)) == done
    assert_resolved(repo, item, resolution, target if target_args(resolution, target) else None)


@pytest.mark.parametrize("resolution", ["superseded", "duplicate", "wont_do"])
def test_a1_closed_column_takes_all_but_completed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, resolution: str,
) -> None:
    repo = make_boards(tmp_path, monkeypatch, a1_items("H", "active"))
    moved(invoke(["move", "H1", "abandoned", "--resolution", resolution,
                  *target_args(resolution, "H9")]))
    assert repo.service().status_label(repo.item("H1")) == "abandoned"
    assert_resolved(repo, "H1", resolution, "H9" if target_args(resolution, "H9") else None)


def test_a1_completed_on_closed_column_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_boards(tmp_path, monkeypatch, a1_items("H", "active"))
    assert_refused(repo, "H1", ["move", "H1", "abandoned", "--resolution", "completed"])


@pytest.mark.parametrize("resolution", SPEC_RESOLUTIONS)
@pytest.mark.parametrize("board", sorted(BOARDS))
def test_a1_any_resolution_on_unfinished_target_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, board: str, resolution: str,
) -> None:
    prefix, _, _, unfinished = BOARDS[board]
    start = "backlog" if board != "hdd" else "active"
    repo = make_boards(tmp_path, monkeypatch, a1_items(prefix, start))
    item, target = ids(prefix)
    assert_refused(repo, item, ["move", item, unfinished, "--resolution", resolution,
                                *target_args(resolution, target)])


@pytest.mark.parametrize("resolution", ["superseded", "duplicate", "wont_do"])
def test_a1_blocked_is_not_a_closed_column(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, resolution: str,
) -> None:
    """nautical `blocked` (stranded) is not `closed: true` (reading a)."""
    repo = make_boards(tmp_path, monkeypatch, a1_items("EXP", "ready"))
    assert_refused(repo, "EXP-1", ["move", "EXP-1", "blocked", "--resolution", resolution,
                                   *target_args(resolution, "EXP-9")])


def test_a1_unknown_resolution_value_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_boards(tmp_path, monkeypatch, a1_items("EXP", "review"))
    for bogus in ("wontfix", "obsolete", "merged"):
        assert_refused(repo, "EXP-1", ["move", "EXP-1", "done", "--resolution", bogus],
                       code=None)


# --- Acceptance 2: --superseded-by pairing and target validation --------------------

A2_ITEMS: dict[str, dict[str, Any]] = {
    "EXP-1": {"status": "review"},
    "EXP-9": {"status": "ready"},
    # EXP-20 is a duplicate of EXP-21, which is superseded by EXP-22 (the final one)
    "EXP-20": {"status": "done", "resolution": "duplicate", "superseded_by": ("EXP-21",)},
    "EXP-21": {"status": "done", "resolution": "superseded", "superseded_by": ("EXP-22",)},
    "EXP-22": {"status": "done"},
    # EXP-30 is superseded by EXP-1: marking EXP-1 a duplicate of it closes a cycle
    "EXP-30": {"status": "done", "resolution": "superseded", "superseded_by": ("EXP-1",)},
    "FEAT-5": {"status": "ready"},
    "H5": {"status": "active"},
}


@pytest.fixture
def a2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Boards:
    return make_boards(tmp_path, monkeypatch, A2_ITEMS)


@pytest.mark.parametrize("resolution", ["superseded", "duplicate"])
def test_a2_target_is_required(a2: Boards, resolution: str) -> None:
    assert_refused(a2, "EXP-1", ["move", "EXP-1", "done", "--resolution", resolution])


@pytest.mark.parametrize("extra", [[], ["--resolution", "wont_do"],
                                   ["--resolution", "completed"]])
def test_a2_superseded_by_needs_superseded_or_duplicate(a2: Boards, extra: list[str]) -> None:
    assert_refused(a2, "EXP-1", ["move", "EXP-1", "done", *extra, "--superseded-by", "EXP-9"])


def test_a2_superseded_by_is_not_repeatable(a2: Boards) -> None:
    """Reading c."""
    assert_refused(a2, "EXP-1", [
        "move", "EXP-1", "done", "--resolution", "duplicate",
        "--superseded-by", "EXP-9", "--superseded-by", "EXP-22",
    ], code=None)


def test_a2_unknown_target_is_refused(a2: Boards) -> None:
    assert_refused(a2, "EXP-1", ["move", "EXP-1", "done", "--resolution", "duplicate",
                                 "--superseded-by", "EXP-404"])


@pytest.mark.parametrize("self_id", ["EXP-1", "exp-1"])
def test_a2_self_target_is_refused(a2: Boards, self_id: str) -> None:
    assert_refused(a2, "EXP-1", ["move", "EXP-1", "done", "--resolution", "superseded",
                                 "--superseded-by", self_id])


@pytest.mark.parametrize("via", ["EXP-20", "EXP-21", "exp-20"])
def test_a2_superseded_target_is_refused_naming_the_final_one(a2: Boards, via: str) -> None:
    result = assert_refused(a2, "EXP-1", ["move", "EXP-1", "done", "--resolution",
                                          "duplicate", "--superseded-by", via])
    assert "EXP-22" in out(result), f"the message does not name EXP-22:\n{result.output}"


def test_a2_supersession_cycle_is_refused(a2: Boards) -> None:
    assert_refused(a2, "EXP-1", ["move", "EXP-1", "done", "--resolution", "duplicate",
                                 "--superseded-by", "EXP-30"])


def test_a2_target_is_upper_cased(a2: Boards) -> None:
    moved(invoke(["move", "EXP-1", "done", "--resolution", "duplicate",
                  "--superseded-by", "exp-9"]))
    assert_resolved(a2, "EXP-1", "duplicate", "EXP-9")


@pytest.mark.parametrize("target", ["FEAT-5", "H5", "h5"])
def test_a2_target_may_be_on_another_board(a2: Boards, target: str) -> None:
    moved(invoke(["move", "EXP-1", "done", "--resolution", "superseded",
                  "--superseded-by", target]))
    assert_resolved(a2, "EXP-1", "superseded", target.upper())


# --- Acceptance 3: a resolution gate judges the proposed item -----------------------


def test_a3_resolution_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = make_boards(
        tmp_path, monkeypatch,
        {"EXP-1": {"status": "review"}, "EXP-2": {"status": "review"}}, gates=DONE_GATE,
    )
    assert_refused(repo, "EXP-1", ["move", "EXP-1", "done"])
    moved(invoke(["move", "EXP-1", "done", "--resolution", "completed"]))
    assert_resolved(repo, "EXP-1", "completed")
    moved(invoke(["move", "EXP-2", "done", "--resolution", "wont_do"]))
    assert_resolved(repo, "EXP-2", "wont_do")


# --- Acceptance 4: implicit completion ----------------------------------------------


def list_ids(args: list[str]) -> list[str]:
    result = invoke(["list", *args, "--json"])
    assert result.exit_code == 0, f"exit {result.exit_code}: {result.exception!r}\n{result.output}"
    payload = json.loads(result.stdout) if result.stdout.strip().startswith("[") else []
    return sorted(entry["id"] for entry in payload)


def test_a4_plain_done_writes_no_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_boards(tmp_path, monkeypatch, {
        "EXP-1": {"status": "review"},
        "EXP-2": {"status": "done", "resolution": "completed"},
        "EXP-3": {"status": "done", "resolution": "wont_do"},
        "EXP-4": {"status": "done", "resolution": "duplicate", "superseded_by": ("EXP-2",)},
        "EXP-5": {"status": "ready"},
    })
    moved(invoke(["move", "EXP-1", "done"]))
    fm = repo.fm("EXP-1")
    assert "resolution" not in fm, f"a plain move wrote a resolution: {fm}"
    assert repo.item("EXP-1").resolution is None
    assert list_ids(["--resolution", "completed"]) == ["EXP-1", "EXP-2"]
    assert list_ids(["--resolution", "wont_do"]) == ["EXP-3"]
    assert list_ids(["--resolution", "duplicate"]) == ["EXP-4"]


# --- Acceptance 5: reopening clears it ----------------------------------------------


def test_a5_reopen_clears_the_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_boards(tmp_path, monkeypatch, {
        "EXP-1": {"status": "done", "resolution": "duplicate", "superseded_by": ("EXP-2",)},
        "EXP-2": {"status": "ready"},
    })
    moved(invoke(["move", "EXP-1", "ready", "--force"]))
    fm = repo.fm("EXP-1")
    assert "resolution" not in fm and "superseded_by" not in fm, f"frontmatter: {fm}"
    item = repo.item("EXP-1")
    assert item.resolution is None and item.superseded_by == []
    node = repo.last_node("EXP-1")
    assert 'kb:clearedResolution "duplicate"' in node, f"history node:\n{node}"


def test_a5_reopen_after_move_with_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The keys the move wrote are the ones reopening removes; hdd abandoned → draft
    is a legal move, no --force."""
    repo = make_boards(tmp_path, monkeypatch, {"H1": {"status": "active"},
                                               "H2": {"status": "active"}})
    moved(invoke(["move", "H1", "abandoned", "--resolution", "superseded",
                  "--superseded-by", "H2"]))
    assert_resolved(repo, "H1", "superseded", "H2")
    moved(invoke(["move", "H1", "draft"]))
    fm = repo.fm("H1")
    assert "resolution" not in fm and "superseded_by" not in fm, f"frontmatter: {fm}"
    assert 'kb:clearedResolution "superseded"' in repo.last_node("H1")


def test_a5_a_finished_to_finished_move_keeps_no_cleared_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only a move to a status that is not finished clears; a move with no
    resolution to clear records no kb:clearedResolution."""
    repo = make_boards(tmp_path, monkeypatch, {"EXP-1": {"status": "done"}})
    moved(invoke(["move", "EXP-1", "ready", "--force"]))
    assert "kb:clearedResolution" not in repo.last_node("EXP-1")


# --- Expected 5: the human show rows -------------------------------------------------


def test_e5_show_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make_boards(tmp_path, monkeypatch, {
        "EXP-1": {"status": "done", "resolution": "duplicate", "superseded_by": ("EXP-2",)},
        "EXP-2": {"status": "ready"},
    })
    shown = invoke(["show", "EXP-1"])
    moved(shown)
    assert re.search(r"Resolution:?\s.*duplicate", shown.output), shown.output
    assert re.search(r"Superseded by:?\s.*EXP-2", shown.output), shown.output
    plain = invoke(["show", "EXP-2"])
    moved(plain)
    assert "Resolution" not in plain.output and "Superseded by" not in plain.output


# --- Acceptance 6: list --resolution validates -------------------------------------


@pytest.mark.parametrize("bogus", ["bogus", "obsolete", "merged", "wontfix"])
def test_a6_list_unknown_resolution_exits_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bogus: str,
) -> None:
    make_boards(tmp_path, monkeypatch, {"EXP-1": {"status": "done"}})
    result = invoke(["list", "--resolution", bogus])
    assert result.exit_code == 1, (
        f"exit {result.exit_code}: {result.exception!r}\n{result.output}"
    )
    assert bogus in result.output


# --- Acceptance 7: the dependency table ---------------------------------------------

A7_ITEMS: dict[str, dict[str, Any]] = {
    # wont_do on done -> dead
    "EXP-1": {"status": "done", "resolution": "wont_do"},
    "EXP-101": {"status": "ready", "deps": ("EXP-1",)},
    # duplicate of an unfinished target -> the target's state, naming it
    "EXP-2": {"status": "done", "resolution": "duplicate", "superseded_by": ("EXP-3",)},
    "EXP-3": {"status": "review"},  # unfinished; review -> done is legal
    "EXP-102": {"status": "ready", "deps": ("EXP-2",)},
    # duplicate of a done target -> met
    "EXP-4": {"status": "done", "resolution": "duplicate", "superseded_by": ("EXP-5",)},
    "EXP-5": {"status": "done"},
    "EXP-104": {"status": "ready", "deps": ("EXP-4",)},
    # superseded on hdd abandoned (closed column) by a done target -> met
    "H6": {"status": "abandoned", "resolution": "superseded", "superseded_by": ("EXP-5",)},
    "EXP-106": {"status": "ready", "deps": ("H6",)},
    # a chain: superseded by a duplicate of a done item -> met
    "EXP-7": {"status": "done", "resolution": "superseded", "superseded_by": ("EXP-4",)},
    "EXP-107": {"status": "ready", "deps": ("EXP-7",)},
    # duplicate of a wont_do item -> dead (the target's state)
    "EXP-8": {"status": "done", "resolution": "duplicate", "superseded_by": ("EXP-1",)},
    "EXP-108": {"status": "ready", "deps": ("EXP-8",)},
    # explicit completed -> met; wont_do on a closed column -> dead
    "EXP-9": {"status": "done", "resolution": "completed"},
    "EXP-109": {"status": "ready", "deps": ("EXP-9",)},
    "H10": {"status": "abandoned", "resolution": "wont_do"},
    "EXP-110": {"status": "ready", "deps": ("H10",)},
    # closed column, no resolution -> dead (unchanged from #575)
    "H11": {"status": "abandoned"},
    "EXP-111": {"status": "ready", "deps": ("H11",)},
    # a supersession cycle entered by hand
    "EXP-12": {"status": "done", "resolution": "duplicate", "superseded_by": ("EXP-13",)},
    "EXP-13": {"status": "done", "resolution": "superseded", "superseded_by": ("EXP-12",)},
    "EXP-112": {"status": "ready", "deps": ("EXP-12",)},
    # a lower-case dependency on a superseded item
    "EXP-114": {"status": "ready", "deps": ("exp-4",)},
}


@pytest.fixture
def a7(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Boards:
    return make_boards(tmp_path, monkeypatch, A7_ITEMS)


@pytest.mark.parametrize(
    "dep_id,state",
    [
        ("EXP-1", "dead"),
        ("EXP-2", "unfinished"),
        ("EXP-4", "met"),
        ("exp-4", "met"),
        ("H6", "met"),
        ("EXP-7", "met"),
        ("EXP-8", "dead"),
        ("EXP-9", "met"),
        ("EXP-5", "met"),     # plain done, unchanged
        ("H10", "dead"),
        ("H11", "dead"),      # unchanged from #575
        ("EXP-12", "cycle"),
        ("EXP-13", "cycle"),
    ],
)
def test_a7_dependency_state(a7: Boards, dep_id: str, state: str) -> None:
    with no_hang():
        assert val(a7.service().dependency_state(dep_id)) == state


@pytest.mark.parametrize("item_id", ["EXP-104", "EXP-106", "EXP-107", "EXP-109", "EXP-114"])
def test_a7_pickable_when_the_final_target_is_met(a7: Boards, item_id: str) -> None:
    ok, reason = a7.service().pickable(a7.item(item_id), "agent-A")
    assert (ok, reason) == (True, "pickable")


@pytest.mark.parametrize("item_id,dep", [("EXP-101", "EXP-1"), ("EXP-110", "H10"),
                                         ("EXP-111", "H11")])
def test_a7_wont_do_is_dead(a7: Boards, item_id: str, dep: str) -> None:
    ok, reason = a7.service().pickable(a7.item(item_id), "agent-A")
    assert not ok
    assert reason.startswith(f"waiting on {dep} (dead"), reason


def test_a7_duplicate_of_unfinished_names_the_target(a7: Boards) -> None:
    ok, reason = a7.service().pickable(a7.item("EXP-102"), "agent-A")
    assert not ok
    assert reason.startswith("waiting on "), reason
    assert "EXP-3" in reason, f"the reason does not name the target EXP-3: {reason}"


def test_a7_duplicate_of_wont_do_is_dead(a7: Boards) -> None:
    ok, reason = a7.service().pickable(a7.item("EXP-108"), "agent-A")
    assert not ok
    assert reason.startswith("waiting on ") and "dead" in reason, reason


def test_a7_supersession_cycle_does_not_hang(a7: Boards) -> None:
    with no_hang():
        ok, reason = a7.service().pickable(a7.item("EXP-112"), "agent-A")
    assert not ok
    assert reason.startswith("waiting on ") and "cycle" in reason, reason


def test_a7_target_done_makes_it_pickable(a7: Boards) -> None:
    """X `duplicate` of Z: Z unfinished → not pickable; Z done → pickable."""
    assert not a7.service().pickable(a7.item("EXP-102"), "agent-A")[0]
    moved(invoke(["move", "EXP-3", "done"]))
    assert a7.service().pickable(a7.item("EXP-102"), "agent-A") == (True, "pickable")


def test_a7_list_pickable(a7: Boards) -> None:
    with no_hang():
        result = invoke(["list", "--pickable", "--agent", "agent-A", "--json"])
    moved(result)
    got = {entry["id"] for entry in json.loads(result.stdout)}
    assert {"EXP-104", "EXP-106", "EXP-107", "EXP-109", "EXP-114"} <= got
    assert not got & {"EXP-101", "EXP-102", "EXP-108", "EXP-110", "EXP-111", "EXP-112"}


# --- Acceptance 8: one vocabulary ----------------------------------------------------


def resolutions() -> Any:
    from yurtle_kanban import models

    assert hasattr(models, "RESOLUTIONS"), "yurtle_kanban.models has no RESOLUTIONS"
    return models.RESOLUTIONS


def test_a8_resolutions_constant() -> None:
    assert resolutions() == SPEC_RESOLUTIONS
    assert isinstance(resolutions(), tuple)


def _option(command: click.Command, name: str) -> click.Option:
    for param in command.params:
        if isinstance(param, click.Option) and name in param.opts:
            return param
    raise AssertionError(f"{command.name} has no {name} option")


def test_a8_move_choice_is_the_constant() -> None:
    opt = _option(main.commands["move"], "--resolution")
    assert isinstance(opt.type, click.Choice)
    assert opt.type.choices is resolutions()  # reading h
    target = _option(main.commands["move"], "--superseded-by")
    assert not target.multiple


def test_a8_mcp_move_enum_is_the_constant() -> None:
    tools = {t["name"]: t for t in KanbanMCPServer(repo_root=Path.cwd()).get_tools()}
    props = tools["kanban_move_item"]["inputSchema"]["properties"]
    assert "resolution" in props, f"kanban_move_item has no resolution: {sorted(props)}"
    assert props["resolution"].get("enum") == list(resolutions())


def test_a8_mcp_move_records_the_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_boards(tmp_path, monkeypatch, {"EXP-1": {"status": "review"}})
    reply = KanbanMCPServer(repo_root=repo.root).handle_tool_call(
        "kanban_move_item",
        {"item_id": "EXP-1", "new_status": "done", "resolution": "wont_do"},
    )
    assert "error" not in reply, reply
    assert_resolved(repo, "EXP-1", "wont_do")


@pytest.mark.parametrize("value,warns", [
    *[(r, False) for r in SPEC_RESOLUTIONS],
    ("obsolete", True), ("merged", True), ("wontfix", True),
])
def test_a8_parser_warns_outside_the_constant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
    value: str, warns: bool,
) -> None:
    items: dict[str, dict[str, Any]] = {
        "EXP-2": {"status": "ready"},
        "EXP-1": {"status": "done", "resolution": value,
                  "superseded_by": ("EXP-2",) if value in ("superseded", "duplicate") else ()},
    }
    with caplog.at_level(logging.WARNING):
        repo = make_boards(tmp_path, monkeypatch, items)
        repo.service().get_items()
    warned = any("Unknown resolution" in r.getMessage() for r in caplog.records)
    assert warned is warns, [r.getMessage() for r in caplog.records]
