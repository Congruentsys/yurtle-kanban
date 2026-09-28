"""Issue #575: ``list --pickable`` and a dependency-aware ``next``.

The spec is the revised issue body (Expected 1-8, Acceptance 1-11); the [steer]
comment says to build it as written. This issue owns three things: **finished**,
**pickable** and the dependency walk. #578, #581 and #582 each add one clause later.

Public surface pinned here:

- ``KanbanService.is_finished(item)``: canonical ``done``, or a native status that is
  a theme column marked ``closed: true``. hdd ``abandoned`` carries that key.
- ``KanbanService.dependency_state(dep_id)``: one of ``met``, ``unfinished``,
  ``unknown``, ``dead`` or ``cycle``. It looks across every board and upper-cases IDs.
  ``dead`` means finished but not canonical ``done``.
- ``KanbanService.unmet_dependencies(item)``: a list of DepNodes, each
  ``{id, status, assignee, state, children}``. It is cycle-safe.
- ``KanbanService.pickable(item, actor) -> (bool, reason)``. The first failing clause
  gives the reason:
  1. ``status <native> is not a ready status``;
  2. ``held by X``;
  3. ``waiting on ID (unfinished)`` / ``(unknown ID)`` / ``(dead: <native>)`` /
     ``(cycle: A → B → A)``.
  hdd items are never pickable.
- ``claim`` evaluates ``pickable`` on the fetched item and refuses with its reason.
  ``--take-over`` skips clause 2 only.
- Order: ``priority_rank`` ascending (unranked last), then ``priority_score``
  descending, then ``numeric_id`` ascending, then ``id``.
- ``next [--agent A] [--json]``: A's own ``in_progress`` items come first, oldest
  ``updated`` first; ``review`` is not own work. Otherwise it gives the first pickable
  item. JSON is ``{"id", "kind": "resume"|"pick", "reason"}``; with nothing to offer it
  prints ``null`` and exits 7.
- ``list --pickable [--agent] [--explain] [--json]``. ``claim --next --agent A``:
  ``refused``/``lost`` moves on to the next candidate, ``unreachable`` stops (exit 4),
  and an exhausted list exits 7.
- ``states --json`` gives ``"finished"`` for each state.
- MCP ``kanban_suggest_next`` returns the same item as ``next``.

Readings the test partner chose (the driver may challenge them):

a. Acceptance 7's "claim's refusal reason (dry-run, no remote)". ``claim`` has no
   dry-run, so every fixture item is claimed in a repo with NO remote (the ``local``
   path, which reads the working tree), and the repo is reset after each ``local`` win.
   Spec §4 says claim refuses "with its reason". Claim's messages already carry
   context (the item ID, the take-over hint), so the test pins that the pickable
   reason is CONTAINED in the refusal message, not that the two are equal. An item
   that is in progress and held by the actor is claim's ``noop`` "already yours" and
   is exempt, per §4.
b. ``updated`` is not parsed from frontmatter today: ``WorkItem.updated`` is the parse
   time. The Acceptance 5 fixture makes every candidate source agree on which item is
   older: the frontmatter ``updated:``, the status-history ``kb:at``, the file mtime
   and the commit order. The OLDER item has the HIGHER numeric ID, so a numeric
   fallback fails the test.
c. ``--json`` payloads of ``list --pickable`` are read as a list of objects with
   ``"id"``, or as an object whose ``"items"`` is such a list. The rest of the shape
   is left open.
d. DepNode may be a mapping or an object with attributes. ``state`` may be a string or
   an enum whose ``.value`` is the string. ``status`` must exist, but its value
   (native or canonical) is not pinned.
e. With several unmet dependencies, the reason must start ``waiting on `` and name the
   first one in ``depends_on`` order with its state. Whether it names the others is
   left open. The cycle path may start at either member.
f. MCP is exercised with no actor argument and the actor coming from
   ``$YURTLE_AGENT`` / git ``user.name`` (the name of an actor argument is open). The
   result keeps today's shape: ``{"suggestion": {"id": ...} | None}``.
g. ``claim --next``'s lost race is injected through #574's seam. The test wraps
   ``KanbanService.sync_and_push`` for clone A only, so B's own claim inside the seam
   runs unwrapped.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import CliRunner, Result

from tests.issues.test_574_claim import item_text, push_from_a
from tests.issues.test_574_sync_and_push import snapshot
from tests.issues.test_585_create_push_loop import EXP_DIR, World, git
from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import main
from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.models import WorkItem
from yurtle_kanban.service import KanbanService

REPO = Path(__file__).resolve().parents[2]
A = "agent-A"
B = "agent-B"
GIT_USER = "tester"  # the git fallback identity: holds nothing in any fixture

CONFIG = """\
version: "2.0"
boards:
  - name: development
    preset: nautical
    path: "work/"
  - name: research
    preset: hdd
    path: "research/"
