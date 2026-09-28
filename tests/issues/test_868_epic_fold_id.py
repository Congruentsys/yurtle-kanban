"""#868: epic lookups and membership, and the CLI/MCP item lookups, use `fold_id`.

`epic_commands` still looks items up with `service._items.get(...)` (exact key) and
tests membership with `epic_id in item.related` (exact string). It is the one
identity path left that doesn't fold (#817). The CLI and MCP `item_id.upper()`
calls before a lookup are redundant (`get_item` folds), and for the six Greek
letters (#817 round 3) a bare `.upper()` is not NFC, so their messages show a
non-NFC id.

Decided ([steer] bucket-1 on #868):
1. `epic` / `voyage` `show` and `add` (and `create --items`) find the epic and the
   items through the folded lookup, and membership folds both sides:
   `fold_id(epic_id) in {fold_id(r) for r in item.related}`.
2. The CLI and MCP `item_id.upper()` become `fold_id(item_id)`: a lookup by any
   spelling finds the NFC id the allocator issues, and a not-found message shows
   the NFC spelling.

Controls: exact-case ids still work everywhere.
"""

from __future__ import annotations

import subprocess
import unicodedata
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from yurtle_kanban.cli import main
from yurtle_kanban.config import KanbanConfig, PathConfig

# `ΐ` (U+0390) upper-cases to a decomposed `Ι U+0308 U+0301`: not NFC (#817 round 3)
LOWER = "ΐX"
NFC_UPPER = unicodedata.normalize("NFC", unicodedata.normalize("NFC", LOWER).upper())
RAW_UPPER = LOWER.upper()
NFD_LOWER = unicodedata.normalize("NFD", LOWER)


def test_fixture_greek_prefix_is_the_817_case() -> None:
    assert RAW_UPPER != NFC_UPPER
    assert unicodedata.normalize("NFC", RAW_UPPER) == NFC_UPPER
    assert NFD_LOWER != LOWER


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A software-theme git repo (epics), cwd set to it."""
    from yurtle_kanban import config as config_mod

    for args in (
        ["init", "-b", "main"],
        ["config", "user.email", "test@test.com"],
        ["config", "user.name", "Test"],
    ):
        subprocess.run(["git", *args], cwd=tmp_path, capture_output=True, check=True)
    (tmp_path / ".kanban").mkdir()
    for folder in ("features", "epics", "bugs"):
        (tmp_path / "work" / folder).mkdir(parents=True)
    KanbanConfig(
        theme="software",
        paths=PathConfig(root="work/", scan_paths=["work/features/", "work/epics/", "work/bugs/"]),
    ).save(tmp_path / ".kanban" / "config.yaml")
    config_mod._theme_cache.clear()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("YURTLE_AGENT", "tester")
    return tmp_path


def _write(
    root: Path,
    item_id: str,
    title: str,
    *,
    kind: str = "feature",
    related: list[str] | None = None,
    status: str = "backlog",
) -> Path:
    folder = root / "work" / ("epics" if kind == "epic" else "features")
    path = folder / f"{title}.md"
    rel = "[" + ", ".join(related or []) + "]"
    path.write_text(
        f'---\nid: {item_id}\ntitle: "{title}"\ntype: {kind}\nstatus: {status}\n'
        f"priority: medium\nrelated: {rel}\n---\n\n# {title}\n",
        encoding="utf-8",
    )
    return path


def _commit(root: Path) -> None:
    subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "items"], cwd=root, capture_output=True, check=True
    )


def _invoke(args: list[str]):
    return CliRunner().invoke(main, args)


def _related(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    fm = yaml.safe_load(text.split("---\n")[1])
    return [str(r) for r in (fm.get("related") or [])]


def _fold(s: str) -> str:
    return unicodedata.normalize("NFC", unicodedata.normalize("NFC", s).upper())


# ---------------------------------------------------------------------------
# 1. epic / voyage show: the epic by any case, members by folded `related`
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("group", ["epic", "voyage"])
def test_epic_show_finds_the_epic_by_lower_case(root: Path, group: str) -> None:
    _write(root, "EPIC-001", "the-epic", kind="epic")
    _write(root, "FEAT-001", "member", related=["EPIC-001"], status="done")
    result = _invoke([group, "show", "epic-001"])
    assert result.exit_code == 0, (result.output, repr(result.exception))
    assert "EPIC-001" in result.output
    assert "member" in result.output
    assert "Progress: 1/1" in result.output


def test_epic_show_counts_a_lower_case_related_as_a_member(root: Path) -> None:
    _write(root, "EPIC-001", "the-epic", kind="epic")
    _write(root, "FEAT-001", "member", related=["epic-001"], status="done")
    _write(root, "FEAT-002", "outsider")
    result = _invoke(["epic", "show", "EPIC-001"])
    assert result.exit_code == 0, (result.output, repr(result.exception))
    assert "member" in result.output, result.output
    assert "outsider" not in result.output
    assert "Progress: 1/1" in result.output, result.output


def test_epic_show_lists_the_epics_own_lower_case_related(root: Path) -> None:
    """Bidirectional: the epic's own `related:` names `feat-001`."""
    _write(root, "EPIC-001", "the-epic", kind="epic", related=["feat-001"])
    _write(root, "FEAT-001", "member")
    result = _invoke(["epic", "show", "EPIC-001"])
    assert result.exit_code == 0, (result.output, repr(result.exception))
    assert "member" in result.output, result.output
    assert "Progress: 0/1" in result.output, result.output


