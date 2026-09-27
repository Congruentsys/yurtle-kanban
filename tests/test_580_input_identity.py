"""#580 — input and identity.

Two halves:

- **Identity.** The *actor* (who performed the action) resolves through one
  function, `resolve_actor`: `--agent`, then `$YURTLE_AGENT`, then git
  `user.name` (unless `allow_git_fallback=False`), then an error. The *assignee*
  (who holds the item) is never defaulted. `kb:by` records the actor. Identity
  inputs are refused when empty, whitespace-only or holding a control character.
- **Free text.** Every free-text `--X` has a `--X-file PATH|-` twin, read by one
  helper: stdin refused on a TTY, read before any subprocess, strict UTF-8,
  CRLF/CR -> LF, trailing newlines stripped, empty refused, otherwise verbatim.

Plus: `stdin=subprocess.DEVNULL` on every git subprocess, the flag renames
(`-a` gone everywhere), and a skill lint.

The helpers live in `yurtle_kanban.inputs` (this file's choice; the spec names
the functions but not their module).
"""

from __future__ import annotations

import ast
import importlib
import json
import os
import pty
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from yurtle_kanban.cli import main
from yurtle_kanban.config import KanbanConfig, PathConfig

SRC = Path(__file__).resolve().parent.parent / "src"
SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"
KB = "https://yurtle.dev/kanban/"

# A body an agent's shell would mangle if it were double-quoted: command
# substitution, backticks, a variable, backslashes, both quote kinds.
NASTY = (
    "Deploy note: run $(touch pwned) first.\n"
    "Then `touch pwned2` and echo $HOME ${PATH}.\n"
    "  indented line with \\ backslash, 'single' and \"double\" quotes\n"
    "last line"
)


# ---------------------------------------------------------------------------
# fixtures and helpers
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, check=True)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A git repo with a software-theme board, git user `Test`; cwd is the repo."""
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.email", "test@test.com")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "config", "commit.gpgsign", "false")
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
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "init")
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def no_global_git(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch):
    """Hide the machine's global/system git config (it may set user.name)."""
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


def _inputs() -> Any:
    """The #580 helper module; missing is a plain test failure, not an error."""
    try:
        return importlib.import_module("yurtle_kanban.inputs")
    except ModuleNotFoundError as exc:
        pytest.fail(f"yurtle_kanban.inputs is missing (#580): {exc}")


def _invoke(args: list[str], input: bytes | str | None = None):
    return CliRunner().invoke(main, args, input=input)


def _ok(args: list[str], input: bytes | str | None = None):
    result = _invoke(args, input)
    assert result.exit_code == 0, (
        f"{args} exited {result.exit_code}: {result.exception!r}\n{result.output}"
    )
    return result


def _flat(text: str) -> str:
    """Output with rich's line wrapping undone."""
    return " ".join(text.split())


def _refused(args: list[str], *names: str, input: bytes | str | None = None):
    """exit 1, cleanly (no traceback), and the message names each of `names`."""
    result = _invoke(args, input)
    assert result.exit_code == 1, (
        f"{args} should be refused with exit 1, got {result.exit_code}\n{result.output}"
    )
    assert result.exception is None or isinstance(result.exception, SystemExit), (
        f"{args} crashed instead of refusing: {result.exception!r}"
    )
    out = _flat(result.output)
    for name in names:
        assert name in out, f"{args}: message should name {name!r}: {out!r}"
    return result


def _usage_error(args: list[str], input: bytes | str | None = None):
    result = _invoke(args, input)
    assert result.exit_code == 2, (
        f"{args} should be a click usage error (exit 2), got {result.exit_code}\n"
        f"{result.output}"
    )
    return result


def _item_file(repo: Path, item_id: str = "FEAT-001") -> Path:
    files = list((repo / "kanban-work" / "features").glob(f"{item_id}*.md"))
    assert len(files) == 1, files
    return files[0]


def _show(item_id: str = "FEAT-001") -> dict[str, Any]:
    return json.loads(_ok(["show", item_id, "--json"]).output)


def _by_values(path: Path) -> list[str]:
    """kb:by of every kb:statusChange node, in file order of the status block."""
    from rdflib import Graph, URIRef

    text = path.read_text()
    blocks = [
        b for b in re.findall(r"```yurtle\n(.*?)```", text, re.DOTALL)
        if "kb:statusChange" in b
    ]
    assert len(blocks) == 1, f"expected one statusChange block:\n{text}"
    g = Graph().parse(data=blocks[0], format="turtle", publicID=path.resolve().as_uri())
    nodes = list(g.objects(None, URIRef(KB + "statusChange")))
    by = URIRef(KB + "by")
    return sorted(str(v) for n in nodes for v in g.objects(n, by))


def _comment_headings(path: Path) -> list[str]:
    return re.findall(r"^### (.*?) \(\d{4}-\d\d-\d\d \d\d:\d\d\)$", path.read_text(), re.M)


# ---------------------------------------------------------------------------
# Acceptance 1 — resolve_actor precedence and refusals
# ---------------------------------------------------------------------------


