"""Issue #1174: a file named `.kanban` where the allocations file's directory must be
is refused naming `.kanban` (the thing to fix), not `_ID_ALLOCATIONS.json`."""
from __future__ import annotations

from collections.abc import Iterator

import pytest

from tests.issues.test_585_create_push_loop import World, git, output_of
from tests.issues.test_603_push_failure_messages import invoke
from yurtle_kanban import config as config_mod
from yurtle_kanban.models import InputRefused
from yurtle_kanban.service import _local_allocations_text


def test_helper_names_kanban_as_a_file(tmp_path) -> None:
    (tmp_path / ".kanban").write_text("not a directory\n")
    with pytest.raises(InputRefused) as info:
        _local_allocations_text(tmp_path / ".kanban" / "_ID_ALLOCATIONS.json")
    said = str(info.value)
    assert ".kanban is a file" in said, said
    assert "_ID_ALLOCATIONS.json" not in said, said


@pytest.fixture
def world(tmp_path, monkeypatch: pytest.MonkeyPatch) -> World:
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    return World(tmp_path)


@pytest.fixture(autouse=True)
def _clean_theme_cache() -> Iterator[None]:
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


def _kanban_is_a_file(world: World) -> None:
    """The board config moves to `.yurtle-kanban/`, and `.kanban` becomes a file."""
    kanban = world.a / ".kanban"
    (world.a / ".yurtle-kanban").mkdir()
    (kanban / "config.yaml").rename(world.a / ".yurtle-kanban" / "config.yaml")
    for leftover in sorted(kanban.rglob("*"), reverse=True):
        leftover.unlink() if leftover.is_file() else leftover.rmdir()
    kanban.rmdir()
    kanban.write_text("not a directory\n")
    git(world.a, "remote", "remove", "origin")


@pytest.mark.parametrize(
    "argv",
    [["next-id", "EXP", "--no-sync", "--no-commit"], ["create", "expedition", "x"]],
    ids=["next-id", "create"],
)
def test_cli_refuses_naming_kanban(world, monkeypatch, argv) -> None:
    _kanban_is_a_file(world)
    before = git(world.a, "status", "--porcelain")
    result = invoke(world, monkeypatch, argv)
    out = output_of(result)
    assert result.exit_code != 0, out
    assert "Traceback" not in out, out
    assert ".kanban is a file" in out, out
    assert git(world.a, "status", "--porcelain") == before  # nothing written
