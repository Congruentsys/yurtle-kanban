"""#272: validate ``kanban.theme`` / ``boards[].preset`` is a known string name.

Follow-up from the review of PR #271 (#256): ``theme: 5`` is accepted silently,
``theme: [a]`` crashes with ``TypeError: unhashable type: 'list'`` in
``_load_builtin_theme``, and an unknown name loads no theme without a word.

Where themes come from (``config._load_builtin_theme``): first the repo-local
``<repo>/.kanban/themes/<name>.yaml`` (repo root, then cwd), then the package's
``themes/`` dir (``sys.prefix/share/yurtle-kanban/themes`` or the source tree's
``themes/``). The built-ins are ``hdd``, ``nautical``, ``software`` and ``spec``.

Decided behaviour:

1. A non-string theme/preset (``5``, ``[a]``, ``{a: 1}``, ``true``) makes
   ``KanbanConfig.load`` raise ``ValueError`` naming the key and saying "string";
   ``list`` exits 1 with ``Invalid …config.yaml: …`` (the #220 path), no traceback.
2. An unknown string name (``nosuch``) loads, value kept, and emits ONE warning on the
   ``yurtle-kanban`` logger naming the key and the value and listing the theme names
   that exist (the built-ins, plus any custom theme in ``.kanban/themes/``).

Controls: every built-in name → no warning; a custom theme in ``.kanban/themes/``
→ no warning; a bare (null) key → the default, no warning; ``""`` → only #256's
empty warning (exactly one warning).
"""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import main
from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.service import KanbanService

LOGGER = "yurtle-kanban"
BOARD = "devboard"
BUILTINS = ("hdd", "nautical", "software", "spec")
CUSTOM = "acmeflow"
NON_STRINGS = ["5", "[a]", "{a: 1}", "true"]

# --- config builders ------------------------------------------------------------------


def _single(value: str) -> str:
    lines = ["kanban:", f"  theme: {value}", "  paths:", '    root: "work/"']
    return "\n".join(lines) + "\n"


def _multi(value: str) -> str:
    lines = ['version: "2.0"', "boards:", f"  - name: {BOARD}", f"    preset: {value}"]
    return "\n".join(lines + ['    path: "work/"']) + "\n"


BUILDERS = {"theme": _single, "preset": _multi}

# --- helpers --------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_theme_cache() -> Iterator[None]:
    """`_theme_cache` is keyed by name only: never let one test's hit leak into another."""
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
    # the handler sits on both root (caplog) and our logger: one record, seen twice
    records = {id(r): r for r in caplog.records}.values()
    return [r.getMessage() for r in records if r.name == LOGGER and r.levelno >= logging.WARNING]


def _write_repo(root: Path, text: str, *, custom: bool = False) -> Path:
    """``root/.kanban/config.yaml`` (+ a custom theme beside it); returns the config."""
    kanban = root / ".kanban"
    kanban.mkdir(parents=True, exist_ok=True)
    cfg = kanban / "config.yaml"
    cfg.write_text(text)
    if custom:
        (kanban / "themes").mkdir(exist_ok=True)
        theme = ["name: Acme Flow", "item_types:", "  task:", "    id_prefix: ACME"]
        (kanban / "themes" / f"{CUSTOM}.yaml").write_text("\n".join(theme) + "\n")
    return cfg


def _load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str, *, custom: bool = False
) -> KanbanConfig:
    repo = tmp_path / "repo"
    cfg = _write_repo(repo, text, custom=custom)
    monkeypatch.chdir(repo)  # the CLI runs from the repo root; no stray .kanban/themes
    config_mod._theme_cache.clear()
    try:
        return KanbanConfig.load(cfg)
    finally:
        config_mod._theme_cache.clear()


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)