class TestResolveActor:
    def test_flag_beats_env_beats_git(self, repo, monkeypatch):
        resolve_actor = _inputs().resolve_actor
        _git(repo, "config", "user.name", "GitUser")
        monkeypatch.setenv("YURTLE_AGENT", "EnvUser")
        assert resolve_actor("Flag") == "Flag"
        assert resolve_actor(None) == "EnvUser"
        monkeypatch.delenv("YURTLE_AGENT")
        assert resolve_actor(None) == "GitUser"

    def test_explicit_is_stripped(self, repo):
        assert _inputs().resolve_actor("  Mini  ") == "Mini"

    def test_env_is_stripped(self, repo, monkeypatch):
        monkeypatch.setenv("YURTLE_AGENT", " Mini\u0020")
        assert _inputs().resolve_actor(None) == "Mini"

    @pytest.mark.parametrize("value", ["", "  ", "\t"], ids=["empty", "spaces", "tab"])
    def test_env_set_but_blank_is_an_error_naming_the_variable(
        self, repo, monkeypatch, value,
    ):
        """Set-but-empty is refused, not treated as unset (git user is configured)."""
        resolve_actor = _inputs().resolve_actor
        monkeypatch.setenv("YURTLE_AGENT", value)
        with pytest.raises(ValueError, match="YURTLE_AGENT"):
            resolve_actor(None)

    @pytest.mark.parametrize("value", ["x\ny", "x\ry", "x\ty", "x\x1by"],
                             ids=["LF", "CR", "TAB", "ESC"])
    def test_env_with_control_character_is_an_error(self, repo, monkeypatch, value):
        resolve_actor = _inputs().resolve_actor
        monkeypatch.setenv("YURTLE_AGENT", value)
        with pytest.raises(ValueError, match="YURTLE_AGENT"):
            resolve_actor(None)

    @pytest.mark.parametrize("value", ["", "   ", "a\nb", "a\tb"],
                             ids=["empty", "spaces", "LF", "TAB"])
    def test_bad_explicit_is_an_error_naming_the_flag(self, repo, value):
        resolve_actor = _inputs().resolve_actor
        with pytest.raises(ValueError, match="--agent"):
            resolve_actor(value)

    def test_no_flag_no_env_empty_git_user_is_an_error(
        self, repo, no_global_git, monkeypatch,
    ):
        """No `"cli"`/`"agent"`/`"unknown"` fallback: nothing configured -> error."""
        resolve_actor = _inputs().resolve_actor
        _git(repo, "config", "--unset", "user.name")
        with pytest.raises(ValueError) as exc:
            resolve_actor(None)
        message = str(exc.value)
        assert "--agent" in message and "YURTLE_AGENT" in message, message

    def test_git_user_name_set_to_empty_string_is_an_error(self, repo, no_global_git):
        resolve_actor = _inputs().resolve_actor
        _git(repo, "config", "user.name", "")
        with pytest.raises(ValueError):
            resolve_actor(None)

    def test_no_git_fallback_with_only_git_configured_is_an_error(self, repo):
        resolve_actor = _inputs().resolve_actor
        with pytest.raises(ValueError) as exc:
            resolve_actor(None, allow_git_fallback=False)
        message = str(exc.value)
        assert "--agent" in message and "YURTLE_AGENT" in message, message

    def test_no_git_fallback_still_takes_flag_and_env(self, repo, monkeypatch):
        resolve_actor = _inputs().resolve_actor
        assert resolve_actor("Flag", allow_git_fallback=False) == "Flag"
        monkeypatch.setenv("YURTLE_AGENT", "EnvUser")
        assert resolve_actor(None, allow_git_fallback=False) == "EnvUser"

    def test_control_git_fallback_is_the_default(self, repo):
        assert _inputs().resolve_actor(None) == "Test"


# ---------------------------------------------------------------------------
# Acceptance 2 — same_actor
# ---------------------------------------------------------------------------


class TestSameActor:
    def test_strip_and_casefold(self):
        same_actor = _inputs().same_actor
        assert same_actor(" Mini ", "mini") is True
        assert same_actor("MINI", "mini") is True

    def test_different_actors(self):
        same_actor = _inputs().same_actor
        assert same_actor("Mini", "Mini2") is False
        assert same_actor("Mini", "M5") is False


# ---------------------------------------------------------------------------
# Acceptance 3 — create never defaults an assignee
# ---------------------------------------------------------------------------


class TestCreateHasNoDefaultAssignee:
    def test_create_with_env_actor_leaves_item_unassigned(self, repo, monkeypatch):
        monkeypatch.setenv("YURTLE_AGENT", "Mini")
        _ok(["create", "feature", "probe"])
        assert _show()["assignee"] is None
        assert "assignee: Mini" not in _item_file(repo).read_text()

    def test_create_push_with_env_actor_leaves_item_unassigned(self, repo, monkeypatch):
        monkeypatch.setenv("YURTLE_AGENT", "Mini")
        _ok(["create", "feature", "probe", "--push"])
        assert _show()["assignee"] is None

    def test_create_assign_sets_the_assignee(self, repo, monkeypatch):
        monkeypatch.setenv("YURTLE_AGENT", "Mini")
        _ok(["create", "feature", "probe", "--assign", "carol"])
        assert _show()["assignee"] == "carol"

    def test_create_assign_is_stripped(self, repo):
        _ok(["create", "feature", "probe", "--assign", "  carol  "])
        assert _show()["assignee"] == "carol"