def test_epic_show_does_not_list_a_folded_member_twice(root: Path) -> None:
    """A member named both ways (the item's `related: [epic-001]` and the epic's
    `related: [feat-001]`) is one row: 1/1, not 2/2."""
    _write(root, "EPIC-001", "the-epic", kind="epic", related=["feat-001"])
    _write(root, "FEAT-001", "member", related=["epic-001"], status="done")
    result = _invoke(["epic", "show", "EPIC-001"])
    assert result.exit_code == 0, (result.output, repr(result.exception))
    assert "Progress: 1/1" in result.output, result.output


def test_epic_show_counts_an_nfd_related_for_the_nfc_epic(root: Path) -> None:
    epic_id = f"{NFC_UPPER}-001"  # the id the allocator issues
    _write(root, epic_id, "greek-epic", kind="epic")
    _write(root, "FEAT-001", "member", related=[f"{NFD_LOWER}-001"], status="done")
    _commit(root)
    result = _invoke(["epic", "show", epic_id])
    assert result.exit_code == 0, (ascii(result.output), repr(result.exception))
    assert "member" in result.output, ascii(result.output)
    assert "Progress: 1/1" in result.output, ascii(result.output)


@pytest.mark.parametrize("asked", [LOWER, NFD_LOWER, RAW_UPPER], ids=["lower", "nfd", "raw"])
def test_epic_show_finds_the_nfc_epic_by_any_spelling(root: Path, asked: str) -> None:
    _write(root, f"{NFC_UPPER}-001", "greek-epic", kind="epic")
    _write(root, "FEAT-001", "member", related=[f"{NFC_UPPER}-001"])
    result = _invoke(["epic", "show", f"{asked}-001"])
    assert result.exit_code == 0, (ascii(result.output), repr(result.exception))
    assert "greek-epic" in result.output, ascii(result.output)
    assert "member" in result.output, ascii(result.output)


def test_control_epic_show_exact_case(root: Path) -> None:
    _write(root, "EPIC-001", "the-epic", kind="epic")
    _write(root, "FEAT-001", "member", related=["EPIC-001"], status="done")
    _write(root, "FEAT-002", "outsider")
    result = _invoke(["epic", "show", "EPIC-001"])
    assert result.exit_code == 0, (result.output, repr(result.exception))
    assert "member" in result.output
    assert "outsider" not in result.output
    assert "Progress: 1/1" in result.output


