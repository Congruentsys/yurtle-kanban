"""#210: cosmetic follow-ups — regex anchors, comment wording, CHANGELOG wrap,
README wording, and the ``repo_root`` annotation."""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from yurtle_kanban import turtle_builder
from yurtle_kanban.service import KanbanService
from yurtle_kanban.turtle_builder import InvalidTurtleName, _validate_turtle_local_name

ROOT = Path(__file__).resolve().parents[2]
SERVICE_SRC = (ROOT / "src" / "yurtle_kanban" / "service.py").read_text(encoding="utf-8")


def test_safe_local_name_has_no_redundant_anchors() -> None:
    pattern = turtle_builder._SAFE_LOCAL_NAME.pattern
    assert not pattern.startswith("^"), pattern
    assert not pattern.endswith("$"), pattern


@pytest.mark.parametrize("bad", ["H1\n", "X Y"])
def test_validator_still_refuses_unsafe_names(bad: str) -> None:
    with pytest.raises(InvalidTurtleName):
        _validate_turtle_local_name(bad)


def test_validator_still_accepts_safe_name() -> None:
    assert _validate_turtle_local_name("H1.2_a-b") == "H1.2_a-b"


def test_experiment_run_validator_comments_reworded() -> None:
    assert "# no `$`: #183" not in SERVICE_SRC
    assert SERVICE_SRC.count("would accept a trailing newline") >= 2


def _changelog_bullet(marker: str) -> list[str]:
    lines = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("- ") and marker in line)
    bullet = [lines[start]]
    for line in lines[start + 1 :]:
        if not line.strip() or not line.startswith(" "):
            break
        bullet.append(line)
    return bullet


def test_changelog_162_bullet_is_wrapped() -> None:
    long_lines = [line for line in _changelog_bullet("(#162)") if len(line) > 90]
    assert long_lines == []


def test_readme_names_create_push_for_git_skip() -> None:
    readme = " ".join((ROOT / "README.md").read_text(encoding="utf-8").split())
    assert "`create --push`, `move` and `comment`" in readme


def test_repo_root_annotation_accepts_str() -> None:
    param = inspect.signature(KanbanService.__init__).parameters["repo_root"]
    assert param.annotation == "Path | str"
