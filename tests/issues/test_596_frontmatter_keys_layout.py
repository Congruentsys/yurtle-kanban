"""Issue #596 — `_add_or_update_frontmatter_field` follow-ups from the PR #591 review.

1. Quoted keys: `'title': Seed` / `"priority": high` / `'tags': [a]` weren't matched,
   so an edit appended a second, unquoted key (PyYAML's last-key-wins hid it, but the
   file had duplicate keys). The edit must change the value on that same line, add no
   key, and leave every other byte alone.
2. List layout: `update_item(tags=...)` rewrote a YAML block list as a flow list.
   Decided ([steer] on #596): a field that is already a block list stays a block list
   (same indentation, one `- item` per line). A flow list stays a flow list (control).
3. The other callers of the shared writer, where they apply: `rank_item`
   (`priority_rank`, `value_summary`) and `move_item(assignee=...)` (`status`,
   `assignee`) on quoted keys. The block-list case doesn't apply to them: they write
   scalars (and a block-list `assignee` is replaced by the scalar, #97/#128).

Note: `epic add` still writes `related` as a flow list over a block list — #169's
tests pin that no `- ` lines remain, so this issue's layout rule is for `tags` in
`update_item` only.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from yurtle_kanban.cli import main
from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.models import WorkItemStatus
from yurtle_kanban.service import KanbanService

ITEM_ID = "FEAT-001"
BODY = "\n# Seed\n\nBody text.\n"
KEY_RE = re.compile(r"""^(?:'([^']*)'|"([^"]*)"|([^\s:#'"-][^:]*?))[ \t]*:(?:[ \t]|$)""")


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------


def _git(path: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=path, capture_output=True, check=True)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from yurtle_kanban import config as config_mod

    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.email", "test@test.com")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "commit", "--allow-empty", "-m", "init")
    config_mod._theme_cache.clear()
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(main, ["init", "--theme", "software"])
    assert result.exit_code == 0, result.output
    return tmp_path


def _write(repo: Path, frontmatter: str) -> Path:
    """A FEAT-001 item file with exactly this frontmatter (lines, no fences)."""
    path = repo / "kanban-work" / "features" / f"{ITEM_ID}-seed.md"
    path.write_text(f"---\n{frontmatter}---\n{BODY}", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "fixture")
    return path


def _service(repo: Path) -> KanbanService:
    return KanbanService(KanbanConfig.load(repo / ".kanban" / "config.yaml"), repo)


def _fm_lines(data: str) -> list[str]:
    assert data.startswith("---\n"), data
    end = data.index("\n---\n", 3)
    return data[4 : end + 1].splitlines()


def _keys(data: str) -> list[str]:
    """Every top-level key in the frontmatter, quoted or not, in order."""
    keys = []
    for line in _fm_lines(data):
        match = KEY_RE.match(line)
        if match:
            keys.append(next(g for g in match.groups() if g is not None))
    return keys


def _assert_no_duplicate_keys(data: str) -> None:
    keys = _keys(data)
    dups = sorted({k for k in keys if keys.count(k) > 1})
    assert not dups, f"duplicate frontmatter keys {dups}:\n{data}"


def _assert_same_line_changed(old: str, new: str, changes: dict[str, object]) -> None:
    """`new` is `old` with only the lines of `changes`' keys changed, each in place and
    each parsing to its new value."""
    _assert_no_duplicate_keys(new)
    old_lines, new_lines = old.split("\n"), new.split("\n")
    assert len(old_lines) == len(new_lines), f"line count changed:\n{new}"
    changed = [i for i, (a, b) in enumerate(zip(old_lines, new_lines, strict=True)) if a != b]
    assert len(changed) == len(changes), f"changed lines {[new_lines[i] for i in changed]}"
    got: dict[str, object] = {}
    for i in changed:
        parsed = yaml.safe_load(new_lines[i])
        assert isinstance(parsed, dict) and len(parsed) == 1, new_lines[i]
        got.update(parsed)
        # the same key, on the same line
        assert set(yaml.safe_load(old_lines[i])) == set(parsed), (old_lines[i], new_lines[i])
    assert got == changes, got


