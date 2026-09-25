"""Issue #188 — follow-ups from the review of PR #187 (#169).

1. `_add_or_update_frontmatter_field` appending a missing key keeps the blank
   lines that sit right before the closing `---` (the new key goes right after
   the last non-blank frontmatter line).
2. Epic linking on an item whose frontmatter exists but doesn't parse says so,
   with the parse reason (#139's `_note_unparseable` wording), not
   "No frontmatter"; the file is unchanged.
3. A `related:` that is a mapping or another non-list, non-string value (a
   number) is refused with a clear message naming `related`; the file is
   unchanged.
"""

from __future__ import annotations

import io
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner
from rich.console import Console

from yurtle_kanban import epic_commands
from yurtle_kanban.cli import main
from yurtle_kanban.config import KanbanConfig, PathConfig
from yurtle_kanban.service import KanbanService

# ---------------------------------------------------------------------------
# Fixtures (as in tests/issues/test_169_epic_related_block_list.py)
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


@pytest.fixture
def epic_out(monkeypatch: pytest.MonkeyPatch) -> io.StringIO:
    """Capture what epic_commands prints (its module-level rich console)."""
    buf = io.StringIO()
    monkeypatch.setattr(
        epic_commands, "console", Console(file=buf, width=300, color_system=None)
    )
    return buf


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
_TAIL = "---\n\n# Linked item\n\nBody text.\n"


def _item_path(repo: Path) -> Path:
    return repo / "work" / "features" / "FEAT-001-linked-item.md"


def _write_item(repo: Path, extra: str) -> Path:
    path = _item_path(repo)
    path.write_bytes((_HEAD + extra + _TAIL).encode("utf-8"))
    return path


def _invoke(runner: CliRunner, args: list[str]):
    """Invoke the CLI; an uncaught exception is an assertion failure, not an error."""
    result = runner.invoke(main, args)
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        raise AssertionError(
            f"`{' '.join(args)}` crashed: {type(result.exception).__name__}: "
            f"{result.exception}\n{result.output}"
        )
    return result


def _service(repo: Path) -> KanbanService:
    config = KanbanConfig.load(repo / ".kanban" / "config.yaml")
    service = KanbanService(config, repo)
    service.scan()
    return service


# ---------------------------------------------------------------------------
# 1. Appending a missing key keeps blank lines before the closing ---
# ---------------------------------------------------------------------------


def _svc() -> KanbanService:
    return KanbanService.__new__(KanbanService)  # the writer uses no instance state


class TestAppendKeepsBlankLinesIssue188:
    def test_one_blank_line_kept(self):
        content = "---\nid: X-1\ntitle: T\n\n---\nbody\n"
        out = _svc()._add_or_update_frontmatter_field(content, "priority", "high")
        assert out == "---\nid: X-1\ntitle: T\npriority: high\n\n---\nbody\n"

    def test_two_blank_lines_kept(self):
        content = "---\nid: X-1\ntitle: T\n\n\n---\nbody\n"
        out = _svc()._add_or_update_frontmatter_field(content, "priority", "high")
        assert out == "---\nid: X-1\ntitle: T\npriority: high\n\n\n---\nbody\n"

    def test_blank_line_between_keys_and_before_close(self):
        content = "---\nid: X-1\n\ntitle: T\n\n---\n"
        out = _svc()._add_or_update_frontmatter_field(content, "priority", "high")
        assert out == "---\nid: X-1\n\ntitle: T\npriority: high\n\n---\n"

    def test_crlf_file_keeps_blank_line(self, tmp_path: Path):
        path = tmp_path / "item.md"
        path.write_bytes(b"---\r\nid: X-1\r\ntitle: T\r\n\r\n---\r\nbody\r\n")
        text, eol = KanbanService._read_item_text(path)
        text = _svc()._add_or_update_frontmatter_field(text, "priority", "high")
        KanbanService._write_item_text(path, text, eol)
        assert path.read_bytes() == (
            b"---\r\nid: X-1\r\ntitle: T\r\npriority: high\r\n\r\n---\r\nbody\r\n"
        )

    # -- controls ---------------------------------------------------------

    def test_control_no_blank_line_unchanged(self):
        content = "---\nid: X-1\ntitle: T\n---\nbody\n"
        out = _svc()._add_or_update_frontmatter_field(content, "priority", "high")
        assert out == "---\nid: X-1\ntitle: T\npriority: high\n---\nbody\n"

    def test_control_update_existing_key_keeps_blank_line(self):
        content = "---\nid: X-1\npriority: low\n\n---\nbody\n"
        out = _svc()._add_or_update_frontmatter_field(content, "priority", "high")
        assert out == "---\nid: X-1\npriority: high\n\n---\nbody\n"

    def test_control_epic_link_on_item_with_trailing_blank_line(
        self, software_runner, software_repo
    ):
        path = _write_item(software_repo, "\n")
        _invoke(software_runner, ["epic", "create", "Big Project"])
        result = _invoke(software_runner, ["epic", "add", "EPIC-001", "FEAT-001"])
        assert result.exit_code == 0, result.output
        assert "related: [EPIC-001]" in path.read_text()

    def test_epic_link_keeps_trailing_blank_line(self, software_runner, software_repo):
        path = _write_item(software_repo, "\n")
        _invoke(software_runner, ["epic", "create", "Big Project"])
        _invoke(software_runner, ["epic", "add", "EPIC-001", "FEAT-001"])
        assert path.read_text() == _HEAD + "related: [EPIC-001]\n\n" + _TAIL