default_board: development
"""

pytestmark = pytest.mark.usefixtures("claim_env")


# --- harness ---------------------------------------------------------------------


def item_md(
    item_id: str,
    status: str,
    *,
    deps: tuple[str, ...] = (),
    assignee: str | None = None,
    priority: str = "medium",
    rank: int | None = None,
    updated: str | None = None,
) -> str:
    hyp = item_id.startswith("H")
    lines = [
        f"id: {item_id}",
        f'title: "Item {item_id}"',
        f"type: {'hypothesis' if hyp else 'expedition'}",
        f"status: {status}",
        f"priority: {priority}",
        f"depends_on: [{', '.join(deps)}]",
    ]
    if assignee:
        lines.append(f"assignee: {assignee}")
    if rank is not None:
        lines.append(f"priority_rank: {rank}")
    if updated:
        lines.append(f"updated: {updated}")
    body = f"---\n{chr(10).join(lines)}\n---\n\n# Item {item_id}\n\nA description long enough.\n"
    if updated:  # a status-history node at the same time (reading b)
        body += (
            "\n```yurtle\n@prefix kb: <https://yurtle.dev/kanban/> .\n"
            "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .\n\n"
            "<> kb:statusChange [\n"
            "    kb:status kb:in_progress ;\n"
            f'    kb:at "{updated}"^^xsd:dateTime ;\n'
            f'    kb:by "{assignee or GIT_USER}" ;\n'
            "] .\n```\n"
        )
    return body


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=True
    ).stdout


class Repo:
    """A two-board repo (development: nautical under work/, research: hdd under
    research/) with NO remote, items hand-written and committed."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def path(self, item_id: str) -> Path:
        if item_id.startswith("H"):
            return self.root / "research" / "hypotheses" / f"{item_id}-item.md"
        return self.root / "work" / "expeditions" / f"{item_id}-item.md"

    def write(self, item_id: str, status: str, **kw: Any) -> Path:
        p = self.path(item_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(item_md(item_id, status, **kw), encoding="utf-8")
        return p

    def commit(self, message: str) -> None:
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-m", message)

    def head(self) -> str:
        return _git(self.root, "rev-parse", "HEAD").strip()

    def service(self) -> KanbanService:
        config_mod._theme_cache.clear()
        return KanbanService(KanbanConfig.load(self.root / ".kanban" / "config.yaml"), self.root)

    def item(self, item_id: str) -> WorkItem:
        found = self.service().get_item(item_id)
        assert found is not None, f"fixture item {item_id} did not parse"
        return found


def make_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, items: dict[str, dict[str, Any]],
    *, user: str = GIT_USER,
) -> Repo:
    root = tmp_path / "repo"
    (root / ".kanban").mkdir(parents=True)
    (root / ".kanban" / "config.yaml").write_text(CONFIG)
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "t@t.com")
    _git(root, "config", "user.name", user)
    _git(root, "config", "commit.gpgsign", "false")
    _git(root, "config", "core.hooksPath", "/dev/null")
    repo = Repo(root)
    for item_id, spec in items.items():
        spec = dict(spec)
        repo.write(item_id, spec.pop("status"), **spec)
    repo.commit("fixture")
    monkeypatch.chdir(root)
    ids = {i.id for i in repo.service().get_items()}
    assert ids == set(items), f"fixture scan mismatch: {sorted(ids ^ set(items))}"
    return repo


# The Acceptance 1 graph, plus the Acceptance 2/3 statuses and holders.
GRAPH: dict[str, dict[str, Any]] = {
    # dependency on a done item -> met (written lower-case: IDs are upper-cased)
    "EXP-1": {"status": "done"},
    "EXP-2": {"status": "ready", "deps": ("exp-1",)},
    # chain EXP-3 <- EXP-4 <- EXP-5
    "EXP-3": {"status": "ready"},
    "EXP-4": {"status": "ready", "deps": ("EXP-3",)},
    "EXP-5": {"status": "ready", "deps": ("EXP-4",)},
    # diamond: EXP-9 -> {EXP-7, EXP-8} -> EXP-6
    "EXP-6": {"status": "ready"},
    "EXP-7": {"status": "ready", "deps": ("EXP-6",), "assignee": B},
    "EXP-8": {"status": "ready", "deps": ("EXP-6",)},
    "EXP-9": {"status": "ready", "deps": ("EXP-7", "EXP-8")},
    # missing ID
    "EXP-10": {"status": "ready", "deps": ("EXP-99",)},
    # cross-board: hdd complete -> met; hdd abandoned -> dead
    "EXP-11": {"status": "ready", "deps": ("H1.1",)},
    "EXP-12": {"status": "ready", "deps": ("H1.2",)},
    # hand-made cycle
    "EXP-13": {"status": "ready", "deps": ("EXP-14",)},
    "EXP-14": {"status": "ready", "deps": ("EXP-13",)},
    # never offered (Acceptance 2)
    "EXP-16": {"status": "backlog"},
    "EXP-17": {"status": "blocked"},
    "EXP-18": {"status": "review"},
    "EXP-19": {"status": "in_progress", "assignee": B},
    "EXP-20": {"status": "in_progress"},
    "EXP-21": {"status": "done"},
    # pre-assigned ready items (Acceptance 3)
    "EXP-22": {"status": "ready", "assignee": A},
    "EXP-23": {"status": "ready", "assignee": B},
    # the research board
    "H1.1": {"status": "complete"},
    "H1.2": {"status": "abandoned"},
    "H1.3": {"status": "draft"},
    "H1.4": {"status": "active"},
}

