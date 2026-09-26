"""Issue #450 — single-board `move` checks the default workflow, not the theme's
`transitions`.

`KanbanService._validate_transition` only consults a theme's `transitions` when
`_get_board_for_item` returns a board, which is multi-board only. A single-board
hdd repo therefore falls through to the default workflow: `move X active` from
draft is refused as "Invalid transition from backlog to in_progress" although
hdd allows `draft -> active`, and `abandoned -> active` (hdd forbids it) is let
through because the default allows `blocked -> in_progress`.

Decided behaviour: a single-board repo validates `move` against its configured
theme's `transitions` (`config.get_theme()`), exactly as a multi-board board uses
its preset's. The workflow file / default workflow applies only when the theme
defines no transitions — today only hdd defines them, so software and nautical
are unchanged. `--force` still bypasses validation.
"""

from __future__ import annotations

import io
import re
import subprocess
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner, Result
from rich.console import Console

from yurtle_kanban import cli
from yurtle_kanban.cli import main
from yurtle_kanban.config import KanbanConfig, _load_builtin_theme
from yurtle_kanban.models import WorkItemStatus
from yurtle_kanban.service import KanbanService

# ---------------------------------------------------------------------------
# Fixtures / helpers (as in tests/issues/test_442_move_wip_column_lookup.py)
# ---------------------------------------------------------------------------


def _git_init(path: Path) -> None:
    for args in (
        ["git", "init", "-b", "main"],
        ["git", "config", "user.email", "test@test.com"],
        ["git", "config", "user.name", "Test"],
        ["git", "commit", "--allow-empty", "-m", "init"],
    ):
        subprocess.run(args, cwd=path, capture_output=True, check=True)


def _clear_theme_cache() -> None:
    from yurtle_kanban import config as config_mod

    config_mod._theme_cache.clear()


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An empty git repo, cwd set to it, no kanban config yet."""
    _git_init(tmp_path)
    _clear_theme_cache()
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def wide(monkeypatch: pytest.MonkeyPatch) -> io.StringIO:
    """Route cli's console to a wide, colourless buffer."""
    buf = io.StringIO()
    monkeypatch.setattr(
        cli, "console", Console(file=buf, width=300, color_system=None, force_terminal=False)
    )
    return buf


def _ok(result: Result) -> Result:
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        raise AssertionError(f"crashed: {result.exception!r}") from result.exception
    assert result.exit_code == 0, result.output
    return result


def _created_id(runner: CliRunner, buf: io.StringIO, args: list[str]) -> str:
    buf.seek(0)
    buf.truncate()
    result = _ok(runner.invoke(main, args))
    out = buf.getvalue() + result.output
    match = re.search(r"Created (\S+):", out)
    assert match, out
    return match.group(1)


def _move(
    runner: CliRunner, buf: io.StringIO, item_id: str, column: str, *flags: str
) -> tuple[Result, str]:
    """Run `move` (never raising on a non-zero exit); return (result, all output)."""
    buf.seek(0)
    buf.truncate()
    result = runner.invoke(
        main, ["move", item_id, column, "--no-commit", "--skip-gates", *flags]
    )
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        raise AssertionError(f"crashed: {result.exception!r}") from result.exception
    return result, buf.getvalue() + result.output


def _status(repo: Path, item_id: str) -> WorkItemStatus:
    _clear_theme_cache()
    service = KanbanService(KanbanConfig.load(repo / ".kanban" / "config.yaml"), repo)
    item = service.get_item(item_id)
    assert item is not None, item_id
    return item.status


def _item(
    runner: CliRunner, wide: io.StringIO, item_type: str, title: str, stage: str | None
) -> str:
    """Create an item and --force it to `stage` (None: leave it where created)."""
    item_id = _created_id(runner, wide, ["create", item_type, title])
    if stage is not None:
        result, out = _move(runner, wide, item_id, stage, "--force")
        assert result.exit_code == 0, out
    return item_id


def _assert_allowed(
    repo: Path, result: Result, out: str, item_id: str, status: WorkItemStatus
) -> None:
    assert result.exit_code == 0, f"valid move refused:\n{out}"
    assert _status(repo, item_id) == status


def _assert_refused(
    repo: Path, result: Result, out: str, item_id: str, frm: str, to: str,
    stays: WorkItemStatus,
) -> None:
    assert result.exit_code != 0, f"invalid move {frm} -> {to} accepted:\n{out}"
    assert f"Invalid transition from {frm} to {to}" in out, out
    assert _status(repo, item_id) == stays, "refused move still moved it"


# ---------------------------------------------------------------------------
# 0. Premises about the shipped themes
# ---------------------------------------------------------------------------


def test_premise_only_hdd_defines_transitions(repo: Path) -> None:
    hdd = _load_builtin_theme("hdd", repo)
    assert hdd is not None
    assert hdd["transitions"]["draft"] == ["active", "abandoned"]
    assert hdd["transitions"]["abandoned"] == ["draft"]
    assert "complete" not in hdd["transitions"]["draft"]
    for name in ("software", "nautical"):
        theme = _load_builtin_theme(name, repo)
        assert theme is not None
        assert "transitions" not in theme, f"{name} now defines transitions"


