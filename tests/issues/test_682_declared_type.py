"""Issue #682 — an item keeps its declared theme type.

Captain's ruling (2026-09-29, option B): `WorkItem.declared_type: str` holds the
frontmatter `type` as written. `item_type` stays the canonical enum used for
workflows and prefixes. `show` and `list` display `declared_type`; `--json` and MCP
gain a `declared_type` key next to the canonical type key (`item_type`), which keeps
today's value.

Today `_map_theme_type` maps every non-nautical theme type to `task`, so a `type:
spec` item (spec theme) or `type: story` item (a custom theme) shows as `task`.
Controls: a nautical `expedition` and CLI-created items, whose declared type is the
canonical type.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import CliRunner, Result

from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import main
from yurtle_kanban.mcp.server import KanbanMCPServer

STORIES_THEME: dict[str, Any] = {
    "name": "Stories",
    "description": "A custom theme whose type is not a built-in type",
    "item_types": {"story": {"id_prefix": "STORY", "name": "Story"}},
    "columns": {
        "backlog": {"name": "Backlog", "order": 1},
        "doing": {"name": "Doing", "order": 2},
        "done": {"name": "Done", "order": 3},
    },
}

# (theme, declared type, canonical item_type today, status written)
THEME_ONLY = [
    ("spec", "spec", "task", "draft"),
    ("stories", "story", "task", "backlog"),
]
CONTROL = ("nautical", "expedition", "expedition", "backlog")
ALL_CASES = [*THEME_ONLY, CONTROL]
CASE_IDS = [c[1] for c in ALL_CASES]

ITEM_ID = "DOC-001"


@pytest.fixture(autouse=True)
def _clean_theme_cache() -> Iterator[None]:
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


def _repo(
    root: Path, monkeypatch: pytest.MonkeyPatch, theme: str,
    item_type: str | None = None, status: str = "backlog",
) -> Path:
    """A single-board repo on `theme`; with `item_type`, one hand-written item."""
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    kanban = root / ".kanban"
    kanban.mkdir()
    (kanban / "config.yaml").write_text(
        yaml.safe_dump({"kanban": {"theme": theme, "paths": {"root": "work/"}}})
    )
    if theme == "stories":
        (kanban / "themes").mkdir()
        (kanban / "themes" / "stories.yaml").write_text(yaml.safe_dump(STORIES_THEME))
    (root / "work").mkdir()
    if item_type is not None:
        (root / "work" / f"{ITEM_ID}-x.md").write_text(
            f"---\nid: {ITEM_ID}\ntitle: Hand written item\ntype: {item_type}\n"
            f"status: {status}\n---\n\n# Hand written item\n"
        )
    monkeypatch.chdir(root)
    return root


def _cli(*args: str) -> Result:
    config_mod._theme_cache.clear()
    result = CliRunner().invoke(main, list(args))
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        raise AssertionError(f"`{' '.join(args)}` crashed: {result.exception!r}")
    assert result.exit_code == 0, result.output
    return result


def _json(*args: str) -> Any:
    return json.loads(_cli(*args).output)


def _one(items: list[dict[str, Any]], item_id: str) -> dict[str, Any]:
    (found,) = [i for i in items if i["id"] == item_id]
    return found


# --- show / list, as text ------------------------------------------------------------


@pytest.mark.parametrize("theme,declared,canonical,status", ALL_CASES, ids=CASE_IDS)
def test_show_displays_declared_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    theme: str, declared: str, canonical: str, status: str,
) -> None:
    _repo(tmp_path, monkeypatch, theme, declared, status)
    out = _cli("show", ITEM_ID).output
    assert re.search(rf"^\s*Type\s+{declared}\s*$", out, re.M), (
        f"`show` should display the declared type `{declared}`:\n{out}"
    )
    if canonical != declared:
        assert not re.search(rf"^\s*Type\s+{canonical}\s*$", out, re.M), out


@pytest.mark.parametrize("theme,declared,canonical,status", ALL_CASES, ids=CASE_IDS)
def test_list_displays_declared_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    theme: str, declared: str, canonical: str, status: str,
) -> None:
    _repo(tmp_path, monkeypatch, theme, declared, status)
    out = _cli("list").output
    (row,) = [ln for ln in out.splitlines() if ITEM_ID in ln]
    assert re.search(rf"\b{declared}\b", row), (
        f"`list` should display the declared type `{declared}` in the item's row:\n{out}"
    )
    if canonical != declared:
        assert not re.search(rf"\b{canonical}\b", row), row


# --- --json and MCP ------------------------------------------------------------------


@pytest.mark.parametrize("theme,declared,canonical,status", ALL_CASES, ids=CASE_IDS)
def test_show_json_has_declared_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    theme: str, declared: str, canonical: str, status: str,
) -> None:
    _repo(tmp_path, monkeypatch, theme, declared, status)
    data = _json("show", ITEM_ID, "--json")
    assert data.get("declared_type") == declared, data
    assert data["item_type"] == canonical, data  # unchanged


@pytest.mark.parametrize("theme,declared,canonical,status", ALL_CASES, ids=CASE_IDS)
def test_list_json_has_declared_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    theme: str, declared: str, canonical: str, status: str,
) -> None:
    _repo(tmp_path, monkeypatch, theme, declared, status)
    data = _one(_json("list", "--json"), ITEM_ID)
    assert data.get("declared_type") == declared, data
    assert data["item_type"] == canonical, data


def test_json_declared_type_sits_next_to_item_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _repo(tmp_path, monkeypatch, "spec", "spec", "draft")
    keys = list(_json("show", ITEM_ID, "--json"))
    assert "declared_type" in keys, keys
    assert keys.index("declared_type") == keys.index("item_type") + 1, keys


@pytest.mark.parametrize("theme,declared,canonical,status", ALL_CASES, ids=CASE_IDS)
def test_mcp_get_and_list_have_declared_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    theme: str, declared: str, canonical: str, status: str,
) -> None:
    root = _repo(tmp_path, monkeypatch, theme, declared, status)
    server = KanbanMCPServer(repo_root=root)
    got = server._get_item({"item_id": ITEM_ID})
    assert got["item"].get("declared_type") == declared, got
    assert got["item"]["item_type"] == canonical, got
    listed = _one(server._list_items({})["items"], ITEM_ID)
    assert listed.get("declared_type") == declared, listed
    assert listed["item_type"] == canonical, listed


# --- CLI-created items: the declared type is the type written ------------------------


@pytest.mark.parametrize(
    "theme,item_type", [("software", "feature"), ("nautical", "expedition")]
)
def test_cli_created_item_declared_type_is_type_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, theme: str, item_type: str
) -> None:
    _repo(tmp_path, monkeypatch, theme)
    _cli("create", item_type, "Made by the CLI")
    (made,) = _json("list", "--json")
    assert made.get("declared_type") == item_type, made
    assert made["item_type"] == item_type, made
    shown = _json("show", made["id"], "--json")
    assert shown.get("declared_type") == item_type, shown


# --- round trip: updating the item keeps `type:` as written ---------------------------


@pytest.mark.parametrize("theme,declared,canonical,status", THEME_ONLY, ids=[c[1] for c in THEME_ONLY])
def test_update_keeps_declared_type_on_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    theme: str, declared: str, canonical: str, status: str,
) -> None:
    root = _repo(tmp_path, monkeypatch, theme, declared, status)
    _cli("update", ITEM_ID, "--priority", "high", "--no-commit")
    text = (root / "work" / f"{ITEM_ID}-x.md").read_text()
    assert re.search(rf"^type: {declared}$", text, re.M), text
    assert "priority: high" in text, text
    data = _json("show", ITEM_ID, "--json")
    assert data.get("declared_type") == declared, data
    assert data["item_type"] == canonical, data
