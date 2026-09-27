"""Issue #613 — a custom theme whose `status_mappings` has a non-string entry crashes
`create` / `move` (`TypeError: unhashable type: 'list'` in
`_get_reverse_status_mapping`, which builds the canonical -> native map).

Decided ([steer] on #613): the loader drops every non-string `status_mappings`
entry with one warning, as `transitions` does (#457/#461). For a value that is a
list, a mapping, an int or null, and for a key that is an int, on a single board
and on a multi-board repo:

1. `create` and `move` succeed: no traceback, exit 0.
2. Each command (a fresh process, so a fresh theme cache) logs exactly one warning
   that names the theme file and the bad entry.
3. The good entries next to the bad one still work: `create` writes the native
   initial status `todo`, `move X doing` and `move X in_progress` both write
   `doing`, and `show --json` stays canonical.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import CliRunner, Result

from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import main

LOGGER = "yurtle-kanban"
THEME = "mytheme"
THEME_FILE = f"{THEME}.yaml"
MOVE = ["--force", "--skip-gates", "--no-commit"]

GOOD_MAPPINGS: dict[Any, Any] = {"todo": "backlog", "doing": "in_progress", "shipped": "done"}

# (bad key, bad value, how the warning names the entry)
BAD_ENTRIES = {
    "value-list": ("ready", [1], "ready"),
    "value-mapping": ("ready", {"a": "b"}, "ready"),
    "value-int": ("ready", 5, "ready"),
    "value-null": ("ready", None, "ready"),
    "key-int": (7, "review", "7"),
}

SINGLE_CFG = f"kanban:\n  theme: {THEME}\n  paths:\n    root: work/\n"
MULTI_CFG = f'version: "2.0"\nboards:\n  - name: devboard\n    preset: {THEME}\n    path: work/\n'
LAYOUTS = {"single": SINGLE_CFG, "multi": MULTI_CFG}


def _theme(bad_key: Any, bad_value: Any) -> dict[Any, Any]:
    return {
        "theme": {"name": "acme"},
        "item_types": {"task": {"id_prefix": "TASK", "path": "work/tasks/"}},
        "columns": {
            "todo": {"name": "Todo", "order": 1},
            "doing": {"name": "Doing", "order": 2},
            "review": {"name": "Review", "order": 3},
            "shipped": {"name": "Shipped", "order": 4},
        },
        "status_mappings": {**GOOD_MAPPINGS, bad_key: bad_value},
    }


# --- fixtures / helpers --------------------------------------------------------------


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


def _repo(root: Path, config: str, theme: dict[Any, Any]) -> Path:
    themes = root / ".kanban" / "themes"
    themes.mkdir(parents=True)
    (root / ".kanban" / "config.yaml").write_text(config)
    (themes / THEME_FILE).write_text(yaml.safe_dump(theme, sort_keys=False))
    (root / "work").mkdir()
    (root / "work" / ".keep").write_text("")
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "t@t.com")
    _git(root, "config", "user.name", "T")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "init")
    return root


def _run(
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    args: list[str],
) -> tuple[Result, list[str]]:
    """Run one command as a fresh process would (fresh theme cache); return its
    result and the warnings it logged. A crash is an assertion failure."""
    monkeypatch.chdir(repo)
    config_mod._theme_cache.clear()
    caplog.clear()
    result = CliRunner().invoke(main, args)
    out = result.output or ""
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        raise AssertionError(f"`{' '.join(args)}` crashed: {result.exception!r}")
    assert "Traceback" not in out, out
    assert result.exit_code == 0, out
    return result, _warnings(caplog)


def _about_entry(warnings: list[str], entry: str) -> list[str]:
    """Warnings naming the theme file and `status_mappings.<entry>`."""
    pattern = re.compile(rf"status_mappings\.{re.escape(entry)}\b")
    return [w for w in warnings if THEME_FILE in w and pattern.search(w)]


def _created_id(result: Result) -> str:
    match = re.search(r"Created (\S+):", result.output)
    assert match, result.output
    return match.group(1)


def _file_status(repo: Path, item_id: str) -> str:
    files = [p for p in (repo / "work").rglob(f"{item_id}-*.md")]
    assert len(files) == 1, files
    front = files[0].read_text().split("---", 2)[1]
    return str(yaml.safe_load(front)["status"])


CASES = [
    pytest.param(layout, case, id=f"{layout}-{case}") for layout in LAYOUTS for case in BAD_ENTRIES
]


# ---------------------------------------------------------------------------
# 0. the fixture theme really carries the bad entry (non-vacuity)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", list(BAD_ENTRIES))
def test_fixture_theme_round_trips_bad_entry(case: str) -> None:
    key, value, _ = BAD_ENTRIES[case]
    dumped = yaml.safe_load(yaml.safe_dump(_theme(key, value), sort_keys=False))
    assert dumped["status_mappings"][key] == value


# ---------------------------------------------------------------------------
# 1-3. create / move: no crash, one warning each, good entries still work
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("layout", "case"), CASES)
def test_create_no_crash_one_warning_native_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    warnings_log: pytest.LogCaptureFixture,
    layout: str,
    case: str,
) -> None:
    key, value, entry = BAD_ENTRIES[case]
    repo = _repo(tmp_path / "repo", LAYOUTS[layout], _theme(key, value))
    result, warned = _run(repo, monkeypatch, warnings_log, ["create", "task", "hi"])
    assert len(_about_entry(warned, entry)) == 1, warned
    assert _file_status(repo, _created_id(result)) == "todo"


@pytest.mark.parametrize("target", ["doing", "in_progress"])
@pytest.mark.parametrize(("layout", "case"), CASES)
def test_move_no_crash_one_warning_native_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    warnings_log: pytest.LogCaptureFixture,
    layout: str,
    case: str,
    target: str,
) -> None:
    key, value, entry = BAD_ENTRIES[case]
    repo = _repo(tmp_path / "repo", LAYOUTS[layout], _theme(key, value))
    result, _ = _run(repo, monkeypatch, warnings_log, ["create", "task", "hi"])
    item_id = _created_id(result)
    _, warned = _run(repo, monkeypatch, warnings_log, ["move", item_id, target, *MOVE])
    assert len(_about_entry(warned, entry)) == 1, warned
    assert _file_status(repo, item_id) == "doing"
    result, _ = _run(repo, monkeypatch, warnings_log, ["show", item_id, "--json"])
    assert json.loads(result.output)["status"] == "in_progress"


# ---------------------------------------------------------------------------
# controls: a clean theme warns nothing and behaves the same
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("layout", list(LAYOUTS))
def test_control_clean_theme_no_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    warnings_log: pytest.LogCaptureFixture,
    layout: str,
) -> None:
    theme = _theme("todo", "backlog")  # the "bad" entry is a good one
    repo = _repo(tmp_path / "repo", LAYOUTS[layout], theme)
    result, warned = _run(repo, monkeypatch, warnings_log, ["create", "task", "hi"])
    assert not warned, warned
    item_id = _created_id(result)
    assert _file_status(repo, item_id) == "todo"
    _, warned = _run(repo, monkeypatch, warnings_log, ["move", item_id, "in_progress", *MOVE])
    assert not warned, warned
    assert _file_status(repo, item_id) == "doing"
