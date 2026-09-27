# ruff: noqa: F811  -- the `repo` fixture imported from #576's module is re-bound as an arg
"""#768: an empty (or whitespace-only) required MCP argument is '<arg> is required'.

Spec ([steer] on #768): since #735 `handle_tool_call` refuses a missing or null required
argument with `{"error": "<arg> is required"}`. An empty string `""` -- and a
whitespace-only one (`"   "`, `"\\t"`) -- is refused the same way, for every
(tool, required arg) pair. No traceback is logged; nothing is written or committed.

Controls: valid calls still work; a non-empty value with surrounding spaces that a tool
accepts today is not refused as required; a non-string `item_id` is still
"item_id must be a string"; missing and null are still "<arg> is required".

The (tool, arg) table, the VALID argument sets and the no-write checks come from #735.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from tests.issues.test_576_cli_update_deps import Repo, repo  # noqa: F401
from tests.issues.test_735_mcp_required_args import (
    PAIRS,
    REQUIRED,
    VALID,
    _call_without,
    _mcp,
    _status,
)
from yurtle_kanban.mcp import server as mcp_server

BLANKS = ["", "   ", "\t"]
BLANK_IDS = ["empty", "spaces", "tab"]


def _call_with(repo: Repo, caplog, tool: str, arg: str, value: Any) -> dict[str, Any]:
    """Call `tool` with every required arg valid except `arg`, which is `value`.

    Asserts the refusal, no traceback / ERROR record, and no file, commit or tree change.
    """
    args: dict[str, Any] = {a: VALID[a] for a in REQUIRED[tool]}
    args[arg] = value
    before, head, status = repo.snapshot(), repo.head(), _status(repo)
    log = mcp_server.logger  # "yurtle-kanban-mcp"
    log.addHandler(caplog.handler)
    caplog.set_level(logging.DEBUG, logger=log.name)
    try:
        out = _mcp(repo).handle_tool_call(tool, args)
    finally:
        log.removeHandler(caplog.handler)
    tracebacks = [r.getMessage() for r in caplog.records if r.exc_info is not None]
    assert not tracebacks, f"logged a traceback: {tracebacks}"
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR], [
        r.getMessage() for r in caplog.records
    ]
    assert repo.snapshot() == before, f"a refused call wrote a file: {out}"
    assert repo.head() == head, f"a refused call committed: {out}"
    assert _status(repo) == status, f"a refused call changed the working tree: {out}"
    assert out == {"error": f"{arg} is required"}, out
    return out


@pytest.mark.parametrize("value", BLANKS, ids=BLANK_IDS)
@pytest.mark.parametrize(("tool", "arg"), PAIRS, ids=[f"{t}-{a}" for t, a in PAIRS])
def test_blank_required_arg_is_refused(
    repo: Repo, caplog, tool: str, arg: str, value: str
) -> None:
    _call_with(repo, caplog, tool, arg, value)


def test_every_schema_required_arg_is_refused_when_empty(repo: Repo) -> None:
    """Driven by the live schema, not the hard-coded list: a newly required arg is covered."""
    mcp = _mcp(repo)
    for tool in mcp.get_tools():
        required = tool["inputSchema"].get("required", [])
        for arg in required:
            if arg not in VALID:
                pytest.fail(f"no valid value known for {tool['name']}.{arg}: extend VALID")
            for blank in BLANKS:
                args = {a: VALID[a] for a in required}
                args[arg] = blank
                out = mcp.handle_tool_call(tool["name"], args)
                assert out == {"error": f"{arg} is required"}, (tool["name"], arg, blank, out)


# --- controls: these pass on the current src and must keep passing ---


def test_a_full_valid_call_still_works(repo: Repo) -> None:
    out = _mcp(repo).handle_tool_call("kanban_get_item", {"item_id": "EXP-5"})
    assert "error" not in out, out
    assert out.get("item"), out


def test_a_padded_non_empty_title_is_not_refused_as_required(repo: Repo) -> None:
    """A title " x " is accepted today; it is not blank, so it is not 'title is required'."""
    out = _mcp(repo).handle_tool_call(
        "kanban_create_item", {"item_type": "expedition", "title": " x "}
    )
    assert out.get("error") != "title is required", out
    assert "error" not in out, out


def test_a_padded_non_empty_comment_is_not_refused_as_required(repo: Repo) -> None:
    out = _mcp(repo).handle_tool_call(
        "kanban_add_comment", {"item_id": "EXP-5", "comment": "  hello  "}
    )
    assert out.get("error") != "comment is required", out
    assert "error" not in out, out


@pytest.mark.parametrize(("tool", "key"), [("kanban_get_item", "item_id"), ("kanban_next_id", "prefix")])
def test_non_string_id_is_still_a_type_error(repo: Repo, tool: str, key: str) -> None:
    out = _mcp(repo).handle_tool_call(tool, {key: 5})
    assert out == {"error": f"{key} must be a string"}, out


@pytest.mark.parametrize("null", [False, True], ids=["missing", "null"])
@pytest.mark.parametrize(("tool", "arg"), PAIRS, ids=[f"{t}-{a}" for t, a in PAIRS])
def test_missing_and_null_are_still_required(
    repo: Repo, caplog, tool: str, arg: str, null: bool
) -> None:
    _call_without(repo, caplog, tool, arg, null=null)
