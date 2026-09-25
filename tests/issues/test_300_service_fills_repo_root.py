"""#300: ``KanbanService`` should fill ``config.repo_root`` when a bare config is used.

Follow-up from the review of PR #298 (#287). ``config.repo_root`` is only set by
``KanbanConfig.load()``. When the CLI or MCP server falls back to a bare
``KanbanConfig()``, ``config.get_theme()`` resolves from the cwd, so a repo's own
``.kanban/themes/`` override is never found from an unrelated cwd.

Decided behaviour:

1. ``KanbanService(config, repo)`` sets ``config.repo_root`` to the repo when it is
   None (a bare ``KanbanConfig()`` or one built directly, single- or multi-board), so
   ``config.get_theme()`` finds the repo's override whatever the cwd.

Control: a config loaded via ``KanbanConfig.load`` keeps its own ``repo_root``; the
service does not overwrite it.

Out of scope: theme-cache invalidation (the #287 pin test asserts no reload).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml

from yurtle_kanban import config as config_mod
from yurtle_kanban.config import CONFIG_VERSION_MULTI, BoardConfig, KanbanConfig
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
    """The built-in nautical theme (loaded with no repo, then uncached)."""
    theme = config_mod._load_builtin_theme(THEME)
    assert theme is not None and KEY not in theme
    config_mod._theme_cache.clear()
    return theme


@pytest.fixture
def elsewhere(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """cwd is a dir that is not the repo and has no .kanban/themes."""
    where = tmp_path / "elsewhere"
    where.mkdir()
    monkeypatch.chdir(where)
    return where


def _repo_with_override(root: Path, builtin: dict, marker: str) -> Path:
    themes = root / ".kanban" / "themes"
    themes.mkdir(parents=True)
    (themes / f"{THEME}.yaml").write_text(yaml.safe_dump({**builtin, KEY: marker}))
    return root


def _single_config_file(root: Path) -> Path:
    cfg = root / ".kanban" / "config.yaml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(["kanban:", f"  theme: {THEME}", "  paths:", '    root: "work/"'])
    cfg.write_text(text + "\n")
    return cfg


# --- 1. a bare / directly built config gets the service's repo root --------------------


def test_bare_config_gets_service_repo_root(tmp_path, elsewhere, builtin):
    repo = _repo_with_override(tmp_path / "repo", builtin, "A")
    config = KanbanConfig(theme=THEME)
    assert config.repo_root is None
    service = KanbanService(config, repo)
    assert config.repo_root is not None, "service left a bare config's repo_root unset"
    assert Path(config.repo_root).resolve() == repo.resolve()
    assert service.config is config


def test_bare_config_get_theme_finds_repo_override(tmp_path, elsewhere, builtin):
    repo = _repo_with_override(tmp_path / "repo", builtin, "A")
    config = KanbanConfig(theme=THEME)
    KanbanService(config, repo)
    config_mod._theme_cache.clear()
    theme = config.get_theme()
    assert theme is not None
    assert theme.get(KEY) == "A", "get_theme() looked in cwd, not the service's repo"


def test_direct_multi_board_config_get_theme_finds_repo_override(tmp_path, elsewhere, builtin):
    repo = _repo_with_override(tmp_path / "repo", builtin, "A")
    board = BoardConfig(name=BOARD, preset=THEME, path="work/")
    config = KanbanConfig(version=CONFIG_VERSION_MULTI, boards=[board])
    assert config.is_multi_board
    KanbanService(config, repo)
    config_mod._theme_cache.clear()
    theme = config.get_theme(BOARD)
    assert theme is not None
    assert theme.get(KEY) == "A", "get_theme(board) looked in cwd, not the service's repo"


# --- control: a loaded config keeps its own repo_root ----------------------------------


def test_control_loaded_config_keeps_own_repo_root(tmp_path, elsewhere, builtin):
    repo_a = _repo_with_override(tmp_path / "a", builtin, "A")
    repo_b = _repo_with_override(tmp_path / "b", builtin, "B")
    config = KanbanConfig.load(_single_config_file(repo_a))
    own = config.repo_root
    assert own is not None
    KanbanService(config, repo_b)
    assert config.repo_root == own, "service overwrote a loaded config's repo_root"
    config_mod._theme_cache.clear()
    theme = config.get_theme()
    assert theme is not None and theme.get(KEY) == "A"


def test_control_loaded_config_same_repo_unchanged(tmp_path, elsewhere, builtin):
    repo = _repo_with_override(tmp_path / "repo", builtin, "A")
    config = KanbanConfig.load(_single_config_file(repo))
    own = config.repo_root
    KanbanService(config, repo)
    assert config.repo_root == own
    theme = config.get_theme()
    assert theme is not None and theme.get(KEY) == "A"
