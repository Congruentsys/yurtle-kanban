"""Issue #651 — `states` piped output wraps; dead second legality source.

The spec is the [steer] decision on the issue:

- Human `states` prints with `soft_wrap=True`, so output without a TTY (piped, or
  under click's CliRunner) keeps ONE line per state, however narrow the width.
- Delete `WorkflowParser.validate_transition` and
  `WorkflowConfig.get_allowed_transitions`; the `workflow` module docstring points at
  `KanbanService.legal_next` instead, leaving one legality source (G3).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner
from rich.console import Console

from yurtle_kanban import cli
from yurtle_kanban import config as config_mod
from yurtle_kanban import workflow as workflow_mod
from yurtle_kanban.cli import main
from yurtle_kanban.workflow import WorkflowConfig, WorkflowParser

from .test_573_states import CANONICAL, DEFAULT_TABLE, KINDS, _build, _label

NAUTICAL = KINDS["nautical"]


@pytest.fixture(scope="module")
def nautical(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _build(tmp_path_factory.mktemp("r651-nautical"), NAUTICAL)


def _display(root: Path, status: str) -> str:
    """How `states` names a canonical status: `native (canonical)` where they differ."""
    label = _label(root, NAUTICAL, status)
    return status if label == status else f"{label} ({status})"


def _expected_lines(root: Path) -> dict[str, str]:
    """canonical status -> the full `name → next1, next2` line (stripped)."""
    lines = {}
    for status in CANONICAL:
        nexts = ", ".join(_display(root, n) for n in DEFAULT_TABLE[status]) or "(terminal)"
        lines[status] = f"{_display(root, status)} → {nexts}"
    return lines


def _check_one_line_per_state(root: Path, text: str) -> None:
    expected = _expected_lines(root)
    # the premise: at least one line is longer than 60 columns, so wrapping would bite
    assert max(len(v) + 2 for v in expected.values()) > 60, expected
    lines = [ln for ln in text.splitlines() if ln.strip()]
    heading = [ln for ln in lines if ln.startswith("Board ")]
    assert len(heading) == 1, text
    body = [ln for ln in lines if not ln.startswith("Board ")]
    # every non-heading line is a state line: no bare continuation lines
    for ln in body:
        assert "→" in ln, f"bare continuation line {ln!r} in:\n{text}"
    for status, want in expected.items():
        left = _display(root, status)
        naming = [ln for ln in body if ln.partition("→")[0].strip() == left]
        assert len(naming) == 1, f"{left!r} on {len(naming)} lines:\n{text}"
        assert naming[0].strip() == want, f"want {want!r}, got {naming[0]!r}:\n{text}"
    assert len(body) == len(CANONICAL), text


def test_states_no_tty_narrow_one_line_per_state(
    nautical: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CliRunner (no TTY), COLUMNS=60, nautical: each state and its full next list
    on exactly one line."""
    monkeypatch.setenv("COLUMNS", "60")
    # the module console as it would be built in a COLUMNS=60, non-TTY process
    monkeypatch.setattr(cli, "console", Console())
    config_mod._theme_cache.clear()
    monkeypatch.chdir(nautical)
    try:
        result = CliRunner().invoke(main, ["states"], env={"COLUMNS": "60"})
    finally:
        config_mod._theme_cache.clear()
    assert result.exception is None or isinstance(result.exception, SystemExit), (
        repr(result.exception)
    )
    assert result.exit_code == 0, result.output
    text = result.output
    ready = _expected_lines(nautical)["ready"]
    assert ready.startswith("provisioning (ready) → ") and ready.endswith(
        ", stranded (blocked)"
    ), ready
    assert any(ln.strip() == ready for ln in text.splitlines()), (
        f"{ready!r} not on one line:\n{text}"
    )
    _check_one_line_per_state(nautical, text)


def test_states_piped_through_cat_one_line_per_state(nautical: Path) -> None:
    """A real process piped through `cat`, COLUMNS unset: rich falls back to 80
    columns, which must not wrap a state line either."""
    # import the source under test (the venv's console script may point elsewhere)
    src = Path(cli.__file__).resolve().parents[1]
    env = {k: v for k, v in os.environ.items() if k not in ("COLUMNS", "LINES")}
    env.pop("FORCE_COLOR", None)
    env.pop("TTY_COMPATIBLE", None)
    env["PYTHONPATH"] = os.pathsep.join([str(src), env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
    cmd = "from yurtle_kanban.cli import main; main()"
    proc = subprocess.run(
        f"'{sys.executable}' -c '{cmd}' states | cat",
        shell=True,
        cwd=nautical,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    _check_one_line_per_state(nautical, proc.stdout)


def test_workflow_parser_has_no_validate_transition() -> None:
    assert not hasattr(WorkflowParser, "validate_transition")


def test_workflow_config_has_no_get_allowed_transitions() -> None:
    assert not hasattr(WorkflowConfig, "get_allowed_transitions")


def test_workflow_docstring_does_not_recommend_validate_transition() -> None:
    doc = workflow_mod.__doc__ or ""
    assert "validate_transition" not in doc, doc
