"""#295: the last hdd metadata lines print repo-derived text through `_click.safe()`.

Follow-up from the review of PR #294 (#285). These lines still used plain `escape()`,
which escapes Rich markup only, so ESC, newline and bidi characters from frontmatter or
a repo path reached the terminal raw:

1. `hdd critical-path --dev-blockers` (`_render_dev_blockers`): the `Status:` value, on
   the same line as the `Assignee:` value fixed in #285 (which had no test; pinned here).
2. `hdd critical-path`: the `Runs:` line (last outcome / last run status).
3. `experiment run`: the `Path:` line. The run folder is created by the real service
   under a repo whose directory NAME carries ESC + newline.

Each is captured through every module console swapped for a
`Console(force_terminal=True)`; each assertion looks at the one line under test.
Controls: printable output is unchanged, and `[bold]` in a value is still literal.
"""

from __future__ import annotations

import io
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from rich.console import Console

from yurtle_kanban import cli, epic_commands, hdd_commands
from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import main
from yurtle_kanban.service import KanbanService

EVIL = "X\x1b[2J\nFORGED"
ESCAPED = "X\\x1b[2J\\nFORGED"  # what `safe()` shows for EVIL
BIDI = "a‮b"
BIDI_ESCAPED = "a\\u202eb"

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


def _run(repo: Path, args: list[str], monkeypatch: pytest.MonkeyPatch) -> str:
    """Run with every module console forced to a terminal; return what Rich wrote."""
    buf = io.StringIO()
    tty = Console(file=buf, force_terminal=True, width=400)
    for mod in (cli, hdd_commands, epic_commands):
        monkeypatch.setattr(mod, "console", tty)
    monkeypatch.chdir(repo)
    result = CliRunner().invoke(main, args)
    assert result.exception is None or isinstance(result.exception, SystemExit), (
        result.output,
        result.exception,
    )
    return buf.getvalue()


def _plain(out: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", out)  # Rich's own colour codes


def _line(out: str, needle: str) -> str:
    """The one output line that starts (after indent) with `needle`."""
    lines = [ln for ln in _plain(out).splitlines() if ln.lstrip().startswith(needle)]
    assert lines, f"expected line missing: {needle!r} in {out!r}"
    assert len(lines) == 1, f"{needle!r} printed {len(lines)} times: {out!r}"
    return lines[0]


def _assert_all_escaped(out: str, needle: str, escaped: str = ESCAPED) -> None:
    line = _line(out, needle)
    assert "\x1b" not in line, f"raw ESC on the {needle!r} line: {line!r}"
    assert "‮" not in line, f"raw bidi override on the {needle!r} line: {line!r}"
    assert escaped in line, f"{needle!r} line doesn't show the escaped text: {line!r}"
    plain = _plain(out)
    assert "\x1b" not in plain, f"raw ESC reached the terminal: {out!r}"
    assert not any(ln.startswith("FORGED") for ln in plain.splitlines()), repr(out)


def _critical_path(
    repo: Path, monkeypatch: pytest.MonkeyPatch, rows: list[dict[str, Any]], *extra: str
) -> str:
    def get_critical_path(self: KanbanService, **kwargs: Any) -> list[dict[str, Any]]:
        return rows

    monkeypatch.setattr(KanbanService, "get_critical_path", get_critical_path)
    return _run(repo, ["hdd", "critical-path", *extra], monkeypatch)


# --- 1. hdd critical-path --dev-blockers: Status / Assignee ---------------------------


def _blocker(**over: Any) -> dict[str, Any]:
    b: dict[str, Any] = {
        "expedition_id": "EXP-001",
        "title": "t",
        "status": "in_progress",
        "assignee": "Mini",
        "impact": 1,
        "unblocks_experiments": ["EXPR-001"],
    }
    b.update(over)
    return b


def _blockers(repo: Path, monkeypatch: pytest.MonkeyPatch, b: dict[str, Any]) -> str:
    return _critical_path(repo, monkeypatch, [b], "--dev-blockers")


@pytest.mark.parametrize(
    ("over", "escaped"),
    [
        ({"status": EVIL}, ESCAPED),
        ({"status": BIDI}, BIDI_ESCAPED),
        ({"assignee": EVIL}, ESCAPED),
        ({"assignee": BIDI}, BIDI_ESCAPED),
    ],
    ids=["status-esc", "status-bidi", "assignee-esc", "assignee-bidi"],
)
def test_dev_blockers_status_line_is_escaped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, over: dict[str, Any], escaped: str
) -> None:
    repo = _repo(tmp_path)
    out = _blockers(repo, monkeypatch, _blocker(**over))
    _assert_all_escaped(out, "Status:", escaped)


