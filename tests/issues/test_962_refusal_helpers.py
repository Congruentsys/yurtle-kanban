"""Issue #962 — the empty metrics shape lives in the service; one JSON-aware `refuse`.

After #905, ``metrics <known item, no history> --json`` hand-builds the empty
metrics dict in cli.py, and ``experiment status`` spells its refusal inline
(``json_refusal`` under ``--json``, else ``console.print("Error: …")`` plus
``raise SystemExit(1)``), duplicating cli.py's ``_refuse``.

Decided ([steer] on #962, bucket 1; output unchanged):
- ``KanbanService.get_flow_metrics`` returns the empty shape for a known item
  with no history: ``{"item_id", "transitions": 0, "time_in_status": {},
  "cycle_time_hours": None, "lead_time_hours": None}``, no ``error`` key. An
  unknown item keeps an ``error`` key.
- ``_refuse(e, plain=None)`` moves to ``_click.py`` as a shared ``refuse`` that is
  JSON-aware via ``json_requested()``; ``experiment status`` uses it.

Controls: #905's CLI output is unchanged (tests/issues/test_905 stays green, and
the exact refusal text is pinned again here).
"""

from __future__ import annotations

import ast
import inspect
import json
import subprocess
import textwrap
from collections.abc import Iterator
from pathlib import Path

import click
import pytest
from click.testing import CliRunner, Result

from yurtle_kanban import config as config_mod
from yurtle_kanban import hdd_commands
from yurtle_kanban.cli import main
from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.service import KanbanService

SOFTWARE_CONFIG = """\
kanban:
  theme: software
  paths:
    root: kanban-work/
    scan_paths:
    - "kanban-work/"
    ignore:
      - "**/_TEMPLATE*"
"""

HDD_CONFIG = """\
kanban:
  theme: hdd
  paths:
    root: research/
    scan_paths:
    - "research/experiments/"
"""

FEAT_NO_HISTORY = """\
---
id: FEAT-001
title: "Hello"
type: feature
status: backlog
priority: medium
created: 2026-01-01
---

# FEAT-001: Hello
"""

FEAT_WITH_HISTORY = """\
---
id: FEAT-002
title: "Moved"
type: feature
status: done
priority: medium
created: 2026-01-01
---

# FEAT-002: Moved

```yurtle
@prefix kb: <https://yurtle.dev/kanban/> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

<> kb:statusChange [
    kb:status kb:ready ;
    kb:at "2026-01-01T10:00:00"^^xsd:dateTime ;
    kb:by "t" ;
] .
<> kb:statusChange [
    kb:status kb:in_progress ;
    kb:at "2026-01-02T10:00:00"^^xsd:dateTime ;
    kb:by "t" ;
] .
<> kb:statusChange [
    kb:status kb:done ;
    kb:at "2026-01-03T10:00:00"^^xsd:dateTime ;
    kb:by "t" ;
] .
```
"""

EMPTY_SHAPE = {
    "item_id": "FEAT-001",
    "transitions": 0,
    "time_in_status": {},
    "cycle_time_hours": None,
    "lead_time_hours": None,
}


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_theme_cache() -> Iterator[None]:
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)


def _init_repo(repo: Path) -> None:
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")


