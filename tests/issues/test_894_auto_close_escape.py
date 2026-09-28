# ruff: noqa: F811  -- the `world` fixture imported from the claim tests is re-bound as an arg
"""Issue #894: the auto-close warning escapes the move's output as workflow-command data.

The "Move items" step of ``.github/workflows/kanban-auto-close.yml`` reports a failed
move as ``::warning::Could not move <id> to <status>: <output>``. GitHub decodes
``%25``, ``%0D`` and ``%0A`` in a workflow command's message, so an unescaped ``%``
in the output can come out changed. The #860 fold (``tr '\\n' ' '``) also leaves a
trailing space.

The [steer] on #894 decides: the output is escaped per GitHub's workflow-command
rules (``%`` -> ``%25`` first, then CR -> ``%0D``, LF -> ``%0A``), so the whole
multi-line reason survives in exactly one annotation line with no trailing space.

The tests run the step's own ``run`` script (the #860 harness: taken from the YAML,
run by ``bash -e``). The failing move uses a stub ``yurtle-kanban`` placed first on
PATH that prints a crafted message and exits 1: the tests pin the step's escaping,
not the CLI's wording.

Ambiguities resolved here (the test partner's reading; the driver may challenge):

a. "The original output" is what ``$(...)`` captures: the stub's text, which ends
   without a newline, so command substitution strips nothing.
b. The step's log is read as bytes and split on LF only, so a CR in the output is
   not taken for a line break by the test (the runner splits on LF).
c. The decoded message must END with the original output; the step's own prefix
   ("Could not move ... : ") is not pinned.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.issues.test_574_claim import ITEM, ITEM_ID, item_text, push_from_a
from tests.issues.test_860_auto_close_take_over import (
    PR_URL,
    REPO,
    VENV_BIN,
    move_step_script,
    run_move_step,
)

# `world` is tests/issues/conftest.py's; its env setup is requested, as in test_860 (#892)
pytestmark = pytest.mark.usefixtures("claim_env")

# (no single quote: the stub single-quotes it)
# a refusal with a literal %, already-encoded-looking %0A / %25 / %0D, a CRLF, and a
# trailing % -- every case the escaping must round-trip
REFUSAL = (
    "Error: EXP-001 is 100% held by agent-A\n"
    "literal %0A and %25 and %0D stay literal\r\n"
    "  indented line ending in %"
)
STUB = f"""#!/bin/sh
printf '%s' '{REFUSAL}'
exit 1
"""


def escape(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def unescape(text: str) -> str:
    return text.replace("%0A", "\n").replace("%0D", "\r").replace("%25", "%")


def run_with_stub(tmp_path: Path) -> tuple[int, bytes]:
    """Run the step with a failing stub `yurtle-kanban` first on PATH; return exit and log."""
    stub_dir = tmp_path / "stub"
    stub_dir.mkdir()
    stub = stub_dir / "yurtle-kanban"
    stub.write_text(STUB)
    stub.chmod(0o755)
    output_file = tmp_path / "github_output"
    output_file.write_text("")
    bin_dir = str(VENV_BIN if VENV_BIN.exists() else Path(sys.executable).parent)
    env = {k: v for k, v in os.environ.items() if k not in {"YURTLE_AGENT", "PYTHONPATH"}}
    env.update(
        PATH=f"{stub_dir}{os.pathsep}{bin_dir}{os.pathsep}{env.get('PATH', '')}",
        PYTHONPATH=str(REPO / "src"),
        ITEM_IDS=ITEM_ID,
        TARGET_STATUS="done",
        PR_URL=PR_URL,
        PR_NUMBER="42",
        GITHUB_OUTPUT=str(output_file),
    )
    script = tmp_path / "move_items.sh"
    script.write_text(move_step_script())
    proc = subprocess.run(
        ["bash", "--noprofile", "--norc", "-e", str(script)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        timeout=60,
        check=False,
    )
    return proc.returncode, proc.stdout + b"\n" + proc.stderr


def test_stub_prints_the_refusal_verbatim(tmp_path) -> None:
    """Harness check: the stub's output, as `$(...)` captures it, is REFUSAL exactly."""
    stub = tmp_path / "yurtle-kanban"
    stub.write_text(STUB)
    stub.chmod(0o755)
    proc = subprocess.run([str(stub)], capture_output=True, check=False)
    assert proc.returncode == 1
    assert proc.stdout.decode() == REFUSAL


# --- 1. the failed move's warning escapes the output ------------------------------------------


def test_failed_move_warning_escapes_output_as_workflow_command_data(tmp_path) -> None:
    code, raw = run_with_stub(tmp_path)
    log = raw.decode()
    assert code == 0, f"a failed move must warn, not fail the job:\n{log}"

    warnings = [line for line in log.split("\n") if "::warning::" in line]
    assert len(warnings) == 1, f"expected exactly one ::warning:: line, got {warnings!r}"
    line = warnings[0]
    message = line.split("::warning::", 1)[1]

    assert escape(REFUSAL) in message, (
        f"the ::warning:: does not carry the escaped output {escape(REFUSAL)!r}; got {message!r}"
    )
    assert "100%25 held" in message, f"'%' was not escaped to '%25': {message!r}"
    assert "%25 held by agent-A%0Aliteral" in message, f"LF was not escaped to '%0A': {message!r}"
    assert "\r" not in message, f"CR was not escaped to '%0D': {message!r}"
    assert unescape(message).endswith(REFUSAL), (
        f"decoding the ::warning:: does not give back the output:\n"
        f"decoded={unescape(message)!r}\noriginal={REFUSAL!r}"
    )
    assert not line.endswith(" "), f"the ::warning:: line ends in a space: {line!r}"


# --- 2. control: a successful move warns nothing ----------------------------------------------


def test_control_successful_move_emits_no_warning(world, tmp_path) -> None:
    push_from_a(world, {ITEM: item_text("review")}, "EXP-001 review")

    proc, outputs = run_move_step(world.a, tmp_path, ITEM_ID)
    log = f"{proc.stdout}\n{proc.stderr}"

    assert proc.returncode == 0, log
    assert outputs.get("moved") == "1", f"expected moved=1, got {outputs}:\n{log}"
    assert "::warning::" not in log, f"a successful move emitted a warning:\n{log}"
