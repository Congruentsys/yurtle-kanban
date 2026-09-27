"""Issue #615 — theme `status_mappings` keys that fold to the same status name.

`move` folds a status name (lower-case, `-` and spaces -> `_`, #587), so `on-hold`,
`on hold` and `on_hold` are one name, as are `doing` and `Doing`. Today:
- the first key wins when names are read, and the others are dropped silently;
- the canonical -> native map (what `move` writes) takes the LAST key, so reads and
  writes disagree: with `doing: in_progress` then `Doing: backlog`,
  `move X backlog` writes `Doing`, which reads back as in_progress.

Decided ([steer] on #615, bucket 2): keys that fold together are ONE name. The
theme's first key wins; each later one is dropped with a single load-time warning
that names the dropped key and the kept one (as #613: warn and drop, never crash).

Covered, on a repo-local custom theme (helpers after test_613):
(a) `on-hold`, `on hold`, `on_hold` -> blocked: one warning per dropped key (per
    command, i.e. per fresh theme load); `move X "on hold"` still works (it folds)
    and writes `on-hold`, the kept key.
(b) `doing: in_progress`, `Doing: backlog`: `Doing` dropped with one warning;
    `move X backlog` reads back as backlog (never in_progress); `move X doing`
    writes `doing` and reads back as in_progress.
(c) controls: a theme without colliding keys warns nothing; the shipped themes
    load with no fold warning.
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
SHIPPED_THEMES = ("nautical", "hdd", "spec", "software")

SINGLE_CFG = f"kanban:\n  theme: {THEME}\n  paths:\n    root: work/\n"
MULTI_CFG = f'version: "2.0"\nboards:\n  - name: devboard\n    preset: {THEME}\n    path: work/\n'
LAYOUTS = {"single": SINGLE_CFG, "multi": MULTI_CFG}

BASE_MAPPINGS: dict[str, str] = {"todo": "backlog", "shipped": "done"}

# (a) three spellings of one name -> blocked; the first is kept
HOLD_MAPPINGS: dict[str, str] = {
    **BASE_MAPPINGS,
    "on-hold": "blocked",
    "on hold": "blocked",
    "on_hold": "blocked",
}
HOLD_KEPT = "on-hold"
HOLD_DROPPED = ("on hold", "on_hold")

# (b) case-colliding keys to DIFFERENT statuses; `doing` is kept
CASE_MAPPINGS: dict[str, str] = {**BASE_MAPPINGS, "doing": "in_progress", "Doing": "backlog"}

# (c) no two keys fold together
CLEAN_MAPPINGS: dict[str, str] = {
    **BASE_MAPPINGS,
    "doing": "in_progress",
    "on-hold": "blocked",
}


def _theme(mappings: dict[str, str]) -> dict[str, Any]:
    return {
        "theme": {"name": "acme"},
        "item_types": {"task": {"id_prefix": "TASK", "path": "work/tasks/"}},
        "columns": {
            "todo": {"name": "Todo", "order": 1},
            "doing": {"name": "Doing", "order": 2},
            "held": {"name": "On hold", "order": 3},
            "shipped": {"name": "Shipped", "order": 4},
        },
        "status_mappings": dict(mappings),
    }


# --- fixtures / helpers (after test_613) ----------------------------------------------


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


def _repo(root: Path, config: str, theme: dict[str, Any]) -> Path:
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


def _names(text: str, key: str) -> bool:
    """`text` names `key` as a whole name (case-sensitive: `doing` is not `Doing`)."""
    return re.search(rf"(?<![\w-]){re.escape(key)}(?![\w-])", text) is not None


def _about_drop(warnings: list[str], dropped: str) -> list[str]:
    """Warnings naming the theme file and the dropped `status_mappings` key."""
    return [w for w in warnings if THEME_FILE in w and _names(w, dropped)]


def _fold_warnings(warnings: list[str], keys: list[str]) -> list[str]:
    """Warnings about the theme's `status_mappings` that name any of `keys`."""
    return [w for w in warnings if "status_mappings" in w or any(_names(w, k) for k in keys)]


def _created_id(result: Result) -> str:
    match = re.search(r"Created (\S+):", result.output)
    assert match, result.output
    return match.group(1)


def _file_status(repo: Path, item_id: str) -> str:
    files = list((repo / "work").rglob(f"{item_id}-*.md"))
    assert len(files) == 1, files
    front = files[0].read_text().split("---", 2)[1]
    return str(yaml.safe_load(front)["status"])


def _show_status(
    repo: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, item_id: str
) -> str:
    result, _ = _run(repo, monkeypatch, caplog, ["show", item_id, "--json"])
    return str(json.loads(result.output)["status"])


# ---------------------------------------------------------------------------
# 0. the fixture themes really carry the colliding keys, in order (non-vacuity)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mappings", [HOLD_MAPPINGS, CASE_MAPPINGS], ids=["hold", "case"])
def test_fixture_theme_keeps_colliding_keys_in_order(mappings: dict[str, str]) -> None:
    dumped = yaml.safe_load(yaml.safe_dump(_theme(mappings), sort_keys=False))
    assert list(dumped["status_mappings"].items()) == list(mappings.items())


