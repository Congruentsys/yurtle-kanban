"""#586: `move` validates the PROPOSED item, not the item as it was.

`move_item` evaluated workflow rules and transition gates against the item
before it applied the new assignee, so `move X in_progress --assign Mini` on an
unassigned item was refused by the very check `--assign` satisfies:

- the gate from tests/test_gates.py (`* -> in_progress: check item.assignee`)
  said "Assignee required (use --assign)";
- the example workflow rule "in_progress requires an assignee"
  (examples/.kanban/workflows/feature.yurtle.md, `item.assignee is not None`)
  said "Must have an assignee before starting work".

Decided behaviour: build the proposed item (new status and assignee) first,
then run workflow rules, gates and WIP limits against it. A move with no
assignee is still refused. A refused move writes nothing: the file is byte-for-
byte unchanged (no status, no assignee, no history entry), and the service's
own view of the item is unchanged too. WIP limits still count the target column
correctly: the moving item is not counted against itself, and a full column
still refuses.

Resolution / superseded_by: `move_item` has no `resolution` argument and `move`
no `--resolution` flag yet; that arrives with #581, which must apply them to the
proposed item the same way. Not covered here.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from click.testing import CliRunner

from yurtle_kanban.cli import main
from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.models import WorkItemStatus
from yurtle_kanban.service import KanbanService

REPO = Path(__file__).resolve().parents[2]
FEATURE_WORKFLOW = REPO / "examples" / ".kanban" / "workflows" / "feature.yurtle.md"

GATE_CONFIG = """
version: "2.0"
boards:
  - name: development
    preset: nautical
    path: work/
{wip}    gates:
      "* -> in_progress":
        - id: require_assignee
          check: item.assignee
          message: "Assignee required (use --assign)"
default_board: development
"""

WORKFLOW_CONFIG = """
kanban:
  theme: software
  paths:
    root: work/
