"""Issue #665 — multi-board: theme lookups that ignore the board.

Decided ([steer] on #665, bucket 2):
- `_get_columns_from_theme` and `_get_type_prefix` resolve the theme of the board that
  holds the item or path, falling back to `config.get_theme()` only with no board.
- `_map_theme_type`'s no-board fallback matches `_theme_status_names`: every board's
  theme, first wins.
- The per-scan board and theme resolution for theme-only types is memoised, as
  `_theme_status_names` does.
(Keeping the real type instead of `task` is #682, not here.)

What is wrong for users today (test partner's findings):

(a) ID prefix. `create` / `create --push` take the prefix from `config.get_theme()`,
    which in v2 is the FIRST board's theme, while `_get_type_directory` places the
    file on the default board, or on the first board whose theme gives the type a
    `path`. So when the file lands on a later board, its ID uses the wrong prefix:
    - a type only a later board's theme gives a prefix (`chore: {id_prefix: CH}`
      on board 2, a software board 1 has no chore) gets the built-in `CHORE-`;
    - a type both boards' themes define with different prefixes gets board 1's
      prefix (`ATASK-`) on a file that lands on board 2 (`BTASK` expected).
(b) Columns. `_get_columns_from_theme` has one caller, the SINGLE-board branch of
    `get_board`; multi-board `get_board` uses `_get_columns_from_preset(board.preset)`.
    Nothing user-visible is wrong: the tests here are controls that pin the right
    board's columns in both modes.
(c) `_map_theme_type` for a file on no board in multi-board mode reads only the
    first board's theme. A multi-board scan only walks board paths, so the reader
    reaches this only for a file outside every board (`_parse_file` called on it);
    tested at the method level. Only found / not found is observable (every
    non-nautical theme type maps to task, #682), so "first wins" is not separable
    from "any board's" here; the test pins that every board's theme is consulted.
(d) Memoisation: a scan of theme-only-type items on a later board loads a board
    theme once per item; it should be at most once per board.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from tests.issues._snapshot import glob_outside_git
from tests.issues.test_633_per_board_column_map import (
    _multi_repo,
    _service,
    _single_repo,
)
from tests.issues.test_652_multiboard_spec_scan import CUSTOM_THEME as STORIES_THEME
from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import main
from yurtle_kanban.models import WorkItemType

# a later board's theme gives `chore` its own prefix and path; software has no chore
TALES_THEME: dict[str, Any] = {
    "theme": {"name": "tales"},
    "item_types": {"chore": {"id_prefix": "CH", "name": "Chore", "path": "tales-board/chores/"}},
    "columns": {
        "shelf": {"name": "Shelf", "order": 1},
        "telling": {"name": "Telling", "order": 2},
        "told": {"name": "Told", "order": 3},
    },
    "status_mappings": {"shelf": "backlog", "telling": "in_progress", "told": "done"},
}

# two themes define `task` with different prefixes and no path
ALPHA_THEME: dict[str, Any] = {
    "theme": {"name": "alpha"},
    "item_types": {"task": {"id_prefix": "ATASK", "name": "Task"}},
}
BETA_THEME: dict[str, Any] = {
    "theme": {"name": "beta"},
    "item_types": {"task": {"id_prefix": "BTASK", "name": "Task"}},
}


@pytest.fixture(autouse=True)
def _clean_theme_cache() -> Iterator[None]:
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


def _set_default_board(repo: Path, name: str) -> None:
    cfg = repo / ".kanban" / "config.yaml"
    cfg.write_text(cfg.read_text() + f"default_board: {name}\n")


def _create(repo: Path, monkeypatch: pytest.MonkeyPatch, args: list[str]) -> tuple[str, Path]:
    """Run `create`, return (id, file) of the one new file under the repo."""
    monkeypatch.chdir(repo)
    config_mod._theme_cache.clear()
    before = set(glob_outside_git(repo, "*.md"))
    result = CliRunner().invoke(main, ["create", *args])
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        raise AssertionError(f"`create {' '.join(args)}` crashed: {result.exception!r}")
    assert result.exit_code == 0, result.output
    new = [p for p in set(glob_outside_git(repo, "*.md")) - before if ".kanban" not in p.parts]
    assert len(new) == 1, f"expected one new item file, got {new}\n{result.output}"
    ids = [ln[3:].strip() for ln in new[0].read_text().splitlines() if ln.startswith("id:")]
    assert ids, f"no id in {new[0]}"
    item_id = ids[0].strip("'\"")
    return item_id, new[0].relative_to(repo)


# --- (a) create: the prefix of the board the file lands on ----------------------------


@pytest.mark.parametrize("push", [False, True], ids=["create", "create_push"])
def test_type_only_later_board_defines_uses_its_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, push: bool
) -> None:
    repo = _multi_repo(
        tmp_path / "r",
        [("dev", "software", "dev-board/"), ("tales", "tales", "tales-board/")],
        {"tales": TALES_THEME},
    )
    args = ["chore", "Sweep the deck"] + (["--push"] if push else [])
    item_id, rel = _create(repo, monkeypatch, args)
    assert rel.parts[0] == "tales-board", f"landed on {rel}, not the tales board"
    assert item_id.startswith("CH-"), (
        f"{item_id} ({rel}): the tales board's theme gives chore the prefix CH; "
        "the first board's theme (software) was used instead"
    )


@pytest.mark.parametrize("push", [False, True], ids=["create", "create_push"])
def test_type_both_boards_define_uses_landing_boards_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, push: bool
) -> None:
    repo = _multi_repo(
        tmp_path / "r",
        [("alpha", "alpha", "alpha-board/"), ("beta", "beta", "beta-board/")],
        {"alpha": ALPHA_THEME, "beta": BETA_THEME},
    )
    _set_default_board(repo, "beta")
    args = ["task", "Stow the lines"] + (["--push"] if push else [])
    item_id, rel = _create(repo, monkeypatch, args)
    assert rel.parts[0] == "beta-board", f"landed on {rel}, not the default beta board"
    assert item_id.startswith("BTASK-"), (
        f"{item_id} ({rel}): the file is on beta, whose theme's task prefix is BTASK; "
        "alpha's (the first board's) prefix was used"
    )


def test_control_first_board_prefix_when_file_lands_there(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _multi_repo(
        tmp_path / "r",
        [("alpha", "alpha", "alpha-board/"), ("beta", "beta", "beta-board/")],
        {"alpha": ALPHA_THEME, "beta": BETA_THEME},
    )
    item_id, rel = _create(repo, monkeypatch, ["task", "Coil the rope"])
    assert rel.parts[0] == "alpha-board"
    assert item_id.startswith("ATASK-"), item_id


def test_control_single_board_prefix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _single_repo(tmp_path / "r", "nautical")
    item_id, _ = _create(repo, monkeypatch, ["expedition", "Chart the reef"])
    assert item_id.startswith("EXP-"), item_id


# --- (b) columns: the right board's theme (controls: nothing reachable is wrong) ------


def test_control_later_custom_board_shows_its_own_columns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _multi_repo(
        tmp_path / "r",
        [("dev", "software", "dev-board/"), ("tales", "tales", "tales-board/")],
        {"tales": TALES_THEME},
    )
    service = _service(repo, monkeypatch)
    assert [c.id for c in service.get_board("tales").columns] == ["shelf", "telling", "told"]
    assert "shelf" not in [c.id for c in service.get_board("dev").columns]


def test_control_single_board_columns_from_its_theme(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _single_repo(tmp_path / "r", "nautical")
    service = _service(repo, monkeypatch)
    theme = config_mod._load_builtin_theme("nautical", repo)
    expected = sorted(theme["columns"], key=lambda c: theme["columns"][c].get("order", 0))
    assert [c.id for c in service.get_board().columns] == expected


# --- (c) a file on no board: every board's theme, first wins ---------------------------


def _three_boards(tmp_path: Path) -> Path:
    return _multi_repo(
        tmp_path / "r",
        [
            ("dev", "software", "dev-board/"),
            ("tales", "tales", "tales-board/"),
            ("stories", "stories", "stories-board/"),
        ],
        {"tales": TALES_THEME, "stories": STORIES_THEME},
    )


def test_off_board_map_theme_type_merges_every_boards_theme(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _three_boards(tmp_path)
    service = _service(repo, monkeypatch)
    assert service._map_theme_type("story", None) is not None, (
        "`story` is the third board's theme type; with no board every theme counts"
    )
    loose = repo / "loose" / "STORY-9.md"
    assert service.config.get_board_for_path(loose, repo) is None
    assert service._map_theme_type("story", loose) is not None


def test_off_board_file_is_read_through_parse_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _three_boards(tmp_path)
    loose = repo / "loose" / "STORY-9.md"
    loose.parent.mkdir()
    loose.write_text("---\nid: STORY-9\ntitle: Loose\ntype: story\nstatus: backlog\n---\n")
    service = _service(repo, monkeypatch)
    item = service._parse_file(loose)
    assert item is not None and item.id == "STORY-9", (
        "a file on no board whose type only a later board's theme defines is dropped"
    )


def test_control_on_board_type_stays_board_scoped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The merge is only for a file on NO board: on the dev board, another board's
    theme type is still unknown (#652)."""
    repo = _three_boards(tmp_path)
    service = _service(repo, monkeypatch)
    assert service._map_theme_type("story", repo / "dev-board" / "x.md") is None
    assert service._map_theme_type("story", repo / "stories-board" / "x.md") is not None


