# ruff: noqa: F811  -- the `repo` fixture imported from #576's module is re-bound as an arg
"""#719: MCP list arguments must be JSON arrays and booleans must be booleans; a
string is refused, never split into characters (and the service refuses a bare
string list too)."""

from __future__ import annotations

import pytest

from tests.issues.test_576_cli_update_deps import Repo, repo  # noqa: F401  (fixture)
from yurtle_kanban.mcp import server as mcp_server


def _mcp(repo: Repo):
    return mcp_server.KanbanMCPServer(repo_root=repo.root)


def _file_text(repo: Repo, item_id: str) -> str:
    return next(repo.root.rglob(f"{item_id}-*.md")).read_text()


@pytest.mark.parametrize(
    ("key", "value"),
    [("depends_on", "EXP-3"), ("related", "EXP-1"), ("tags", "ui"), ("depends_on", 5)],
)
def test_mcp_refuses_a_non_list(repo: Repo, key: str, value) -> None:
    before = _file_text(repo, "EXP-2")
    out = _mcp(repo)._update_item({"item_id": "EXP-2", key: value, "allow_unknown": True})
    assert "error" in out and key in out["error"], out
    assert _file_text(repo, "EXP-2") == before


@pytest.mark.parametrize("value", ["false", "true", 0, 1])
def test_mcp_refuses_a_non_boolean_allow_unknown(repo: Repo, value) -> None:
    out = _mcp(repo)._update_item({"item_id": "EXP-2", "depends_on": ["EXP-3"], "allow_unknown": value})
    assert "error" in out and "allow_unknown" in out["error"], out


def test_mcp_list_still_works(repo: Repo) -> None:
    out = _mcp(repo)._update_item({"item_id": "EXP-2", "depends_on": ["exp-3"]})
    assert out.get("success"), out
    assert "EXP-3" in _file_text(repo, "EXP-2")


def test_service_refuses_a_bare_string_list(repo: Repo) -> None:
    from tests.issues.test_576_cli_update_deps import KanbanConfig, KanbanService

    svc = KanbanService(KanbanConfig.load(repo.root / ".kanban" / "config.yaml"), repo.root)
    with pytest.raises(ValueError):
        svc.update_item("EXP-2", depends_on="EXP-3", allow_unknown=True)