# --- 2. hdd critical-path: Runs -------------------------------------------------------


def _experiment(**over: Any) -> dict[str, Any]:
    exp: dict[str, Any] = {
        "experiment_id": "EXPR-001",
        "title": "t",
        "readiness": "training_complete",
        "hypothesis_id": "H1.1",
        "paper_id": "PAPER-1",
        "implements": [],
        "implements_status": {},
        "assignee": "Mini",
        "runs": 2,
        "downstream_impact": 0,
    }
    exp.update(over)
    return exp


@pytest.mark.parametrize(
    ("over", "escaped"),
    [
        ({"last_outcome": EVIL}, ESCAPED),
        ({"last_run_status": EVIL}, ESCAPED),
        ({"last_outcome": BIDI}, BIDI_ESCAPED),
    ],
    ids=["last-outcome", "last-run-status", "last-outcome-bidi"],
)
def test_critical_path_runs_line_is_escaped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, over: dict[str, Any], escaped: str
) -> None:
    repo = _repo(tmp_path)
    out = _critical_path(repo, monkeypatch, [_experiment(**over)])
    _assert_all_escaped(out, "Runs:", escaped)


# --- 3. experiment run: Path ----------------------------------------------------------


def test_experiment_run_path_line_is_escaped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real run folder, created by the real service under a repo directory whose
    name carries ESC + newline (macOS and Linux both allow it)."""
    repo = _repo(tmp_path / ("r" + EVIL))
    out = _run(repo, ["experiment", "run", "EXPR-001", "--being", "b"], monkeypatch)
    assert "Created run for EXPR-001" in _plain(out), repr(out)
    assert list((repo / "research" / "runs" / "EXPR-001").iterdir()), repr(out)
    _assert_all_escaped(out, "Path:")


# --- controls -------------------------------------------------------------------------


def test_printable_dev_blockers_output_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    out = _plain(_blockers(repo, monkeypatch, _blocker()))
    assert "     Status: in_progress  Assignee: Mini\n" in out, repr(out)
    out = _plain(_blockers(repo, monkeypatch, _blocker(assignee=None)))
    assert "     Status: in_progress  Assignee: unassigned\n" in out, repr(out)


def test_printable_runs_line_is_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo(tmp_path)
    out = _plain(_critical_path(repo, monkeypatch, [_experiment(last_outcome="win")]))
    assert "    Runs: 2 run(s), last: win\n" in out, repr(out)
    out = _plain(_critical_path(repo, monkeypatch, [_experiment(last_run_status="ok")]))
    assert "    Runs: 2 run(s), last: ok\n" in out, repr(out)
    out = _plain(_critical_path(repo, monkeypatch, [_experiment()]))
    assert "    Runs: 2 run(s)\n" in out, repr(out)


def test_printable_experiment_run_path_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path / "repo")
    out = _plain(_run(repo, ["experiment", "run", "EXPR-001", "--being", "b"], monkeypatch))
    line = _line(out, "Path:")
    assert line.startswith("  Path: "), repr(line)
    path = Path(line.removeprefix("  Path: "))
    assert path.is_dir() and path.parent.name == "EXPR-001", repr(line)
    assert (path / "config.yaml").exists(), repr(line)


def test_markup_in_values_is_still_literal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo(tmp_path)
    out = _plain(_blockers(repo, monkeypatch, _blocker(status="[bold]s", assignee="[b]x")))
    assert "     Status: [bold]s  Assignee: [b]x\n" in out, repr(out)
    out = _plain(_critical_path(repo, monkeypatch, [_experiment(last_outcome="[bold]w[/bold]")]))
    assert "    Runs: 2 run(s), last: [bold]w[/bold]\n" in out, repr(out)
