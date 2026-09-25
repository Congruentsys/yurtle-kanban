"""Issue #365 — a theme file with no known sections falls through, like a broken one.

Follow-up from the review of PR #362 (#352). ``_load_builtin_theme`` walks
``_theme_dirs`` (repo ``.kanban/themes``, cwd ``.kanban/themes``, then the package
themes), first match wins. #352 made a broken (unparseable / non-mapping) local file
fall through with one warning, but:

- a ``{}`` theme, or one whose every section was dropped by ``_drop_bad_sections``
  (#351), still counts as *found* and shadows the built-in, while ``cli.py``'s
  ``board-add`` (``if not _load_builtin_theme(...)``) treats ``{}`` as missing;
- an empty file warns "is not a mapping (NoneType)" rather than "is empty";
- a dangling-symlink override is skipped silently (``exists()`` is False).

Decided behaviour ([steer] bucket 2; the known sections are ``theme``,
``item_types``, ``columns``, ``transitions``, ``id_formats``, ``status_mappings``,
``status_aliases``):

1. A repo-local ``software.yaml`` that is ``{}`` or an empty file falls through to
   the built-in with one warning naming the file; for the empty file it says
   "is empty", not "not a mapping (NoneType)".
2. A repo-local ``software.yaml`` whose known sections were all dropped also falls
   through to the built-in, with the warnings.
3. A custom ``mytheme.yaml`` that is ``{}`` or has no known sections: the loader
   returns None, ``_available_themes`` doesn't list it, and ``board-add --preset
   mytheme`` refuses it as an unknown preset.
4. A theme that keeps at least one known section still loads (including a spec-like
   theme with top-level ``name``/``description`` plus ``columns``).
5. A dangling-symlink ``software.yaml`` warns once naming it, then falls through.
6. Warn once per file per process.
7. Controls: the built-ins load with no warnings; a valid override still wins.
"""

from __future__ import annotations

import logging
import os
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
BUILTIN_NAMES = ("hdd", "nautical", "software", "spec")

# Files that parse to a mapping with no content: `{}` and an empty file.
EMPTY = {
    "empty_mapping": "{}\n",
    "empty_file": "",
}

# Files whose known sections are all dropped by _drop_bad_sections (#351).
ALL_DROPPED = {
    "columns_scalar": "columns: 5\n",
    "columns_and_item_types": "columns: 5\nitem_types: [x]\n",
}
DROPPED_SECTIONS = {
    "columns_scalar": ("columns",),
    "columns_and_item_types": ("columns", "item_types"),
}

# Custom themes with no known section at all.
NO_SECTIONS = {
    **EMPTY,
    **ALL_DROPPED,
    "name_only": "name: Acme\n",
    "name_and_description": "name: Acme\ndescription: A theme\n",
}

# Themes that keep at least one known section.
KEEPS_SECTION = {
    "columns_only": "columns:\n  todo:\n    name: Todo\n",
    "spec_like": (
        "name: Acme Spec\ndescription: |\n  A spec-like theme.\n"
        "columns:\n  todo:\n    name: Todo\n"
    ),
    "one_section_survives": "columns: 5\nitem_types:\n  task:\n    id_prefix: T\n",
}

VALID_OVERRIDE = "columns:\n  zeta:\n    name: Zeta\n    order: 1\n"

SINGLE_CFG = "kanban:\n  theme: {theme}\n  paths:\n    root: work/\n"


# --- fixtures / helpers (after tests/issues/test_352_theme_override_fallthrough.py) --


@pytest.fixture(autouse=True)
def _clean_theme_cache() -> Iterator[None]:
    """The cache is keyed by resolved theme file (#287) and doubles as the
    warn-once record (#352); clear it so no test sees another's entries."""
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
    (kanban / "themes").mkdir()
    for name, text in (themes or {}).items():
        (kanban / "themes" / f"{name}.yaml").write_text(text)
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "t@t.com")
    _git(root, "config", "user.name", "T")
    (root / "work").mkdir()
    (root / "work" / ".keep").write_text("")
    return root


