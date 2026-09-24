"""Issue #172 — user text containing a lone surrogate (U+D800–U+DFFF) must be
rejected before anything is written.

Python decodes undecodable argv bytes with surrogateescape (b"\\xff" -> "\\udcff").
Such a string cannot be encoded as UTF-8, so today `create` crashes with
`UnicodeEncodeError: ... surrogates not allowed` and leaves a 0-byte item file.

Decided behaviour: the service entry points that take user text raise a
ValueError naming the field and containing "invalid UTF-8", and no file is
created or changed. The CLI exits non-zero with that message (no traceback);
the MCP tools return {"error": ...} with that message.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from yurtle_kanban.cli import main
from yurtle_kanban.config import KanbanConfig, PathConfig
from yurtle_kanban.models import WorkItemType
from yurtle_kanban.service import KanbanService

LONE = "a\udcffb"  # what surrogateescape yields for argv bytes b"a\xffb"
MSG = "invalid UTF-8"
SRC = Path(__file__).resolve().parents[2] / "src"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A minimal git repo with the software theme."""
    for args in (
        ["git", "init", "-b", "main"],
        ["git", "config", "user.email", "test@test.com"],
        ["git", "config", "user.name", "Test"],
    ):
        subprocess.run(args, cwd=tmp_path, capture_output=True, check=True)
    (tmp_path / ".kanban").mkdir()
    (tmp_path / "kanban-work" / "features").mkdir(parents=True)
    (tmp_path / "kanban-work" / "bugs").mkdir(parents=True)
    KanbanConfig(
        theme="software",
        paths=PathConfig(
            root="kanban-work/",
            scan_paths=["kanban-work/features/", "kanban-work/bugs/"],
        ),
    ).save(tmp_path / ".kanban" / "config.yaml")
    from yurtle_kanban import config as config_mod

    config_mod._theme_cache.clear()
    return tmp_path


def _service(repo: Path) -> KanbanService:
    return KanbanService(KanbanConfig.load(repo / ".kanban" / "config.yaml"), repo)


@pytest.fixture
def svc(repo: Path) -> KanbanService:
    return _service(repo)


@pytest.fixture
def existing(svc: KanbanService) -> str:
    """An already-created item whose bytes must stay identical."""
    item = svc.create_item(WorkItemType.FEATURE, "Existing item", description="Body")
    return item.id


