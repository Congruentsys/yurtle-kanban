"""Issue #583 — `update_item` (and MCP `kanban_update_item`) rewrites the whole item file.

`update_item` wrote `item.to_markdown()`, which regenerates the file from the model
fields alone, so one `update` deleted the ```yurtle `kb:statusChange` history block,
every frontmatter key the model doesn't know (`custom_key`, `blocks`, ...) and wrote
`status:` back as its canonical name (an hdd `draft` became `backlog`, reversing
#439/#448). The MCP tool `kanban_update_item` goes through the same method.

Expected (issue body, and the adversarial review on #576): field-level edits only, in
the `rank_item` / `_add_or_update_frontmatter_field` pattern. After an update the file
is byte-identical to the original except for the changed frontmatter line(s), the
`# Title` H1 for a title change, and the body span (between the H1 and the first
```yurtle fence / `## Comments`) for a description change.

Fixtures: a nautical EXP (LF and CRLF), a software FEAT, and an hdd idea left in a
native `active` and a native `draft` status. Each has a real `move` history block, a
`## Comments` section, a body paragraph, and hand-added `tags`, `blocks`,
`superseded_by` and `custom_key: keepme` frontmatter keys.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from yurtle_kanban.cli import main
from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.mcp.server import KanbanMCPServer
from yurtle_kanban.models import WorkItemStatus
from yurtle_kanban.service import KanbanService

# name -> (theme, create type, statuses to move through, CRLF?, expected status line)
FIXTURES: dict[str, tuple[str, str, list[WorkItemStatus], bool, str]] = {
    "nautical": ("nautical", "expedition", [WorkItemStatus.IN_PROGRESS], False, "in_progress"),
    "nautical-crlf": (
        "nautical",
        "expedition",
        [WorkItemStatus.IN_PROGRESS],
        True,
        "in_progress",
    ),
    "software": ("software", "feature", [WorkItemStatus.IN_PROGRESS], False, "in_progress"),
    "hdd-active": ("hdd", "idea", [WorkItemStatus.IN_PROGRESS], False, "active"),
    # moved to in_progress and back: a history block AND a native `draft` status
    "hdd-draft-crlf": (
        "hdd",
        "idea",
        [WorkItemStatus.IN_PROGRESS, WorkItemStatus.BACKLOG],
        True,
        "draft",
    ),
}

EXTRA_KEYS = [
    "tags: [alpha]",
    "blocks: [X-9]",
    "superseded_by: [X-8]",
    "custom_key: keepme",
]
BODY = "Original body paragraph."


# ---------------------------------------------------------------------------
# fixture construction
# ---------------------------------------------------------------------------


def _git(path: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=path, capture_output=True, check=True)


def _clear_theme_cache() -> None:
    from yurtle_kanban import config as config_mod

    config_mod._theme_cache.clear()


def _service(root: Path) -> KanbanService:
    """A fresh service (no cached items) over the repo."""
    return KanbanService(KanbanConfig.load(root / ".kanban" / "config.yaml"), root)


class Item:
    def __init__(self, root: Path, item_id: str, path: Path, crlf: bool, status: str):
        self.root = root
        self.id = item_id
        self.path = path
        self.crlf = crlf
        self.status = status


def _build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str) -> Item:
    theme, item_type, moves, crlf, status = FIXTURES[name]
    root = tmp_path / name
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "test@test.com")
    _git(root, "config", "user.name", "Test")
    _git(root, "commit", "--allow-empty", "-m", "init")
    _clear_theme_cache()
    monkeypatch.chdir(root)
    runner = CliRunner()
    for args in (["init", "--theme", theme], ["create", item_type, "Alpha one", "-p", "high"]):
        result = runner.invoke(main, args)
        assert result.exit_code == 0, result.output

    svc = _service(root)
    (item,) = svc.get_items()
    path = item.file_path

    # hand edits: unknown / unparsed keys in the frontmatter, and a body paragraph
    text = path.read_text(encoding="utf-8")
    text = text.replace("depends_on: []\n", "depends_on: []\n" + "\n".join(EXTRA_KEYS) + "\n", 1)
    text = text.replace("# Alpha one\n", f"# Alpha one\n\n{BODY}\n", 1)
    assert BODY in text and "custom_key: keepme" in text, text
    path.write_text(text, encoding="utf-8")

    # a real history block via move, then a real comment
    svc = _service(root)
    for target in moves:
        svc.move_item(
            item.id,
            target,
            commit=False,
            validate_workflow=False,
            skip_wip_check=True,
            skip_gates=True,
        )
    svc.add_comment(item.id, "a note worth keeping", "bob", commit=False)

    if crlf:
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "fixture")

    data = path.read_bytes()
    assert b"```yurtle" in data and b"kb:statusChange" in data, data
    assert b"## Comments" in data and b"a note worth keeping" in data, data
    assert re.search(rb"^status: " + status.encode() + rb"\r?$", data, re.M), data
    assert (b"\r\n" in data) is crlf
    return Item(root, item.id, path, crlf, status)


@pytest.fixture(params=list(FIXTURES))
def item(request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Item:
    return _build(tmp_path, monkeypatch, request.param)


@pytest.fixture
def nautical(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Item:
    return _build(tmp_path, monkeypatch, "nautical")


@pytest.fixture
def hdd_draft(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Item:
    return _build(tmp_path, monkeypatch, "hdd-draft-crlf")


# ---------------------------------------------------------------------------
# checks
# ---------------------------------------------------------------------------


def _history(data: bytes) -> bytes:
    start = data.index(b"```yurtle")
    end = data.index(b"```", start + 3) + 3
    return data[start:end]


def _comments(data: bytes) -> bytes:
    return data[data.index(b"## Comments") :]


def _assert_preserved(old: bytes, new: bytes, status: str) -> None:
    """The named data-loss symptoms of #583, each with its own message."""
    assert b"kb:statusChange" in new and _history(old) == _history(new), (
        f"status history block not preserved:\n{new.decode()}"
    )
    assert _comments(old) in new, f"## Comments section not preserved:\n{new.decode()}"
    for key in EXTRA_KEYS[1:]:
        assert re.search(rb"^" + re.escape(key.encode()) + rb"\r?$", new, re.M), (
            f"frontmatter key {key!r} not preserved:\n{new.decode()}"
        )
    assert re.search(rb"^status: " + status.encode() + rb"\r?$", new, re.M), (
        f"native status {status!r} rewritten:\n{new.decode()}"
    )
    if b"\r\n" in old:  # the CRLF fixtures are CRLF throughout
        assert new.count(b"\n") == new.count(b"\r\n"), f"CRLF line endings not preserved:\n{new!r}"
    else:
        assert b"\r" not in new, f"LF file gained CR:\n{new!r}"