# ---------------------------------------------------------------------------
# 1. Single-board hdd: the theme's transitions decide
# ---------------------------------------------------------------------------


@pytest.fixture
def hdd(repo: Path, runner: CliRunner) -> Path:
    _ok(runner.invoke(main, ["init", "--theme", "hdd"]))
    return repo


# (stage to --force to first (None = fresh draft), target column, resulting status)
HDD_ALLOWED = [
    pytest.param(None, "active", WorkItemStatus.IN_PROGRESS, id="draft-active"),  # RED
    pytest.param(None, "abandoned", WorkItemStatus.BLOCKED, id="draft-abandoned"),
    pytest.param("active", "draft", WorkItemStatus.BACKLOG, id="active-draft"),  # RED
    pytest.param("active", "complete", WorkItemStatus.DONE, id="active-complete"),
    pytest.param("active", "abandoned", WorkItemStatus.BLOCKED, id="active-abandoned"),
    pytest.param("abandoned", "draft", WorkItemStatus.BACKLOG, id="abandoned-draft"),
]


@pytest.mark.parametrize(("stage", "column", "status"), HDD_ALLOWED)
def test_single_board_hdd_allowed_transition(
    hdd: Path, runner: CliRunner, wide: io.StringIO,
    stage: str | None, column: str, status: WorkItemStatus,
) -> None:
    item_id = _item(runner, wide, "idea", "allowed", stage)

    result, out = _move(runner, wide, item_id, column)

    _assert_allowed(hdd, result, out, item_id, status)


def test_single_board_hdd_draft_to_active_via_canonical_name(
    hdd: Path, runner: CliRunner, wide: io.StringIO
) -> None:
    """`move X in_progress` is the same hdd `draft -> active` move."""
    item_id = _item(runner, wide, "idea", "canonical", None)

    result, out = _move(runner, wide, item_id, "in_progress")

    _assert_allowed(hdd, result, out, item_id, WorkItemStatus.IN_PROGRESS)


# (stage, target column, hdd from-name, hdd to-name, status it stays in)
HDD_REFUSED = [
    pytest.param(
        None, "complete", "draft", "complete", WorkItemStatus.BACKLOG, id="draft-complete",
    ),
    pytest.param(
        "abandoned", "active", "abandoned", "active", WorkItemStatus.BLOCKED,
        id="abandoned-active",
    ),
    pytest.param(
        "abandoned", "complete", "abandoned", "complete", WorkItemStatus.BLOCKED,
        id="abandoned-complete",
    ),
    pytest.param(
        "complete", "active", "complete", "active", WorkItemStatus.DONE, id="complete-active",
    ),
]


@pytest.mark.parametrize(("stage", "column", "frm", "to", "stays"), HDD_REFUSED)
def test_single_board_hdd_forbidden_transition_refused(
    hdd: Path, runner: CliRunner, wide: io.StringIO,
    stage: str | None, column: str, frm: str, to: str, stays: WorkItemStatus,
) -> None:
    item_id = _item(runner, wide, "idea", "forbidden", stage)

    result, out = _move(runner, wide, item_id, column)

    _assert_refused(hdd, result, out, item_id, frm, to, stays)


@pytest.mark.parametrize(("stage", "column", "frm", "to", "stays"), HDD_REFUSED)
def test_single_board_hdd_force_bypasses(
    hdd: Path, runner: CliRunner, wide: io.StringIO,
    stage: str | None, column: str, frm: str, to: str, stays: WorkItemStatus,
) -> None:
    """Control: `--force` moves past the theme's transitions."""
    item_id = _item(runner, wide, "idea", "forced", stage)

    result, out = _move(runner, wide, item_id, column, "--force")

    target = WorkItemStatus.from_string(
        {"active": "in_progress", "complete": "done"}.get(column, column)
    )
    _assert_allowed(hdd, result, out, item_id, target)


# ---------------------------------------------------------------------------
# 2. Controls: software / nautical single-board keep the default workflow
# ---------------------------------------------------------------------------

DEFAULT_THEMES = [
    pytest.param("software", "feature", id="software"),
    pytest.param("nautical", "expedition", id="nautical"),
]


@pytest.mark.parametrize(("theme", "item_type"), DEFAULT_THEMES)
def test_default_theme_backlog_to_in_progress_still_refused(
    repo: Path, runner: CliRunner, wide: io.StringIO, theme: str, item_type: str
) -> None:
    _ok(runner.invoke(main, ["init", "--theme", theme]))
    item_id = _item(runner, wide, item_type, "skip ahead", None)

    result, out = _move(runner, wide, item_id, "in_progress")

    _assert_refused(
        repo, result, out, item_id, "backlog", "in_progress", WorkItemStatus.BACKLOG
    )