QUOTED_FM = (
    "id: FEAT-001\n"
    "'title': Seed\n"
    "type: feature\n"
    "status: backlog\n"
    '"priority": high\n'
    "assignee: null\n"
    "'tags': [a, b]\n"
    "depends_on: []\n"
)


# ---------------------------------------------------------------------------
# 1. quoted keys through update_item
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "changes"),
    [
        pytest.param({"priority": "low"}, {"priority": "low"}, id="priority"),
        pytest.param({"tags": ["a", "b", "c"]}, {"tags": ["a", "b", "c"]}, id="tags"),
    ],
)
def test_update_quoted_key_in_place(
    repo: Path, kwargs: dict[str, object], changes: dict[str, object]
) -> None:
    path = _write(repo, QUOTED_FM)
    old = path.read_text(encoding="utf-8")
    _service(repo).update_item(ITEM_ID, commit=False, **kwargs)  # type: ignore[arg-type]
    _assert_same_line_changed(old, path.read_text(encoding="utf-8"), changes)


def test_update_quoted_title_in_place(repo: Path) -> None:
    """`'title': Seed`: the title line changes in place, plus the H1."""
    path = _write(repo, QUOTED_FM)
    old = path.read_text(encoding="utf-8")
    _service(repo).update_item(ITEM_ID, title="Renamed", commit=False)
    new = path.read_text(encoding="utf-8")
    assert "# Renamed\n" in new, new
    _assert_same_line_changed(old.replace("# Seed\n", "# Renamed\n"), new, {"title": "Renamed"})


def test_update_quoted_keys_all_at_once(repo: Path) -> None:
    path = _write(repo, QUOTED_FM)
    old = path.read_text(encoding="utf-8")
    _service(repo).update_item(ITEM_ID, title="Renamed", priority="low", tags=["z"], commit=False)
    new = path.read_text(encoding="utf-8")
    _assert_same_line_changed(
        old.replace("# Seed\n", "# Renamed\n"),
        new,
        {"title": "Renamed", "priority": "low", "tags": ["z"]},
    )


def test_quoted_key_item_still_reads(repo: Path) -> None:
    """Control: the parser reads quoted keys (so the item is found and updatable)."""
    _write(repo, QUOTED_FM)
    item = _service(repo).get_item(ITEM_ID)
    assert item is not None
    assert (item.title, item.priority, item.tags) == ("Seed", "high", ["a", "b"])


# ---------------------------------------------------------------------------
# 2. block-list layout through update_item(tags=...)
# ---------------------------------------------------------------------------

FM_HEAD = 'id: FEAT-001\ntitle: "Seed"\ntype: feature\nstatus: backlog\npriority: high\n'
FM_TAIL = "assignee: null\ndepends_on: []\n"


@pytest.mark.parametrize(
    ("key", "dash"),
    [
        pytest.param("tags", "  - ", id="indented"),
        pytest.param("tags", "- ", id="column-0"),
        pytest.param("tags", "    - ", id="indented-4"),
        pytest.param("'tags'", "  - ", id="quoted-key"),
    ],
)
@pytest.mark.parametrize(
    "new_tags",
    [
        pytest.param(["a", "b", "c"], id="grow"),
        pytest.param(["z"], id="shrink"),
    ],
)
def test_block_list_tags_stay_block(repo: Path, key: str, dash: str, new_tags: list[str]) -> None:
    block = f"{key}:\n{dash}a\n{dash}b\n"
    path = _write(repo, FM_HEAD + block + FM_TAIL)
    old = path.read_text(encoding="utf-8")
    _service(repo).update_item(ITEM_ID, tags=new_tags, commit=False)
    new = path.read_text(encoding="utf-8")
    _assert_no_duplicate_keys(new)
    expected_block = f"{key}:\n" + "".join(f"{dash}{t}\n" for t in new_tags)
    assert new == old.replace(block, expected_block), (
        f"expected the block list kept as a block list:\n{new}"
    )
    assert _service(repo).get_item(ITEM_ID).tags == new_tags  # type: ignore[union-attr]


