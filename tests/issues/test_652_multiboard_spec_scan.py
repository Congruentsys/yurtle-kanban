"""Issue #652 — multi-board: a hand-written `type: spec` item on a `preset: spec`
board is not found by show/list.

Reproduction (test partner): the item IS on disk under the spec board's path, and the
multi-board scan does walk that path. It is dropped at parse time. `spec`, `rfc`,
`spike` and `adr` are not `WorkItemType` members, so `_parse_file` falls back to
`_map_theme_type`, and that reads `self.config.get_theme()` with no board. In v2 that
is `boards[0].preset`, the FIRST board's theme, not the theme of the board the file is
on. When the spec board is not listed first, the type is unknown, `_parse_file`
returns None, and the item disappears from show, list and list --board. `scan_paths`
has nothing to do with it (the item is under the board path either way). The issue's
repro (nautical + hdd + spec, with scan_paths) is one case of this.

Readings (the driver may challenge):

1. "Found" means `show <ID>` exits 0 and prints the item, and `list` / `list --board
   <spec board>` include the ID. The item's `item_type` is NOT pinned: a found spec
   item reads back as `task` on a single spec board today (the `_map_theme_type`
   fallback maps every non-nautical theme type to TASK), which is a separate question.
2. The cause is general to any theme type outside the enum on a board that is not
   first. Among the built-in themes only `spec` has such types (rfc, spec, spike,
   adr); a custom theme (`.kanban/themes/<name>.yaml`) is affected the same way, so it
   is covered too.
3. Controls: a v1 single spec board, a v2 repo with ONE spec board, and a v2 repo
   with the spec board listed FIRST all find the item today and must keep doing so.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from yurtle_kanban.cli import main

SPEC_ONLY_TYPES = ["spec", "rfc", "spike", "adr"]  # in themes/spec.yaml, not in the enum

CUSTOM_THEME = {
    "name": "Stories",
    "description": "A custom theme whose type is not a built-in type",
    "item_types": {"story": {"id_prefix": "STORY", "name": "Story"}},
    "columns": {
        "backlog": {"name": "Backlog", "order": 1},
        "doing": {"name": "Doing", "order": 2},
        "done": {"name": "Done", "order": 3},
    },
}


def _repo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    config: dict,
    item_rel: str,
    item_id: str,
    item_type: str,
    status: str = "draft",
    custom_theme: bool = False,
) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".kanban").mkdir()
    (tmp_path / ".kanban" / "config.yaml").write_text(yaml.safe_dump(config, sort_keys=False))
    if custom_theme:
        (tmp_path / ".kanban" / "themes").mkdir()
        (tmp_path / ".kanban" / "themes" / "stories.yaml").write_text(yaml.safe_dump(CUSTOM_THEME))
    item = tmp_path / item_rel
    item.parent.mkdir(parents=True)
    item.write_text(
        f"---\nid: {item_id}\ntitle: Hand written {item_type}\ntype: {item_type}\n"
        f"status: {status}\n---\n\n# Hand written {item_type}\n"
    )
    monkeypatch.chdir(tmp_path)


def _assert_found(item_id: str, board: str | None) -> None:
    runner = CliRunner()
    shown = runner.invoke(main, ["show", item_id])
    assert shown.exit_code == 0 and "not found" not in shown.output.lower(), (
        f"show {item_id} did not find it: exit {shown.exit_code}\n{shown.output}"
    )
    listed = runner.invoke(main, ["list", "--json"])
    assert listed.exit_code == 0, listed.output
    assert item_id in listed.output, f"list does not include {item_id}:\n{listed.output}"
    if board is not None:
        on_board = runner.invoke(main, ["list", "--board", board, "--json"])
        assert on_board.exit_code == 0, on_board.output
        assert item_id in on_board.output, (
            f"list --board {board} does not include {item_id}:\n{on_board.output}"
        )


def _board(name: str, preset: str, path: str, **extra: object) -> dict:
    return {"name": name, "preset": preset, "path": path, **extra}


# --- the issue's repro: nautical + hdd + spec, scan_paths set ----------------------


def test_issue_repro_nautical_hdd_spec_with_scan_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = {
        "version": "2.0",
        "boards": [
            _board("development", "nautical", "kanban-work/"),
            _board("research", "hdd", "research/"),
            _board("specs", "spec", "specs/", scan_paths=["specs/specs/"]),
        ],
    }
    _repo(tmp_path, monkeypatch, config, "specs/specs/SPEC-001-hand.md", "SPEC-001", "spec")
    _assert_found("SPEC-001", "specs")


# --- minimal: two boards, spec board NOT first --------------------------------------


@pytest.mark.parametrize("first_preset", ["nautical", "software", "hdd"])
@pytest.mark.parametrize("item_type", SPEC_ONLY_TYPES)
def test_spec_board_second_finds_spec_only_types(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, first_preset: str, item_type: str
) -> None:
    config = {
        "version": "2.0",
        "boards": [
            _board("first", first_preset, "first-board/"),
            _board("specs", "spec", "specs-board/"),
        ],
    }
    item_id = f"{item_type.upper()}-001"
    rel = f"specs-board/{item_type}s/{item_id}-hand.md"
    _repo(tmp_path, monkeypatch, config, rel, item_id, item_type)
    _assert_found(item_id, "specs")


def test_default_board_does_not_hide_second_board_types(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`default_board: specs` must not be what makes it work: the item's own board
    decides its theme, whichever board is first or default."""
    config = {
        "version": "2.0",
        "default_board": "dev",
        "boards": [
            _board("dev", "nautical", "kanban-work/"),
            _board("specs", "spec", "specs-board/"),
        ],
    }
    _repo(tmp_path, monkeypatch, config, "specs-board/specs/SPEC-007-x.md", "SPEC-007", "spec")
    _assert_found("SPEC-007", "specs")


# --- the same cause on a custom theme ------------------------------------------------


def test_custom_theme_board_second_finds_its_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = {
        "version": "2.0",
        "boards": [
            _board("dev", "software", "kanban-work/"),
            _board("stories", "stories", "stories-board/"),
        ],
    }
    _repo(
        tmp_path,
        monkeypatch,
        config,
        "stories-board/stories/STORY-001-x.md",
        "STORY-001",
        "story",
        status="backlog",
        custom_theme=True,
    )
    _assert_found("STORY-001", "stories")


# --- controls: pass today, must keep passing ----------------------------------------


def test_control_single_board_v1_spec(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = {"kanban": {"theme": "spec", "paths": {"root": "specs-board/"}}}
    _repo(tmp_path, monkeypatch, config, "specs-board/specs/SPEC-001-x.md", "SPEC-001", "spec")
    _assert_found("SPEC-001", None)


def test_control_v2_one_spec_board(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = {"version": "2.0", "boards": [_board("specs", "spec", "specs-board/")]}
    _repo(tmp_path, monkeypatch, config, "specs-board/specs/SPEC-001-x.md", "SPEC-001", "spec")
    _assert_found("SPEC-001", "specs")


def test_control_v2_spec_board_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = {
        "version": "2.0",
        "boards": [
            _board("specs", "spec", "specs-board/"),
            _board("dev", "nautical", "kanban-work/"),
        ],
    }
    _repo(tmp_path, monkeypatch, config, "specs-board/specs/SPEC-001-x.md", "SPEC-001", "spec")
    _assert_found("SPEC-001", "specs")
