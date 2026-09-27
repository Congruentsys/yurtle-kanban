# ruff: noqa: F811  -- the `repo` fixture imported from #576's module is re-bound as an arg
"""#786: every user refusal is an `InputRefused`; MCP logs anything else as a bug.

Decided ([steer] on #786, following #666's design):
1. Every `raise ValueError` in service.py, config.py and models.py (`from_string`)
   refuses user input, item state or the user's config; each becomes `InputRefused`
   (a ValueError, so every existing `except ValueError` still works).
2. MCP `handle_tool_call` catches `InputRefused` as a refusal: an error result and one
   WARNING line, no traceback. Any other exception, a plain ValueError included, is a
   bug: an error result plus `logger.exception` (an ERROR record with exc_info).
3. A structural test pins that src raises no bare `ValueError` outside an allow-list
   of genuinely internal raises (see `ALLOWED`).

The MCP logger is the server module's own `logger` ("yurtle-kanban-mcp"), which is
not a child of "yurtle-kanban", so the tests attach to it directly.

Fixture: #576's two-board repo (`development` nautical under `work/`, `research` hdd
under `research/`): EXP-1 [], EXP-2 [], EXP-3 [EXP-1], EXP-4 [EXP-3], EXP-5 [EXP-2],
H1.1 [EXP-1], all `ready` (H1.1 `draft`).
"""

from __future__ import annotations

import ast
import logging
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from click.testing import CliRunner

import yurtle_kanban
from tests.issues.test_576_cli_update_deps import Repo, _git, repo  # noqa: F401
from tests.issues.test_743_swallowed_what import _comments_only
from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import main
from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.mcp import server as mcp_server
from yurtle_kanban.models import InputRefused, WorkItemStatus, WorkItemType
from yurtle_kanban.service import KanbanService

SRC = Path(yurtle_kanban.__file__).parent


def _mcp(repo: Repo) -> mcp_server.KanbanMCPServer:
    return mcp_server.KanbanMCPServer(repo_root=repo.root)


def _repo_with(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, board_extra: str) -> Path:
    """A one-board nautical repo whose board carries `board_extra` YAML (indented
    under the board), with EXP-1 and EXP-2 `ready` and assigned."""
    root = tmp_path / "gated"
    (root / ".kanban").mkdir(parents=True)
    (root / ".kanban" / "config.yaml").write_text(
        'version: "2.0"\nboards:\n  - name: development\n    preset: nautical\n'
        f'    path: work/\n{board_extra}default_board: development\n'
    )
    exp = root / "work" / "expeditions"
    exp.mkdir(parents=True)
    for n in (1, 2):
        (exp / f"EXP-{n}-item.md").write_text(
            f'---\nid: EXP-{n}\ntitle: "Item {n}"\ntype: expedition\nstatus: ready\n'
            f"assignee: Mini\npriority: medium\n---\n\n# Item {n}\n\nBody.\n"
        )
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "t@t.com")
    _git(root, "config", "user.name", "T")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "fixture")
    config_mod._theme_cache.clear()
    monkeypatch.chdir(root)
    monkeypatch.setenv("YURTLE_AGENT", "tester")
    return root


def _svc(root: Path) -> KanbanService:
    return KanbanService(KanbanConfig.load(root / ".kanban" / "config.yaml"), root)


# ---------------------------------------------------------------------------
# 1. every user refusal in service/config/models is an InputRefused
# ---------------------------------------------------------------------------


def _update(**kw: object) -> Callable[[KanbanService], object]:
    def call(s: KanbanService) -> object:
        return s.update_item("EXP-5", commit=False, **kw)  # type: ignore[arg-type]

    return call


SERVICE_REFUSALS: list = [
    pytest.param(
        lambda s: s.update_item("EXP-99", title="x", commit=False), "not found", id="not-found"
    ),
    pytest.param(
        # nautical: provisioning (ready) -> arrived (done) skips the workflow
        lambda s: s.move_item("EXP-5", WorkItemStatus.DONE, commit=False),
        "Illegal move",
        id="illegal-move",
    ),
    pytest.param(_update(priority="urgent-ish"), "", id="bad-priority"),
    pytest.param(_update(tags=["ok", "  "]), "tag is empty", id="empty-tag"),
    pytest.param(_update(title="   "), "title is empty", id="empty-title"),
    pytest.param(_update(title="one\ntwo"), "line break", id="multiline-title"),
    pytest.param(_update(add_depends_on=["EXP-5"]), "depend on itself", id="self-dep"),
    pytest.param(_update(add_depends_on=["EXP-99"]), "EXP-99", id="unknown-dep"),
    pytest.param(
        lambda s: s.update_item("EXP-1", add_depends_on=["EXP-4"], commit=False),
        "cycle",
        id="dep-cycle",
    ),
    pytest.param(lambda s: s.rank_item("EXP-5", 0, commit=False), "Rank", id="rank-lt-1"),
    pytest.param(
        lambda s: s.move_item(
            "EXP-5", WorkItemStatus.IN_PROGRESS, commit=False, closed_by='a"b'
        ),
        "",
        id="bad-closed-by",
    ),
]