# ---------------------------------------------------------------------------
# 2. Frontmatter exists but doesn't parse
# ---------------------------------------------------------------------------

_BROKEN = (
    "---\n"
    "id: FEAT-001\n"
    "title: [unclosed\n"
    "type: feature\n"
    "status: backlog\n"
    "---\n\n# Broken\n"
)


class TestUnparseableFrontmatterIssue188:
    """The item was scanned while good, then its frontmatter broke before linking.

    (A file that is broken at scan time never becomes an item, so the CLI stops
    at "not found" first; the message under test is `_update_item_related`'s.)
    """

    def _break_after_scan(self, repo: Path) -> tuple[KanbanService, Path]:
        path = _write_item(repo, "")
        service = _service(repo)
        assert "FEAT-001" in service._items  # precondition
        path.write_bytes(_BROKEN.encode("utf-8"))
        return service, path

    def test_says_does_not_parse(self, software_repo, epic_out):
        service, _ = self._break_after_scan(software_repo)
        linked = epic_commands._update_item_related(service, "FEAT-001", "EPIC-001")
        out = epic_out.getvalue()
        assert linked is False
        assert "No frontmatter" not in out, out
        assert "doesn't parse" in out or "does not parse" in out, out
        assert "FEAT-001" in out, out

    def test_gives_parse_reason(self, software_repo, epic_out):
        service, _ = self._break_after_scan(software_repo)
        epic_commands._update_item_related(service, "FEAT-001", "EPIC-001")
        out = epic_out.getvalue()
        # #139's reason text for a YAML error
        assert "YAML error" in out, out

    def test_file_unchanged(self, software_repo, epic_out):
        service, path = self._break_after_scan(software_repo)
        epic_commands._update_item_related(service, "FEAT-001", "EPIC-001")
        assert path.read_bytes() == _BROKEN.encode("utf-8")

    # -- control ----------------------------------------------------------

    def test_control_truly_no_frontmatter_says_no_frontmatter(self, software_repo, epic_out):
        path = _write_item(software_repo, "")
        service = _service(software_repo)
        plain = "# Just a note\n\nNo frontmatter here.\n"
        path.write_bytes(plain.encode("utf-8"))
        linked = epic_commands._update_item_related(service, "FEAT-001", "EPIC-001")
        out = epic_out.getvalue()
        assert linked is False
        assert "No frontmatter" in out, out
        assert path.read_bytes() == plain.encode("utf-8")


# ---------------------------------------------------------------------------
# 3. related: mapping / number is refused
# ---------------------------------------------------------------------------

_BAD_RELATED = [
    pytest.param("related: {a: 1}\n", id="flow-mapping"),
    pytest.param("related:\n  a: 1\n  b: 2\n", id="block-mapping"),
    pytest.param("related: 5\n", id="number"),
]

_GOOD_RELATED = [
    pytest.param(None, "EPIC-001", id="absent"),
    pytest.param("related: null\n", "EPIC-001", id="null"),
    pytest.param("related: FEAT-002\n", "FEAT-002, EPIC-001", id="string"),
    pytest.param("related: [FEAT-002]\n", "FEAT-002, EPIC-001", id="flow"),
    pytest.param("related:\n  - FEAT-002\n", "FEAT-002, EPIC-001", id="block"),
]

_LINK_PATHS = [
    pytest.param("add", id="epic-add"),
    pytest.param("create-items", id="epic-create-items"),
]


def _link(runner: CliRunner, how: str):
    if how == "add":
        _invoke(runner, ["epic", "create", "Big Project"])
        return _invoke(runner, ["epic", "add", "EPIC-001", "FEAT-001"])
    return _invoke(runner, ["epic", "create", "Big Project", "--items", "FEAT-001"])


class TestNonListRelatedRefusedIssue188:
    @pytest.mark.parametrize("related", _BAD_RELATED)
    @pytest.mark.parametrize("how", _LINK_PATHS)
    def test_cli_refuses_and_file_unchanged(
        self, software_runner, software_repo, how, related
    ):
        path = _write_item(software_repo, related)
        before = path.read_bytes()
        result = _link(software_runner, how)
        assert path.read_bytes() == before, path.read_text()
        assert "Linked FEAT-001" not in result.output, result.output
        assert "already linked" not in result.output, result.output
        assert "related" in result.output, result.output

    @pytest.mark.parametrize("related", _BAD_RELATED)
    def test_direct_call_refuses_and_names_related(self, software_repo, epic_out, related):
        path = _write_item(software_repo, related)
        before = path.read_bytes()
        service = _service(software_repo)
        linked = epic_commands._update_item_related(service, "FEAT-001", "EPIC-001")
        out = epic_out.getvalue()
        assert linked is False
        assert path.read_bytes() == before, path.read_text()
        assert "related" in out, out
        assert "FEAT-001" in out, out

    # -- controls (#169 behaviour) ------------------------------------------

    @pytest.mark.parametrize(("related", "flow"), _GOOD_RELATED)
    @pytest.mark.parametrize("how", _LINK_PATHS)
    def test_control_list_like_related_links(
        self, software_runner, software_repo, how, related, flow
    ):
        path = _write_item(software_repo, related or "")
        result = _link(software_runner, how)
        assert result.exit_code == 0, result.output
        assert "Linked" in result.output, result.output
        text = path.read_text()
        assert f"related: [{flow}]\n" in text, text
        assert text.endswith(_TAIL), text