def test_block_list_tags_with_crlf_stay_block(repo: Path) -> None:
    block = "tags:\n  - a\n  - b\n"
    path = _write(repo, FM_HEAD + block + FM_TAIL)
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    old = path.read_bytes()
    _service(repo).update_item(ITEM_ID, tags=["a", "b", "c"], commit=False)
    new = path.read_bytes()
    assert new == old.replace(
        b"tags:\r\n  - a\r\n  - b\r\n", b"tags:\r\n  - a\r\n  - b\r\n  - c\r\n"
    ), new


@pytest.mark.parametrize(
    "flow",
    [pytest.param("tags: [a, b]\n", id="flow"), pytest.param("'tags': [a, b]\n", id="quoted")],
)
def test_flow_list_tags_stay_flow(repo: Path, flow: str) -> None:
    """Control: a flow list stays a flow list, on its own line."""
    path = _write(repo, FM_HEAD + flow + FM_TAIL)
    old = path.read_text(encoding="utf-8")
    _service(repo).update_item(ITEM_ID, tags=["a", "b", "c"], commit=False)
    new = path.read_text(encoding="utf-8")
    key = flow.split(":")[0]
    assert new == old.replace(flow, f"{key}: [a, b, c]\n"), new


# ---------------------------------------------------------------------------
# 3. the other writers: rank_item and move_item(assignee=...)
# ---------------------------------------------------------------------------


def test_rank_quoted_keys_in_place(repo: Path) -> None:
    path = _write(
        repo,
        FM_HEAD + FM_TAIL + "'priority_rank': 5\n" + '"value_summary": "old value"\n',
    )
    old = path.read_text(encoding="utf-8")
    _service(repo).rank_item(ITEM_ID, 2, value_summary="new value", commit=False)
    new = path.read_text(encoding="utf-8")
    _assert_same_line_changed(old, new, {"priority_rank": 2, "value_summary": "new value"})


def test_move_assign_quoted_keys_in_place(repo: Path) -> None:
    path = _write(
        repo,
        'id: FEAT-001\ntitle: "Seed"\ntype: feature\n"status": backlog\n'
        "priority: high\n'assignee': null\ndepends_on: []\n",
    )
    old = path.read_text(encoding="utf-8")
    _service(repo).move_item(
        ITEM_ID,
        WorkItemStatus.READY,
        assignee="carol",
        commit=False,
        validate_workflow=False,
        skip_wip_check=True,
        skip_gates=True,
    )
    new = path.read_text(encoding="utf-8")
    _assert_no_duplicate_keys(new)
    fm_old, fm_new = _fm_lines(old), _fm_lines(new)
    assert len(fm_old) == len(fm_new), f"frontmatter gained lines:\n{new}"
    changed = {
        i: yaml.safe_load(b) for i, (a, b) in enumerate(zip(fm_old, fm_new, strict=True)) if a != b
    }
    assert changed == {3: {"status": "ready"}, 5: {"assignee": "carol"}}, changed
    # the body is kept; move only appends its history block after it
    assert new.split("\n---\n", 1)[1].startswith(BODY), new


def test_move_quoted_status_without_assign(repo: Path) -> None:
    path = _write(repo, 'id: FEAT-001\ntitle: "Seed"\ntype: feature\n"status": backlog\n' + FM_TAIL)
    _service(repo).move_item(
        ITEM_ID,
        WorkItemStatus.READY,
        commit=False,
        validate_workflow=False,
        skip_wip_check=True,
        skip_gates=True,
    )
    new = path.read_text(encoding="utf-8")
    _assert_no_duplicate_keys(new)
    assert '"status": ready' in _fm_lines(new) or "status: ready" in _fm_lines(new), new
    assert _fm_lines(new).index(next(line for line in _fm_lines(new) if "status" in line)) == 3, new