# ---------------------------------------------------------------------------
# Acceptance 4 — kb:by is the actor, not the assignee
# ---------------------------------------------------------------------------


class TestKbByIsTheActor:
    def test_move_agent_on_item_held_by_someone_else(self, repo):
        _ok(["create", "feature", "probe", "--assign", "B"])
        _ok(["move", "FEAT-001", "ready", "--agent", "A", "--no-commit"])
        assert _by_values(_item_file(repo)) == ["A"]
        assert _show()["assignee"] == "B"

    def test_move_assign_without_agent_records_git_user_not_assignee(self, repo):
        _ok(["create", "feature", "probe"])
        _ok(["move", "FEAT-001", "ready", "--assign", "carol", "--no-commit"])
        assert _by_values(_item_file(repo)) == ["Test"]
        assert _show()["assignee"] == "carol"

    def test_move_assign_with_env_actor_records_env(self, repo, monkeypatch):
        monkeypatch.setenv("YURTLE_AGENT", "Mini")
        _ok(["create", "feature", "probe"])
        _ok(["move", "FEAT-001", "ready", "--assign", "carol", "--no-commit"])
        assert _by_values(_item_file(repo)) == ["Mini"]
        assert _show()["assignee"] == "carol"

    def test_move_does_not_default_the_assignee(self, repo, monkeypatch):
        """A reviewer's `move X done` must not reassign the item to the reviewer."""
        monkeypatch.setenv("YURTLE_AGENT", "Mini")
        _ok(["create", "feature", "probe"])
        _ok(["move", "FEAT-001", "ready", "--no-commit"])
        assert _show()["assignee"] is None

    def test_move_agent_is_stripped(self, repo):
        _ok(["create", "feature", "probe"])
        _ok(["move", "FEAT-001", "ready", "--agent", "  A  ", "--no-commit"])
        assert _by_values(_item_file(repo)) == ["A"]

    def test_move_with_no_resolvable_actor_is_refused_not_unknown(
        self, repo, no_global_git,
    ):
        _ok(["create", "feature", "probe"])
        _git(repo, "config", "--unset", "user.name")
        _refused(["move", "FEAT-001", "ready", "--no-commit"], "YURTLE_AGENT")
        assert "unknown" not in _item_file(repo).read_text()


# ---------------------------------------------------------------------------
# Acceptance 5 — comment author = actor (CLI and MCP)
# ---------------------------------------------------------------------------


class TestCommentActor:
    def test_env_actor_is_the_heading(self, repo, monkeypatch):
        monkeypatch.setenv("YURTLE_AGENT", "Mini")
        _ok(["create", "feature", "probe"])
        _ok(["comment", "FEAT-001", "--body", "hi"])
        assert _comment_headings(_item_file(repo)) == ["Mini"]

    def test_agent_flag_beats_env(self, repo, monkeypatch):
        monkeypatch.setenv("YURTLE_AGENT", "Mini")
        _ok(["create", "feature", "probe"])
        _ok(["comment", "FEAT-001", "--body", "hi", "--agent", "Z"])
        assert _comment_headings(_item_file(repo)) == ["Z"]

    def test_no_env_falls_back_to_git_user_never_cli(self, repo):
        _ok(["create", "feature", "probe"])
        _ok(["comment", "FEAT-001", "--body", "hi"])
        assert _comment_headings(_item_file(repo)) == ["Test"]

    def _mcp_comment(self, repo: Path, arguments: dict[str, Any]) -> dict[str, Any]:
        from yurtle_kanban.mcp.server import KanbanMCPServer

        return KanbanMCPServer(repo_root=repo).handle_tool_call(
            "kanban_add_comment", {"item_id": "FEAT-001", "comment": "hi", **arguments},
        )

    def test_mcp_comment_without_author_uses_env_actor(self, repo, monkeypatch):
        _ok(["create", "feature", "probe"])
        monkeypatch.setenv("YURTLE_AGENT", "Mini")
        result = self._mcp_comment(repo, {})
        assert "error" not in result, result
        assert _comment_headings(_item_file(repo)) == ["Mini"]

    def test_mcp_comment_without_author_or_env_uses_git_user_never_agent(self, repo):
        _ok(["create", "feature", "probe"])
        result = self._mcp_comment(repo, {})
        assert "error" not in result, result
        assert _comment_headings(_item_file(repo)) == ["Test"]

    def test_mcp_explicit_author_is_the_explicit_actor(self, repo, monkeypatch):
        _ok(["create", "feature", "probe"])
        monkeypatch.setenv("YURTLE_AGENT", "Mini")
        self._mcp_comment(repo, {"author": "Zed"})
        assert _comment_headings(_item_file(repo)) == ["Zed"]

    @pytest.mark.parametrize("author", ["", "  ", "a\nb"], ids=["empty", "spaces", "LF"])
    def test_mcp_bad_author_is_an_error(self, repo, author):
        _ok(["create", "feature", "probe"])
        result = self._mcp_comment(repo, {"author": author})
        assert "error" in result, result
        assert _comment_headings(_item_file(repo)) == []

    def test_mcp_schema_advertises_no_agent_default(self):
        from yurtle_kanban.mcp.server import KanbanMCPServer

        tools = {t["name"]: t for t in KanbanMCPServer(repo_root=Path.cwd()).get_tools()}
        author = tools["kanban_add_comment"]["inputSchema"]["properties"]["author"]
        assert author.get("default") != "agent", author


