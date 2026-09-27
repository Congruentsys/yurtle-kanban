"""Issue #612 — follow-up from the review of PR #610 (#602).

#602 counts the source tree's ``themes/`` as a checkout's when a ``pyproject.toml``
sits beside it. A package three levels under ANOTHER project's root (e.g. vendored
into ``other/src/yurtle_kanban/``) passes that test, so the other project's
``themes/`` shadowed the share copy.

Decided ([steer] bucket 2): the sibling ``pyproject.toml`` must also declare
``name = "yurtle-kanban"``, matched as plain text (no tomli at runtime on 3.10).

1. Another project's root (``themes/`` plus ``pyproject.toml`` naming
   ``other-project``): the share copy wins, and that ``themes/`` is not in
   ``_theme_dirs``.
2. Controls: this worktree's real checkout still counts; a ``pyproject.toml`` with
   ``name = 'yurtle-kanban'`` (single quotes, extra spacing) counts.

Fixtures come from the #592 and #602 tests.
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
from tests.issues.test_602_checkout_signal import (
    CHECKOUT_MARK,
    _checkout,
    _point_package_at,
    _resolved,
    _share,
    _theme,
)
from yurtle_kanban import config as config_mod


def _layout(root: Path, pyproject: str, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A checkout-shaped tree at ``root`` whose ``pyproject.toml`` is ``pyproject``;
    the package is pointed at it. Returns its ``themes/``."""
    _point_package_at(_checkout(root), monkeypatch)
    (root / "pyproject.toml").write_text(pyproject)
    return root / "themes"


# --- 1. another project's root is not a checkout ------------------------------------


OTHER = '[project]\nname = "other-project"\nversion = "1.0"\n'


@pytest.mark.usefixtures("share")
def test_other_project_themes_do_not_shadow_share(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _layout(tmp_path / "other", OTHER, monkeypatch)
    loaded = config_mod._load_builtin_theme(BUILTIN)
    assert loaded is not None
    assert CHECKOUT_MARK not in loaded, "another project's themes/ shadowed the share copy"
    assert loaded == _stale(BUILTIN)


@pytest.mark.usefixtures("share")
def test_other_project_themes_not_in_theme_dirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    themes = _layout(tmp_path / "other", OTHER, monkeypatch)
    dirs = _resolved(config_mod._theme_dirs())
    assert themes.resolve() not in dirs, dirs
    assert _share().resolve() in dirs, dirs


# pyproject.toml spellings that name yurtle-kanban, so count as its checkout
COUNTS = {
    "single-quotes": "[project]\nname = 'yurtle-kanban'\n",
    "extra-spacing": "[project]\nname   =   'yurtle-kanban'\n",
    "no-spacing": '[project]\nname="yurtle-kanban"\n',
    "indented-after-table": (
        '[build-system]\nrequires = ["setuptools"]\n\n[project]\n  name  =  "yurtle-kanban"\n'
    ),
}


# --- 2. controls ---------------------------------------------------------------------


@pytest.mark.usefixtures("share")
def test_real_checkout_still_counts() -> None:
    root = Path(config_mod.__file__).resolve().parent.parent.parent
    dirs = _resolved(config_mod._theme_dirs())
    assert (root / "themes").resolve() in dirs, dirs
    loaded = config_mod._load_builtin_theme(BUILTIN)
    assert loaded is not None
    assert STALE_MARK not in loaded


@pytest.mark.usefixtures("share")
@pytest.mark.parametrize("pyproject", list(COUNTS.values()), ids=list(COUNTS))
def test_yurtle_kanban_pyproject_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pyproject: str
) -> None:
    themes = _layout(tmp_path / "checkout", pyproject, monkeypatch)
    dirs = _resolved(config_mod._theme_dirs())
    assert dirs.index(themes.resolve()) < dirs.index(_share().resolve())
    assert config_mod._load_builtin_theme(BUILTIN) == _theme(CHECKOUT_MARK)
