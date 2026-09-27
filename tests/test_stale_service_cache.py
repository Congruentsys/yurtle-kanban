"""One long-lived KanbanService never serves stale item state (#638).

The MCP server keeps one KanbanService for its whole session. Its item cache
used to keep the pre-write copy after move/update/comment/rank, so a second
move was judged from the old status ("Invalid transition from backlog to
in_progress") and reads returned the old state. After any write, the next
read (and the next write's checks) must see what is in the file now.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from yurtle_kanban.config import KanbanConfig, PathConfig
from yurtle_kanban.mcp import server as mcp_server
from yurtle_kanban.models import WorkItemStatus
from yurtle_kanban.service import KanbanService

ITEM = "EXP-001"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, check=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.email", "test@test.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / ".kanban").mkdir()
    exp_dir = tmp_path / "kanban-work" / "expeditions"
    exp_dir.mkdir(parents=True)
    KanbanConfig(
        theme="nautical",
        paths=PathConfig(root="kanban-work/", scan_paths=["kanban-work/expeditions/"]),
    ).save(tmp_path / ".kanban" / "config.yaml")
    (exp_dir / "EXP-001-Stale-Cache.md").write_text(
        "---\nid: EXP-001\ntitle: Stale Cache\nstatus: backlog\npriority: medium\n---\n\nBody.\n"
    )
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-m", "init")
    return tmp_path


def _item_file(repo: Path) -> Path:
    return repo / "kanban-work" / "expeditions" / "EXP-001-Stale-Cache.md"


def _service(repo: Path) -> KanbanService:
    return KanbanService(KanbanConfig.load(repo / ".kanban" / "config.yaml"), repo)


def _snapshot(svc: KanbanService) -> dict[str, Any]:
    item = svc.get_item(ITEM)
    assert item is not None
    return {
        "status": item.status.value,
        "priority": item.priority,
        "priority_rank": item.priority_rank,
        "assignee": item.assignee,
        "title": item.title,
    }


def _assert_current(svc: KanbanService, repo: Path, step: str) -> dict[str, Any]:
    """The long-lived service's read equals a fresh service's read of the files."""
    cached = _snapshot(svc)
    fresh = _snapshot(_service(repo))
    assert cached == fresh, f"stale read after {step}: cached={cached} file={fresh}"
    return cached


# --- through the real MCP loop, one long-lived server ------------------------


def _call(rid: int, name: str, **arguments: Any) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": rid,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }


def _run(monkeypatch, repo: Path, *requests: dict[str, Any]) -> dict[int, dict[str, Any]]:
    """Pipe requests through ONE run_server session; return tool payloads by id."""
    monkeypatch.chdir(repo)
    stdin = io.StringIO("".join(json.dumps(r) + "\n" for r in requests))
    stdout = io.StringIO()
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "stdout", stdout)

    mcp_server.run_server()

    payloads: dict[int, dict[str, Any]] = {}
    for line in stdout.getvalue().splitlines():
        if not line.strip():
            continue
        reply = json.loads(line)
        assert "result" in reply, f"protocol error: {reply!r}"
        payloads[reply["id"]] = json.loads(reply["result"]["content"][0]["text"])
    assert sorted(payloads) == sorted(r["id"] for r in requests)
    return payloads


def test_mcp_two_moves_in_one_session_both_succeed(monkeypatch, repo):
    out = _run(
        monkeypatch,
        repo,
        _call(1, "kanban_move_item", item_id=ITEM, new_status="ready"),
        _call(2, "kanban_move_item", item_id=ITEM, new_status="in_progress"),
    )
    assert "error" not in out[1], out[1]
    assert "error" not in out[2], f"second move judged a stale status: {out[2]}"
    assert out[2]["item"]["status"] == "in_progress"


def test_mcp_get_item_after_move_shows_new_status(monkeypatch, repo):
    out = _run(
        monkeypatch,
        repo,
        _call(1, "kanban_move_item", item_id=ITEM, new_status="ready"),
        _call(2, "kanban_get_item", item_id=ITEM),
        _call(3, "kanban_move_item", item_id=ITEM, new_status="in_progress"),
        _call(4, "kanban_get_item", item_id=ITEM),
    )
    assert out[2]["item"]["status"] == "ready", f"stale read after move: {out[2]}"
    assert out[4]["item"]["status"] == "in_progress", f"stale read after move: {out[4]}"


def test_mcp_update_and_comment_then_get_item(monkeypatch, repo):
    out = _run(
        monkeypatch,
        repo,
        _call(1, "kanban_move_item", item_id=ITEM, new_status="ready"),
        _call(2, "kanban_update_item", item_id=ITEM, priority="high"),
        _call(3, "kanban_add_comment", item_id=ITEM, comment="noted", author="t"),
        _call(4, "kanban_get_item", item_id=ITEM),
    )
    for rid in (1, 2, 3):
        assert "error" not in out[rid], out[rid]
    item = out[4]["item"]
    assert item["status"] == "ready"
    assert item["priority"] == "high"


def _move(svc: KanbanService, status: str) -> None:
    try:
        svc.move_item(ITEM, WorkItemStatus.from_string(status))
    except ValueError as e:
        pytest.fail(f"move to {status} judged a stale status: {e}")


# --- through the service API, one long-lived KanbanService --------------------


def test_service_two_moves_in_a_row(repo):
    svc = _service(repo)
    _move(svc, "ready")
    assert _assert_current(svc, repo, "move ready")["status"] == "ready"
    _move(svc, "in_progress")
    assert _assert_current(svc, repo, "move in_progress")["status"] == "in_progress"


def test_service_interleaved_writes_every_read_is_current(repo):
    svc = _service(repo)
    _move(svc, "ready")
    _assert_current(svc, repo, "move ready")

    svc.update_item(ITEM, priority="high")
    snap = _assert_current(svc, repo, "update_item")
    assert (snap["status"], snap["priority"]) == ("ready", "high")

    _move(svc, "in_progress")
    _assert_current(svc, repo, "move in_progress")

    svc.add_comment(ITEM, "first note", "t")
    snap = _assert_current(svc, repo, "add_comment")
    assert snap["status"] == "in_progress"
    # comments are not parsed back from the file, so pin the file itself
    assert "first note" in _item_file(repo).read_text()

    svc.rank_item(ITEM, 3)
    snap = _assert_current(svc, repo, "rank_item")
    assert snap["priority_rank"] == 3

    svc.update_item(ITEM, title="Renamed")
    snap = _assert_current(svc, repo, "update_item title")
    assert snap == {
        "status": "in_progress",
        "priority": "high",
        "priority_rank": 3,
        "assignee": snap["assignee"],
        "title": "Renamed",
    }


def test_service_returned_item_is_current(repo):
    svc = _service(repo)
    moved = svc.move_item(ITEM, WorkItemStatus.from_string("ready"))
    assert moved.status.value == "ready"
    updated = svc.update_item(ITEM, priority="high")
    assert (updated.status.value, updated.priority) == ("ready", "high")


# --- a file changed outside the service between calls -------------------------


def test_service_write_after_external_edit_judges_the_file(repo):
    """Pinned: after the service's own write, and before the next write, a
    manual edit to the file is honoured by that next write's checks, and the
    read after it reflects both. (Whether a pure read with no write in between
    sees an external edit is NOT pinned here.)"""
    svc = _service(repo)
    svc.update_item(ITEM, priority="high")  # the service has written once
    path = _item_file(repo)
    path.write_text(path.read_text().replace("status: backlog", "status: ready"))

    _move(svc, "in_progress")
    snap = _assert_current(svc, repo, "move after external edit")
    assert (snap["status"], snap["priority"]) == ("in_progress", "high")


def test_service_write_after_git_checkout_judges_the_file(repo):
    """Pinned: after the service's own write, a `git checkout` that rolls the
    item file back is what the next write is judged against. Rolled back to
    `backlog`, a move to in_progress is refused (backlog -> in_progress is not
    a legal transition), even though the service itself last wrote `ready`."""
    svc = _service(repo)
    _move(svc, "ready")
    assert _assert_current(svc, repo, "move ready")["status"] == "ready"
    rel = str(_item_file(repo).relative_to(repo))
    _git(repo, "checkout", "HEAD~1", "--", rel)  # the initial commit: backlog
    assert "status: backlog" in _item_file(repo).read_text()

    with pytest.raises(ValueError, match="(?i)illegal move"):
        svc.move_item(ITEM, WorkItemStatus.from_string("in_progress"))
    assert "status: backlog" in _item_file(repo).read_text()
