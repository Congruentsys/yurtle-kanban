# ruff: noqa: F811  -- the `repo` fixture imported from #576's module is re-bound as an arg
"""#732: a long-lived MCP service sees duplicates as the files are now; IDs that
differ only in case are duplicates."""

from __future__ import annotations

from tests.issues.test_576_cli_update_deps import Repo, _item_text, invoke, repo  # noqa: F401
from yurtle_kanban.mcp import server as mcp_server


def test_mcp_update_sees_a_duplicate_made_after_its_first_scan(repo: Repo) -> None:
    mcp = mcp_server.KanbanMCPServer(repo_root=repo.root)
    assert mcp.handle_tool_call("kanban_get_item", {"item_id": "EXP-4"}).get("item")
    dup = repo.root / "research" / "ideas" / "EXP-4-copy.md"
    dup.parent.mkdir(parents=True, exist_ok=True)
    dup.write_text(_item_text("EXP-4", []).replace("type: expedition", "type: idea"))
    before = repo.snapshot()
    out = mcp.handle_tool_call("kanban_update_item", {"item_id": "EXP-4", "priority": "high"})
    assert "error" in out and "EXP-4" in out["error"], out
    assert repo.snapshot() == before


def test_case_only_duplicate_ids_are_duplicates(repo: Repo) -> None:
    dup = repo.root / "research" / "ideas" / "exp-4-lower.md"
    dup.parent.mkdir(parents=True, exist_ok=True)
    dup.write_text(
        _item_text("EXP-4", []).replace("id: EXP-4", "id: exp-4").replace("type: expedition", "type: idea")
    )
    repo.commit("case-only duplicate")
    result = invoke(["validate"])
    assert "duplicate" in result.output.lower() and "EXP-4" in result.output.upper(), result.output
