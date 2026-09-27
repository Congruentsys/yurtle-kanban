# ruff: noqa: F811  -- the `repo` fixture imported from #576's module is re-bound as an arg
"""#720: a description/body with an unclosed code fence is refused (create and
update, CLI and MCP): the fence would swallow the status-history block, so the
next body edit would delete the history."""

from __future__ import annotations

import pytest

from tests.issues.test_576_cli_update_deps import Repo, invoke, repo  # noqa: F401
from yurtle_kanban.mcp import server as mcp_server

UNCLOSED = "Intro\n```python\nunclosed\n"


def test_cli_update_body_with_unclosed_fence_is_refused(repo: Repo) -> None:
    before = repo.snapshot()
    result = invoke(["update", "EXP-2", "--body-file", "-"], input=UNCLOSED)
    assert result.exit_code == 1, result.output
    assert "fence" in result.output.lower() and "line 2" in result.output, result.output
    assert repo.snapshot() == before


def test_cli_create_with_unclosed_fence_is_refused(repo: Repo) -> None:
    before = repo.snapshot()
    result = invoke(["create", "expedition", "X", "--body", "a\n~~~\nb"])
    assert result.exit_code == 1, result.output
    assert repo.snapshot() == before


def test_mcp_update_with_unclosed_fence_is_refused(repo: Repo) -> None:
    before = repo.snapshot()
    out = mcp_server.KanbanMCPServer(repo_root=repo.root)._update_item(
        {"item_id": "EXP-2", "description": UNCLOSED}
    )
    assert "error" in out and "fence" in out["error"].lower(), out
    assert repo.snapshot() == before


@pytest.mark.parametrize(
    "body",
    ["Intro\n```python\nx = 1\n```\n", "a\n~~~\nb\n~~~\n", "no fences at all\n", "````\n```\n````\n"],
)
def test_closed_fences_are_accepted(repo: Repo, body: str) -> None:
    result = invoke(["update", "EXP-2", "--body-file", "-"], input=body)
    assert result.exit_code == 0, result.output
