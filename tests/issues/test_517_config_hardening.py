"""#517: an empty ``scan_paths`` entry is no path; only null ``kanban.paths`` is empty.

Follow-ups from the reviews of #503/#495. Decided ([steer] on #517):

1. An empty ``scan_paths`` element is dropped with one warning naming
   ``scan_paths`` and the empty entry. It never means the repo root
   (``Path('')`` is ``.``), so ``scan_paths: ['']`` scans what an absent
   ``scan_paths`` scans. Same for a multi-board board and the kanban-level
   fallback (#482).
2. A non-string element (a number, null, a mapping) is a ValueError naming
   ``scan_paths``.
3. Only null (or absent) ``kanban.paths`` means empty. ``0``, ``''``, ``[]`` and
   ``false`` are refused like any other non-mapping, naming ``kanban.paths`` and
   "mapping".

Controls: ordinary lists and fleet-shaped configs load unchanged, no warnings.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import pytest

from yurtle_kanban import config as config_mod
from yurtle_kanban.config import KanbanConfig, PathConfig
from yurtle_kanban.service import KanbanService

LOGGER = "yurtle-kanban"

# --- config builders ------------------------------------------------------------------


def _single(scan_line: str) -> str:
    """Single-board config whose ``paths.scan_paths`` is ``scan_line`` (raw YAML)."""
    return f'kanban:\n  theme: software\n  paths:\n    root: "work/"\n    scan_paths: {scan_line}\n'


def _kanban_level(scan_line: str) -> str:
    """Single-board config with ``scan_paths`` beside ``paths`` (#482 fallback)."""
    return f'kanban:\n  theme: software\n  paths:\n    root: "work/"\n  scan_paths: {scan_line}\n'


def _multi(scan_line: str) -> str:
    return (
        'version: "2.0"\nboards:\n  - name: dev\n    preset: software\n'
        f'    path: "work/"\n    scan_paths: {scan_line}\ndefault_board: dev\n'
    )


BUILDERS = {"single": _single, "kanban_level": _kanban_level, "multi": _multi}


def _scan_of(config: KanbanConfig, shape: str) -> list[str]:
    return config.boards[0].scan_paths if shape == "multi" else config.paths.scan_paths


# --- helpers --------------------------------------------------------------------------


def _load(tmp_path: Path, text: str) -> KanbanConfig:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(text)
    config_mod._theme_cache.clear()
    return KanbanConfig.load(cfg)


def _load_logged(
    tmp_path: Path, text: str, caplog: pytest.LogCaptureFixture
) -> tuple[KanbanConfig, list[str]]:
    """The loaded config and every package WARNING message the load emitted."""
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        config = _load(tmp_path, text)
    msgs = [
        r.getMessage()
        for r in caplog.records
        if r.name.startswith(LOGGER) and r.levelno >= logging.WARNING
    ]
    return config, msgs


def _load_error(tmp_path: Path, text: str) -> Exception:
    """The exception loading ``text`` raises, asserted to be a ValueError (a crash
    of another type is the bug, so it fails as an assertion, not an error)."""
    try:
        _load(tmp_path, text)
    except Exception as e:
        exc = e
    else:
        raise AssertionError(f"loaded without error:\n{text}")
    assert isinstance(exc, ValueError), repr(exc)
    return exc


def _assert_one_empty_entry_warning(msgs: list[str]) -> None:
    hits = [m for m in msgs if "scan_paths" in m]
    assert len(hits) == 1, msgs
    assert "''" in hits[0] or "empty" in hits[0].lower(), hits[0]


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)


def _item(path: Path, item_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'---\nid: {item_id}\ntitle: "t"\ntype: feature\nstatus: backlog\n'
        f"priority: medium\ncreated: 2026-09-26\n---\n\n# {item_id}: t\n"
    )


def _scanned_ids(tmp_path: Path, name: str, config_yaml: str) -> set[str]:
    """Ids ``scan()`` finds in a repo with one item at the repo root and one in work/."""
    repo = tmp_path / name
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@t.com")
    _git(repo, "config", "user.name", "T")
    (repo / ".kanban").mkdir()
    (repo / ".kanban" / "config.yaml").write_text(config_yaml)
    _item(repo / "FEAT-001-at-root.md", "FEAT-001")
    _item(repo / "work" / "FEAT-002-in-work.md", "FEAT-002")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    config_mod._theme_cache.clear()
    config = KanbanConfig.load(repo / ".kanban" / "config.yaml")
    return {i.id for i in KanbanService(config, repo).scan()}


# --- 1. an empty entry is dropped with one warning ------------------------------------


@pytest.mark.parametrize("shape", sorted(BUILDERS))
def test_empty_entry_dropped_with_warning(tmp_path, caplog, shape):
    config, msgs = _load_logged(tmp_path, BUILDERS[shape]('["", "a/"]'), caplog)
    assert _scan_of(config, shape) == ["a/"]
    _assert_one_empty_entry_warning(msgs)


