"""Issue #169 — epic linking must replace a block-style `related:` list whole,
and treat `related: null` / an empty `related:` as an empty list.

Covers both linking paths: `epic add` and `epic create --items`.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from yurtle_kanban.cli import main
from yurtle_kanban.config import KanbanConfig, PathConfig

# ---------------------------------------------------------------------------
# Fixtures (copied from tests/test_epic_commands.py to stay self-contained)
# ---------------------------------------------------------------------------


@pytest.fixture
def software_repo(tmp_path: Path) -> Path:
    """A minimal git repo with the software theme (epics)."""
    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=tmp_path, capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path, capture_output=True, check=True,
    )
    (tmp_path / ".kanban").mkdir()
    (tmp_path / "work" / "features").mkdir(parents=True)
    (tmp_path / "work" / "epics").mkdir(parents=True)
    (tmp_path / "work" / "bugs").mkdir(parents=True)
    config = KanbanConfig(
        theme="software",
        paths=PathConfig(
            root="work/",
            scan_paths=["work/features/", "work/epics/", "work/bugs/"],
        ),
    )
    config.save(tmp_path / ".kanban" / "config.yaml")
    return tmp_path


@pytest.fixture
def software_runner(software_repo: Path, monkeypatch: pytest.MonkeyPatch) -> CliRunner:
    """Click runner with cwd set to the software repo."""
    from yurtle_kanban import config as config_mod

    config_mod._theme_cache.clear()
    monkeypatch.chdir(software_repo)
    return CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HEAD = (
    "---\n"
    "id: FEAT-001\n"
    'title: "Linked item"\n'
    "type: feature\n"
    "status: backlog\n"
    "priority: medium\n"
)

# Keys after `related:` plus a body that itself contains list-looking lines
# which must never be touched.
_TAIL = (
    "depends_on: [FEAT-003]\n"
    "assignee: null\n"
    "---\n"
    "\n"
    "# Linked item\n"
    "\n"
    "Notes:\n"
    "related:\n"
    "  - FEAT-010\n"
    "- FEAT-010\n"
)


def _write_item(repo: Path, related_block: str | None) -> Path:
    """Write FEAT-001 verbatim; `related_block` is the raw `related:` text (or None)."""
    path = repo / "work" / "features" / "FEAT-001-linked-item.md"
    path.write_text(_HEAD + (related_block or "") + _TAIL)
    return path


def _split(text: str) -> tuple[str, str]:
    """Return (frontmatter text, everything from the closing --- on)."""
    assert text.startswith("---\n"), text[:200]
    end = text.index("\n---\n", 4)
    return text[4 : end + 1], text[end + 1 :]


def _frontmatter(path: Path) -> dict:
    """Parse the item's frontmatter; unparseable YAML is an assertion failure."""
    text = path.read_text()
    fm_text, _ = _split(text)
    try:
        fm = yaml.safe_load(fm_text)
    except yaml.YAMLError as exc:
        raise AssertionError(
            f"frontmatter is not valid YAML after linking: {exc}\n{text}"
        ) from None
    assert isinstance(fm, dict), text
    return fm


def _invoke(runner: CliRunner, args: list[str]):
    """Invoke the CLI; an uncaught exception is an assertion failure, not an error."""
    result = runner.invoke(main, args)
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        raise AssertionError(
            f"`{' '.join(args)}` crashed: {type(result.exception).__name__}: "
            f"{result.exception}\n{result.output}"
        )
    assert result.exit_code == 0, result.output
    return result


def _link(runner: CliRunner, how: str) -> None:
    """Link FEAT-001 to EPIC-001 via `epic add` or `epic create --items`."""
    if how == "add":
        _invoke(runner, ["epic", "create", "Big Project"])
        _invoke(runner, ["epic", "add", "EPIC-001", "FEAT-001"])
    else:
        result = _invoke(runner, ["epic", "create", "Big Project", "--items", "FEAT-001"])
        assert "Linked FEAT-001" in result.output, result.output


def _json(output: str):
    """Parse CLI JSON output; anything else (e.g. a parse warning) is an assertion failure."""
    try:
        return json.loads(output)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"output is not clean JSON ({exc}):\n{output}") from None


def _assert_listed(runner: CliRunner, expected_related: list[str]) -> None:
    listed = _invoke(runner, ["list", "--json"])
    ids = [i["id"] for i in _json(listed.output)]
    assert "FEAT-001" in ids, f"FEAT-001 dropped out of `list`: {ids}"
    shown = _invoke(runner, ["show", "FEAT-001", "--json"])
    assert _json(shown.output)["related"] == expected_related


_LINK_PATHS = [
    pytest.param("add", id="epic-add"),
    pytest.param("create-items", id="epic-create-items"),
]

_BLOCK_LISTS = [
    pytest.param("related:\n  - FEAT-009\n  - FEAT-010\n", id="indented-block"),
    pytest.param("related:\n- FEAT-009\n- FEAT-010\n", id="column0-block"),
]

_EMPTY_RELATED = [
    pytest.param("related: null\n", id="null"),
    pytest.param("related:\n", id="empty-value"),
]


# ---------------------------------------------------------------------------
# 1. Block-style related list
# ---------------------------------------------------------------------------