def _frontmatter_end(lines: list[bytes]) -> int:
    """Index of the closing `---` line."""
    assert lines[0].rstrip(b"\r") == b"---"
    return next(i for i in range(1, len(lines)) if lines[i].rstrip(b"\r") == b"---")


def _assert_only_lines(
    old: bytes,
    new: bytes,
    key: str,
    expected: object,
    h1: str | None = None,
) -> None:
    """`new` is `old` with only frontmatter `key:` (value `expected`) and, when `h1` is
    given, the `# Title` line changed; every changed line keeps its line ending."""
    old_lines, new_lines = old.split(b"\n"), new.split(b"\n")
    assert len(old_lines) == len(new_lines), (
        f"line count changed {len(old_lines)} -> {len(new_lines)}:\n{new.decode()}"
    )
    fm_end = _frontmatter_end(old_lines)
    allowed = {i for i in range(1, fm_end) if old_lines[i].startswith(key.encode() + b":")}
    assert len(allowed) == 1, old.decode()
    if h1 is not None:
        allowed |= {
            next(i for i in range(fm_end + 1, len(old_lines)) if old_lines[i].startswith(b"# "))
        }
    changed = {i for i, (a, b) in enumerate(zip(old_lines, new_lines, strict=True)) if a != b}
    assert changed == allowed, (
        "changed lines "
        + repr([new_lines[i] for i in sorted(changed - allowed)])
        + f" beyond the intended ones:\n{new.decode()}"
    )
    for i in changed:
        assert old_lines[i].endswith(b"\r") == new_lines[i].endswith(b"\r"), new_lines[i]
        line = new_lines[i].rstrip(b"\r").decode()
        if i < fm_end:
            assert line.startswith(f"{key}:"), line
            assert yaml.safe_load(line)[key] == expected, line
        else:
            assert line == f"# {h1}", line


# ---------------------------------------------------------------------------
# service.update_item, one field at a time
# ---------------------------------------------------------------------------

