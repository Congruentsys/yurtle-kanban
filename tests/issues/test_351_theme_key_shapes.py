"""Issue #351 — a mapping theme whose section keys are not mappings.

Follow-up from the review of PR #345 (#338). #338 made a theme FILE that is a
YAML list/scalar count as missing; a theme that IS a mapping but has, say,
``columns: 5`` still reaches ``theme["columns"].items()`` and crashes:

- ``board``: ``service.py`` ``_get_columns_from_theme`` / ``_get_columns_from_preset``
  → ``AttributeError: 'int' object has no attribute 'items'``;
- ``status_mappings``: ``_get_column_status_map`` / ``_get_reverse_status_mapping``;
- ``item_types``: ``cli.init``, ``_get_type_prefix`` (create), ``_map_theme_type`` (scan);
- ``transitions``: ``_validate_transition`` → ``board_transitions.get(...)``;
- ``theme``: ``epic create`` → ``theme.get("theme", {}).get("name")``.

Decided behaviour (bucket 2, #338 precedent):

1. At load, ``_load_builtin_theme`` drops any of the section keys ``columns``,
   ``item_types``, ``status_mappings``, ``transitions``, ``id_formats``,
   ``status_aliases``, ``theme`` whose value is not a mapping (list, str, int,
   bool), with ONE warning naming the file and the key; the rest of the theme
   loads.
2. Consumers then behave as for a theme without that section (no traceback).
3. Controls: a well-formed custom theme loads unchanged, a theme missing a section
   behaves as today.

Null (``columns:`` with no value): today it is NOT "absent" — ``"columns" in
theme`` is true and ``None.items()`` crashes the same way — so null is red here
too: it must be dropped like the other shapes. Whether a null warns is left to
the driver (the tests only require at most one warning for it).
"""

from __future__ import annotations

import logging
import re
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import CliRunner

from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import main

LOGGER = "yurtle-kanban"
THEME = "mytheme"
THEME_FILE = f"{THEME}.yaml"

SECTION_KEYS = [
    "columns",
    "item_types",
    "status_mappings",
    "transitions",
    "id_formats",
    "status_aliases",
    "theme",
]
BAD_SHAPES: dict[str, Any] = {
    "list": ["a", "b"],
    "str": "x",
    "int": 5,
    "bool": True,
    "null": None,
}

# A well-formed theme with every section a mapping.
GOOD_THEME: dict[str, Any] = {
    "theme": {"name": "acme", "description": "custom"},
    "item_types": {"widget": {"id_prefix": "WID", "path": "work/widgets/"}},
    "columns": {
        "todo": {"name": "Todo", "order": 1},
        "doing": {"name": "Doing", "order": 2, "wip_limit": 2},
        "shipped": {"name": "Shipped", "order": 3},
    },
    "status_mappings": {"todo": "backlog", "doing": "in_progress", "shipped": "done"},
    "transitions": {"todo": ["doing"], "doing": ["shipped", "todo"], "shipped": []},
    "id_formats": {"widget": "WID-{n:03d}"},
    "status_aliases": {"wip": "doing"},
}

SINGLE_CFG = f"kanban:\n  theme: {THEME}\n  paths:\n    root: work/\n"
MULTI_CFG = (
    'version: "2.0"\nboards:\n  - name: devboard\n'
    f"    preset: {THEME}\n    path: work/\n"
)


# --- fixtures / helpers (as in test_338) ------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_theme_cache() -> Iterator[None]:
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


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
    records = {id(r): r for r in caplog.records}.values()
    return [r.getMessage() for r in records if r.name == LOGGER and r.levelno >= logging.WARNING]


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)


def _theme_yaml(**overrides: Any) -> str:
    return yaml.safe_dump(overrides, sort_keys=False)


def _repo(root: Path, config: str | None, theme: str | None = None) -> Path:
    """A git repo at ``root`` with ``.kanban/config.yaml`` (+ ``themes/mytheme.yaml``)."""
    root.mkdir(parents=True, exist_ok=True)
    kanban = root / ".kanban"
    kanban.mkdir()
    if config is not None:
        (kanban / "config.yaml").write_text(config)
    if theme is not None:
        (kanban / "themes").mkdir()
        (kanban / "themes" / THEME_FILE).write_text(theme)
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "t@t.com")
    _git(root, "config", "user.name", "T")
    (root / "work").mkdir()
    (root / "work" / ".keep").write_text("")
    return root


