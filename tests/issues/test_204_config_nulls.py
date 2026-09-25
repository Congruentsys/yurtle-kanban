"""#204: YAML nulls in config load as empty; a string ``ignore`` is one pattern.

Nulls (a bare key) must mean "empty", never a crash in ``list``/``move``:

- ``kanban.paths:`` → the default paths config (as if absent)
- ``kanban.paths.scan_paths:`` / ``boards[].scan_paths:`` → ``[]``
- ``boards:`` (with ``version: "2.0"``) → no boards, as when the key is absent
- ``boards[].wip_exempt_types:`` → ``[]``
- ``boards[].gates:`` / ``kanban.gates:`` → ``{}``
- ``kanban.workflows:`` → what an absent key gives

A string ``ignore: "**/x/**"`` loads as ``["**/x/**"]`` (not one entry per
character), hides only matching items, and survives a ``board-add`` rewrite.
Single-board ``ignore: []`` round-trips through save as ``[]``.
Controls: absent keys give today's defaults; explicit lists are unchanged.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import main
from yurtle_kanban.config import KanbanConfig, PathConfig
from yurtle_kanban.service import KanbanService

X_PATTERN = "**/x/**"
IDS = {"FEAT-001", "FEAT-002"}  # FEAT-002 lives under work/features/x/


# --- config builders ------------------------------------------------------------------


def _single(*, paths: list[str] | None = None, extra: list[str] | None = None) -> str:
    """Single-board config; ``paths`` replaces the lines under ``paths:``,
    ``None`` meaning a normal root + scan_paths."""
    if paths is None:
        paths = ['    root: "work/"', "    scan_paths:", '      - "work/"']
    lines = ["kanban:", "  theme: software", "  paths:", *paths, *(extra or [])]
    return "\n".join(lines) + "\n"


def _multi(board_lines: list[str] | None = None) -> str:
    lines = ['version: "2.0"', "boards:", "  - name: dev", "    preset: software",
             '    path: "work/"', *(board_lines or [])]
    return "\n".join(lines) + "\ndefault_board: dev\n"


BARE_PATHS = "kanban:\n  theme: software\n  paths:\n"
NO_PATHS = "kanban:\n  theme: software\n"
BARE_BOARDS = 'version: "2.0"\nboards:\n'
NO_BOARDS = 'version: "2.0"\n'


# --- helpers --------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)


def _item(path: Path, item_id: str, item_type: str = "feature") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nid: {item_id}\ntitle: \"t\"\ntype: {item_type}\nstatus: backlog\n"
        f"priority: medium\ncreated: 2026-09-25\n---\n\n# {item_id}: t\n"
    )


def _repo(tmp_path: Path, config_yaml: str) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@t.com")
    _git(repo, "config", "user.name", "T")
    (repo / ".kanban").mkdir()
    (repo / ".kanban" / "config.yaml").write_text(config_yaml)
    _item(repo / "work" / "features" / "FEAT-001-live.md", "FEAT-001")
    _item(repo / "work" / "features" / "x" / "FEAT-002-in-x.md", "FEAT-002")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    return repo


def _load(tmp_path: Path, text: str) -> KanbanConfig:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(text)
    config_mod._theme_cache.clear()
    return KanbanConfig.load(cfg)


def _cli(repo: Path, monkeypatch: pytest.MonkeyPatch, args: list[str]):
    monkeypatch.chdir(repo)
    config_mod._theme_cache.clear()
    return CliRunner().invoke(main, args)


def _assert_clean(result) -> None:
    """Exit 0, or a normal refusal (exit 1) — never an unhandled exception."""
    assert "Traceback" not in (result.output or ""), result.output
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        raise AssertionError(repr(result.exception)) from result.exception


def _assert_list_ok(result) -> None:
    _assert_clean(result)
    assert result.exit_code == 0, result.output


def _assert_move_ok(result) -> None:
    _assert_clean(result)
    if result.exit_code != 0:
        # a WIP / gate refusal is fine; a crash is not
        assert result.exit_code == 1, result.output
        assert "Error:" in result.output, result.output


# --- nulls: config loading ------------------------------------------------------------


def test_bare_paths_loads_default_paths(tmp_path):
    config = _load(tmp_path, BARE_PATHS)
    assert config.paths == _load(tmp_path, NO_PATHS).paths
    assert config.paths == PathConfig()


def test_bare_scan_paths_loads_empty(tmp_path):
    config = _load(tmp_path, _single(paths=['    root: "work/"', "    scan_paths:"]))
    assert config.paths.scan_paths == []
    assert config.paths.root == "work/"


def test_bare_boards_loads_no_boards_like_absent(tmp_path):
    config = _load(tmp_path, BARE_BOARDS)
    absent = _load(tmp_path, NO_BOARDS)
    assert config.boards == [] == absent.boards
    assert config.is_multi_board is absent.is_multi_board is False
    assert config.paths == absent.paths


def test_bare_board_scan_paths_loads_empty(tmp_path):
    config = _load(tmp_path, _multi(["    scan_paths:"]))
    assert config.boards[0].scan_paths == []
    assert config.paths.scan_paths == []


def test_bare_board_wip_exempt_types_loads_empty(tmp_path):
    config = _load(tmp_path, _multi(["    wip_exempt_types:"]))
    assert config.boards[0].wip_exempt_types == []


def test_bare_board_gates_loads_empty(tmp_path):
    config = _load(tmp_path, _multi(["    gates:"]))
    assert config.boards[0].gates == {}


def test_bare_kanban_gates_loads_empty(tmp_path):
    config = _load(tmp_path, _single(extra=["  gates:"]))
    assert config.gates == {}


def test_bare_workflows_loads_like_absent(tmp_path):
    config = _load(tmp_path, _single(extra=["  workflows:"]))
    assert config.workflows == _load(tmp_path, _single()).workflows == {}


# --- nulls: CLI list / move -----------------------------------------------------------

NULL_CONFIGS = {
    "paths": BARE_PATHS,
    "scan_paths": _single(paths=['    root: "work/"', "    scan_paths:"]),
    "boards": BARE_BOARDS,
    "board_scan_paths": _multi(["    scan_paths:"]),
    "wip_exempt_types": _multi(["    wip_exempt_types:"]),
    "board_gates": _multi(["    gates:"]),
    "kanban_gates": _single(extra=["  gates:"]),
    "workflows": _single(extra=["  workflows:"]),
}
MOVE_CONFIGS = ["wip_exempt_types", "board_gates", "kanban_gates"]


@pytest.mark.parametrize("key", sorted(NULL_CONFIGS))
def test_cli_list_with_null_key_exits_0(tmp_path, monkeypatch, key):
    repo = _repo(tmp_path, NULL_CONFIGS[key])
    _assert_list_ok(_cli(repo, monkeypatch, ["list"]))


@pytest.mark.parametrize("key", MOVE_CONFIGS)
def test_cli_move_with_null_key_does_not_crash(tmp_path, monkeypatch, key):
    repo = _repo(tmp_path, NULL_CONFIGS[key])
    result = _cli(repo, monkeypatch, ["move", "FEAT-001", "ready", "--no-commit"])
    _assert_move_ok(result)


# --- nulls: controls ------------------------------------------------------------------


def test_control_absent_scan_paths_and_explicit_list(tmp_path):
    assert _load(tmp_path, _single(paths=['    root: "work/"'])).paths.scan_paths == []
    assert _load(tmp_path, _single()).paths.scan_paths == ["work/"]


def test_control_explicit_board_lists_kept(tmp_path):
    config = _load(tmp_path, _multi([
        "    scan_paths:", '      - "work/a/"', "    wip_exempt_types:", "      - epic",
    ]))
    board = config.boards[0]
    assert board.scan_paths == ["work/a/"]
    assert board.wip_exempt_types == ["epic"]
    assert board.gates == {}


def test_control_absent_board_keys_give_defaults(tmp_path):
    board = _load(tmp_path, _multi()).boards[0]
    assert (board.scan_paths, board.wip_exempt_types, board.gates) == ([], [], {})


@pytest.mark.parametrize("cfg", [_single(), _multi()], ids=["single", "multi"])
def test_control_list_and_move_without_nulls(tmp_path, monkeypatch, cfg):
    repo = _repo(tmp_path, cfg)
    _assert_list_ok(_cli(repo, monkeypatch, ["list"]))
    _assert_list_ok(_cli(repo, monkeypatch, ["move", "FEAT-001", "ready", "--no-commit"]))


# --- string ignore --------------------------------------------------------------------

STRING_IGNORE = {
    "single": _single(extra=[]).replace(
        '      - "work/"\n', f'      - "work/"\n    ignore: "{X_PATTERN}"\n'
    ),
    "multi": _multi([f'    ignore: "{X_PATTERN}"']),
}


def _ignores(config: KanbanConfig, kind: str) -> list[str]:
    return config.paths.ignore if kind == "single" else config.boards[0].ignore


@pytest.mark.parametrize("kind", ["single", "multi"])
def test_string_ignore_loads_as_one_pattern(tmp_path, kind):
    assert _ignores(_load(tmp_path, STRING_IGNORE[kind]), kind) == [X_PATTERN]


@pytest.mark.parametrize("kind", ["single", "multi"])
def test_string_ignore_scan_hides_only_matching(tmp_path, kind):
    repo = _repo(tmp_path, STRING_IGNORE[kind])
    config_mod._theme_cache.clear()
    service = KanbanService(KanbanConfig.load(repo / ".kanban" / "config.yaml"), repo)
    assert {i.id for i in service.scan()} == {"FEAT-001"}


@pytest.mark.parametrize("kind", ["single", "multi"])
def test_string_ignore_cli_list_shows_item_outside_x(tmp_path, monkeypatch, kind):
    repo = _repo(tmp_path, STRING_IGNORE[kind])
    result = _cli(repo, monkeypatch, ["list"])
    _assert_list_ok(result)
    assert "FEAT-001" in result.output, result.output
    assert "FEAT-002" not in result.output, result.output


def test_board_add_keeps_string_ignore_as_one_pattern(tmp_path, monkeypatch):
    repo = _repo(tmp_path, STRING_IGNORE["multi"])
    result = _cli(repo, monkeypatch, ["board-add", "b2", "--path", "b2/"])
    _assert_list_ok(result)
    saved = yaml.safe_load((repo / ".kanban" / "config.yaml").read_text())
    dev = next(b for b in saved["boards"] if b["name"] == "dev")
    assert dev.get("ignore") == [X_PATTERN], saved


@pytest.mark.parametrize("kind", ["single", "multi"])
def test_control_explicit_ignore_list_kept(tmp_path, kind):
    # ``ignore`` sits at 4 spaces in both layouts, so its items go at 6
    text = STRING_IGNORE[kind].replace(
        f'ignore: "{X_PATTERN}"', f'ignore:\n      - "{X_PATTERN}"'
    )
    assert _ignores(_load(tmp_path, text), kind) == [X_PATTERN]


# --- single-board ignore: [] round-trip -----------------------------------------------


def test_single_save_round_trip_keeps_empty_ignore(tmp_path):
    text = _single().replace('      - "work/"\n', '      - "work/"\n    ignore: []\n')
    config = _load(tmp_path, text)
    assert config.paths.ignore == []
    out = tmp_path / "saved" / "config.yaml"
    config.save(out)
    assert KanbanConfig.load(out).paths.ignore == [], out.read_text()
