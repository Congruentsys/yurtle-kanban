# ruff: noqa: F811  -- fixtures imported from other issue modules are re-bound as args
"""#774: the create paths record no false duplicate in `duplicate_ids`.

Since #751 both cache writes in `create_item_and_push` go through `_index_item`, which
records a duplicate when another file already holds the same case-folded ID (#576,
#732). Indexing the SAME file again, however its path is spelled, is not a duplicate.
After each create below, `service.duplicate_ids == {}` and the item is found by its
lower-cased ID:

- `create_item_and_push`, local-commit path (no remote) and pushed path (bare origin);
- plain `create_item` (control);
- a second create in the same session;
- a re-index of the same item (`_index_item(item)` again);
- the same file spelled through a symlinked directory;
- a rescan after the create.

Fixtures: #576's repo (no remote), #585's World (bare origin), reused via #751's module.
"""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path

import pytest

from tests.issues.test_576_cli_update_deps import (  # noqa: F401
    Repo,
    repo,
)
from tests.issues.test_585_create_push_loop import World
from tests.issues.test_751_folded_create_and_messages import (  # noqa: F401
    _clean_theme_cache,
    world,
)
from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.models import WorkItemType
from yurtle_kanban.service import KanbanService


def _assert_clean(svc: KanbanService, new_id: str) -> None:
    """No duplicate recorded, and the item is found by its lower-cased ID."""
    assert new_id != new_id.lower(), f"want an upper-case id to fold: {new_id}"
    assert svc.duplicate_ids == {}, f"false duplicate recorded: {svc.duplicate_ids}"
    found = svc.get_item(new_id.lower())
    assert found is not None, f"get_item({new_id.lower()!r}) missed {new_id}"
    assert found.id == new_id


def _push_local(svc: KanbanService, title: str) -> str:
    result = svc.create_item_and_push(WorkItemType.EXPEDITION, title)
    assert result["success"] is True, result
    assert result.get("pushed") is False, result
    return str(result["id"])


def _world_service(world: World) -> KanbanService:
    return KanbanService(KanbanConfig.load(world.a / ".kanban" / "config.yaml"), world.a)


def _symlinked_service(repo: Repo, tmp_path: Path) -> tuple[KanbanService, Path]:
    """A service rooted at `repo.root` spelled through a symlinked directory.

    macOS `/tmp` -> `/private/tmp` is the real-world case; a link inside `tmp_path`
    is the same shape and needs no particular host layout."""
    alias = tmp_path / "alias"
    try:
        os.symlink(repo.root.parent, alias, target_is_directory=True)
    except (OSError, NotImplementedError) as e:
        pytest.skip(f"cannot create a directory symlink here: {e}")
    root = alias / repo.root.name
    assert root != root.resolve() and root.resolve() == repo.root.resolve()
    svc = KanbanService(KanbanConfig.load(root / ".kanban" / "config.yaml"), root)
    os.chdir(root)  # monkeypatch in `repo` restores the cwd
    return svc, root


# ---------------------------------------------------------------------------
# create paths
# ---------------------------------------------------------------------------


def test_create_push_local_path_records_no_duplicate(repo: Repo) -> None:
    svc = repo.service()
    svc.scan()
    new_id = _push_local(svc, "Fresh local item")
    _assert_clean(svc, new_id)


def test_create_push_pushed_path_records_no_duplicate(world: World) -> None:
    svc = _world_service(world)
    svc.scan()
    result = svc.create_item_and_push(WorkItemType.EXPEDITION, "Fresh pushed item")
    assert result["success"] is True, result
    assert result.get("pushed") is True and result.get("local") is True, result
    _assert_clean(svc, str(result["id"]))


def test_create_item_records_no_duplicate(repo: Repo) -> None:
    """Control: plain `create_item`."""
    svc = repo.service()
    svc.scan()
    item = svc.create_item(WorkItemType.EXPEDITION, "Plain created item")
    _assert_clean(svc, item.id)


def test_second_create_in_session_records_no_duplicate(repo: Repo) -> None:
    svc = repo.service()
    svc.scan()
    first = _push_local(svc, "First item")
    second = _push_local(svc, "Second item")
    assert first.upper() != second.upper()
    _assert_clean(svc, first)
    _assert_clean(svc, second)


def test_pushed_second_create_records_no_duplicate(world: World) -> None:
    svc = _world_service(world)
    svc.scan()
    ids = []
    for title in ("First pushed", "Second pushed"):
        result = svc.create_item_and_push(WorkItemType.EXPEDITION, title)
        assert result["success"] is True and result.get("pushed") is True, result
        ids.append(str(result["id"]))
    assert ids[0].upper() != ids[1].upper()
    for new_id in ids:
        _assert_clean(svc, new_id)


# ---------------------------------------------------------------------------
# the same file indexed again
# ---------------------------------------------------------------------------


def test_reindex_same_item_records_no_duplicate(repo: Repo) -> None:
    svc = repo.service()
    svc.scan()
    new_id = _push_local(svc, "Reindexed item")
    item = svc._folded_items[new_id.upper()]
    svc._index_item(item)
    svc._index_item(dataclasses.replace(item))  # an equal copy, same path
    _assert_clean(svc, new_id)


def test_same_file_through_symlinked_dir_records_no_duplicate(repo: Repo, tmp_path: Path) -> None:
    """The item indexed under the symlinked root, then under the resolved one."""
    svc, root = _symlinked_service(repo, tmp_path)
    svc.scan()
    new_id = _push_local(svc, "Linked item")
    item = svc._folded_items[new_id.upper()]
    assert root in item.file_path.parents, item.file_path
    resolved = dataclasses.replace(item, file_path=item.file_path.resolve())
    assert resolved.file_path != item.file_path, "want two spellings of one file"
    svc._index_item(resolved)
    _assert_clean(svc, new_id)
    svc._index_item(item)  # and back again
    _assert_clean(svc, new_id)


def test_create_through_symlinked_root_then_rescan_records_no_duplicate(
    repo: Repo, tmp_path: Path
) -> None:
    svc, _ = _symlinked_service(repo, tmp_path)
    svc.scan()
    new_id = _push_local(svc, "Linked rescan item")
    _assert_clean(svc, new_id)
    svc.scan()
    _assert_clean(svc, new_id)


# ---------------------------------------------------------------------------
# rescan after the create
# ---------------------------------------------------------------------------


def test_rescan_after_local_create_records_no_duplicate(repo: Repo) -> None:
    svc = repo.service()
    svc.scan()
    new_id = _push_local(svc, "Rescanned item")
    svc.scan()
    _assert_clean(svc, new_id)


def test_rescan_after_pushed_create_records_no_duplicate(world: World) -> None:
    svc = _world_service(world)
    svc.scan()
    result = svc.create_item_and_push(WorkItemType.EXPEDITION, "Rescanned pushed")
    assert result["success"] is True and result.get("pushed") is True, result
    svc.scan()
    _assert_clean(svc, str(result["id"]))
