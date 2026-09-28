# ruff: noqa: F811  (the borrowed `world` fixture)
"""Issue #943: pin two parts of `create --push`'s twin checks (#788, #834, #903).

1. `_tree_names` lists folders (`ls-tree -t`): a folder on origin named like the new
   item file (`EXPR-042-b.md/x.txt`) is a file twin, refused as "already exists …
   would replace it", not a raw git error from writing a file over a directory.
2. The file-twin check matches only the item's own folder: a twin under a folder
   spelled in another case (`research/Experiments/expr-042-B.md`, board folder
   `research/experiments/`) gets the FOLDER message (#834). Its `id:` differs from
   its name, so `_holder_at` doesn't refuse first.
"""

from __future__ import annotations

from tests.issues.test_674_parent_edges import (  # noqa: F401  (fixtures)
    _clean_theme_cache,
    world,
)
from tests.issues.test_788_no_overwrite import EXPR_099, _board_with
from tests.issues.test_834_folder_case_guard import _top_board
from tests.issues.test_903_one_tree_listing import FILE_TWIN_MSG, FOLDER_TWIN_MSG, NEW_ID
from yurtle_kanban.models import WorkItemType


def test_folder_named_like_the_item_is_a_file_twin(world) -> None:
    svc = _board_with(world, {f"research/experiments/{NEW_ID}-b.md/x.txt": "not an item\n"})
    base = world.remote_sha()
    result = svc.create_item_and_push(WorkItemType.EXPERIMENT, "b", item_id=NEW_ID)
    assert world.remote_sha() == base, "something was pushed"
    msg = result.get("message", "")
    assert result["success"] is False, result
    assert f"{NEW_ID}-b.md" in msg, result
    for needle in FILE_TWIN_MSG:
        assert needle in msg, result


def test_twin_under_a_case_twin_folder_gets_the_folder_message(world) -> None:
    svc = _top_board(
        world, "research/experiments/", "research/Experiments/",
        {"research/Experiments/expr-042-B.md": EXPR_099},
    )
    base = world.remote_sha()
    result = svc.create_item_and_push(WorkItemType.EXPERIMENT, "b", item_id=NEW_ID)
    assert world.remote_sha() == base, "something was pushed"
    msg = result.get("message", "")
    assert result["success"] is False, result
    assert FOLDER_TWIN_MSG in msg, result
    assert "research/Experiments/" in msg, result  # origin's spelling, not the board's (#982)
    for needle in FILE_TWIN_MSG:
        assert needle not in msg, result