# kwargs -> (frontmatter key, parsed value, new H1 or None)
UPDATES: dict[str, tuple[dict[str, object], str, object, str | None]] = {
    "title": ({"title": "Beta two"}, "title", "Beta two", "Beta two"),
    "priority": ({"priority": "low"}, "priority", "low", None),
    "assignee": ({"assignee": "alice"}, "assignee", "alice", None),
    "tags": ({"tags": ["alpha", "gamma"]}, "tags", ["alpha", "gamma"], None),
}


@pytest.mark.parametrize("field", list(UPDATES))
def test_update_changes_only_that_field(item: Item, field: str) -> None:
    kwargs, key, value, h1 = UPDATES[field]
    old = item.path.read_bytes()
    _service(item.root).update_item(item.id, commit=False, **kwargs)  # type: ignore[arg-type]
    new = item.path.read_bytes()
    _assert_preserved(old, new, item.status)
    _assert_only_lines(old, new, key, value, h1)


def test_update_description_replaces_only_the_body_span(item: Item) -> None:
    old = item.path.read_bytes()
    _service(item.root).update_item(item.id, description="A brand new body.", commit=False)
    new = item.path.read_bytes()
    _assert_preserved(old, new, item.status)

    h1_old = old.index(b"# Alpha one")
    h1_end = old.index(b"\n", h1_old) + 1
    assert new[:h1_end] == old[:h1_end], f"frontmatter/H1 changed:\n{new.decode()}"
    fence = old.index(b"```yurtle")
    assert new.endswith(old[fence:]), f"history/comments tail changed:\n{new.decode()}"
    span = new[h1_end : len(new) - len(old[fence:])]
    assert b"A brand new body." in span, span
    assert BODY.encode() not in new, new.decode()
    if item.crlf:
        assert b"\n" not in span.replace(b"\r\n", b""), f"bare LF in CRLF body: {span!r}"


def test_noop_update_changes_nothing(item: Item) -> None:
    """Control: every field set to its current value writes nothing."""
    old = item.path.read_bytes()
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=item.root, capture_output=True, text=True, check=True
    ).stdout
    _service(item.root).update_item(
        item.id, title="Alpha one", priority="high", tags=["alpha"], commit=True
    )
    assert item.path.read_bytes() == old
    assert (
        subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=item.root, capture_output=True, text=True, check=True
        ).stdout
        == head
    )


def test_several_fields_at_once(hdd_draft: Item) -> None:
    """title + priority in one call: exactly the title line, H1 and priority line change."""
    old = hdd_draft.path.read_bytes()
    _service(hdd_draft.root).update_item(
        hdd_draft.id, title="Beta two", priority="critical", commit=False
    )
    new = hdd_draft.path.read_bytes()
    _assert_preserved(old, new, hdd_draft.status)
    old_lines, new_lines = old.split(b"\n"), new.split(b"\n")
    assert len(old_lines) == len(new_lines), new.decode()
    changed = [
        new_lines[i] for i, (a, b) in enumerate(zip(old_lines, new_lines, strict=True)) if a != b
    ]
    assert all(line.endswith(b"\r") for line in changed), changed
    text = [line.rstrip(b"\r").decode() for line in changed]
    assert "# Beta two" in text, text
    fm = yaml.safe_load("\n".join(t for t in text if not t.startswith("# ")))
    assert fm == {"title": "Beta two", "priority": "critical"}, text
    assert len(text) == 3, text


# ---------------------------------------------------------------------------
# MCP kanban_update_item
# ---------------------------------------------------------------------------


def _mcp_update(root: Path, args: dict[str, object]) -> dict[str, object]:
    server = KanbanMCPServer(repo_root=root)
    result = server.handle_tool_call("kanban_update_item", args)
    assert result.get("success") is True, result
    return result


@pytest.mark.parametrize("fixture_name", ["nautical", "hdd_draft"])
def test_mcp_update_item_is_field_level(fixture_name: str, request: pytest.FixtureRequest) -> None:
    it: Item = request.getfixturevalue(fixture_name)
    old = it.path.read_bytes()
    cwd = os.getcwd()
    _mcp_update(it.root, {"item_id": it.id.lower(), "title": "Beta two"})
    assert os.getcwd() == cwd
    new = it.path.read_bytes()
    _assert_preserved(old, new, it.status)
    _assert_only_lines(old, new, "title", "Beta two", "Beta two")
