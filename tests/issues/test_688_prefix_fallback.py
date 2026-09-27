"""#688: when the board a new item lands on has a theme that doesn't define the
type, the prefix comes from the first board (config order) whose theme does; the
built-in prefix only when no board's theme defines it."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.issues.test_633_per_board_column_map import _multi_repo
from tests.issues.test_665_board_theme_lookups import (
    ALPHA_THEME,
    TALES_THEME,
    _clean_theme_cache,  # noqa: F401  (autouse fixture)
    _create,
    _set_default_board,
)


@pytest.mark.parametrize("push", [False, True], ids=["create", "create_push"])
def test_landing_theme_silent_falls_back_to_first_defining_board(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, push: bool
) -> None:
    repo = _multi_repo(
        tmp_path / "r",
        [("alpha", "alpha", "alpha-board/"), ("tales", "tales", "tales-board/")],
        {"alpha": ALPHA_THEME, "tales": TALES_THEME},
    )
    _set_default_board(repo, "tales")  # tales' theme has no `task`
    item_id, rel = _create(repo, monkeypatch, ["task", "Coil the rope"] + (["--push"] if push else []))
    assert item_id.startswith("ATASK-"), f"{item_id} ({rel}): alpha is the first board defining task"


def test_control_no_board_defines_the_type_uses_builtin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _multi_repo(
        tmp_path / "r",
        [("tales", "tales", "tales-board/"), ("tales2", "tales", "tales2-board/")],
        {"tales": TALES_THEME},
    )
    item_id, _ = _create(repo, monkeypatch, ["task", "Coil the rope"])
    assert item_id.startswith("TASK-"), item_id
