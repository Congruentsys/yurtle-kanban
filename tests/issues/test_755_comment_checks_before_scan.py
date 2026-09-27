# ruff: noqa: F811  -- the `repo` fixture imported from #576's module is re-bound as an arg
"""#755: an MCP `kanban_add_comment` checks the comment and author text before its
#742 freshness rescan, so a refused comment scans the files not at all.

Spec ([steer] on #755, the #740 rule):
- a comment or author the service's `_check_text` refuses (a lone surrogate: text
  that can't be written as UTF-8, #172) makes ZERO `scan()` calls once the server is
  set up, and the refusal is unchanged: an error naming the field, nothing written,
  nothing committed;
- a valid comment still makes exactly ONE scan;
- #742 holds: a duplicate made after the server's first scan still refuses a
  comment, in one scan.

Scans are counted as in #740: `server.service.scan` is wrapped after the server and
its service are set up (a `kanban_get_item` first, as a long-lived server would have
served).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from tests.issues.test_576_cli_update_deps import Repo, _item_text, repo  # noqa: F401
from yurtle_kanban.mcp import server as mcp_server

ITEM = "EXP-4"
SURROGATE = "\udcff"  # what surrogateescape makes of an undecodable byte (#172)


def _served(repo: Repo, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, list[int]]:
    """A set-up server whose service's `scan()` calls are counted from now on."""
    mcp = mcp_server.KanbanMCPServer(repo_root=repo.root)
    assert mcp.handle_tool_call("kanban_get_item", {"item_id": ITEM}).get("item")
    service = mcp.service
    real_scan: Callable[..., Any] = service.scan
    calls: list[int] = []

    def counted(*args: Any, **kwargs: Any) -> Any:
        calls.append(1)
        return real_scan(*args, **kwargs)

    monkeypatch.setattr(service, "scan", counted)
    return mcp, calls


@pytest.mark.parametrize(
    ("args", "needle"),
    [
        ({"comment": f"bad {SURROGATE} bytes", "author": "tester"}, "comment"),
        ({"comment": "hello", "author": f"tes{SURROGATE}ter"}, "author"),
    ],
    ids=["bad-comment", "bad-author"],
)
def test_a_refused_comment_scans_nothing(
    repo: Repo, monkeypatch: pytest.MonkeyPatch, args: dict[str, Any], needle: str
) -> None:
    mcp, calls = _served(repo, monkeypatch)
    before, head = repo.snapshot(), repo.head()
    out = mcp.handle_tool_call("kanban_add_comment", {"item_id": ITEM, **args})
    assert "error" in out, out
    assert needle in out["error"] and "invalid UTF-8" in out["error"], out
    assert repo.snapshot() == before, "a refused comment wrote a file"
    assert repo.head() == head, "a refused comment committed"
    assert len(calls) == 0, f"a refused {needle} scanned {len(calls)} time(s) first"


def test_a_control_character_author_scans_nothing(
    repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Control: an author `check_identity` refuses is already refused before the scan."""
    mcp, calls = _served(repo, monkeypatch)
    before, head = repo.snapshot(), repo.head()
    out = mcp.handle_tool_call(
        "kanban_add_comment", {"item_id": ITEM, "comment": "hello", "author": "tes\nter"}
    )
    assert "error" in out and "control character" in out["error"], out
    assert repo.snapshot() == before and repo.head() == head
    assert len(calls) == 0, f"a control-character author scanned {len(calls)} time(s)"


def test_a_valid_comment_scans_once(repo: Repo, monkeypatch: pytest.MonkeyPatch) -> None:
    """Control: the #742 freshness scan stays for a comment that is written."""
    mcp, calls = _served(repo, monkeypatch)
    head = repo.head()
    out = mcp.handle_tool_call(
        "kanban_add_comment", {"item_id": ITEM, "comment": "hello there", "author": "tester"}
    )
    assert out.get("success") is True, out
    assert "hello there" in (repo.root / repo.rel(ITEM)).read_text(encoding="utf-8")
    assert repo.head() != head, "a valid comment was not committed"
    assert len(calls) == 1, f"a valid comment scanned {len(calls)} times"


def test_a_late_duplicate_still_refuses_a_comment_in_one_scan(
    repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#742 kept: the duplicate is made after the server's first scan."""
    mcp, calls = _served(repo, monkeypatch)
    dup = repo.root / "research" / "ideas" / "EXP-4-copy.md"
    dup.parent.mkdir(parents=True, exist_ok=True)
    dup.write_text(
        _item_text(ITEM, []).replace("type: expedition", "type: idea"), encoding="utf-8"
    )
    before = repo.snapshot()
    out = mcp.handle_tool_call(
        "kanban_add_comment", {"item_id": ITEM, "comment": "hello", "author": "tester"}
    )
    assert "error" in out, out
    assert ITEM in out["error"] and "is on more than one board" in out["error"], out
    assert repo.snapshot() == before, "the refused duplicate comment wrote a file"
    assert len(calls) == 1, f"the refused duplicate comment scanned {len(calls)} times"
