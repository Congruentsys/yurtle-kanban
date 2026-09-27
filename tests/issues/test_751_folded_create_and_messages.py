# ruff: noqa: F811  -- fixtures imported from other issue modules are re-bound as args
"""#751: `create_item_and_push` joins the case-folded index; commits name the file's ID.

Follow-up from the #748 review (#741). Decided ([steer] on #751):

(1) Both cache writes in `create_item_and_push` (the local-commit path, no remote, and
    the pushed path, a bare origin) go through `_index_item`, as `create_item` does
    (#741). After it returns, `_folded_items[ID.upper()]` holds the new item and
    `get_item(<id lower-cased>)` returns it with zero `scan()` calls.
(2) The move, update, comment and rank commit messages use `item.id`, the file's own
    spelling, not the ID as passed. On a file saying `id: exp-9`, reached as `EXP-9`,
    the commit subject says `exp-9`.
(3) Control: an upper-case item's messages are unchanged.

Fixtures: #576's two-board repo (no remote) with #741's lower-case `exp-9` file, and
the #585/#590/#645 World (a bare origin, clone A on main) for the pushed path.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.issues.test_576_cli_update_deps import (  # noqa: F401
    Repo,
    _git,
    repo,
)
from tests.issues.test_585_create_push_loop import World
from tests.issues.test_741_case_folded_lookup import _lower
from yurtle_kanban import config as config_mod
from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.models import WorkItemStatus, WorkItemType
from yurtle_kanban.service import KanbanService


@pytest.fixture(autouse=True)
def _clean_theme_cache() -> Iterator[None]:
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    monkeypatch.setenv("YURTLE_AGENT", "tester")
    return World(tmp_path)


def _count_scans(monkeypatch: pytest.MonkeyPatch, svc: KanbanService) -> list[int]:
    """Count `svc.scan()` calls from here on, so a hidden rescan is detected."""
    calls: list[int] = []
    real = svc.scan

    def counted() -> Any:
        calls.append(1)
        return real()

    monkeypatch.setattr(svc, "scan", counted)
    return calls


def _subject(root: Path) -> str:
    return _git(root, "log", "-1", "--format=%s").strip()


def _assert_folded_without_scan(
    monkeypatch: pytest.MonkeyPatch, svc: KanbanService, result: dict[str, Any]
) -> None:
    assert result["success"] is True, result
    new_id = result["id"]
    assert new_id != new_id.lower(), f"want an upper-case id to fold: {new_id}"

    folded = svc._folded_items.get(new_id.upper())
    assert folded is not None, (
        f"_folded_items lacks {new_id}: create_item_and_push skipped _index_item"
    )
    assert folded.id == new_id

    scans = _count_scans(monkeypatch, svc)
    item = svc.get_item(new_id.lower())
    assert item is not None, f"get_item({new_id.lower()!r}) missed the just-created item"
    assert item.id == new_id
    assert scans == [], f"get_item rescanned {len(scans)}x to find a just-created item"


# ---------------------------------------------------------------------------
# (1) create_item_and_push joins the folded index
# ---------------------------------------------------------------------------


def test_create_push_local_path_joins_the_folded_index(
    repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No remote: the local allocate + create + commit path."""
    assert _git(repo.root, "remote").strip() == ""
    svc = repo.service()
    svc.scan()
    result = svc.create_item_and_push(WorkItemType.EXPEDITION, "Fresh local item")
    assert result.get("pushed") is False, result
    _assert_folded_without_scan(monkeypatch, svc, result)


def test_create_push_pushed_path_joins_the_folded_index(
    world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bare origin, clone A on main: the compare-and-swap path, landed locally."""
    svc = KanbanService(KanbanConfig.load(world.a / ".kanban" / "config.yaml"), world.a)
    svc.scan()
    result = svc.create_item_and_push(WorkItemType.EXPEDITION, "Fresh pushed item")
    assert result.get("pushed") is True, result
    assert result.get("local") is True, result
    _assert_folded_without_scan(monkeypatch, svc, result)


def test_create_item_already_joins_the_folded_index(
    repo: Repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Control: plain `create_item` indexes through `_index_item` since #741."""
    svc = repo.service()
    svc.scan()
    item = svc.create_item(WorkItemType.EXPEDITION, "Plain created item")
    assert svc._folded_items.get(item.id.upper()) is item
    scans = _count_scans(monkeypatch, svc)
    found = svc.get_item(item.id.lower())
    assert found is not None and found.id == item.id
    assert scans == []


# ---------------------------------------------------------------------------
# (2) commit messages name the file's ID
# ---------------------------------------------------------------------------


def _move(svc: KanbanService, item_id: str) -> None:
    svc.move_item(item_id, WorkItemStatus.IN_PROGRESS, validate_workflow=False,
                  skip_wip_check=True, skip_gates=True)


def _update(svc: KanbanService, item_id: str) -> None:
    svc.update_item(item_id, priority="high")


def _comment(svc: KanbanService, item_id: str) -> None:
    svc.add_comment(item_id, "a note", "tester")


def _rank(svc: KanbanService, item_id: str) -> None:
    svc.rank_item(item_id, 2)


WRITERS = {
    "move": (_move, "Move {} to in_progress"),
    "update": (_update, "Update {}:"),
    "comment": (_comment, "Add comment to {}"),
    "rank": (_rank, "Rank {} as #2"),
}


@pytest.mark.parametrize("name", list(WRITERS))
def test_commit_message_names_the_files_id(repo: Repo, name: str) -> None:
    """`EXP-9` on a file saying `id: exp-9`: the subject says `exp-9`."""
    _lower(repo)
    before = repo.head()
    write, template = WRITERS[name]
    write(repo.service(), "EXP-9")
    assert repo.head() != before, f"{name} made no commit"
    subject = _subject(repo.root)
    assert template.format("exp-9") in subject, subject
    assert "EXP-9" not in subject, f"{name} named the ID as typed: {subject!r}"


@pytest.mark.parametrize("name", list(WRITERS))
def test_commit_message_for_an_upper_case_item_is_unchanged(repo: Repo, name: str) -> None:
    """Control: `EXP-2` on a file saying `id: EXP-2`."""
    before = repo.head()
    write, template = WRITERS[name]
    write(repo.service(), "EXP-2")
    assert repo.head() != before, f"{name} made no commit"
    subject = _subject(repo.root)
    assert template.format("EXP-2") in subject, subject


def test_commit_message_with_exact_lowercase_argument(repo: Repo) -> None:
    """Control: passing the file's own spelling always named it."""
    _lower(repo)
    repo.service().update_item("exp-9", priority="high")
    assert _subject(repo.root).startswith("Update exp-9:"), _subject(repo.root)