def _snapshot(repo: Path) -> dict[str, bytes]:
    """Every file under kanban-work/ with its bytes."""
    root = repo / "kanban-work"
    return {str(p.relative_to(repo)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def _assert_rejected(excinfo: pytest.ExceptionInfo[BaseException], field: str) -> None:
    msg = str(excinfo.value)
    assert MSG in msg, f"expected {MSG!r} in message, got: {msg!r}"
    assert field in msg, f"expected field {field!r} named in message, got: {msg!r}"


# ---------------------------------------------------------------------------
# Service: create_item
# ---------------------------------------------------------------------------


class TestCreateItemRejectsLoneSurrogate:
    @pytest.mark.parametrize(
        ("field", "kwargs"),
        [
            ("title", {"title": LONE}),
            ("description", {"title": "ok", "description": LONE}),
            ("assignee", {"title": "ok", "assignee": LONE}),
            ("tags", {"title": "ok", "tags": ["fine", LONE]}),
        ],
    )
    def test_rejected_and_nothing_written(
        self, repo: Path, svc: KanbanService, field: str, kwargs: dict
    ) -> None:
        before = _snapshot(repo)
        with pytest.raises(ValueError) as excinfo:
            svc.create_item(WorkItemType.FEATURE, **kwargs)
        _assert_rejected(excinfo, field)
        assert _snapshot(repo) == before, "a file was created or changed"

    def test_no_zero_byte_file_left(self, repo: Path, svc: KanbanService) -> None:
        with pytest.raises(ValueError):
            svc.create_item(WorkItemType.FEATURE, LONE)
        empties = [p for p in (repo / "kanban-work").rglob("*.md") if p.stat().st_size == 0]
        assert empties == []


# ---------------------------------------------------------------------------
# Service: update_item
# ---------------------------------------------------------------------------


class TestUpdateItemRejectsLoneSurrogate:
    @pytest.mark.parametrize(
        ("field", "kwargs"),
        [
            ("title", {"title": LONE}),
            ("description", {"description": LONE}),
            ("assignee", {"assignee": LONE}),
            ("tags", {"tags": ["fine", LONE]}),
        ],
    )
    def test_rejected_and_item_unchanged(
        self, repo: Path, svc: KanbanService, existing: str, field: str, kwargs: dict
    ) -> None:
        before = _snapshot(repo)
        with pytest.raises(ValueError) as excinfo:
            svc.update_item(existing, commit=False, **kwargs)
        _assert_rejected(excinfo, field)
        assert _snapshot(repo) == before, "item file bytes changed"


# ---------------------------------------------------------------------------
# Service: add_comment
# ---------------------------------------------------------------------------


class TestAddCommentRejectsLoneSurrogate:
    @pytest.mark.parametrize(
        ("field", "content", "author"),
        [
            ("comment", LONE, "Mini"),
            ("author", "fine comment", LONE),
        ],
    )
    def test_rejected_and_item_unchanged(
        self,
        repo: Path,
        svc: KanbanService,
        existing: str,
        field: str,
        content: str,
        author: str,
    ) -> None:
        before = _snapshot(repo)
        with pytest.raises(ValueError) as excinfo:
            svc.add_comment(existing, content, author, commit=False)
        _assert_rejected(excinfo, field)
        assert _snapshot(repo) == before, "item file bytes changed"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestCliCreateRejectsLoneSurrogate:
    def test_subprocess_invalid_utf8_argv(self, repo: Path) -> None:
        """The realistic path: raw invalid UTF-8 bytes in argv."""
        before = _snapshot(repo)
        env = {**os.environ, "PYTHONPATH": str(SRC)}
        env.pop("PYTHONIOENCODING", None)
        proc = subprocess.run(
            [
                os.fsencode(sys.executable),
                b"-c",
                b"from yurtle_kanban.cli import main; main()",
                b"create",
                b"feature",
                b"a\xffb",
            ],
            cwd=repo,
            env=env,
            capture_output=True,
        )
        out = (proc.stdout + proc.stderr).decode("utf-8", "replace")
        assert proc.returncode != 0, out
        assert "Traceback" not in out, out
        assert MSG in out, out
        assert _snapshot(repo) == before, "an item file was left behind"

    def test_clirunner_str_arg(self, repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(repo)
        before = _snapshot(repo)
        result = CliRunner().invoke(main, ["create", "feature", LONE])
        assert result.exit_code != 0, result.output
        assert not isinstance(result.exception, UnicodeEncodeError), repr(result.exception)
        assert "Traceback" not in result.output, result.output
        assert MSG in result.output, result.output
        assert _snapshot(repo) == before, "an item file was left behind"


# ---------------------------------------------------------------------------
# MCP
# ---------------------------------------------------------------------------


def _mcp(repo: Path):
    mcp_server = pytest.importorskip("yurtle_kanban.mcp.server")
    return mcp_server.KanbanMCPServer(repo_root=repo)


class TestMcpRejectsLoneSurrogate:
    @pytest.mark.parametrize(
        "extra",
        [
            {"title": LONE},
            {"title": "ok", "description": LONE},
            {"title": "ok", "assignee": LONE},
            {"title": "ok", "tags": ["fine", LONE]},
        ],
    )
    def test_create_item(self, repo: Path, monkeypatch: pytest.MonkeyPatch, extra: dict) -> None:
        monkeypatch.chdir(repo)
        server = _mcp(repo)
        before = _snapshot(repo)
        result = server.handle_tool_call("kanban_create_item", {"item_type": "feature", **extra})
        assert not result.get("success"), result
        assert MSG in result.get("error", ""), result
        assert _snapshot(repo) == before, "a file was created or changed"

    @pytest.mark.parametrize(
        "extra",
        [
            {"title": LONE},
            {"description": LONE},
            {"assignee": LONE},
            {"tags": ["fine", LONE]},
        ],
    )
    def test_update_item(
        self, repo: Path, existing: str, monkeypatch: pytest.MonkeyPatch, extra: dict
    ) -> None:
        monkeypatch.chdir(repo)
        server = _mcp(repo)
        before = _snapshot(repo)
        result = server.handle_tool_call("kanban_update_item", {"item_id": existing, **extra})
        assert not result.get("success"), result
        assert MSG in result.get("error", ""), result
        assert _snapshot(repo) == before, "item file bytes changed"

    @pytest.mark.parametrize(
        "extra",
        [{"comment": LONE}, {"comment": "fine", "author": LONE}],
    )
    def test_add_comment(
        self, repo: Path, existing: str, monkeypatch: pytest.MonkeyPatch, extra: dict
    ) -> None:
        monkeypatch.chdir(repo)
        server = _mcp(repo)
        before = _snapshot(repo)
        result = server.handle_tool_call("kanban_add_comment", {"item_id": existing, **extra})
        assert not result.get("success"), result
        assert MSG in result.get("error", ""), result
        assert _snapshot(repo) == before, "item file bytes changed"


# ---------------------------------------------------------------------------
# Controls — valid non-ASCII text keeps working (pass before AND after the fix)
# ---------------------------------------------------------------------------

VALID_TEXTS = ["café ☕ 日本", "clef 𝄞 astral"]


class TestValidNonAsciiControls:
    @pytest.mark.parametrize("text", VALID_TEXTS)
    def test_create_round_trips(self, repo: Path, svc: KanbanService, text: str) -> None:
        item = svc.create_item(
            WorkItemType.FEATURE, text, description=f"desc {text}", tags=[text]
        )
        assert item.file_path.stat().st_size > 0
        assert text in item.file_path.read_text(encoding="utf-8")
        reloaded = _service(repo).get_item(item.id)
        assert reloaded is not None
        assert reloaded.title == text

    @pytest.mark.parametrize("text", VALID_TEXTS)
    def test_update_round_trips(
        self, repo: Path, svc: KanbanService, existing: str, text: str
    ) -> None:
        svc.update_item(existing, title=text, description=f"desc {text}", commit=False)
        reloaded = _service(repo).get_item(existing)
        assert reloaded is not None
        assert reloaded.title == text

    @pytest.mark.parametrize("text", VALID_TEXTS)
    def test_add_comment_writes(
        self, repo: Path, svc: KanbanService, existing: str, text: str
    ) -> None:
        item = svc.add_comment(existing, f"note {text}", "Mini", commit=False)
        assert f"note {text}" in item.file_path.read_text(encoding="utf-8")

    @pytest.mark.parametrize("text", VALID_TEXTS)
    def test_cli_create(self, repo: Path, monkeypatch: pytest.MonkeyPatch, text: str) -> None:
        monkeypatch.chdir(repo)
        result = CliRunner().invoke(main, ["create", "feature", text])
        assert result.exit_code == 0, result.output
        reloaded = _service(repo).get_item("FEAT-001")
        assert reloaded is not None
        assert reloaded.title == text

    @pytest.mark.parametrize("text", VALID_TEXTS)
    def test_mcp_create(self, repo: Path, monkeypatch: pytest.MonkeyPatch, text: str) -> None:
        monkeypatch.chdir(repo)
        result = _mcp(repo).handle_tool_call(
            "kanban_create_item", {"item_type": "feature", "title": text}
        )
        assert result.get("success"), result
        assert result["item"]["title"] == text