# ---------------------------------------------------------------------------
# (a) on-hold / on hold / on_hold: one warning per dropped key; first key kept
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("layout", list(LAYOUTS))
def test_hold_one_warning_per_dropped_key_naming_kept(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    warnings_log: pytest.LogCaptureFixture,
    layout: str,
) -> None:
    repo = _repo(tmp_path / "repo", LAYOUTS[layout], _theme(HOLD_MAPPINGS))
    _, warned = _run(repo, monkeypatch, warnings_log, ["create", "task", "hi"])
    for dropped in HOLD_DROPPED:
        about = _about_drop(warned, dropped)
        assert len(about) == 1, (dropped, warned)
        assert _names(about[0], HOLD_KEPT), about
    # the kept key is never itself reported as dropped
    assert not [w for w in warned if _names(w, HOLD_KEPT) and not any(
        _names(w, d) for d in HOLD_DROPPED
    )], warned


@pytest.mark.parametrize("target", ["on hold", "on_hold", "On-Hold", "blocked"])
@pytest.mark.parametrize("layout", list(LAYOUTS))
def test_hold_move_folds_and_writes_kept_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    warnings_log: pytest.LogCaptureFixture,
    layout: str,
    target: str,
) -> None:
    repo = _repo(tmp_path / "repo", LAYOUTS[layout], _theme(HOLD_MAPPINGS))
    result, _ = _run(repo, monkeypatch, warnings_log, ["create", "task", "hi"])
    item_id = _created_id(result)
    _, warned = _run(repo, monkeypatch, warnings_log, ["move", item_id, target, *MOVE])
    assert _file_status(repo, item_id) == HOLD_KEPT
    for dropped in HOLD_DROPPED:
        assert len(_about_drop(warned, dropped)) == 1, (dropped, warned)
    assert _show_status(repo, monkeypatch, warnings_log, item_id) == "blocked"


# ---------------------------------------------------------------------------
# (b) doing: in_progress, Doing: backlog — `Doing` dropped; reads and writes agree
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("layout", list(LAYOUTS))
def test_case_collision_one_warning_naming_both(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    warnings_log: pytest.LogCaptureFixture,
    layout: str,
) -> None:
    repo = _repo(tmp_path / "repo", LAYOUTS[layout], _theme(CASE_MAPPINGS))
    _, warned = _run(repo, monkeypatch, warnings_log, ["create", "task", "hi"])
    about = _about_drop(warned, "Doing")
    assert len(about) == 1, warned
    assert _names(about[0], "doing"), about


@pytest.mark.parametrize("layout", list(LAYOUTS))
def test_case_collision_move_backlog_reads_back_backlog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    warnings_log: pytest.LogCaptureFixture,
    layout: str,
) -> None:
    repo = _repo(tmp_path / "repo", LAYOUTS[layout], _theme(CASE_MAPPINGS))
    result, _ = _run(repo, monkeypatch, warnings_log, ["create", "task", "hi"])
    item_id = _created_id(result)
    # leave backlog first, so moving back to it is a real write
    _run(repo, monkeypatch, warnings_log, ["move", item_id, "done", *MOVE])
    _, warned = _run(repo, monkeypatch, warnings_log, ["move", item_id, "backlog", *MOVE])
    written = _file_status(repo, item_id)
    assert written.lower().replace("-", "_").replace(" ", "_") != "doing", written
    assert _show_status(repo, monkeypatch, warnings_log, item_id) == "backlog"
    assert len(_about_drop(warned, "Doing")) == 1, warned


@pytest.mark.parametrize("target", ["doing", "Doing", "in_progress"])
@pytest.mark.parametrize("layout", list(LAYOUTS))
def test_case_collision_move_doing_writes_doing_in_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    warnings_log: pytest.LogCaptureFixture,
    layout: str,
    target: str,
) -> None:
    repo = _repo(tmp_path / "repo", LAYOUTS[layout], _theme(CASE_MAPPINGS))
    result, _ = _run(repo, monkeypatch, warnings_log, ["create", "task", "hi"])
    item_id = _created_id(result)
    _run(repo, monkeypatch, warnings_log, ["move", item_id, target, *MOVE])
    assert _file_status(repo, item_id) == "doing"
    assert _show_status(repo, monkeypatch, warnings_log, item_id) == "in_progress"


# ---------------------------------------------------------------------------
# (c) controls: no colliding keys, no warning; shipped themes load clean
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("layout", list(LAYOUTS))
def test_control_no_collision_no_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    warnings_log: pytest.LogCaptureFixture,
    layout: str,
) -> None:
    repo = _repo(tmp_path / "repo", LAYOUTS[layout], _theme(CLEAN_MAPPINGS))
    result, warned = _run(repo, monkeypatch, warnings_log, ["create", "task", "hi"])
    assert not warned, warned
    item_id = _created_id(result)
    _, warned = _run(repo, monkeypatch, warnings_log, ["move", item_id, "on hold", *MOVE])
    assert not warned, warned
    assert _file_status(repo, item_id) == "on-hold"
    _, warned = _run(repo, monkeypatch, warnings_log, ["move", item_id, "in_progress", *MOVE])
    assert not warned, warned
    assert _file_status(repo, item_id) == "doing"


@pytest.mark.parametrize("name", SHIPPED_THEMES)
def test_control_shipped_theme_loads_without_fold_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    warnings_log: pytest.LogCaptureFixture,
    name: str,
) -> None:
    monkeypatch.chdir(tmp_path)  # no repo-local override in the way
    warnings_log.clear()
    theme = config_mod._load_builtin_theme(name, repo_root=tmp_path)
    assert theme is not None, name
    keys = [str(k) for k in (theme.get("status_mappings") or {})]
    assert not _fold_warnings(_warnings(warnings_log), keys), _warnings(warnings_log)
