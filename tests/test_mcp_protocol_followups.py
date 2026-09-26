"""MCP protocol follow-ups to the JSON-RPC envelope (#568).

- A tool call whose tool fails is still a JSON-RPC success, but its
  `result.isError` is true (MCP spec); a successful call has no true `isError`.
- A notification (no `id`) never gets a reply, known method or not; an
  unknown method sent as a request (with `id`) still gets -32601 with that `id`.
- A tool failure stays inside `result`; it never turns into a protocol `error`.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from typing import Any

from yurtle_kanban.mcp import server as mcp_server


def _run(monkeypatch, tmp_path: Path, *requests: dict[str, Any]) -> list[dict[str, Any]]:
    """Pipe JSON-RPC requests through the real run_server loop; return the replies."""
    monkeypatch.chdir(tmp_path)
    stdin = io.StringIO("".join(json.dumps(r) + "\n" for r in requests))
    stdout = io.StringIO()
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stdout", stdout)

    mcp_server.run_server()

    return [json.loads(ln) for ln in stdout.getvalue().splitlines() if ln.strip()]


def _call(name: str, arguments: dict[str, Any], rid: int = 1) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": rid,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }


def _one_result(replies: list[dict[str, Any]], rid: int) -> dict[str, Any]:
    assert len(replies) == 1, f"expected one reply, got {replies!r}"
    reply = replies[0]
    assert set(reply) == {"jsonrpc", "id", "result"}, (
        f"a tool call reply must be a JSON-RPC success, got keys {sorted(reply)}"
    )
    assert reply["id"] == rid
    return reply["result"]


def _tool_payload(result: dict[str, Any]) -> dict[str, Any]:
    content = result["content"]
    assert content and content[0]["type"] == "text"
    return json.loads(content[0]["text"])


# (1) isError on a failed tool call -------------------------------------------


def test_unknown_tool_sets_is_error(monkeypatch, tmp_path):
    replies = _run(monkeypatch, tmp_path, _call("kanban_no_such_tool", {}, rid=11))
    result = _one_result(replies, 11)
    assert "error" in _tool_payload(result)
    assert result.get("isError") is True


def test_missing_item_sets_is_error(monkeypatch, tmp_path):
    replies = _run(monkeypatch, tmp_path, _call("kanban_get_item", {"item_id": "EXP-999"}, rid=12))
    result = _one_result(replies, 12)
    assert "not found" in _tool_payload(result)["error"].lower()
    assert result.get("isError") is True


def test_tool_raising_sets_is_error(monkeypatch, tmp_path):
    # a missing required argument raises inside the tool; handle_tool_call
    # turns it into {"error": ...}
    replies = _run(monkeypatch, tmp_path, _call("kanban_get_item", {}, rid=13))
    result = _one_result(replies, 13)
    assert "error" in _tool_payload(result)
    assert result.get("isError") is True


def test_successful_call_is_not_an_error(monkeypatch, tmp_path):
    replies = _run(monkeypatch, tmp_path, _call("kanban_list_items", {}, rid=14))
    result = _one_result(replies, 14)
    assert _tool_payload(result)["count"] == 0
    assert result.get("isError", False) is False


# (2) notifications never get a reply ------------------------------------------


def test_unknown_notification_gets_no_reply(monkeypatch, tmp_path):
    replies = _run(
        monkeypatch,
        tmp_path,
        {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 1}},
    )
    assert replies == []


def test_unknown_method_without_id_gets_no_reply(monkeypatch, tmp_path):
    replies = _run(monkeypatch, tmp_path, {"jsonrpc": "2.0", "method": "bogus"})
    assert replies == []


def test_unknown_method_with_id_still_errors(monkeypatch, tmp_path):
    replies = _run(monkeypatch, tmp_path, {"jsonrpc": "2.0", "id": 7, "method": "bogus"})
    assert len(replies) == 1, f"expected one reply, got {replies!r}"
    reply = replies[0]
    assert set(reply) == {"jsonrpc", "id", "error"}
    assert reply["id"] == 7
    assert reply["error"]["code"] == -32601


def test_notifications_interleaved_only_requests_answered(monkeypatch, tmp_path):
    replies = _run(
        monkeypatch,
        tmp_path,
        {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 0}},
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        {"jsonrpc": "2.0", "method": "notifications/progress", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "bogus"},
    )
    assert [r.get("id") for r in replies] == [1, 2]
    assert "result" in replies[0]
    assert replies[1]["error"]["code"] == -32601


# (3) a tool failure never leaks out as a protocol error -----------------------


def test_tool_error_payload_stays_under_result(monkeypatch, tmp_path):
    # the tool's own payload is {"error": ...}; that must not be mistaken for
    # a JSON-RPC error reply
    replies = _run(monkeypatch, tmp_path, _call("kanban_no_such_tool", {}, rid=21))
    assert len(replies) == 1
    assert "error" not in replies[0]
    assert "result" in replies[0]