def _list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str):
    repo = tmp_path / "repo"
    _write_repo(repo, text)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@t.com")
    _git(repo, "config", "user.name", "T")
    (repo / "work").mkdir()
    (repo / "work" / ".keep").write_text("")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    monkeypatch.chdir(repo)
    config_mod._theme_cache.clear()
    try:
        return CliRunner().invoke(main, ["list"])
    finally:
        config_mod._theme_cache.clear()


# --- 1. a non-string name is refused at load ------------------------------------------


@pytest.mark.parametrize("key", ["theme", "preset"])
@pytest.mark.parametrize("value", NON_STRINGS)
def test_non_string_name_raises_value_error(tmp_path, monkeypatch, key, value):
    with pytest.raises(ValueError) as exc:
        _load(tmp_path, monkeypatch, BUILDERS[key](value))
    msg = str(exc.value)
    assert key in msg, msg
    assert "string" in msg.lower(), msg


@pytest.mark.parametrize("key", ["theme", "preset"])
@pytest.mark.parametrize("value", NON_STRINGS)
def test_cli_list_non_string_name_exits_one_cleanly(tmp_path, monkeypatch, key, value):
    result = _list(tmp_path, monkeypatch, BUILDERS[key](value))
    out = result.output or ""
    assert "Traceback" not in out, out
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        raise AssertionError(repr(result.exception)) from result.exception
    assert result.exit_code == 1, out
    flat = " ".join(out.split())  # rich may wrap the long path
    assert "Invalid" in flat and "config.yaml" in flat, out
    assert key in flat and "string" in flat.lower(), out


# --- 2. an unknown name loads but warns once, listing what exists ---------------------


def _unknown_warnings(caplog: pytest.LogCaptureFixture, key: str) -> list[str]:
    return [m for m in _warnings(caplog) if key in m and "nosuch" in m]


def test_unknown_theme_warns_once_listing_builtins(tmp_path, monkeypatch, warnings_log):
    config = _load(tmp_path, monkeypatch, _single("nosuch"))
    assert config.theme == "nosuch"  # value kept
    found = _unknown_warnings(warnings_log, "theme")
    assert len(found) == 1, _warnings(warnings_log)
    assert len(_warnings(warnings_log)) == 1, _warnings(warnings_log)
    for name in BUILTINS:
        assert name in found[0], found[0]


def test_unknown_board_preset_warns_once_listing_builtins(tmp_path, monkeypatch, warnings_log):
    config = _load(tmp_path, monkeypatch, _multi("nosuch"))
    assert config.boards[0].preset == "nosuch"  # value kept
    found = _unknown_warnings(warnings_log, "preset")
    assert len(found) == 1, _warnings(warnings_log)
    assert len(_warnings(warnings_log)) == 1, _warnings(warnings_log)
    for name in BUILTINS:
        assert name in found[0], found[0]


@pytest.mark.parametrize("key", ["theme", "preset"])
def test_unknown_name_lists_custom_themes_too(tmp_path, monkeypatch, warnings_log, key):
    _load(tmp_path, monkeypatch, BUILDERS[key]("nosuch"), custom=True)
    found = _unknown_warnings(warnings_log, key)
    assert len(found) == 1, _warnings(warnings_log)
    assert CUSTOM in found[0], found[0]
    for name in BUILTINS:
        assert name in found[0], found[0]


@pytest.mark.parametrize("key", ["theme", "preset"])
def test_cli_list_unknown_name_still_exits_zero(tmp_path, monkeypatch, key):
    result = _list(tmp_path, monkeypatch, BUILDERS[key]("nosuch"))
    assert "Traceback" not in (result.output or ""), result.output
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        raise AssertionError(repr(result.exception)) from result.exception
    assert result.exit_code == 0, result.output


# --- controls -------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["theme", "preset"])
@pytest.mark.parametrize("name", BUILTINS)
def test_control_builtin_name_no_warning(tmp_path, monkeypatch, warnings_log, key, name):
    config = _load(tmp_path, monkeypatch, BUILDERS[key](name))
    got = config.theme if key == "theme" else config.boards[0].preset
    assert got == name
    assert _warnings(warnings_log) == []


