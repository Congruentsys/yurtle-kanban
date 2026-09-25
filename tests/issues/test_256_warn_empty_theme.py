"""#256: warn at load time on an explicit empty ``kanban.theme`` / ``boards[].preset``.

Follow-up from the review of PR #254 (#241): ``theme: ""`` / ``preset: ""`` loads an
empty theme silently, so the board gets no WIP limits and a generic title. ``""`` is
never a valid theme name.

Decided behaviour:

1. ``kanban.theme: ""`` or ``boards[].preset: ""`` emits ONE warning on the
   ``yurtle-kanban`` logger, naming the key (``theme`` / ``preset``, plus the board
   name for boards) and saying it is empty. The loaded value stays ``""`` (#241).
2. A whitespace-only value (``"  "``) warns the same way.
3. ``list`` still exits 0 with such a config.

Controls: a bare (null) key → the default, no warning; a valid theme/preset → no
warning; ``boards[].path: ""`` → no warning (it means the repo root, #241).
"""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from click.testing import CliRunner

from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import main
from yurtle_kanban.config import KanbanConfig

LOGGER = "yurtle-kanban"
BOARD = "devboard"

# --- config builders ------------------------------------------------------------------


def _single(*, theme: str = "  theme: software") -> str:
    lines = ["kanban:", theme, "  paths:", '    root: "work/"']
    return "\n".join(lines) + "\n"


def _multi(*, preset: str = "    preset: software", path: str = '    path: "work/"') -> str:
    lines = ['version: "2.0"', "boards:", f"  - name: {BOARD}", preset, path]
    return "\n".join(lines) + "\n"


# --- helpers --------------------------------------------------------------------------


@pytest.fixture
def warnings_log(caplog: pytest.LogCaptureFixture) -> Iterator[pytest.LogCaptureFixture]:
    """caplog, attached straight to the yurtle-kanban logger (whatever its propagation)."""
    log = logging.getLogger(LOGGER)
    log.addHandler(caplog.handler)
    old_level = log.level
    log.setLevel(logging.WARNING)
    caplog.handler.setLevel(logging.WARNING)
    try:
        yield caplog
    finally:
        log.removeHandler(caplog.handler)
        log.setLevel(old_level)


def _warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    # the handler sits on both root (caplog) and our logger: one record, seen twice
    records = {id(r): r for r in caplog.records}.values()
    return [r.getMessage() for r in records if r.name == LOGGER and r.levelno >= logging.WARNING]


def _empty_warnings(caplog: pytest.LogCaptureFixture, key: str) -> list[str]:
    return [m for m in _warnings(caplog) if key in m and "empty" in m.lower()]


def _load(tmp_path: Path, text: str) -> KanbanConfig:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(text)
    config_mod._theme_cache.clear()
    return KanbanConfig.load(cfg)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)


def _repo(tmp_path: Path, config_yaml: str) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@t.com")
    _git(repo, "config", "user.name", "T")
    (repo / ".kanban").mkdir()
    (repo / ".kanban" / "config.yaml").write_text(config_yaml)
    (repo / "work").mkdir()
    (repo / "work" / ".keep").write_text("")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    return repo


def _list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str):
    repo = _repo(tmp_path, text)
    monkeypatch.chdir(repo)
    config_mod._theme_cache.clear()
    return CliRunner().invoke(main, ["list"])


def _assert_list_ok(result) -> None:
    assert "Traceback" not in (result.output or ""), result.output
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        raise AssertionError(repr(result.exception)) from result.exception
    assert result.exit_code == 0, result.output


# --- 1. explicit "" warns once; value unchanged ---------------------------------------


def test_empty_theme_warns_once(tmp_path, warnings_log):
    config = _load(tmp_path, _single(theme='  theme: ""'))
    assert config.theme == ""  # #241 unchanged
    found = _empty_warnings(warnings_log, "theme")
    assert len(found) == 1, _warnings(warnings_log)


def test_empty_board_preset_warns_once_naming_board(tmp_path, warnings_log):
    config = _load(tmp_path, _multi(preset='    preset: ""'))
    assert config.boards[0].preset == ""  # #241 unchanged
    found = _empty_warnings(warnings_log, "preset")
    assert len(found) == 1, _warnings(warnings_log)
    assert BOARD in found[0], found[0]


# --- 2. whitespace-only warns the same ------------------------------------------------


def test_whitespace_theme_warns_once(tmp_path, warnings_log):
    _load(tmp_path, _single(theme='  theme: "  "'))
    found = _empty_warnings(warnings_log, "theme")
    assert len(found) == 1, _warnings(warnings_log)


def test_whitespace_board_preset_warns_once_naming_board(tmp_path, warnings_log):
    _load(tmp_path, _multi(preset='    preset: "  "'))
    found = _empty_warnings(warnings_log, "preset")
    assert len(found) == 1, _warnings(warnings_log)
    assert BOARD in found[0], found[0]


# --- 3. list still exits 0 ------------------------------------------------------------


def test_cli_list_empty_theme_exits_zero(tmp_path, monkeypatch):
    _assert_list_ok(_list(tmp_path, monkeypatch, _single(theme='  theme: ""')))


def test_cli_list_empty_board_preset_exits_zero(tmp_path, monkeypatch):
    _assert_list_ok(_list(tmp_path, monkeypatch, _multi(preset='    preset: ""')))


# --- controls: no warning -------------------------------------------------------------


def test_control_bare_theme_no_warning(tmp_path, warnings_log):
    assert _load(tmp_path, _single(theme="  theme:")).theme == "software"
    assert _warnings(warnings_log) == []


def test_control_bare_board_preset_no_warning(tmp_path, warnings_log):
    assert _load(tmp_path, _multi(preset="    preset:")).boards[0].preset == "software"
    assert _warnings(warnings_log) == []


def test_control_valid_theme_no_warning(tmp_path, warnings_log):
    assert _load(tmp_path, _single()).theme == "software"
    assert _warnings(warnings_log) == []


def test_control_valid_board_preset_no_warning(tmp_path, warnings_log):
    assert _load(tmp_path, _multi()).boards[0].preset == "software"
    assert _warnings(warnings_log) == []


def test_control_empty_board_path_no_warning(tmp_path, warnings_log):
    assert _load(tmp_path, _multi(path='    path: ""')).boards[0].path == ""
    assert _warnings(warnings_log) == []
