# ruff: noqa: F811  -- the `repo` fixture imported from #576's module is re-bound as an arg
"""#803: a missing user file is a refusal (`MissingFile`), not a bare FileNotFoundError.

Decided ([steer] on #803, following #666/#786):
1. `yurtle_kanban.models.MissingFile(InputRefused, FileNotFoundError)`.
2. `KanbanService.update_run_status` (no `config.yaml` in the run folder) and
   `TemplateEngine.render` (no template for theme/type) raise it, the message naming
   the missing file / template.
3. It is BOTH an `InputRefused` (the CLI group and MCP report it as a one-line
   refusal) AND a `FileNotFoundError` (every existing `except FileNotFoundError`,
   e.g. the hdd "template not found" ClickExceptions, still catches it).

Scope notes found while writing these:
- `update_run_status` has no CLI or MCP caller in src (`experiment run` creates a
  run, `experiment status` only reads), so it is pinned at the service level.
- No MCP tool renders a template (`kanban_create_item` never touches
  `TemplateEngine`), so there is no MCP template case.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from tests.issues.test_576_cli_update_deps import Repo, repo  # noqa: F401
from yurtle_kanban import cli
from yurtle_kanban.cli import main
from yurtle_kanban.models import InputRefused
from yurtle_kanban.template_engine import TemplateEngine


def _missing_file() -> type:
    """`MissingFile`, imported late so the controls below still run on a src that
    lacks it (the type tests then fail with the ImportError)."""
    from yurtle_kanban.models import MissingFile

    return MissingFile


# ---------------------------------------------------------------------------
# 1. the type
# ---------------------------------------------------------------------------


def test_missing_file_is_input_refused_and_file_not_found() -> None:
    missing_file = _missing_file()
    assert issubclass(missing_file, InputRefused)
    assert issubclass(missing_file, FileNotFoundError)
    err = missing_file("gone")
    assert isinstance(err, InputRefused) and isinstance(err, FileNotFoundError)
    assert isinstance(err, ValueError) and isinstance(err, OSError)


# ---------------------------------------------------------------------------
# 2. update_run_status: no config.yaml in the run folder
# ---------------------------------------------------------------------------


def test_run_status_missing_config_is_missing_file(repo: Repo) -> None:
    missing_file = _missing_file()
    run = repo.root / "runs" / "run-1"
    run.mkdir(parents=True)
    with pytest.raises(missing_file) as exc:
        repo.service().update_run_status(run, "running")
    assert isinstance(exc.value, InputRefused) and isinstance(exc.value, FileNotFoundError)
    assert "config.yaml" in str(exc.value) and str(run) in str(exc.value)


def test_run_status_missing_folder_is_missing_file(repo: Repo) -> None:
    missing_file = _missing_file()
    with pytest.raises(missing_file, match="config.yaml"):
        repo.service().update_run_status(repo.root / "nonexistent", "complete")


def test_run_status_missing_config_still_caught_as_file_not_found(repo: Repo) -> None:
    """Control: an existing `except FileNotFoundError` caller is unaffected."""
    run = repo.root / "runs" / "run-1"
    run.mkdir(parents=True)
    with pytest.raises(FileNotFoundError, match="config.yaml"):
        repo.service().update_run_status(run, "running")


def test_run_status_present_config_updates(repo: Repo) -> None:
    """Control: a run with a config.yaml is updated as before."""
    run = repo.root / "runs" / "run-1"
    run.mkdir(parents=True)
    (run / "config.yaml").write_text("status: pending\n")
    repo.service().update_run_status(run, "complete", outcome="VALIDATED")
    text = (run / "config.yaml").read_text()
    assert "status: complete" in text and "outcome: VALIDATED" in text


# ---------------------------------------------------------------------------
# 3. TemplateEngine: no template for this theme/type
# ---------------------------------------------------------------------------


def test_missing_template_is_missing_file(tmp_path: Path) -> None:
    missing_file = _missing_file()
    with pytest.raises(missing_file) as exc:
        TemplateEngine(tmp_path).render("hdd", "idea", {"id": "IDEA-R-001", "title": "T"})
    assert isinstance(exc.value, InputRefused) and isinstance(exc.value, FileNotFoundError)
    msg = str(exc.value)
    assert "hdd" in msg and "idea" in msg, msg


def test_missing_template_unknown_type_is_missing_file() -> None:
    """The shipped templates dir, a type it has no template for."""
    missing_file = _missing_file()
    with pytest.raises(missing_file, match="gizmo"):
        TemplateEngine(cli._get_templates_dir()).render("hdd", "gizmo", {"title": "T"})


def test_missing_template_still_caught_as_file_not_found(tmp_path: Path) -> None:
    """Control: an existing `except FileNotFoundError` caller is unaffected."""
    with pytest.raises(FileNotFoundError):
        TemplateEngine(tmp_path).render("hdd", "idea", {"title": "T"})


def test_present_template_renders_as_before() -> None:
    """Control: a shipped template renders with its id and title substituted."""
    out = TemplateEngine(cli._get_templates_dir()).render(
        "hdd", "idea", {"id": "IDEA-R-042", "title": "A present template"}
    )
    assert "IDEA-R-042" in out and "A present template" in out


# ---------------------------------------------------------------------------
# 4. CLI: an hdd create with no template is still a clean one-line error
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("args", "needle"),
    [
        pytest.param(["idea", "create", "No template here"], "template not found", id="idea"),
        pytest.param(
            ["literature", "create", "No template here"], "template not found", id="literature"
        ),
        pytest.param(
            ["idea", "create", "No template here", "--push"], "template not found", id="idea-push"
        ),
    ],
)
def test_cli_hdd_create_missing_template_is_one_line_error(
    repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, args: list[str], needle: str
) -> None:
    empty = tmp_path / "no-templates"
    empty.mkdir()
    monkeypatch.setattr(cli, "_get_templates_dir", lambda: empty)
    before = repo.snapshot()
    result = CliRunner().invoke(main, args)
    assert result.exit_code == 1, (result.output, repr(result.exception))
    assert isinstance(result.exception, SystemExit), repr(result.exception)
    assert "Traceback" not in result.output, result.output
    assert "Error:" in result.output and needle in " ".join(result.output.split()), result.output
    assert repo.snapshot() == before, "a refused create wrote a file"
