"""#503: a string ``scan_paths`` is one path, not one path per character.

``paths.scan_paths: "a/"`` loaded as the string ``"a/"``; ``get_work_paths``
then iterated it into ``Path("a")`` and ``Path("/")`` — the filesystem root.
Decided ([steer] on #503), mirroring ``_ignore_list`` (#194 null, #204 string):

1. A string ``scan_paths`` loads as ``[that string]``; the scan finds items under
   it and never the per-character paths.
2. A bare ``scan_paths:`` (YAML null) loads as ``[]``; a list is unchanged.
3. Any other type (a number, a mapping) is a ValueError naming ``scan_paths``,
   which the CLI reports as an invalid config (exit 1), not a traceback.
4. A multi-board board's ``scan_paths`` follows the same rule.
5. A non-mapping ``kanban.paths`` is a ValueError saying ``kanban.paths`` must be
   a mapping; a bare ``paths:`` still loads the defaults.

Tests go through ``KanbanConfig.load`` on YAML files so they hold whether or not
#482's kanban-level ``scan_paths`` fallback is present; the one kanban-level case
is skipped until that fallback exists (detected by behaviour).
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import pytest
from click.testing import CliRunner

from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import main
from yurtle_kanban.config import KanbanConfig, PathConfig
from yurtle_kanban.service import KanbanService

# --- config builders ------------------------------------------------------------------


def _single(scan_line: str) -> str:
    """Single-board config whose ``paths.scan_paths`` is ``scan_line`` (raw YAML)."""
    return f'kanban:\n  theme: software\n  paths:\n    root: "work/"\n    scan_paths: {scan_line}\n'


def _multi(scan_line: str) -> str:
    return (
        'version: "2.0"\nboards:\n  - name: dev\n    preset: software\n'
        f'    path: "work/"\n    scan_paths: {scan_line}\ndefault_board: dev\n'
    )


BAD_SCAN = {"int": "5", "mapping": "{a: b}"}


# --- helpers --------------------------------------------------------------------------


def _load(tmp_path: Path, text: str) -> KanbanConfig:
    cfg = tmp_path / "config.yaml"
    cfg.write_text(text)
    config_mod._theme_cache.clear()
    return KanbanConfig.load(cfg)


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


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)


def _item(path: Path, item_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'---\nid: {item_id}\ntitle: "t"\ntype: feature\nstatus: backlog\n'
        f"priority: medium\ncreated: 2026-09-26\n---\n\n# {item_id}: t\n"
    )


def _repo(tmp_path: Path, config_yaml: str) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@t.com")
    _git(repo, "config", "user.name", "T")
    (repo / ".kanban").mkdir()
    (repo / ".kanban" / "config.yaml").write_text(config_yaml)
    _item(repo / "a" / "FEAT-001-in-a.md", "FEAT-001")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    return repo


def _cli_list(repo: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(repo)
    config_mod._theme_cache.clear()
    return CliRunner().invoke(main, ["list"])


def _assert_invalid(result, *words: str) -> None:
    """Exit 1 with the CLI's one-line invalid-config message, never a traceback."""
    out = result.output or ""
    assert "Traceback" not in out, out
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        raise AssertionError(repr(result.exception)) from result.exception
    assert result.exit_code == 1, out
    flat = " ".join(out.split())
    assert "Invalid" in flat and "config.yaml" in flat, out
    for word in words:
        assert word in flat, out


def _kanban_level_scan_paths_supported() -> bool:
    """Whether #482's kanban-level ``scan_paths`` fallback is present."""
    with tempfile.TemporaryDirectory() as d:
        cfg = Path(d) / "config.yaml"
        cfg.write_text('kanban:\n  theme: software\n  scan_paths:\n    - "probe/"\n')
        try:
            return KanbanConfig.load(cfg).paths.scan_paths == ["probe/"]
        except Exception:
            return False


# --- 1. a string is one path ----------------------------------------------------------


def test_string_scan_paths_loads_as_one_path(tmp_path):
    config = _load(tmp_path, _single('"a/"'))
    assert config.paths.scan_paths == ["a/"]


def test_string_scan_paths_work_paths_not_per_character(tmp_path):
    config = _load(tmp_path, _single('"a/"'))
    assert config.paths.scan_paths == ["a/"]  # fail fast: never scan "/"
    work = config.get_work_paths()
    assert Path("a/") in work
    assert Path("/") not in work, work


