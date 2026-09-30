"""Issue #1179: with `.kanban` a file (the config in `.yurtle-kanban/`), the writers
`init`, `board-add` and `control halt` crashed with a raw FileExistsError traceback.

Decided shape:
1. `init` refuses: exit != 0, one `Error:` line naming ".kanban is a file", no
   traceback, and nothing created.
2. `board-add` saves to the config file it LOADED. With the config in
   `.yurtle-kanban/config.yaml` it writes there and never creates or writes `.kanban/`,
   whether `.kanban` is a file or absent. A config in `.kanban/config.yaml` still
   saves there (control).
3. `control halt` refuses as `init` does, and nothing is committed or pushed.
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import CliRunner

from tests.issues._snapshot import paths_outside_git
from tests.issues.test_585_create_push_loop import World, git, output_of
from tests.issues.test_603_push_failure_messages import invoke
from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import main

KANBAN_FILE_TEXT = "not a directory\n"
BOARD = ["board-add", "research", "--preset", "software", "--path", "kanban-work/"]


@pytest.fixture
def world(tmp_path, monkeypatch: pytest.MonkeyPatch) -> World:
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    monkeypatch.delenv("YURTLE_AGENT", raising=False)
    return World(tmp_path)


@pytest.fixture(autouse=True)
def _clean_theme_cache() -> Iterator[None]:
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


def _config_in_yurtle_kanban(world: World, kanban_file: bool) -> None:
    """Move the board config to `.yurtle-kanban/`; `.kanban` becomes a file, or is gone.
    Committed and pushed, so origin and A agree (the remote stays)."""
    kanban = world.a / ".kanban"
    (world.a / ".yurtle-kanban").mkdir()
    (kanban / "config.yaml").rename(world.a / ".yurtle-kanban" / "config.yaml")
    for leftover in sorted(kanban.rglob("*"), reverse=True):
        leftover.unlink() if leftover.is_file() else leftover.rmdir()
    kanban.rmdir()
    if kanban_file:
        kanban.write_text(KANBAN_FILE_TEXT)
    git(world.a, "add", "-A")
    git(world.a, "commit", "-m", "config in .yurtle-kanban")
    git(world.a, "push", "origin", f"HEAD:refs/heads/{world.default}")


def _no_crash(result: Any) -> None:
    """CliRunner swallows an uncaught exception's traceback: check the exception."""
    exc = result.exception
    assert exc is None or isinstance(exc, SystemExit), f"crashed: {exc!r}"


def _refused_naming_kanban(result: Any) -> None:
    out = output_of(result)
    assert result.exit_code != 0, out
    _no_crash(result)
    assert "Traceback" not in out, out
    assert "FileExistsError" not in out, out
    assert ".kanban is a file" in out, out
    errors = [line for line in out.splitlines() if line.startswith("Error:")]
    assert len(errors) == 1, out
    assert ".kanban is a file" in errors[0], out


def _boards(config_file: Path) -> list[str]:
    data = yaml.safe_load(config_file.read_text()) or {}
    kanban = data.get("kanban", data)
    return [b.get("name") for b in (kanban.get("boards") or [])]


# --- 1. init -----------------------------------------------------------------------


def test_init_refuses_when_kanban_is_a_file(tmp_path, monkeypatch) -> None:
    (tmp_path / ".kanban").write_text(KANBAN_FILE_TEXT)
    before = sorted(p.relative_to(tmp_path) for p in paths_outside_git(tmp_path))
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(main, ["init"])
    _refused_naming_kanban(result)
    assert (tmp_path / ".kanban").read_text() == KANBAN_FILE_TEXT
    after = sorted(p.relative_to(tmp_path) for p in paths_outside_git(tmp_path))
    assert after == before, f"init created {set(after) - set(before)}"


# --- 2. board-add ------------------------------------------------------------------


@pytest.mark.parametrize("kanban_file", [True, False], ids=["kanban-file", "no-kanban"])
def test_board_add_saves_to_yurtle_kanban_config(world, monkeypatch, kanban_file) -> None:
    _config_in_yurtle_kanban(world, kanban_file)
    result = invoke(world, monkeypatch, BOARD)
    out = output_of(result)
    _no_crash(result)
    assert result.exit_code == 0, out
    assert "research" in _boards(world.a / ".yurtle-kanban" / "config.yaml"), out
    kanban = world.a / ".kanban"
    if kanban_file:
        assert kanban.is_file() and kanban.read_text() == KANBAN_FILE_TEXT
    else:
        assert not kanban.exists(), f"board-add created {sorted(kanban.rglob('*'))}"


def test_board_add_control_kanban_config_still_saves_there(world, monkeypatch) -> None:
    result = invoke(world, monkeypatch, BOARD)
    out = output_of(result)
    assert result.exit_code == 0, out
    assert "research" in _boards(world.a / ".kanban" / "config.yaml"), out
    assert not (world.a / ".yurtle-kanban").exists()


# --- 3. control halt ---------------------------------------------------------------


@pytest.mark.parametrize("remote", [True, False], ids=["origin", "no-remote"])
def test_control_halt_refuses_when_kanban_is_a_file(world, monkeypatch, remote) -> None:
    _config_in_yurtle_kanban(world, kanban_file=True)
    if not remote:
        git(world.a, "remote", "remove", "origin")
    head = git(world.a, "rev-parse", "HEAD").strip()
    origin = world.remote_sha()
    status = git(world.a, "status", "--porcelain")
    result = invoke(world, monkeypatch, ["control", "halt", "--reason", "x", "--agent", "A"])
    _refused_naming_kanban(result)
    assert world.remote_sha() == origin, "control halt pushed"
    assert git(world.a, "for-each-ref", "refs/heads").count("\n") == 1
    assert git(world.a, "rev-parse", "HEAD").strip() == head, "control halt committed"
    assert git(world.a, "status", "--porcelain") == status
    assert (world.a / ".kanban").read_text() == KANBAN_FILE_TEXT
