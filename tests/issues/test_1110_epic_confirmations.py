"""#1110: `epic` / `voyage` create, add and link confirmations render control characters escaped.

The #251/#215 ruling as #1093/#1105 applied it (repo text reaches the terminal through
`safe()`: markup escaped + `escape_nonprintable`) extends to the lines #1105 left on bare Rich
`escape()` in `epic_commands.py`: `create`'s "Created …" line and its "File:" line, the
`--items` "Linked … → …" / "… already linked" lines, `add`'s "Linked … → …" / "… is already
linked to …" confirmations, and `show`'s "Link items with: … add <id> ITEM-ID" hint for an
epic with no linked items (the #1110 addendum: `epic_id` there is the repo's `epic_item.id`).

Markup-only `escape()` let an ESC (YAML `\\e`) through raw (`\\x1b[2J` clears the reader's
screen) and a newline break the line (forging an output line that starts `FORGED`).

Decided behaviour: every id, title and path these lines print shows ESC as `\\x1b`, a newline
as `\\n`, brackets verbatim.

Reachability offline:
- ids: an item / epic file whose `id:` holds the control characters, named on argv (the
  folded lookup matches it); the epic `create` allocates is always `PREFIX-NNN`, so the
  `create` lines' repo-text ids are the `--items` ones.
- title: `create`'s argv title.
- path: the board root (scan dir) named with an ESC in `.kanban/config.yaml`, so the new
  file's path holds it (the filename is slugified, so the title never reaches it).
- `create --items` "already linked": the item's `related:` already names the id `create` will
  allocate (`VOY-001` on an empty board).

Each run swaps `epic_commands.console` for a wide, NON-terminal Rich `Console`: Rich writes no
colour codes of its own, so ANY `\\x1b` in the output came from the repo or argv.

Controls: printable text with `\\` and `[x]` renders exactly as written.
"""

from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner
from rich.console import Console

from yurtle_kanban import config as config_mod
from yurtle_kanban import epic_commands
from yurtle_kanban.cli import main

EVIL = "\x1b[2J\nFORGED[b]x[/b]"
EVIL_SHOWN = "\\x1b[2J\\nFORGED[b]x[/b]"

TITLE = "T" + EVIL
TITLE_SHOWN = "T" + EVIL_SHOWN
ITEM_ID = "EXP-9" + EVIL
ITEM_SHOWN = "EXP-9" + EVIL_SHOWN
EPIC_ID = "VOY-9" + EVIL
EPIC_SHOWN = "VOY-9" + EVIL_SHOWN
# the board root: a directory name, so ESC only (no newline / slash games)
ROOT = "kanban\x1b[2J"
ROOT_SHOWN = "kanban\\x1b[2J"

PRINTABLE = r"back\slash [x] and [bold]b[/bold]"
PRINTABLE_ID = "EXP-7[x]"
PRINTABLE_EPIC = r"VOY-7\[b]"


def _yaml(text: str) -> str:
    """`text` as a YAML double-quoted scalar (`\\e`, `\\n`, `\\\\`, `\\"` escapes)."""
    body = (
        text.replace("\\", "\\\\").replace('"', '\\"').replace("\x1b", "\\e").replace("\n", "\\n")
    )
    return f'"{body}"'


def _config(root: str) -> str:
    return (
        f"kanban:\n  theme: nautical\n  paths:\n    root: {_yaml(root + '/')}\n"
        f"    scan_paths:\n      - {_yaml(root + '/')}\n"
    )


# --- helpers --------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)


def _repo(tmp_path: Path, root: str = "kanban-work") -> Path:
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@t.com")
    _git(tmp_path, "config", "user.name", "T")
    (tmp_path / ".kanban").mkdir()
    (tmp_path / ".kanban" / "config.yaml").write_text(_config(root))
    (tmp_path / root).mkdir()
    return tmp_path


def _item(
    repo: Path, n: int, item_id: str, item_type: str, front: dict[str, str] | None = None
) -> None:
    lines = [
        f"id: {_yaml(item_id)}",
        f"type: {item_type}",
        'title: "t"',
        "status: backlog",
        "priority: medium",
        "created: 2026-09-29",
    ]
    lines += [f"{k}: {v}" for k, v in (front or {}).items()]
    # a plain filename: the id's controls live in the frontmatter only
    path = repo / "kanban-work" / f"{item_type}-{n}.md"
    path.write_text("---\n" + "\n".join(lines) + "\n---\n\n# item\n")


@pytest.fixture(autouse=True)
def _clean() -> None:
    config_mod._theme_cache.clear()


def _run(repo: Path, args: list[str], monkeypatch: pytest.MonkeyPatch) -> str:
    """Run with `epic_commands.console` swapped for a wide non-terminal one: no colour, so
    any ESC in the output is the repo's or argv's."""
    buf = io.StringIO()
    monkeypatch.setattr(epic_commands, "console", Console(file=buf, width=300))
    monkeypatch.chdir(repo)
    result = CliRunner().invoke(main, args)
    assert result.exit_code == 0, (result.output, result.stderr, result.exception)
    return buf.getvalue() + result.output


def _no_raw_control(out: str) -> None:
    assert "\x1b" not in out, f"raw ESC reached the terminal: {out!r}"
    assert not any(line.lstrip(" ").startswith("FORGED") for line in out.splitlines()), (
        f"a newline forged an output line: {out!r}"
    )


