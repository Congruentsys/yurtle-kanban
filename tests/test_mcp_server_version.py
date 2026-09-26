"""The MCP server's `initialize` reply reports the package version (#561).

`serverInfo.version` was hardcoded to "0.1.0". It must be the single source
of truth, `yurtle_kanban.__version__`, which in turn matches pyproject.toml.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import yurtle_kanban
from yurtle_kanban.mcp import server as mcp_server

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def _initialize(monkeypatch, tmp_path: Path) -> dict:
    """Pipe one JSON-RPC initialize request through run_server; return its `result`."""
    monkeypatch.chdir(tmp_path)
    request = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    stdin = io.StringIO(json.dumps(request) + "\n")
    stdout = io.StringIO()
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stdout", stdout)

    mcp_server.run_server()

    lines = [ln for ln in stdout.getvalue().splitlines() if ln.strip()]
    assert len(lines) == 1, f"expected one reply, got {lines!r}"
    reply = json.loads(lines[0])
    assert reply.get("id") == 1
    # JSON-RPC 2.0 envelope (#563/#567): the payload lives under `result`.
    assert "result" in reply, f"reply has no `result` envelope: {reply!r}"
    assert "serverInfo" not in reply, "serverInfo must be inside `result`, not top level"
    result = reply["result"]
    assert "serverInfo" in result, f"`result` has no serverInfo: {result!r}"
    return result


def test_initialize_reports_package_version(monkeypatch, tmp_path):
    result = _initialize(monkeypatch, tmp_path)
    assert result["serverInfo"]["name"] == "yurtle-kanban"
    assert result["serverInfo"]["version"] == yurtle_kanban.__version__


def test_initialize_version_matches_pyproject(monkeypatch, tmp_path):
    pyproject_version = tomllib.loads(PYPROJECT.read_text())["project"]["version"]
    result = _initialize(monkeypatch, tmp_path)
    assert result["serverInfo"]["version"] == pyproject_version