def test_control_single_board_no_path_uses_configured_theme(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _single_repo(tmp_path / "r", "spec")
    service = _service(repo, monkeypatch)
    assert service._map_theme_type("rfc", None) is WorkItemType.TASK
    assert service._map_theme_type("story", None) is None


# --- (d) memoised within one scan ------------------------------------------------------


def test_scan_loads_each_board_theme_at_most_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _multi_repo(
        tmp_path / "r",
        [("dev", "software", "dev-board/"), ("stories", "stories", "stories-board/")],
        {"stories": STORIES_THEME},
    )
    folder = repo / "stories-board" / "stories"
    folder.mkdir(parents=True)
    for n in range(1, 7):
        (folder / f"STORY-{n}.md").write_text(
            f"---\nid: STORY-{n}\ntitle: s{n}\ntype: story\nstatus: backlog\n---\n"
        )
    service = _service(repo, monkeypatch)
    calls: list[Any] = []
    real = service._load_board_theme

    def counting(board: Any) -> Any:
        calls.append(board.name if board else None)
        return real(board)

    monkeypatch.setattr(service, "_load_board_theme", counting)
    items = service.scan()
    assert len([i for i in items if i.id.startswith("STORY-")]) == 6
    assert len(calls) <= 2, f"one scan loaded a board theme {len(calls)} times: {calls}"