def _theme_file(repo: Path, name: str) -> Path:
    return repo / ".kanban" / "themes" / f"{name}.yaml"


def _packaged(name: str) -> dict[str, Any]:
    """A packaged theme, read straight from the source tree."""
    path = Path(config_mod.__file__).resolve().parent.parent.parent / "themes" / f"{name}.yaml"
    data = yaml.safe_load(path.read_text())
    assert isinstance(data, dict), path
    return data


def _builtin_software() -> dict[str, Any]:
    data = _packaged(BUILTIN)
    assert data["theme"]["name"] == "software"
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


def _dangling(repo: Path, name: str) -> Path:
    """``.kanban/themes/<name>.yaml`` as a symlink to a file that doesn't exist."""
    link = _theme_file(repo, name)
    try:
        os.symlink(repo / "no-such-target.yaml", link)
    except (OSError, NotImplementedError) as e:
        pytest.skip(f"symlinks unavailable: {e}")
    assert link.is_symlink() and not link.exists()
    return link


# ---------------------------------------------------------------------------
# 1. An empty override ({} / empty file) falls through, with one warning
# ---------------------------------------------------------------------------


class TestEmptyOverride:
    @pytest.mark.parametrize("kind", list(EMPTY))
    def test_repo_override_falls_through_to_builtin(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
    ) -> None:
        repo = _repo(tmp_path / "repo", {BUILTIN: EMPTY[kind]})
        monkeypatch.chdir(repo)
        assert config_mod._load_builtin_theme(BUILTIN, repo) == _builtin_software()

    @pytest.mark.parametrize("kind", list(EMPTY))
    def test_cwd_override_falls_through_to_builtin(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
    ) -> None:
        """The cwd `.kanban/themes` dir (repo_root=None) behaves the same."""
        cwd = _repo(tmp_path / "cwd", {BUILTIN: EMPTY[kind]})
        monkeypatch.chdir(cwd)
        assert config_mod._load_builtin_theme(BUILTIN, None) == _builtin_software()

    @pytest.mark.parametrize("kind", list(EMPTY))
    def test_warns_once_naming_file(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        kind: str,
        warnings_log: pytest.LogCaptureFixture,
    ) -> None:
        repo = _repo(tmp_path / "repo", {BUILTIN: EMPTY[kind]})
        monkeypatch.chdir(repo)
        config_mod._load_builtin_theme(BUILTIN, repo)
        named = _naming(warnings_log, _theme_file(repo, BUILTIN))
        assert len(named) == 1, _warnings(warnings_log)

    def test_empty_file_warning_says_is_empty(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        warnings_log: pytest.LogCaptureFixture,
    ) -> None:
        repo = _repo(tmp_path / "repo", {BUILTIN: EMPTY["empty_file"]})
        monkeypatch.chdir(repo)
        config_mod._load_builtin_theme(BUILTIN, repo)
        named = _naming(warnings_log, _theme_file(repo, BUILTIN))
        assert len(named) == 1, _warnings(warnings_log)
        assert "is empty" in named[0], named
        assert "NoneType" not in named[0] and "not a mapping" not in named[0], named

    @pytest.mark.parametrize("kind", list(EMPTY))
    def test_get_theme_is_builtin_and_no_unknown_theme_warning(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        kind: str,
        warnings_log: pytest.LogCaptureFixture,
    ) -> None:
        repo = _repo(tmp_path / "repo", {BUILTIN: EMPTY[kind]}, SINGLE_CFG.format(theme=BUILTIN))
        monkeypatch.chdir(repo)
        config = KanbanConfig.load(repo / ".kanban" / "config.yaml")
        assert config.get_theme() == _builtin_software()
        assert not [m for m in _warnings(warnings_log) if "not a known theme" in m], (
            _warnings(warnings_log)
        )

    @pytest.mark.parametrize("kind", list(EMPTY))
    def test_board_shows_builtin_software(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        kind: str,
        warnings_log: pytest.LogCaptureFixture,
    ) -> None:
        repo = _repo(tmp_path / "repo", {BUILTIN: EMPTY[kind]}, SINGLE_CFG.format(theme=BUILTIN))
        result = _invoke(repo, monkeypatch, ["board"])
        _no_crash(result)
        out = result.output
        assert result.exit_code == 0, out
        assert "Software Board" in out, out
        for column in ("Backlog", "Ready", "Review", "Done"):
            assert column in out, out
        logged = _warnings(warnings_log)
        assert len(_naming(warnings_log, _theme_file(repo, BUILTIN))) == 1, logged


