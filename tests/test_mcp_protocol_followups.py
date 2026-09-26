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


# (4) round 2: a bad line never ends the session; parse/shape errors per spec --

TOOLS_LIST_2 = b'{"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}\n'


def _run_raw(
    monkeypatch, tmp_path: Path, raw: bytes
) -> tuple[list[dict[str, Any]], BaseException | None]:
    """Feed raw bytes as stdin (a strict UTF-8 text wrapper, like a real pipe).

    Returns the replies and whatever exception escaped run_server (None if the
    loop ended normally at EOF), so a crash fails by assertion, not as an error.
    """
    monkeypatch.chdir(tmp_path)
    stdin = io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8")
    stdout = io.StringIO()
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stdout", stdout)

    crash: BaseException | None = None
    try:
        mcp_server.run_server()
    except Exception as e:  # noqa: BLE001 - the crash itself is what we assert on
        crash = e

    replies = [json.loads(ln) for ln in stdout.getvalue().splitlines() if ln.strip()]
    return replies, crash


def _assert_survives(replies: list[dict[str, Any]], crash: BaseException | None) -> None:
    assert crash is None, f"a bad line ended the session: {type(crash).__name__}: {crash}"
    answered = [r for r in replies if r.get("id") == 2]
    assert len(answered) == 1, f"tools/list after the bad line was not answered: {replies!r}"
    assert "tools" in answered[0]["result"]
    # anything else sent back is an error about the bad line, with id null
    for reply in replies:
        if reply is not answered[0]:
            assert set(reply) == {"jsonrpc", "id", "error"}, reply
            assert reply["id"] is None


def test_invalid_utf8_line_does_not_end_session(monkeypatch, tmp_path):
    replies, crash = _run_raw(monkeypatch, tmp_path, b"\xff\xfe bad\n" + TOOLS_LIST_2)
    _assert_survives(replies, crash)


def test_invalid_utf8_last_line_does_not_crash(monkeypatch, tmp_path):
    _replies, crash = _run_raw(monkeypatch, tmp_path, b"\xff\xfe bad\n")
    assert crash is None, f"{type(crash).__name__}: {crash}"


def test_deeply_nested_line_does_not_end_session(monkeypatch, tmp_path):
    replies, crash = _run_raw(monkeypatch, tmp_path, b"[" * 100_000 + b"\n" + TOOLS_LIST_2)
    _assert_survives(replies, crash)


def test_malformed_json_does_not_end_session(monkeypatch, tmp_path):
    replies, crash = _run_raw(
        monkeypatch, tmp_path, b'{"jsonrpc": "2.0", "id": 1,\n' + TOOLS_LIST_2
    )
    _assert_survives(replies, crash)


def test_malformed_json_gets_parse_error(monkeypatch, tmp_path):
    replies, crash = _run_raw(monkeypatch, tmp_path, b"{not json\n")
    assert crash is None, f"{type(crash).__name__}: {crash}"
    assert len(replies) == 1, f"expected one -32700 reply, got {replies!r}"
    reply = replies[0]
    assert set(reply) == {"jsonrpc", "id", "error"}
    assert reply["jsonrpc"] == "2.0"
    assert reply["id"] is None
    assert reply["error"]["code"] == -32700
    assert isinstance(reply["error"]["message"], str)


def test_parse_error_then_request_both_answered(monkeypatch, tmp_path):
    replies, crash = _run_raw(monkeypatch, tmp_path, b"{not json\n" + TOOLS_LIST_2)
    assert crash is None, f"{type(crash).__name__}: {crash}"
    assert [r["id"] for r in replies] == [None, 2]
    assert replies[0]["error"]["code"] == -32700
    assert "result" in replies[1]


def _assert_invalid_request(monkeypatch, tmp_path: Path, line: bytes) -> None:
    replies, crash = _run_raw(monkeypatch, tmp_path, line + b"\n")
    assert crash is None, f"{type(crash).__name__}: {crash}"
    assert len(replies) == 1, f"expected one -32600 reply, got {replies!r}"
    reply = replies[0]
    assert set(reply) == {"jsonrpc", "id", "error"}
    assert reply["jsonrpc"] == "2.0"
    assert reply["id"] is None
    assert reply["error"]["code"] == -32600
    assert isinstance(reply["error"]["message"], str)


def test_array_is_invalid_request(monkeypatch, tmp_path):
    # batches stay unsupported; one -32600 reply for the whole array
    _assert_invalid_request(monkeypatch, tmp_path, b"[1, 2, 3]")


def test_number_is_invalid_request(monkeypatch, tmp_path):
    _assert_invalid_request(monkeypatch, tmp_path, b"42")


def test_string_is_invalid_request(monkeypatch, tmp_path):
    _assert_invalid_request(monkeypatch, tmp_path, b'"s"')
