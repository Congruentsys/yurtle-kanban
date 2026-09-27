# ruff: noqa: F811  -- the `repo` / `mcp_log` fixtures imported from #576 / #786 are re-bound
"""#802: `next-id` refuses a malformed prefix; titles refuse control characters.

Decided ([steer] on #802, bucket 1):
1. `allocate_next_id` (so CLI `next-id` and MCP `kanban_next_id`) refuses a prefix
   that isn't a letter, then letters or digits, in dash-separated segments, with an
   optional trailing `.` (paper-scoped `H130.`), case-insensitive. The refusal is an
   `InputRefused` whose message names the allowed form. Before anything is written
   or committed: no allocation record, no commit.
2. A title holding a control character (C0 — NUL, `\\x01`, ESC, TAB — or DEL) is
   refused as `InputRefused` on create and update, before anything is written. A TAB
   counts: a title is one line of text.

Controls: valid prefixes (every shipped theme's, the HDD ones hdd_commands builds —
`IDEA-R`, `IDEA-F`, `LIT`, `H`, `H{paper}.`, `EXPR`, `M`, `PAPER` — and the service's
built-in defaults) allocate as before; Unicode letters in a title are accepted.

Fixture: #576's two-board repo (`development` nautical under `work/`, `research` hdd
under `research/`): EXP-1..EXP-5 and H1.1, committed, no remote.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner, Result

import yurtle_kanban
from tests.issues.test_576_cli_update_deps import Repo, _git, repo  # noqa: F401
from tests.issues.test_786_mcp_input_refused import (  # noqa: F401  (fixture)
    _assert_one_warning,
    _mcp,
    _records,
    mcp_log,
)
from yurtle_kanban.cli import main
from yurtle_kanban.models import InputRefused, WorkItemType

THEMES = Path(yurtle_kanban.__file__).parent.parent.parent / "themes"

GOOD_PREFIXES = ["EXP", "exp", "IDEA-R", "H130.", "M", "LIT", "PAPER", "EXPR"]

BAD_PREFIXES = [
    pytest.param("", id="empty"),
    pytest.param("a b", id="space"),
    pytest.param("../x", id="path"),
    pytest.param("a\x00", id="nul"),
    pytest.param("-EXP", id="leading-dash"),
    pytest.param("EXP-", id="trailing-dash"),
    pytest.param("1EXP", id="leading-digit"),
    pytest.param("EX P", id="inner-space"),
    pytest.param("EXP--R", id="double-dash"),
    pytest.param("H130..", id="double-dot"),
]

BAD_TITLES = [
    pytest.param("a\x00b", id="nul"),
    pytest.param("a\x01b", id="soh"),
    pytest.param("a\x1b[31mb", id="esc"),
    pytest.param("a\x7fb", id="del"),
    pytest.param("a\tb", id="tab"),
]

GOOD_TITLES = [
    pytest.param("Café crème", id="latin"),
    pytest.param("日本 plan", id="cjk"),
]

ALLOCATIONS = Path(".kanban") / "_ID_ALLOCATIONS.json"


def _flat(text: str) -> str:
    return " ".join(text.split())


def _names_prefix_grammar(message: str) -> None:
    low = message.lower()
    assert "prefix" in low and "letter" in low, f"refusal does not name the form: {message!r}"


def _names_control_character(message: str) -> None:
    low = message.lower()
    assert "title" in low and "control" in low, f"refusal does not say why: {message!r}"


class _State:
    """What a refusal must leave untouched: every file, HEAD and the index/tree."""

    def __init__(self, repo: Repo):
        self.repo = repo
        self.files = repo.snapshot()
        self.head = repo.head()

    def assert_untouched(self, what: str) -> None:
        r = self.repo
        assert not (r.root / ALLOCATIONS).exists(), f"{what}: an allocation was recorded"
        assert r.snapshot() == self.files, f"{what}: an item file was written"
        assert r.head() == self.head, f"{what}: a commit was made"
        status = _git(r.root, "status", "--porcelain").strip()
        assert not status, f"{what}: the working tree was dirtied: {status}"


def _cli_refused(repo: Repo, args: list[str]) -> str:
    state = _State(repo)
    result: Result = CliRunner().invoke(main, args)
    out = result.output
    assert result.exit_code == 1, (
        f"{args!r} exited {result.exit_code}: {result.exception!r}\n{out}"
    )
    assert isinstance(result.exception, SystemExit), repr(result.exception)
    assert "Traceback" not in out, out
    assert "Error:" in out, f"not an `Error:` line: {out!r}"
    assert len(out.strip().splitlines()) == 1, f"not one line: {out!r}"
    state.assert_untouched(repr(args))
    return _flat(out)


# ---------------------------------------------------------------------------
# 1. prefix grammar — RED
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("commit_allocation", [True, False], ids=["commit", "no-commit"])
@pytest.mark.parametrize("prefix", BAD_PREFIXES)
def test_service_refuses_malformed_prefix(repo: Repo, prefix: str, commit_allocation) -> None:
    state = _State(repo)
    with pytest.raises(InputRefused) as info:
        repo.service().allocate_next_id(
            prefix, sync_remote=False, commit_allocation=commit_allocation
        )
    _names_prefix_grammar(str(info.value))
    state.assert_untouched(f"allocate_next_id({prefix!r})")


@pytest.mark.parametrize("flags", [[], ["--no-sync"], ["--no-commit"]], ids=["sync", "no-sync",
                                                                              "no-commit"])
@pytest.mark.parametrize("prefix", BAD_PREFIXES)
def test_cli_next_id_refuses_malformed_prefix(repo: Repo, prefix: str, flags) -> None:
    # `--`: `-EXP` is the PREFIX argument, not an option
    out = _cli_refused(repo, ["next-id", *flags, "--", prefix])
    _names_prefix_grammar(out)


@pytest.mark.parametrize("prefix", BAD_PREFIXES)
def test_cli_next_id_json_refuses_malformed_prefix(repo: Repo, prefix: str) -> None:
    state = _State(repo)
    result = CliRunner().invoke(main, ["next-id", "--json", "--", prefix])
    assert result.exit_code == 1, (result.output, repr(result.exception))
    assert isinstance(result.exception, SystemExit), repr(result.exception)
    assert "Traceback" not in result.output, result.output
    state.assert_untouched(f"next-id {prefix!r} --json")


@pytest.mark.parametrize("prefix", BAD_PREFIXES)
def test_mcp_next_id_refuses_malformed_prefix(repo: Repo, mcp_log, prefix: str) -> None:
    state = _State(repo)
    out = _mcp(repo).handle_tool_call("kanban_next_id", {"prefix": prefix})
    if prefix == "":
        # MCP refuses a blank required argument before any handler runs (#768):
        # its own wording, and no log record at all
        assert out == {"error": "prefix is required"}, out
        assert not _records(mcp_log), [r.getMessage() for r in _records(mcp_log)]
        state.assert_untouched("kanban_next_id('')")
        return
    assert "error" in out and not out.get("success"), out
    _names_prefix_grammar(out["error"])
    _assert_one_warning(mcp_log)  # a refusal: one WARNING, no traceback
    state.assert_untouched(f"kanban_next_id({prefix!r})")


# ---------------------------------------------------------------------------
# 1. controls — valid prefixes allocate as before (GREEN)
# ---------------------------------------------------------------------------


EXPECTED = {
    "EXP": ("EXP-006", 6),
    "exp": ("EXP-006", 6),
    "IDEA-R": ("IDEA-R-001", 1),
    "H130.": ("H130.1", 1),
    "M": ("M-001", 1),
    "LIT": ("LIT-001", 1),
    "PAPER": ("PAPER-001", 1),
    "EXPR": ("EXPR-001", 1),
}


@pytest.mark.parametrize("prefix", GOOD_PREFIXES)
def test_control_service_valid_prefix_allocates(repo: Repo, prefix: str) -> None:
    result = repo.service().allocate_next_id(prefix, sync_remote=False, commit_allocation=True)
    assert result["success"], result
    assert (result["id"], result["number"]) == EXPECTED[prefix], result
    assert (repo.root / ALLOCATIONS).exists()


@pytest.mark.parametrize("prefix", GOOD_PREFIXES)
def test_control_cli_valid_prefix_allocates(repo: Repo, prefix: str) -> None:
    result = CliRunner().invoke(main, ["next-id", prefix, "--no-sync"])
    assert result.exit_code == 0, (result.output, repr(result.exception))
    assert EXPECTED[prefix][0] in result.output, result.output


@pytest.mark.parametrize("prefix", GOOD_PREFIXES)
def test_control_mcp_valid_prefix_allocates(repo: Repo, mcp_log, prefix: str) -> None:
    out = _mcp(repo).handle_tool_call("kanban_next_id", {"prefix": prefix})
    assert out.get("success"), out
    assert out["id"] == EXPECTED[prefix][0], out
    assert not _records(mcp_log), [r.getMessage() for r in _records(mcp_log)]


def _theme_prefixes() -> list[str]:
    found: list[str] = []
    for path in sorted(THEMES.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for type_def in (data.get("item_types") or {}).values():
            if isinstance(type_def, dict) and "id_prefix" in type_def:
                found.append(str(type_def["id_prefix"]))
    return found


# the prefixes hdd_commands.py builds by hand (IDEA-F, H{paper}., the fallback ITEM)
HDD_PREFIXES = ["IDEA-R", "IDEA-F", "LIT", "PAPER", "H", "H0.", "H1.", "H130.", "EXPR", "M",
                "ITEM"]


def test_control_every_shipped_prefix_is_accepted(repo: Repo) -> None:
    theme_prefixes = _theme_prefixes()
    assert theme_prefixes, f"no theme prefixes found under {THEMES}"
    svc = repo.service()
    builtin = [svc._get_type_prefix(t) for t in WorkItemType]
    fallback = [t.value[:4].upper() for t in WorkItemType]  # `_get_type_prefix`'s default
    refused = {}
    for prefix in dict.fromkeys(theme_prefixes + HDD_PREFIXES + builtin + fallback):
        try:
            result = svc.allocate_next_id(prefix, sync_remote=False, commit_allocation=False)
        except InputRefused as e:
            refused[prefix] = str(e)
            continue
        if not result["success"]:
            refused[prefix] = result["message"]
    assert not refused, f"shipped prefixes the grammar refuses: {refused}"


# ---------------------------------------------------------------------------
# 2. titles with a control character — RED
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("title", BAD_TITLES)
def test_service_create_refuses_control_title(repo: Repo, title: str) -> None:
    state = _State(repo)
    with pytest.raises(InputRefused) as info:
        repo.service().create_item(WorkItemType.EXPEDITION, title)
    _names_control_character(str(info.value))
    state.assert_untouched(f"create_item({title!r})")


@pytest.mark.parametrize("title", BAD_TITLES)
def test_service_create_and_push_refuses_control_title(repo: Repo, title: str) -> None:
    state = _State(repo)
    with pytest.raises(InputRefused) as info:
        repo.service().create_item_and_push(WorkItemType.EXPEDITION, title)
    _names_control_character(str(info.value))
    state.assert_untouched(f"create_item_and_push({title!r})")


@pytest.mark.parametrize("commit", [True, False], ids=["commit", "no-commit"])
@pytest.mark.parametrize("title", BAD_TITLES)
def test_service_update_refuses_control_title(repo: Repo, title: str, commit: bool) -> None:
    state = _State(repo)
    with pytest.raises(InputRefused) as info:
        repo.service().update_item("EXP-5", title=title, commit=commit)
    _names_control_character(str(info.value))
    state.assert_untouched(f"update_item(title={title!r})")


@pytest.mark.parametrize("push", [[], ["--push"]], ids=["local", "push"])
@pytest.mark.parametrize("title", BAD_TITLES)
def test_cli_create_refuses_control_title(repo: Repo, title: str, push) -> None:
    _names_control_character(_cli_refused(repo, ["create", "expedition", title, *push]))


@pytest.mark.parametrize("title", BAD_TITLES)
def test_cli_update_refuses_control_title(repo: Repo, title: str) -> None:
    _names_control_character(_cli_refused(repo, ["update", "EXP-5", "--title", title]))


@pytest.mark.parametrize("title", BAD_TITLES)
def test_mcp_create_refuses_control_title(repo: Repo, mcp_log, title: str) -> None:
    state = _State(repo)
    out = _mcp(repo).handle_tool_call(
        "kanban_create_item", {"item_type": "expedition", "title": title}
    )
    assert "error" in out and not out.get("success"), out
    _names_control_character(out["error"])
    _assert_one_warning(mcp_log)
    state.assert_untouched(f"kanban_create_item({title!r})")


@pytest.mark.parametrize("title", BAD_TITLES)
def test_mcp_update_refuses_control_title(repo: Repo, mcp_log, title: str) -> None:
    state = _State(repo)
    out = _mcp(repo).handle_tool_call("kanban_update_item", {"item_id": "EXP-5", "title": title})
    assert "error" in out and not out.get("success"), out
    _names_control_character(out["error"])
    _assert_one_warning(mcp_log)
    state.assert_untouched(f"kanban_update_item({title!r})")


# ---------------------------------------------------------------------------
# 2. controls — Unicode letters are text, not control characters (GREEN)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("title", GOOD_TITLES)
def test_control_service_unicode_title_create_and_update(repo: Repo, title: str) -> None:
    svc = repo.service()
    item = svc.create_item(WorkItemType.EXPEDITION, title)
    assert item.title == title
    assert svc.update_item("EXP-5", title=title, commit=False).title == title
    assert repo.fm("EXP-5")["title"] == title


@pytest.mark.parametrize("title", GOOD_TITLES)
def test_control_cli_unicode_title(repo: Repo, title: str) -> None:
    for args in (["create", "expedition", title], ["update", "EXP-5", "--title", title]):
        result = CliRunner().invoke(main, args)
        assert result.exit_code == 0, (args, result.output, repr(result.exception))
    assert repo.fm("EXP-5")["title"] == title


@pytest.mark.parametrize("title", GOOD_TITLES)
def test_control_mcp_unicode_title(repo: Repo, mcp_log, title: str) -> None:
    mcp = _mcp(repo)
    out = mcp.handle_tool_call("kanban_create_item", {"item_type": "expedition", "title": title})
    assert out.get("success") and out["item"]["title"] == title, out
    out = mcp.handle_tool_call("kanban_update_item", {"item_id": "EXP-5", "title": title})
    assert out.get("success") and out["item"]["title"] == title, out
    assert not _records(mcp_log), [r.getMessage() for r in _records(mcp_log)]


def test_control_existing_title_refusals_unchanged(repo: Repo) -> None:
    """#576's empty and multi-line refusals keep their wording."""
    svc = repo.service()
    with pytest.raises(InputRefused, match="title is empty"):
        svc.update_item("EXP-5", title="   ", commit=False)
    with pytest.raises(InputRefused, match="line break"):
        svc.update_item("EXP-5", title="one\ntwo", commit=False)