PICKABLE_FOR_A = ["EXP-2", "EXP-3", "EXP-6", "EXP-11", "EXP-22"]  # in spec order
PICKABLE_UNASSIGNED = ["EXP-2", "EXP-3", "EXP-6", "EXP-11"]
PICKABLE_FOR_B = ["EXP-2", "EXP-3", "EXP-6", "EXP-11", "EXP-23"]
NOT_READY = [
    "EXP-1", "EXP-16", "EXP-17", "EXP-18", "EXP-19", "EXP-20", "EXP-21",
    "H1.1", "H1.2", "H1.3", "H1.4",
]
EXACT_REASONS_FOR_A = {
    "EXP-4": "waiting on EXP-3 (unfinished)",
    "EXP-5": "waiting on EXP-4 (unfinished)",
    "EXP-7": f"held by {B}",
    "EXP-8": "waiting on EXP-6 (unfinished)",
    "EXP-10": "waiting on EXP-99 (unknown ID)",
    "EXP-12": "waiting on H1.2 (dead: abandoned)",
    "EXP-23": f"held by {B}",
}
CYCLE_REASON = {
    "EXP-13": re.compile(
        r"^waiting on EXP-14 \(cycle: (EXP-13 → EXP-14 → EXP-13|EXP-14 → EXP-13 → EXP-14)\)$"
    ),
    "EXP-14": re.compile(
        r"^waiting on EXP-13 \(cycle: (EXP-13 → EXP-14 → EXP-13|EXP-14 → EXP-13 → EXP-14)\)$"
    ),
}


