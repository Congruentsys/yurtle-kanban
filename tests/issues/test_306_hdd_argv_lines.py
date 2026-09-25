"""#306: hdd lines that echo a command-line value print it through `_click.safe()`.

Follow-up from the review of PR #304 (#295). These lines used plain `escape()`, which
escapes Rich markup only, so ESC / newline in an argv value reached the terminal raw:

1. `experiment run`: `Created run for {expr_id}`.
2. `experiment status`: the dim `{expr_id}: {title}` line (no runs) and the runs table
   title (`{expr_id}: {title}`).
3. `hdd critical-path --agent`: `No experiments found for agent '{agent}'` and the
   `HDD Critical Path (Agent: {agent})` title.
4. `hdd registry --output`: `Registry written to {out}`.

The commands are invoked on the `hdd` / `experiment` subgroups directly with CliRunner
(as tests/issues/test_239 does), so the root argv guard is out of the way. ESC is
valid UTF-8, so that guard would let EVIL through anyway.

The service validates an experiment id (`[A-Za-z]+-[A-Za-z0-9]+...`) and refuses one
with ESC before anything is echoed, so for (1) and (2) the service calls are
monkeypatched to accept EVIL: `create_experiment_run` returns a real folder,
`get_experiment_runs` returns [] or one run, and `get_item` returns an item with a
printable title, so only the argv value is under test. `hdd critical-path` has
`get_critical_path` monkeypatched as in test_295; `hdd registry` runs for real.

Output is captured through every module console swapped for a
`Console(force_terminal=True)`. Controls: printable output is unchanged, and `[bold]`
in a value is still literal.
"""

from __future__ import annotations

import io
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import click
import pytest
from click.testing import CliRunner
from rich.console import Console

from yurtle_kanban import cli, epic_commands, hdd_commands
from yurtle_kanban import config as config_mod
from yurtle_kanban.hdd_commands import experiment, hdd
from yurtle_kanban.service import KanbanService

EVIL = "X\x1b[2J\nFORGED"
ESCAPED = "X\\x1b[2J\\nFORGED"  # what `safe()` shows for EVIL
EVIL_ID = "EXPR-" + EVIL
ESCAPED_ID = "EXPR-" + ESCAPED

HDD = (
    "kanban:\n  theme: hdd\n  paths:\n    root: research/\n    scan_paths:\n"
    "      - research/papers/\n      - research/hypotheses/\n"
)

# --- helpers --------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)


def _repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "t@t.com")
    _git(root, "config", "user.name", "T")
    (root / ".kanban").mkdir()
    (root / ".kanban" / "config.yaml").write_text(HDD)
    return root


@pytest.fixture(autouse=True)
def _clean() -> None:
    config_mod._theme_cache.clear()


def _run(
    repo: Path, group: click.Group, args: list[str], monkeypatch: pytest.MonkeyPatch
) -> str:
    """Invoke a subgroup (no root argv guard) with every module console forced to a
    terminal; return what Rich wrote."""
    buf = io.StringIO()
    tty = Console(file=buf, force_terminal=True, width=400)
    for mod in (cli, hdd_commands, epic_commands):
        monkeypatch.setattr(mod, "console", tty)
    monkeypatch.chdir(repo)
    result = CliRunner().invoke(group, args)
    assert result.exception is None or isinstance(result.exception, SystemExit), (
        result.output,
        result.exception,
    )
    assert result.exit_code == 0, (result.output, buf.getvalue())
    return buf.getvalue()


def _plain(out: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", out)  # Rich's own colour codes


def _line(out: str, needle: str) -> str:
    """The one output line that contains `needle`."""
    lines = [ln for ln in _plain(out).splitlines() if needle in ln]
    assert lines, f"expected line missing: {needle!r} in {out!r}"
    assert len(lines) == 1, f"{needle!r} printed {len(lines)} times: {out!r}"
    return lines[0]


def _assert_all_escaped(out: str, needle: str, escaped: str) -> None:
    plain = _plain(out)
    assert "\x1b" not in plain, f"raw ESC reached the terminal: {out!r}"
    assert not any(ln.lstrip().startswith("FORGED") for ln in plain.splitlines()), repr(out)
    line = _line(out, needle)
    assert escaped in line, f"{needle!r} line doesn't show the escaped text: {line!r}"


# --- service stubs --------------------------------------------------------------------


def _stub_run(monkeypatch: pytest.MonkeyPatch, run_dir: Path) -> None:
    def create_experiment_run(self: KanbanService, **kwargs: Any) -> Path:
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_dir

    monkeypatch.setattr(KanbanService, "create_experiment_run", create_experiment_run)


def _stub_status(monkeypatch: pytest.MonkeyPatch, runs: list[dict[str, Any]]) -> None:
    def get_experiment_runs(self: KanbanService, expr_id: str) -> list[dict[str, Any]]:
        return [dict(r) for r in runs]

    def get_item(self: KanbanService, item_id: str) -> Any:
        return SimpleNamespace(title="Title")

    monkeypatch.setattr(KanbanService, "get_experiment_runs", get_experiment_runs)
    monkeypatch.setattr(KanbanService, "get_item", get_item)


RUN = {
    "timestamp": "2026-09-25T10:00:00",
    "being": "b",
    "status": "complete",
    "outcome": "win",
    "run_path": Path("/nowhere"),
}


def _critical_path(
    repo: Path, monkeypatch: pytest.MonkeyPatch, rows: list[dict[str, Any]], *extra: str
) -> str:
    def get_critical_path(self: KanbanService, **kwargs: Any) -> list[dict[str, Any]]:
        return rows

    monkeypatch.setattr(KanbanService, "get_critical_path", get_critical_path)
    return _run(repo, hdd, ["critical-path", *extra], monkeypatch)


def _experiment() -> dict[str, Any]:
    return {
        "experiment_id": "EXPR-001",
        "title": "t",
        "readiness": "training_complete",
        "hypothesis_id": "H1.1",
        "paper_id": "PAPER-1",
        "implements": [],
        "implements_status": {},
        "assignee": "Mini",
        "runs": 0,
        "downstream_impact": 0,
    }


# --- 1. experiment run: Created run for {expr_id} -------------------------------------


def test_experiment_run_created_line_is_escaped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path / "repo")
    _stub_run(monkeypatch, repo / "research" / "runs" / "R" / "T")
    out = _run(repo, experiment, ["run", EVIL_ID, "--being", "b"], monkeypatch)
    _assert_all_escaped(out, "Created run for", ESCAPED_ID)


