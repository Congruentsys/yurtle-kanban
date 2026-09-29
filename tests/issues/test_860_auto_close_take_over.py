# ruff: noqa: F811  (the borrowed `bash` fixture)
"""Issue #860: the auto-close workflow moves a held item and reports why a move fails.

When a PR merges, ``.github/workflows/kanban-auto-close.yml`` moves the linked items
to the target status. Its "Move items" step runs ``yurtle-kanban move ... --no-commit
--assign "github-actions[bot]" --closed-by "$PR_URL" 2>/dev/null``. Since #574 a move
of an item someone else holds in progress is refused without ``--take-over``, so the
merge of a held item's PR leaves it in progress, and the ``::warning::`` cannot say
why, because stderr went to /dev/null.

The [steer] on #860 decides:

- The move line uses ``--take-over --agent "github-actions[bot]"``: the merge is the
  authority and the bot is the actor. ``--take-over`` records the holder
  (``kb:takenOverFrom``), so the history keeps who held the item. It keeps
  ``--no-commit``, ``--assign "github-actions[bot]"`` and ``--closed-by "$PR_URL"``.
- stderr is not discarded: the ``::warning::`` carries the refusal's reason.

Every test here runs the "Move items" step's own ``run`` script, taken from the YAML
with ``yaml.safe_load`` and executed by ``bash -e`` (the shell GitHub Actions uses),
in a scratch clone with ``yurtle-kanban`` on PATH from this checkout. Nothing about
the command line is hard-coded, so the tests pin the real workflow.

Ambiguities resolved here (the test partner's reading; the driver may challenge):

a. "Held" is ``in_progress`` with ``assignee: agent-A``, seeded as the #574/#823
   tests seed it (``seed`` from ``test_574_claim``): the state ``move`` refuses
   without ``--take-over``.
b. "The history records the take-over" is exactly one status-history node carrying
   ``kb:takenOverFrom "agent-A"``, and it also carries ``kb:closedBy <PR_URL>``. Its ``kb:by`` actor is not pinned here (the
   steer says the bot; the #823 tests pin the take-over mechanics).
c. The refusal text is matched case-insensitively as "not found" on the SAME output
   line as ``::warning::``, since a GitHub annotation is one line. The step output
   is stdout and stderr combined, as a runner log shows it.
d. The step must exit 0 even when a move fails: auto-close warns, it does not fail
   the job.
e. The item's status after the step is read from its frontmatter and resolved to
   canonical form, so ``done`` or the theme's native name both pass.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.issues._bashes import bash  # noqa: F401  (fixture: each bash, #940, #981)
from tests.issues.test_574_claim import (
    ITEM,
    ITEM_ID,
    A,
    frontmatter,
    item_text,
    push_from_a,
    seed,
    service,
)
from tests.issues.test_585_create_push_loop import World
from yurtle_kanban.models import WorkItemStatus

# the #574 claim tests' clean env (tests/issues/conftest.py), as they have it
pytestmark = pytest.mark.usefixtures("claim_env")

REPO = Path(__file__).resolve().parents[2]
WORKFLOW = REPO / ".github" / "workflows" / "kanban-auto-close.yml"
VENV_BIN = REPO / ".venv" / "bin"
PR_URL = "https://github.com/Congruentsys/example/pull/42"
TAKEN = re.compile(r'kb:takenOverFrom\s+"((?:[^"\\]|\\.)*)"')


# --- harness ------------------------------------------------------------------------------


def move_step_script() -> str:
    doc = yaml.safe_load(WORKFLOW.read_text())
    steps = doc["jobs"]["auto-close"]["steps"]
    step = next(s for s in steps if s.get("name") == "Move items")
    return str(step["run"])


def run_move_step(
    clone: Path, tmp_path: Path, ids: str, bash: str
) -> tuple[Any, dict[str, str]]:
    """Run the step's script in `clone`; return the process and its GITHUB_OUTPUT."""
    output_file = tmp_path / "github_output"
    output_file.write_text("")
    bin_dir = str(VENV_BIN if VENV_BIN.exists() else Path(sys.executable).parent)
    env = {k: v for k, v in os.environ.items() if k not in {"YURTLE_AGENT", "PYTHONPATH"}}
    env.update(
        PATH=f"{bin_dir}{os.pathsep}{env.get('PATH', '')}",
        PYTHONPATH=str(REPO / "src"),
        ITEM_IDS=ids,
        TARGET_STATUS="done",
        PR_URL=PR_URL,
        PR_NUMBER="42",
        GITHUB_OUTPUT=str(output_file),
    )
    # GitHub Actions runs `run:` with `bash -e {0}` (plus --noprofile --norc)
    script = tmp_path / "move_items.sh"
    script.write_text(move_step_script())
    proc = subprocess.run(
        [bash, "--noprofile", "--norc", "-e", str(script)],  # each bash (#940, #981)
        cwd=clone,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    outputs: dict[str, str] = {}
    for line in output_file.read_text().splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            outputs[key] = value
    return proc, outputs


def log_of(proc: Any) -> str:
    return f"{proc.stdout}\n{proc.stderr}"


def status_of(world: World) -> WorkItemStatus | None:
    svc = service(world.a)
    item = svc.get_item(ITEM_ID)
    assert item is not None
    return svc.resolve_status_name(item, str(frontmatter((world.a / ITEM).read_text())["status"]))


def status_nodes(text: str) -> list[str]:
    """The item's status-history blank nodes. Not ``history_nodes`` from #574: its
    ``[(.*?)]`` stops at the ``]`` of ``github-actions[bot]``, so a node is taken
    from a ``[`` that ends its line to a line that is only ``]`` plus punctuation."""
    blocks = re.findall(r"```yurtle\n(.*?)```", text, re.S)
    return [
        node
        for block in blocks
        for node in re.findall(r"\[[ \t]*\n(.*?)^[ \t]*\][ \t]*[.;,]?[ \t]*$", block, re.S | re.M)
    ]


def item_file(world: World) -> str:
    return (world.a / ITEM).read_text()


# --- 1. a held in-progress item is moved, and the take-over is recorded ------------------------


def test_held_in_progress_item_is_moved_to_done_with_take_over(world, tmp_path, bash) -> None:
    seed(world, "in_progress", A)

    proc, outputs = run_move_step(world.a, tmp_path, ITEM_ID, bash)
    log = log_of(proc)

    assert proc.returncode == 0, f"the step failed (exit {proc.returncode}):\n{log}"
    assert outputs.get("moved") == "1", f"expected moved=1, got {outputs}:\n{log}"
    assert status_of(world) is WorkItemStatus.DONE, (
        f"{ITEM_ID} held by {A} was not moved to done:\n{log}\n{item_file(world)}"
    )
    nodes = [n for n in status_nodes(item_file(world)) if TAKEN.search(n)]
    taken = [TAKEN.search(n).group(1) for n in nodes]  # type: ignore[union-attr]
    assert all(f"kb:closedBy <{PR_URL}>" in n for n in nodes), (
        f"the take-over node does not carry kb:closedBy <{PR_URL}>:\n{item_file(world)}"
    )
    assert taken == [A], (
        f"expected one history node with kb:takenOverFrom {A!r}, got {taken}:\n{item_file(world)}"
    )


# --- 2. a failed move's warning carries the refusal's reason ----------------------------------


def test_failed_move_warning_includes_refusal_reason(world, tmp_path, bash) -> None:
    seed(world, "in_progress", A)

    proc, outputs = run_move_step(world.a, tmp_path, "EXP-999", bash)
    log = log_of(proc)

    assert proc.returncode == 0, f"a failed move must warn, not fail the job:\n{log}"
    assert outputs.get("moved") == "0", f"expected moved=0, got {outputs}:\n{log}"
    warnings = [line for line in log.splitlines() if "::warning::" in line]
    assert warnings, f"no ::warning:: for the failed move:\n{log}"
    assert any("not found" in line.lower() for line in warnings), (
        f"the ::warning:: does not carry the refusal's reason ('not found'); "
        f"stderr is still discarded:\n{log}"
    )


# --- 3. controls (pass on the current workflow) ------------------------------------------------


def test_control_unheld_review_item_moves_to_done(world, tmp_path, bash) -> None:
    push_from_a(world, {ITEM: item_text("review")}, "EXP-001 review")

    proc, outputs = run_move_step(world.a, tmp_path, ITEM_ID, bash)
    log = log_of(proc)

    assert proc.returncode == 0, log
    assert outputs.get("moved") == "1", f"expected moved=1, got {outputs}:\n{log}"
    assert status_of(world) is WorkItemStatus.DONE, f"{log}\n{item_file(world)}"


def test_control_no_ids_moves_nothing(world, tmp_path, bash) -> None:
    before = item_file(world)

    proc, outputs = run_move_step(world.a, tmp_path, "", bash)

    assert proc.returncode == 0, log_of(proc)
    assert outputs.get("moved") == "0", f"expected moved=0, got {outputs}"
    assert item_file(world) == before


@pytest.mark.parametrize(
    "flag", ["--no-commit", '--assign "github-actions[bot]"', '--closed-by "$PR_URL"']
)
def test_control_move_line_keeps_its_flags(flag: str) -> None:
    assert flag in move_step_script(), f"the move line lost {flag}"