# ---------------------------------------------------------------------------
# Acceptance 6 — identity validation
# ---------------------------------------------------------------------------


class TestIdentityValidation:
    @pytest.mark.parametrize("value", ["", "  ", "\t"], ids=["empty", "spaces", "tab"])
    def test_list_assignee_blank_is_refused(self, repo, value):
        """`if assignee:` made `""` match everything; now it is refused."""
        _ok(["create", "feature", "probe", "--assign", "carol"])
        _refused(["list", "--assignee", value, "--json"], "--assignee")

    def test_list_assignee_is_stripped(self, repo):
        _ok(["create", "feature", "probe", "--assign", "carol"])
        out = _ok(["list", "--assignee", "  carol  ", "--json"]).output
        assert [i["id"] for i in json.loads(out)] == ["FEAT-001"]

    @pytest.mark.parametrize(
        "value", ["", "   ", "x\ny", "x\ry", "x\ty", "x\x00y", "x\x7fy", "x\x85y"],
        ids=["empty", "spaces", "LF", "CR", "TAB", "NUL", "DEL", "NEL"],
    )
    def test_create_assign_bad_value_is_refused_and_writes_nothing(self, repo, value):
        _refused(["create", "feature", "probe", "--assign", value], "--assign")
        assert list((repo / "kanban-work" / "features").glob("*.md")) == []

    @pytest.mark.parametrize("value", ["", "  ", "x\ny"], ids=["empty", "spaces", "LF"])
    def test_move_assign_bad_value_is_refused(self, repo, value):
        _ok(["create", "feature", "probe"])
        _refused(["move", "FEAT-001", "ready", "--assign", value, "--no-commit"], "--assign")
        assert _show()["status"] == "backlog"

    @pytest.mark.parametrize("value", ["", "  ", "a\nb", "a\tb"],
                             ids=["empty", "spaces", "LF", "TAB"])
    def test_comment_agent_bad_value_is_refused(self, repo, value):
        _ok(["create", "feature", "probe"])
        _refused(["comment", "FEAT-001", "--body", "hi", "--agent", value], "--agent")
        assert _comment_headings(_item_file(repo)) == []

    @pytest.mark.parametrize("value", ["", "  ", "a\nb"], ids=["empty", "spaces", "LF"])
    def test_move_agent_bad_value_is_refused(self, repo, value):
        _ok(["create", "feature", "probe"])
        _refused(["move", "FEAT-001", "ready", "--agent", value, "--no-commit"], "--agent")

    @pytest.mark.parametrize("value", ["", "  "], ids=["empty", "spaces"])
    def test_next_agent_blank_is_refused(self, repo, value):
        _refused(["next", "--agent", value], "--agent")

    @pytest.mark.parametrize("value", ["", "  ", "a\nb"], ids=["empty", "spaces", "LF"])
    def test_bad_env_actor_is_refused_naming_the_variable(self, repo, monkeypatch, value):
        _ok(["create", "feature", "probe"])
        monkeypatch.setenv("YURTLE_AGENT", value)
        _refused(["comment", "FEAT-001", "--body", "hi"], "YURTLE_AGENT")
        assert _comment_headings(_item_file(repo)) == []


# ---------------------------------------------------------------------------
# Acceptance 7 — the free-text helper, on every --X / --X-file pair
# ---------------------------------------------------------------------------

# Each free-text pair: (command prefix, option base name). `comment` requires the
# text; `create`'s body is optional. Both run the same battery.
PAIRS = [
    pytest.param(["comment", "FEAT-001"], "body", True, id="comment--body"),
    pytest.param(["create", "feature", "other"], "body", False, id="create--body"),
]


def _stored_file(repo: Path, cmd: list[str]) -> Path:
    """The file the command's text lands in: the comment's item or the new item."""
    return _item_file(repo, "FEAT-001" if cmd[0] == "comment" else "FEAT-002")