@pytest.mark.parametrize(("call", "needle"), SERVICE_REFUSALS)
def test_service_refusal_is_input_refused(repo: Repo, call, needle: str) -> None:
    with pytest.raises(InputRefused) as info:
        call(repo.service())
    assert needle in str(info.value), str(info.value)


def test_input_fence_refusal_is_input_refused(repo: Repo) -> None:
    """Control: a description that opens a fence was already InputRefused (#720)."""
    with pytest.raises(InputRefused, match="fence"):
        repo.service().update_item("EXP-5", description="a\n```\nb", commit=False)


def test_swallowed_body_fence_refusal_is_input_refused(repo: Repo) -> None:
    """The item's own file has an unclosed fence over its comments: a body edit
    is refused (`_replace_body`, #727/#743/#758)."""
    item_id, _line = _comments_only(repo)
    with pytest.raises(InputRefused, match="runs over"):
        repo.service().update_item(item_id, description="New body", commit=False)


def test_duplicate_id_refusal_is_input_refused(repo: Repo) -> None:
    """The same ID on both boards: a write to it is ambiguous (#742)."""
    dup = repo.root / "research" / "hypotheses" / "EXP-2-copy.md"
    dup.write_text(repo.path("EXP-2").read_text(encoding="utf-8"), encoding="utf-8")
    svc = repo.service()
    with pytest.raises(InputRefused, match="more than one board"):
        svc.update_item("EXP-2", title="New", commit=False)


def test_normalize_priority_non_string_is_input_refused() -> None:
    with pytest.raises(InputRefused):
        KanbanService._normalize_priority(1)


def test_wip_limit_refusal_is_input_refused(tmp_path: Path, monkeypatch) -> None:
    root = _repo_with(tmp_path, monkeypatch, "    wip_limits:\n      in_progress: 1\n")
    svc = _svc(root)
    svc.move_item("EXP-1", WorkItemStatus.IN_PROGRESS, commit=False, validate_workflow=False)
    with pytest.raises(InputRefused, match="WIP limit reached"):
        svc.move_item("EXP-2", WorkItemStatus.IN_PROGRESS, commit=False, validate_workflow=False)


def test_per_type_wip_limit_refusal_is_input_refused(tmp_path: Path, monkeypatch) -> None:
    root = _repo_with(
        tmp_path, monkeypatch, "    wip_limits:\n      in_progress:\n        expedition: 1\n"
    )
    svc = _svc(root)
    svc.move_item("EXP-1", WorkItemStatus.IN_PROGRESS, commit=False, validate_workflow=False)
    with pytest.raises(InputRefused, match="WIP limit reached for expeditions"):
        svc.move_item("EXP-2", WorkItemStatus.IN_PROGRESS, commit=False, validate_workflow=False)


def test_gate_failure_is_input_refused(tmp_path: Path, monkeypatch) -> None:
    root = _repo_with(
        tmp_path,
        monkeypatch,
        '    gates:\n      "* -> in_progress":\n        - id: need_resolution\n'
        "          check: item.resolution\n"
        '          message: "Resolution required"\n',
    )
    with pytest.raises(InputRefused, match="Gate check failed"):
        _svc(root).move_item(
            "EXP-1", WorkItemStatus.IN_PROGRESS, commit=False, validate_workflow=False
        )


def test_run_config_not_a_mapping_is_input_refused(repo: Repo) -> None:
    run = repo.root / "runs" / "run-1"
    run.mkdir(parents=True)
    (run / "config.yaml").write_text("- a\n- b\n")
    with pytest.raises(InputRefused, match="not a mapping"):
        repo.service().update_run_status(run, "running")


@pytest.mark.parametrize(
    "parse",
    [
        pytest.param(lambda: WorkItemStatus.from_string("sideways"), id="status"),
        pytest.param(lambda: WorkItemType.from_string("gizmo"), id="type"),
    ],
)
def test_from_string_unknown_is_input_refused(parse) -> None:
    with pytest.raises(InputRefused, match="Unknown"):
        parse()


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("- a\n- b\n", id="not-a-mapping"),
        pytest.param("kanban:\n  paths: 5\n", id="paths-not-a-mapping"),
        pytest.param("kanban:\n  paths:\n    scan_paths: 5\n", id="scan-paths-not-a-list"),
        pytest.param("kanban:\n  paths:\n    scan_paths: [1]\n", id="scan-path-not-a-string"),
        pytest.param("kanban:\n  paths:\n    ignore: 5\n", id="ignore-not-a-list"),
        pytest.param("kanban:\n  theme: [a]\n", id="theme-not-a-string"),
    ],
)
def test_bad_config_is_input_refused(tmp_path: Path, text: str) -> None:
    path = tmp_path / ".kanban" / "config.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(text)
    config_mod._theme_cache.clear()
    with pytest.raises(InputRefused):
        KanbanConfig.load(path)


