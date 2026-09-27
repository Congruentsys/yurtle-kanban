# ruff: noqa: F811  -- the `repo` fixture imported from #576's module is re-bound as an arg
"""#735: a tool call missing a required argument is refused as '<arg> is required'.

Spec ([steer] on #735): `handle_tool_call` reads each tool's schema `required` list and,
before any handler runs, refuses a missing or null required argument with
`{"error": "<arg> is required"}`. The refusal is an expected one: at most a one-line
warning, no ERROR record with a traceback. Nothing is written or committed. The type
checks that already exist are unchanged; an unknown tool is still reported first; an
argument that is not required may be omitted.

The (tool, arg) pairs are read from `get_tools()` at test time AND hard-coded below, so
a schema change is noticed.
"""

from __future__ import annotations

import logging
import subprocess
from typing import Any

import pytest

from tests.issues.test_576_cli_update_deps import Repo, repo  # noqa: F401
from yurtle_kanban.mcp import server as mcp_server

REQUIRED: dict[str, list[str]] = {
    "kanban_get_item": ["item_id"],
    "kanban_create_item": ["item_type", "title"],
    "kanban_move_item": ["item_id", "new_status"],
    "kanban_get_my_items": ["assignee"],
    "kanban_add_comment": ["item_id", "comment"],
    "kanban_update_item": ["item_id"],
    "kanban_next_id": ["prefix"],
}

# a valid value for every required argument, so only the one under test is missing
VALID: dict[str, str] = {
    "item_id": "EXP-5",
    "item_type": "expedition",
    "title": "A new thing",
    "new_status": "underway",  # a nautical status
    "assignee": "tester",
    "comment": "a comment",
    "prefix": "EXP",
}

PAIRS = [(tool, arg) for tool, args in REQUIRED.items() for arg in args]


def _mcp(repo: Repo):
    return mcp_server.KanbanMCPServer(repo_root=repo.root)


def _status(repo: Repo) -> str:
    return subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo.root, capture_output=True, text=True, check=True
    ).stdout


def test_schema_required_lists_match_the_hard_coded_list(tmp_path) -> None:
    tools = mcp_server.KanbanMCPServer(repo_root=tmp_path).get_tools()
    schema = {
        t["name"]: list(t["inputSchema"].get("required", []))
        for t in tools
        if t["inputSchema"].get("required")
    }
    assert schema == REQUIRED, schema


def _call_without(repo: Repo, caplog, tool: str, arg: str, *, null: bool) -> dict[str, Any]:
    args: dict[str, Any] = {a: VALID[a] for a in REQUIRED[tool] if a != arg}
    if null:
        args[arg] = None
    before, head, status = repo.snapshot(), repo.head(), _status(repo)
    log = mcp_server.logger  # "yurtle-kanban-mcp"
    log.addHandler(caplog.handler)
    caplog.set_level(logging.DEBUG, logger=log.name)
    try:
        out = _mcp(repo).handle_tool_call(tool, args)
    finally:
        log.removeHandler(caplog.handler)
    assert out == {"error": f"{arg} is required"}, out
    tracebacks = [r.getMessage() for r in caplog.records if r.exc_info is not None]
    assert not tracebacks, f"logged a traceback: {tracebacks}"
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR], [
        r.getMessage() for r in caplog.records
    ]
    assert repo.snapshot() == before, "a refused call wrote a file"
    assert repo.head() == head, "a refused call committed"
    assert _status(repo) == status, "a refused call changed the working tree"
    return out


@pytest.mark.parametrize(("tool", "arg"), PAIRS, ids=[f"{t}-{a}" for t, a in PAIRS])
def test_missing_required_arg_is_refused(repo: Repo, caplog, tool: str, arg: str) -> None:
    _call_without(repo, caplog, tool, arg, null=False)


@pytest.mark.parametrize(("tool", "arg"), PAIRS, ids=[f"{t}-{a}" for t, a in PAIRS])
def test_null_required_arg_is_refused(repo: Repo, caplog, tool: str, arg: str) -> None:
    _call_without(repo, caplog, tool, arg, null=True)


def test_every_schema_required_arg_is_refused_when_missing(repo: Repo, caplog) -> None:
    """Driven by the live schema, not the hard-coded list: a newly required arg is covered."""
    mcp = _mcp(repo)
    for tool in mcp.get_tools():
        required = tool["inputSchema"].get("required", [])
        for arg in required:
            if arg not in VALID:
                pytest.fail(f"no valid value known for {tool['name']}.{arg}: extend VALID")
            args = {a: VALID[a] for a in required if a != arg}
            out = mcp.handle_tool_call(tool["name"], args)
            assert out == {"error": f"{arg} is required"}, (tool["name"], arg, out)


# --- controls: these pass on the current src and must keep passing ---


def test_a_full_valid_call_still_works(repo: Repo) -> None:
    out = _mcp(repo).handle_tool_call("kanban_get_item", {"item_id": "EXP-5"})
    assert "error" not in out, out
    assert out.get("item"), out


@pytest.mark.parametrize(("tool", "key"), [("kanban_get_item", "item_id"), ("kanban_next_id", "prefix")])
def test_non_string_id_is_still_refused_as_before(repo: Repo, tool: str, key: str) -> None:
    out = _mcp(repo).handle_tool_call(tool, {key: 5})
    assert out.get("error") == f"{key} must be a string", out


def test_unknown_tool_is_still_reported_first(repo: Repo) -> None:
    out = _mcp(repo).handle_tool_call("kanban_bogus", {})
    assert "Unknown tool" in out.get("error", ""), out


def test_a_non_required_arg_may_be_omitted(repo: Repo) -> None:
    """update_item with only item_id + one optional field; the other optionals omitted."""
    out = _mcp(repo).handle_tool_call("kanban_update_item", {"item_id": "EXP-5", "title": "Renamed"})
    assert out.get("success") is True, out


def test_a_tool_with_no_required_args_takes_none(repo: Repo) -> None:
    out = _mcp(repo).handle_tool_call("kanban_get_board", {})
    assert "error" not in out, out