def test_string_scan_paths_scan_finds_item(tmp_path):
    repo = _repo(tmp_path, _single('"a/"'))
    config_mod._theme_cache.clear()
    config = KanbanConfig.load(repo / ".kanban" / "config.yaml")
    assert config.paths.scan_paths == ["a/"]  # fail fast: never scan "/"
    service = KanbanService(config, repo)
    assert {i.id for i in service.scan()} == {"FEAT-001"}


def test_string_scan_paths_cli_list(tmp_path, monkeypatch):
    repo = _repo(tmp_path, _single('"a/"'))
    config_mod._theme_cache.clear()
    # fail fast: never let the CLI scan "/"
    assert KanbanConfig.load(repo / ".kanban" / "config.yaml").paths.scan_paths == ["a/"]
    result = _cli_list(repo, monkeypatch)
    assert result.exception is None, repr(result.exception)
    assert result.exit_code == 0, result.output
    assert "FEAT-001" in result.output, result.output


@pytest.mark.skipif(
    not _kanban_level_scan_paths_supported(),
    reason="kanban-level scan_paths fallback (#482 / PR #497) not present yet",
)
def test_kanban_level_string_scan_paths_loads_as_one_path(tmp_path):
    config = _load(tmp_path, 'kanban:\n  theme: software\n  scan_paths: "a/"\n')
    assert config.paths.scan_paths == ["a/"]


# --- 2. null and list -----------------------------------------------------------------


def test_null_scan_paths_loads_empty(tmp_path):
    assert _load(tmp_path, _single("")).paths.scan_paths == []


def test_control_list_scan_paths_unchanged(tmp_path):
    assert _load(tmp_path, _single('["a/", "b/"]')).paths.scan_paths == ["a/", "b/"]


def test_control_absent_scan_paths_empty(tmp_path):
    config = _load(tmp_path, 'kanban:\n  theme: software\n  paths:\n    root: "work/"\n')
    assert config.paths.scan_paths == []


# --- 3. other types are an invalid config ---------------------------------------------


@pytest.mark.parametrize("case", sorted(BAD_SCAN))
def test_bad_scan_paths_raises_value_error(tmp_path, case):
    exc = _load_error(tmp_path, _single(BAD_SCAN[case]))
    assert "scan_paths" in str(exc), str(exc)


@pytest.mark.parametrize("case", sorted(BAD_SCAN))
def test_bad_scan_paths_cli_invalid_config(tmp_path, monkeypatch, case):
    repo = _repo(tmp_path, _single(BAD_SCAN[case]))
    _assert_invalid(_cli_list(repo, monkeypatch), "scan_paths")


# --- 4. multi-board boards ------------------------------------------------------------


def test_multi_board_string_scan_paths_loads_as_one_path(tmp_path):
    config = _load(tmp_path, _multi('"a/"'))
    assert config.boards[0].scan_paths == ["a/"]
    assert config.paths.scan_paths == ["a/"]  # the aggregate, too


def test_multi_board_null_scan_paths_empty(tmp_path):
    assert _load(tmp_path, _multi("")).boards[0].scan_paths == []


def test_control_multi_board_list_scan_paths_unchanged(tmp_path):
    assert _load(tmp_path, _multi('["a/", "b/"]')).boards[0].scan_paths == ["a/", "b/"]


@pytest.mark.parametrize("case", sorted(BAD_SCAN))
def test_multi_board_bad_scan_paths_raises_value_error(tmp_path, case):
    exc = _load_error(tmp_path, _multi(BAD_SCAN[case]))
    assert "scan_paths" in str(exc), str(exc)


# --- 5. kanban.paths must be a mapping ------------------------------------------------


@pytest.mark.parametrize("value", ['"x"', "[a/, b/]", "5"])
def test_non_mapping_paths_raises_value_error(tmp_path, value):
    msg = str(_load_error(tmp_path, f"kanban:\n  theme: software\n  paths: {value}\n"))
    assert "kanban.paths" in msg and "mapping" in msg.lower(), msg


def test_non_mapping_paths_cli_invalid_config(tmp_path, monkeypatch):
    repo = _repo(tmp_path, 'kanban:\n  theme: software\n  paths: "x"\n')
    _assert_invalid(_cli_list(repo, monkeypatch), "kanban.paths")


def test_control_null_paths_loads_defaults(tmp_path):
    assert _load(tmp_path, "kanban:\n  theme: software\n  paths:\n").paths == PathConfig()