class TestFreeTextHelper:
    @pytest.fixture(autouse=True)
    def _item(self, repo, monkeypatch):
        monkeypatch.setenv("YURTLE_AGENT", "Mini")
        _ok(["create", "feature", "probe"])

    @pytest.mark.parametrize("cmd, name, required", PAIRS)
    def test_inline_text_is_stored_verbatim(self, repo, cmd, name, required):
        _ok([*cmd, f"--{name}", NASTY])
        assert NASTY.encode() in _stored_file(repo, cmd).read_bytes()

    @pytest.mark.parametrize("cmd, name, required", PAIRS)
    def test_stdin_with_substitutions_is_stored_verbatim(self, repo, cmd, name, required):
        _ok([*cmd, f"--{name}-file", "-"], input=(NASTY + "\n").encode())
        assert NASTY.encode() in _stored_file(repo, cmd).read_bytes()
        assert not (repo / "pwned").exists()

    @pytest.mark.parametrize("cmd, name, required", PAIRS)
    def test_file_path_is_stored_verbatim(self, repo, tmp_path_factory, cmd, name, required):
        src = tmp_path_factory.mktemp("body") / "body.md"
        src.write_bytes((NASTY + "\n").encode())
        _ok([*cmd, f"--{name}-file", str(src)])
        assert NASTY.encode() in _stored_file(repo, cmd).read_bytes()

    @pytest.mark.parametrize("cmd, name, required", PAIRS)
    def test_crlf_and_cr_become_lf(self, repo, cmd, name, required):
        _ok([*cmd, f"--{name}-file", "-"], input=b"alpha\r\nbeta\rgamma\r\n")
        data = _stored_file(repo, cmd).read_bytes()
        assert b"alpha\nbeta\ngamma" in data
        assert b"\r" not in data

    @pytest.mark.parametrize("cmd, name, required", PAIRS)
    def test_trailing_newlines_are_stripped(self, repo, cmd, name, required):
        _ok([*cmd, f"--{name}-file", "-"], input=b"keep me\n\n\n")
        data = _stored_file(repo, cmd).read_bytes()
        assert b"keep me" in data
        assert b"keep me\n\n" not in data

    @pytest.mark.parametrize("cmd, name, required", PAIRS)
    def test_invalid_utf8_stdin_is_refused(self, repo, cmd, name, required):
        _refused([*cmd, f"--{name}-file", "-"], input=b"bad \xff\xfe bytes")

    @pytest.mark.parametrize("cmd, name, required", PAIRS)
    def test_invalid_utf8_file_is_refused(self, repo, tmp_path_factory, cmd, name, required):
        src = tmp_path_factory.mktemp("body") / "bad.md"
        src.write_bytes(b"bad \xc3\x28 bytes")
        _refused([*cmd, f"--{name}-file", str(src)])

    @pytest.mark.parametrize("cmd, name, required", PAIRS)
    @pytest.mark.parametrize("payload", [b"", b"\n\n", b"  \t \r\n"],
                             ids=["empty", "newlines", "whitespace"])
    def test_empty_stdin_is_refused(self, repo, cmd, name, required, payload):
        _refused([*cmd, f"--{name}-file", "-"], input=payload)

    @pytest.mark.parametrize("cmd, name, required", PAIRS)
    @pytest.mark.parametrize("value", ["", "   "], ids=["empty", "spaces"])
    def test_blank_inline_text_is_refused(self, repo, cmd, name, required, value):
        _refused([*cmd, f"--{name}", value])

    @pytest.mark.parametrize("cmd, name, required", PAIRS)
    def test_missing_file_is_refused(self, repo, tmp_path_factory, cmd, name, required):
        missing = tmp_path_factory.mktemp("body") / "nope.md"
        result = _invoke([*cmd, f"--{name}-file", str(missing)])
        assert result.exit_code in (1, 2), result.output
        assert "nope.md" in _flat(result.output), result.output
        assert result.exception is None or isinstance(result.exception, SystemExit), (
            result.exception
        )

    @pytest.mark.parametrize("cmd, name, required", PAIRS)
    def test_both_inline_and_file_is_a_usage_error(self, repo, cmd, name, required):
        result = _usage_error([*cmd, f"--{name}", "x", f"--{name}-file", "-"], input=b"y\n")
        out = _flat(result.output)
        assert "No such option" not in out, out
        assert f"--{name}-file" in out, out

    def test_comment_with_neither_is_a_usage_error(self, repo):
        out = _flat(_usage_error(["comment", "FEAT-001"]).output)
        assert "--body" in out and "--body-file" in out, out

    def test_create_with_neither_is_fine(self, repo):
        _ok(["create", "feature", "other"])

    def test_comment_positional_text_is_gone(self, repo):
        """`comment ID TEXT` was the injection channel; the text is `--body` now."""
        _usage_error(["comment", "FEAT-001", "hi"])

    def test_body_file_dash_on_create_push_reaches_the_file(self, repo):
        _ok(["create", "feature", "other", "--push", "--body-file", "-"],
            input=(NASTY + "\n").encode())
        assert NASTY.encode() in _item_file(repo, "FEAT-002").read_bytes()


class TestStdinReadBeforeSubprocess:
    """The whole text is read in the CLI layer before any subprocess starts."""

    @pytest.fixture(autouse=True)
    def _item(self, repo, monkeypatch):
        monkeypatch.setenv("YURTLE_AGENT", "Mini")
        _ok(["create", "feature", "probe"])

    @pytest.mark.parametrize(
        "args",
        [
            pytest.param(["comment", "FEAT-001", "--body-file", "-"], id="comment"),
            pytest.param(["create", "feature", "other", "--push", "--body-file", "-"],
                         id="create-push"),
        ],
    )
    def test_stdin_is_drained_before_the_first_subprocess(self, repo, monkeypatch, args):
        payload = (NASTY + "\n").encode()
        real_run = subprocess.run
        positions: list[object] = []

        def spy(*a: Any, **kw: Any):
            try:
                positions.append(sys.stdin.buffer.tell())
            except Exception as exc:  # stdin replaced by something untellable
                positions.append(repr(exc))
            return real_run(*a, **kw)

        monkeypatch.setattr(subprocess, "run", spy)
        _ok(args, input=payload)
        monkeypatch.setattr(subprocess, "run", real_run)

        assert positions, "expected at least one subprocess (git) call"
        assert all(p == len(payload) for p in positions), (
            f"stdin was not fully read before a subprocess ran: {positions}"
        )