# ---------------------------------------------------------------------------
# 2. MCP: a refusal warns once; anything else is a bug with a traceback
# ---------------------------------------------------------------------------


@pytest.fixture
def mcp_log(caplog: pytest.LogCaptureFixture) -> Iterator[pytest.LogCaptureFixture]:
    log = mcp_server.logger
    log.addHandler(caplog.handler)
    caplog.handler.setLevel(logging.DEBUG)
    old = log.level
    log.setLevel(logging.DEBUG)
    try:
        yield caplog
    finally:
        log.setLevel(old)
        log.removeHandler(caplog.handler)


def _records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    """The MCP logger's WARNING-or-higher records (the handler is attached twice
    through propagation otherwise, so dedupe by identity)."""
    seen: dict[int, logging.LogRecord] = {}
    for r in caplog.records:
        if r.name == mcp_server.logger.name and r.levelno >= logging.WARNING:
            seen[id(r)] = r
    return list(seen.values())


def _assert_one_warning(caplog: pytest.LogCaptureFixture) -> None:
    recs = _records(caplog)
    assert [r.levelno for r in recs] == [logging.WARNING], [
        (r.levelname, r.getMessage()) for r in recs
    ]
    assert recs[0].exc_info is None, recs[0].getMessage()


@pytest.mark.parametrize(
    ("tool", "args", "needle"),
    [
        pytest.param(
            "kanban_update_item", {"item_id": "EXP-99", "title": "x"}, "not found", id="unknown-id"
        ),
        pytest.param(
            "kanban_update_item",
            {"item_id": "EXP-5", "depends_on": ["EXP-5"]},
            "itself",
            id="self-dep",
        ),
        pytest.param(
            "kanban_update_item",
            {"item_id": "EXP-5", "description": "a\n```\nb"},
            "fence",
            id="fence",
        ),
    ],
)
def test_mcp_refusal_warns_once_without_traceback(
    repo: Repo, mcp_log, tool: str, args: dict, needle: str
) -> None:
    """Control: already so (#728); must stay so once only InputRefused is a refusal."""
    out = _mcp(repo).handle_tool_call(tool, args)
    assert needle in out.get("error", ""), out
    _assert_one_warning(mcp_log)


def test_mcp_illegal_move_warns_once_without_traceback(repo: Repo, mcp_log) -> None:
    """Control: provisioning (ready) -> arrived (done) is refused, one warning."""
    out = _mcp(repo).handle_tool_call(
        "kanban_move_item", {"item_id": "EXP-5", "new_status": "arrived"}
    )
    assert "Illegal move" in out.get("error", ""), out
    _assert_one_warning(mcp_log)


def test_mcp_list_unknown_status_warns_once(repo: Repo, mcp_log) -> None:
    """`from_string` on a bad filter: a refusal, not a bug."""
    out = _mcp(repo).handle_tool_call("kanban_list_items", {"status": "sideways"})
    assert "Unknown status" in out.get("error", ""), out
    _assert_one_warning(mcp_log)


@pytest.mark.parametrize(
    ("method", "tool", "args"),
    [
        pytest.param(
            "update_item", "kanban_update_item", {"item_id": "EXP-5", "title": "New"}, id="update"
        ),
        pytest.param(
            "move_item",
            "kanban_move_item",
            {"item_id": "EXP-5", "new_status": "in_progress"},
            id="move",
        ),
    ],
)
def test_mcp_plain_value_error_is_a_bug_with_traceback(
    repo: Repo, mcp_log, monkeypatch: pytest.MonkeyPatch, method: str, tool: str, args: dict
) -> None:
    def boom(self: KanbanService, *a: object, **kw: object) -> None:
        raise ValueError("boom")

    monkeypatch.setattr(KanbanService, method, boom)
    out = _mcp(repo).handle_tool_call(tool, args)
    assert out.get("error") == "boom", out
    recs = _records(mcp_log)
    errors = [r for r in recs if r.levelno >= logging.ERROR]
    assert errors, (
        "a plain ValueError (a bug) was logged as a refusal: "
        f"{[(r.levelname, r.getMessage()) for r in recs]}"
    )
    assert errors[0].exc_info is not None, "the bug's ERROR record carries no traceback"
    assert not [r for r in recs if r.levelno == logging.WARNING], "a bug is not a refusal"