# ---------------------------------------------------------------------------
# 2. An override whose every known section was dropped falls through too
# ---------------------------------------------------------------------------


class TestAllSectionsDroppedOverride:
    @pytest.mark.parametrize("kind", list(ALL_DROPPED))
    def test_repo_override_falls_through_to_builtin(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
    ) -> None:
        repo = _repo(tmp_path / "repo", {BUILTIN: ALL_DROPPED[kind]})
        monkeypatch.chdir(repo)
        assert config_mod._load_builtin_theme(BUILTIN, repo) == _builtin_software()

    @pytest.mark.parametrize("kind", list(ALL_DROPPED))
    def test_section_warnings_name_file(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        kind: str,
        warnings_log: pytest.LogCaptureFixture,
    ) -> None:
        repo = _repo(tmp_path / "repo", {BUILTIN: ALL_DROPPED[kind]})
        monkeypatch.chdir(repo)
        config_mod._load_builtin_theme(BUILTIN, repo)
        named = _naming(warnings_log, _theme_file(repo, BUILTIN))
        for section in DROPPED_SECTIONS[kind]:
            assert [m for m in named if f"`{section}`" in m], named

    @pytest.mark.parametrize("kind", list(ALL_DROPPED))
    def test_get_theme_is_builtin(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        kind: str,
        warnings_log: pytest.LogCaptureFixture,
    ) -> None:
        repo = _repo(
            tmp_path / "repo", {BUILTIN: ALL_DROPPED[kind]}, SINGLE_CFG.format(theme=BUILTIN)
        )
        monkeypatch.chdir(repo)
        config = KanbanConfig.load(repo / ".kanban" / "config.yaml")
        assert config.get_theme() == _builtin_software()
        assert not [m for m in _warnings(warnings_log) if "not a known theme" in m], (
            _warnings(warnings_log)
        )

    @pytest.mark.parametrize("kind", list(ALL_DROPPED))
    def test_board_shows_builtin_software(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
    ) -> None:
        repo = _repo(
            tmp_path / "repo", {BUILTIN: ALL_DROPPED[kind]}, SINGLE_CFG.format(theme=BUILTIN)
        )
        result = _invoke(repo, monkeypatch, ["board"])
        _no_crash(result)
        assert result.exit_code == 0, result.output
        assert "Software Board" in result.output, result.output


# ---------------------------------------------------------------------------
# 3. A custom theme with no known sections: None, unlisted, refused by board-add
# ---------------------------------------------------------------------------


class TestCustomWithoutSections:
    @pytest.mark.parametrize("kind", list(NO_SECTIONS))
    def test_load_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
    ) -> None:
        repo = _repo(tmp_path / "repo", {CUSTOM: NO_SECTIONS[kind]})
        monkeypatch.chdir(repo)
        assert config_mod._load_builtin_theme(CUSTOM, repo) is None

    @pytest.mark.parametrize("kind", list(NO_SECTIONS))
    def test_not_in_available_themes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
    ) -> None:
        repo = _repo(tmp_path / "repo", {CUSTOM: NO_SECTIONS[kind]})
        monkeypatch.chdir(repo)
        names = config_mod._available_themes(repo)
        assert CUSTOM not in names, names
        assert set(BUILTIN_NAMES) <= set(names), names

    @pytest.mark.parametrize("kind", list(NO_SECTIONS))
    def test_unknown_theme_warning_does_not_list_it(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        kind: str,
        warnings_log: pytest.LogCaptureFixture,
    ) -> None:
        repo = _repo(tmp_path / "repo", {CUSTOM: NO_SECTIONS[kind]}, SINGLE_CFG.format(theme=CUSTOM))
        monkeypatch.chdir(repo)
        config = KanbanConfig.load(repo / ".kanban" / "config.yaml")
        available = _available_from_warning(_warnings(warnings_log))
        assert CUSTOM not in available, available
        assert config.get_theme() is None

    @pytest.mark.parametrize("kind", list(NO_SECTIONS))
    def test_board_add_refuses_as_unknown_preset(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
    ) -> None:
        repo = _repo(
            tmp_path / "repo", {CUSTOM: NO_SECTIONS[kind]}, SINGLE_CFG.format(theme=BUILTIN)
        )
        cfg_before = (repo / ".kanban" / "config.yaml").read_text()
        result = _invoke(
            repo, monkeypatch, ["board-add", "extra", "--preset", CUSTOM, "--path", "extra/"]
        )
        _no_crash(result)
        assert result.exit_code == 1, result.output
        assert "Unknown preset" in result.output, result.output
        assert (repo / ".kanban" / "config.yaml").read_text() == cfg_before


