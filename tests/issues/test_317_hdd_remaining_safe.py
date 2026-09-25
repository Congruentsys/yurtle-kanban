"""#317: the remaining hdd echo lines print argv / repo values through `_click.safe()`.

Follow-up from the review of PR #314 (#306). These lines still used plain `escape()`,
which escapes Rich markup only, so ESC / newline in the value reached the terminal raw:

1. The six `* create` commands' `Created {id}: {title}` line (hypothesis echoes its
   statement), in BOTH the `--push` branch (`result['id']`) and the normal branch
   (`item.id`): idea, literature, paper, hypothesis, experiment, measure.
   `experiment create` also echoes an argv `EXPR_ID`.
2. `experiment status`: the run-table cells (timestamp, being, status, outcome), which
   come from run files in the repo.
3. `hdd critical-path`: the experiment header row (id, title) and, with
   `--dev-blockers`, the blocker header row (expedition id, title) and the
   `Unblocks N experiment(s): ...` list.
4. `hdd backfill`: the ID / Type cells of the results table.

Plus the other half of #306's `experiment status` fix: the `safe(title)` side, where
the title comes from the item file (#306 only stubbed a printable title).

The commands are invoked on their subgroups directly with CliRunner (as test_306
does). The normal create branch runs the real `create_item` (it writes the file with
the title as given); the `--push` branch needs a remote, so `create_item_and_push` is
monkeypatched to return a successful result. The experiment-id case also stubs
`create_item`, since the service would refuse an id with ESC before the echo.
`get_critical_path`, `get_experiment_runs`, `get_item` and `backfill_turtle_blocks` are
monkeypatched to hand back repo-shaped values carrying EVIL.

Output is captured through every module console swapped for a
`Console(force_terminal=True)`. Controls: printable output is unchanged, and `[b]`
markup in a value is still literal.
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
from yurtle_kanban.hdd_commands import (
    experiment,
    hdd,
    hypothesis,
    idea,
    literature,
    measure,
    paper,
)
from yurtle_kanban.service import KanbanService

EVIL = "X\x1b[2J\nFORGED"
ESCAPED = "X\\x1b[2J\\nFORGED"  # what `safe()` shows for EVIL

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


def _assert_no_raw(out: str) -> None:
    plain = _plain(out)
    assert "\x1b" not in plain, f"raw ESC reached the terminal: {out!r}"
    forged = [ln for ln in plain.splitlines() if ln.lstrip(" │┃|").startswith("FORGED")]
    assert not forged, f"forged output line: {forged!r} in {out!r}"


def _assert_all_escaped(out: str, needle: str, escaped: str = ESCAPED) -> None:
    _assert_no_raw(out)
    line = _line(out, needle)
    assert escaped in line, f"{needle!r} line doesn't show the escaped text: {line!r}"


# --- 1. `* create`: Created {id}: {title} ---------------------------------------------

# (group, argv before the title-bearing value, argv after, id prefix in the line)
CREATES: dict[str, tuple[click.Group, list[str], list[str], str]] = {
    "idea": (idea, ["create"], [], "IDEA-R-001"),
    "literature": (literature, ["create"], [], "LIT-001"),
    "paper": (paper, ["create", "130"], [], "PAPER-130"),
    "hypothesis": (hypothesis, ["create"], [], "H-001"),
    "experiment": (experiment, ["create", "--title"], [], "EXPR-001"),
    "measure": (measure, ["create"], ["--unit", "ms", "--category", "perf"], "M-001"),
}


def _create_args(kind: str, value: str, push: bool) -> list[str]:
    _, head, tail, _ = CREATES[kind]
    return [*head, value, *tail, *(["--push"] if push else [])]


def _stub_push(monkeypatch: pytest.MonkeyPatch, repo: Path) -> None:
    """`--push` needs a remote; stand in for a successful create-commit-push."""

    def create_item_and_push(self: KanbanService, **kwargs: Any) -> dict[str, Any]:
        item_id = kwargs["item_id"]
        return {
            "success": True,
            "pushed": True,
            "id": item_id,
            "item": SimpleNamespace(id=item_id, file_path=repo / f"{item_id}.md"),
            "message": "ok",
        }

    monkeypatch.setattr(KanbanService, "create_item_and_push", create_item_and_push)


@pytest.mark.parametrize("push", [False, True], ids=["normal", "push"])
@pytest.mark.parametrize("kind", list(CREATES))
def test_create_line_title_is_escaped(
    kind: str, push: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    if push:
        _stub_push(monkeypatch, repo)
    out = _run(repo, CREATES[kind][0], _create_args(kind, EVIL, push), monkeypatch)
    _assert_all_escaped(out, f"{CREATES[kind][3]}: ")


@pytest.mark.parametrize("push", [False, True], ids=["normal", "push"])
def test_experiment_create_argv_id_is_escaped(
    push: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`experiment create EXPR_ID` echoes the argv id as `item.id` / `result['id']`.
    The template's turtle block and the service would refuse ESC in an id before the
    echo, so `_render` and `create_item` are stubbed too."""
    repo = _repo(tmp_path)
    _stub_push(monkeypatch, repo)
    monkeypatch.setattr(hdd_commands, "_render", lambda *a, **k: "# stub\n")

    def create_item(self: KanbanService, **kwargs: Any) -> Any:
        return SimpleNamespace(id=kwargs["item_id"], file_path=repo / "E.md")

    monkeypatch.setattr(KanbanService, "create_item", create_item)
    monkeypatch.setattr(KanbanService, "get_item", lambda self, item_id: None)
    args = ["create", "EXPR-" + EVIL, "--title", "T", *(["--push"] if push else [])]
    out = _run(repo, experiment, args, monkeypatch)
    _assert_all_escaped(out, ": T", "EXPR-" + ESCAPED)