class TestStdinTty:
    """`--X-file -` on a TTY is refused at once — it would otherwise wait forever."""

    def _shim_env(self) -> dict[str, str]:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(SRC)
        env["YURTLE_AGENT"] = "Mini"
        return env

    @pytest.mark.parametrize(
        "args",
        [
            pytest.param(["comment", "FEAT-001", "--body-file", "-"], id="comment"),
            pytest.param(["create", "feature", "other", "--body-file", "-"], id="create"),
        ],
    )
    def test_tty_stdin_is_refused_without_hanging(self, repo, args):
        _ok(["create", "feature", "probe"])
        master, slave = pty.openpty()
        try:
            proc = subprocess.run(
                [sys.executable, "-c", "from yurtle_kanban.cli import main; main()", *args],
                stdin=slave, capture_output=True, cwd=repo, env=self._shim_env(),
                timeout=60,
            )
        except subprocess.TimeoutExpired:
            pytest.fail(f"{args} blocked reading a TTY stdin instead of refusing")
        finally:
            os.close(master)
            os.close(slave)
        out = _flat((proc.stdout + proc.stderr).decode(errors="replace"))
        assert proc.returncode == 1, f"{args} exited {proc.returncode}: {out}"
        assert "pipe" in out.lower(), out


# ---------------------------------------------------------------------------
# Acceptance 8 — a real shell with a quoted heredoc
# ---------------------------------------------------------------------------


class TestRealShellHeredoc:
    @pytest.fixture
    def shell_env(self, tmp_path_factory) -> dict[str, str]:
        """PATH with a `yurtle-kanban` that runs THIS checkout's source."""
        bindir = tmp_path_factory.mktemp("bin")
        shim = bindir / "yurtle-kanban"
        shim.write_text(
            "#!/bin/sh\n"
            f'exec "{sys.executable}" -c '
            "'from yurtle_kanban.cli import main; main()' \"$@\"\n"
        )
        shim.chmod(0o755)
        env = dict(os.environ)
        env["PATH"] = f"{bindir}{os.pathsep}{env.get('PATH', '')}"
        env["PYTHONPATH"] = str(SRC)
        env["YURTLE_AGENT"] = "Mini"
        return env

    def test_quoted_heredoc_body_is_byte_identical_and_runs_nothing(
        self, repo, shell_env,
    ):
        _ok(["create", "feature", "probe"])
        script = (
            "yurtle-kanban comment FEAT-001 --body-file - <<'EOF'\n"
            f"{NASTY}\n"
            "EOF\n"
        )
        proc = subprocess.run(
            ["sh", "-c", script], cwd=repo, env=shell_env,
            capture_output=True, timeout=120, stdin=subprocess.DEVNULL,
        )
        assert proc.returncode == 0, (proc.stdout + proc.stderr).decode(errors="replace")
        assert not (repo / "pwned").exists()
        assert not (repo / "pwned2").exists()
        assert NASTY.encode() in _item_file(repo).read_bytes()
        assert _comment_headings(_item_file(repo)) == ["Mini"]

    def test_control_double_quoted_argument_is_expanded_by_the_shell(
        self, repo, shell_env,
    ):
        """The threat is real: the shell runs `$(…)` before any CLI sees it."""
        proc = subprocess.run(
            ["sh", "-c", 'echo "$(touch pwned)" > /dev/null'], cwd=repo, env=shell_env,
            capture_output=True, timeout=30, stdin=subprocess.DEVNULL,
        )
        assert proc.returncode == 0
        assert (repo / "pwned").exists()


# ---------------------------------------------------------------------------
# Acceptance 9 — skill lint
# ---------------------------------------------------------------------------

_YK = r"yurtle-kanban\s+"
_QUOTED_HEREDOC = re.compile(r"<<-?\s*(['\"])[A-Za-z_]\w*\1")


