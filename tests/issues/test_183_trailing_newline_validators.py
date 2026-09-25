"""#183: `$`-anchored validators used with `.match` accept a trailing newline.

`$` matches just before a final `\\n`, so `re.match(r"^...$", "X\\n")` succeeds (the
#161 class). Remaining instances:

- service `create_experiment_run` / `get_experiment_runs`: `EXPR-130\\n` must be
  refused with the existing "Invalid experiment ID format" ValueError, and no
  directory named with a newline is created under `research/runs`.
- query `UnifiedGraph` (`_ITEM_ID_RE` via `_ref_or_literal`): a relationship value
  `EXPR-1\\n` must not become an item URIRef (no URIRef with a newline in it).

Also: `hdd_commands._render` must convert only the Turtle builder's own
`InvalidTurtleName(ValueError)` to a clean CLI error; any other ValueError from
`engine.render` (a real bug) propagates.

Controls: `EXPR-130` / `EXPR-1` still work; an invalid Turtle name through the CLI
still exits with one clean `Error:` line and no traceback.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import click
import pytest
from click.testing import CliRunner
from rdflib import URIRef

from yurtle_kanban.cli import main
from yurtle_kanban.config import KanbanConfig, PathConfig
from yurtle_kanban.models import WorkItem, WorkItemStatus, WorkItemType
from yurtle_kanban.query import ITEM, UnifiedGraph
from yurtle_kanban.service import KanbanService
from yurtle_kanban.template_engine import TemplateEngine

_BAD_EXPR_IDS = [
    pytest.param("EXPR-130\n", id="trailing-newline"),
    pytest.param("EXPR-131.5\n", id="dotted-trailing-newline"),
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, capture_output=True, check=True)
    for key, val in (("user.email", "test@test.com"), ("user.name", "Test")):
        subprocess.run(
            ["git", "config", key, val], cwd=tmp_path, capture_output=True, check=True
        )
    (tmp_path / ".kanban").mkdir()
    for sub in ("ideas", "literature", "papers", "hypotheses", "experiments", "measures"):
        (tmp_path / "research" / sub).mkdir(parents=True)
    return tmp_path


@pytest.fixture
def hdd_config(temp_repo: Path) -> KanbanConfig:
    config = KanbanConfig(
        theme="hdd",
        paths=PathConfig(
            root="research/",
            scan_paths=[
                "research/ideas/",
                "research/literature/",
                "research/papers/",
                "research/hypotheses/",
                "research/experiments/",
                "research/measures/",
            ],
        ),
    )
    config.save(temp_repo / ".kanban" / "config.yaml")
    return config


@pytest.fixture
def service(temp_repo: Path, hdd_config: KanbanConfig) -> KanbanService:
    return KanbanService(hdd_config, temp_repo)


@pytest.fixture
def runner(temp_repo: Path, hdd_config: KanbanConfig, monkeypatch) -> CliRunner:
    from yurtle_kanban import config as config_mod

    config_mod._theme_cache.clear()
    monkeypatch.chdir(temp_repo)
    return CliRunner()


def _newline_paths(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return [p for p in root.rglob("*") if "\n" in p.name or "\r" in p.name]


# ---------------------------------------------------------------------------
# service: experiment runs
# ---------------------------------------------------------------------------


class TestExperimentRunIdTrailingNewline:
    @pytest.mark.parametrize("expr_id", _BAD_EXPR_IDS)
    def test_create_experiment_run_refuses_trailing_newline(
        self, service: KanbanService, temp_repo: Path, expr_id: str
    ) -> None:
        with pytest.raises(ValueError, match="Invalid experiment ID format"):
            service.create_experiment_run(expr_id=expr_id, being="b", run_by="Agent")
        runs = temp_repo / "research" / "runs"
        assert not _newline_paths(runs), f"created {_newline_paths(runs)}"

    @pytest.mark.parametrize("expr_id", _BAD_EXPR_IDS)
    def test_get_experiment_runs_refuses_trailing_newline(
        self, service: KanbanService, temp_repo: Path, expr_id: str
    ) -> None:
        with pytest.raises(ValueError, match="Invalid experiment ID format"):
            service.get_experiment_runs(expr_id)

    @pytest.mark.parametrize("expr_id", _BAD_EXPR_IDS)
    def test_get_experiment_runs_does_not_read_newline_dir(
        self, service: KanbanService, temp_repo: Path, expr_id: str
    ) -> None:
        """Even when a newline-named run dir exists, it is refused, not read."""
        run_dir = temp_repo / "research" / "runs" / expr_id / "2026-01-01T000000-000000"
        run_dir.mkdir(parents=True)
        (run_dir / "config.yaml").write_text("being: sneaky\nstatus: running\n")
        with pytest.raises(ValueError, match="Invalid experiment ID format"):
            service.get_experiment_runs(expr_id)

    def test_control_plain_expr_id_still_works(
        self, service: KanbanService, temp_repo: Path
    ) -> None:
        run_path = service.create_experiment_run(
            expr_id="EXPR-130", being="test-being", run_by="Agent"
        )
        assert run_path.exists()
        assert run_path.parent == temp_repo / "research" / "runs" / "EXPR-130"
        runs = service.get_experiment_runs("EXPR-130")
        assert len(runs) == 1
        assert runs[0]["being"] == "test-being"

    def test_control_dotted_expr_id_still_works(self, service: KanbanService) -> None:
        run_path = service.create_experiment_run(
            expr_id="EXPR-131.5", being="b", run_by="Agent"
        )
        assert run_path.exists()
        assert len(service.get_experiment_runs("EXPR-131.5")) == 1

    def test_control_plain_invalid_id_still_refused(self, service: KanbanService) -> None:
        with pytest.raises(ValueError, match="Invalid experiment ID format"):
            service.create_experiment_run(expr_id="../etc", being="b", run_by="Agent")


# ---------------------------------------------------------------------------
# query: _ITEM_ID_RE via UnifiedGraph.add_item
# ---------------------------------------------------------------------------


def _item_with_relations(value: str) -> WorkItem:
    return WorkItem(
        id="EXP-001",
        title="Source",
        item_type=WorkItemType.EXPEDITION,
        status=WorkItemStatus.BACKLOG,
        file_path=Path("test.md"),
        depends_on=[value],
        related=[value],
        blocks=[value],
    )


def _newline_urirefs(ug: UnifiedGraph) -> list[URIRef]:
    return [
        term
        for triple in ug.graph
        for term in triple
        if isinstance(term, URIRef) and ("\n" in str(term) or "\r" in str(term))
    ]


class TestQueryItemIdTrailingNewline:
    @pytest.mark.parametrize("value", ["EXPR-1\n", "EXPR-1.2\n"])
    def test_trailing_newline_value_is_not_an_item_ref(self, value: str) -> None:
        ug = UnifiedGraph()
        ug.add_item(_item_with_relations(value))
        bad = _newline_urirefs(ug)
        assert not bad, f"URIRef with a newline produced: {bad!r}"
        assert ITEM[value] not in {o for _, _, o in ug.graph}

    def test_control_plain_id_is_item_ref(self) -> None:
        ug = UnifiedGraph()
        ug.add_item(_item_with_relations("EXPR-1"))
        objects = {o for _, _, o in ug.graph}
        assert ITEM["EXPR-1"] in objects
        assert not _newline_urirefs(ug)


# ---------------------------------------------------------------------------
# hdd_commands._render catches only InvalidTurtleName
# ---------------------------------------------------------------------------


def _templates_dir() -> Path:
    pkg_dir = Path(__file__).resolve().parents[2] / "templates"
    if not pkg_dir.exists():
        pytest.skip("Templates directory not found")
    return pkg_dir


class TestRenderCatchesOnlyInvalidTurtleName:
    def test_invalid_turtle_name_exception_exists(self) -> None:
        from yurtle_kanban.turtle_builder import InvalidTurtleName

        assert issubclass(InvalidTurtleName, ValueError)

    def test_validator_raises_invalid_turtle_name(self) -> None:
        from yurtle_kanban.turtle_builder import (
            InvalidTurtleName,
            _validate_turtle_local_name,
        )

        with pytest.raises(InvalidTurtleName):
            _validate_turtle_local_name("X Y")

    def test_render_converts_invalid_turtle_name(self, monkeypatch) -> None:
        from yurtle_kanban import hdd_commands
        from yurtle_kanban.turtle_builder import InvalidTurtleName

        def boom(self, *args, **kwargs):
            raise InvalidTurtleName("Invalid Turtle local name: bad")

        monkeypatch.setattr(TemplateEngine, "render", boom)
        engine = TemplateEngine(_templates_dir())
        with pytest.raises(click.ClickException, match="Invalid Turtle local name"):
            hdd_commands._render(engine, "hdd", "hypothesis", {})

    def test_render_does_not_hide_other_value_errors(self, monkeypatch) -> None:
        from yurtle_kanban import hdd_commands

        def boom(self, *args, **kwargs):
            raise ValueError("boom")

        monkeypatch.setattr(TemplateEngine, "render", boom)
        engine = TemplateEngine(_templates_dir())
        with pytest.raises(ValueError, match="boom") as excinfo:
            hdd_commands._render(engine, "hdd", "hypothesis", {})
        assert not isinstance(excinfo.value, click.ClickException)

    @pytest.mark.parametrize("value", ["X Y", "X<Y", "H1\n"])
    def test_control_invalid_name_is_clean_cli_error(
        self, runner: CliRunner, temp_repo: Path, value: str
    ) -> None:
        result = runner.invoke(
            main,
            ["hypothesis", "create", "Better recall", "--paper", "130", "--id", value],
        )
        crashed = result.exception is not None and not isinstance(
            result.exception, SystemExit
        )
        assert not crashed, (result.output, repr(result.exception))
        assert result.exit_code != 0, result.output
        assert "Traceback" not in result.output
        error_lines = [ln for ln in result.output.splitlines() if ln.startswith("Error:")]
        assert len(error_lines) == 1, result.output
        assert "Invalid Turtle local name" in error_lines[0]
        assert not list((temp_repo / "research" / "hypotheses").glob("*.md"))