# ---------------------------------------------------------------------------
# 4. A theme that keeps at least one known section still loads
# ---------------------------------------------------------------------------


class TestKeepsASection:
    @pytest.mark.parametrize("kind", list(KEEPS_SECTION))
    def test_custom_loads(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
    ) -> None:
        repo = _repo(tmp_path / "repo", {CUSTOM: KEEPS_SECTION[kind]})
        monkeypatch.chdir(repo)
        data = config_mod._load_builtin_theme(CUSTOM, repo)
        assert isinstance(data, dict) and data, data
        assert CUSTOM in config_mod._available_themes(repo)

    def test_columns_only_loads_as_is(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        text = KEEPS_SECTION["columns_only"]
        repo = _repo(tmp_path / "repo", {CUSTOM: text})
        monkeypatch.chdir(repo)
        assert config_mod._load_builtin_theme(CUSTOM, repo) == yaml.safe_load(text)

    def test_spec_like_loads_as_is(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        text = KEEPS_SECTION["spec_like"]
        repo = _repo(tmp_path / "repo", {CUSTOM: text})
        monkeypatch.chdir(repo)
        assert config_mod._load_builtin_theme(CUSTOM, repo) == yaml.safe_load(text)

    def test_surviving_section_override_wins(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """One bad section dropped, one good one kept: the override still shadows."""
        repo = _repo(tmp_path / "repo", {BUILTIN: KEEPS_SECTION["one_section_survives"]})
        monkeypatch.chdir(repo)
        assert config_mod._load_builtin_theme(BUILTIN, repo) == {
            "item_types": {"task": {"id_prefix": "T"}}
        }

    @pytest.mark.parametrize("kind", ["columns_only", "spec_like"])
    def test_board_add_accepts_preset(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
    ) -> None:
        repo = _repo(
            tmp_path / "repo", {CUSTOM: KEEPS_SECTION[kind]}, SINGLE_CFG.format(theme=BUILTIN)
        )
        result = _invoke(
            repo, monkeypatch, ["board-add", "extra", "--preset", CUSTOM, "--path", "extra/"]
        )
        _no_crash(result)
        assert "Unknown preset" not in result.output, result.output


# ---------------------------------------------------------------------------
# 5. A dangling-symlink override warns once, then falls through
# ---------------------------------------------------------------------------


class TestDanglingSymlink:
    def test_falls_through_to_builtin(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _repo(tmp_path / "repo")
        _dangling(repo, BUILTIN)
        monkeypatch.chdir(repo)
        assert config_mod._load_builtin_theme(BUILTIN, repo) == _builtin_software()

    def test_warns_once_naming_link(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        warnings_log: pytest.LogCaptureFixture,
    ) -> None:
        repo = _repo(tmp_path / "repo")
        link = _dangling(repo, BUILTIN)
        monkeypatch.chdir(repo)
        config_mod._load_builtin_theme(BUILTIN, repo)
        assert len(_naming(warnings_log, link)) == 1, _warnings(warnings_log)

    def test_warns_once_across_lookups(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        warnings_log: pytest.LogCaptureFixture,
    ) -> None:
        repo = _repo(tmp_path / "repo", config=SINGLE_CFG.format(theme=BUILTIN))
        link = _dangling(repo, BUILTIN)
        monkeypatch.chdir(repo)
        for _ in range(3):
            config_mod._load_builtin_theme(BUILTIN, repo)
        config_mod._available_themes(repo)
        config = KanbanConfig.load(repo / ".kanban" / "config.yaml")
        assert config.get_theme() == _builtin_software()
        assert len(_naming(warnings_log, link)) == 1, _warnings(warnings_log)

    def test_custom_dangling_is_none_and_unlisted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _repo(tmp_path / "repo")
        _dangling(repo, CUSTOM)
        monkeypatch.chdir(repo)
        assert config_mod._load_builtin_theme(CUSTOM, repo) is None
        assert CUSTOM not in config_mod._available_themes(repo)


# ---------------------------------------------------------------------------
# 6. Warn once per file per process
# ---------------------------------------------------------------------------


class TestWarnOnce:
    @pytest.mark.parametrize("name", [BUILTIN, CUSTOM])
    @pytest.mark.parametrize("kind", list(EMPTY))
    def test_empty_repeated_lookups_warn_once(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        kind: str,
        name: str,
        warnings_log: pytest.LogCaptureFixture,
    ) -> None:
        repo = _repo(tmp_path / "repo", {name: EMPTY[kind]}, SINGLE_CFG.format(theme=name))
        monkeypatch.chdir(repo)
        for _ in range(3):
            config_mod._load_builtin_theme(name, repo)
        config_mod._available_themes(repo)
        config = KanbanConfig.load(repo / ".kanban" / "config.yaml")
        config.get_theme()
        config.get_theme()
        assert len(_naming(warnings_log, _theme_file(repo, name))) == 1, _warnings(
            warnings_log
        )

    @pytest.mark.parametrize("name", [BUILTIN, CUSTOM])
    @pytest.mark.parametrize("kind", list(ALL_DROPPED))
    def test_dropped_repeated_lookups_do_not_rewarn(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        kind: str,
        name: str,
        warnings_log: pytest.LogCaptureFixture,
    ) -> None:
        """Whatever the first lookup says about the file, later lookups say nothing."""
        repo = _repo(tmp_path / "repo", {name: ALL_DROPPED[kind]}, SINGLE_CFG.format(theme=name))
        monkeypatch.chdir(repo)
        config_mod._load_builtin_theme(name, repo)
        first = len(_naming(warnings_log, _theme_file(repo, name)))
        assert first >= 1, _warnings(warnings_log)
        for _ in range(3):
            config_mod._load_builtin_theme(name, repo)
        config_mod._available_themes(repo)
        config = KanbanConfig.load(repo / ".kanban" / "config.yaml")
        config.get_theme()
        assert len(_naming(warnings_log, _theme_file(repo, name))) == first, _warnings(
            warnings_log
        )


# ---------------------------------------------------------------------------
# 7. Controls
# ---------------------------------------------------------------------------


class TestControls:
    @pytest.mark.parametrize("name", BUILTIN_NAMES)
    def test_builtins_load_without_warnings(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        name: str,
        warnings_log: pytest.LogCaptureFixture,
    ) -> None:
        repo = _repo(tmp_path / "repo")
        monkeypatch.chdir(repo)
        assert config_mod._load_builtin_theme(name, repo) == _packaged(name)
        assert name in config_mod._available_themes(repo)
        assert not _warnings(warnings_log), _warnings(warnings_log)

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

    def test_board_valid_override_wins(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo = _repo(tmp_path / "repo", {BUILTIN: VALID_OVERRIDE}, SINGLE_CFG.format(theme=BUILTIN))
        result = _invoke(repo, monkeypatch, ["board"])
        _no_crash(result)
        assert result.exit_code == 0, result.output
        assert "Zeta" in result.output and "Ready" not in result.output, result.output
