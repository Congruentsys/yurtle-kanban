"""#194: a bare ``ignore:`` (YAML null) must load as ``[]``, not crash the scan.

Under single-board ``kanban.paths`` and under a ``boards[]`` entry, a null
``ignore`` means "no ignore patterns": the config loads ``ignore == []`` (NOT the
defaults), and scanning lists every item, including ones in an ``archive/``
folder the defaults would have skipped. Saving the loaded config must not crash.
Controls: an absent ``ignore`` key still gives the defaults; an explicit list is
kept as is.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import main
from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.service import KanbanService

DEFAULTS = ["**/archive/**", "**/templates/**"]
IDS = {"FEAT-001", "FEAT-002"}  # FEAT-002 lives under work/features/archive/

NULL = "null"  # bare ``ignore:``
ABSENT = "absent"  # no ``ignore`` key at all
EXPLICIT = "explicit"  # ``ignore: ["**/hidden/**"]``
EXPLICIT_LIST = ["**/hidden/**"]


def _ignore_lines(mode: str, indent: str) -> list[str]:
    if mode == NULL:
        return [f"{indent}ignore:"]
    if mode == EXPLICIT:
        return [f"{indent}ignore:"] + [f'{indent}  - "{p}"' for p in EXPLICIT_LIST]
    return []


def _single_yaml(mode: str) -> str:
    lines = ["kanban:", "  theme: software", "  paths:", '    root: "work/"',
             "    scan_paths:", '      - "work/"', *_ignore_lines(mode, "    ")]
    return "\n".join(lines) + "\n"


def _multi_yaml(mode: str) -> str:
    lines = ['version: "2.0"', "boards:", "  - name: dev", "    preset: software",
             '    path: "work/"', *_ignore_lines(mode, "    ")]
    return "\n".join(lines) + "\ndefault_board: dev\n"


YAMLS = {"single": _single_yaml, "multi": _multi_yaml}


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)


def _item(path: Path, item_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nid: {item_id}\ntitle: \"t\"\ntype: feature\nstatus: backlog\n"
        f"priority: medium\ncreated: 2026-09-25\n---\n\n# {item_id}: t\n"
    )


def _repo(tmp_path: Path, config_yaml: str) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@t.com")
    _git(repo, "config", "user.name", "T")
    (repo / ".kanban").mkdir()
    (repo / ".kanban" / "config.yaml").write_text(config_yaml)
    _item(repo / "work" / "features" / "FEAT-001-live.md", "FEAT-001")
    _item(repo / "work" / "features" / "archive" / "FEAT-002-old.md", "FEAT-002")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    return repo


def _load(tmp_path: Path, text: str) -> KanbanConfig:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(text)
    config_mod._theme_cache.clear()
    return KanbanConfig.load(cfg)


def _ignores(config: KanbanConfig, kind: str) -> list[str] | None:
    return config.paths.ignore if kind == "single" else config.boards[0].ignore


# --- config loading -------------------------------------------------------------------


def test_single_null_ignore_loads_as_empty_list(tmp_path):
    config = _load(tmp_path, _single_yaml(NULL))
    assert config.paths.ignore == []


def test_multi_null_ignore_loads_as_empty_list(tmp_path):
    config = _load(tmp_path, _multi_yaml(NULL))
    assert len(config.boards) == 1
    assert all(board.ignore == [] for board in config.boards)


@pytest.mark.parametrize("kind", ["single", "multi"])
def test_control_absent_ignore_gives_defaults(tmp_path, kind):
    config = _load(tmp_path, YAMLS[kind](ABSENT))
    assert _ignores(config, kind) == DEFAULTS


@pytest.mark.parametrize("kind", ["single", "multi"])
def test_control_explicit_ignore_kept(tmp_path, kind):
    config = _load(tmp_path, YAMLS[kind](EXPLICIT))
    assert _ignores(config, kind) == EXPLICIT_LIST


# --- config round-trip ----------------------------------------------------------------


@pytest.mark.parametrize("kind", ["single", "multi"])
def test_save_loaded_null_ignore_config_does_not_crash(tmp_path, kind):
    config = _load(tmp_path, YAMLS[kind](NULL))
    out = tmp_path / "saved" / "config.yaml"
    config.save(out)
    reloaded = KanbanConfig.load(out)
    assert _ignores(reloaded, kind) is not None, out.read_text()


def test_multi_save_round_trip_keeps_empty_ignore(tmp_path):
    config = _load(tmp_path, _multi_yaml(NULL))
    out = tmp_path / "saved" / "config.yaml"
    config.save(out)
    assert KanbanConfig.load(out).boards[0].ignore == [], out.read_text()


# --- scanning, service and CLI --------------------------------------------------------


@pytest.mark.parametrize("kind", ["single", "multi"])
def test_service_scan_with_null_ignore_lists_all(tmp_path, kind):
    repo = _repo(tmp_path, YAMLS[kind](NULL))
    config_mod._theme_cache.clear()
    service = KanbanService(KanbanConfig.load(repo / ".kanban" / "config.yaml"), repo)
    assert {i.id for i in service.scan()} == IDS


@pytest.mark.parametrize("kind", ["single", "multi"])
def test_cli_list_with_null_ignore_lists_all(tmp_path, monkeypatch, kind):
    repo = _repo(tmp_path, YAMLS[kind](NULL))
    monkeypatch.chdir(repo)
    config_mod._theme_cache.clear()
    result = CliRunner().invoke(main, ["list"])
    assert result.exception is None, repr(result.exception)
    assert result.exit_code == 0, result.output
    for item_id in IDS:
        assert item_id in result.output, result.output


@pytest.mark.parametrize("kind", ["single", "multi"])
def test_control_absent_ignore_scan_skips_archive(tmp_path, kind):
    repo = _repo(tmp_path, YAMLS[kind](ABSENT))
    config_mod._theme_cache.clear()
    service = KanbanService(KanbanConfig.load(repo / ".kanban" / "config.yaml"), repo)
    assert {i.id for i in service.scan()} == {"FEAT-001"}