def _line(out: str, marker: str) -> str:
    """The first output line holding `marker`."""
    for line in out.splitlines():
        if marker in line:
            return line
    raise AssertionError(f"no line with {marker!r}: {out!r}")


GROUPS = pytest.mark.parametrize("group", ["epic", "voyage"])


# --- the fixture really is evil (so the escaped checks below prove something) -----------


def test_fixture_ids_parse_with_the_raw_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    _item(repo, 1, EPIC_ID, "voyage")
    _item(repo, 2, ITEM_ID, "expedition")
    monkeypatch.chdir(repo)
    for item_id in (EPIC_ID, ITEM_ID):
        result = CliRunner().invoke(main, ["show", item_id, "--json"])
        assert result.exit_code == 0, (result.output, result.exception)
        assert json.loads(result.output)["id"] == item_id


# --- create: "Created …" + "File:" ------------------------------------------------------


@GROUPS
def test_create_title_is_escaped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, group: str
) -> None:
    out = _run(_repo(tmp_path), [group, "create", TITLE], monkeypatch)
    _no_raw_control(out)
    assert TITLE_SHOWN in _line(out, "Created"), out


@GROUPS
def test_create_file_path_is_escaped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, group: str
) -> None:
    out = _run(_repo(tmp_path, ROOT), [group, "create", "plain"], monkeypatch)
    _no_raw_control(out)
    assert ROOT_SHOWN in _line(out, "File:"), out


# --- create --items: "Linked … → …" / "… already linked" --------------------------------


@GROUPS
def test_create_items_linked_line_is_escaped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, group: str
) -> None:
    repo = _repo(tmp_path)
    _item(repo, 1, ITEM_ID, "expedition")
    out = _run(repo, [group, "create", "plain", "--items", ITEM_ID], monkeypatch)
    _no_raw_control(out)
    assert ITEM_SHOWN in _line(out, "Linked"), out


@GROUPS
def test_create_items_already_linked_line_is_escaped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, group: str
) -> None:
    repo = _repo(tmp_path)
    # names the id `create` allocates on an empty board
    _item(repo, 1, ITEM_ID, "expedition", {"related": "[VOY-001]"})
    out = _run(repo, [group, "create", "plain", "--items", ITEM_ID], monkeypatch)
    _no_raw_control(out)
    assert ITEM_SHOWN in _line(out, "already linked"), out


# --- add: "Linked … → …" / "… is already linked to …" -----------------------------------


@GROUPS
def test_add_linked_line_is_escaped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, group: str
) -> None:
    repo = _repo(tmp_path)
    _item(repo, 1, EPIC_ID, "voyage")
    _item(repo, 2, ITEM_ID, "expedition")
    out = _run(repo, [group, "add", EPIC_ID, ITEM_ID], monkeypatch)
    _no_raw_control(out)
    line = _line(out, "Linked")
    assert ITEM_SHOWN in line and EPIC_SHOWN in line, out


@GROUPS
def test_add_already_linked_line_is_escaped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, group: str
) -> None:
    repo = _repo(tmp_path)
    _item(repo, 1, EPIC_ID, "voyage")
    _item(repo, 2, ITEM_ID, "expedition", {"related": f"[{_yaml(EPIC_ID)}]"})
    out = _run(repo, [group, "add", EPIC_ID, ITEM_ID], monkeypatch)
    _no_raw_control(out)
    line = _line(out, "already linked to")
    assert ITEM_SHOWN in line and EPIC_SHOWN in line, out


# --- show: the unlinked epic's "Link items with" hint -----------------------------------


@GROUPS
def test_show_unlinked_hint_is_escaped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, group: str
) -> None:
    repo = _repo(tmp_path)
    _item(repo, 1, EPIC_ID, "voyage")
    out = _run(repo, [group, "show", EPIC_ID], monkeypatch)
    assert "No linked items found." in out, out
    _no_raw_control(out)
    assert EPIC_SHOWN in _line(out, "Link items with"), out


# --- control: printable text is unchanged -----------------------------------------------


@GROUPS
def test_printable_create_lines_render_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, group: str
) -> None:
    repo = _repo(tmp_path)
    _item(repo, 1, PRINTABLE_ID, "expedition")
    out = _run(repo, [group, "create", PRINTABLE, "--items", PRINTABLE_ID], monkeypatch)
    assert PRINTABLE in _line(out, "Created"), out
    assert PRINTABLE_ID in _line(out, "Linked"), out
    assert "\\\\" not in out, f"a printable backslash was escaped: {out!r}"


@GROUPS
def test_printable_add_and_hint_render_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, group: str
) -> None:
    repo = _repo(tmp_path)
    _item(repo, 1, PRINTABLE_EPIC, "voyage")
    _item(repo, 2, PRINTABLE_ID, "expedition")
    out = _run(repo, [group, "show", PRINTABLE_EPIC], monkeypatch)
    assert PRINTABLE_EPIC in _line(out, "Link items with"), out
    out = _run(repo, [group, "add", PRINTABLE_EPIC, PRINTABLE_ID], monkeypatch)
    line = _line(out, "Linked")
    assert PRINTABLE_ID in line and PRINTABLE_EPIC in line, out
    out = _run(repo, [group, "add", PRINTABLE_EPIC, PRINTABLE_ID], monkeypatch)
    line = _line(out, "already linked to")
    assert PRINTABLE_ID in line and PRINTABLE_EPIC in line, out
    assert "\\\\" not in out, f"a printable backslash was escaped: {out!r}"
