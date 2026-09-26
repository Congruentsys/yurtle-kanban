"""MCP server replies use the JSON-RPC 2.0 envelope (#563).

Every success reply must be exactly `{"jsonrpc", "id", "result"}` with the
payload under `result`, nothing at top level. Errors stay
`{"jsonrpc", "id", "error": {"code", "message"}}`. Notifications (no `id`)
get no reply at all.
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


def _one_success(monkeypatch, tmp_path: Path, request: dict[str, Any]) -> dict[str, Any]:
    replies = _run(monkeypatch, tmp_path, request)
    assert len(replies) == 1, f"expected one reply, got {replies!r}"
    reply = replies[0]
    assert set(reply) == {"jsonrpc", "id", "result"}, (
        f"success reply must be exactly jsonrpc/id/result, got keys {sorted(reply)}"
    )
    assert reply["jsonrpc"] == "2.0"
    assert reply["id"] == request["id"]
    assert isinstance(reply["result"], dict)
    return reply["result"]


def test_initialize_payload_is_under_result(monkeypatch, tmp_path):
    result = _one_success(
        monkeypatch,
        tmp_path,
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
    )
    assert "protocolVersion" in result
    assert "capabilities" in result
    assert result["serverInfo"]["name"] == "yurtle-kanban"
    assert isinstance(result["serverInfo"]["version"], str)


def test_tools_list_payload_is_under_result(monkeypatch, tmp_path):
    result = _one_success(
        monkeypatch,
        tmp_path,
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    )
    names = {tool["name"] for tool in result["tools"]}
    assert "kanban_list_items" in names


def test_tools_call_payload_is_under_result(monkeypatch, tmp_path):
    result = _one_success(
        monkeypatch,
        tmp_path,
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "kanban_list_items", "arguments": {}},
        },
    )
    content = result["content"]
    assert content and content[0]["type"] == "text"
    payload = json.loads(content[0]["text"])
    assert payload["count"] == 0


def test_string_id_is_echoed(monkeypatch, tmp_path):
    result = _one_success(
        monkeypatch,
        tmp_path,
        {"jsonrpc": "2.0", "id": "abc", "method": "tools/list", "params": {}},
    )
    assert "tools" in result


def test_unknown_method_is_an_error_envelope(monkeypatch, tmp_path):
    replies = _run(monkeypatch, tmp_path, {"jsonrpc": "2.0", "id": 4, "method": "bogus"})
    assert len(replies) == 1, f"expected one reply, got {replies!r}"
    reply = replies[0]
    assert set(reply) == {"jsonrpc", "id", "error"}
    assert reply["jsonrpc"] == "2.0"
    assert reply["id"] == 4
    assert isinstance(reply["error"]["code"], int)
    assert reply["error"]["code"] == -32601
    assert isinstance(reply["error"]["message"], str)


def test_notification_gets_no_reply(monkeypatch, tmp_path):
    replies = _run(
        monkeypatch,
        tmp_path,
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
    )
    assert replies == []


def test_notification_between_requests_is_silent(monkeypatch, tmp_path):
    replies = _run(
        monkeypatch,
        tmp_path,
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    )
    assert [r.get("id") for r in replies] == [1, 2]
    for reply in replies:
        assert set(reply) == {"jsonrpc", "id", "result"}
