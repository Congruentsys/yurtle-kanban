"""Issue #622 — follow-ups from the reviews of #617 and #618.

#612 counts the source tree as yurtle-kanban's checkout when its ``pyproject.toml``
has a ``name = "yurtle-kanban"`` line, matched as plain text anywhere in the file.
Two gaps:

- ``name = "yurtle-kanban"`` under another table (``[tool.x]``) counted, though the
  project is named something else.
- ``name = "yurtle-kanban"  # comment`` in ``[project]`` did not count.

Decided ([steer] bucket 2): search for the name only within the ``[project]``
table, allowing a trailing comment.

1. ``[project] name = "other"`` with ``[tool.x] name = "yurtle-kanban"``: not a
   checkout; the share copy wins.
2. ``name = "yurtle-kanban"  # comment`` in ``[project]``: a checkout.
3. Controls: this repo's own pyproject.toml, and the #612 spellings, still count.

Fixtures come from the #592, #602 and #612 tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.issues.test_592_theme_source_first import (  # noqa: F401  (fixtures)
    BUILTIN,
    STALE_MARK,
    _clean_theme_cache,
    _empty_cwd,
    _stale,
    share,
)
from tests.issues.test_602_checkout_signal import CHECKOUT_MARK, _resolved, _share, _theme
from tests.issues.test_612_checkout_signal_name import COUNTS, _layout
from yurtle_kanban import config as config_mod

# a pyproject.toml whose [project] is another project, though another table names us
NOT_OURS = {
    "tool-table": '[project]\nname = "other"\n\n[tool.x]\nname = "yurtle-kanban"\n',
    "tool-table-first": '[tool.x]\nname = "yurtle-kanban"\n\n[project]\nname = "other"\n',
    "no-project-table": '[tool.x]\nname = "yurtle-kanban"\n',
}

# [project] names yurtle-kanban, with a trailing comment
COMMENTED = {
    "comment": '[project]\nname = "yurtle-kanban"  # comment\n',
    "comment-no-space": "[project]\nname = 'yurtle-kanban'# the package\n",
    "comment-then-table": (
        '[project]\nname = "yurtle-kanban"  # comment\nversion = "1"\n\n'
        '[tool.x]\nname = "other"\n'
    ),
}


def _counts(themes: Path) -> None:
    dirs = _resolved(config_mod._theme_dirs())
    assert themes.resolve() in dirs, dirs
    assert dirs.index(themes.resolve()) < dirs.index(_share().resolve())
    assert config_mod._load_builtin_theme(BUILTIN) == _theme(CHECKOUT_MARK)


# --- 1. the name must be in [project] ------------------------------------------------


@pytest.mark.usefixtures("share")
@pytest.mark.parametrize("pyproject", list(NOT_OURS.values()), ids=list(NOT_OURS))
def test_name_outside_project_table_does_not_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pyproject: str
) -> None:
    themes = _layout(tmp_path / "other", pyproject, monkeypatch)
    dirs = _resolved(config_mod._theme_dirs())
    assert themes.resolve() not in dirs, dirs
    loaded = config_mod._load_builtin_theme(BUILTIN)
    assert loaded is not None
    assert CHECKOUT_MARK not in loaded, "a name outside [project] counted as a checkout"
    assert loaded == _stale(BUILTIN)


# --- 2. a trailing comment is allowed --------------------------------------------------


@pytest.mark.usefixtures("share")
@pytest.mark.parametrize("pyproject", list(COMMENTED.values()), ids=list(COMMENTED))
def test_name_with_trailing_comment_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pyproject: str
) -> None:
    _counts(_layout(tmp_path / "checkout", pyproject, monkeypatch))


# --- 3. controls -----------------------------------------------------------------------


@pytest.mark.usefixtures("share")
def test_own_pyproject_still_counts() -> None:
    root = Path(config_mod.__file__).resolve().parent.parent.parent
    assert config_mod._is_own_checkout(root)
    assert (root / "themes").resolve() in _resolved(config_mod._theme_dirs())
    loaded = config_mod._load_builtin_theme(BUILTIN)
    assert loaded is not None
    assert STALE_MARK not in loaded


@pytest.mark.usefixtures("share")
@pytest.mark.parametrize("pyproject", list(COUNTS.values()), ids=list(COUNTS))
def test_612_spellings_still_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pyproject: str
) -> None:
    _counts(_layout(tmp_path / "checkout", pyproject, monkeypatch))
