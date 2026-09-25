"""#270: every error/warning line prints repo-file and argv text through `_click.safe()`.

Follow-up from the review of PR #267 (#251). `safe()` escapes control characters as well
as Rich markup; plain `escape()` only escapes markup, so ESC and newline from a repo file
(or argv) still reached the terminal raw on these lines:

1. `show` of an item whose frontmatter doesn't parse: the reason quotes the bad text
   (a real YAML error quoting a bidi override; and a reason carrying ESC + newline).
2. `validate` DUPLICATE ID / FILENAME MISMATCH: the id comes from frontmatter.
3. `hdd validate`: an Error line whose id comes from frontmatter.
4. `epic add`: the warning lines naming the item id.
5. `metrics` error text, and git failure messages (`create --push`, `next-id`).
6. argv-only: unknown status / type on `list`, `create`, `move`.

Each prints text carrying `X\\x1b[2J\\nFORGED` (or `\\u202e`), captured through every
module console swapped for a `Console(force_terminal=True)`. Controls: printable text is
unchanged. Static: in cli.py, hdd_commands.py and epic_commands.py, every
`console.print(f"...[red]/[yellow]...")` interpolates only `safe(...)` or an allowlisted
known-safe expression (ints, our own constants).
"""

from __future__ import annotations

import ast
import io
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from rich.console import Console

from yurtle_kanban import cli, epic_commands, hdd_commands
from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import main
from yurtle_kanban.service import KanbanService

SRC = Path(cli.__file__).resolve().parent
EVIL = "X\x1b[2J\nFORGED"
EVIL_YAML = "X\\e[2J\\nFORGED"  # YAML double-quoted escapes for the same text
RLO = "‮"  # RIGHT-TO-LEFT OVERRIDE: YAML accepts it raw, a terminal obeys it

PLAIN = "kanban:\n  theme: software\n  paths:\n    root: work/\n"
HDD = (
    "kanban:\n  theme: hdd\n  paths:\n    root: research/\n    scan_paths:\n"
    "      - research/papers/\n      - research/hypotheses/\n"
)

# --- helpers --------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)


def _item(
    repo: Path, rel: str, item_id: str, item_type: str = "feature", extra: str = ""
) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'---\nid: "{item_id}"\ntitle: "t"\ntype: {item_type}\nstatus: backlog\n'
        f"priority: medium\ncreated: 2026-09-25\n{extra}---\n\n# t\n"
    )


def _repo(tmp_path: Path, config: str = PLAIN) -> Path:
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@t.com")
    _git(tmp_path, "config", "user.name", "T")
    (tmp_path / ".kanban").mkdir()
    (tmp_path / ".kanban" / "config.yaml").write_text(config)
    if config == PLAIN:
        _item(tmp_path, "work/FEAT-001.md", "FEAT-001")
    return tmp_path


@pytest.fixture(autouse=True)
def _clean() -> None:
    config_mod._theme_cache.clear()


def _run(repo: Path, args: list[str], monkeypatch: pytest.MonkeyPatch) -> str:
    """Run with every module console forced to a terminal; return what Rich wrote."""
    buf = io.StringIO()
    tty = Console(file=buf, force_terminal=True, width=200)
    for mod in (cli, hdd_commands, epic_commands):
        monkeypatch.setattr(mod, "console", tty)
    monkeypatch.chdir(repo)
    result = CliRunner().invoke(main, args)
    assert result.exception is None or isinstance(result.exception, SystemExit), (
        result.output,
        result.exception,
    )
    return buf.getvalue()


