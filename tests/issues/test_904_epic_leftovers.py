"""#904: epic_commands leftovers from the PR #901 (#868) review.

1. `_update_item_related`'s "not found" and "malformed related" warnings print the
   raw id they were given, which is not NFC; they should show the NFC (folded) id.
2. `_do_show` iterates the epic's own `related` without checking it is a list.
   The service keeps a mapping-valued `related:` as a mapping (#675), so `show`
   walks its keys and lists them as linked items. The convention for a malformed
   `related:` (#188, `_links`) is that a mapping or a number links nothing; that
   is what is pinned here, with no traceback. A string `related:` is a
   comma-separated list, as #188 and `_list_text` read it.
3. #868's third point: the fallback id for an item file without `id:` is built
   with `stem.upper()`, which is not NFC for a decomposed stem; it should be
   `fold_id(stem)`-shaped. The live path is `KanbanService._parse_text`
   (service.py, `stem.upper().replace("-", "_")`), which `list`/`show` use; the
   deprecated `WorkItemIndexer` (indexer.py) is not pinned.
"""

from __future__ import annotations

import json
import subprocess
import unicodedata
from pathlib import Path

import pytest
from click.testing import CliRunner

from yurtle_kanban.cli import main
from yurtle_kanban.config import KanbanConfig, PathConfig
from yurtle_kanban.models import fold_id

NFC_ITEM = unicodedata.normalize("NFC", "ÉX-001")
NFD_ITEM = unicodedata.normalize("NFD", NFC_ITEM)
NFC_MISSING = unicodedata.normalize("NFC", "ÉX-099")
NFD_MISSING = unicodedata.normalize("NFD", NFC_MISSING)


def test_fixture_nfd_spellings_differ() -> None:
    assert NFD_ITEM != NFC_ITEM
    assert NFD_MISSING != NFC_MISSING
    assert fold_id(NFD_ITEM) == NFC_ITEM


# ---------------------------------------------------------------------------
# Fixtures (as tests/issues/test_868_epic_fold_id.py)
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
    item_id: str | None,
    title: str,
    *,
    kind: str = "feature",
    related_line: str = "related: []\n",
    stem: str | None = None,
) -> Path:
    folder = root / "work" / ("epics" if kind == "epic" else "features")
    path = folder / f"{stem or title}.md"
    id_line = f"id: {item_id}\n" if item_id is not None else ""
    path.write_text(
        f'---\n{id_line}title: "{title}"\ntype: {kind}\nstatus: backlog\n'
        f"priority: medium\n{related_line}---\n\n# {title}\n",
        encoding="utf-8",
    )
    return path


def _invoke(args: list[str]):
    return CliRunner().invoke(main, args)


def _no_traceback(result) -> None:
    assert "Traceback" not in result.output, result.output
    assert result.exception is None or isinstance(result.exception, SystemExit), (
        repr(result.exception),
        result.output,
    )


_BAD_RELATED = [
    pytest.param("related: {a: 1}\n", id="mapping"),
    pytest.param("related: 5\n", id="number"),
]


# ---------------------------------------------------------------------------
# 1. _update_item_related warnings show the NFC id
# ---------------------------------------------------------------------------


def test_create_items_not_found_warning_is_nfc(root: Path) -> None:
    result = _invoke(["epic", "create", "Big", "--items", NFD_MISSING])
    _no_traceback(result)
    assert "not found" in result.output, result.output
    assert NFC_MISSING in result.output, result.output
    assert NFD_MISSING not in result.output, result.output


def test_add_not_found_output_is_nfc(root: Path) -> None:
    _write(root, "EPIC-001", "Epic", kind="epic")
    result = _invoke(["epic", "add", "EPIC-001", NFD_MISSING])
    _no_traceback(result)
    assert "not found" in result.output, result.output
    assert NFC_MISSING in result.output, result.output
    assert NFD_MISSING not in result.output, result.output


@pytest.mark.parametrize("related_line", _BAD_RELATED)
def test_add_malformed_related_warning_is_nfc(root: Path, related_line: str) -> None:
    _write(root, "EPIC-001", "Epic", kind="epic")
    path = _write(root, NFC_ITEM, "Item", related_line=related_line)
    before = path.read_bytes()
    result = _invoke(["epic", "add", "EPIC-001", NFD_ITEM])
    _no_traceback(result)
    assert path.read_bytes() == before
    assert "related" in result.output, result.output
    assert NFC_ITEM in result.output, result.output
    assert NFD_ITEM not in result.output, result.output