@pytest.fixture
def board(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A software board: FEAT-001 (no status history), FEAT-002 (history); the cwd."""
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    repo = tmp_path / "repo"
    (repo / ".kanban").mkdir(parents=True)
    (repo / ".kanban" / "config.yaml").write_text(SOFTWARE_CONFIG)
    features = repo / "kanban-work" / "features"
    features.mkdir(parents=True)
    (features / "FEAT-001-hello.md").write_text(FEAT_NO_HISTORY)
    (features / "FEAT-002-moved.md").write_text(FEAT_WITH_HISTORY)
    _init_repo(repo)
    monkeypatch.chdir(repo)
    return repo


@pytest.fixture
def service(board: Path) -> KanbanService:
    config = KanbanConfig.load(board / ".kanban" / "config.yaml")
    return KanbanService(config, board)


@pytest.fixture
def hdd_board(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An HDD board with EXPR-001 (no runs); the cwd."""
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    repo = tmp_path / "repo"
    (repo / ".kanban").mkdir(parents=True)
    (repo / ".kanban" / "config.yaml").write_text(HDD_CONFIG)
    experiments = repo / "research" / "experiments"
    experiments.mkdir(parents=True)
    (experiments / "EXPR-001-known.md").write_text(
        '---\nid: EXPR-001\ntitle: "Known"\ntype: experiment\nstatus: draft\n'
        "created: 2026-01-01\npriority: medium\ntags: []\n---\n\n# EXPR-001: Known\n"
    )
    _init_repo(repo)
    monkeypatch.chdir(repo)
    return repo


def _shown(result: Result) -> str:
    return (
        f"exit {result.exit_code}\n--- stdout ---\n{result.stdout}\n"
        f"--- stderr ---\n{result.stderr}\n--- exception ---\n{result.exception!r}"
    )


def _refuse_fn():
    """The shared `refuse` in `_click` (red until it exists)."""
    from yurtle_kanban import _click

    fn = getattr(_click, "refuse", None)
    assert callable(fn), "yurtle_kanban._click.refuse does not exist (#962)"
    return fn


def _tiny_command(**refuse_kwargs: object) -> click.Command:
    refuse = _refuse_fn()

    @click.command()
    @click.option("--json", "as_json", is_flag=True)
    def tiny(as_json: bool) -> None:
        refuse("boom [bold]x[/bold]", **refuse_kwargs)
        click.echo("NOT REACHED")

    return tiny


# ---------------------------------------------------------------------------
# 1. service.get_flow_metrics: the empty shape for a known item without history
# ---------------------------------------------------------------------------


class TestServiceFlowMetrics:
    def test_known_item_no_history_is_empty_shape(self, service: KanbanService) -> None:
        """Red: today `{"error": "No status history found"}`."""
        assert service.get_flow_metrics("FEAT-001") == EMPTY_SHAPE

    def test_known_item_no_history_has_no_error_key(self, service: KanbanService) -> None:
        """Red: today the only key is `error`."""
        assert "error" not in service.get_flow_metrics("FEAT-001")

    def test_unknown_item_keeps_error_key(self, service: KanbanService) -> None:
        """Control: an unknown item is still an `error` dict."""
        data = service.get_flow_metrics("FEAT-999")
        assert isinstance(data, dict) and "error" in data, data

    def test_known_item_with_history_unchanged(self, service: KanbanService) -> None:
        """Control: the with-history shape and numbers."""
        data = service.get_flow_metrics("FEAT-002")
        assert set(data) == set(EMPTY_SHAPE), data
        assert data["item_id"] == "FEAT-002"
        assert data["transitions"] == 3
        assert data["cycle_time_hours"] == pytest.approx(24.0)
        assert data["lead_time_hours"] == pytest.approx(48.0)

    def test_cli_no_history_json_matches_service_shape(self, board: Path) -> None:
        """Control (#905): the CLI prints the same empty shape, exit 0."""
        result = CliRunner().invoke(main, ["metrics", "FEAT-001", "--json"])
        assert result.exit_code == 0, _shown(result)
        assert json.loads(result.stdout) == EMPTY_SHAPE, _shown(result)


# ---------------------------------------------------------------------------
# 2. _click.refuse: one JSON-aware refusal
# ---------------------------------------------------------------------------


class TestSharedRefuse:
    def test_refuse_exists_in_click_module(self) -> None:
        """Red: `_refuse` lives only in cli.py today."""
        _refuse_fn()

    def test_refuse_json_is_one_object_exit_1(self) -> None:
        """Red: no `_click.refuse`. Under --json: one JSON object on stdout, exit 1."""
        result = CliRunner().invoke(_tiny_command(), ["--json"])
        shown = _shown(result)
        assert result.exit_code == 1, shown
        assert json.loads(result.stdout) == {
            "success": False,
            "error": "boom [bold]x[/bold]",
        }, shown
        assert "NOT REACHED" not in result.output, shown

    def test_refuse_plain_is_error_line_exit_1(self) -> None:
        """Red: no `_click.refuse`. Without --json: `Error: …` (markup escaped), exit 1."""
        result = CliRunner().invoke(_tiny_command(), [])
        shown = _shown(result)
        assert result.exit_code == 1, shown
        assert "Error: boom [bold]x[/bold]" in result.output, shown
        assert "NOT REACHED" not in result.output, shown
        with pytest.raises(json.JSONDecodeError):
            json.loads(result.stdout)

    def test_refuse_plain_override(self) -> None:
        """Red: no `_click.refuse`. `plain` replaces the `Error:` line without --json..."""
        result = CliRunner().invoke(_tiny_command(plain="custom plain line"), [])
        shown = _shown(result)
        assert result.exit_code == 1, shown
        assert "custom plain line" in result.output, shown
        assert "boom" not in result.output, shown

    def test_refuse_plain_override_ignored_under_json(self) -> None:
        """Red: no `_click.refuse`. ...but under --json the refusal carries `e`."""
        result = CliRunner().invoke(_tiny_command(plain="custom plain line"), ["--json"])
        shown = _shown(result)
        assert result.exit_code == 1, shown
        assert json.loads(result.stdout)["error"] == "boom [bold]x[/bold]", shown


# ---------------------------------------------------------------------------
# 3. experiment status uses the shared refuse (static)
# ---------------------------------------------------------------------------


def _experiment_status_ast() -> ast.FunctionDef:
    fn = hdd_commands.experiment_status
    fn = getattr(fn, "callback", fn)
    fn = inspect.unwrap(fn)
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    node = tree.body[0]
    assert isinstance(node, ast.FunctionDef), ast.dump(node)
    return node


def _called_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            if isinstance(sub.func, ast.Name):
                names.add(sub.func.id)
            elif isinstance(sub.func, ast.Attribute):
                names.add(sub.func.attr)
    return names


class TestExperimentStatusUsesRefuse:
    def test_calls_refuse(self) -> None:
        """Red: today it calls `json_refusal` and raises SystemExit inline."""
        assert "refuse" in _called_names(_experiment_status_ast())

    def test_no_inline_system_exit(self) -> None:
        """Red: today `raise SystemExit(1)` after the `Error:` print."""
        func = _experiment_status_ast()
        raises = [
            n
            for n in ast.walk(func)
            if isinstance(n, ast.Raise) and n.exc is not None and "SystemExit" in ast.unparse(n.exc)
        ]
        assert not raises, [ast.unparse(r) for r in raises]

    def test_no_inline_not_found_print_or_json_refusal(self) -> None:
        """Red: today an inline `console.print("[red]Error: Experiment not found…")`
        and a direct `json_refusal` call."""
        func = _experiment_status_ast()
        assert "json_refusal" not in _called_names(func)
        prints = [
            ast.unparse(n)
            for n in ast.walk(func)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "print"
            and "Error: Experiment not found" in ast.unparse(n)
        ]
        assert not prints, prints


# ---------------------------------------------------------------------------
# 4. Controls: experiment status output unchanged (#905)
# ---------------------------------------------------------------------------


class TestExperimentStatusOutputUnchanged:
    def test_unknown_json_refusal_exact(self, hdd_board: Path) -> None:
        result = CliRunner().invoke(main, ["experiment", "status", "EXPR-999", "--json"])
        shown = _shown(result)
        assert result.exit_code == 1, shown
        assert json.loads(result.stdout) == {
            "success": False,
            "error": "Experiment not found: EXPR-999",
        }, shown

    def test_unknown_plain_error_line(self, hdd_board: Path) -> None:
        result = CliRunner().invoke(main, ["experiment", "status", "EXPR-999"])
        shown = _shown(result)
        assert result.exit_code == 1, shown
        assert "Error: Experiment not found: EXPR-999" in result.output, shown

    def test_known_no_runs_json_is_empty_list(self, hdd_board: Path) -> None:
        result = CliRunner().invoke(main, ["experiment", "status", "EXPR-001", "--json"])
        assert result.exit_code == 0, _shown(result)
        assert json.loads(result.stdout) == [], _shown(result)
