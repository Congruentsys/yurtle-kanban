"""Issue #592 — in a source checkout, the source tree's themes/ beats a stale share copy.

``config._theme_dirs`` looked up themes in the repo's ``.kanban/themes``, the cwd's,
then ``sys.prefix/share/yurtle-kanban/themes`` (copied once by ``pip install -e``),
and only then the source tree's ``themes/``. So the stale share copy always won and
edits to ``themes/*.yaml`` in a checkout or worktree were ignored (found in #588).

Expected:

1. When the package runs from a source checkout (its ``themes/`` directory exists),
   the source tree's theme is loaded, not the stale share copy, and
   ``_theme_dirs()`` lists the source tree before the share dir.
2. An installed wheel has no such directory: the share copy is used, as before.
3. A repo ``.kanban/themes/`` override still wins over both.
4. ``_available_themes`` still lists each theme name once.

Hermetic: ``sys.prefix`` points at a tmp dir holding the stale share copy; the real
theme files are never touched.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

import yurtle_kanban
from yurtle_kanban import config as config_mod

BUILTIN = "software"
BUILTIN_NAMES = ("hdd", "nautical", "software", "spec")
STALE_MARK = "stale_share_copy_592"
SHARE_ONLY = "shareonly592"


@pytest.fixture(autouse=True)
def _clean_theme_cache() -> Iterator[None]:
    """The cache is keyed by resolved theme file (#287); no test sees another's entries."""
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


@pytest.fixture(autouse=True)
def _empty_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The cwd's ``.kanban/themes`` is on the lookup path; keep it empty."""
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)


# the source tree's themes/ dir, fixed before any test repoints ``__file__``
SOURCE_THEMES = Path(config_mod.__file__).resolve().parent.parent.parent / "themes"


def _source_themes() -> Path:
    """The source tree's themes/ dir, as the checkout has it."""
    return SOURCE_THEMES


def _packaged(name: str) -> dict[str, Any]:
    """A packaged theme, read straight from the source tree."""
    path = _source_themes() / f"{name}.yaml"
    data = yaml.safe_load(path.read_text())
    assert isinstance(data, dict), path
    return data


def _stale(name: str) -> dict[str, Any]:
    """A share copy of ``name`` that differs from the source tree's."""
    data = dict(_packaged(name))
    data[STALE_MARK] = True
    return data


@pytest.fixture
def share(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """``sys.prefix`` at a tmp dir whose share/yurtle-kanban/themes holds stale copies
    of every built-in plus a theme only the share dir has."""
    prefix = tmp_path / "prefix"
    themes = prefix / "share" / "yurtle-kanban" / "themes"
    themes.mkdir(parents=True)
    for name in BUILTIN_NAMES:
        (themes / f"{name}.yaml").write_text(yaml.safe_dump(_stale(name)))
    (themes / f"{SHARE_ONLY}.yaml").write_text(yaml.safe_dump({"name": "Share only"}))
    monkeypatch.setattr(sys, "prefix", str(prefix))
    return themes


@pytest.fixture
def wheel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The package as an installed wheel: its files live where no themes/ sits beside."""
    pkg = tmp_path / "site" / "lib" / "site-packages" / "yurtle_kanban"
    pkg.mkdir(parents=True)
    monkeypatch.setattr(yurtle_kanban, "__file__", str(pkg / "__init__.py"))
    monkeypatch.setattr(config_mod, "__file__", str(pkg / "config.py"))
    for up in (pkg, *pkg.parents):
        assert not (up / "themes").exists(), up
    return pkg


def _index(dirs: list[Path], target: Path) -> int:
    resolved = [d.resolve() for d in dirs]
    assert target.resolve() in resolved, (target, dirs)
    return resolved.index(target.resolve())


# --- 1. source checkout: the source tree beats the stale share copy ---------------


def test_source_checkout_prefers_source_tree(share: Path) -> None:
    assert _source_themes().is_dir()
    loaded = config_mod._load_builtin_theme(BUILTIN)
    assert loaded is not None
    assert STALE_MARK not in loaded, "the stale share copy shadowed the source tree"
    assert loaded == _packaged(BUILTIN)


@pytest.mark.parametrize("name", BUILTIN_NAMES)
def test_every_builtin_loads_from_source_tree(share: Path, tmp_path: Path, name: str) -> None:
    repo = tmp_path / "repo"
    (repo / ".kanban" / "themes").mkdir(parents=True)
    loaded = config_mod._load_builtin_theme(name, repo)
    assert loaded == _packaged(name)


def test_theme_dirs_lists_source_before_share(share: Path) -> None:
    dirs = config_mod._theme_dirs()
    assert _index(dirs, _source_themes()) < _index(dirs, share)


def test_share_dir_still_consulted_after_source(share: Path) -> None:
    """A theme only the share dir has is still found."""
    assert config_mod._load_builtin_theme(SHARE_ONLY) == {"name": "Share only"}


# --- 2. installed wheel: no source themes/, the share copy is used ----------------


def test_wheel_uses_share_copy(share: Path, wheel: Path) -> None:
    loaded = config_mod._load_builtin_theme(BUILTIN)
    assert loaded == _stale(BUILTIN)


def test_wheel_theme_dirs_include_share(share: Path, wheel: Path) -> None:
    dirs = config_mod._theme_dirs()
    _index(dirs, share)
    assert not any((d / f"{BUILTIN}.yaml").exists() for d in dirs if d.resolve() != share.resolve())


# --- 3. a repo override wins over both ---------------------------------------------


def _repo_with_override(root: Path) -> dict[str, Any]:
    override = {"name": "Repo override (#592)", "repo_override_592": True}
    themes = root / ".kanban" / "themes"
    themes.mkdir(parents=True)
    (themes / f"{BUILTIN}.yaml").write_text(yaml.safe_dump(override))
    return override


def test_repo_override_beats_source_and_share(share: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    override = _repo_with_override(repo)
    assert config_mod._load_builtin_theme(BUILTIN, repo) == override


def test_repo_override_beats_share_in_wheel(share: Path, wheel: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    override = _repo_with_override(repo)
    assert config_mod._load_builtin_theme(BUILTIN, repo) == override


def test_repo_override_first_in_theme_dirs(share: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _repo_with_override(repo)
    dirs = config_mod._theme_dirs(repo)
    first = _index(dirs, repo / ".kanban" / "themes")
    assert first < _index(dirs, _source_themes())
    assert first < _index(dirs, share)


# --- 4. each theme name listed once ------------------------------------------------


def test_available_themes_lists_each_name_once(share: Path) -> None:
    names = config_mod._available_themes()
    assert len(names) == len(set(names)), names
    for name in (*BUILTIN_NAMES, SHARE_ONLY):
        assert names.count(name) == 1, (name, names)


def test_available_themes_once_in_wheel(share: Path, wheel: Path) -> None:
    names = config_mod._available_themes()
    assert len(names) == len(set(names)), names
    for name in (*BUILTIN_NAMES, SHARE_ONLY):
        assert names.count(name) == 1, (name, names)