def _item(repo: Path, item_id: str, item_type: str, status: str) -> Path:
    path = repo / "work" / f"{item_id}.md"
    path.write_text(
        f"---\nid: {item_id}\ntype: {item_type}\nstatus: {status}\n"
        f"title: Item {item_id}\n---\n\n# Item {item_id}\n"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "seed")
    return path


def _invoke(repo: Path, monkeypatch: pytest.MonkeyPatch, args: list[str]):
    monkeypatch.chdir(repo)
    config_mod._theme_cache.clear()
    return CliRunner().invoke(main, args)


def _no_crash(result) -> None:
    """No exception other than SystemExit reached the runner, no traceback printed."""
    out = result.output or ""
    assert "Traceback" not in out, out
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        raise AssertionError(repr(result.exception)) from result.exception


def _names_key(message: str, key: str) -> bool:
    """The warning names ``key`` quoted (`key`, 'key', "key") or as ``key:`` — a bare
    substring would be satisfied by "theme file …/mytheme.yaml" for the `theme` key."""
    return re.search(rf"[`'\"]{key}[`'\"]|\b{key}:", message) is not None


def _bad_theme(key: str, shape: str) -> tuple[str, dict[str, Any]]:
    """GOOD_THEME with ``key`` set to a bad shape; returns (yaml, expected load)."""
    data = dict(GOOD_THEME)
    data[key] = BAD_SHAPES[shape]
    expected = {k: v for k, v in GOOD_THEME.items() if k != key}
    return yaml.safe_dump(data, sort_keys=False), expected


# ---------------------------------------------------------------------------
# 1. _load_builtin_theme — a non-mapping section is dropped with one warning
# ---------------------------------------------------------------------------


class TestLoaderDropsBadSection:
    @pytest.mark.parametrize("shape", ["list", "str", "int", "bool"])
    @pytest.mark.parametrize("key", SECTION_KEYS)
    def test_bad_section_dropped_once_with_warning(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        key: str,
        shape: str,
        warnings_log: pytest.LogCaptureFixture,
    ) -> None:
        text, expected = _bad_theme(key, shape)
        repo = _repo(tmp_path / "repo", None, text)
        monkeypatch.chdir(repo)
        first = config_mod._load_builtin_theme(THEME, repo)
        second = config_mod._load_builtin_theme(THEME, repo)  # cached lookup
        assert first == expected, first
        assert second == expected, second
        hits = [m for m in _warnings(warnings_log) if THEME_FILE in m and _names_key(m, key)]
        assert len(hits) == 1, _warnings(warnings_log)

    @pytest.mark.parametrize("key", SECTION_KEYS)
    def test_null_section_dropped(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        key: str,
        warnings_log: pytest.LogCaptureFixture,
    ) -> None:
        text, expected = _bad_theme(key, "null")
        repo = _repo(tmp_path / "repo", None, text)
        monkeypatch.chdir(repo)
        assert config_mod._load_builtin_theme(THEME, repo) == expected
        assert config_mod._load_builtin_theme(THEME, repo) == expected
        # a null may or may not warn (driver's call), but never more than once
        assert len([m for m in _warnings(warnings_log) if key in m]) <= 1

    def test_control_good_theme_loads_unchanged(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        warnings_log: pytest.LogCaptureFixture,
    ) -> None:
        repo = _repo(tmp_path / "repo", None, _theme_yaml(**GOOD_THEME))
        monkeypatch.chdir(repo)
        assert config_mod._load_builtin_theme(THEME, repo) == GOOD_THEME
        assert not _warnings(warnings_log), _warnings(warnings_log)

    def test_control_missing_sections_load_unchanged(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        warnings_log: pytest.LogCaptureFixture,
    ) -> None:
        repo = _repo(tmp_path / "repo", None, "name: Acme\n")
        monkeypatch.chdir(repo)
        assert config_mod._load_builtin_theme(THEME, repo) == {"name": "Acme"}
        assert not _warnings(warnings_log), _warnings(warnings_log)


# ---------------------------------------------------------------------------
# 2. Consumers — no AttributeError/TypeError reaches the CLI
# ---------------------------------------------------------------------------

DEFAULT_COLS = ("Backlog", "Ready", "In Progress", "Review", "Done")