def skill_violations(text: str) -> list[str]:
    """Why this skill text teaches free text or flags #580 forbids ([] = clean).

    - `comment` is taught only as `--body-file -` fed by a QUOTED heredoc;
    - `create` never takes a double-quoted `--body`, nor `--description`/`-d`,
      and a `--body-file -` on it is fed by a quoted heredoc;
    - the removed flags are not taught: `-a`, `comment --author`,
      `create --assignee`.
    """
    problems: list[str] = []
    for n, line in enumerate(text.splitlines(), 1):
        if re.search(_YK + r"comment\b", line):
            if "--body-file -" not in line or not _QUOTED_HEREDOC.search(line):
                problems.append(f"{n}: comment without `--body-file -` + quoted heredoc")
            if re.search(r"--author\b", line):
                problems.append(f"{n}: comment --author (now --agent)")
        if re.search(_YK + r"create\b", line):
            if re.search(r"--body\s+\"", line):
                problems.append(f"{n}: create with a double-quoted --body")
            if re.search(r"--description\b|\s-d\b", line):
                problems.append(f"{n}: create --description/-d (now --body/--body-file)")
            if "--body-file -" in line and not _QUOTED_HEREDOC.search(line):
                problems.append(f"{n}: create --body-file - without a quoted heredoc")
        if re.search(_YK + r"(?:list|next|create|move|comment)\b.*\s-a\b", line):
            problems.append(f"{n}: short flag -a (removed)")
    if re.search(_YK + r"create\b", text) and re.search(r"`--assignee\b", text):
        problems.append("create option `--assignee` (now `--assign`)")
    return problems


def _skill_files() -> list[Path]:
    return sorted(SKILLS_DIR.rglob("*.md"))


class TestSkillLint:
    def test_there_are_skills_to_check(self):
        assert len(_skill_files()) >= 10

    @pytest.mark.parametrize(
        "path", _skill_files(), ids=lambda p: str(p.relative_to(SKILLS_DIR)),
    )
    def test_skill_teaches_safe_free_text_and_current_flags(self, path):
        assert skill_violations(path.read_text()) == []

    # -- the lint itself -----------------------------------------------------

    @pytest.mark.parametrize(
        "line",
        [
            'yurtle-kanban comment EXP-1 "shipped $(date)"',
            "yurtle-kanban comment EXP-1 --body \"text\"",
            "yurtle-kanban comment EXP-1 --body-file - <<EOF",
            'yurtle-kanban comment EXP-1 --body-file - <<\'EOF\' --author "M5"',
            'yurtle-kanban create feature "T" --body "text"',
            'yurtle-kanban create feature "T" --description "text"',
            'yurtle-kanban create feature "T" -d "text"',
            'yurtle-kanban create feature "T" --body-file - <<EOF',
            "yurtle-kanban list -a Mini",
            "yurtle-kanban move EXP-1 in_progress -a Mini",
        ],
    )
    def test_lint_flags_unsafe_lines(self, line):
        assert skill_violations(line) != []

    @pytest.mark.parametrize(
        "line",
        [
            "yurtle-kanban comment EXP-1 --body-file - <<'EOF'",
            'yurtle-kanban comment EXP-1 --body-file - <<"EOF"',
            "yurtle-kanban comment EXP-1 --agent Mini --body-file - <<-'EOF'",
            'yurtle-kanban create feature "T" --push --priority medium',
            "yurtle-kanban create feature \"T\" --body-file - <<'EOF'",
            "yurtle-kanban move EXP-1 in_progress --assign Mini",
            "yurtle-kanban list --assignee Mini",
            "git tag -a v1 -m 'x'",
        ],
    )
    def test_lint_passes_safe_lines(self, line):
        assert skill_violations(line) == []

    def test_lint_flags_create_assignee_option_in_prose(self):
        text = "yurtle-kanban create feature \"T\" --push\n\n**Options:**\n- `--assignee <name>`\n"
        assert skill_violations(text) != []


# ---------------------------------------------------------------------------
# Acceptance 10 / Expected 5 — stdin=DEVNULL on every git subprocess
# ---------------------------------------------------------------------------

_SUBPROCESS_FUNCS = {"run", "Popen", "check_output", "check_call", "call"}


def _git_calls(tree: ast.AST) -> list[ast.Call]:
    calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and func.attr in _SUBPROCESS_FUNCS
            and isinstance(func.value, ast.Name)
            and func.value.id == "subprocess"
        ):
            continue
        first = node.args[0]
        if (
            isinstance(first, ast.List)
            and first.elts
            and isinstance(first.elts[0], ast.Constant)
            and first.elts[0].value == "git"
        ):
            calls.append(node)
    return calls


