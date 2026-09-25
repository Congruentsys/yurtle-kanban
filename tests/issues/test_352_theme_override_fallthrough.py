"""Issue #352 — broken local theme overrides fall through, with one warning.

Follow-up from the review of PR #345 (#338). ``_load_builtin_theme`` walks
``_theme_dirs`` (repo ``.kanban/themes``, cwd ``.kanban/themes``, then the package
themes), first match wins. Today the two kinds of broken local override behave
differently:

- an *unparseable* ``software.yaml`` hits ``except Exception: continue`` and falls
  through to the built-in ``software`` **silently**;
- a *list/scalar* ``software.yaml`` returns None and **shadows** the built-in, so the
  board has no theme at all (no WIP limits, no workflows).

``_available_themes`` also globs ``*.yaml``, so a broken ``mytheme.yaml`` is listed
under "not a known theme … Available: …".

Decided behaviour ([steer], bucket 1/2, G1/G2):

1. An unparseable local override falls through to the next dir (ending at the
   built-in), with exactly one warning naming the file.
2. A list/scalar local override also falls through, with one warning.
3. A custom name that exists only locally and is broken (either kind):
   ``_load_builtin_theme`` returns None, and the caller's "not a known theme"
   warning does NOT list it under "Available:".
4. ``_available_themes`` lists only names that load as a mapping from some dir:
   valid local custom themes and the built-ins.
5. At most one warning per broken file per process: repeated lookups don't re-warn.
6. End to end: ``theme: software`` + a broken local ``software.yaml`` → ``board``
   shows the built-in software columns, exits 0, and no "not a known theme".
7. Controls: a valid local override still wins; a valid custom theme loads.
"""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import CliRunner

from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import main
from yurtle_kanban.config import KanbanConfig

LOGGER = "yurtle-kanban"
BUILTIN = "software"
CUSTOM = "mytheme"
BUILTIN_NAMES = {"hdd", "nautical", "software", "spec"}

# The two kinds of broken theme file: unparseable YAML, and a parseable non-mapping.
BROKEN = {
    "unparseable": "columns: [unclosed\n",
    "list": "- columns\n",
    "scalar": "columns\n",
}
KINDS = list(BROKEN)
NON_MAPPING = ["list", "scalar"]

VALID_OVERRIDE = "columns:\n  zeta:\n    name: Zeta\n    order: 1\n"

SINGLE_CFG = "kanban:\n  theme: {theme}\n  paths:\n    root: work/\n"


# --- fixtures / helpers (after tests/issues/test_338_yaml_loaders_mapping.py) -------


@pytest.fixture(autouse=True)
def _clean_theme_cache() -> Iterator[None]:
    """The cache is keyed by resolved theme file (#287); clear it so no entry
    cached by one test is seen by another."""
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


def _naming(caplog: pytest.LogCaptureFixture, path: Path) -> list[str]:
    """Warnings that name ``path`` (as given or resolved)."""
    forms = {str(path), str(path.resolve())}
    return [m for m in _warnings(caplog) if any(f in m for f in forms)]


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)


def _repo(
    root: Path, themes: dict[str, str] | None = None, config: str | None = None
) -> Path:
    """A git repo at ``root`` with ``.kanban/themes/<name>.yaml`` for each of ``themes``
    and, if given, ``.kanban/config.yaml``."""
    root.mkdir(parents=True, exist_ok=True)
    kanban = root / ".kanban"
    kanban.mkdir()
    if config is not None:
        (kanban / "config.yaml").write_text(config)
    if themes:
        (kanban / "themes").mkdir()
        for name, text in themes.items():
            (kanban / "themes" / f"{name}.yaml").write_text(text)
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "t@t.com")
    _git(root, "config", "user.name", "T")
    (root / "work").mkdir()
    (root / "work" / ".keep").write_text("")
    return root


def _theme_file(repo: Path, name: str) -> Path:
    return repo / ".kanban" / "themes" / f"{name}.yaml"


def _builtin_software() -> dict[str, Any]:
    """The packaged software theme, read straight from the source tree."""
    path = Path(config_mod.__file__).resolve().parent.parent.parent / "themes" / "software.yaml"
    data = yaml.safe_load(path.read_text())
    assert isinstance(data, dict) and data["theme"]["name"] == "software"
    return data


def _available_from_warning(messages: list[str]) -> list[str]:
    """The names after "Available:" in the "not a known theme" warning."""
    hits = [m for m in messages if "not a known theme" in m]
    assert len(hits) == 1, messages
    tail = hits[0].split("Available:", 1)[1]
    return [n.strip() for n in tail.split(",") if n.strip()]


def _invoke(repo: Path, monkeypatch: pytest.MonkeyPatch, args: list[str]):
    monkeypatch.chdir(repo)
    config_mod._theme_cache.clear()
    return CliRunner().invoke(main, args)


def _no_crash(result) -> None:
    out = result.output or ""
    assert "Traceback" not in out, out
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        raise AssertionError(repr(result.exception)) from result.exception