"""


def _expedition(item_id: str, status: str = "ready", assignee: str | None = None) -> str:
    who = f"assignee: {assignee}\n" if assignee else ""
    return (
        f"---\nid: {item_id}\ntitle: \"Expedition {item_id}\"\ntype: expedition\n"
        f"status: {status}\n{who}---\n# Expedition {item_id}\n\nA description long enough.\n"
    )


def _feature(item_id: str, status: str = "ready") -> str:
    return (
        f"---\nid: {item_id}\ntitle: \"Feature {item_id}\"\ntype: feature\n"
        f"status: {status}\n---\n# Feature {item_id}\n\nA description long enough.\n"
    )


@pytest.fixture
def gate_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    """Nautical board with the `* -> in_progress: item.assignee` gate; EXP-100 unassigned."""
    cfg = tmp_path / ".kanban" / "config.yaml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(GATE_CONFIG.format(wip=""))
    (tmp_path / "work" / "expeditions").mkdir(parents=True)
    item = tmp_path / "work" / "expeditions" / "EXP-100.md"
    item.write_text(_expedition("EXP-100"))
    monkeypatch.chdir(tmp_path)
    return {"tmp_path": tmp_path, "cfg": cfg, "item": item}


@pytest.fixture
def workflow_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    """Software board with the example feature workflow installed; FEAT-001 unassigned."""
    cfg = tmp_path / ".kanban" / "config.yaml"
    (tmp_path / ".kanban" / "workflows").mkdir(parents=True)
    cfg.write_text(WORKFLOW_CONFIG)
    shutil.copy(FEATURE_WORKFLOW, tmp_path / ".kanban" / "workflows" / "feature.yurtle.md")
    (tmp_path / "work").mkdir()
    item = tmp_path / "work" / "FEAT-001.md"
    item.write_text(_feature("FEAT-001"))
    monkeypatch.chdir(tmp_path)
    return {"tmp_path": tmp_path, "cfg": cfg, "item": item}


def _service(repo: dict) -> KanbanService:
    return KanbanService(KanbanConfig.load(repo["cfg"]), repo["tmp_path"])


def _proposed_move(svc: KanbanService, item_id: str, assignee: str):
    """move_item to in_progress with an assignee; a refusal is an assertion failure."""
    try:
        return svc.move_item(
            item_id, WorkItemStatus.IN_PROGRESS, commit=False, assignee=assignee
        )
    except ValueError as e:
        pytest.fail(f"move with assignee={assignee!r} refused: {e}")


def _move(*args: str):
    return CliRunner().invoke(main, ["move", *args, "--no-commit"])


# ── 1. the gate ────────────────────────────────────────────────────────


class TestAssigneeGate:
    def test_move_with_assign_passes_the_gate(self, gate_repo: dict) -> None:
        result = _move("EXP-100", "in_progress", "--assign", "Mini")
        assert result.exit_code == 0, result.output
        assert "Assignee required" not in result.output
        item = _service(gate_repo).get_item("EXP-100")
        assert item is not None
        assert item.status == WorkItemStatus.IN_PROGRESS
        assert item.assignee == "Mini"

    def test_service_move_with_assignee_passes_the_gate(self, gate_repo: dict) -> None:
        moved = _proposed_move(_service(gate_repo), "EXP-100", "Mini")
        assert moved.status == WorkItemStatus.IN_PROGRESS
        assert moved.assignee == "Mini"

    def test_move_without_assignee_is_still_refused(self, gate_repo: dict) -> None:
        result = _move("EXP-100", "in_progress")
        assert result.exit_code == 1, result.output
        assert "Assignee required (use --assign)" in result.output

    def test_existing_assignee_still_passes(self, gate_repo: dict) -> None:
        """Control: an item already assigned passes with no --assign."""
        gate_repo["item"].write_text(_expedition("EXP-100", assignee="M5"))
        result = _move("EXP-100", "in_progress")
        assert result.exit_code == 0, result.output


# ── 2. the workflow rule ───────────────────────────────────────────────


class TestAssigneeWorkflowRule:
    def test_move_with_assign_passes_the_rule(self, workflow_repo: dict) -> None:
        result = _move("FEAT-001", "in_progress", "--assign", "Mini")
        assert result.exit_code == 0, result.output
        assert "Must have an assignee" not in result.output
        item = _service(workflow_repo).get_item("FEAT-001")
        assert item is not None
        assert item.status == WorkItemStatus.IN_PROGRESS
        assert item.assignee == "Mini"

    def test_service_move_with_assignee_passes_the_rule(self, workflow_repo: dict) -> None:
        moved = _proposed_move(_service(workflow_repo), "FEAT-001", "Mini")
        assert moved.status == WorkItemStatus.IN_PROGRESS
        assert moved.assignee == "Mini"

    def test_move_without_assignee_is_still_refused(self, workflow_repo: dict) -> None:
        result = _move("FEAT-001", "in_progress")
        assert result.exit_code == 1, result.output
        assert "Must have an assignee before starting work" in result.output

    def test_illegal_transition_is_still_refused_with_assign(self, workflow_repo: dict) -> None:
        """--assign satisfies the rule, never the state machine: backlog -> in_progress."""
        workflow_repo["item"].write_text(_feature("FEAT-001", status="backlog"))
        result = _move("FEAT-001", "in_progress", "--assign", "Mini")
        assert result.exit_code == 1, result.output
        assert "Illegal move" in result.output


# ── 4. a refused move writes nothing ───────────────────────────────────


class TestRefusedMoveWritesNothing:
    @pytest.mark.parametrize("fixture", ["gate_repo", "workflow_repo"])
    def test_file_unchanged_after_refusal(self, fixture: str, request) -> None:
        repo = request.getfixturevalue(fixture)
        before = repo["item"].read_bytes()
        item_id = repo["item"].stem
        result = _move(item_id, "in_progress")
        assert result.exit_code == 1, result.output
        assert repo["item"].read_bytes() == before

    def test_refused_by_wip_leaves_service_view_unchanged(self, gate_repo: dict) -> None:
        """A refused move must not leave a half-built proposal on the cached item."""
        gate_repo["cfg"].write_text(
            GATE_CONFIG.format(wip="    wip_limits:\n      in_progress: 1\n")
        )
        (gate_repo["tmp_path"] / "work" / "expeditions" / "EXP-101.md").write_text(
            _expedition("EXP-101", status="in_progress", assignee="M5")
        )
        before = gate_repo["item"].read_bytes()
        svc = _service(gate_repo)
        with pytest.raises(ValueError, match="WIP limit reached"):
            svc.move_item("EXP-100", WorkItemStatus.IN_PROGRESS, commit=False, assignee="Mini")
        item = svc.get_item("EXP-100")
        assert item is not None
        assert item.status == WorkItemStatus.READY
        assert item.assignee is None
        assert gate_repo["item"].read_bytes() == before

    def test_refused_by_rule_leaves_service_view_unchanged(self, workflow_repo: dict) -> None:
        workflow_repo["item"].write_text(_feature("FEAT-001", status="backlog"))
        before = workflow_repo["item"].read_bytes()
        svc = _service(workflow_repo)
        with pytest.raises(ValueError, match="Illegal move"):
            svc.move_item("FEAT-001", WorkItemStatus.IN_PROGRESS, commit=False, assignee="Mini")
        item = svc.get_item("FEAT-001")
        assert item is not None
        assert item.status == WorkItemStatus.BACKLOG
        assert item.assignee is None
        assert workflow_repo["item"].read_bytes() == before


# ── 5. WIP counts the proposed state ───────────────────────────────────


class TestWipWithProposedItem:
    @pytest.fixture
    def wip_repo(self, gate_repo: dict) -> dict:
        gate_repo["cfg"].write_text(
            GATE_CONFIG.format(wip="    wip_limits:\n      in_progress: 2\n")
        )
        (gate_repo["tmp_path"] / "work" / "expeditions" / "EXP-101.md").write_text(
            _expedition("EXP-101", status="in_progress", assignee="M5")
        )
        return gate_repo

    def test_one_slot_left_with_assign_succeeds(self, wip_repo: dict) -> None:
        """1/2 in progress: the moving item is not counted against itself."""
        result = _move("EXP-100", "in_progress", "--assign", "Mini")
        assert result.exit_code == 0, result.output

    def test_full_column_still_refuses_with_assign(self, wip_repo: dict) -> None:
        (wip_repo["tmp_path"] / "work" / "expeditions" / "EXP-102.md").write_text(
            _expedition("EXP-102", status="in_progress", assignee="DGX")
        )
        before = wip_repo["item"].read_bytes()
        result = _move("EXP-100", "in_progress", "--assign", "Mini")
        assert result.exit_code == 1, result.output
        assert "WIP limit reached" in result.output
        assert wip_repo["item"].read_bytes() == before

    def test_fill_then_refuse_in_one_service(self, wip_repo: dict) -> None:
        """Two moves through one service: the second sees the first's item in the column."""
        (wip_repo["tmp_path"] / "work" / "expeditions" / "EXP-102.md").write_text(
            _expedition("EXP-102")
        )
        svc = _service(wip_repo)
        _proposed_move(svc, "EXP-100", "Mini")
        with pytest.raises(ValueError, match="WIP limit reached"):
            svc.move_item("EXP-102", WorkItemStatus.IN_PROGRESS, commit=False, assignee="Mini")
