# ruff: noqa: F811  -- pytest fixtures imported from the #439 test module are re-bound as args
"""Issue #588 — the `spec` theme declared its native status names under
`status_aliases`, a key nothing reads, so a spec board wrote canonical names
(`status: done`) though its columns are Draft / Proposed / Implementing / Accepted.

Decided ([steer] on #588): rename the key to `status_mappings` (native -> canonical,
the shape hdd uses). Then on a single spec board:

1. `create` writes the native initial status (`status: draft`).
2. `move ITEM <native>` writes the native name to the file; `show` and the `move`
   confirmation print it; `show --json` stays canonical; `board` counts the item
   under the native column.
3. `move ITEM <canonical>` also writes the native name.
4. A file with a native spec status reads as the right canonical status.
5. Guard: every top-level key of every built-in theme is one the code reads, so a
   theme can't silently carry an ignored section again (no `status_aliases`).
   #611: the guard's key set is `config._THEME_SECTIONS` itself, not a hand-kept
   copy; `_THEME_SECTIONS` drops the dead `status_aliases`, and hdd drops its unread
   `id_formats` (hdd ids come from the item types' prefixes).
"""

from __future__ import annotations

import io
import re
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from tests.issues.test_439_list_theme_status import (  # noqa: F401  (fixtures)
    THEMES_DIR,
    _board_counts,
    _created_id,
    _file_status,
    _invoke,
    _item_file,
    _json,
    _text,
    repo,
    runner,
    wide,
)
from yurtle_kanban.config import _THEME_SECTIONS

MOVE = ["--force", "--skip-gates", "--no-commit"]

# native -> canonical, as the spec theme's columns name them
SPEC_NATIVE: dict[str, str] = {
    "draft": "backlog",
    "proposed": "ready",
    "implementing": "in_progress",
    "accepted": "done",
}
# spec columns whose id already is the canonical name
SPEC_SAME = {"review": "review", "blocked": "blocked"}
SPEC_ALL = {**SPEC_NATIVE, **SPEC_SAME}
CANONICAL_TO_NATIVE = {canonical: native for native, canonical in SPEC_ALL.items()}
COLUMN_TITLE = {
    "draft": "Draft",
    "proposed": "Proposed",
    "implementing": "Implementing",
    "review": "Review",
    "accepted": "Accepted",
    "blocked": "Blocked",
}

# the top-level theme sections the code knows: the loader's own list (#611)
CONSUMED_KEYS = frozenset(_THEME_SECTIONS)


def _spec_theme() -> dict:
    return yaml.safe_load((THEMES_DIR / "spec.yaml").read_text())


def _spec_item(runner: CliRunner, wide: io.StringIO) -> str:
    _invoke(runner, ["init", "--theme", "spec"])
    return _created_id(runner, wide, ["create", "issue", "a spec issue"])


def _show_status(runner: CliRunner, wide: io.StringIO, item_id: str) -> str:
    text = _text(runner, wide, ["show", item_id])
    match = re.search(r"^\s*Status\s+(\S+)\s*$", text, re.MULTILINE)
    assert match, text
    return match.group(1)


def _json_status(runner: CliRunner, item_id: str) -> str:
    data = _json(runner, ["show", item_id, "--json"])
    assert isinstance(data, dict), data
    return str(data["status"])


def _set_file_status(repo: Path, item_id: str, status: str) -> None:
    path = _item_file(repo, item_id)
    path.write_text(re.sub(r"(?m)^status: .*$", f"status: {status}", path.read_text(), count=1))


# ---------------------------------------------------------------------------
# 0. The theme file itself
# ---------------------------------------------------------------------------


def test_spec_theme_declares_status_mappings_native_to_canonical() -> None:
    theme = _spec_theme()
    assert "status_aliases" not in theme
    assert theme.get("status_mappings") == SPEC_NATIVE


def test_spec_status_mappings_name_real_columns() -> None:
    theme = _spec_theme()
    columns = set(theme["columns"])
    assert set(theme.get("status_mappings") or {}) <= columns
    assert set(SPEC_ALL) == columns  # non-vacuity: the table above covers every column


# ---------------------------------------------------------------------------
# 1. create writes the native initial status
# ---------------------------------------------------------------------------


def test_create_writes_native_initial_status(
    repo: Path, runner: CliRunner, wide: io.StringIO
) -> None:
    item_id = _spec_item(runner, wide)
    assert _file_status(repo, item_id) == "draft"
    assert _json_status(runner, item_id) == "backlog"
    assert _show_status(runner, wide, item_id) == "draft"


