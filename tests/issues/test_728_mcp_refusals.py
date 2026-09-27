# ruff: noqa: F811  -- the `repo` fixture imported from #576's module is re-bound as an arg
"""#728: an expected MCP refusal is logged as one warning line with no traceback;
string args are type-checked; an explicit null boolean means 'omitted'."""

from __future__ import annotations

import logging

import pytest

from tests.issues.test_576_cli_update_deps import Repo, repo  # noqa: F401
from yurtle_kanban.mcp import server as mcp_server


def _mcp(repo: Repo):
    return mcp_server.KanbanMCPServer(repo_root=repo.root)


def test_refusal_logs_no_traceback(repo: Repo, caplog) -> None:
    log = logging.getLogger("yurtle-kanban")
    log.addHandler(caplog.handler)
    try:
        out = _mcp(repo).handle_tool_call(
            "kanban_update_item", {"item_id": "EXP-2", "description": "a\n```\nb"}
        )
    finally:
        log.removeHandler(caplog.handler)
    assert "error" in out, out
    assert caplog.records, "the refusal is still logged"
    assert all(r.exc_info is None for r in caplog.records), [r.getMessage() for r in caplog.records]


@pytest.mark.parametrize(("tool", "key"), [("kanban_get_item", "item_id"), ("kanban_next_id", "prefix")])
def test_non_string_id_args_are_refused_clearly(repo: Repo, tool: str, key: str) -> None:
    out = _mcp(repo).handle_tool_call(tool, {key: 5})
    assert out.get("error") == f"{key} must be a string", out


def test_null_booleans_mean_omitted(repo: Repo) -> None:
    out = _mcp(repo).handle_tool_call(
        "kanban_update_item", {"item_id": "EXP-2", "depends_on": ["EXP-3"], "allow_unknown": None}
    )
    assert out.get("success"), out


def test_null_arguments_mean_no_arguments(repo: Repo) -> None:
    out = _mcp(repo).handle_tool_call("kanban_get_board", None)
    assert "error" not in out, out


@pytest.mark.parametrize("args", [[1, 2], "x", 5])
def test_non_object_arguments_are_refused_clearly(repo: Repo, args) -> None:
    out = _mcp(repo).handle_tool_call("kanban_get_board", args)
    assert out.get("error") == "arguments must be an object", out


def test_unknown_tool_is_reported_before_arg_types(repo: Repo) -> None:
    out = _mcp(repo).handle_tool_call("kanban_bogus", {"item_id": 5})
    assert "Unknown tool" in out.get("error", ""), out
