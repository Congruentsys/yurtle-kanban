"""#482: a single-board config's kanban-level ``ignore`` / ``scan_paths`` are honoured.

Fleet configs (noesis-ship, carclaw, noesis-ships-comm) put ``ignore:`` and
``scan_paths:`` beside ``paths:`` rather than under it — the shape the README
documented. They were silently dropped. Decided ([steer] on #482):

1. A single-board config reads ``kanban.ignore`` / ``kanban.scan_paths`` as a
   fallback when ``kanban.paths`` doesn't set them.
2. When both are set, ``paths.*`` wins and ONE warning names the ignored
   kanban-level copy.
3. A bare ``kanban.ignore:`` (YAML null) means "no patterns", as a bare
   ``paths.ignore:`` does (#194).
4. Controls: ``paths.ignore`` alone is unchanged; no ignore anywhere gives the
   defaults; multi-board configs are unaffected; ``save()`` of a loaded
   kanban-level config reloads with the same effective ignore / scan_paths.
5. The fleet's real config shapes load with the effective values they meant.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import pytest

from yurtle_kanban import config as config_mod
from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.service import KanbanService

LOGGER = "yurtle-kanban"
DEFAULTS = ["**/archive/**", "**/templates/**"]


@pytest.fixture(autouse=True)
def _clear_theme_cache():
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)


def _item(path: Path, item_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nid: {item_id}\ntitle: \"t\"\ntype: feature\nstatus: backlog\n"
        f"priority: medium\ncreated: 2026-09-26\n---\n\n# {item_id}: t\n"
    )


def _repo(tmp_path: Path, config_yaml: str, items: dict[str, str]) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@t.com")
    _git(repo, "config", "user.name", "T")
    (repo / ".kanban").mkdir()
    (repo / ".kanban" / "config.yaml").write_text(config_yaml)
    for rel, item_id in items.items():
        _item(repo / rel, item_id)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    return repo


def _load(tmp_path: Path, text: str) -> KanbanConfig:
    cfg = tmp_path / "cfg" / "config.yaml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(text)
    config_mod._theme_cache.clear()
    return KanbanConfig.load(cfg)


def _scan_ids(repo: Path) -> set[str]:
    config_mod._theme_cache.clear()
    config = KanbanConfig.load(repo / ".kanban" / "config.yaml")
    return {i.id for i in KanbanService(config, repo).scan()}


def _warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]


# --- 1. kanban-level keys are the fallback ------------------------------------------

KANBAN_IGNORE_ROOT_DOT = (
    "kanban:\n"
    "  theme: software\n"
    "  paths:\n"
    '    root: "."\n'
    "  ignore:\n"
    '    - "ideas/*"\n'
)

KANBAN_SCAN_PATHS = (
    "kanban:\n"
    "  theme: software\n"
    "  paths:\n"
    '    root: "work/"\n'
    "  scan_paths:\n"
    '    - "elsewhere/"\n'
)


def test_kanban_level_ignore_loads(tmp_path):
    assert _load(tmp_path, KANBAN_IGNORE_ROOT_DOT).paths.ignore == ["ideas/*"]


def test_kanban_level_ignore_excludes_item_from_scan(tmp_path):
    repo = _repo(tmp_path, KANBAN_IGNORE_ROOT_DOT, {
        "ideas/FEAT-002-idea.md": "FEAT-002",
        "work/FEAT-001-live.md": "FEAT-001",
    })
    assert _scan_ids(repo) == {"FEAT-001"}


def test_kanban_level_scan_paths_loads(tmp_path):
    assert _load(tmp_path, KANBAN_SCAN_PATHS).paths.scan_paths == ["elsewhere/"]


def test_kanban_level_scan_paths_reach_item(tmp_path):
    repo = _repo(tmp_path, KANBAN_SCAN_PATHS, {"elsewhere/FEAT-003-far.md": "FEAT-003"})
    assert "FEAT-003" in _scan_ids(repo)


# --- 2. paths.* wins, with one warning naming the ignored copy ----------------------

BOTH_IGNORE = (
    "kanban:\n"
    "  theme: software\n"
    "  paths:\n"
    '    root: "work/"\n'
    "    ignore:\n"
    '      - "**/paths-level/**"\n'
    "  ignore:\n"
    '    - "**/kanban-level/**"\n'
)

BOTH_SCAN_PATHS = (
    "kanban:\n"
    "  theme: software\n"
    "  paths:\n"
    '    root: "work/"\n'
    "    scan_paths:\n"
    '      - "paths-level/"\n'
    "  scan_paths:\n"
    '    - "kanban-level/"\n'
)


def test_both_ignore_paths_wins_with_one_warning(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        config = _load(tmp_path, BOTH_IGNORE)
    assert config.paths.ignore == ["**/paths-level/**"]
    warnings = _warnings(caplog)
    assert len(warnings) == 1, warnings
    assert "kanban.ignore" in warnings[0], warnings


def test_both_scan_paths_paths_wins_with_one_warning(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        config = _load(tmp_path, BOTH_SCAN_PATHS)
    assert config.paths.scan_paths == ["paths-level/"]
    warnings = _warnings(caplog)
    assert len(warnings) == 1, warnings
    assert "kanban.scan_paths" in warnings[0], warnings


def test_both_ignore_and_scan_paths_each_warn_once(tmp_path, caplog):
    text = BOTH_SCAN_PATHS.replace(
        "  paths:\n", '  paths:\n    ignore: ["**/p/**"]\n'
    ) + '  ignore: ["**/k/**"]\n'
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        config = _load(tmp_path, text)
    assert config.paths.ignore == ["**/p/**"]
    assert config.paths.scan_paths == ["paths-level/"]
    warnings = _warnings(caplog)
    assert len(warnings) == 2, warnings
    assert sum("kanban.ignore" in w for w in warnings) == 1, warnings
    assert sum("kanban.scan_paths" in w for w in warnings) == 1, warnings


# --- 3. a bare kanban.ignore: is "no patterns", as #194 --------------------------------

NULL_KANBAN_IGNORE = (
    "kanban:\n"
    "  theme: software\n"
    "  paths:\n"
    '    root: "work/"\n'
    "  ignore:\n"
)


def test_null_kanban_ignore_loads_as_empty(tmp_path):
    assert _load(tmp_path, NULL_KANBAN_IGNORE).paths.ignore == []


def test_null_kanban_ignore_scan_lists_archive(tmp_path):
    repo = _repo(tmp_path, NULL_KANBAN_IGNORE, {
        "work/features/FEAT-001-live.md": "FEAT-001",
        "work/features/archive/FEAT-002-old.md": "FEAT-002",
    })
    assert _scan_ids(repo) == {"FEAT-001", "FEAT-002"}


def test_single_string_kanban_ignore_is_one_pattern(tmp_path):
    # mirrors _ignore_list: a single string is one pattern (#204)
    text = NULL_KANBAN_IGNORE.replace("  ignore:\n", '  ignore: "**/x/**"\n')
    assert _load(tmp_path, text).paths.ignore == ["**/x/**"]


# --- 4. controls ---------------------------------------------------------------------


def test_control_paths_ignore_alone_unchanged(tmp_path, caplog):
    text = 'kanban:\n  theme: software\n  paths:\n    root: "work/"\n    ignore: ["**/p/**"]\n'
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        config = _load(tmp_path, text)
    assert config.paths.ignore == ["**/p/**"]
    assert _warnings(caplog) == []


def test_control_null_paths_ignore_still_empty(tmp_path):
    text = 'kanban:\n  theme: software\n  paths:\n    root: "work/"\n    ignore:\n'
    assert _load(tmp_path, text).paths.ignore == []


def test_control_no_ignore_anywhere_gives_defaults(tmp_path, caplog):
    text = 'kanban:\n  theme: software\n  paths:\n    root: "work/"\n'
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        config = _load(tmp_path, text)
    assert config.paths.ignore == DEFAULTS
    assert config.paths.scan_paths == []
    assert _warnings(caplog) == []


def test_control_no_ignore_anywhere_scan_skips_archive(tmp_path):
    text = 'kanban:\n  theme: software\n  paths:\n    root: "work/"\n'
    repo = _repo(tmp_path, text, {
        "work/features/FEAT-001-live.md": "FEAT-001",
        "work/features/archive/FEAT-002-old.md": "FEAT-002",
    })
    assert _scan_ids(repo) == {"FEAT-001"}


def test_control_root_dot_without_kanban_ignore_lists_ideas(tmp_path):
    text = KANBAN_IGNORE_ROOT_DOT.split("  ignore:")[0]
    repo = _repo(tmp_path, text, {
        "ideas/FEAT-002-idea.md": "FEAT-002",
        "work/FEAT-001-live.md": "FEAT-001",
    })
    assert _scan_ids(repo) == {"FEAT-001", "FEAT-002"}


def test_control_multi_board_unaffected(tmp_path, caplog):
    text = (
        'version: "2.0"\n'
        "boards:\n"
        "  - name: dev\n"
        "    preset: software\n"
        '    path: "work/"\n'
        '    ignore: ["**/board/**"]\n'
        "default_board: dev\n"
        "kanban:\n"
        '  ignore: ["**/kanban-level/**"]\n'
        '  scan_paths: ["elsewhere/"]\n'
    )
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        config = _load(tmp_path, text)
    assert config.is_multi_board
    assert config.boards[0].ignore == ["**/board/**"]
    assert config.boards[0].scan_paths == []
    assert config.paths.scan_paths == []
    assert _warnings(caplog) == []


@pytest.mark.parametrize("text", [KANBAN_IGNORE_ROOT_DOT, KANBAN_SCAN_PATHS,
                                  NULL_KANBAN_IGNORE], ids=["ignore", "scan_paths", "null"])
def test_save_round_trips_effective_values(tmp_path, text):
    config = _load(tmp_path, text)
    out = tmp_path / "saved" / "config.yaml"
    config.save(out)
    config_mod._theme_cache.clear()
    reloaded = KanbanConfig.load(out)
    assert reloaded.paths.ignore == config.paths.ignore, out.read_text()
    assert reloaded.paths.scan_paths == config.paths.scan_paths, out.read_text()
    # and the effective values are the kanban-level ones, not the defaults
    expected = {
        KANBAN_IGNORE_ROOT_DOT: (["ideas/*"], []),
        KANBAN_SCAN_PATHS: (DEFAULTS, ["elsewhere/"]),
        NULL_KANBAN_IGNORE: ([], []),
    }[text]
    assert (reloaded.paths.ignore, reloaded.paths.scan_paths) == expected, out.read_text()


# --- 5. fleet shapes (copied from the consumers' .kanban/config.yaml) -----------------

NOESIS_SHIP = """\
# yurtle-kanban configuration
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