def _plain(out: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", out)  # Rich's own colour codes


def _assert_escaped(out: str, needle: str) -> None:
    plain = _plain(out)
    assert needle in plain, f"expected line missing: {needle!r} in {out!r}"
    assert "\x1b" not in plain, f"raw ESC reached the terminal: {out!r}"
    assert not any(line.startswith("FORGED") for line in plain.splitlines()), repr(out)
    assert "\\x1b[2J\\nFORGED" in plain, repr(out)


# --- 1. show: the parse reason of an unparseable item ---------------------------------


def test_show_parse_reason_real_yaml_error_escapes_bidi_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    # an unclosed quote: PyYAML's error quotes the line, RLO and all
    (repo / "work" / "FEAT-002.md").write_text(
        f'---\nid: FEAT-002\ntitle: "a{RLO}b\n---\n\n# t\n'
    )
    plain = _plain(_run(repo, ["show", "FEAT-002"], monkeypatch))
    assert "doesn't parse" in plain, repr(plain)
    assert RLO not in plain, f"raw RLO reached the terminal: {plain!r}"
    assert "\\u202e" in plain, repr(plain)


def test_show_parse_reason_with_control_chars_is_escaped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    (repo / "work" / "FEAT-002.md").write_text("---\nid: [FEAT-002\n---\n\n# t\n")

    def reason(self: KanbanService, file_path: Path, content: str) -> str:
        return f"YAML error: quoting {EVIL}"  # a reason that quotes the file's text

    monkeypatch.setattr(KanbanService, "_unparseable_reason", reason)
    _assert_escaped(_run(repo, ["show", "FEAT-002"], monkeypatch), "doesn't parse")


# --- 2. validate: ids from frontmatter ------------------------------------------------


def _doubled(monkeypatch: pytest.MonkeyPatch) -> None:
    """The scan keys items by id, so a duplicate id reaches `validate` only when
    `get_items` returns it twice: make it do so."""
    real = KanbanService.get_items

    def get_items(self: KanbanService, *args: Any, **kwargs: Any) -> list[Any]:
        items = real(self, *args, **kwargs)
        return items + items

    monkeypatch.setattr(KanbanService, "get_items", get_items)


def test_validate_duplicate_and_mismatch_ids_are_escaped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    _item(repo, "work/FEAT-010.md", EVIL_YAML)
    _doubled(monkeypatch)
    out = _run(repo, ["validate"], monkeypatch)
    _assert_escaped(out, "DUPLICATE ID:")
    _assert_escaped(out, "FILENAME MISMATCH:")


# --- 3. hdd validate: an id from frontmatter ------------------------------------------


def test_hdd_validate_error_id_is_escaped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, HDD)
    (repo / "research" / "papers").mkdir(parents=True)
    _item(
        repo, "research/hypotheses/H1.md", "H1" + EVIL_YAML, "hypothesis", "paper: PAPER-9\n"
    )
    out = _run(repo, ["hdd", "validate"], monkeypatch)
    _assert_escaped(out, "Error:")


# --- 4. epic add: warning lines naming the item ---------------------------------------


def test_epic_add_related_warning_is_escaped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    _item(repo, "work/EPIC-001.md", "EPIC-001", "epic")
    _item(repo, "work/FEAT-010.md", EVIL_YAML, extra="related: {a: 1}\n")
    out = _run(repo, ["epic", "add", "EPIC-001", EVIL], monkeypatch)
    _assert_escaped(out, "`related:` is a dict")


def test_epic_add_not_found_warning_is_escaped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    _item(repo, "work/EPIC-001.md", "EPIC-001", "epic")
    out = _run(repo, ["epic", "add", "EPIC-001", EVIL], monkeypatch)
    _assert_escaped(out, "Warning: Item")


# --- 5. metrics error, git failure messages -------------------------------------------


def test_metrics_error_is_escaped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo(tmp_path)

    def metrics(self: KanbanService, item_id: str) -> dict[str, Any]:
        return {"error": f"bad history: {EVIL}"}

    monkeypatch.setattr(KanbanService, "get_flow_metrics", metrics)
    _assert_escaped(_run(repo, ["metrics", "FEAT-001"], monkeypatch), "bad history")


def test_create_push_git_message_is_escaped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)

    def push(self: KanbanService, **kwargs: object) -> dict[str, Any]:
        return {"success": False, "message": f"git said: {EVIL}"}

    monkeypatch.setattr(KanbanService, "create_item_and_push", push)
    out = _run(repo, ["create", "feature", "T", "--push"], monkeypatch)
    _assert_escaped(out, "Failed: git said")