# --- 2. experiment status: dim line and table title ----------------------------------


def test_experiment_status_no_runs_line_is_escaped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    _stub_status(monkeypatch, [])
    out = _run(repo, experiment, ["status", EVIL_ID], monkeypatch)
    assert "No runs found." in out, repr(out)
    _assert_all_escaped(out, ": Title", ESCAPED_ID)


def test_experiment_status_table_title_is_escaped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    _stub_status(monkeypatch, [RUN])
    out = _run(repo, experiment, ["status", EVIL_ID], monkeypatch)
    assert "win" in out, repr(out)
    _assert_all_escaped(out, ": Title", ESCAPED_ID)


# --- 3. hdd critical-path --agent -----------------------------------------------------


def test_critical_path_no_experiments_for_agent_is_escaped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    out = _critical_path(repo, monkeypatch, [], "--agent", EVIL)
    _assert_all_escaped(out, "No experiments found for agent", ESCAPED)


def test_critical_path_agent_title_is_escaped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    out = _critical_path(repo, monkeypatch, [_experiment()], "--agent", EVIL)
    _assert_all_escaped(out, "HDD Critical Path (Agent:", ESCAPED)


# --- 4. hdd registry --output ---------------------------------------------------------


def test_registry_written_line_is_escaped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real registry file, written by the real command to a directory whose name
    carries ESC + newline (macOS and Linux both allow it)."""
    repo = _repo(tmp_path / "repo")
    out_file = tmp_path / ("o" + EVIL) / "REG.md"
    out = _run(repo, hdd, ["registry", "--output", str(out_file)], monkeypatch)
    assert out_file.read_text().startswith("# HDD Research Registry"), repr(out)
    _assert_all_escaped(out, "Registry written to", "o" + ESCAPED + "/REG.md")


# --- controls -------------------------------------------------------------------------


def test_printable_experiment_run_line_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path / "repo")
    _stub_run(monkeypatch, repo / "research" / "runs" / "R" / "T")
    out = _plain(_run(repo, experiment, ["run", "130", "--being", "b"], monkeypatch))
    assert "Created run for EXPR-130\n" in out, repr(out)
    out = _plain(_run(repo, experiment, ["run", "EXPR-[bold]x", "--being", "b"], monkeypatch))
    assert "Created run for EXPR-[bold]x\n" in out, repr(out)


def test_printable_experiment_status_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    _stub_status(monkeypatch, [])
    out = _plain(_run(repo, experiment, ["status", "130"], monkeypatch))
    assert "EXPR-130: Title\n  No runs found.\n" in out, repr(out)
    out = _plain(_run(repo, experiment, ["status", "EXPR-[b]x"], monkeypatch))
    assert "EXPR-[b]x: Title\n" in out, repr(out)
    _stub_status(monkeypatch, [RUN])
    out = _plain(_run(repo, experiment, ["status", "EXPR-130"], monkeypatch))
    assert _line(out, "EXPR-130: Title").strip() == "EXPR-130: Title", repr(out)


def test_printable_critical_path_agent_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    out = _plain(_critical_path(repo, monkeypatch, [], "--agent", "DGX"))
    assert "No experiments found for agent 'DGX'.\n" in out, repr(out)
    out = _plain(_critical_path(repo, monkeypatch, [_experiment()], "--agent", "DGX"))
    assert "HDD Critical Path (Agent: DGX)\n" in out, repr(out)
    out = _plain(_critical_path(repo, monkeypatch, [_experiment()], "--agent", "[b]x"))
    assert "HDD Critical Path (Agent: [b]x)\n" in out, repr(out)


def test_printable_registry_line_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path / "repo")
    out_file = tmp_path / "out[b]" / "REG.md"
    out = _plain(_run(repo, hdd, ["registry", "--output", str(out_file)], monkeypatch))
    assert f"Registry written to {out_file}\n" in out, repr(out)
    assert out_file.exists()