# ---------------------------------------------------------------------------
# 2. move to a native name
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("native", list(SPEC_ALL))
def test_move_to_native_name_writes_native(
    repo: Path, runner: CliRunner, wide: io.StringIO, native: str
) -> None:
    item_id = _spec_item(runner, wide)
    if native == "draft":  # already there: move away first so the move is real
        _invoke(runner, ["move", item_id, "proposed", *MOVE])
    text = _text(runner, wide, ["move", item_id, native, *MOVE])
    assert f"Moved {item_id} to {native}" in text, text
    assert _file_status(repo, item_id) == native
    assert _show_status(runner, wide, item_id) == native
    assert _json_status(runner, item_id) == SPEC_ALL[native]
    assert _board_counts(runner, wide, [])[COLUMN_TITLE[native]] == 1


def test_move_through_every_native_name_in_order(
    repo: Path, runner: CliRunner, wide: io.StringIO
) -> None:
    item_id = _spec_item(runner, wide)
    for native in ["proposed", "implementing", "review", "accepted", "blocked", "draft"]:
        _invoke(runner, ["move", item_id, native, *MOVE])
        assert _file_status(repo, item_id) == native, native


# ---------------------------------------------------------------------------
# 3. move to a canonical name writes the native one
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("canonical", list(CANONICAL_TO_NATIVE))
def test_move_to_canonical_name_writes_native(
    repo: Path, runner: CliRunner, wide: io.StringIO, canonical: str
) -> None:
    item_id = _spec_item(runner, wide)
    if canonical == "backlog":
        _invoke(runner, ["move", item_id, "ready", *MOVE])
    native = CANONICAL_TO_NATIVE[canonical]
    text = _text(runner, wide, ["move", item_id, canonical, *MOVE])
    assert f"Moved {item_id} to {native}" in text, text
    assert _file_status(repo, item_id) == native
    assert _json_status(runner, item_id) == canonical


# ---------------------------------------------------------------------------
# 4. a native status in the file reads as the right canonical status
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("native", list(SPEC_ALL))
def test_native_status_in_file_parses_to_canonical(
    repo: Path, runner: CliRunner, wide: io.StringIO, native: str
) -> None:
    item_id = _spec_item(runner, wide)
    _set_file_status(repo, item_id, native)
    canonical = SPEC_ALL[native]
    assert _json_status(runner, item_id) == canonical
    assert _show_status(runner, wide, item_id) == native
    listed = _json(runner, ["list", "--status", canonical, "--json"])
    assert isinstance(listed, list)
    assert [row["id"] for row in listed] == [item_id]
    assert _board_counts(runner, wide, [])[COLUMN_TITLE[native]] == 1


# ---------------------------------------------------------------------------
# 5. guard: no built-in theme carries a section nothing reads
# ---------------------------------------------------------------------------

BUILTIN_THEMES = sorted(p.stem for p in THEMES_DIR.glob("*.yaml"))


def test_builtin_theme_list_non_vacuous() -> None:
    assert {"hdd", "nautical", "software", "spec"} <= set(BUILTIN_THEMES)


@pytest.mark.parametrize("name", BUILTIN_THEMES)
def test_no_builtin_theme_uses_status_aliases(name: str) -> None:
    data = yaml.safe_load((THEMES_DIR / f"{name}.yaml").read_text())
    assert "status_aliases" not in data


@pytest.mark.parametrize("name", BUILTIN_THEMES)
def test_every_builtin_theme_key_is_consumed(name: str) -> None:
    data = yaml.safe_load((THEMES_DIR / f"{name}.yaml").read_text())
    unread = set(data) - CONSUMED_KEYS
    assert not unread, f"{name}.yaml has top-level keys nothing reads: {sorted(unread)}"


# ---------------------------------------------------------------------------
# #611: one list, no dead sections
# ---------------------------------------------------------------------------


def test_theme_sections_non_vacuous() -> None:
    assert {"theme", "item_types", "columns", "status_mappings"} <= CONSUMED_KEYS


def test_theme_sections_drop_status_aliases() -> None:
    assert "status_aliases" not in _THEME_SECTIONS


@pytest.mark.parametrize("name", BUILTIN_THEMES)
def test_no_builtin_theme_declares_id_formats(name: str) -> None:
    data = yaml.safe_load((THEMES_DIR / f"{name}.yaml").read_text())
    assert "id_formats" not in data, f"{name}.yaml declares `id_formats`, which nothing reads"