def test_next_id_git_message_is_escaped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)

    def allocate(self: KanbanService, **kwargs: object) -> dict[str, Any]:
        return {"success": False, "message": f"git said: {EVIL}"}

    monkeypatch.setattr(KanbanService, "allocate_next_id", allocate)
    out = _run(repo, ["next-id", "FEAT"], monkeypatch)
    _assert_escaped(out, "Failed to allocate ID: git said")


# --- 6. argv-only ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("args", "needle"),
    [
        (["list", "--status", EVIL], "Unknown status:"),
        (["list", "--type", EVIL], "Unknown type:"),
        (["create", EVIL, "T"], "Unknown type:"),
        (["move", "FEAT-001", EVIL, "--no-commit"], "Unknown status:"),
    ],
    ids=["list-status", "list-type", "create-type", "move-status"],
)
def test_argv_value_in_error_is_escaped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, args: list[str], needle: str
) -> None:
    _assert_escaped(_run(_repo(tmp_path), args, monkeypatch), needle)


# --- controls -------------------------------------------------------------------------


def test_printable_values_are_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    _doubled(monkeypatch)
    out = _plain(_run(repo, ["validate"], monkeypatch))
    assert "DUPLICATE ID: FEAT-001\n" in out, repr(out)
    out = _plain(_run(repo, ["list", "--status", "bogus"], monkeypatch))
    assert "Unknown status: bogus\n" in out, repr(out)


def test_markup_in_argv_is_still_escaped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = _plain(_run(_repo(tmp_path), ["list", "--type", "[bold]x[/bold]"], monkeypatch))
    assert "Unknown type: [bold]x[/bold]\n" in out, repr(out)


# --- static ---------------------------------------------------------------------------

# Expressions (as `ast.unparse` spells them) an error/warning line may interpolate
# without `safe()`: none can carry text from a repo file or argv.
KNOWN_SAFE = {
    "len(items)",  # int
    "len(issues)",  # int
    "', '.join(PRIORITIES)",  # our own constant tuple of priority names
    "s['hypotheses']",  # hdd summary counts: len(...) in validate_hdd_links
    "s['experiments']",
    "s['measures']",
    "referenced",  # int: s['measures'] - len(measure_warns)
    "unused",  # int: len(measure_warns)
    "exp['downstream_impact']",  # int: a sum of counts in the blocking-chain analysis
    "type(related).__name__",  # a Python builtin type name (dict, int, ...)
    # the one priority wording: a non-printable value is shown as its repr (#190)
    "escape(unknown_priority_message(value))",
}
ALARM = re.compile(r"\[(?:bold )?(?:red|yellow)\]")


def _alarm_lines(name: str) -> list[tuple[int, list[str]]]:
    """(line, interpolated expressions) of each `console.print(f"...[red]...")`."""
    tree = ast.parse((SRC / name).read_text())
    found = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "print"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "console"
            and node.args
            and isinstance(node.args[0], ast.JoinedStr)
        ):
            continue
        parts = node.args[0].values
        text = "".join(p.value for p in parts if isinstance(p, ast.Constant))
        if ALARM.search(text):
            exprs = [ast.unparse(p.value) for p in parts if isinstance(p, ast.FormattedValue)]
            found.append((node.lineno, exprs))
    return found


@pytest.mark.parametrize("name", ["cli.py", "hdd_commands.py", "epic_commands.py"])
def test_error_and_warning_lines_interpolate_only_safe_values(name: str) -> None:
    lines = _alarm_lines(name)
    assert lines, f"{name}: no [red]/[yellow] f-string lines found (check is vacuous)"
    bad = [
        (lineno, expr)
        for lineno, exprs in lines
        for expr in exprs
        if not expr.startswith("safe(") and expr not in KNOWN_SAFE
    ]
    assert not bad, f"{name}: values not routed through safe(): {bad}"
