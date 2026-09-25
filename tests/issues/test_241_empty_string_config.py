"""#241: an explicit empty-string config scalar keeps its pre-#220 meaning.

Follow-up from the review of PR #237 (#220), which made ``data.get(k) or default``
turn ``""`` into the default too. So ``boards[].path: ""`` (``Path("")`` → ``.``, the
repo root) silently became ``work/``.

Decided behaviour: only a YAML null (bare key) means "use the default"; an explicit
empty string loads as ``""``, as before #220:

1. ``boards[].path: ""`` loads as ``""``, and the board scans the repo root:
   ``list`` shows an item under ``features/`` at the repo root, as it did before #220.
2. ``kanban.paths.root: ""`` loads as ``""``.
3. ``boards[].name: ""``, ``boards[].preset: ""``, ``kanban.theme: ""`` load as ``""``.

Controls: a bare (null) key still gives the default (#220).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import main
from yurtle_kanban.config import KanbanConfig

# --- config builders ------------------------------------------------------------------


def _single(*, root: str = '    root: "work/"', theme: str = "  theme: software") -> str:
    lines = ["kanban:", theme, "  paths:", root]
    return "\n".join(lines) + "\n"


def _multi(
    *,
    name: str = "  - name: dev",
    preset: str = "    preset: software",
    path: str = '    path: "work/"',
) -> str:
    lines = ['version: "2.0"', "boards:", name, preset, path]
    return "\n".join(lines) + "\n"


# --- helpers --------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)


def _item(path: Path, item_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'---\nid: {item_id}\ntitle: "t"\ntype: feature\nstatus: backlog\n'
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
    # at the repo root, NOT under work/: only a repo-root board finds it
    _item(repo / "features" / "FEAT-001-root.md", "FEAT-001")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    return repo


def _load(tmp_path: Path, text: str) -> KanbanConfig:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(text)
    config_mod._theme_cache.clear()
    return KanbanConfig.load(cfg)


def _list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str):
    repo = _repo(tmp_path, text)
    monkeypatch.chdir(repo)
    config_mod._theme_cache.clear()
    return CliRunner().invoke(main, ["list"])


def _assert_list_ok(result) -> None:
    assert "Traceback" not in (result.output or ""), result.output
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        raise AssertionError(repr(result.exception)) from result.exception
    assert result.exit_code == 0, result.output


# --- 1. boards[].path: "" is the repo root --------------------------------------------


def test_empty_board_path_loads_as_empty(tmp_path):
    config = _load(tmp_path, _multi(path='    path: ""'))
    assert config.boards[0].path == ""
    assert config.paths.root == ""


def test_cli_list_empty_board_path_scans_repo_root(tmp_path, monkeypatch):
    result = _list(tmp_path, monkeypatch, _multi(path='    path: ""'))
    _assert_list_ok(result)
    assert "FEAT-001" in result.output, result.output


# --- 2. kanban.paths.root: "" ---------------------------------------------------------


def test_empty_root_loads_as_empty(tmp_path):
    assert _load(tmp_path, _single(root='    root: ""')).paths.root == ""


# --- 3. name / preset / theme: "" -----------------------------------------------------


def test_empty_board_name_loads_as_empty(tmp_path):
    assert _load(tmp_path, _multi(name='  - name: ""')).boards[0].name == ""


def test_empty_board_preset_loads_as_empty(tmp_path):
    config = _load(tmp_path, _multi(preset='    preset: ""'))
    assert config.boards[0].preset == ""
    assert config.theme == ""


def test_empty_theme_loads_as_empty(tmp_path):
    assert _load(tmp_path, _single(theme='  theme: ""')).theme == ""


# --- controls: a bare (null) key still means the default (#220) -----------------------


def test_control_bare_board_path_loads_default(tmp_path):
    config = _load(tmp_path, _multi(path="    path:"))
    assert config.boards[0].path == "work/"
    assert config.paths.root == "work/"


def test_control_bare_root_loads_default(tmp_path):
    assert _load(tmp_path, _single(root="    root:")).paths.root == "work/"


def test_control_bare_board_name_loads_default(tmp_path):
    assert _load(tmp_path, _multi(name="  - name:")).boards[0].name == "default"


def test_control_bare_board_preset_loads_default(tmp_path):
    config = _load(tmp_path, _multi(preset="    preset:"))
    assert (config.boards[0].preset, config.theme) == ("software", "software")


def test_control_bare_theme_loads_default(tmp_path):
    assert _load(tmp_path, _single(theme="  theme:")).theme == "software"


def test_control_work_board_does_not_see_repo_root_item(tmp_path, monkeypatch):
    result = _list(tmp_path, monkeypatch, _multi())
    _assert_list_ok(result)
    assert "FEAT-001" not in result.output, result.output