@pytest.mark.parametrize(("theme", "item_type"), DEFAULT_THEMES)
def test_default_theme_valid_moves_still_allowed(
    repo: Path, runner: CliRunner, wide: io.StringIO, theme: str, item_type: str
) -> None:
    _ok(runner.invoke(main, ["init", "--theme", theme]))
    item_id = _item(runner, wide, item_type, "step by step", None)

    for column, status in (
        ("ready", WorkItemStatus.READY),
        ("in_progress", WorkItemStatus.IN_PROGRESS),
        ("done", WorkItemStatus.DONE),
    ):
        result, out = _move(runner, wide, item_id, column)
        _assert_allowed(repo, result, out, item_id, status)


@pytest.mark.parametrize(("theme", "item_type"), DEFAULT_THEMES)
def test_default_theme_blocked_to_in_progress_still_allowed(
    repo: Path, runner: CliRunner, wide: io.StringIO, theme: str, item_type: str
) -> None:
    """The default allows `blocked -> in_progress` (hdd's abandoned -> active
    does not): the fix must not leak hdd's rules into other themes."""
    _ok(runner.invoke(main, ["init", "--theme", theme]))
    item_id = _item(runner, wide, item_type, "unblocked", "blocked")

    result, out = _move(runner, wide, item_id, "in_progress")

    _assert_allowed(repo, result, out, item_id, WorkItemStatus.IN_PROGRESS)


@pytest.mark.parametrize(("theme", "item_type"), DEFAULT_THEMES)
def test_default_theme_force_bypasses(
    repo: Path, runner: CliRunner, wide: io.StringIO, theme: str, item_type: str
) -> None:
    _ok(runner.invoke(main, ["init", "--theme", theme]))
    item_id = _item(runner, wide, item_type, "forced", None)

    result, out = _move(runner, wide, item_id, "in_progress", "--force")

    _assert_allowed(repo, result, out, item_id, WorkItemStatus.IN_PROGRESS)


# ---------------------------------------------------------------------------
# 3. Control: multi-board hdd is unchanged
# ---------------------------------------------------------------------------


def _item_file(path: Path, item_id: str, item_type: str, status: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nid: {item_id}\ntitle: Test {item_id}\ntype: {item_type}\n"
        f"status: {status}\n---\n\n# Test {item_id}\n"
    )


@pytest.fixture
def multiboard(repo: Path) -> Path:
    """v2 config: `development` (nautical) default, `research` (hdd).

    Research holds IDEA-R-001 (draft) and IDEA-R-002 (abandoned); development
    holds EXP-001 (backlog)."""
    config = {
        "version": "2.0",
        "boards": [
            {
                "name": "development",
                "preset": "nautical",
                "path": "kanban-work/",
                "scan_paths": ["kanban-work/expeditions/"],
            },
            {
                "name": "research",
                "preset": "hdd",
                "path": "research/",
                "scan_paths": ["research/ideas/"],
            },
        ],
        "default_board": "development",
    }
    (repo / ".kanban").mkdir()
    (repo / ".kanban" / "config.yaml").write_text(yaml.dump(config))
    _item_file(repo / "research" / "ideas" / "IDEA-R-001.md", "IDEA-R-001", "idea", "backlog")
    _item_file(repo / "research" / "ideas" / "IDEA-R-002.md", "IDEA-R-002", "idea", "blocked")
    _item_file(
        repo / "kanban-work" / "expeditions" / "EXP-001.md", "EXP-001", "expedition",
        "backlog",
    )
    subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "fixture"], cwd=repo, capture_output=True, check=True)
    _clear_theme_cache()
    return repo


def test_multiboard_hdd_draft_to_active_allowed(
    multiboard: Path, runner: CliRunner, wide: io.StringIO
) -> None:
    result, out = _move(runner, wide, "IDEA-R-001", "active")

    _assert_allowed(multiboard, result, out, "IDEA-R-001", WorkItemStatus.IN_PROGRESS)


def test_multiboard_hdd_draft_to_complete_refused(
    multiboard: Path, runner: CliRunner, wide: io.StringIO
) -> None:
    result, out = _move(runner, wide, "IDEA-R-001", "complete")

    _assert_refused(
        multiboard, result, out, "IDEA-R-001", "draft", "complete", WorkItemStatus.BACKLOG
    )


def test_multiboard_hdd_abandoned_to_active_refused(
    multiboard: Path, runner: CliRunner, wide: io.StringIO
) -> None:
    result, out = _move(runner, wide, "IDEA-R-002", "active")

    _assert_refused(
        multiboard, result, out, "IDEA-R-002", "abandoned", "active", WorkItemStatus.BLOCKED
    )


def test_multiboard_hdd_force_bypasses(
    multiboard: Path, runner: CliRunner, wide: io.StringIO
) -> None:
    result, out = _move(runner, wide, "IDEA-R-001", "complete", "--force")

    _assert_allowed(multiboard, result, out, "IDEA-R-001", WorkItemStatus.DONE)


def test_multiboard_nautical_board_keeps_default_workflow(
    multiboard: Path, runner: CliRunner, wide: io.StringIO
) -> None:
    """Control: the nautical board beside hdd still uses the default rules."""
    result, out = _move(runner, wide, "EXP-001", "in_progress")

    _assert_refused(
        multiboard, result, out, "EXP-001", "backlog", "in_progress", WorkItemStatus.BACKLOG
    )