def test_control_epic_show_unknown_epic_is_not_found(root: Path) -> None:
    _write(root, "EPIC-001", "the-epic", kind="epic")
    result = _invoke(["epic", "show", "EPIC-009"])
    assert result.exit_code != 0
    assert "EPIC-009 not found" in result.output


# ---------------------------------------------------------------------------
# 2. epic add / create --items: epic and item by any case; folded "already linked"
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("group", ["epic", "voyage"])
def test_epic_add_finds_the_epic_by_lower_case(root: Path, group: str) -> None:
    _write(root, "EPIC-001", "the-epic", kind="epic")
    item = _write(root, "FEAT-001", "member")
    result = _invoke([group, "add", "epic-001", "FEAT-001"])
    assert result.exit_code == 0, (result.output, repr(result.exception))
    assert [_fold(r) for r in _related(item)] == ["EPIC-001"], _related(item)


def test_epic_add_finds_the_item_by_lower_case(root: Path) -> None:
    _write(root, "EPIC-001", "the-epic", kind="epic")
    item = _write(root, "FEAT-001", "member")
    result = _invoke(["epic", "add", "EPIC-001", "feat-001"])
    assert result.exit_code == 0, (result.output, repr(result.exception))
    assert "not found" not in result.output, result.output
    assert _related(item) == ["EPIC-001"], _related(item)


def test_epic_add_finds_the_nfc_item_by_nfd_lower(root: Path) -> None:
    _write(root, "EPIC-001", "the-epic", kind="epic")
    item = _write(root, f"{NFC_UPPER}-002", "greek-item")
    _commit(root)
    result = _invoke(["epic", "add", "EPIC-001", f"{NFD_LOWER}-002"])
    assert result.exit_code == 0, (ascii(result.output), repr(result.exception))
    assert _related(item) == ["EPIC-001"], _related(item)


def test_epic_add_a_lower_case_related_is_already_linked(root: Path) -> None:
    """`related: [epic-001]` already links EPIC-001: no second entry is appended."""
    _write(root, "EPIC-001", "the-epic", kind="epic")
    item = _write(root, "FEAT-001", "member", related=["epic-001"])
    result = _invoke(["epic", "add", "EPIC-001", "FEAT-001"])
    assert result.exit_code == 0, (result.output, repr(result.exception))
    assert _related(item) == ["epic-001"], _related(item)
    assert "already linked" in result.output, result.output


def test_epic_add_an_nfd_related_is_already_linked_to_the_nfc_epic(root: Path) -> None:
    epic_id = f"{NFC_UPPER}-001"
    _write(root, epic_id, "greek-epic", kind="epic")
    nfd = f"{NFD_LOWER}-001"
    item = _write(root, "FEAT-001", "member", related=[nfd])
    _commit(root)
    result = _invoke(["epic", "add", epic_id, "FEAT-001"])
    assert result.exit_code == 0, (ascii(result.output), repr(result.exception))
    assert _related(item) == [nfd], ascii(_related(item))
    assert "already linked" in result.output, ascii(result.output)


def test_epic_create_items_links_a_lower_case_item(root: Path) -> None:
    item = _write(root, "FEAT-001", "member")
    result = _invoke(["epic", "create", "Big", "--items", "feat-001"])
    assert result.exit_code == 0, (result.output, repr(result.exception))
    assert "not found" not in result.output, result.output
    assert [_fold(r) for r in _related(item)] == ["EPIC-001"], _related(item)


def test_control_epic_add_exact_case(root: Path) -> None:
    _write(root, "EPIC-001", "the-epic", kind="epic")
    item = _write(root, "FEAT-001", "member")
    result = _invoke(["epic", "add", "EPIC-001", "FEAT-001"])
    assert result.exit_code == 0, (result.output, repr(result.exception))
    assert "Linked" in result.output
    assert _related(item) == ["EPIC-001"]
    again = _invoke(["epic", "add", "EPIC-001", "FEAT-001"])
    assert again.exit_code == 0
    assert "already linked" in again.output
    assert _related(item) == ["EPIC-001"]


