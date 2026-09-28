"""#816: a theme's `item_types.<type>.id_prefix` is held to #802's prefix grammar.

Found by PR #815's reviewer in a throwaway repo: `config._drop_bad_sections` checks
`id_prefix` only for being text (#378), so

- `id_prefix: "../x y"` makes `create bug` write the item file OUTSIDE its type folder;
- `id_prefix: "a\\u0000b"` makes `create feature` crash with a traceback;

while `next-id` refuses both (#802's `KanbanService._check_prefix` / `_PREFIX_RE`).

Decided ([steer] on #816, bucket 1 — config's existing rule for bad fields): when a
theme loads, an `id_prefix` that fails the #802 grammar is dropped with a warning
naming the theme, the type and the bad value (repr); the type falls back to its
default prefix, the one used when no `id_prefix` is set (`_get_type_prefix`:
`type[:4].upper()` for a type the theme defines). So `create` writes inside the type
folder with the default prefix, exits 0, never crashes.

Controls: valid prefixes (`BUG`, `IDEA-R`, `ÉXP`) work unchanged and load with no
warning; `next-id` behaves as before (refuses a bad prefix, allocates a good one).
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

from tests.issues._snapshot import paths_outside_git
from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import main

LOGGER = "yurtle-kanban"
THEME = "mytheme"
THEME_FILE = f"{THEME}.yaml"

TYPE_PATHS = {"bug": "work/bugs/", "feature": "work/features/"}
# the prefix a type falls back to when the theme defines it with no `id_prefix`
DEFAULT_PREFIX = {"bug": "BUG", "feature": "FEAT"}

BAD_PREFIXES = [
    pytest.param("../x y", id="path-escape"),
    pytest.param("a\x00b", id="nul"),
    pytest.param("-X", id="leading-dash"),
    pytest.param("X-", id="trailing-dash"),
    pytest.param("a b", id="space"),
]
GOOD_PREFIXES = [
    pytest.param("BUG", id="BUG"),
    pytest.param("IDEA-R", id="IDEA-R"),
    pytest.param("ÉXP", id="EXP-accented"),
]
TYPES = ["bug", "feature"]

SINGLE_CFG = f"kanban:\n  theme: {THEME}\n  paths:\n    root: work/\n"
MULTI_CFG = f'version: "2.0"\nboards:\n  - name: devboard\n    preset: {THEME}\n    path: work/\n'
CONFIGS = [pytest.param(SINGLE_CFG, id="single"), pytest.param(MULTI_CFG, id="multi")]


# --- fixtures / helpers ------------------------------------------------------------


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


def _prefix_warnings(caplog: pytest.LogCaptureFixture, bad: str) -> list[str]:
    """Warnings about the bad `id_prefix`: they carry its repr."""
    return [m for m in _warnings(caplog) if repr(bad) in m]


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout


def _theme(item_type: str, prefix: str) -> str:
    """A custom theme defining bug + feature; `item_type` gets `id_prefix: prefix`."""
    types: dict[str, Any] = {t: {"path": p} for t, p in TYPE_PATHS.items()}
    types[item_type]["id_prefix"] = prefix
    data = {
        "theme": {"name": "acme", "description": "custom"},
        "item_types": types,
        "columns": {
            "backlog": {"name": "Backlog", "order": 1},
            "in_progress": {"name": "In Progress", "order": 2},
            "done": {"name": "Done", "order": 3},
        },
    }
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def _repo(tmp_path: Path, config: str, theme: str) -> Path:
    """A committed git repo at ``tmp_path/repo`` (so a file escaping it lands, visibly,
    elsewhere under ``tmp_path``)."""
    root = tmp_path / "repo"
    (root / ".kanban" / "themes").mkdir(parents=True)
    (root / ".kanban" / "config.yaml").write_text(config)
    (root / ".kanban" / "themes" / THEME_FILE).write_text(theme, encoding="utf-8")
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "t@t.com")
    _git(root, "config", "user.name", "T")
    for folder in TYPE_PATHS.values():
        (root / folder).mkdir(parents=True, exist_ok=True)
        (root / folder / ".keep").write_text("")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "seed")
    return root


def _snapshot(tmp_path: Path) -> set[Path]:
    return {
        p
        for p in paths_outside_git(tmp_path)
        if p.is_file()
    }


def _invoke(repo: Path, monkeypatch: pytest.MonkeyPatch, args: list[str]):
    monkeypatch.chdir(repo)
    config_mod._theme_cache.clear()
    return CliRunner().invoke(main, args)


def _no_crash(result) -> None:
    out = result.output or ""
    assert "Traceback" not in out, out
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        raise AssertionError(repr(result.exception)) from result.exception


def _create(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, repo: Path, item_type: str
) -> list[Path]:
    """`create <type> Hi` exits 0 without a traceback; returns the new .md files."""
    before = _snapshot(tmp_path)
    result = _invoke(repo, monkeypatch, ["create", item_type, "Hi"])
    _no_crash(result)
    assert result.exit_code == 0, f"exit {result.exit_code}: {result.output}"
    return sorted(p for p in _snapshot(tmp_path) - before if p.suffix == ".md")


def _assert_created_as(repo: Path, new: list[Path], item_type: str, prefix: str) -> None:
    folder = (repo / TYPE_PATHS[item_type]).resolve()
    assert len(new) == 1, f"expected one new item file, got {new}"
    path = new[0].resolve()
    assert path.parent == folder, f"{path} is not in the type folder {folder}"
    assert path.name.upper().startswith(f"{prefix.upper()}-"), (
        f"{path.name} doesn't use the prefix {prefix!r}"
    )


# ---------------------------------------------------------------------------
# a malformed `id_prefix` — RED before the fix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("item_type", TYPES)
@pytest.mark.parametrize("bad", BAD_PREFIXES)
def test_loading_drops_malformed_prefix_with_one_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, warnings_log, bad: str, item_type: str
) -> None:
    repo = _repo(tmp_path, SINGLE_CFG, _theme(item_type, bad))
    monkeypatch.chdir(repo)
    theme = config_mod._load_builtin_theme(THEME, repo)
    assert theme is not None
    type_def = theme["item_types"][item_type]
    assert "id_prefix" not in type_def, f"kept the malformed prefix {type_def['id_prefix']!r}"
    # the rest of the entry survives, as does the sibling type
    assert type_def.get("path") == TYPE_PATHS[item_type]
    other = next(t for t in TYPES if t != item_type)
    assert theme["item_types"][other] == {"path": TYPE_PATHS[other]}

    hits = _prefix_warnings(warnings_log, bad)
    assert len(hits) == 1, f"expected one warning carrying {bad!r}: {_warnings(warnings_log)}"
    msg = hits[0]
    assert THEME in msg, f"warning doesn't name the theme: {msg!r}"
    assert item_type in msg, f"warning doesn't name the type: {msg!r}"
    assert "id_prefix" in msg, f"warning doesn't name the field: {msg!r}"


@pytest.mark.parametrize("config", CONFIGS)
@pytest.mark.parametrize("item_type", TYPES)
@pytest.mark.parametrize("bad", BAD_PREFIXES)
def test_create_falls_back_to_default_prefix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    warnings_log,
    bad: str,
    item_type: str,
    config: str,
) -> None:
    repo = _repo(tmp_path, config, _theme(item_type, bad))
    new = _create(tmp_path, monkeypatch, repo, item_type)
    _assert_created_as(repo, new, item_type, DEFAULT_PREFIX[item_type])
    assert _prefix_warnings(warnings_log, bad), (
        f"no warning carrying {bad!r}: {_warnings(warnings_log)}"
    )


def test_path_escape_prefix_writes_nothing_outside_the_type_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, warnings_log
) -> None:
    """The reviewer's repro: `id_prefix: "../x y"` + `create bug` wrote the item one
    folder up (`work/x y-001-….md`). No new .md may land anywhere but `work/bugs/`,
    in the repo or outside it."""
    repo = _repo(tmp_path, SINGLE_CFG, _theme("bug", "../x y"))
    before = _snapshot(tmp_path)
    result = _invoke(repo, monkeypatch, ["create", "bug", "Hi"])
    _no_crash(result)
    folder = (repo / TYPE_PATHS["bug"]).resolve()
    stray = [
        p
        for p in _snapshot(tmp_path) - before
        if p.suffix == ".md" and p.resolve().parent != folder
    ]
    assert not stray, f"written outside {folder}: {stray}"
    assert result.exit_code == 0, result.output


# ---------------------------------------------------------------------------
# controls — green before and after
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("config", CONFIGS)
@pytest.mark.parametrize("item_type", TYPES)
@pytest.mark.parametrize("good", GOOD_PREFIXES)
def test_valid_prefix_kept_without_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    warnings_log,
    good: str,
    item_type: str,
    config: str,
) -> None:
    repo = _repo(tmp_path, config, _theme(item_type, good))
    monkeypatch.chdir(repo)
    theme = config_mod._load_builtin_theme(THEME, repo)
    assert theme is not None and theme["item_types"][item_type]["id_prefix"] == good
    new = _create(tmp_path, monkeypatch, repo, item_type)
    _assert_created_as(repo, new, item_type, good)
    assert not _warnings(warnings_log), f"a valid theme warned: {_warnings(warnings_log)}"


@pytest.mark.parametrize("item_type", TYPES)
def test_no_id_prefix_uses_default_without_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, warnings_log, item_type: str
) -> None:
    """The fallback the fix must reach: the same theme with `id_prefix` absent."""
    theme = yaml.safe_load(_theme(item_type, "X"))
    del theme["item_types"][item_type]["id_prefix"]
    repo = _repo(tmp_path, SINGLE_CFG, yaml.safe_dump(theme, sort_keys=False))
    new = _create(tmp_path, monkeypatch, repo, item_type)
    _assert_created_as(repo, new, item_type, DEFAULT_PREFIX[item_type])
    assert not _warnings(warnings_log), _warnings(warnings_log)


@pytest.mark.parametrize("bad", BAD_PREFIXES)
def test_next_id_still_refuses_malformed_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    repo = _repo(tmp_path, SINGLE_CFG, _theme("bug", "BUG"))
    head = _git(repo, "rev-parse", "HEAD")
    result = _invoke(repo, monkeypatch, ["next-id", "--no-sync", "--", bad])
    _no_crash(result)
    assert result.exit_code == 1, result.output
    assert "prefix" in result.output.lower(), result.output
    assert _git(repo, "rev-parse", "HEAD") == head
    assert not (repo / ".kanban" / "_ID_ALLOCATIONS.json").exists()


@pytest.mark.parametrize("theme_prefix", [*GOOD_PREFIXES, *BAD_PREFIXES])
@pytest.mark.parametrize("good", GOOD_PREFIXES)
def test_next_id_allocates_valid_prefix_whatever_the_theme(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, good: str, theme_prefix: str
) -> None:
    repo = _repo(tmp_path, SINGLE_CFG, _theme("bug", theme_prefix))
    result = _invoke(repo, monkeypatch, ["next-id", "--no-sync", good])
    _no_crash(result)
    assert result.exit_code == 0, result.output
    assert f"{good.upper()}-" in result.output.upper(), result.output
