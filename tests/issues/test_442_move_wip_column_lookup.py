"""Issue #442 — `move` never enforces a themed column's WIP limit.

`KanbanService.move_item` finds the target column with `col.id == new_status.value`.
On hdd the column id is `active` and the status it holds is `in_progress`, so no
column ever matches and `Active (n/5)` is never enforced; the same for spec's
`proposed` (10) and `implementing` (5). The board counts those columns through
`Board.column_status` (#87), so the board shows `Active (6/5)` that `move` let in.

Decided behaviour:
1. `move` finds the target column with the same column->status lookup as the
   board (`Board.column_status`). With a themed column at its limit, a move into
   it without `--force` is refused with a WIP-limit message and the item stays
   put. Single-board and multi-board (hdd preset), aggregate and per-type limits.
   Controls: software / nautical enforcement unchanged; `--force` still bypasses.
2. `Board.get_wip_violations` uses `column_status` too (folded in from the #438
   review). Its inline map-then-`from_string` lookup is identical to
   `column_status`, so no theme can make them disagree — the refactor has no
   observable difference. Only pinned: an hdd `Active` column over its limit is
   reported (green today, a guard for the refactor).
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
from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.models import WorkItemStatus
from yurtle_kanban.service import KanbanService

# ---------------------------------------------------------------------------
# Fixtures / helpers (as in tests/issues/test_87_board_count_vs_draw.py)
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


def _invoke(runner: CliRunner, args: list[str]) -> Result:
    return _ok(runner.invoke(main, args))


def _created_id(runner: CliRunner, buf: io.StringIO, args: list[str]) -> str:
    buf.seek(0)
    buf.truncate()
    result = _invoke(runner, args)
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
    result = runner.invoke(main, ["move", item_id, column, "--no-commit", *flags])
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        raise AssertionError(f"crashed: {result.exception!r}") from result.exception
    return result, buf.getvalue() + result.output


def _status(repo: Path, item_id: str) -> WorkItemStatus:
    _clear_theme_cache()
    service = KanbanService(KanbanConfig.load(repo / ".kanban" / "config.yaml"), repo)
    item = service.get_item(item_id)
    assert item is not None, item_id
    return item.status


def _assert_refused(
    result: Result, out: str, column_name: str, count: int, limit: int
) -> None:
    assert result.exit_code != 0, f"move into a full {column_name} was accepted:\n{out}"
    assert "WIP limit" in out, f"refused, but not for WIP:\n{out}"
    assert column_name in out, out
    assert f"({count}/{limit})" in out, out


def _fill(
    runner: CliRunner, wide: io.StringIO, item_type: str, column: str, n: int
) -> list[str]:
    """Create n items and force them into `column`; return their IDs."""
    ids = [_created_id(runner, wide, ["create", item_type, f"filler {i}"]) for i in range(n)]
    for item_id in ids:
        result, out = _move(runner, wide, item_id, column, "--force")
        assert result.exit_code == 0, out
    return ids


def _candidate(
    runner: CliRunner, wide: io.StringIO, item_type: str, title: str, stage: str
) -> str:
    """Create the item to move and --force it to `stage`, a status from which the
    move into the target column is a valid transition, so these tests stay about
    WIP only. Themes without `transitions` use the default workflow, which refuses
    `backlog -> in_progress`, so their candidates are staged; hdd validates against
    its own transitions (#450), where `draft -> active` is allowed and
    `abandoned -> active` is not, so its candidate starts in draft (`backlog`)."""
    item_id = _created_id(runner, wide, ["create", item_type, title])
    if stage != "backlog":
        result, out = _move(runner, wide, item_id, stage, "--force")
        assert result.exit_code == 0, out
    return item_id


# ---------------------------------------------------------------------------
# 1. Single board: themed columns with a limit, filled to it
# ---------------------------------------------------------------------------

# (theme, type, column id, column name, the theme's limit, status the column holds)
THEMED_LIMITS = [
    pytest.param(
        "hdd", "idea", "active", "Active", 5, WorkItemStatus.IN_PROGRESS, "backlog",
        id="hdd-active",
    ),
    pytest.param(
        "spec", "task", "implementing", "Implementing", 5, WorkItemStatus.IN_PROGRESS,
        "blocked", id="spec-implementing",
    ),
    pytest.param(
        "spec", "task", "proposed", "Proposed", 10, WorkItemStatus.READY,
        "backlog", id="spec-proposed",
    ),
]

# Controls: column ids that are canonical statuses (enforced today).
CONTROL_LIMITS = [
    pytest.param(
        "software", "feature", "in_progress", "In Progress", 3,
        WorkItemStatus.IN_PROGRESS, "ready", id="software-in_progress",
    ),
    pytest.param(
        "nautical", "expedition", "in_progress", "Underway", 10,
        WorkItemStatus.IN_PROGRESS, "ready", id="nautical-in_progress",
    ),
    pytest.param(
        "spec", "task", "review", "Review", 3, WorkItemStatus.REVIEW, "in_progress",
        id="spec-review",
    ),
]


@pytest.mark.parametrize(
    ("theme", "item_type", "column", "name", "limit", "status", "stage"),
    [*THEMED_LIMITS, *CONTROL_LIMITS],
)
def test_move_into_full_column_is_refused(
    repo: Path, runner: CliRunner, wide: io.StringIO,
    theme: str, item_type: str, column: str, name: str, limit: int,
    status: WorkItemStatus, stage: str,
) -> None:
    _invoke(runner, ["init", "--theme", theme])
    _fill(runner, wide, item_type, column, limit)
    extra = _candidate(runner, wide, item_type, "one too many", stage)

    result, out = _move(runner, wide, extra, column, "--skip-gates")

    _assert_refused(result, out, name, limit, limit)
    assert _status(repo, extra) == WorkItemStatus.from_string(stage), (
        "refused move still moved it"
    )


@pytest.mark.parametrize(
    ("theme", "item_type", "column", "name", "limit", "status", "stage"),
    [*THEMED_LIMITS, *CONTROL_LIMITS],
)
def test_move_below_limit_is_allowed(
    repo: Path, runner: CliRunner, wide: io.StringIO,
    theme: str, item_type: str, column: str, name: str, limit: int,
    status: WorkItemStatus, stage: str,
) -> None:
    """Control: the last free slot is still usable (no off-by-one over-enforcement)."""
    _invoke(runner, ["init", "--theme", theme])
    _fill(runner, wide, item_type, column, limit - 1)
    last = _candidate(runner, wide, item_type, "last slot", stage)

    result, out = _move(runner, wide, last, column, "--skip-gates")

    assert result.exit_code == 0, out
    assert _status(repo, last) == status


@pytest.mark.parametrize(
    ("theme", "item_type", "column", "name", "limit", "status", "stage"),
    [*THEMED_LIMITS, *CONTROL_LIMITS],
)
def test_force_bypasses_full_column(
    repo: Path, runner: CliRunner, wide: io.StringIO,
    theme: str, item_type: str, column: str, name: str, limit: int,
    status: WorkItemStatus, stage: str,
) -> None:
    """Control: `--force` moves past the limit, as today."""
    _invoke(runner, ["init", "--theme", theme])
    _fill(runner, wide, item_type, column, limit)
    extra = _candidate(runner, wide, item_type, "forced", stage)

    result, out = _move(runner, wide, extra, column, "--force")

    assert result.exit_code == 0, out
    assert _status(repo, extra) == status


def test_hdd_move_via_canonical_status_name_is_refused_too(
    repo: Path, runner: CliRunner, wide: io.StringIO
) -> None:
    """`move X in_progress` on hdd lands in the same Active column: same limit."""
    _invoke(runner, ["init", "--theme", "hdd"])
    _fill(runner, wide, "idea", "active", 5)
    extra = _candidate(runner, wide, "idea", "one too many", "backlog")

    result, out = _move(runner, wide, extra, "in_progress", "--skip-gates")

    _assert_refused(result, out, "Active", 5, 5)
    assert _status(repo, extra) == WorkItemStatus.BACKLOG


# ---------------------------------------------------------------------------
# 2. Multi-board: a nautical dev board plus an hdd research board
# ---------------------------------------------------------------------------


def _item_file(path: Path, item_id: str, item_type: str, status: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nid: {item_id}\ntitle: Test {item_id}\ntype: {item_type}\n"
        f"status: {status}\n---\n\n# Test {item_id}\n"
    )


def _multiboard_repo(repo: Path, research_wip: object = None) -> None:
    """v2 config: `development` (nautical) default, `research` (hdd).

    Research holds 5 ideas already active (hdd's Active limit), and in backlog
    one more idea (IDEA-R-006) and one literature review (LIT-001).
    `research_wip`, when given, is the research board's `wip_limits`.
    """
    research: dict[str, object] = {
        "name": "research",
        "preset": "hdd",
        "path": "research/",
        "scan_paths": ["research/ideas/", "research/literature/"],
    }
    if research_wip is not None:
        research["wip_limits"] = research_wip
    config = {
        "version": "2.0",
        "boards": [
            {
                "name": "development",
                "preset": "nautical",
                "path": "kanban-work/",
                "scan_paths": ["kanban-work/expeditions/"],
            },
            research,
        ],
        "default_board": "development",
    }
    (repo / ".kanban").mkdir()
    (repo / ".kanban" / "config.yaml").write_text(yaml.dump(config))
    (repo / "kanban-work" / "expeditions").mkdir(parents=True)
    for i in range(1, 6):
        _item_file(
            repo / "research" / "ideas" / f"IDEA-R-00{i}.md", f"IDEA-R-00{i}", "idea",
            "in_progress",
        )
    _item_file(repo / "research" / "ideas" / "IDEA-R-006.md", "IDEA-R-006", "idea", "backlog")
    _item_file(
        repo / "research" / "literature" / "LIT-001.md", "LIT-001", "literature", "backlog"
    )
    subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "fixture"], cwd=repo, capture_output=True, check=True)
    _clear_theme_cache()


def test_multiboard_hdd_active_limit_refuses_move(
    repo: Path, runner: CliRunner, wide: io.StringIO
) -> None:
    _multiboard_repo(repo)

    result, out = _move(runner, wide, "IDEA-R-006", "active", "--skip-gates")

    _assert_refused(result, out, "Active", 5, 5)
    assert "Research Board" in out, out  # the item's own board, not the default
    assert _status(repo, "IDEA-R-006") == WorkItemStatus.BACKLOG


def test_multiboard_hdd_force_bypasses(
    repo: Path, runner: CliRunner, wide: io.StringIO
) -> None:
    _multiboard_repo(repo)

    result, out = _move(runner, wide, "IDEA-R-006", "active", "--force")

    assert result.exit_code == 0, out
    assert _status(repo, "IDEA-R-006") == WorkItemStatus.IN_PROGRESS


def test_multiboard_hdd_per_type_limit_refuses_move(
    repo: Path, runner: CliRunner, wide: io.StringIO
) -> None:
    """Per-type limit on hdd's `active` column: ideas capped at 5, literature free."""
    _multiboard_repo(repo, research_wip={"active": {"idea": 5, "literature": None}})

    result, out = _move(runner, wide, "IDEA-R-006", "active", "--skip-gates")

    assert result.exit_code != 0, f"6th idea let into Active:\n{out}"
    assert "WIP limit reached for ideas" in out, out
    assert "Active" in out and "(5/5)" in out, out
    assert _status(repo, "IDEA-R-006") == WorkItemStatus.BACKLOG


def test_multiboard_hdd_per_type_other_type_allowed(
    repo: Path, runner: CliRunner, wide: io.StringIO
) -> None:
    """Control: an unlimited type still moves into a column full of another type."""
    _multiboard_repo(repo, research_wip={"active": {"idea": 5, "literature": None}})

    result, out = _move(runner, wide, "LIT-001", "active", "--skip-gates")

    assert result.exit_code == 0, out
    assert _status(repo, "LIT-001") == WorkItemStatus.IN_PROGRESS


# ---------------------------------------------------------------------------
# 3. Board.get_wip_violations on a themed column (guard for the refactor)
# ---------------------------------------------------------------------------


def test_hdd_active_over_limit_is_a_wip_violation(
    repo: Path, runner: CliRunner, wide: io.StringIO
) -> None:
    _invoke(runner, ["init", "--theme", "hdd"])
    _fill(runner, wide, "idea", "active", 6)  # --force past the limit of 5
    _clear_theme_cache()
    service = KanbanService(KanbanConfig.load(repo / ".kanban" / "config.yaml"), repo)

    violations = service.get_board().get_wip_violations()

    assert [(col.id, count, t) for col, count, t in violations] == [("active", 6, None)]


def test_multiboard_hdd_per_type_violation_reported(repo: Path) -> None:
    _multiboard_repo(repo, research_wip={"active": {"idea": 4, "literature": None}})
    service = KanbanService(KanbanConfig.load(repo / ".kanban" / "config.yaml"), repo)

    violations = service.get_board(board_name="research").get_wip_violations()

    assert [(col.id, count, t) for col, count, t in violations] == [("active", 5, "idea")]