def test_control_epic_create_items_exact_case(root: Path) -> None:
    item = _write(root, "FEAT-001", "member")
    result = _invoke(["epic", "create", "Big", "--items", "FEAT-001"])
    assert result.exit_code == 0, (result.output, repr(result.exception))
    assert "Linked FEAT-001" in result.output
    assert _related(item) == ["EPIC-001"]


# ---------------------------------------------------------------------------
# 3. CLI show / move / comment: fold_id, and NFC in not-found messages
# ---------------------------------------------------------------------------


@pytest.fixture
def greek(root: Path) -> Path:
    path = _write(root, f"{NFC_UPPER}-002", "greek-item", status="ready")
    _commit(root)
    return path


def test_cli_show_finds_the_nfc_id_by_lower(greek: Path) -> None:
    result = _invoke(["show", f"{LOWER}-002"])
    assert result.exit_code == 0, (ascii(result.output), repr(result.exception))
    assert "greek-item" in result.output


def test_cli_move_not_found_message_is_nfc(greek: Path) -> None:
    result = _invoke(["move", f"{LOWER}-099", "done"])
    assert result.exit_code != 0
    assert f"Item not found: {NFC_UPPER}-099" in result.output, ascii(result.output)
    assert RAW_UPPER not in result.output, ascii(result.output)


def test_cli_move_finds_the_nfc_id_by_lower(greek: Path) -> None:
    result = _invoke(["move", f"{LOWER}-002", "in_progress", "--no-commit"])
    assert result.exit_code == 0, (ascii(result.output), repr(result.exception))
    assert f"Moved {NFC_UPPER}-002" in result.output, ascii(result.output)


def test_cli_comment_not_found_message_is_nfc(greek: Path) -> None:
    result = _invoke(["comment", f"{LOWER}-099", "--body", "hi", "--agent", "Z"])
    assert result.exit_code != 0
    assert f"Item not found: {NFC_UPPER}-099" in result.output, ascii(result.output)
    assert RAW_UPPER not in result.output, ascii(result.output)


def test_cli_comment_finds_the_nfc_id_by_lower(greek: Path) -> None:
    result = _invoke(["comment", f"{LOWER}-002", "--body", "hi", "--agent", "Z"])
    assert result.exit_code == 0, (ascii(result.output), repr(result.exception))
    assert "hi" in greek.read_text(encoding="utf-8")


def test_control_cli_move_not_found_ascii(root: Path) -> None:
    _write(root, "FEAT-001", "member")
    result = _invoke(["move", "feat-099", "done"])
    assert result.exit_code != 0
    assert "Item not found: FEAT-099" in result.output


# ---------------------------------------------------------------------------
# 4. MCP kanban_get_item: fold_id, and NFC in the not-found error
# ---------------------------------------------------------------------------


def _mcp(root: Path, name: str, arguments: dict) -> dict:
    from yurtle_kanban.mcp.server import KanbanMCPServer

    return KanbanMCPServer(repo_root=root).handle_tool_call(name, arguments)


def test_mcp_get_item_not_found_error_is_nfc(root: Path, greek: Path) -> None:
    result = _mcp(root, "kanban_get_item", {"item_id": f"{LOWER}-099"})
    assert result.get("error") == f"Item not found: {NFC_UPPER}-099", ascii(result)


def test_mcp_get_item_finds_the_nfc_id_by_lower(root: Path, greek: Path) -> None:
    result = _mcp(root, "kanban_get_item", {"item_id": f"{NFD_LOWER}-002"})
    assert "error" not in result, ascii(result)
    assert result["item"]["id"] == f"{NFC_UPPER}-002", ascii(result)


def test_control_mcp_get_item_exact_and_not_found(root: Path) -> None:
    _write(root, "FEAT-001", "member")
    assert _mcp(root, "kanban_get_item", {"item_id": "FEAT-001"})["item"]["id"] == "FEAT-001"
    assert _mcp(root, "kanban_get_item", {"item_id": "feat-099"}) == {
        "error": "Item not found: FEAT-099"
    }
