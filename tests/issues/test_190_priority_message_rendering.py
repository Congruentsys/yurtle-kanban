"""#190: the unknown-priority message renders the refused value one way on every
surface: a string raw (no quotes), anything else as its repr. The full message is
byte-identical across the service, MCP, and CLI `list --priority`. MCP
`_check_priority` is typed `object`, since it takes any JSON value."""

from __future__ import annotations

import inspect
import subprocess
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from yurtle_kanban.cli import main
from yurtle_kanban.config import KanbanConfig, PathConfig
from yurtle_kanban.models import WorkItemType, unknown_priority_message
from yurtle_kanban.service import KanbanService

VALID = "valid: critical, high, medium, low"
URGENT_MSG = f"Unknown priority: urgent; {VALID}"
NON_STRINGS: list[tuple[Any, str]] = [
    (5, f"Unknown priority: 5; {VALID}"),
    (True, f"Unknown priority: True; {VALID}"),
    (["high"], f"Unknown priority: ['high']; {VALID}"),
]
NON_STRING_IDS = ["int", "bool", "list"]
ALL_CASES = [("urgent", URGENT_MSG), *NON_STRINGS]
ALL_IDS = ["str", *NON_STRING_IDS]


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, capture_output=True, check=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@t.com")
    _git(tmp_path, "config", "user.name", "T")
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


def _service(root: Path) -> KanbanService:
    return KanbanService(KanbanConfig.load(root / ".kanban" / "config.yaml"), root)


def _server_module():
    return pytest.importorskip("yurtle_kanban.mcp.server")


def _server(root: Path):
    return _server_module().KanbanMCPServer(repo_root=root)


def _mcp_create(root: Path, value: Any) -> dict[str, Any]:
    return _server(root).handle_tool_call(
        "kanban_create_item", {"item_type": "feature", "title": "probe", "priority": value}
    )


def _mcp_update(root: Path, value: Any) -> dict[str, Any]:
    return _server(root).handle_tool_call(
        "kanban_update_item", {"item_id": "EXP-001", "priority": value}
    )


# --- 1. the helper renders the raw value itself --------------------------------


class _StrDiffersFromRepr:
    def __str__(self) -> str:
        return "shown-by-str"

    def __repr__(self) -> str:
        return "shown-by-repr"


class TestHelperRenders:
    @pytest.mark.parametrize(("value", "expected"), ALL_CASES, ids=ALL_IDS)
    def test_helper_renders_raw_value(self, value, expected):
        assert unknown_priority_message(value) == expected

    def test_helper_uses_repr_for_non_strings(self):
        # 5 / True / ['high'] have str == repr; this object does not, so it shows
        # whether the helper itself applies repr to a non-string.
        expected = f"Unknown priority: shown-by-repr; {VALID}"
        assert unknown_priority_message(_StrDiffersFromRepr()) == expected


# --- 2. service ------------------------------------------------------------------


class TestServiceMessage:
    @pytest.mark.parametrize(("value", "expected"), ALL_CASES, ids=ALL_IDS)
    def test_normalize_priority(self, value, expected):
        with pytest.raises(ValueError) as exc:
            KanbanService._normalize_priority(value)
        assert str(exc.value) == expected

    @pytest.mark.parametrize(("value", "expected"), ALL_CASES, ids=ALL_IDS)
    def test_create_item(self, repo, value, expected):
        with pytest.raises(ValueError) as exc:
            _service(repo).create_item(
                item_type=WorkItemType.FEATURE, title="probe", priority=value
            )
        assert str(exc.value) == expected

    @pytest.mark.parametrize(("value", "expected"), ALL_CASES, ids=ALL_IDS)
    def test_update_item(self, repo, value, expected):
        with pytest.raises(ValueError) as exc:
            _service(repo).update_item("EXP-001", priority=value)
        assert str(exc.value) == expected


# --- 3. MCP ----------------------------------------------------------------------


class TestMcpMessage:
    @pytest.mark.parametrize(("value", "expected"), ALL_CASES, ids=ALL_IDS)
    def test_create(self, repo, monkeypatch, value, expected):
        monkeypatch.chdir(repo)
        result = _mcp_create(repo, value)
        assert not result.get("success"), result
        assert result.get("error") == expected

    @pytest.mark.parametrize(("value", "expected"), ALL_CASES, ids=ALL_IDS)
    def test_update(self, repo, monkeypatch, value, expected):
        monkeypatch.chdir(repo)
        result = _mcp_update(repo, value)
        assert not result.get("success"), result
        assert result.get("error") == expected


# --- 4. CLI (strings only) --------------------------------------------------------


