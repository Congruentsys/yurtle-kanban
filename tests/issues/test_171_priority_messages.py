"""#171: MCP priority validation must not crash on non-string values, and the
unknown-priority message is worded the same on every surface (service, MCP,
CLI `list --priority`)."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from tests.issues._snapshot import paths_outside_git
from yurtle_kanban.cli import main
from yurtle_kanban.config import KanbanConfig, PathConfig
from yurtle_kanban.models import WorkItemType
from yurtle_kanban.service import KanbanService

VALID_LIST = "valid: critical, high, medium, low"
NON_STRINGS: list[Any] = [1, 1.5, True, ["high"]]
NON_STRING_IDS = ["int", "float", "bool", "list"]


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "t@t.com"], cwd=tmp_path, capture_output=True, check=True
    )
    subprocess.run(
        ["git", "config", "user.name", "T"], cwd=tmp_path, capture_output=True, check=True
    )
    (tmp_path / ".kanban").mkdir()
    exp = tmp_path / "kanban-work" / "expeditions"
    exp.mkdir(parents=True)
    KanbanConfig(
        theme="nautical",
        paths=PathConfig(root="kanban-work/", scan_paths=["kanban-work/expeditions/"]),
    ).save(tmp_path / ".kanban" / "config.yaml")
    (exp / "EXP-001-High-Item.md").write_text(
        "---\nid: EXP-001\ntitle: High Item\nstatus: backlog\npriority: high\n---\n"
    )
    (exp / "EXP-002-Low-Item.md").write_text(
        "---\nid: EXP-002\ntitle: Low Item\nstatus: backlog\npriority: low\n---\n"
    )
    return tmp_path


def _snapshot(root: Path) -> dict[Path, str]:
    return {p: p.read_text() for p in paths_outside_git(root, ".md")}


def _server(root: Path):
    mcp_server = pytest.importorskip("yurtle_kanban.mcp.server")
    return mcp_server.KanbanMCPServer(repo_root=root)


def _names_value(message: str, value: Any) -> bool:
    lowered = message.lower()
    forms = {str(value), repr(value), json.dumps(value)}
    return any(f.lower() in lowered for f in forms)


def _assert_unknown_priority_message(message: str, value: str) -> None:
    assert re.search(r"Unknown priority:\s*['\"]?" + re.escape(value), message), message
    assert VALID_LIST in message, message


# --- 1. MCP rejects non-string priorities with the normal message -------------


class TestMcpNonStringPriority:
    @pytest.mark.parametrize("value", NON_STRINGS, ids=NON_STRING_IDS)
    def test_create_rejects_non_string(self, repo, monkeypatch, value):
        monkeypatch.chdir(repo)
        server = _server(repo)
        before = _snapshot(repo)

        result = server.handle_tool_call(
            "kanban_create_item",
            {"item_type": "feature", "title": "probe", "priority": value},
        )

        assert not result.get("success"), result
        error = result.get("error")
        assert error, result
        assert "has no attribute" not in error, error
        assert _names_value(error, value), error
        assert VALID_LIST in error, error
        assert _snapshot(repo) == before

    @pytest.mark.parametrize("value", NON_STRINGS, ids=NON_STRING_IDS)
    def test_update_rejects_non_string(self, repo, monkeypatch, value):
        monkeypatch.chdir(repo)
        server = _server(repo)
        before = _snapshot(repo)

        result = server.handle_tool_call(
            "kanban_update_item", {"item_id": "EXP-001", "priority": value}
        )

        assert not result.get("success"), result
        error = result.get("error")
        assert error, result
        assert "has no attribute" not in error, error
        assert _names_value(error, value), error
        assert VALID_LIST in error, error
        assert _snapshot(repo) == before


# --- 2. One wording everywhere -------------------------------------------------


class TestOneUnknownPriorityWording:
    def test_service_message(self, repo):
        svc = KanbanService(KanbanConfig.load(repo / ".kanban" / "config.yaml"), repo)
        with pytest.raises(ValueError) as exc:
            svc.create_item(item_type=WorkItemType.FEATURE, title="probe", priority="urgent")
        _assert_unknown_priority_message(str(exc.value), "urgent")

    def test_mcp_create_message(self, repo, monkeypatch):
        monkeypatch.chdir(repo)
        result = _server(repo).handle_tool_call(
            "kanban_create_item",
            {"item_type": "feature", "title": "probe", "priority": "urgent"},
        )
        assert not result.get("success"), result
        _assert_unknown_priority_message(result.get("error", ""), "urgent")

    def test_mcp_update_message(self, repo, monkeypatch):
        monkeypatch.chdir(repo)
        result = _server(repo).handle_tool_call(
            "kanban_update_item", {"item_id": "EXP-001", "priority": "urgent"}
        )
        assert not result.get("success"), result
        _assert_unknown_priority_message(result.get("error", ""), "urgent")

    def test_cli_list_message(self, repo, monkeypatch):
        monkeypatch.chdir(repo)
        result = CliRunner().invoke(main, ["list", "--priority", "urgent"])
        assert result.exit_code != 0, result.output
        _assert_unknown_priority_message(" ".join(result.output.split()), "urgent")


# --- negative controls ---------------------------------------------------------


class TestControls:
    @pytest.mark.parametrize("value", ["High", "HIGH"])
    def test_mcp_create_mixed_case_accepted(self, repo, monkeypatch, value):
        monkeypatch.chdir(repo)
        before = set(_snapshot(repo))
        result = _server(repo).handle_tool_call(
            "kanban_create_item",
            {"item_type": "feature", "title": "probe", "priority": value},
        )
        assert result.get("success"), result
        new = [p for p in _snapshot(repo) if p not in before]
        assert len(new) == 1
        assert "\npriority: high\n" in new[0].read_text()

    @pytest.mark.parametrize("value", ["High", "HIGH"])
    def test_mcp_update_mixed_case_accepted(self, repo, monkeypatch, value):
        monkeypatch.chdir(repo)
        path = repo / "kanban-work" / "expeditions" / "EXP-002-Low-Item.md"
        result = _server(repo).handle_tool_call(
            "kanban_update_item", {"item_id": "EXP-002", "priority": value}
        )
        assert result.get("success"), result
        assert "\npriority: high\n" in path.read_text()

    @pytest.mark.parametrize("value", ["critical", "high", "medium", "low"])
    def test_mcp_create_every_valid_value(self, repo, monkeypatch, value):
        monkeypatch.chdir(repo)
        before = set(_snapshot(repo))
        result = _server(repo).handle_tool_call(
            "kanban_create_item",
            {"item_type": "feature", "title": "probe", "priority": value},
        )
        assert result.get("success"), result
        new = [p for p in _snapshot(repo) if p not in before]
        assert len(new) == 1
        assert f"\npriority: {value}\n" in new[0].read_text()

    @pytest.mark.parametrize("value", ["critical", "high", "medium", "low"])
    def test_mcp_update_every_valid_value(self, repo, monkeypatch, value):
        monkeypatch.chdir(repo)
        path = repo / "kanban-work" / "expeditions" / "EXP-002-Low-Item.md"
        result = _server(repo).handle_tool_call(
            "kanban_update_item", {"item_id": "EXP-002", "priority": value}
        )
        assert result.get("success"), result
        assert f"\npriority: {value}\n" in path.read_text()

    def test_cli_list_priority_still_filters(self, repo, monkeypatch):
        monkeypatch.chdir(repo)
        result = CliRunner().invoke(main, ["list", "--priority", "high", "--json"])
        assert result.exit_code == 0, result.output
        assert [d["id"] for d in json.loads(result.output)] == ["EXP-001"]