@pytest.mark.parametrize("shape", sorted(BUILDERS))
def test_only_empty_entry_loads_empty(tmp_path, caplog, shape):
    config, msgs = _load_logged(tmp_path, BUILDERS[shape]('[""]'), caplog)
    assert _scan_of(config, shape) == []
    _assert_one_empty_entry_warning(msgs)


def test_multi_board_empty_entry_not_in_aggregate(tmp_path):
    config = _load(tmp_path, _multi('["", "a/"]'))
    assert config.paths.scan_paths == ["a/"]


@pytest.mark.parametrize("shape", ["single", "kanban_level"])
def test_empty_entry_never_scans_repo_root(tmp_path, shape):
    """``scan_paths: ['']`` scans what an absent ``scan_paths`` scans (``root``),
    never the repo root."""
    config_yaml = BUILDERS[shape]('[""]')
    got = _scanned_ids(tmp_path, "empty", config_yaml)
    baseline = _scanned_ids(
        tmp_path, "absent", 'kanban:\n  theme: software\n  paths:\n    root: "work/"\n'
    )
    assert baseline == {"FEAT-002"}  # sanity: the root item isn't otherwise scanned
    assert got == baseline, got


def test_empty_entry_not_in_work_paths(tmp_path):
    work = _load(tmp_path, _single('["", "a/"]')).get_work_paths()
    assert Path("") not in work and Path(".") not in work, work


# --- 2. a non-string entry is refused -------------------------------------------------

BAD_ELEMENTS = {"int": '["a/", 5]', "null": "[null]", "mapping": "[{x: 1}]"}


@pytest.mark.parametrize("shape", sorted(BUILDERS))
@pytest.mark.parametrize("case", sorted(BAD_ELEMENTS))
def test_non_string_entry_raises_value_error(tmp_path, shape, case):
    exc = _load_error(tmp_path, BUILDERS[shape](BAD_ELEMENTS[case]))
    assert "scan_paths" in str(exc), str(exc)


# --- 3. only null kanban.paths means empty --------------------------------------------


@pytest.mark.parametrize("value", ["0", '""', "[]", "false"])
def test_falsy_non_mapping_paths_raises_value_error(tmp_path, value):
    msg = str(_load_error(tmp_path, f"kanban:\n  theme: software\n  paths: {value}\n"))
    assert "kanban.paths" in msg and "mapping" in msg.lower(), msg


@pytest.mark.parametrize(
    "text",
    ["kanban:\n  theme: software\n  paths:\n", "kanban:\n  theme: software\n"],
    ids=["null", "absent"],
)
def test_control_null_or_absent_paths_loads_defaults(tmp_path, caplog, text):
    config, msgs = _load_logged(tmp_path, text, caplog)
    assert config.paths == PathConfig()
    assert msgs == [], msgs


# --- controls -------------------------------------------------------------------------


@pytest.mark.parametrize("shape", sorted(BUILDERS))
def test_control_list_unchanged_no_warning(tmp_path, caplog, shape):
    config, msgs = _load_logged(tmp_path, BUILDERS[shape]('["a/", "b/"]'), caplog)
    assert _scan_of(config, shape) == ["a/", "b/"]
    assert msgs == [], msgs


def test_control_string_and_null_scan_paths_unchanged(tmp_path, caplog):
    config, msgs = _load_logged(tmp_path, _single('"a/"'), caplog)
    assert config.paths.scan_paths == ["a/"]
    config, more = _load_logged(tmp_path, _single(""), caplog)
    assert config.paths.scan_paths == []
    assert msgs == more == [], msgs + more


# Shapes copied from the fleet (noesis-ship, carclaw .kanban/config.yaml, 2026-09-26)
NOESIS_SHAPE = """\
kanban:
  theme: nautical

  paths:
    root: kanban-work/
    scan_paths:
    - "kanban-work/expeditions/"
    - "kanban-work/tasks/"
    - "kanban-work/bugs/"

  ignore:
    - "**/archive/**"
    - "**/templates/**"
    - "**/_TEMPLATE*"
"""

CARCLAW_SHAPE = """\
kanban:
  theme: software

  paths:
    root: kanban-work

  scan_paths:
    - kanban-work/features
    - kanban-work/tasks
    - kanban-work/bugs

  ignore:
    - "**/archive/**"
    - "**/templates/**"
"""


def test_control_fleet_noesis_shape(tmp_path, caplog):
    config, msgs = _load_logged(tmp_path, NOESIS_SHAPE, caplog)
    assert config.paths.root == "kanban-work/"
    assert config.paths.scan_paths == [
        "kanban-work/expeditions/",
        "kanban-work/tasks/",
        "kanban-work/bugs/",
    ]
    assert config.paths.ignore == ["**/archive/**", "**/templates/**", "**/_TEMPLATE*"]
    assert msgs == [], msgs


def test_control_fleet_carclaw_shape(tmp_path, caplog):
    config, msgs = _load_logged(tmp_path, CARCLAW_SHAPE, caplog)
    assert config.paths.root == "kanban-work"
    assert config.paths.scan_paths == [
        "kanban-work/features",
        "kanban-work/tasks",
        "kanban-work/bugs",
    ]
    assert config.paths.ignore == ["**/archive/**", "**/templates/**"]
    assert msgs == [], msgs