class TestGitStdinDevnull:
    def test_every_git_subprocess_in_src_passes_stdin_devnull(self):
        """Literal `subprocess.<fn>(["git", ...])` sites must pass DEVNULL; calls
        through the service's `_git_run` runner (#585) count as git calls too —
        the runner itself is one of the literal sites, so it is checked above."""
        offenders = []
        literal = runner = 0
        for path in sorted((SRC / "yurtle_kanban").rglob("*.py")):
            tree = ast.parse(path.read_text(), filename=str(path))
            for call in _git_calls(tree):
                literal += 1
                stdin = next((k.value for k in call.keywords if k.arg == "stdin"), None)
                if stdin is None or not ast.unparse(stdin).endswith("DEVNULL"):
                    offenders.append(f"{path.relative_to(SRC)}:{call.lineno}")
            runner += sum(
                1 for node in ast.walk(tree)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "_git_run"
            )
        assert literal >= 5, f"found only {literal} literal git calls — is the scan broken?"
        assert literal + runner >= 20, (
            f"found only {literal} literal + {runner} _git_run calls — is the scan broken?"
        )
        assert offenders == [], f"git calls without stdin=subprocess.DEVNULL: {offenders}"

    def test_no_subprocess_imported_by_another_name(self):
        """The static scan above looks for `subprocess.<fn>`; keep it that way."""
        pattern = re.compile(
            r"^\s*(?:from\s+subprocess\s+import|import\s+subprocess\s+as)\b", re.M
        )
        hits = [
            str(p.relative_to(SRC)) for p in sorted((SRC / "yurtle_kanban").rglob("*.py"))
            if pattern.search(p.read_text())
        ]
        assert hits == []

    def test_runtime_git_calls_get_devnull(self, repo, monkeypatch):
        """comment, move and create --push: every git call made carries DEVNULL."""
        monkeypatch.setenv("YURTLE_AGENT", "Mini")
        real_run = subprocess.run
        seen: list[tuple[list[str], object]] = []

        def spy(*a: Any, **kw: Any):
            cmd = a[0] if a else kw.get("args")
            if isinstance(cmd, list) and cmd and cmd[0] == "git":
                seen.append((cmd, kw.get("stdin", "<unset>")))
            return real_run(*a, **kw)

        monkeypatch.setattr(subprocess, "run", spy)
        _ok(["create", "feature", "probe", "--push"])
        _ok(["move", "FEAT-001", "ready"])
        _ok(["comment", "FEAT-001", "--body", "hi"])
        monkeypatch.setattr(subprocess, "run", real_run)

        assert seen, "expected git calls"
        bad = [(cmd[:2], stdin) for cmd, stdin in seen if stdin is not subprocess.DEVNULL]
        assert bad == [], f"git calls without stdin=DEVNULL: {bad}"


# ---------------------------------------------------------------------------
# Acceptance 11 / Expected 4 — flag normalisation (no aliases kept)
# ---------------------------------------------------------------------------


class TestFlagNormalisation:
    @pytest.fixture(autouse=True)
    def _item(self, repo):
        _ok(["create", "feature", "probe"])

    @pytest.mark.parametrize(
        "args",
        [
            pytest.param(["list", "-a", "X"], id="list -a"),
            pytest.param(["next", "-a", "X"], id="next -a"),
            pytest.param(["next", "--assignee", "X"], id="next --assignee"),
            pytest.param(["create", "feature", "t", "-a", "X"], id="create -a"),
            pytest.param(["create", "feature", "t", "--assignee", "X"], id="create --assignee"),
            pytest.param(["create", "feature", "t", "--description", "d"],
                         id="create --description"),
            pytest.param(["create", "feature", "t", "-d", "d"], id="create -d"),
            pytest.param(["move", "FEAT-001", "ready", "-a", "X", "--no-commit"], id="move -a"),
            pytest.param(["comment", "FEAT-001", "--body", "hi", "--author", "X"],
                         id="comment --author"),
            pytest.param(["comment", "FEAT-001", "--body", "hi", "-a", "X"], id="comment -a"),
        ],
    )
    def test_old_flag_is_a_usage_error(self, args):
        """Rejected for the OLD flag itself — not for some other unknown option."""
        old = next(a for a in args if a in {"-a", "-d", "--assignee", "--description",
                                            "--author"})
        out = _flat(_usage_error(args).output)
        assert re.search(rf"No such option:? ['\"]?{re.escape(old)}(?![\w-])", out), out

    def test_next_agent_is_accepted(self):
        _ok(["next", "--agent", "Mini"])

    def test_list_assignee_filter_is_kept(self):
        _ok(["move", "FEAT-001", "ready", "--assign", "carol", "--no-commit"])
        out = _ok(["list", "--assignee", "carol", "--json"]).output
        assert [i["id"] for i in json.loads(out)] == ["FEAT-001"]

    @pytest.mark.parametrize(
        "command, option",
        [
            ("comment", "--agent"),
            ("comment", "--body"),
            ("comment", "--body-file"),
            ("create", "--body"),
            ("create", "--body-file"),
            ("create", "--assign"),
            ("move", "--assign"),
            ("move", "--agent"),
            ("next", "--agent"),
            ("list", "--assignee"),
        ],
    )
    def test_new_flag_is_documented_in_help(self, command, option):
        out = _ok([command, "--help"]).output
        assert re.search(rf"(?<![\w-]){re.escape(option)}(?![\w-])", out), out


# ---------------------------------------------------------------------------
# Expected 6 — test isolation
# ---------------------------------------------------------------------------


def test_conftest_unsets_yurtle_agent():
    assert "YURTLE_AGENT" not in os.environ


def test_conftest_gives_a_hermetic_git_identity(tmp_path):
    """No host identity leaks in, and CI (no identity) still has one (#580)."""
    _git(tmp_path, "init", "-b", "main")
    name = subprocess.run(
        ["git", "config", "user.name"], cwd=tmp_path, capture_output=True, text=True,
    ).stdout.strip()
    assert name == "test-git-user"


def test_no_global_git_opts_out_of_the_suite_identity(tmp_path, no_global_git):
    _git(tmp_path, "init", "-b", "main")
    done = subprocess.run(
        ["git", "config", "user.name"], cwd=tmp_path, capture_output=True, text=True,
    )
    assert done.stdout.strip() == ""