class TestBlockRelatedListIssue169:
    """A block-style `related:` list is replaced whole when an epic is linked."""

    @pytest.mark.parametrize("block", _BLOCK_LISTS)
    @pytest.mark.parametrize("how", _LINK_PATHS)
    def test_block_list_parses_and_gains_epic(self, software_runner, software_repo, how, block):
        path = _write_item(software_repo, block)
        assert _frontmatter(path)["related"] == ["FEAT-009", "FEAT-010"]  # precondition
        _link(software_runner, how)

        fm = _frontmatter(path)
        assert fm["related"] == ["FEAT-009", "FEAT-010", "EPIC-001"]
        assert fm["depends_on"] == ["FEAT-003"]
        assert fm["assignee"] is None
        assert fm["id"] == "FEAT-001"

    @pytest.mark.parametrize("block", _BLOCK_LISTS)
    @pytest.mark.parametrize("how", _LINK_PATHS)
    def test_block_list_leaves_no_continuation_lines(
        self, software_runner, software_repo, how, block
    ):
        path = _write_item(software_repo, block)
        _link(software_runner, how)

        fm_text, _ = _split(path.read_text())
        leftovers = [ln for ln in fm_text.splitlines() if re.match(r"^\s*-\s", ln)]
        assert not leftovers, f"leftover block-list lines in frontmatter: {leftovers}\n{fm_text}"
        assert fm_text.count("FEAT-009") == 1, fm_text
        assert fm_text.count("FEAT-010") == 1, fm_text
        assert fm_text.count("EPIC-001") == 1, fm_text
        assert len(re.findall(r"^related:", fm_text, re.MULTILINE)) == 1, fm_text

    @pytest.mark.parametrize("block", _BLOCK_LISTS)
    @pytest.mark.parametrize("how", _LINK_PATHS)
    def test_block_list_item_still_listed(self, software_runner, software_repo, how, block):
        _write_item(software_repo, block)
        _link(software_runner, how)
        _assert_listed(software_runner, ["FEAT-009", "FEAT-010", "EPIC-001"])

    @pytest.mark.parametrize("block", _BLOCK_LISTS)
    def test_block_list_linked_twice_no_duplicate(self, software_runner, software_repo, block):
        path = _write_item(software_repo, block)
        _link(software_runner, "add")
        _invoke(software_runner, ["epic", "add", "EPIC-001", "FEAT-001"])
        assert _frontmatter(path)["related"] == ["FEAT-009", "FEAT-010", "EPIC-001"]


# ---------------------------------------------------------------------------
# 2. related: null / empty value
# ---------------------------------------------------------------------------


class TestNullRelatedIssue169:
    """`related: null` and an empty `related:` are treated as an empty list."""

    @pytest.mark.parametrize("related", _EMPTY_RELATED)
    @pytest.mark.parametrize("how", _LINK_PATHS)
    def test_null_related_links_without_crash(
        self, software_runner, software_repo, how, related
    ):
        path = _write_item(software_repo, related)
        assert _frontmatter(path)["related"] is None  # precondition
        _link(software_runner, how)

        fm = _frontmatter(path)
        assert fm["related"] == ["EPIC-001"]
        assert fm["depends_on"] == ["FEAT-003"]
        assert fm["assignee"] is None

    @pytest.mark.parametrize("related", _EMPTY_RELATED)
    @pytest.mark.parametrize("how", _LINK_PATHS)
    def test_null_related_item_still_listed(self, software_runner, software_repo, how, related):
        _write_item(software_repo, related)
        _link(software_runner, how)
        _assert_listed(software_runner, ["EPIC-001"])


# ---------------------------------------------------------------------------
# 3. Following key and body are byte-identical
# ---------------------------------------------------------------------------


class TestFollowingKeysAndBodyUntouchedIssue169:
    """Everything outside the `related:` value is byte-identical after linking."""

    @pytest.mark.parametrize("block", [*_BLOCK_LISTS, *_EMPTY_RELATED])
    @pytest.mark.parametrize("how", _LINK_PATHS)
    def test_rest_of_file_byte_identical(self, software_runner, software_repo, how, block):
        path = _write_item(software_repo, block)
        _link(software_runner, how)

        text = path.read_text()
        assert text.startswith(_HEAD + "related:"), text
        assert text.endswith(_TAIL), (
            f"keys after `related:` or the body changed:\n{text}"
        )
        # exactly one line sits between the head and the tail: the new related value
        middle = text[len(_HEAD) : len(text) - len(_TAIL)]
        assert middle.count("\n") == 1 and middle.endswith("\n"), repr(middle)


# ---------------------------------------------------------------------------
# Negative controls (must already be green)
# ---------------------------------------------------------------------------


class TestControlsIssue169:
    """Behaviour that already works and must stay exactly as it is."""

    @pytest.mark.parametrize("how", _LINK_PATHS)
    def test_control_flow_list_written_exactly(self, software_runner, software_repo, how):
        path = _write_item(software_repo, "related: [FEAT-002]\n")
        _link(software_runner, how)
        assert path.read_text() == _HEAD + "related: [FEAT-002, EPIC-001]\n" + _TAIL

    @pytest.mark.parametrize("how", _LINK_PATHS)
    def test_control_no_related_line_gets_one(self, software_runner, software_repo, how):
        path = _write_item(software_repo, None)
        _link(software_runner, how)
        assert "\nrelated: [EPIC-001]\n" in path.read_text()
        fm = _frontmatter(path)
        assert fm["related"] == ["EPIC-001"]
        assert fm["depends_on"] == ["FEAT-003"]
        assert path.read_text().endswith(_TAIL.split("---\n", 1)[1])

    def test_control_flow_list_linked_twice_no_duplicate(self, software_runner, software_repo):
        path = _write_item(software_repo, "related: [FEAT-002]\n")
        _link(software_runner, "add")
        result = _invoke(software_runner, ["epic", "add", "EPIC-001", "FEAT-001"])
        assert "already linked" in result.output
        assert path.read_text() == _HEAD + "related: [FEAT-002, EPIC-001]\n" + _TAIL

    def test_control_flow_list_item_listed(self, software_runner, software_repo):
        _write_item(software_repo, "related: [FEAT-002]\n")
        _link(software_runner, "add")
        _assert_listed(software_runner, ["FEAT-002", "EPIC-001"])
