"""Issue #602 — follow-up from the review of PR #597 (#592).

#592 put the source tree's ``themes/`` before the pip ``share`` copy whenever a
``themes/`` directory exists three levels above the package file. Two gaps:

- An installed wheel lives at ``<prefix>/lib/pythonX.Y/site-packages/yurtle_kanban/``,
  so that path is ``<prefix>/lib/pythonX.Y/themes/``. A stray directory there
  counted as a checkout and shadowed the share copy.
- The two source paths (from ``yurtle_kanban.__file__`` and ``config.__file__``) were
  de-duplicated as spelled, so a symlinked checkout listed the same dir twice.

Decided ([steer] bucket 2): the source tree counts as a checkout only when
``pyproject.toml`` sits beside its ``themes/``; paths are resolved before
de-duplicating.

1. Wheel layout with a stray ``themes/`` and no ``pyproject.toml``: the share copy
   wins, and the stray dir is not in ``_theme_dirs``.
2. A checkout (``themes/`` with ``pyproject.toml`` beside it) still beats the share
   copy, as #592 made it.
3. A checkout reached through a symlink lists its ``themes/`` once.

Fixtures (stale share copy under a tmp ``sys.prefix``, empty cwd, clean theme cache)
come from the #592 tests.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

import yurtle_kanban
from tests.issues.test_592_theme_source_first import (  # noqa: F401  (fixtures)
    BUILTIN,
    _clean_theme_cache,
    _empty_cwd,
    _stale,
    share,
)
from yurtle_kanban import config as config_mod

PYVER = f"python{sys.version_info.major}.{sys.version_info.minor}"
CHECKOUT_MARK = "checkout_copy_602"
STRAY_MARK = "stray_themes_dir_602"


def _theme(mark: str) -> dict[str, Any]:
    return {"name": f"#602 {mark}", mark: True}


def _point_package_at(pkg: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Both source paths the code derives: ``yurtle_kanban.__file__`` and ``config.__file__``."""
    monkeypatch.setattr(yurtle_kanban, "__file__", str(pkg / "__init__.py"))
    monkeypatch.setattr(config_mod, "__file__", str(pkg / "config.py"))


def _checkout(root: Path) -> Path:
    """A source checkout at ``root``: src/yurtle_kanban/, themes/, pyproject.toml.
    Returns the package dir."""
    pkg = root / "src" / "yurtle_kanban"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "config.py").write_text("")
    themes = root / "themes"
    themes.mkdir()
    (themes / f"{BUILTIN}.yaml").write_text(yaml.safe_dump(_theme(CHECKOUT_MARK)))
    (root / "pyproject.toml").write_text('[project]\nname = "yurtle-kanban"\n')
    return pkg


def _share() -> Path:
    """The stale share dir the #592 ``share`` fixture put under ``sys.prefix``."""
    return Path(sys.prefix) / "share" / "yurtle-kanban" / "themes"


def _resolved(dirs: list[Path]) -> list[Path]:
    return [d.resolve() for d in dirs]


# --- 1. wheel layout: a stray themes/ without pyproject.toml is not a checkout ------


@pytest.fixture
def stray_wheel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """``<prefix>/lib/pythonX.Y/site-packages/yurtle_kanban/`` with a stray
    ``<prefix>/lib/pythonX.Y/themes/`` and no ``pyproject.toml``. Returns the stray dir."""
    libdir = tmp_path / "wheelprefix" / "lib" / PYVER
    pkg = libdir / "site-packages" / "yurtle_kanban"
    pkg.mkdir(parents=True)
    stray = libdir / "themes"
    stray.mkdir()
    (stray / f"{BUILTIN}.yaml").write_text(yaml.safe_dump(_theme(STRAY_MARK)))
    assert not (libdir / "pyproject.toml").exists()
    _point_package_at(pkg, monkeypatch)
    return stray


@pytest.mark.usefixtures("share")
def test_wheel_stray_themes_dir_does_not_shadow_share(stray_wheel: Path) -> None:
    loaded = config_mod._load_builtin_theme(BUILTIN)
    assert loaded is not None
    assert STRAY_MARK not in loaded, "a stray themes/ beside a wheel shadowed the share copy"
    assert loaded == _stale(BUILTIN)


@pytest.mark.usefixtures("share")
def test_wheel_stray_themes_dir_not_in_theme_dirs(stray_wheel: Path) -> None:
    dirs = _resolved(config_mod._theme_dirs())
    assert stray_wheel.resolve() not in dirs, dirs
    assert _share().resolve() in dirs, dirs


# --- 2. a checkout (themes/ + pyproject.toml) still beats the share copy -------------


@pytest.mark.usefixtures("share")
def test_checkout_with_pyproject_beats_share(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "checkout"
    _point_package_at(_checkout(root), monkeypatch)
    assert config_mod._load_builtin_theme(BUILTIN) == _theme(CHECKOUT_MARK)
    dirs = _resolved(config_mod._theme_dirs())
    assert dirs.index((root / "themes").resolve()) < dirs.index(_share().resolve())


@pytest.mark.usefixtures("share")
def test_real_checkout_still_beats_share() -> None:
    """This test run's own checkout has pyproject.toml beside themes/."""
    root = Path(config_mod.__file__).resolve().parent.parent.parent
    assert (root / "pyproject.toml").is_file()
    loaded = config_mod._load_builtin_theme(BUILTIN)
    assert loaded is not None
    assert "stale_share_copy_592" not in loaded


# --- 3. symlinked checkout: its themes/ listed once ---------------------------------


@pytest.mark.usefixtures("share")
@pytest.mark.parametrize("linked", ["package", "config"])
def test_symlinked_checkout_listed_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, linked: str
) -> None:
    """One source path goes through a symlink to the checkout, the other doesn't:
    after resolving, the checkout's themes/ appears once in ``_theme_dirs``."""
    root = tmp_path / "checkout"
    pkg = _checkout(root)
    link = tmp_path / "link"
    link.symlink_to(root, target_is_directory=True)
    via_link = link / "src" / "yurtle_kanban"
    if linked == "package":
        monkeypatch.setattr(yurtle_kanban, "__file__", str(via_link / "__init__.py"))
        monkeypatch.setattr(config_mod, "__file__", str(pkg / "config.py"))
    else:
        monkeypatch.setattr(yurtle_kanban, "__file__", str(pkg / "__init__.py"))
        monkeypatch.setattr(config_mod, "__file__", str(via_link / "config.py"))
    dirs = _resolved(config_mod._theme_dirs())
    assert dirs.count((root / "themes").resolve()) == 1, dirs
    assert len(dirs) == len(set(dirs)), dirs
    assert config_mod._load_builtin_theme(BUILTIN) == _theme(CHECKOUT_MARK)
