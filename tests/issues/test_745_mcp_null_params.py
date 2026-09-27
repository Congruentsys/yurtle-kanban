"""A `tools/call` with null or non-object `params` is not an internal error (#745).

`handle_request` did `params = request.get("params", {})` then `params.get(...)`,
so `"params": null` (or an array/string/number) raised AttributeError: the client
got -32603 and the server logged a traceback.

Spec ([steer] on #745):
- null or missing `params` means no params: the call reaches `handle_tool_call`
  with no name, and the client gets a normal tool result marked `isError`
  ("Unknown tool"), never a -32603.
- an array, string or number `params` is a JSON-RPC -32602 whose message
  contains "params must be an object".
- neither logs a traceback (no ERROR record with exc_info on the server logger).
- a notification (no `id`) with bad params is still never answered.
"""

from __future__ import annotations

import io
import json
import logging
import sys
from pathlib import Path
from typing import Any

import pytest

from yurtle_kanban.mcp import server as mcp_server

SERVER_LOGGER = "yurtle-kanban-mcp"


def _run(monkeypatch, tmp_path: Path, *requests: dict[str, Any]) -> list[dict[str, Any]]:
    """Pipe JSON-RPC requests through the real run_server loop; return the replies."""
    monkeypatch.chdir(tmp_path)  # a scratch, empty board
    stdin = io.StringIO("".join(json.dumps(r) + "\n" for r in requests))
    stdout = io.StringIO()
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stdout", stdout)

    mcp_server.run_server()

    return [json.loads(ln) for ln in stdout.getvalue().splitlines() if ln.strip()]


def _tracebacks(caplog) -> list[logging.LogRecord]:
    return [
        r
        for r in caplog.records
        if r.name == SERVER_LOGGER and r.levelno >= logging.ERROR and r.exc_info
    ]


def _only_reply(replies: list[dict[str, Any]], req_id: Any) -> dict[str, Any]:
    assert len(replies) == 1, f"expected one reply, got {replies!r}"
    reply = replies[0]
    assert reply["jsonrpc"] == "2.0"
    assert reply["id"] == req_id
    return reply


def _assert_unknown_tool_result(reply: dict[str, Any]) -> None:
    assert "error" not in reply, f"expected a tool result, got protocol error {reply!r}"
    assert set(reply) == {"jsonrpc", "id", "result"}, reply
    result = reply["result"]
    assert result.get("isError") is True, result
    text = result["content"][0]["text"]
    assert "Unknown tool" in text, text


# --- null / missing params: no params, an isError tool result -------------------


def test_null_params_is_an_unknown_tool_result(monkeypatch, tmp_path, caplog):
    caplog.set_level(logging.DEBUG, logger=SERVER_LOGGER)
    replies = _run(
        monkeypatch,
        tmp_path,
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": None},
    )
    _assert_unknown_tool_result(_only_reply(replies, 1))
    assert _tracebacks(caplog) == []


def test_missing_params_is_an_unknown_tool_result(monkeypatch, tmp_path, caplog):
    caplog.set_level(logging.DEBUG, logger=SERVER_LOGGER)
    replies = _run(
        monkeypatch,
        tmp_path,
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call"},
    )
    _assert_unknown_tool_result(_only_reply(replies, 2))
    assert _tracebacks(caplog) == []


def test_null_params_logs_no_traceback(monkeypatch, tmp_path, caplog):
    caplog.set_level(logging.DEBUG, logger=SERVER_LOGGER)
    _run(
        monkeypatch,
        tmp_path,
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": None},
    )
    assert _tracebacks(caplog) == [], [r.getMessage() for r in _tracebacks(caplog)]


# --- non-object params: -32602 ---------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [["kanban_list_items"], "kanban_list_items", 42, 1.5, True],
    ids=["array", "string", "int", "float", "bool"],
)
def test_non_object_params_is_invalid_params(monkeypatch, tmp_path, caplog, bad):
    caplog.set_level(logging.DEBUG, logger=SERVER_LOGGER)
    replies = _run(
        monkeypatch,
        tmp_path,
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": bad},
    )
    reply = _only_reply(replies, 4)
    assert set(reply) == {"jsonrpc", "id", "error"}, reply
    assert reply["error"]["code"] == -32602, reply
    assert "params must be an object" in reply["error"]["message"], reply
    assert _tracebacks(caplog) == [], [r.getMessage() for r in _tracebacks(caplog)]


# --- notifications with bad params stay silent -----------------------------------


@pytest.mark.parametrize("bad", [None, ["x"], "x", 7], ids=["null", "array", "string", "int"])
def test_notification_with_bad_params_is_not_answered(monkeypatch, tmp_path, caplog, bad):
    caplog.set_level(logging.DEBUG, logger=SERVER_LOGGER)
    replies = _run(
        monkeypatch,
        tmp_path,
        {"jsonrpc": "2.0", "method": "tools/call", "params": bad},
    )
    assert replies == []
    assert _tracebacks(caplog) == [], [r.getMessage() for r in _tracebacks(caplog)]


def test_bad_params_costs_only_itself(monkeypatch, tmp_path):
    """The requests after a bad-params call are answered normally."""
    replies = _run(
        monkeypatch,
        tmp_path,
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": None},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": [1]},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}},
    )
    assert [r["id"] for r in replies] == [1, 2, 3]
    _assert_unknown_tool_result(replies[0])
    assert replies[1]["error"]["code"] == -32602
    assert "tools" in replies[2]["result"]


# --- controls ---------------------------------------------------------------------


def test_control_normal_tools_call_still_works(monkeypatch, tmp_path, caplog):
    caplog.set_level(logging.DEBUG, logger=SERVER_LOGGER)
    replies = _run(
        monkeypatch,
        tmp_path,
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "kanban_list_items", "arguments": {}},
        },
    )
    reply = _only_reply(replies, 5)
    assert set(reply) == {"jsonrpc", "id", "result"}, reply
    assert "isError" not in reply["result"]
    assert json.loads(reply["result"]["content"][0]["text"])["count"] == 0
    assert _tracebacks(caplog) == []


def test_control_unknown_method_is_still_32601(monkeypatch, tmp_path):
    replies = _run(
        monkeypatch,
        tmp_path,
        {"jsonrpc": "2.0", "id": 6, "method": "bogus", "params": None},
    )
    reply = _only_reply(replies, 6)
    assert reply["error"]["code"] == -32601, reply


def test_control_empty_object_params_is_unknown_tool(monkeypatch, tmp_path):
    replies = _run(
        monkeypatch,
        tmp_path,
        {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {}},
    )
    _assert_unknown_tool_result(_only_reply(replies, 7))
