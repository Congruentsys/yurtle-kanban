"""#220: bare scalar config keys fall back to defaults; odd ``ignore``/``boards``.

Follow-ups from the review of PR #218 (#204).

Decided behaviour:

1. A bare scalar key (YAML null) is the same as an absent one: ``kanban.paths.root:``
   → ``"work/"``, ``kanban.theme:`` → ``"software"``, ``boards[].path:`` →
   ``"work/"``, ``boards[].name:`` → ``"default"``, ``boards[].preset:`` →
   ``"software"``. ``list`` exits 0 with each. ``boards[].wip_limits:`` null stays
   ``None`` (explicitly unlimited).
2. A bare ``kanban:`` loads the defaults; ``list`` exits 0 (pins #204's ``or {}``).
3. A null board entry (``boards:\\n  -\\n  - name: dev``) is skipped; ``list`` exits 0.
4. ``ignore: 5`` / ``ignore: {a: 1}`` is refused at load with a clear error naming
   ``ignore``; ``list`` exits non-zero with that message, no traceback.
5. A bare ``boards:`` alongside ``namespace:`` or ``default_board:`` still falls
   back to v1 (#204) but warns, mentioning ``boards``.

Controls: absent keys give the defaults; explicit values are kept.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import main
from yurtle_kanban.config import KanbanConfig, PathConfig

# --- config builders ------------------------------------------------------------------


def _single(*, root: str = '    root: "work/"', theme: str = "  theme: software") -> str:
    lines = ["kanban:", theme, "  paths:", root, "    scan_paths:", '      - "work/"']
    return "\n".join(lines) + "\n"


def _multi(
    *,
    name: str = "  - name: dev",
    preset: str = "    preset: software",
    path: str = '    path: "work/"',
    extra: list[str] | None = None,
) -> str:
    lines = ['version: "2.0"', "boards:", name, preset, path, *(extra or [])]
    return "\n".join(lines) + "\n"


def _single_ignore(value: str) -> str:
    return _single() + f"    ignore: {value}\n"


def _multi_ignore(value: str) -> str:
    return _multi(extra=[f"    ignore: {value}"])


BARE_KANBAN = "kanban:\n"
NULL_BOARD_ENTRY = 'version: "2.0"\nboards:\n  -\n  - name: dev\n    path: work/\n'
BARE_BOARDS_NS = 'version: "2.0"\nboards:\nnamespace: acme\n'
BARE_BOARDS_DEFAULT = 'version: "2.0"\nboards:\ndefault_board: dev\n'


# --- helpers --------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)


def _item(path: Path, item_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'---\nid: {item_id}\ntitle: "t"\ntype: feature\nstatus: backlog\n'
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
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    return repo


def _load(tmp_path: Path, text: str) -> KanbanConfig:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(text)
    config_mod._theme_cache.clear()
    return KanbanConfig.load(cfg)


def _list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str):
    repo = _repo(tmp_path, text)
    monkeypatch.chdir(repo)
    config_mod._theme_cache.clear()
    return CliRunner().invoke(main, ["list"])


def _assert_no_crash(result) -> None:
    assert "Traceback" not in (result.output or ""), result.output
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        raise AssertionError(repr(result.exception)) from result.exception


def _assert_list_ok(result) -> None:
    _assert_no_crash(result)
    assert result.exit_code == 0, result.output


# --- 1. bare scalars fall back to defaults --------------------------------------------


def test_bare_root_loads_default(tmp_path):
    assert _load(tmp_path, _single(root="    root:")).paths.root == "work/"


def test_bare_theme_loads_default(tmp_path):
    assert _load(tmp_path, _single(theme="  theme:")).theme == "software"


def test_bare_board_path_loads_default(tmp_path):
    config = _load(tmp_path, _multi(path="    path:"))
    assert config.boards[0].path == "work/"
    assert config.paths.root == "work/"


def test_bare_board_name_loads_default(tmp_path):
    assert _load(tmp_path, _multi(name="  - name:")).boards[0].name == "default"


def test_bare_board_preset_loads_default(tmp_path):
    config = _load(tmp_path, _multi(preset="    preset:"))
    assert config.boards[0].preset == "software"
    assert config.theme == "software"


BARE_SCALARS = {
    "root": _single(root="    root:"),
    "theme": _single(theme="  theme:"),
    "board_path": _multi(path="    path:"),
    "board_name": _multi(name="  - name:"),
    "board_preset": _multi(preset="    preset:"),
    "kanban": BARE_KANBAN,
    "null_board_entry": NULL_BOARD_ENTRY,
}


@pytest.mark.parametrize("key", sorted(BARE_SCALARS))
def test_cli_list_with_bare_key_exits_0(tmp_path, monkeypatch, key):
    _assert_list_ok(_list(tmp_path, monkeypatch, BARE_SCALARS[key]))


def test_control_bare_wip_limits_stays_none(tmp_path):
    board = _load(tmp_path, _multi(extra=["    wip_limits:"])).boards[0]
    assert board.wip_limits is None


# --- 2. bare kanban: ------------------------------------------------------------------


def test_bare_kanban_loads_defaults(tmp_path):
    config = _load(tmp_path, BARE_KANBAN)
    default = KanbanConfig()
    assert config.theme == default.theme == "software"
    assert config.paths == default.paths == PathConfig()
    assert config.is_multi_board is False


# --- 3. null board entry is skipped ---------------------------------------------------


def test_null_board_entry_is_skipped(tmp_path):
    config = _load(tmp_path, NULL_BOARD_ENTRY)
    assert [b.name for b in config.boards] == ["dev"]
    assert config.boards[0].path == "work/"
    assert config.is_multi_board is True


# --- 4. non-list, non-string ignore is refused ----------------------------------------

BAD_IGNORE = {
    "single_int": _single_ignore("5"),
    "single_map": _single_ignore("{a: 1}"),
    "multi_int": _multi_ignore("5"),
    "multi_map": _multi_ignore("{a: 1}"),
}


@pytest.mark.parametrize("kind", sorted(BAD_IGNORE))
def test_bad_ignore_refused_at_load(tmp_path, kind):
    with pytest.raises(ValueError, match="ignore"):
        _load(tmp_path, BAD_IGNORE[kind])


@pytest.mark.parametrize("kind", sorted(BAD_IGNORE))
def test_cli_list_refuses_bad_ignore_cleanly(tmp_path, monkeypatch, kind):
    result = _list(tmp_path, monkeypatch, BAD_IGNORE[kind])
    _assert_no_crash(result)
    assert result.exit_code != 0, result.output
    assert "ignore" in result.output, result.output


# --- 5. bare boards: with namespace/default_board warns -------------------------------

WARN_CONFIGS = {"namespace": BARE_BOARDS_NS, "default_board": BARE_BOARDS_DEFAULT}


def _warning_text(caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture) -> str:
    logged = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    return "\n".join([*logged, capsys.readouterr().err])


@pytest.mark.parametrize("key", sorted(WARN_CONFIGS))
def test_bare_boards_with_v2_keys_falls_back_and_warns(tmp_path, caplog, capsys, key):
    with caplog.at_level(logging.WARNING):
        config = _load(tmp_path, WARN_CONFIGS[key])
    assert config.is_multi_board is False
    assert config.boards == []
    assert config.paths == PathConfig()
    assert "boards" in _warning_text(caplog, capsys)


@pytest.mark.parametrize("key", sorted(WARN_CONFIGS))
def test_cli_list_with_bare_boards_and_v2_keys_exits_0(tmp_path, monkeypatch, key):
    _assert_list_ok(_list(tmp_path, monkeypatch, WARN_CONFIGS[key]))


def test_control_bare_boards_alone_does_not_warn(tmp_path, caplog, capsys):
    with caplog.at_level(logging.WARNING):
        _load(tmp_path, 'version: "2.0"\nboards:\n')
    assert "boards" not in _warning_text(caplog, capsys)


# --- controls -------------------------------------------------------------------------


def test_control_absent_scalars_give_defaults(tmp_path):
    single = _load(tmp_path, "kanban:\n  paths:\n    scan_paths: []\n")
    assert (single.paths.root, single.theme) == ("work/", "software")
    board = _load(tmp_path, 'version: "2.0"\nboards:\n  - scan_paths: []\n').boards[0]
    assert (board.name, board.preset, board.path) == ("default", "software", "work/")
    assert board.wip_limits == {}


def test_control_explicit_scalars_kept(tmp_path):
    single = _load(tmp_path, _single(root='    root: "tasks/"', theme="  theme: nautical"))
    assert (single.paths.root, single.theme) == ("tasks/", "nautical")
    board = _load(
        tmp_path,
        _multi(
            name="  - name: ops",
            preset="    preset: nautical",
            path='    path: "ops/"',
        ),
    ).boards[0]
    assert (board.name, board.preset, board.path) == ("ops", "nautical", "ops/")


@pytest.mark.parametrize("value", ['"**/x/**"', '["**/x/**"]', "[]"])
def test_control_valid_ignore_values_load(tmp_path, value):
    expected = [] if value == "[]" else ["**/x/**"]
    assert _load(tmp_path, _single_ignore(value)).paths.ignore == expected
    assert _load(tmp_path, _multi_ignore(value)).boards[0].ignore == expected


@pytest.mark.parametrize("cfg", [_single(), _multi()], ids=["single", "multi"])
def test_control_list_without_nulls(tmp_path, monkeypatch, cfg):
    _assert_list_ok(_list(tmp_path, monkeypatch, cfg))