@pytest.mark.parametrize("key", ["theme", "preset"])
def test_control_custom_theme_no_warning(tmp_path, monkeypatch, warnings_log, key):
    config = _load(tmp_path, monkeypatch, BUILDERS[key](CUSTOM), custom=True)
    got = config.theme if key == "theme" else config.boards[0].preset
    assert got == CUSTOM
    assert _warnings(warnings_log) == []


@pytest.mark.parametrize("key", ["theme", "preset"])
def test_control_bare_key_default_no_warning(tmp_path, monkeypatch, warnings_log, key):
    config = _load(tmp_path, monkeypatch, BUILDERS[key](""))  # `key: ` is YAML null
    got = config.theme if key == "theme" else config.boards[0].preset
    assert got == "software"
    assert _warnings(warnings_log) == []


@pytest.mark.parametrize("key", ["theme", "preset"])
@pytest.mark.parametrize("value", ['""', '"  "'])
def test_control_empty_name_only_256_warning(tmp_path, monkeypatch, warnings_log, key, value):
    _load(tmp_path, monkeypatch, BUILDERS[key](value))
    found = _warnings(warnings_log)
    assert len(found) == 1, found
    assert key in found[0] and "empty" in found[0].lower(), found[0]


# --- round 2: the config's own repo is searched, whatever the cwd ---------------------
#
# Review of PR #284: the load-time check looked only in cwd/.kanban/themes, so from an
# unrelated cwd a repo-only theme warned falsely, and a repo override of a built-in name
# was shadowed: the built-in got cached at load, and the service then got it too.

MARKER = "repo-override-272"


def _repo_elsewhere(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str, theme_name: str, body: str
) -> tuple[Path, Path]:
    """A repo with ``.kanban/themes/<theme_name>.yaml``; cwd is an unrelated dir."""
    repo = tmp_path / "repo"
    cfg = _write_repo(repo, text)
    (repo / ".kanban" / "themes").mkdir(exist_ok=True)
    (repo / ".kanban" / "themes" / f"{theme_name}.yaml").write_text(body)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    return repo, cfg


def _unknown_theme_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [m for m in _warnings(caplog) if "not a known theme" in m or "Available" in m]


@pytest.mark.parametrize("key", ["theme", "preset"])
def test_repo_only_theme_from_other_cwd_no_warning(tmp_path, monkeypatch, warnings_log, key):
    body = "\n".join(["theme:", "  name: acme", "item_types:", "  task:", "    id_prefix: AC"])
    _, cfg = _repo_elsewhere(tmp_path, monkeypatch, BUILDERS[key]("acme"), "acme", body + "\n")
    config = KanbanConfig.load(cfg)
    got = config.theme if key == "theme" else config.boards[0].preset
    assert got == "acme"
    assert _unknown_theme_warnings(warnings_log) == [], _warnings(warnings_log)
    assert _warnings(warnings_log) == []


def _override_body() -> str:
    """The built-in nautical, plus a top-level key that only the repo's copy has."""
    builtin = config_mod._load_builtin_theme("nautical")
    assert builtin is not None and MARKER not in builtin  # the built-in exists, unmarked
    config_mod._theme_cache.clear()
    return yaml.safe_dump({**builtin, "override_marker": MARKER})


def test_repo_override_of_builtin_wins_from_other_cwd(tmp_path, monkeypatch, warnings_log):
    body = _override_body()
    repo, cfg = _repo_elsewhere(tmp_path, monkeypatch, _multi("nautical"), "nautical", body)
    config = KanbanConfig.load(cfg)
    assert config.boards[0].preset == "nautical"
    assert _warnings(warnings_log) == []
    service = KanbanService(config, repo)
    theme = service._load_board_theme(config.boards[0])
    assert theme is not None
    assert theme.get("override_marker") == MARKER, "service got the built-in, not the override"