# --- 2. experiment status: run-table cells and the title ------------------------------


def _stub_status(
    monkeypatch: pytest.MonkeyPatch, runs: list[dict[str, Any]], title: str | None
) -> None:
    def get_experiment_runs(self: KanbanService, expr_id: str) -> list[dict[str, Any]]:
        return [dict(r) for r in runs]

    def get_item(self: KanbanService, item_id: str) -> Any:
        return None if title is None else SimpleNamespace(title=title)

    monkeypatch.setattr(KanbanService, "get_experiment_runs", get_experiment_runs)
    monkeypatch.setattr(KanbanService, "get_item", get_item)


def _run_row(**over: str) -> dict[str, Any]:
    row: dict[str, Any] = {
        "timestamp": "2026-09-25T10:00:00",
        "being": "b",
        "status": "complete",
        "outcome": "win",
        "run_path": Path("/nowhere"),
    }
    row.update(over)
    return row


@pytest.mark.parametrize("field", ["timestamp", "being", "status", "outcome"])
def test_experiment_status_run_cell_is_escaped(
    field: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run file's field carrying ESC + newline (EVIL is 14 chars, so it survives the
    timestamp's [:19] cut). The Run/Being/Status columns are fixed-width, so the
    escaped text may wrap inside its cell: the check is no raw ESC / FORGED line, and
    the escaped `X\\x1b` is shown."""
    repo = _repo(tmp_path)
    _stub_status(monkeypatch, [_run_row(**{field: EVIL})], "Title")
    out = _run(repo, experiment, ["status", "EXPR-001"], monkeypatch)
    assert "EXPR-001: Title" in _plain(out), repr(out)
    _assert_no_raw(out)
    assert "X\\x1b" in _plain(out), repr(out)


@pytest.mark.parametrize("runs", [False, True], ids=["no-runs-line", "table-title"])
def test_experiment_status_item_title_is_escaped(
    runs: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The title half of #306's fix: the title read from the experiment's file."""
    repo = _repo(tmp_path)
    _stub_status(monkeypatch, [_run_row()] if runs else [], EVIL)
    out = _run(repo, experiment, ["status", "EXPR-001"], monkeypatch)
    _assert_all_escaped(out, "EXPR-001: ")


# --- 3. hdd critical-path rows --------------------------------------------------------


def _critical_path(
    repo: Path, monkeypatch: pytest.MonkeyPatch, rows: list[dict[str, Any]], *extra: str
) -> str:
    def get_critical_path(self: KanbanService, **kwargs: Any) -> list[dict[str, Any]]:
        return rows

    monkeypatch.setattr(KanbanService, "get_critical_path", get_critical_path)
    return _run(repo, hdd, ["critical-path", *extra], monkeypatch)


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
        "runs": 0,
        "downstream_impact": 0,
    }
    exp.update(over)
    return exp