class TestCliMessage:
    def test_list_priority_urgent(self, repo, monkeypatch):
        monkeypatch.chdir(repo)
        result = CliRunner().invoke(main, ["list", "--priority", "urgent"])
        assert result.exit_code != 0, result.output
        assert result.output.strip() == URGENT_MSG


# --- 5. byte-identical across surfaces --------------------------------------------


class TestIdenticalAcrossSurfaces:
    @pytest.mark.parametrize(("value", "expected"), ALL_CASES, ids=ALL_IDS)
    def test_service_equals_mcp(self, repo, monkeypatch, value, expected):
        monkeypatch.chdir(repo)
        with pytest.raises(ValueError) as exc:
            KanbanService._normalize_priority(value)
        messages = {
            str(exc.value),
            _mcp_create(repo, value).get("error"),
            _mcp_update(repo, value).get("error"),
        }
        assert messages == {expected}

    def test_all_three_surfaces_urgent(self, repo, monkeypatch):
        monkeypatch.chdir(repo)
        with pytest.raises(ValueError) as exc:
            _service(repo).create_item(
                item_type=WorkItemType.FEATURE, title="probe", priority="urgent"
            )
        cli = CliRunner().invoke(main, ["list", "--priority", "urgent"])
        messages = {
            str(exc.value),
            _mcp_create(repo, "urgent").get("error"),
            cli.output.strip(),
        }
        assert messages == {URGENT_MSG}


# --- 6. static: MCP _check_priority takes any JSON value ---------------------------


class TestCheckPriorityAnnotation:
    def test_priority_param_is_object(self):
        server_cls = _server_module().KanbanMCPServer
        param = inspect.signature(server_cls._check_priority).parameters["priority"]
        # `from __future__ import annotations` leaves the annotation as a string.
        assert param.annotation in ("object", object), param.annotation

    def test_normalize_priority_param_is_object(self):
        param = inspect.signature(KanbanService._normalize_priority).parameters["priority"]
        assert param.annotation in ("object", object), param.annotation


# --- 7. round 2: a non-printable string is shown as its repr ---------------------

NON_PRINTABLE = [
    ("a\x1bb", f"Unknown priority: 'a\\x1bb'; {VALID}"),
    ("a\nb", f"Unknown priority: 'a\\nb'; {VALID}"),
]
NON_PRINTABLE_IDS = ["esc", "newline"]


class TestNonPrintableShownAsRepr:
    @pytest.mark.parametrize(("value", "expected"), NON_PRINTABLE, ids=NON_PRINTABLE_IDS)
    def test_helper(self, value, expected):
        assert unknown_priority_message(value) == expected

    @pytest.mark.parametrize(("value", "expected"), NON_PRINTABLE, ids=NON_PRINTABLE_IDS)
    def test_normalize_priority(self, value, expected):
        with pytest.raises(ValueError) as exc:
            KanbanService._normalize_priority(value)
        assert str(exc.value) == expected

    def test_printable_string_still_raw(self):
        assert unknown_priority_message("urgent") == URGENT_MSG
        with pytest.raises(ValueError) as exc:
            KanbanService._normalize_priority("urgent")
        assert str(exc.value) == URGENT_MSG


# --- negative controls -------------------------------------------------------------


class TestControls:
    @pytest.mark.parametrize("value", ["high", "High", "HIGH", " low "])
    def test_normalize_accepts_valid_any_case(self, value):
        assert KanbanService._normalize_priority(value) == value.strip().lower()

    def test_normalize_none(self):
        assert KanbanService._normalize_priority(None) is None

    @pytest.mark.parametrize("value", ["critical", "High", "MEDIUM", "low"])
    def test_mcp_create_accepts_valid(self, repo, monkeypatch, value):
        monkeypatch.chdir(repo)
        result = _mcp_create(repo, value)
        assert result.get("success"), result

    @pytest.mark.parametrize("value", ["critical", "High", "MEDIUM", "low"])
    def test_mcp_update_accepts_valid(self, repo, monkeypatch, value):
        monkeypatch.chdir(repo)
        path = repo / "kanban-work" / "expeditions" / "EXP-001-High-Item.md"
        result = _mcp_update(repo, value)
        assert result.get("success"), result
        assert f"\npriority: {value.lower()}\n" in path.read_text()

    @pytest.mark.parametrize("value", ["high", "High", "HIGH"])
    def test_cli_list_accepts_valid(self, repo, monkeypatch, value):
        monkeypatch.chdir(repo)
        result = CliRunner().invoke(main, ["list", "--priority", value, "--json"])
        assert result.exit_code == 0, result.output

    def test_service_create_accepts_valid(self, repo):
        item = _service(repo).create_item(
            item_type=WorkItemType.FEATURE, title="probe", priority="HIGH"
        )
        assert item.priority == "high"
