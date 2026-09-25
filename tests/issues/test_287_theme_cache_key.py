"""#287: ``_theme_cache`` is keyed by theme name only; ``get_theme()`` lacks repo_root.

Follow-up from the review of PR #284 (#272). In a process that serves several repos
(MCP, a dashboard, tests), the first lookup of ``nautical`` decides that theme for
every repo, including one with its own ``.kanban/themes/nautical.yaml``. And
``KanbanConfig.get_theme()`` calls the loader without the config's repo root, so from
an unrelated cwd a repo override is never found.

Decided behaviour:

1. The cache is keyed by the resolved theme FILE (or by ``(repo_root, name)``): two
   repos in one process never share an entry when their overrides differ. Each repo's
   service gets its own override, or the built-in when it has none, in either order.
2. ``KanbanConfig.load(<repo>/.kanban/config.yaml).get_theme()`` resolves themes in
   the config's own repo, whatever the cwd (single-board, and multi-board by name).

Controls: a theme file changed on disk after its first load in the same process is
NOT reloaded (today's caching, pinned so a change is visible); a repo with no
override still gets the built-in.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml

from yurtle_kanban import config as config_mod
from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.service import KanbanService

THEME = "nautical"
BOARD = "devboard"
KEY = "override_marker"


@pytest.fixture(autouse=True)
def _clean_theme_cache() -> Iterator[None]:
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


@pytest.fixture
def builtin() -> dict:
    """The built-in nautical theme (loaded from an empty dir, then uncached)."""
    theme = config_mod._load_builtin_theme(THEME)
    assert theme is not None and KEY not in theme
    config_mod._theme_cache.clear()
    return theme


@pytest.fixture
def elsewhere(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """cwd is a dir that is neither repo and has no .kanban/themes."""
    where = tmp_path / "elsewhere"
    where.mkdir()
    monkeypatch.chdir(where)
    return where


# --- builders -------------------------------------------------------------------------


def _multi() -> str:
    lines = ['version: "2.0"', "boards:", f"  - name: {BOARD}", f"    preset: {THEME}"]
    return "\n".join(lines + ['    path: "work/"']) + "\n"


def _single() -> str:
    return "\n".join(["kanban:", f"  theme: {THEME}", "  paths:", '    root: "work/"']) + "\n"


def _repo(root: Path, text: str, builtin: dict, marker: str | None = None) -> Path:
    """``root/.kanban/config.yaml``; with a marker, an override of the built-in theme."""
    kanban = root / ".kanban"
    kanban.mkdir(parents=True)
    cfg = kanban / "config.yaml"
    cfg.write_text(text)
    if marker is not None:
        _write_override(root, builtin, marker)
    return cfg


def _write_override(root: Path, builtin: dict, marker: str) -> Path:
    themes = root / ".kanban" / "themes"
    themes.mkdir(parents=True, exist_ok=True)
    path = themes / f"{THEME}.yaml"
    path.write_text(yaml.safe_dump({**builtin, KEY: marker}))
    return path


def _service_theme(repo: Path, cfg: Path) -> dict:
    config = KanbanConfig.load(cfg)
    service = KanbanService(config, repo)
    theme = service._load_board_theme(config.boards[0])
    assert theme is not None
    return theme


# --- 1. two repos in one process: the first caller does not decide for both ------------


@pytest.mark.parametrize("a_first", [True, False], ids=["A-then-B", "B-then-A"])
def test_override_and_builtin_repos_each_get_own(tmp_path, elsewhere, builtin, a_first):
    repo_a, repo_b = tmp_path / "a", tmp_path / "b"
    cfg_a = _repo(repo_a, _multi(), builtin, marker="A")
    cfg_b = _repo(repo_b, _multi(), builtin)  # no override: the built-in
    order = [("A", repo_a, cfg_a), ("B", repo_b, cfg_b)]
    got = {name: _service_theme(repo, cfg) for name, repo, cfg in order[:: 1 if a_first else -1]}
    assert got["A"].get(KEY) == "A", "repo A's override lost to the shared cache"
    assert KEY not in got["B"], "repo B (no override) got repo A's theme"
    assert got["B"] == builtin


@pytest.mark.parametrize("a_first", [True, False], ids=["A-then-B", "B-then-A"])
def test_two_different_overrides_each_get_their_own(tmp_path, elsewhere, builtin, a_first):
    repo_a, repo_b = tmp_path / "a", tmp_path / "b"
    cfg_a = _repo(repo_a, _multi(), builtin, marker="A")
    cfg_b = _repo(repo_b, _multi(), builtin, marker="B")
    order = [("A", repo_a, cfg_a), ("B", repo_b, cfg_b)]
    got = {name: _service_theme(repo, cfg) for name, repo, cfg in order[:: 1 if a_first else -1]}
    assert got["A"].get(KEY) == "A"
    assert got["B"].get(KEY) == "B"


def test_loader_two_repos_same_name_not_shared(tmp_path, elsewhere, builtin):
    """The loader itself, with explicit repo roots (what every service call passes)."""
    repo_a, repo_b = tmp_path / "a", tmp_path / "b"
    _write_override(repo_a, builtin, "A")
    _write_override(repo_b, builtin, "B")
    a = config_mod._load_builtin_theme(THEME, repo_a)
    b = config_mod._load_builtin_theme(THEME, repo_b)
    plain = config_mod._load_builtin_theme(THEME)
    assert a is not None and a.get(KEY) == "A"
    assert b is not None and b.get(KEY) == "B"
    assert plain is not None and KEY not in plain


# --- 2. KanbanConfig.get_theme() resolves in the config's own repo --------------------


def test_single_board_config_get_theme_uses_its_repo(tmp_path, elsewhere, builtin):
    cfg = _repo(tmp_path / "repo", _single(), builtin, marker="A")
    config = KanbanConfig.load(cfg)
    config_mod._theme_cache.clear()  # not a load-time cache hit: get_theme must find it
    theme = config.get_theme()
    assert theme is not None
    assert theme.get(KEY) == "A", "get_theme() looked in cwd, not the config's repo"


def test_multi_board_config_get_theme_by_board_uses_its_repo(tmp_path, elsewhere, builtin):
    cfg = _repo(tmp_path / "repo", _multi(), builtin, marker="A")
    config = KanbanConfig.load(cfg)
    config_mod._theme_cache.clear()
    theme = config.get_theme(BOARD)
    assert theme is not None
    assert theme.get(KEY) == "A", "get_theme(board) looked in cwd, not the config's repo"


def test_single_board_get_theme_two_repos(tmp_path, elsewhere, builtin):
    """Config A's get_theme() is A's override even after repo B loaded the built-in."""
    cfg_b = _repo(tmp_path / "b", _single(), builtin)
    cfg_a = _repo(tmp_path / "a", _single(), builtin, marker="A")
    theme_b = KanbanConfig.load(cfg_b).get_theme()
    theme_a = KanbanConfig.load(cfg_a).get_theme()
    assert theme_b is not None and KEY not in theme_b
    assert theme_a is not None and theme_a.get(KEY) == "A"


# --- controls -------------------------------------------------------------------------


def test_control_builtin_only_repo_gets_builtin(tmp_path, elsewhere, builtin):
    repo = tmp_path / "repo"
    cfg = _repo(repo, _multi(), builtin)
    assert _service_theme(repo, cfg) == builtin
    single = _repo(tmp_path / "single", _single(), builtin)
    assert KanbanConfig.load(single).get_theme() == builtin


def test_control_changed_theme_file_is_not_reloaded(tmp_path, elsewhere, builtin):
    """Today's behaviour, pinned: a theme is read once per process; a later edit of the
    same file is not picked up (the cache has no mtime check). Change on purpose only."""
    repo = tmp_path / "repo"
    cfg = _repo(repo, _multi(), builtin, marker="first")
    assert _service_theme(repo, cfg).get(KEY) == "first"
    _write_override(repo, builtin, "second")
    assert _service_theme(repo, cfg).get(KEY) == "first"
