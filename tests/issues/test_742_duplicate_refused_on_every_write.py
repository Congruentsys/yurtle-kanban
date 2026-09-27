# ruff: noqa: F811  -- the `repo` fixture imported from #576's module is re-bound as an arg
"""#742: `move`, `comment` and `rank` refuse an item whose ID is on more than one
board, as `update` has since #721 — exact-case or case-folded (#732), through the CLI,
the service and MCP (`kanban_move_item`, `kanban_add_comment`), including a long-lived
MCP server that must rescan to see a duplicate written after it started.

A refusal names every file, says "is on more than one board" and "ambiguous", writes
nothing, commits nothing, and exits non-zero. Controls: a non-duplicated item still
moves, comments and ranks; `update` still refuses; a bad MCP argument is still refused.

Fixture: the #576 two-board repo; the duplicate copy of EXP-4 goes on the research
(hdd) board as `research/ideas/...`, as in #732.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from tests.issues.test_576_cli_update_deps import (  # noqa: F401
    Repo,
    _flat,
    _git,
    _item_text,
    _ok,
    _refused,
    invoke,
    repo,
)
from yurtle_kanban.mcp import server as mcp_server

ITEM = "EXP-4"
NEEDLES = ("is on more than one board", "ambiguous")


def _write_dup(repo: Repo, *, case_folded: bool) -> Path:
    """A second copy of EXP-4 on the research board (id `exp-4` when case-folded)."""
    name = "exp-4-lower.md" if case_folded else "EXP-4-copy.md"
    dup = repo.root / "research" / "ideas" / name
    dup.parent.mkdir(parents=True, exist_ok=True)
    text = _item_text(ITEM, []).replace("type: expedition", "type: idea")
    if case_folded:
        text = text.replace("id: EXP-4", "id: exp-4")
    dup.write_text(text, encoding="utf-8")
    return dup


def _dup_rel(repo: Repo, dup: Path) -> str:
    return dup.relative_to(repo.root).as_posix()


@pytest.fixture(params=[False, True], ids=["exact-case", "case-folded"])
def dup(request: pytest.FixtureRequest, repo: Repo) -> Path:
    """EXP-4 duplicated on a second board, committed (a clean tree)."""
    path = _write_dup(repo, case_folded=request.param)
    repo.commit("duplicate EXP-4")
    return path


# ---------------------------------------------------------------------------
# CLI: move, comment, rank refuse a duplicated ID (RED on current src)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "args",
    [
        ["move", ITEM, "in_progress"],
        ["move", ITEM.lower(), "in_progress"],
        ["comment", ITEM, "--body", "hello"],
        ["rank", ITEM, "1"],
    ],
    ids=["move", "move-lowercase-arg", "comment", "rank"],
)
def test_cli_write_refuses_duplicated_id(repo: Repo, dup: Path, args: list[str]) -> None:
    _refused(repo, args, ITEM, repo.rel(ITEM), _dup_rel(repo, dup), *NEEDLES)


# ---------------------------------------------------------------------------
# Service: move_item, add_comment, rank_item raise before writing (RED)
# ---------------------------------------------------------------------------


def _service_calls() -> dict[str, Callable[..., object]]:
    def move(svc: object) -> object:
        target = svc.get_item(ITEM)  # type: ignore[attr-defined]
        status = svc.resolve_status_name(target, "in_progress")  # type: ignore[attr-defined]
        return svc.move_item(ITEM, status)  # type: ignore[attr-defined]

    return {
        "move_item": move,
        "add_comment": lambda svc: svc.add_comment(ITEM, "hello", "tester"),
        "rank_item": lambda svc: svc.rank_item(ITEM, 1),
    }


@pytest.mark.parametrize("call", list(_service_calls()))
def test_service_write_refuses_duplicated_id(repo: Repo, dup: Path, call: str) -> None:
    before, head = repo.snapshot(), repo.head()
    with pytest.raises(ValueError) as exc:
        _service_calls()[call](repo.service())
    msg = str(exc.value)
    for needle in (ITEM, repo.rel(ITEM), _dup_rel(repo, dup), *NEEDLES):
        assert needle in msg, f"{needle!r} not in {msg!r}"
    assert repo.snapshot() == before, f"{call} wrote a file"
    assert repo.head() == head, f"{call} committed"


# ---------------------------------------------------------------------------
# MCP: move / comment refuse, also on a long-lived server (RED)
# ---------------------------------------------------------------------------

MCP_CALLS = {
    "kanban_move_item": {"item_id": ITEM, "new_status": "in_progress"},
    "kanban_add_comment": {"item_id": ITEM, "comment": "hello", "author": "tester"},
}


def _mcp_refused(repo: Repo, mcp: mcp_server.KanbanMCPServer, tool: str, dup: Path) -> None:
    before, head = repo.snapshot(), repo.head()
    out = mcp.handle_tool_call(tool, dict(MCP_CALLS[tool]))
    assert "error" in out, out
    for needle in (ITEM, repo.rel(ITEM), _dup_rel(repo, dup), *NEEDLES):
        assert needle in out["error"], f"{needle!r} not in {out['error']!r}"
    assert repo.snapshot() == before, f"{tool} wrote a file"
    assert repo.head() == head, f"{tool} committed"


@pytest.mark.parametrize("tool", list(MCP_CALLS))
def test_mcp_write_refuses_duplicated_id(repo: Repo, dup: Path, tool: str) -> None:
    mcp = mcp_server.KanbanMCPServer(repo_root=repo.root)
    _mcp_refused(repo, mcp, tool, dup)


@pytest.mark.parametrize("case_folded", [False, True], ids=["exact-case", "case-folded"])
@pytest.mark.parametrize("tool", list(MCP_CALLS))
def test_mcp_long_lived_server_sees_duplicate_written_after_start(
    repo: Repo, tool: str, case_folded: bool
) -> None:
    mcp = mcp_server.KanbanMCPServer(repo_root=repo.root)
    assert mcp.handle_tool_call("kanban_get_item", {"item_id": ITEM}).get("item")
    dup = _write_dup(repo, case_folded=case_folded)  # by hand, after the first scan
    repo.commit("hand-made duplicate")
    _mcp_refused(repo, mcp, tool, dup)


# ---------------------------------------------------------------------------
# Controls (GREEN on current src)
# ---------------------------------------------------------------------------


def test_control_update_still_refuses_duplicated_id(repo: Repo, dup: Path) -> None:
    _refused(repo, ["update", ITEM, "--priority", "high"], ITEM, *NEEDLES)


def test_control_non_duplicated_item_moves(repo: Repo, dup: Path) -> None:
    _ok(["move", "EXP-5", "in_progress"])
    # nautical stores in_progress as `underway`
    assert repo.fm("EXP-5")["status"] in ("in_progress", "underway"), repo.fm("EXP-5")


def test_control_non_duplicated_item_comments(repo: Repo, dup: Path) -> None:
    _ok(["comment", "EXP-5", "--body", "hello there"])
    assert "hello there" in repo.path("EXP-5").read_text(encoding="utf-8")


def test_control_non_duplicated_item_ranks(repo: Repo, dup: Path) -> None:
    head = repo.head()
    result = _ok(["rank", "EXP-5", "1"])
    assert "Ranked EXP-5" in _flat(result.output), result.output
    assert repo.head() != head, "rank did not commit"


@pytest.mark.parametrize("tool", list(MCP_CALLS))
def test_control_mcp_non_duplicated_item_writes(repo: Repo, dup: Path, tool: str) -> None:
    mcp = mcp_server.KanbanMCPServer(repo_root=repo.root)
    args = {**MCP_CALLS[tool], "item_id": "EXP-5"}
    out = mcp.handle_tool_call(tool, args)
    assert out.get("success"), out


@pytest.mark.parametrize(
    "tool,args",
    [
        ("kanban_move_item", {"item_id": "EXP-5", "new_status": "no_such_status"}),
        ("kanban_move_item", {"item_id": ["EXP-5"], "new_status": "in_progress"}),
        ("kanban_add_comment", {"item_id": 5, "comment": "hi", "author": "tester"}),
        ("kanban_move_item", {"item_id": "EXP-99", "new_status": "in_progress"}),
    ],
    ids=["move-bad-status", "move-nonstring-id", "comment-nonstring-id", "move-unknown-id"],
)
def test_control_mcp_bad_argument_still_refused(
    repo: Repo, tool: str, args: dict[str, object]
) -> None:
    before, head = repo.snapshot(), repo.head()
    out = mcp_server.KanbanMCPServer(repo_root=repo.root).handle_tool_call(tool, args)
    assert "error" in out, out
    assert repo.snapshot() == before and repo.head() == head
    assert not _git(repo.root, "status", "--porcelain").strip()