@pytest.mark.parametrize("related_line", _BAD_RELATED)
def test_create_items_malformed_related_warning_is_nfc(root: Path, related_line: str) -> None:
    _write(root, NFC_ITEM, "Item", related_line=related_line)
    result = _invoke(["epic", "create", "Big", "--items", NFD_ITEM])
    _no_traceback(result)
    assert "related" in result.output, result.output
    assert NFC_ITEM in result.output, result.output
    assert NFD_ITEM not in result.output, result.output


def test_control_add_links_nfc_item_by_nfd(root: Path) -> None:
    _write(root, "EPIC-001", "Epic", kind="epic")
    _write(root, NFC_ITEM, "Item")
    result = _invoke(["epic", "add", "EPIC-001", NFD_ITEM])
    assert result.exit_code == 0, result.output
    assert f"Linked {NFC_ITEM}" in result.output, result.output


# ---------------------------------------------------------------------------
# 2. epic show with a non-list `related:` on the epic itself
# ---------------------------------------------------------------------------


def test_show_epic_with_mapping_related_lists_nothing_from_it(root: Path) -> None:
    # a mapping is not a list of IDs (#188): its keys are not linked items
    _write(root, "EPIC-001", "Epic", kind="epic", related_line="related: {FEAT-001: 1}\n")
    _write(root, "FEAT-001", "Feature")
    result = _invoke(["epic", "show", "EPIC-001"])
    _no_traceback(result)
    assert "EPIC-001" in result.output, result.output
    assert "FEAT-001" not in result.output, result.output


def test_show_epic_with_number_related_does_not_crash(root: Path) -> None:
    _write(root, "EPIC-001", "Epic", kind="epic", related_line="related: 5\n")
    _write(root, "FEAT-001", "Feature")
    result = _invoke(["epic", "show", "EPIC-001"])
    _no_traceback(result)
    assert "EPIC-001" in result.output, result.output
    assert "FEAT-001" not in result.output, result.output


def test_show_epic_with_string_related_does_not_crash(root: Path) -> None:
    # a string is a comma-separated list (#188, `_list_text`)
    _write(root, "EPIC-001", "Epic", kind="epic", related_line="related: FEAT-001\n")
    _write(root, "FEAT-001", "Feature")
    result = _invoke(["epic", "show", "EPIC-001"])
    _no_traceback(result)
    assert "EPIC-001" in result.output, result.output
    assert "FEAT-001" in result.output, result.output


def test_control_show_epic_with_list_related_shows_its_items(root: Path) -> None:
    _write(root, "EPIC-001", "Epic", kind="epic", related_line="related: [FEAT-001]\n")
    _write(root, "FEAT-001", "Feature")
    result = _invoke(["epic", "show", "EPIC-001"])
    assert result.exit_code == 0, result.output
    assert "FEAT-001" in result.output, result.output
    assert "Progress: 0/1" in result.output, result.output


# ---------------------------------------------------------------------------
# 3. fallback id for an item without `id:` is NFC
# ---------------------------------------------------------------------------


def _listed_ids(root: Path) -> list[str]:
    result = _invoke(["list", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    items = data["items"] if isinstance(data, dict) else data
    return [str(i["id"]) for i in items]


def test_fallback_id_of_nfd_stem_is_nfc(root: Path) -> None:
    stem = unicodedata.normalize("NFD", "énote")
    assert stem != unicodedata.normalize("NFC", stem)
    _write(root, None, "Note", stem=stem)
    ids = _listed_ids(root)
    assert len(ids) == 1, ids
    (got,) = ids
    assert got == unicodedata.normalize("NFC", got), ascii(got)
    assert got == fold_id(stem), (ascii(got), ascii(fold_id(stem)))


def test_control_fallback_id_of_ascii_stem_unchanged(root: Path) -> None:
    _write(root, None, "Note", stem="my-note")
    assert _listed_ids(root) == ["MY_NOTE"]
