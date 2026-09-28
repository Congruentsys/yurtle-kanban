# ruff: noqa: F811  -- the `repo` / `mcp_log` fixtures imported from #576 / #786 are re-bound
"""#802: `next-id` refuses a malformed prefix.

Decided ([steer] on #802, as revised): `allocate_next_id` (so CLI `next-id` and MCP
`kanban_next_id`) refuses a prefix that isn't a letter, then letters or digits, in
dash-separated segments, with an optional trailing `.` (paper-scoped `H130.`),
case-insensitive. Letters and digits are any script's: #193 and #219 accept
non-ASCII prefixes such as `ÉXP`. The refusal is an `InputRefused` whose message
names the allowed form, raised before anything is written or committed (no
allocation record, no commit). MCP's blank `prefix` is refused earlier, as
"prefix is required" (#768).

The title half of the original issue is dropped: #141 / #148 round-trip titles with
control characters, stored escaped.

Controls: valid prefixes (every shipped theme's, the HDD ones hdd_commands builds:
`IDEA-R`, `IDEA-F`, `LIT`, `H`, `H{paper}.`, `EXPR`, `M`, `PAPER`; the service's
built-in defaults; non-ASCII ones such as `ÉXP`, `ÜBER-R`) allocate as before.

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

# any script's letters and digits (#193, #219): upper-cased as ASCII ones are
NON_ASCII_PREFIXES = {
    "ÉXP": ("ÉXP-001", 1),
    "éxp": ("ÉXP-001", 1),
    "ÜBER-R": ("ÜBER-R-001", 1),
    "Ωmega2": ("ΩMEGA2-001", 1),
}

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

ALLOCATIONS = Path(".kanban") / "_ID_ALLOCATIONS.json"


def _flat(text: str) -> str:
    return " ".join(text.split())


def _names_prefix_grammar(message: str) -> None:
    low = message.lower()
    assert "prefix" in low and "letter" in low, f"refusal does not name the form: {message!r}"


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
# prefix grammar — RED before the fix
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
# controls — valid prefixes allocate as before (GREEN)
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


@pytest.mark.parametrize("prefix", list(NON_ASCII_PREFIXES))
def test_control_non_ascii_prefix_allocates(repo: Repo, mcp_log, prefix: str) -> None:
    """#193 / #219 accept a prefix in any script: the grammar's letters and digits
    are Unicode ones, through the service, the CLI and MCP."""
    want_id, want_num = NON_ASCII_PREFIXES[prefix]
    result = repo.service().allocate_next_id(prefix, sync_remote=False, commit_allocation=False)
    assert result["success"], result
    assert (result["id"], result["number"]) == (want_id, want_num), result

    cli = CliRunner().invoke(main, ["next-id", prefix, "--no-sync"])
    assert cli.exit_code == 0, (cli.output, repr(cli.exception))
    assert want_id in cli.output, cli.output

    out = _mcp(repo).handle_tool_call("kanban_next_id", {"prefix": prefix})
    assert out.get("success"), out
    assert out["id"].startswith(want_id.rsplit("-", 1)[0] + "-"), out
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