# ---------------------------------------------------------------------------
# 1 + 2. A broken local override of a built-in falls through, with one warning
# ---------------------------------------------------------------------------


class TestOverrideFallsThrough:
    @pytest.mark.parametrize("kind", KINDS)
    def test_repo_override_falls_through_to_builtin(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
    ) -> None:
        repo = _repo(tmp_path / "repo", {BUILTIN: BROKEN[kind]})
        monkeypatch.chdir(repo)
        assert config_mod._load_builtin_theme(BUILTIN, repo) == _builtin_software()

    @pytest.mark.parametrize("kind", KINDS)
    def test_repo_override_warns_once_naming_file(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        kind: str,
        warnings_log: pytest.LogCaptureFixture,
    ) -> None:
        repo = _repo(tmp_path / "repo", {BUILTIN: BROKEN[kind]})
        monkeypatch.chdir(repo)
        config_mod._load_builtin_theme(BUILTIN, repo)
        named = _naming(warnings_log, _theme_file(repo, BUILTIN))
        assert len(named) == 1, _warnings(warnings_log)

    @pytest.mark.parametrize("kind", KINDS)
    def test_cwd_override_falls_through_to_builtin(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        kind: str,
        warnings_log: pytest.LogCaptureFixture,
    ) -> None:
        """The cwd `.kanban/themes` dir (repo_root=None) behaves the same."""
        cwd = _repo(tmp_path / "cwd", {BUILTIN: BROKEN[kind]})
        monkeypatch.chdir(cwd)
        assert config_mod._load_builtin_theme(BUILTIN, None) == _builtin_software()
        assert len(_naming(warnings_log, _theme_file(cwd, BUILTIN))) == 1, _warnings(
            warnings_log
        )

    @pytest.mark.parametrize("kind", KINDS)
    def test_get_theme_is_builtin_and_no_unknown_theme_warning(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        kind: str,
        warnings_log: pytest.LogCaptureFixture,
    ) -> None:
        repo = _repo(
            tmp_path / "repo", {BUILTIN: BROKEN[kind]}, SINGLE_CFG.format(theme=BUILTIN)
        )
        monkeypatch.chdir(repo)
        config = KanbanConfig.load(repo / ".kanban" / "config.yaml")
        assert config.get_theme() == _builtin_software()
        assert not [m for m in _warnings(warnings_log) if "not a known theme" in m], (
            _warnings(warnings_log)
        )


# ---------------------------------------------------------------------------
# 3. A custom name broken everywhere: None, and not offered as "Available"
# ---------------------------------------------------------------------------


class TestBrokenCustomTheme:
    @pytest.mark.parametrize("kind", KINDS)
    def test_load_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
    ) -> None:
        repo = _repo(tmp_path / "repo", {CUSTOM: BROKEN[kind]})
        monkeypatch.chdir(repo)
        assert config_mod._load_builtin_theme(CUSTOM, repo) is None

    @pytest.mark.parametrize("kind", KINDS)
    def test_load_warns_once_naming_file(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        kind: str,
        warnings_log: pytest.LogCaptureFixture,
    ) -> None:
        repo = _repo(tmp_path / "repo", {CUSTOM: BROKEN[kind]})
        monkeypatch.chdir(repo)
        config_mod._load_builtin_theme(CUSTOM, repo)
        assert len(_naming(warnings_log, _theme_file(repo, CUSTOM))) == 1, _warnings(
            warnings_log
        )

    @pytest.mark.parametrize("kind", KINDS)
    def test_unknown_theme_warning_does_not_list_broken_name(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        kind: str,
        warnings_log: pytest.LogCaptureFixture,
    ) -> None:
        repo = _repo(
            tmp_path / "repo", {CUSTOM: BROKEN[kind]}, SINGLE_CFG.format(theme=CUSTOM)
        )
        monkeypatch.chdir(repo)
        KanbanConfig.load(repo / ".kanban" / "config.yaml")
        available = _available_from_warning(_warnings(warnings_log))
        assert CUSTOM not in available, available
        assert BUILTIN_NAMES <= set(available), available


# ---------------------------------------------------------------------------
# 4. _available_themes lists only names that load as a mapping from some dir
# ---------------------------------------------------------------------------


class TestAvailableThemes:
    def test_valid_custom_and_builtins_listed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _repo(tmp_path / "repo", {"acme": "name: Acme\n"})
        monkeypatch.chdir(repo)
        names = config_mod._available_themes(repo)
        assert "acme" in names and BUILTIN_NAMES <= set(names), names

    @pytest.mark.parametrize("kind", KINDS)
    def test_broken_custom_not_listed_valid_one_is(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
    ) -> None:
        repo = _repo(tmp_path / "repo", {CUSTOM: BROKEN[kind], "acme": "name: Acme\n"})
        monkeypatch.chdir(repo)
        names = config_mod._available_themes(repo)
        assert CUSTOM not in names, names
        assert "acme" in names and BUILTIN_NAMES <= set(names), names

    @pytest.mark.parametrize("kind", KINDS)
    def test_builtin_with_broken_override_still_listed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
    ) -> None:
        """`software` still loads (from the package), so it stays available."""
        repo = _repo(tmp_path / "repo", {BUILTIN: BROKEN[kind]})
        monkeypatch.chdir(repo)
        assert BUILTIN in config_mod._available_themes(repo)