def _blocker(**over: Any) -> dict[str, Any]:
    b: dict[str, Any] = {
        "expedition_id": "EXP-1",
        "title": "t",
        "status": "ready",
        "assignee": "Mini",
        "impact": 1,
        "unblocks_experiments": ["EXPR-001"],
    }
    b.update(over)
    return b


@pytest.mark.parametrize(
    "field,needle", [("experiment_id", "— t"), ("title", "EXPR-001 — ")]
)
def test_critical_path_experiment_row_is_escaped(
    field: str, needle: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    out = _critical_path(repo, monkeypatch, [_experiment(**{field: EVIL})])
    _assert_all_escaped(out, needle)


@pytest.mark.parametrize(
    "over,needle",
    [
        ({"expedition_id": EVIL}, "— t"),
        ({"title": EVIL}, "1. EXP-1 — "),
        ({"unblocks_experiments": ["EXPR-001", EVIL]}, "Unblocks 1 experiment(s):"),
    ],
    ids=["expedition_id", "title", "unblocks"],
)
def test_critical_path_dev_blocker_row_is_escaped(
    over: dict[str, Any], needle: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    out = _critical_path(repo, monkeypatch, [_blocker(**over)], "--dev-blockers")
    _assert_all_escaped(out, needle)


# --- 4. hdd backfill table ------------------------------------------------------------


def _backfill(repo: Path, monkeypatch: pytest.MonkeyPatch, **over: Any) -> str:
    row: dict[str, Any] = {"id": "H-001", "type": "hypothesis", "triples_added": 3}
    row.update(over)
    row["action"] = "would_backfill"

    def backfill_turtle_blocks(self: KanbanService, dry_run: bool = False) -> list[dict]:
        return [row]

    monkeypatch.setattr(KanbanService, "backfill_turtle_blocks", backfill_turtle_blocks)
    return _run(repo, hdd, ["backfill", "--dry-run"], monkeypatch)


@pytest.mark.parametrize("field", ["id", "type"])
def test_backfill_table_cell_is_escaped(
    field: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Width 400, but the ID/Type columns are fixed-width (16/12), so the cell wraps;
    the check is that no raw ESC / FORGED line reaches the terminal."""
    repo = _repo(tmp_path)
    out = _backfill(repo, monkeypatch, **{field: EVIL})
    _assert_no_raw(out)
    assert "\\x1b" in _plain(out), repr(out)


# --- controls -------------------------------------------------------------------------


@pytest.mark.parametrize("push", [False, True], ids=["normal", "push"])
@pytest.mark.parametrize("kind", list(CREATES))
def test_printable_create_line_is_unchanged(
    kind: str, push: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    if push:
        _stub_push(monkeypatch, repo)
    out = _run(repo, CREATES[kind][0], _create_args(kind, "Plain [b]x", push), monkeypatch)
    verb = "Created and pushed" if push else "Created"
    assert f"{verb} {CREATES[kind][3]}: Plain [b]x\n" in _plain(out), repr(out)


def test_printable_status_table_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    _stub_status(monkeypatch, [_run_row(being="[b]x", outcome="win")], "Title [b]")
    out = _plain(_run(repo, experiment, ["status", "EXPR-001"], monkeypatch))
    assert "EXPR-001: Title [b]" in out, repr(out)
    row = _line(out, "2026-09-25T10:00:00")
    for cell in ("[b]x", "complete", "win"):
        assert cell in row, repr(row)
    _stub_status(monkeypatch, [], None)  # no item file: the title falls back to the id
    out = _plain(_run(repo, experiment, ["status", "EXPR-001"], monkeypatch))
    assert "EXPR-001: EXPR-001\n  No runs found.\n" in out, repr(out)


def test_printable_critical_path_rows_are_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    out = _plain(_critical_path(repo, monkeypatch, [_experiment(title="T [b]")]))
    assert "● EXPR-001 — T [b]\n" in out, repr(out)
    rows = [_blocker(title="B [b]", unblocks_experiments=["EXPR-001", "EXPR-[b]"])]
    out = _plain(_critical_path(repo, monkeypatch, rows, "--dev-blockers"))
    assert "1. EXP-1 — B [b]\n" in out, repr(out)
    assert "Unblocks 1 experiment(s): EXPR-001, EXPR-[b]\n" in out, repr(out)


def test_printable_backfill_table_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    out = _plain(_backfill(repo, monkeypatch, id="H-[b]1"))
    row = _line(out, "H-[b]1")
    assert "hypothesis" in row and "would add" in row, repr(row)