@pytest.fixture
def graph(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Repo:
    return make_repo(tmp_path, monkeypatch, GRAPH)


def invoke(args: list[str]) -> Result:
    return CliRunner().invoke(main, args)


def flat(text: str | None) -> str:
    return " ".join((text or "").split())


def json_of(result: Result) -> Any:
    assert result.exit_code in (0, 7), (
        f"exit {result.exit_code}: {result.exception!r}\n{result.output}"
    )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise AssertionError(f"stdout is not JSON ({e}):\n{result.stdout}") from e


def ids_of(payload: Any) -> list[str]:
    """IDs of a `list --pickable --json` payload (reading c)."""
    if isinstance(payload, Mapping):
        payload = payload.get("items")
    assert isinstance(payload, list), f"not a list of items: {payload!r}"
    return [entry["id"] for entry in payload]


def pickable_ids(args: list[str]) -> list[str]:
    result = invoke(["list", "--pickable", *args, "--json"])
    assert result.exit_code == 0, f"exit {result.exit_code}: {result.exception!r}\n{result.output}"
    return ids_of(json_of(result))


def next_json(args: list[str] = ()) -> Any:  # type: ignore[assignment]
    return json_of(invoke(["next", *args, "--json"]))


def val(x: Any) -> Any:
    return getattr(x, "value", x)


def field(node: Any, name: str) -> Any:
    if isinstance(node, Mapping):
        assert name in node, f"DepNode lacks {name!r}: {node!r}"
        return node[name]
    assert hasattr(node, name), f"DepNode lacks {name!r}: {node!r}"
    return getattr(node, name)


def tree(nodes: list[Any]) -> list[tuple[str, str, list[Any]]]:
    """(id, state, children-tree) for each node, recursively."""
    return [
        (field(n, "id"), val(field(n, "state")), tree(list(field(n, "children"))))
        for n in nodes
    ]


def walk(nodes: list[Any]) -> Iterator[Any]:
    for n in nodes:
        yield n
        yield from walk(list(field(n, "children")))


def status_reason(repo: Repo, item_id: str) -> str:
    svc = repo.service()
    return f"status {svc.status_label(repo.item(item_id))} is not a ready status"


def spec_order(items: list[WorkItem]) -> list[str]:
    ranked = sorted(
        items,
        key=lambda i: (
            i.priority_rank is None,
            i.priority_rank if i.priority_rank is not None else 0,
            -i.priority_score,
            i.numeric_id,
            i.id,
        ),
    )
    return [i.id for i in ranked]


# --- Acceptance 1: finished and the dependency walk ---------------------------------


@pytest.mark.parametrize(
    "item_id,finished",
    [
        ("EXP-1", True),   # canonical done
        ("H1.1", True),    # hdd complete -> done
        ("H1.2", True),    # hdd abandoned: a `closed: true` column
        ("EXP-3", False),  # ready
        ("EXP-17", False), # nautical stranded -> blocked, not closed
        ("H1.3", False),   # hdd draft
        ("H1.4", False),   # hdd active
    ],
)
def test_a1_is_finished(graph: Repo, item_id: str, finished: bool) -> None:
    assert graph.service().is_finished(graph.item(item_id)) is finished


@pytest.mark.parametrize(
    "dep_id,state",
    [
        ("EXP-1", "met"),         # done
        ("exp-1", "met"),         # IDs upper-cased
        ("H1.1", "met"),          # cross-board, hdd complete
        ("h1.1", "met"),
        ("H1.2", "dead"),         # cross-board, hdd abandoned: finished but not done
        ("EXP-3", "unfinished"),
        ("EXP-19", "unfinished"), # in progress
        ("H1.3", "unfinished"),   # hdd draft
        ("EXP-99", "unknown"),
        ("EXP-13", "cycle"),      # hand-made cycle
        ("EXP-14", "cycle"),
    ],
)
def test_a1_dependency_state(graph: Repo, dep_id: str, state: str) -> None:
    assert val(graph.service().dependency_state(dep_id)) == state


def test_a1_unmet_dependencies_met_is_empty(graph: Repo) -> None:
    svc = graph.service()
    assert list(svc.unmet_dependencies(graph.item("EXP-2"))) == []   # done dep
    assert list(svc.unmet_dependencies(graph.item("EXP-11"))) == []  # cross-board met
    assert list(svc.unmet_dependencies(graph.item("EXP-3"))) == []   # no deps


def test_a1_unmet_dependencies_chain(graph: Repo) -> None:
    nodes = list(graph.service().unmet_dependencies(graph.item("EXP-5")))
    assert tree(nodes) == [("EXP-4", "unfinished", [("EXP-3", "unfinished", [])])]
    for n in walk(nodes):
        field(n, "status")
        assert field(n, "assignee") in (None, "")


def test_a1_unmet_dependencies_diamond(graph: Repo) -> None:
    nodes = list(graph.service().unmet_dependencies(graph.item("EXP-9")))
    assert tree(nodes) == [
        ("EXP-7", "unfinished", [("EXP-6", "unfinished", [])]),
        ("EXP-8", "unfinished", [("EXP-6", "unfinished", [])]),
    ], "a shared dependency reached twice is not a cycle"
    assert field(nodes[0], "assignee") == B


def test_a1_unmet_dependencies_missing_and_dead(graph: Repo) -> None:
    svc = graph.service()
    assert tree(list(svc.unmet_dependencies(graph.item("EXP-10")))) == [
        ("EXP-99", "unknown", [])
    ]
    assert tree(list(svc.unmet_dependencies(graph.item("EXP-12")))) == [
        ("H1.2", "dead", [])
    ]


def test_a1_unmet_dependencies_cycle_terminates(graph: Repo) -> None:
    nodes = list(graph.service().unmet_dependencies(graph.item("EXP-13")))
    everything = list(walk(nodes))
    assert everything, "EXP-13's cycle dependency is unmet"
    assert field(nodes[0], "id") == "EXP-14"
    assert len(everything) <= 4, f"the cycle walk did not stop: {tree(nodes)}"
    assert any(val(field(n, "state")) == "cycle" for n in everything), tree(nodes)


# --- the pickable reasons (§3) ---------------------------------------------------------


@pytest.mark.parametrize("item_id", PICKABLE_FOR_A)
def test_pickable_accepts(graph: Repo, item_id: str) -> None:
    ok, _ = graph.service().pickable(graph.item(item_id), A)
    assert ok is True


@pytest.mark.parametrize("item_id", sorted(EXACT_REASONS_FOR_A))
def test_pickable_reason_exact(graph: Repo, item_id: str) -> None:
    assert graph.service().pickable(graph.item(item_id), A) == (
        False, EXACT_REASONS_FOR_A[item_id]
    )


@pytest.mark.parametrize("item_id", sorted(CYCLE_REASON))
def test_pickable_reason_cycle(graph: Repo, item_id: str) -> None:
    ok, reason = graph.service().pickable(graph.item(item_id), A)
    assert ok is False
    assert CYCLE_REASON[item_id].match(reason), reason


def test_pickable_reason_several_unmet_names_the_first(graph: Repo) -> None:
    ok, reason = graph.service().pickable(graph.item("EXP-9"), A)
    assert ok is False
    assert reason.startswith("waiting on EXP-7 (unfinished)"), reason


def test_pickable_status_clause_comes_first(graph: Repo) -> None:
    """EXP-19 is held by B AND in progress: clause 1 (status) is reported."""
    assert graph.service().pickable(graph.item("EXP-19"), A) == (
        False, status_reason(graph, "EXP-19")
    )


def test_pickable_holder_clause_before_dependencies(graph: Repo) -> None:
    """EXP-7 is held by B AND waits on EXP-6: clause 2 is reported."""
    assert graph.service().pickable(graph.item("EXP-7"), A) == (False, f"held by {B}")


# --- Acceptance 2: never offered ---------------------------------------------------------


@pytest.mark.parametrize("item_id", NOT_READY)
def test_a2_not_ready_is_never_pickable(graph: Repo, item_id: str) -> None:
    svc = graph.service()
    for actor in (A, B, None):
        assert svc.pickable(graph.item(item_id), actor) == (
            False, status_reason(graph, item_id)
        ), f"actor {actor!r}"


def test_a2_never_offered_by_list_or_next(graph: Repo) -> None:
    for args in ([], ["--agent", A], ["--agent", B]):
        offered = set(pickable_ids(args))
        assert not offered & set(NOT_READY), f"{args}: offered {sorted(offered & set(NOT_READY))}"
        assert not any(i.startswith("H") for i in offered), f"{args}: an hdd item offered"
    assert next_json(["--agent", A])["id"] not in NOT_READY


def test_a2_hdd_draft_is_not_pickable(graph: Repo) -> None:
    """Decision in the revised spec: hdd boards have no ready column."""
    ok, reason = graph.service().pickable(graph.item("H1.3"), A)
    assert (ok, reason) == (False, "status draft is not a ready status")


# --- Acceptance 3: a pre-assigned ready item ------------------------------------------------


def test_a3_pre_assigned_offered_to_its_assignee_only(graph: Repo) -> None:
    svc = graph.service()
    item = graph.item("EXP-22")
    assert svc.pickable(item, A)[0] is True
    assert svc.pickable(item, "Agent-a")[0] is True, "same_actor: case ignored"
    assert svc.pickable(item, B) == (False, f"held by {A}")
    assert svc.pickable(item, None) == (False, f"held by {A}")

    assert "EXP-22" in pickable_ids(["--agent", A])
    assert "EXP-22" not in pickable_ids(["--agent", B])
    assert "EXP-22" not in pickable_ids([]), "list --pickable without an actor"


# --- Acceptance 4: ordering -------------------------------------------------------------


ORDERING = {
    "EXP-1": {"status": "ready", "priority": "medium"},
    "EXP-2": {"status": "ready", "priority": "medium"},
    "EXP-5": {"status": "ready", "priority": "low", "rank": 2},
    "EXP-6": {"status": "ready", "priority": "critical"},
    "EXP-7": {"status": "ready", "priority": "low", "rank": 1},
    "EXP-8": {"status": "ready", "priority": "high"},
}
ORDER = ["EXP-7", "EXP-5", "EXP-6", "EXP-8", "EXP-1", "EXP-2"]


def test_a4_ordering(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make_repo(tmp_path, monkeypatch, ORDERING)
    listed = pickable_ids([])
    assert listed == ORDER
    assert listed.index("EXP-1") < listed.index("EXP-2"), "same-priority tie: oldest first"
    assert listed.index("EXP-5") < listed.index("EXP-6"), "ranked beats higher priority"
    got = next_json()
    assert (got["id"], got["kind"]) == ("EXP-7", "pick"), got
    assert set(got) == {"id", "kind", "reason"} and isinstance(got["reason"], str), got


def test_a4_next_human_line(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make_repo(tmp_path, monkeypatch, ORDERING)
    result = invoke(["next"])
    assert result.exit_code == 0, result.output
    first = next(line for line in result.stdout.splitlines() if line.strip())
    assert first.strip() == "EXP-7 — top pickable: rank 1, priority low", result.stdout


def test_a4_unranked_tie_break(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make_repo(tmp_path, monkeypatch, {
        "EXP-2": {"status": "ready"}, "EXP-1": {"status": "ready"},
    })
    assert pickable_ids([]) == ["EXP-1", "EXP-2"]
    assert next_json()["id"] == "EXP-1"


# --- Acceptance 5: own in-progress first ---------------------------------------------------


def _own_work(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, with_in_progress: bool) -> Repo:
    """A holds EXP-3 (in progress, OLDER) and EXP-2 (in progress, newer), and EXP-1 in
    review (oldest of all). EXP-4 is pickable. Reading b: frontmatter `updated`,
    history `kb:at`, mtime and commit order all agree; the older has the higher ID."""
    items: dict[str, dict[str, Any]] = {
        "EXP-1": {"status": "review", "assignee": A, "updated": "2025-06-01T09:00:00"},
        "EXP-4": {"status": "ready"},
    }
    repo = make_repo(tmp_path, monkeypatch, items)
    if with_in_progress:
        p = repo.write("EXP-3", "in_progress", assignee=A, updated="2026-01-01T09:00:00")
        os.utime(p, (1767258000, 1767258000))
        repo.commit("EXP-3 underway")
        p = repo.write("EXP-2", "in_progress", assignee=A, updated="2026-02-01T09:00:00")
        os.utime(p, (1769936400, 1769936400))
        repo.commit("EXP-2 underway")
    os.utime(repo.path("EXP-1"), (1748768400, 1748768400))
    return repo


def test_a5_own_in_progress_oldest_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _own_work(tmp_path, monkeypatch, with_in_progress=True)
    got = next_json(["--agent", A])
    assert (got["id"], got["kind"]) == ("EXP-3", "resume"), got
    assert isinstance(got["reason"], str) and got["reason"]

    result = invoke(["next", "--agent", A])
    assert result.exit_code == 0, result.output
    first = next(line for line in result.stdout.splitlines() if line.strip())
    assert first.strip() == "EXP-3 — resume your in-progress item", result.stdout


def test_a5_review_item_is_not_own_work(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _own_work(tmp_path, monkeypatch, with_in_progress=False)
    got = next_json(["--agent", A])
    assert (got["id"], got["kind"]) == ("EXP-4", "pick"), got


def test_a5_others_in_progress_is_not_resumed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _own_work(tmp_path, monkeypatch, with_in_progress=True)
    got = next_json(["--agent", B])
    assert (got["id"], got["kind"]) == ("EXP-4", "pick"), got


def test_next_actor_falls_back_to_git_user(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """§6: `next` resolves the actor with the git fallback allowed."""
    root_items: dict[str, dict[str, Any]] = {
        "EXP-4": {"status": "ready"},
        "EXP-5": {"status": "in_progress", "assignee": A},
    }
    make_repo(tmp_path, monkeypatch, root_items, user=A)
    got = next_json()
    assert (got["id"], got["kind"]) == ("EXP-5", "resume"), got


# --- Acceptance 6: nothing pickable -------------------------------------------------------


def test_a6_next_json_nothing_pickable_exits_7(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make_repo(tmp_path, monkeypatch, {
        "EXP-1": {"status": "done"},
        "EXP-2": {"status": "ready", "assignee": B},
        "EXP-3": {"status": "ready", "deps": ("EXP-4",)},
        "EXP-4": {"status": "backlog"},
        "H1.1": {"status": "draft"},
    })
    result = invoke(["next", "--agent", A, "--json"])
    assert result.exit_code == 7, f"exit {result.exit_code}: {result.output}"
    assert result.stdout.strip() == "null", result.stdout


# --- Acceptance 7: the agreement test -------------------------------------------------------


def test_a7_claim_agrees_with_pickable(graph: Repo) -> None:
    """For every fixture item: claim (no remote: the `local` path) refuses exactly when
    `pickable` does, with pickable's reason in its message (reading a)."""
    base = graph.head()
    mismatches: list[str] = []
    for item_id in GRAPH:
        svc = graph.service()
        ok, reason = svc.pickable(graph.item(item_id), A)
        out = graph.service().claim_item(
            item_id, actor=A, sleep=lambda s: None, jitter=lambda lo, hi: 0.0
        )
        if out.kind == "local":
            _git(graph.root, "reset", "--hard", base)
            if not ok:
                mismatches.append(f"{item_id}: claimed, but pickable says {reason!r}")
        elif out.kind == "refused":
            if ok:
                mismatches.append(f"{item_id}: pickable, but claim refused: {out.message}")
            elif reason not in out.message:
                mismatches.append(
                    f"{item_id}: pickable says {reason!r}, claim says {out.message!r}"
                )
        else:
            mismatches.append(f"{item_id}: claim ended {out.kind}: {out.message}")
        assert graph.head() == base
    assert not mismatches, "\n".join(mismatches)


def test_a7_claim_noop_for_own_in_progress_is_exempt(graph: Repo) -> None:
    """§4: an item held by the actor and in progress is still `already yours`."""
    out = graph.service().claim_item("EXP-19", actor=B)
    assert out.kind == "noop", out.message
    assert "already yours" in out.message.lower()


def test_a7_take_over_skips_only_the_holder_clause(graph: Repo) -> None:
    """EXP-7 is held by B and waits on EXP-6: --take-over still refuses on clause 3."""
    base = graph.head()
    out = graph.service().claim_item("EXP-7", actor=A, take_over=True)
    assert out.kind == "refused", out.message
    assert "waiting on EXP-6 (unfinished)" in out.message
    assert graph.head() == base


def test_a7_pickable_reasons_match_the_spec(graph: Repo) -> None:
    svc = graph.service()
    for item_id in GRAPH:
        ok, reason = svc.pickable(graph.item(item_id), A)
        if item_id in PICKABLE_FOR_A:
            assert ok is True, f"{item_id}: {reason}"
        elif item_id in EXACT_REASONS_FOR_A:
            assert (ok, reason) == (False, EXACT_REASONS_FOR_A[item_id])
        elif item_id in CYCLE_REASON:
            assert ok is False and CYCLE_REASON[item_id].match(reason), reason
        elif item_id == "EXP-9":
            assert ok is False and reason.startswith("waiting on EXP-7 (unfinished)"), reason
        else:
            assert (ok, reason) == (False, status_reason(graph, item_id)), item_id


@pytest.mark.parametrize(
    "args,actor,expected",
    [
        (["--agent", A], A, PICKABLE_FOR_A),
        (["--agent", B], B, PICKABLE_FOR_B),
        ([], None, PICKABLE_UNASSIGNED),
    ],
)
def test_a7_list_offers_exactly_the_pickable_items(
    graph: Repo, args: list[str], actor: str | None, expected: list[str]
) -> None:
    svc = graph.service()
    accepted = [i for i in svc.get_items() if svc.pickable(i, actor)[0]]
    assert spec_order(accepted) == expected, "fixture reading of pickable"
    assert pickable_ids(args) == expected


def test_a7_next_offers_the_first_pickable_item(graph: Repo) -> None:
    got = next_json(["--agent", A])
    assert (got["id"], got["kind"]) == (PICKABLE_FOR_A[0], "pick"), got
    got = next_json()  # git fallback identity holds nothing
    assert (got["id"], got["kind"]) == (PICKABLE_UNASSIGNED[0], "pick"), got
    got = next_json(["--agent", B])  # B holds EXP-19 in progress
    assert (got["id"], got["kind"]) == ("EXP-19", "resume"), got


# --- Acceptance 8: claim --next -------------------------------------------------------------


NEXT_ITEMS = {
    "EXP-001": f"{EXP_DIR}/EXP-001-x.md",
    "EXP-002": f"{EXP_DIR}/EXP-002-y.md",
    "EXP-003": f"{EXP_DIR}/EXP-003-z.md",
}


def _seed_three(world: World) -> None:
    push_from_a(world, {
        NEXT_ITEMS["EXP-002"]: item_text("ready", None, "EXP-002", "Y"),
        NEXT_ITEMS["EXP-003"]: item_text("ready", None, "EXP-003", "Z"),
    }, "seed EXP-002, EXP-003")


def _b_claims(world: World, item_id: str) -> None:
    svc = KanbanService(KanbanConfig.load(world.b / ".kanban" / "config.yaml"), world.b)
    out = svc.claim_item(item_id, actor=B, sleep=lambda s: None, jitter=lambda lo, hi: 0.0)
    assert out.kind == "won", f"B's claim of {item_id}: {out.kind}: {out.message}"


def _holder_on_origin(world: World, item_id: str) -> str | None:
    text = world.remote_show(NEXT_ITEMS[item_id])
    m = re.match(r"\A---\n(.*?)^---", text, re.S | re.M)
    assert m
    return (yaml.safe_load(m.group(1)) or {}).get("assignee")


def _seam_on_a(monkeypatch: pytest.MonkeyPatch, world: World, action: Any) -> list[int]:
    """Wrap `sync_and_push` for clone A only: `action(call, attempt)` runs in its
    seam (reading g). Returns the list of A's calls, appended as they start."""
    real = KanbanService.sync_and_push
    calls: list[int] = []
    a_root = world.a.resolve()

    def wrapped(self: KanbanService, mutate: Any, **kw: Any) -> Any:
        kw["sleep"] = lambda s: None
        kw["jitter"] = lambda lo, hi: 0.0
        if Path(self.repo_root).resolve() != a_root:
            return real(self, mutate, **kw)
        n = len(calls)
        calls.append(n)
        inner = kw.get("seam")

        def seam(attempt: int) -> None:
            if inner is not None:
                inner(attempt)
            action(n, attempt)

        kw["seam"] = seam
        return real(self, mutate, **kw)

    monkeypatch.setattr(KanbanService, "sync_and_push", wrapped)
    return calls


def _claim_next(world: World, monkeypatch: pytest.MonkeyPatch, *extra: str) -> Result:
    monkeypatch.chdir(world.a)
    return CliRunner().invoke(main, ["claim", "--next", *extra])


def test_a8_claim_next_falls_through_a_lost_candidate(world, monkeypatch) -> None:
    _seed_three(world)

    def action(call: int, attempt: int) -> None:
        if call == 0 and attempt == 0:
            _b_claims(world, "EXP-001")

    calls = _seam_on_a(monkeypatch, world, action)
    result = _claim_next(world, monkeypatch, "--agent", A)

    assert result.exit_code == 0, flat(result.output)
    assert _holder_on_origin(world, "EXP-001") == B
    assert _holder_on_origin(world, "EXP-002") == A
    assert _holder_on_origin(world, "EXP-003") is None
    assert len(calls) == 2, f"A tried {len(calls)} candidates"


def test_a8_claim_next_all_lost_exits_7(world, monkeypatch) -> None:
    _seed_three(world)

    def action(call: int, attempt: int) -> None:
        if call == 0 and attempt == 0:
            for item_id in NEXT_ITEMS:
                _b_claims(world, item_id)

    _seam_on_a(monkeypatch, world, action)
    result = _claim_next(world, monkeypatch, "--agent", A)

    assert result.exit_code == 7, f"exit {result.exit_code}: {flat(result.output)}"
    assert "nothing pickable" in flat(result.output).lower(), flat(result.output)
    for item_id in NEXT_ITEMS:
        assert _holder_on_origin(world, item_id) == B


def test_a8_claim_next_unreachable_stops_at_the_first(world, monkeypatch, tmp_path) -> None:
    _seed_three(world)
    git(world.a, "remote", "set-url", "origin", str(tmp_path / "missing.git"))
    before = snapshot(world.a)
    calls = _seam_on_a(monkeypatch, world, lambda call, attempt: None)

    result = _claim_next(world, monkeypatch, "--agent", A)

    assert result.exit_code == 4, f"exit {result.exit_code}: {flat(result.output)}"
    assert len(calls) == 1, f"A tried {len(calls)} candidates after unreachable"
    assert snapshot(world.a) == before


def test_a8_claim_next_requires_an_explicit_actor(world, monkeypatch) -> None:
    assert git(world.a, "config", "user.name").strip(), "A must have a git user.name"
    base = world.remote_sha()

    result = _claim_next(world, monkeypatch)

    out = flat(result.output)
    assert result.exit_code == 1, out
    assert "--agent" in out and "YURTLE_AGENT" in out, out
    assert world.remote_sha() == base


# --- Acceptance 9: list --pickable --explain ---------------------------------------------------


def test_a9_explain_lists_each_non_pickable_ready_item_with_reason(graph: Repo) -> None:
    result = invoke(["list", "--pickable", "--agent", A, "--explain"])
    assert result.exit_code == 0, f"exit {result.exit_code}: {result.exception!r}\n{result.output}"
    out = flat(result.output)
    svc = graph.service()
    for item_id in GRAPH:
        item = graph.item(item_id)
        if item.status.value != "ready":
            continue
        ok, reason = svc.pickable(item, A)
        if ok:
            continue
        assert re.search(rf"\b{re.escape(item_id)}\b", out), f"{item_id} not listed:\n{out}"
        assert reason in out, f"{item_id}'s reason {reason!r} not shown:\n{out}"
    for item_id in set(NOT_READY) - {"H1.2"}:  # H1.2 is named in EXP-12's reason
        assert not re.search(rf"\b{re.escape(item_id)}\b", out), (
            f"--explain lists {item_id}, which is not canonical ready:\n{out}"
        )


def test_list_pickable_warns_about_a_cycle(graph: Repo) -> None:
    result = invoke(["list", "--pickable", "--agent", A])
    assert result.exit_code == 0, result.output
    out = flat(result.output)
    assert "cycle" in out.lower(), out
    assert "EXP-13" in out and "EXP-14" in out, out


def test_list_help_says_pickable_differs_from_status_ready() -> None:
    result = invoke(["list", "--help"])
    assert result.exit_code == 0
    out = flat(result.output)
    assert "--pickable" in out and "--explain" in out and "--agent" in out, out
    assert "--status ready" in out, out


def test_next_help_offers_json_and_agent() -> None:
    out = flat(invoke(["next", "--help"]).output)
    assert "--json" in out and "--agent" in out, out
    assert "--assignee" not in out, "the rename to --agent drops --assignee"


# --- Acceptance 10: closed theme columns and states --json ---------------------------------------


def test_a10_hdd_theme_marks_abandoned_closed() -> None:
    theme = yaml.safe_load((REPO / "themes" / "hdd.yaml").read_text(encoding="utf-8"))
    columns = theme["columns"]
    assert columns["abandoned"].get("closed") is True
    for name in ("draft", "active", "complete"):
        assert columns[name].get("closed", False) is False, name


def test_a10_states_json_finished(graph: Repo) -> None:
    result = invoke(["states", "--json"])
    assert result.exit_code == 0, result.output
    boards = {entry["board"]: entry for entry in json.loads(result.stdout)}
    finished = {
        (board, state["name"]): state.get("finished")
        for board, entry in boards.items()
        for state in entry["states"]
    }
    assert finished[("research", "abandoned")] is True
    assert finished[("research", "complete")] is True
    assert finished[("research", "active")] is False
    assert finished[("research", "draft")] is False
    assert finished[("development", "arrived")] is True
    assert finished[("development", "stranded")] is False
    assert finished[("development", "provisioning")] is False
    assert all(isinstance(v, bool) for v in finished.values()), finished


# --- Acceptance 11: MCP kanban_suggest_next ----------------------------------------------------------


def _mcp_next(root: Path) -> Any:
    from yurtle_kanban.mcp.server import KanbanMCPServer

    result = KanbanMCPServer(repo_root=root).handle_tool_call("kanban_suggest_next", {})
    assert "error" not in result, result
    suggestion = result["suggestion"]
    return None if suggestion is None else suggestion["id"]


def test_a11_mcp_matches_next_on_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = make_repo(tmp_path, monkeypatch, ORDERING)
    assert _mcp_next(repo.root) == next_json()["id"] == "EXP-7"


def test_a11_mcp_matches_next_on_the_graph(graph: Repo) -> None:
    assert _mcp_next(graph.root) == next_json()["id"] == PICKABLE_UNASSIGNED[0]


def test_a11_mcp_matches_next_resume(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _own_work(tmp_path, monkeypatch, with_in_progress=True)
    monkeypatch.setenv("YURTLE_AGENT", A)
    assert _mcp_next(repo.root) == next_json()["id"] == "EXP-3"


def test_a11_mcp_nothing_pickable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = make_repo(tmp_path, monkeypatch, {
        "EXP-1": {"status": "ready", "deps": ("EXP-2",)},
        "EXP-2": {"status": "backlog"},
    })
    assert _mcp_next(repo.root) is None
    assert invoke(["next", "--json"]).exit_code == 7