# ---------------------------------------------------------------------------
# 5. Warn once: repeated lookups of the same broken file don't re-warn
# ---------------------------------------------------------------------------


class TestWarnOnce:
    @pytest.mark.parametrize("name", [BUILTIN, CUSTOM])
    @pytest.mark.parametrize("kind", KINDS)
    def test_repeated_loader_calls_warn_once(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        kind: str,
        name: str,
        warnings_log: pytest.LogCaptureFixture,
    ) -> None:
        repo = _repo(tmp_path / "repo", {name: BROKEN[kind]})
        monkeypatch.chdir(repo)
        for _ in range(3):
            config_mod._load_builtin_theme(name, repo)
        config_mod._available_themes(repo)
        assert len(_naming(warnings_log, _theme_file(repo, name))) == 1, _warnings(
            warnings_log
        )

    @pytest.mark.parametrize("name", [BUILTIN, CUSTOM])
    @pytest.mark.parametrize("kind", KINDS)
    def test_config_load_then_get_theme_warns_once(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        kind: str,
        name: str,
        warnings_log: pytest.LogCaptureFixture,
    ) -> None:
        """One CLI flow looks the theme up several times (load, available list,
        get_theme); the broken file is still reported once."""
        repo = _repo(tmp_path / "repo", {name: BROKEN[kind]}, SINGLE_CFG.format(theme=name))
        monkeypatch.chdir(repo)
        config = KanbanConfig.load(repo / ".kanban" / "config.yaml")
        config.get_theme()
        config.get_theme()
        assert len(_naming(warnings_log, _theme_file(repo, name))) == 1, _warnings(
            warnings_log
        )


# ---------------------------------------------------------------------------
# 6. End to end: `board` with a broken local software.yaml
# ---------------------------------------------------------------------------


class TestBoardCli:
    @pytest.mark.parametrize("kind", KINDS)
    def test_board_shows_builtin_software(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        kind: str,
        warnings_log: pytest.LogCaptureFixture,
    ) -> None:
        repo = _repo(
            tmp_path / "repo", {BUILTIN: BROKEN[kind]}, SINGLE_CFG.format(theme=BUILTIN)
        )
        result = _invoke(repo, monkeypatch, ["board"])
        _no_crash(result)
        out = result.output
        assert result.exit_code == 0, out
        assert "Software Board" in out, out
        for column in ("Backlog", "Ready", "Review", "Done"):
            assert column in out, out
        # the log goes to caplog under pytest, not to the runner's output
        logged = _warnings(warnings_log)
        assert not [m for m in logged if "not a known theme" in m], logged  # theme loaded
        assert len(_naming(warnings_log, _theme_file(repo, BUILTIN))) == 1, logged

    def test_control_board_valid_override_wins(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _repo(
            tmp_path / "repo", {BUILTIN: VALID_OVERRIDE}, SINGLE_CFG.format(theme=BUILTIN)
        )
        result = _invoke(repo, monkeypatch, ["board"])
        _no_crash(result)
        assert result.exit_code == 0, result.output
        assert "Zeta" in result.output and "Ready" not in result.output, result.output


# ---------------------------------------------------------------------------
# 7. Controls: valid overrides and custom themes keep working
# ---------------------------------------------------------------------------


class TestControls:
    def test_valid_repo_override_wins(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        warnings_log: pytest.LogCaptureFixture,
    ) -> None:
        repo = _repo(tmp_path / "repo", {BUILTIN: VALID_OVERRIDE})
        monkeypatch.chdir(repo)
        assert config_mod._load_builtin_theme(BUILTIN, repo) == yaml.safe_load(VALID_OVERRIDE)
        assert not _warnings(warnings_log), _warnings(warnings_log)

    def test_valid_custom_theme_loads(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        warnings_log: pytest.LogCaptureFixture,
    ) -> None:
        repo = _repo(tmp_path / "repo", {CUSTOM: "name: Acme\n"}, SINGLE_CFG.format(theme=CUSTOM))
        monkeypatch.chdir(repo)
        assert config_mod._load_builtin_theme(CUSTOM, repo) == {"name": "Acme"}
        config = KanbanConfig.load(repo / ".kanban" / "config.yaml")
        assert config.get_theme() == {"name": "Acme"}
        assert not _warnings(warnings_log), _warnings(warnings_log)

    def test_no_override_loads_builtin(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _repo(tmp_path / "repo")
        monkeypatch.chdir(repo)
        assert config_mod._load_builtin_theme(BUILTIN, repo) == _builtin_software()

    def test_unknown_name_still_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _repo(tmp_path / "repo")
        monkeypatch.chdir(repo)
        assert config_mod._load_builtin_theme("nosuch", repo) is None
        assert "nosuch" not in config_mod._available_themes(repo)
