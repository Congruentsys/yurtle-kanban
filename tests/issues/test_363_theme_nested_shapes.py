"""Issue #363 — nested non-mapping theme entries, and a non-str ``theme.name``.

Follow-up from the review of PR #361 (#351), whose load-time guard
(``config._drop_bad_sections``) only checks the top-level sections. One level
down still crashes:

- ``columns: {todo: 5}`` → ``board`` → ``AttributeError: 'int' object has no
  attribute 'get'`` (``_get_columns_from_theme`` / ``_get_columns_from_preset``);
- ``item_types: {task: 5}`` → ``create task hi`` → ``AttributeError``
  (``_get_type_prefix``), ``list`` → ``TypeError`` (``"path" in type_def``),
  ``init --theme`` → ``type_def.get("path")``;
- ``theme: {name: [a]}`` → ``epic create`` → ``TypeError: unhashable type: 'list'``.

Decided behaviour (extends #351 one level down):

1. At load, a ``columns.<id>`` / ``item_types.<id>`` entry whose value is not a
   mapping (list, str, int, bool, null) is dropped with ONE warning naming the file
   and the entry (e.g. ``columns.todo``); well-formed siblings survive.
2. A ``theme.name`` that is not a str (list, int) is dropped with a warning; then
   ``epic create`` gives the clean "does not support epics" refusal, as for a theme
   without a name (observed today: exit 1, that message).
3. Public paths don't raise: ``board`` shows the remaining columns; ``create task``
   falls back as for a theme without a ``task`` type (observed today: TASK-001 under
   ``work/tasks/``); ``init --theme``; ``list``.
4. Controls: ``status_mappings: {todo: 5}`` and ``transitions: {todo: 5}`` don't
   crash today and are NOT dropped; a well-formed theme loads unchanged, no warnings.
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

BAD_SHAPES: dict[str, Any] = {
    "list": ["a", "b"],
    "str": "x",
    "int": 5,
    "bool": True,
    "null": None,
}

# The bad entry goes under `<section>.<entry>`; the other entry is well-formed.
NESTED: dict[str, tuple[str, str, dict[str, Any]]] = {
    "columns": ("todo", "doing", {"name": "Doing", "order": 2}),
    "item_types": ("task", "bug", {"id_prefix": "BUG", "path": "work/bugs/"}),
}

GOOD_THEME: dict[str, Any] = {
    "theme": {"name": "acme", "description": "custom"},
    "item_types": {
        "task": {"id_prefix": "TSK", "path": "work/tasks/"},
        "bug": {"id_prefix": "BUG", "path": "work/bugs/"},
    },
    "columns": {
        "todo": {"name": "Todo", "order": 1},
        "doing": {"name": "Doing", "order": 2, "wip_limit": 2},
    },
    "status_mappings": {"todo": "backlog", "doing": "in_progress"},
    "transitions": {"todo": ["doing"], "doing": ["todo"]},
}

SINGLE_CFG = f"kanban:\n  theme: {THEME}\n  paths:\n    root: work/\n"
MULTI_CFG = (
    'version: "2.0"\nboards:\n  - name: devboard\n'
    f"    preset: {THEME}\n    path: work/\n"
)


# --- fixtures / helpers (as in test_351) ------------------------------------------------


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


def _dump(data: dict[str, Any]) -> str:
    return yaml.safe_dump(data, sort_keys=False)


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


def _names_entry(message: str, dotted: str) -> bool:
    """The warning names ``section.entry`` (quoted or bare, as a whole token)."""
    return re.search(rf"(?<![\w.]){re.escape(dotted)}(?![\w.])", message) is not None


def _nested_theme(section: str, shape: str) -> tuple[str, dict[str, Any]]:
    """A theme whose ``section`` holds one bad entry and one good sibling; returns
    (yaml, the load expected once the bad entry is dropped)."""
    bad, good, good_def = NESTED[section]
    data = {
        "theme": {"name": "acme"},
        section: {bad: BAD_SHAPES[shape], good: good_def},
    }
    expected = {"theme": {"name": "acme"}, section: {good: good_def}}
    return _dump(data), expected


def _load(repo: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any] | None:
    monkeypatch.chdir(repo)
    return config_mod._load_builtin_theme(THEME, repo)


# ---------------------------------------------------------------------------
# 1. Loader — a non-mapping columns.<id> / item_types.<id> is dropped, one warning
# ---------------------------------------------------------------------------


class TestLoaderDropsBadEntry:
    @pytest.mark.parametrize("shape", list(BAD_SHAPES))
    @pytest.mark.parametrize("section", list(NESTED))
    def test_bad_entry_dropped_once_with_warning(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        section: str,
        shape: str,
        warnings_log: pytest.LogCaptureFixture,
    ) -> None:
        text, expected = _nested_theme(section, shape)
        repo = _repo(tmp_path / "repo", None, text)
        first = _load(repo, monkeypatch)
        second = config_mod._load_builtin_theme(THEME, repo)  # cached lookup
        assert first == expected, first
        assert second == expected, second
        dotted = f"{section}.{NESTED[section][0]}"
        hits = [m for m in _warnings(warnings_log) if THEME_FILE in m and _names_entry(m, dotted)]
        assert len(hits) == 1, _warnings(warnings_log)

    @pytest.mark.parametrize("shape", ["list", "int"])
    def test_non_str_theme_name_dropped_with_warning(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        shape: str,
        warnings_log: pytest.LogCaptureFixture,
    ) -> None:
        text = _dump({"theme": {"name": BAD_SHAPES[shape], "description": "d"}})
        repo = _repo(tmp_path / "repo", None, text)
        assert _load(repo, monkeypatch) == {"theme": {"description": "d"}}
        assert config_mod._load_builtin_theme(THEME, repo) == {"theme": {"description": "d"}}
        hits = [
            m for m in _warnings(warnings_log) if THEME_FILE in m and _names_entry(m, "theme.name")
        ]
        assert len(hits) == 1, _warnings(warnings_log)

    @pytest.mark.parametrize("section", ["status_mappings", "transitions"])
    def test_control_status_mappings_and_transitions_entries_kept(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        section: str,
        warnings_log: pytest.LogCaptureFixture,
    ) -> None:
        data = {"theme": {"name": "acme"}, section: {"todo": 5}}
        repo = _repo(tmp_path / "repo", None, _dump(data))
        assert _load(repo, monkeypatch) == data
        assert not _warnings(warnings_log), _warnings(warnings_log)

    def test_control_good_theme_loads_unchanged(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        warnings_log: pytest.LogCaptureFixture,
    ) -> None:
        repo = _repo(tmp_path / "repo", None, _dump(GOOD_THEME))
        assert _load(repo, monkeypatch) == GOOD_THEME
        assert not _warnings(warnings_log), _warnings(warnings_log)


# ---------------------------------------------------------------------------
# 2. Public paths — no AttributeError/TypeError reaches the CLI
# ---------------------------------------------------------------------------


class TestBoard:
    @pytest.mark.parametrize("cfg", [SINGLE_CFG, MULTI_CFG], ids=["single", "multi"])
    @pytest.mark.parametrize("shape", list(BAD_SHAPES))
    def test_board_bad_column_entry_shows_remaining_columns(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shape: str, cfg: str
    ) -> None:
        text, _ = _nested_theme("columns", shape)
        repo = _repo(tmp_path / "repo", cfg, text)
        result = _invoke(repo, monkeypatch, ["board"])
        _no_crash(result)
        assert result.exit_code == 0, result.output
        assert "Doing" in result.output, result.output

    @pytest.mark.parametrize("section", ["status_mappings", "transitions"])
    def test_control_board_nested_bad_entry_no_crash_today(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, section: str
    ) -> None:
        data = {
            "columns": {"todo": {"name": "Todo", "order": 1}},
            section: {"todo": 5},
        }
        repo = _repo(tmp_path / "repo", SINGLE_CFG, _dump(data))
        result = _invoke(repo, monkeypatch, ["board"])
        _no_crash(result)
        assert result.exit_code == 0, result.output

    def test_control_board_good_theme(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _repo(tmp_path / "repo", SINGLE_CFG, _dump(GOOD_THEME))
        result = _invoke(repo, monkeypatch, ["board"])
        _no_crash(result)
        assert result.exit_code == 0, result.output
        for name in ("Todo", "Doing"):
            assert name in result.output, result.output


class TestItemTypes:
    @pytest.mark.parametrize("shape", list(BAD_SHAPES))
    def test_create_task_bad_entry_falls_back_like_missing_type(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shape: str
    ) -> None:
        text, _ = _nested_theme("item_types", shape)
        repo = _repo(tmp_path / "repo", SINGLE_CFG, text)
        result = _invoke(repo, monkeypatch, ["create", "task", "hi"])
        _no_crash(result)
        assert result.exit_code == 0, result.output
        # as for a theme whose item_types lacks `task`
        assert (repo / "work" / "tasks" / "TASK-001-hi.md").exists(), result.output

    def test_control_create_task_theme_without_task_type(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        text = _dump({"item_types": {"bug": {"id_prefix": "BUG", "path": "work/bugs/"}}})
        repo = _repo(tmp_path / "repo", SINGLE_CFG, text)
        result = _invoke(repo, monkeypatch, ["create", "task", "hi"])
        _no_crash(result)
        assert result.exit_code == 0, result.output
        assert (repo / "work" / "tasks" / "TASK-001-hi.md").exists(), result.output

    def test_control_create_task_good_theme(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _repo(tmp_path / "repo", SINGLE_CFG, _dump(GOOD_THEME))
        result = _invoke(repo, monkeypatch, ["create", "task", "hi"])
        _no_crash(result)
        assert result.exit_code == 0, result.output
        assert (repo / "work" / "tasks" / "TSK-001-hi.md").exists(), result.output

    @pytest.mark.parametrize("shape", list(BAD_SHAPES))
    def test_list_bad_item_type_entry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shape: str
    ) -> None:
        text, _ = _nested_theme("item_types", shape)
        repo = _repo(tmp_path / "repo", SINGLE_CFG, text)
        _item(repo, "TASK-001", "task", "backlog")
        _item(repo, "WID-001", "widget", "backlog")  # non-enum type → theme lookup
        result = _invoke(repo, monkeypatch, ["list"])
        _no_crash(result)
        assert result.exit_code == 0, result.output
        assert "TASK-001" in result.output, result.output

    @pytest.mark.parametrize("shape", list(BAD_SHAPES))
    def test_init_bad_item_type_entry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shape: str
    ) -> None:
        text, _ = _nested_theme("item_types", shape)
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


class TestThemeName:
    @pytest.mark.parametrize("shape", ["list", "int"])
    def test_epic_create_non_str_name_refused_cleanly(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shape: str
    ) -> None:
        text = _dump({"theme": {"name": BAD_SHAPES[shape]}})
        repo = _repo(tmp_path / "repo", SINGLE_CFG, text)
        result = _invoke(repo, monkeypatch, ["epic", "create", "E"])
        _no_crash(result)
        assert result.exit_code == 1, result.output
        assert "does not support epics" in " ".join(result.output.split()), result.output

    def test_control_epic_create_theme_without_name(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _repo(tmp_path / "repo", SINGLE_CFG, _dump({"theme": {"description": "d"}}))
        result = _invoke(repo, monkeypatch, ["epic", "create", "E"])
        _no_crash(result)
        assert result.exit_code == 1, result.output
        assert "does not support epics" in " ".join(result.output.split()), result.output
