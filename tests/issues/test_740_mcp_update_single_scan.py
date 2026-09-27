# ruff: noqa: F811  -- the `repo` fixture imported from #576's module is re-bound as an arg
"""#740: an MCP `kanban_update_item` scans the files once, never twice, and a refused
bad argument scans them not at all.

Spec ([steer] on #740):
- every argument check (priority, string lists, booleans) runs before any scan: a
  refused bad argument makes ZERO `scan()` calls once the server is set up;
- an update with `depends_on` makes exactly ONE scan (not the MCP's #732 scan plus the
  service's #638 deps scan);
- an update without `depends_on` also makes exactly ONE scan: the #732 freshness scan
  stays;
- #732 holds: a hand-made duplicate made after the server's first scan still refuses a
  `depends_on` update (now through the service's own deps scan), in one scan.

Scans are counted by wrapping `server.service.scan` after the server and its service
are set up (a `kanban_get_item` first, as a long-lived server would have served).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from tests.issues.test_576_cli_update_deps import Repo, _item_text, repo  # noqa: F401
from yurtle_kanban.mcp import server as mcp_server


def _served(repo: Repo, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, list[int]]:
    """A set-up server whose service's `scan()` calls are counted from now on."""
    mcp = mcp_server.KanbanMCPServer(repo_root=repo.root)
    assert mcp.handle_tool_call("kanban_get_item", {"item_id": "EXP-4"}).get("item")
    service = mcp.service
    real_scan: Callable[..., Any] = service.scan
    calls: list[int] = []

    def counted(*args: Any, **kwargs: Any) -> Any:
        calls.append(1)
        return real_scan(*args, **kwargs)

    monkeypatch.setattr(service, "scan", counted)
    return mcp, calls


@pytest.mark.parametrize(
    ("bad", "needle"),
    [
        ({"allow_unknown": "yes"}, "allow_unknown"),
        ({"priority": "urgent-ish"}, "priority"),
        ({"depends_on": "EXP-3"}, "depends_on"),
        ({"tags": "ui"}, "tags"),
    ],
    ids=["bad-boolean", "bad-priority", "bad-depends_on-list", "bad-tags-list"],
)
def test_a_refused_argument_scans_nothing(
    repo: Repo, monkeypatch: pytest.MonkeyPatch, bad: dict[str, Any], needle: str
) -> None:
    mcp, calls = _served(repo, monkeypatch)
    before, head = repo.snapshot(), repo.head()
    args = {"item_id": "EXP-5", "depends_on": ["EXP-1"], **bad}
    out = mcp.handle_tool_call("kanban_update_item", args)
    assert "error" in out and needle in out["error"].lower(), out
    assert repo.snapshot() == before and repo.head() == head
    assert len(calls) == 0, f"a refused {needle} scanned {len(calls)} time(s) first"


def test_a_depends_on_update_scans_once(repo: Repo, monkeypatch: pytest.MonkeyPatch) -> None:
    mcp, calls = _served(repo, monkeypatch)
    out = mcp.handle_tool_call("kanban_update_item", {"item_id": "EXP-5", "depends_on": ["exp-1"]})
    assert out.get("success") is True, out
    assert repo.deps("EXP-5") == ["EXP-1"]
    assert len(calls) == 1, f"a depends_on update scanned {len(calls)} times"


def test_an_update_without_deps_scans_once(repo: Repo, monkeypatch: pytest.MonkeyPatch) -> None:
    """Control: the #732 freshness scan stays when no dependency is edited."""
    mcp, calls = _served(repo, monkeypatch)
    out = mcp.handle_tool_call("kanban_update_item", {"item_id": "EXP-5", "title": "Renamed"})
    assert out.get("success") is True, out
    assert repo.fm("EXP-5")["title"] == "Renamed"
    assert len(calls) == 1, f"a title update scanned {len(calls)} times"


def test_a_late_duplicate_still_refuses_a_depends_on_update_in_one_scan(
    repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#732 kept: the duplicate is made after the server's first scan."""
    mcp, calls = _served(repo, monkeypatch)
    dup = repo.root / "research" / "ideas" / "EXP-4-copy.md"
    dup.parent.mkdir(parents=True, exist_ok=True)
    dup.write_text(_item_text("EXP-4", []).replace("type: expedition", "type: idea"))
    before = repo.snapshot()
    out = mcp.handle_tool_call("kanban_update_item", {"item_id": "EXP-4", "depends_on": ["EXP-1"]})
    assert "error" in out and "EXP-4" in out["error"], out
    assert repo.snapshot() == before
    assert len(calls) == 1, f"the refused duplicate update scanned {len(calls)} times"