def test_mcp_valid_call_logs_nothing(repo: Repo, mcp_log) -> None:
    """Control: a valid update succeeds and logs no warning or error."""
    out = _mcp(repo).handle_tool_call("kanban_update_item", {"item_id": "EXP-5", "title": "New"})
    assert out.get("success"), out
    assert not _records(mcp_log), [r.getMessage() for r in _records(mcp_log)]


# ---------------------------------------------------------------------------
# Controls: valid service calls still succeed; CLI refusals stay one-line errors
# ---------------------------------------------------------------------------


def test_valid_service_calls_succeed(repo: Repo) -> None:
    svc = repo.service()
    item = svc.update_item(
        "EXP-5", title="Renamed", priority="high", tags=["a"], add_depends_on=["EXP-1"],
        commit=False,
    )
    assert item.title == "Renamed" and "EXP-1" in repo.deps("EXP-5")
    assert svc.rank_item("EXP-5", 1, commit=False).id == "EXP-5"
    assert WorkItemStatus.from_string("in-progress") is WorkItemStatus.IN_PROGRESS
    assert WorkItemType.from_string("Expedition") is WorkItemType.EXPEDITION


@pytest.mark.parametrize(
    ("args", "needle"),
    [
        pytest.param(["update", "EXP-99", "--title", "x"], "not found", id="not-found"),
        pytest.param(["update", "EXP-5", "--add-dep", "EXP-5"], "itself", id="self-dep"),
        pytest.param(["update", "EXP-1", "--add-dep", "EXP-4"], "cycle", id="cycle"),
        pytest.param(["update", "EXP-5", "--title", " "], "title is empty", id="empty-title"),
    ],
)
def test_cli_refusal_is_one_line_error(repo: Repo, args: list[str], needle: str) -> None:
    result = CliRunner().invoke(main, args)
    assert result.exit_code == 1, (result.output, repr(result.exception))
    assert isinstance(result.exception, SystemExit), repr(result.exception)
    assert "Traceback" not in result.output, result.output
    assert "Error:" in result.output and needle in " ".join(result.output.split()), result.output


# ---------------------------------------------------------------------------
# 3. structural: src raises no bare ValueError outside the allow-list
# ---------------------------------------------------------------------------

# (file relative to src/yurtle_kanban, enclosing function's qualified name) -> reason.
# Keep this minimal: an entry must be a raise no user input can reach as a refusal.
ALLOWED: dict[tuple[str, str], str] = {
    ("cli.py", "board_add"): (
        "a bare `raise ValueError` used as control flow in `board add`'s --wip-limit parse: "
        "it is caught by the `except ValueError` on the next line, which prints the "
        "refusal and exits; it never leaves the function"
    ),
    ("service.py", "KanbanService._id_list"): (
        "a programming-error type guard: a caller passing a str where a list of IDs is "
        "expected. The CLI builds lists and MCP refuses a non-array first "
        "(`_check_string_lists`, #719), so no user input reaches it; a bug keeps its "
        "traceback"
    ),
}


def _raises_of_value_error() -> Iterator[tuple[str, str, int]]:
    """(file, enclosing qualname, line) of every `raise ValueError` / `raise
    ValueError(...)` under src/yurtle_kanban."""
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

        def walk(node: ast.AST, scope: list[str]) -> Iterator[tuple[str, str, int]]:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                    yield from walk(child, [*scope, child.name])
                    continue
                if isinstance(child, ast.Raise) and child.exc is not None:
                    exc = child.exc.func if isinstance(child.exc, ast.Call) else child.exc
                    if isinstance(exc, ast.Name) and exc.id == "ValueError":
                        yield rel, ".".join(scope), child.lineno
                yield from walk(child, scope)

        yield from walk(tree, [])


def test_structural_walker_finds_raises() -> None:
    """Control: the walker sees the allow-listed raises, so an empty result below
    is not a walker that finds nothing."""
    found = {(f, q) for f, q, _ in _raises_of_value_error()}
    assert set(ALLOWED) <= found, sorted(set(ALLOWED) - found)


def test_no_bare_value_error_outside_allow_list() -> None:
    stray = [
        f"{f}:{line} in {q or '<module>'}"
        for f, q, line in _raises_of_value_error()
        if (f, q) not in ALLOWED
    ]
    assert not stray, (
        "raise InputRefused for a user refusal (or allow-list a genuinely internal "
        "raise with its reason):\n  " + "\n  ".join(stray)
    )


def test_allow_listed_value_error_still_propagates(repo: Repo) -> None:
    """Control for `_id_list`'s entry: a str is a caller bug, a plain ValueError."""
    with pytest.raises(ValueError) as info:
        KanbanService._id_list("EXP-1")  # type: ignore[arg-type]
    assert not isinstance(info.value, InputRefused)