class TestBoardColumns:
    @pytest.mark.parametrize("shape", list(BAD_SHAPES))
    def test_single_board_bad_columns_falls_back_to_defaults(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shape: str
    ) -> None:
        text, _ = _bad_theme("columns", shape)
        repo = _repo(tmp_path / "repo", SINGLE_CFG, text)
        result = _invoke(repo, monkeypatch, ["board"])
        _no_crash(result)
        assert result.exit_code == 0, result.output
        assert "Mytheme Board" in result.output, result.output
        for name in DEFAULT_COLS:
            assert name in result.output, result.output

    @pytest.mark.parametrize("shape", list(BAD_SHAPES))
    def test_multi_board_bad_columns_falls_back_to_defaults(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shape: str
    ) -> None:
        text, _ = _bad_theme("columns", shape)
        repo = _repo(tmp_path / "repo", MULTI_CFG, text)
        result = _invoke(repo, monkeypatch, ["board"])
        _no_crash(result)
        assert result.exit_code == 0, result.output
        assert "Devboard Board" in result.output, result.output
        for name in DEFAULT_COLS:
            assert name in result.output, result.output

    def test_control_board_theme_without_columns(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _repo(tmp_path / "repo", SINGLE_CFG, "name: Acme\n")
        result = _invoke(repo, monkeypatch, ["board"])
        _no_crash(result)
        assert result.exit_code == 0, result.output
        assert "Mytheme Board" in result.output
        for name in DEFAULT_COLS:
            assert name in result.output, result.output

    def test_control_board_good_theme_uses_its_columns(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _repo(tmp_path / "repo", SINGLE_CFG, _theme_yaml(**GOOD_THEME))
        result = _invoke(repo, monkeypatch, ["board"])
        _no_crash(result)
        assert result.exit_code == 0, result.output
        for name in ("Todo", "Doing", "Shipped"):
            assert name in result.output, result.output


class TestItemTypes:
    @pytest.mark.parametrize("shape", list(BAD_SHAPES))
    def test_list_with_bad_item_types(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shape: str
    ) -> None:
        text, _ = _bad_theme("item_types", shape)
        repo = _repo(tmp_path / "repo", SINGLE_CFG, text)
        # a non-enum type is what sends the scan into the theme's item_types
        _item(repo, "WID-001", "widget", "backlog")
        _item(repo, "TASK-001", "task", "backlog")
        result = _invoke(repo, monkeypatch, ["list"])
        _no_crash(result)
        assert result.exit_code == 0, result.output
        assert "TASK-001" in result.output, result.output

    @pytest.mark.parametrize("shape", list(BAD_SHAPES))
    def test_create_with_bad_item_types(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shape: str
    ) -> None:
        text, _ = _bad_theme("item_types", shape)
        repo = _repo(tmp_path / "repo", SINGLE_CFG, text)
        result = _invoke(repo, monkeypatch, ["create", "task", "Shape check"])
        _no_crash(result)
        assert result.exit_code == 0, result.output
        created = [p for p in (repo / "work").rglob("*.md")]
        assert created, result.output

    @pytest.mark.parametrize("shape", list(BAD_SHAPES))
    def test_init_with_bad_item_types(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shape: str
    ) -> None:
        text, _ = _bad_theme("item_types", shape)
        repo = tmp_path / "repo"
        themes = repo / ".kanban" / "themes"
        themes.mkdir(parents=True)
        (themes / THEME_FILE).write_text(text)
        _git(repo, "init", "-b", "main")
        result = _invoke(repo, monkeypatch, ["init", "--theme", THEME])
        _no_crash(result)
        assert result.exit_code == 0, result.output
        cfg = (repo / ".kanban" / "config.yaml").read_text()
        assert f"theme: {THEME}" in cfg and "root: work/" in cfg, cfg

    def test_control_init_theme_without_item_types(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = tmp_path / "repo"
        themes = repo / ".kanban" / "themes"
        themes.mkdir(parents=True)
        (themes / THEME_FILE).write_text("name: Acme\n")
        _git(repo, "init", "-b", "main")
        result = _invoke(repo, monkeypatch, ["init", "--theme", THEME])
        _no_crash(result)
        assert result.exit_code == 0, result.output
        cfg = (repo / ".kanban" / "config.yaml").read_text()
        assert f"theme: {THEME}" in cfg and "root: work/" in cfg, cfg

    def test_control_create_good_theme(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _repo(tmp_path / "repo", SINGLE_CFG, _theme_yaml(**GOOD_THEME))
        result = _invoke(repo, monkeypatch, ["create", "task", "Shape check"])
        _no_crash(result)
        assert result.exit_code == 0, result.output


class TestStatusMappings:
    """Theme status_mappings are read on multi-board boards (column map, reverse map)."""

    @pytest.mark.parametrize("shape", list(BAD_SHAPES))
    def test_board_with_bad_status_mappings(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shape: str
    ) -> None:
        text, without = _bad_theme("status_mappings", shape)
        repo = _repo(tmp_path / "repo", MULTI_CFG, text)
        _item(repo, "TASK-001", "task", "active")  # non-canonical status
        result = _invoke(repo, monkeypatch, ["board"])
        _no_crash(result)
        assert result.exit_code == 0, result.output
        # the same repo whose theme simply lacks `status_mappings`
        expected = self._board_without_status_mappings(tmp_path, monkeypatch, without)
        assert result.output == expected, (result.output, expected)

    @staticmethod
    def _board_without_status_mappings(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, theme: dict[str, Any]
    ) -> str:
        repo = _repo(tmp_path / "absent", MULTI_CFG, yaml.safe_dump(theme, sort_keys=False))
        _item(repo, "TASK-001", "task", "active")
        result = _invoke(repo, monkeypatch, ["board"])
        _no_crash(result)
        assert result.exit_code == 0, result.output
        return result.output

    def test_control_board_theme_without_status_mappings(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        theme = {k: v for k, v in GOOD_THEME.items() if k != "status_mappings"}
        out = self._board_without_status_mappings(tmp_path, monkeypatch, theme)
        assert "Devboard Board" in out, out

    @pytest.mark.parametrize("shape", list(BAD_SHAPES))
    def test_move_with_bad_status_mappings(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shape: str
    ) -> None:
        text, _ = _bad_theme("status_mappings", shape)
        repo = _repo(tmp_path / "repo", MULTI_CFG, _drop(text, "transitions"))
        item = _item(repo, "TASK-001", "task", "active")
        result = _invoke(repo, monkeypatch, ["move", "TASK-001", "review", "--no-commit"])
        _no_crash(result)
        assert result.exit_code == 0, result.output
        assert "status: review" in item.read_text()

    def test_control_move_theme_without_status_mappings(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _repo(tmp_path / "repo", MULTI_CFG, "name: Acme\n")
        item = _item(repo, "TASK-001", "task", "active")
        result = _invoke(repo, monkeypatch, ["move", "TASK-001", "review", "--no-commit"])
        _no_crash(result)
        assert result.exit_code == 0, result.output
        assert "status: review" in item.read_text()


class TestTransitions:
    @pytest.mark.parametrize("shape", list(BAD_SHAPES))
    def test_move_with_bad_transitions(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shape: str
    ) -> None:
        text, _ = _bad_theme("transitions", shape)
        repo = _repo(tmp_path / "repo", MULTI_CFG, _drop(text, "status_mappings"))
        item = _item(repo, "TASK-001", "task", "backlog")
        result = _invoke(repo, monkeypatch, ["move", "TASK-001", "ready", "--no-commit"])
        _no_crash(result)
        assert result.exit_code == 0, result.output
        assert "status: ready" in item.read_text()

    def test_control_move_theme_without_transitions(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _repo(tmp_path / "repo", MULTI_CFG, "name: Acme\n")
        item = _item(repo, "TASK-001", "task", "backlog")
        result = _invoke(repo, monkeypatch, ["move", "TASK-001", "ready", "--no-commit"])
        _no_crash(result)
        assert result.exit_code == 0, result.output
        assert "status: ready" in item.read_text()


class TestThemeSection:
    @pytest.mark.parametrize("shape", list(BAD_SHAPES))
    def test_epic_create_with_bad_theme_section(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shape: str
    ) -> None:
        text, _ = _bad_theme("theme", shape)
        repo = _repo(tmp_path / "repo", SINGLE_CFG, text)
        result = _invoke(repo, monkeypatch, ["epic", "create", "Big thing"])
        _no_crash(result)
        # as for a theme with no `theme:` name: the clean "does not support epics" refusal
        assert result.exit_code == 1, result.output
        assert "does not support epics" in " ".join(result.output.split()), result.output

    def test_control_epic_create_theme_without_theme_section(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _repo(tmp_path / "repo", SINGLE_CFG, "name: Acme\n")
        result = _invoke(repo, monkeypatch, ["epic", "create", "Big thing"])
        _no_crash(result)
        assert result.exit_code == 1, result.output
        assert "does not support epics" in " ".join(result.output.split()), result.output


def _drop(text: str, key: str) -> str:
    """The theme YAML without ``key`` (so a well-formed status_mappings can't mask
    the transitions path with its own reverse mapping)."""
    data = yaml.safe_load(text)
    data.pop(key, None)
    return yaml.safe_dump(data, sort_keys=False)