# carclaw and noesis-ships-comm carry this same shape
CARCLAW = """\
# yurtle-kanban configuration
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


def test_fleet_noesis_ship_effective_config(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        config = _load(tmp_path, NOESIS_SHIP)
    assert config.paths.ignore == ["**/archive/**", "**/templates/**", "**/_TEMPLATE*"]
    assert config.paths.scan_paths == [
        "kanban-work/expeditions/", "kanban-work/tasks/", "kanban-work/bugs/",
    ]
    assert _warnings(caplog) == []


def test_fleet_noesis_ship_template_not_listed(tmp_path):
    repo = _repo(tmp_path, NOESIS_SHIP, {
        "kanban-work/tasks/TASK-001-real.md": "TASK-001",
        "kanban-work/tasks/_TEMPLATE-task.md": "TASK-999",
    })
    ids = _scan_ids(repo)
    assert "TASK-001" in ids
    assert "TASK-999" not in ids


def test_fleet_carclaw_effective_config(tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        config = _load(tmp_path, CARCLAW)
    assert config.paths.root == "kanban-work"
    assert config.paths.scan_paths == [
        "kanban-work/features", "kanban-work/tasks", "kanban-work/bugs",
    ]
    assert config.paths.ignore == ["**/archive/**", "**/templates/**"]
    assert _warnings(caplog) == []
