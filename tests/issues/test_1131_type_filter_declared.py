"""Issue #1131 — `list --type` filters by a declared theme type too.

After #682, `list` shows an item's `declared_type` (e.g. `spec`), but `list --type
spec` refused with `Unknown type: spec`: the filter knew only the canonical enum.

[steer] ruling: `--type` accepts a canonical type as today (matching `item_type`);
otherwise it matches items whose `declared_type` equals it, case-insensitively. A
name that is neither canonical nor declared by any item on the board is still
refused, and the valid list includes the declared types present. MCP's list filter
does the same.

Fixture: a spec-theme repo with two `type: spec` items and one `type: task` item.
The spec items are canonical `task` (#682), so `--type task` lists all three —
today's behaviour, pinned.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import CliRunner, Result

from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import main
from yurtle_kanban.mcp.server import KanbanMCPServer

SPECS = {"DOC-001", "DOC-002"}
TASK = "DOC-003"
ALL = SPECS | {TASK}


@pytest.fixture(autouse=True)
def _clean_theme_cache() -> Iterator[None]:
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    kanban = tmp_path / ".kanban"
    kanban.mkdir()
    (kanban / "config.yaml").write_text(
        yaml.safe_dump({"kanban": {"theme": "spec", "paths": {"root": "work/"}}})
    )
    work = tmp_path / "work"
    work.mkdir()
    for item_id, item_type in [("DOC-001", "spec"), ("DOC-002", "Spec"), (TASK, "task")]:
        (work / f"{item_id}-x.md").write_text(
            f"---\nid: {item_id}\ntitle: Item {item_id}\ntype: {item_type}\n"
            f"status: draft\n---\n\n# Item {item_id}\n"
        )
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _invoke(*args: str) -> Result:
    config_mod._theme_cache.clear()
    result = CliRunner().invoke(main, list(args))
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        raise AssertionError(f"`{' '.join(args)}` crashed: {result.exception!r}")
    return result


def _ok(*args: str) -> Result:
    result = _invoke(*args)
    assert result.exit_code == 0, result.output
    return result


def _ids_json(*args: str) -> set[str]:
    data: list[dict[str, Any]] = json.loads(_ok(*args).output)
    return {i["id"] for i in data}


def _ids_text(*args: str) -> set[str]:
    out = _ok(*args).output
    return {i for i in ALL if i in out}


@pytest.mark.parametrize("name", ["spec", "SPEC", "Spec"])
def test_list_type_declared_json(repo: Path, name: str) -> None:
    assert _ids_json("list", "--type", name, "--json") == SPECS


@pytest.mark.parametrize("name", ["spec", "SPEC"])
def test_list_type_declared_text(repo: Path, name: str) -> None:
    assert _ids_text("list", "--type", name) == SPECS


def test_list_type_canonical_unchanged(repo: Path) -> None:
    """`task` is canonical: it matches `item_type`, which the spec items share."""
    assert _ids_json("list", "--type", "task", "--json") == ALL
    assert _ids_text("list", "--type", "task") == ALL


def test_list_type_unknown_refused_with_declared_in_valid(repo: Path) -> None:
    result = _invoke("list", "--type", "bogus")
    assert result.exit_code == 1, result.output
    err = result.stderr
    assert "Unknown type: bogus" in err, err
    assert "Valid types:" in err, err
    valid = err.split("Valid types:", 1)[1]
    assert "spec" in valid and "task" in valid, err


def test_list_type_unknown_refused_json(repo: Path) -> None:
    result = _invoke("list", "--type", "bogus", "--json")
    assert result.exit_code == 1, result.output
    data = json.loads(result.stdout)
    assert data["success"] is False, data
    assert "Unknown type: bogus" in data["error"], data
    assert "spec" in data["error"], data


def test_list_type_declared_by_no_item_refused(repo: Path) -> None:
    """A theme type no item on the board declares is still unknown."""
    result = _invoke("list", "--type", "rfc")
    assert result.exit_code == 1, result.output
    assert "Unknown type: rfc" in result.stderr, result.stderr


def test_roadmap_type_declared(repo: Path) -> None:
    assert _ids_json("roadmap", "--type", "spec", "--json") == SPECS


def test_mcp_list_type_declared(repo: Path) -> None:
    server = KanbanMCPServer(repo_root=repo)
    for name in ("spec", "SPEC"):
        got = server._list_items({"item_type": name})
        assert {i["id"] for i in got["items"]} == SPECS, got
        assert got["count"] == 2, got
    got = server._list_items({"item_type": "task"})
    assert {i["id"] for i in got["items"]} == ALL, got


def test_mcp_list_type_unknown_refused(repo: Path) -> None:
    server = KanbanMCPServer(repo_root=repo)
    got = server.handle_tool_call("kanban_list_items", {"item_type": "bogus"})
    assert "Unknown type: bogus" in got.get("error", ""), got


def test_mcp_list_schema_accepts_declared_type(repo: Path) -> None:
    """The list tool's `item_type` isn't pinned to the canonical enum."""
    server = KanbanMCPServer(repo_root=repo)
    tools = {t["name"]: t for t in server.get_tools()}
    prop = tools["kanban_list_items"]["inputSchema"]["properties"]["item_type"]
    assert "enum" not in prop, prop
