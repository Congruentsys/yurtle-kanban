"""Issue #1090 — the remaining red refusal lines still print on stdout.

Found while fixing #1086 (PR #1089): #1086's sweep caught ``console.print("[red]Error…")``
followed by an exit, but other red refusals followed by ``sys.exit`` still printed
on stdout: ``Unknown type``, ``Failed: …``, ``Item not found``, ``Unknown preset``,
``Invalid WIP limit``, ``Board already exists``, ``Can't upgrade to multi-board``,
``Unknown format``, ``Failed to allocate ID``, the bare ``[red]{e}[/red]`` refusals
and ``query``'s usage line.

Expected (the [steer] ruling, by #1080 as #1086 applied it): every text-mode refusal
goes through ``refuse()`` — on stderr, its exit code kept, a JSON refusal under
``--json``. Report lines that don't exit (``validate``'s ``DUPLICATE ID`` rows) stay
as they are.

Reds: each behavioural test below pins exit code unchanged, stdout empty, the
message on stderr. Where the command takes ``--json`` the JSON refusal (one object
on stdout, same exit code) is pinned too; those halves already hold today and
guard the fix. ``export``'s ``Unknown format`` is unreachable from argv
(``--format`` is a ``click.Choice``), so only the sweep covers it.

The static sweep's rule (pinned here, widening #1086's): in ``cli.py``,
``hdd_commands.py`` and ``epic_commands.py``, no ``console.print`` /
``err_console.print`` whose first argument's literal text starts with a red markup
tag (plain or f-string, leading spaces ignored) is followed in its block — next, or
after only further ``console.print`` / ``err_console.print`` lines (a ``Valid
types:`` / ``Available presets:`` / ``Example:`` hint) — by an exit
(``sys.exit(...)``, ``ctx.exit(...)`` or ``raise SystemExit(...)``). Such a run is
a refusal written by hand; it goes through ``refuse()`` instead.

Widened by #1099: in those files, no function may hold such a red print with a
non-zero exit anywhere LATER in the same function (not in a nested def) — red rows
in a ``for`` loop and ``sys.exit(1)`` in a later ``if invalid:`` is the same
refusal (``list --priority`` before #1098 r1 F1). The report commands that exit
after red report rows (``validate``, ``hdd validate``) are allowlisted by (file,
function) in ``REPORT_COMMANDS``, each with its reason.

Widened by #1108: a red markup tag is any leading ``[...]`` tag whose
whitespace-separated style words include ``red`` (``[red]``, ``[bold red]``,
``[red bold]``, ``[bold red on white]``) — the style is incidental to a refusal.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner, Result

from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import main
from yurtle_kanban.service import KanbanService

PKG = Path(__file__).resolve().parents[2] / "src" / "yurtle_kanban"
SWEPT = ("cli.py", "hdd_commands.py", "epic_commands.py")

CONFIG = """\
kanban:
  theme: software
  paths:
    root: kanban-work/
    scan_paths:
    - "kanban-work/"
    ignore:
      - "**/_TEMPLATE*"
"""

# single-board, scanning two places no one path covers: board-add can't upgrade (#122)
SPLIT_CONFIG = """\
kanban:
  theme: software
  paths:
    root: kanban-work/
    scan_paths:
    - "kanban-work/"
    - "elsewhere/"
"""

MULTI_CONFIG = """\
version: "2.0"
boards:
  - name: dev
    preset: software
    path: "kanban-work/"
"""

ITEM = """\
---
id: {id}
title: "Hello"
type: feature
status: backlog
priority: medium
created: 2026-01-01
---

# {id}: Hello
"""


@pytest.fixture(autouse=True)
def _clean_theme_cache() -> Iterator[None]:
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)


def _make_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, config: str) -> Path:
    """A git repo (no remote) with FEAT-001 in backlog under `config`; the cwd."""
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    monkeypatch.delenv("YURTLE_AGENT", raising=False)
    repo = tmp_path / "repo"
    (repo / ".kanban").mkdir(parents=True)
    (repo / ".kanban" / "config.yaml").write_text(config)
    features = repo / "kanban-work" / "features"
    features.mkdir(parents=True)
    (features / "FEAT-001-hello.md").write_text(ITEM.format(id="FEAT-001"))
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    monkeypatch.chdir(repo)
    return repo


@pytest.fixture
def board(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return _make_repo(tmp_path, monkeypatch, CONFIG)


def _run(args: list[str]) -> Result:
    # click 8.2+: result.stdout and result.stderr are captured separately
    return CliRunner().invoke(main, args)


def _shown(result: Result) -> str:
    return (
        f"exit {result.exit_code}\n--- stdout ---\n{result.stdout}"
        f"\n--- stderr ---\n{result.stderr}"
    )


def _assert_on_stderr(result: Result, code: int, *lines: str) -> None:
    shown = _shown(result)
    assert result.exit_code == code, shown
    for line in lines:
        assert line in result.stderr, shown
        assert line not in result.stdout, shown
    assert result.stdout.strip() == "", shown


def _assert_json_refusal(result: Result, code: int, error_part: str) -> None:
    shown = _shown(result)
    assert result.exit_code == code, shown
    payload = json.loads(result.stdout)  # the whole of stdout: exactly one object
    assert isinstance(payload, dict), shown
    assert payload["success"] is False, shown
    assert error_part in payload["error"], shown


# --- create ---------------------------------------------------------------------------


def test_create_unknown_type_on_stderr(board: Path) -> None:
    """cli.py `create`: `Unknown type` + its `Valid types:` hint, exit 1."""
    result = _run(["create", "bogus", "T"])
    _assert_on_stderr(result, 1, "Unknown type: bogus", "Valid types:")


def test_create_push_failed_on_stderr(board: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """cli.py `create --push`: a failed push is `Failed: <message>`, exit 1."""

    def push(self: KanbanService, **kwargs: object) -> dict[str, Any]:
        return {"success": False, "message": "git said no"}

    monkeypatch.setattr(KanbanService, "create_item_and_push", push)
    result = _run(["create", "feature", "T", "--push"])
    _assert_on_stderr(result, 1, "Failed: git said no")


# --- show -----------------------------------------------------------------------------


def test_show_item_not_found_on_stderr(board: Path) -> None:
    result = _run(["show", "NOSUCH-1"])
    _assert_on_stderr(result, 1, "Item not found: NOSUCH-1")


def test_show_item_not_found_json_is_one_object(board: Path) -> None:
    """Guard: `show --json` already refuses in JSON (the fix must keep it)."""
    _assert_json_refusal(_run(["show", "NOSUCH-1", "--json"]), 1, "Item not found: NOSUCH-1")


# --- board-add ------------------------------------------------------------------------


def test_board_add_unknown_preset_on_stderr(board: Path) -> None:
    result = _run(["board-add", "research", "--preset", "bogus", "--path", "research/"])
    _assert_on_stderr(result, 1, "Unknown preset: bogus", "Available presets:")


def test_board_add_invalid_wip_limit_on_stderr(board: Path) -> None:
    result = _run(
        ["board-add", "research", "--path", "research/", "--wip-limit", "in_progress:abc"]
    )
    _assert_on_stderr(result, 1, "Invalid WIP limit: in_progress:abc")


def test_board_add_cant_upgrade_on_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_repo(tmp_path, monkeypatch, SPLIT_CONFIG)
    result = _run(["board-add", "research", "--path", "research/"])
    _assert_on_stderr(result, 1, "Can't upgrade to multi-board", "was not changed")


def test_board_add_already_exists_on_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_repo(tmp_path, monkeypatch, MULTI_CONFIG)
    result = _run(["board-add", "dev", "--path", "other/"])
    _assert_on_stderr(result, 1, "Board 'dev' already exists")


# --- bare `[red]{e}[/red]` refusals ---------------------------------------------------


def test_rank_refusal_on_stderr(board: Path) -> None:
    """cli.py `rank`: the service's refusal as a bare red `{e}` line, exit 1."""
    result = _run(["rank", "FEAT-001", "0", "--no-commit"])
    _assert_on_stderr(result, 1, "Rank must be >= 1, got 0")


def test_invalid_config_on_stderr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """cli.py `get_service`: a config value of the wrong kind (#220), exit 1."""
    _make_repo(tmp_path, monkeypatch, "kanban:\n  theme: 5\n")
    result = _run(["list"])
    _assert_on_stderr(result, 1, "Invalid ", "`theme` must be a string theme name")


def test_invalid_config_json_is_one_object(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Guard: under `--json` the same refusal is one JSON object (#877)."""
    _make_repo(tmp_path, monkeypatch, "kanban:\n  theme: 5\n")
    _assert_json_refusal(_run(["list", "--json"]), 1, "`theme` must be a string theme name")


def test_undecodable_argv_on_stderr(board: Path) -> None:
    """cli.py `_Main`: an argument that isn't valid UTF-8 (#193), exit 1."""
    result = _run(["show", "FEAT-\udcff"])
    shown = _shown(result)
    assert result.exit_code == 1, shown
    assert result.stdout.strip() == "", shown
    assert "argument 2" in result.stderr, shown


# --- next-id --------------------------------------------------------------------------


def _fail_allocation(monkeypatch: pytest.MonkeyPatch) -> None:
    def allocate(self: KanbanService, **kwargs: object) -> dict[str, Any]:
        return {"success": False, "id": None, "prefix": "FEAT", "number": None,
                "message": "git said no"}

    monkeypatch.setattr(KanbanService, "allocate_next_id", allocate)


def test_next_id_failed_on_stderr(board: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fail_allocation(monkeypatch)
    result = _run(["next-id", "FEAT"])
    _assert_on_stderr(result, 1, "Failed to allocate ID: git said no")


def test_next_id_failed_json_is_one_object(
    board: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Guard: `next-id --json` already answers a failure in JSON, exit 1 (#590, #847)."""
    _fail_allocation(monkeypatch)
    _assert_json_refusal(_run(["next-id", "FEAT", "--json"]), 1, "git said no")


# --- query ----------------------------------------------------------------------------


def test_query_without_a_query_on_stderr(board: Path) -> None:
    result = _run(["query", "--no-semantic"])
    _assert_on_stderr(
        result, 1, "Provide a query string, --sparql, or --semantic.", "Example:"
    )


def test_query_without_a_query_json_is_one_object(board: Path) -> None:
    """Guard: `query --json` already refuses in JSON (#877)."""
    _assert_json_refusal(
        _run(["query", "--no-semantic", "--json"]), 1, "Provide a query string"
    )


# --- static sweep -------------------------------------------------------------------


_PRINTERS = ("console", "err_console")


def _literal_start(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr) and node.values:
        first = node.values[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            return first.value
    return None


def _print_call(stmt: ast.stmt) -> ast.Call | None:
    """`console.print(...)` / `err_console.print(...)` as a statement, else None."""
    if not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)):
        return None
    func = stmt.value.func
    if (
        isinstance(func, ast.Attribute)
        and func.attr == "print"
        and isinstance(func.value, ast.Name)
        and func.value.id in _PRINTERS
    ):
        return stmt.value
    return None


_LEADING_TAG = re.compile(r"\[([^\[\]]*)\]")


# any Rich colour name ending in `red` (`bright_red`, `orange_red1`), matched on the
# lowercased word; numeric colours (`#ff0000`, `rgb()`, `color(N)`) aren't swept:
# src markup uses named colours only ([steer] on #1117)
_RED = re.compile(r"(?:[a-z]+_)*red\d*")


def _is_red_print(stmt: ast.stmt) -> bool:
    """A print whose text opens with a markup tag with a red among its style words
    (#1108): `red`, and Rich's `bright_red`, `dark_red`, `red1`… (#1114), in any
    case, and any other `…_red` name (#1117)."""
    call = _print_call(stmt)
    if call is None or not call.args:
        return False
    text = _literal_start(call.args[0])
    if text is None:
        return False
    tag = _LEADING_TAG.match(text.lstrip())
    return tag is not None and any(_RED.fullmatch(w.lower()) for w in tag.group(1).split())


def _is_exit(stmt: ast.stmt) -> bool:
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
        func = stmt.value.func
        return (
            isinstance(func, ast.Attribute)
            and func.attr == "exit"
            and isinstance(func.value, ast.Name)
            and func.value.id in ("sys", "ctx")
        )
    if isinstance(stmt, ast.Raise) and stmt.exc is not None:
        exc = stmt.exc.func if isinstance(stmt.exc, ast.Call) else stmt.exc
        return isinstance(exc, ast.Name) and exc.id == "SystemExit"
    return False


def _refusals_in(source: str, name: str) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(source, filename=name)):
        for field in ("body", "orelse", "finalbody"):
            block = getattr(node, field, None)
            if not isinstance(block, list) or not block or not isinstance(block[0], ast.stmt):
                continue
            for i, stmt in enumerate(block):
                if not _is_red_print(stmt):
                    continue
                rest = block[i + 1:]
                j = 0
                while j < len(rest) and _print_call(rest[j]) is not None:
                    j += 1  # a hint line printed along with the refusal
                if j < len(rest) and _is_exit(rest[j]):
                    found.append(f"{name}:{stmt.lineno}")
    return found


def _is_nonzero_exit(stmt: ast.stmt) -> bool:
    """An exit whose code isn't a literal 0 / absent (`sys.exit()` is a success)."""
    if not _is_exit(stmt):
        return False
    call = stmt.value if isinstance(stmt, ast.Expr) else stmt.exc  # type: ignore[attr-defined]
    if not isinstance(call, ast.Call):
        return False  # a bare `raise SystemExit` exits 0
    if not call.args:
        return False
    code = call.args[0]
    return not (isinstance(code, ast.Constant) and code.value in (0, None))


def _own_statements(func: ast.FunctionDef | ast.AsyncFunctionDef) -> Iterator[ast.stmt]:
    """Every statement in `func`'s body, not descending into nested defs/classes."""
    stack: list[ast.AST] = list(reversed(func.body))
    while stack:
        node = stack.pop()
        if isinstance(node, ast.stmt):
            yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        stack.extend(reversed(list(ast.iter_child_nodes(node))))


# Functions that print red REPORT rows (one per finding) and then exit non-zero
# because findings exist — the exit is the report's verdict, not a refusal of
# the command, so the rows stay on stdout (#1090's ruling: report lines stay).
# #1099's function-wide rule skips these; the same-block rule above still applies.
# Keyed by (file, function) (#1108): a `validate` elsewhere is swept like any other.
REPORT_COMMANDS: frozenset[tuple[str, str]] = frozenset(
    {
        # cli.py `validate`: `DUPLICATE ID` / `DEPENDENCY CYCLE` / `BAD CONTROL FILE`
        # rows, one per issue found; `sys.exit(1)` afterwards says "issues found".
        ("cli.py", "validate"),
        # hdd_commands.py `hdd validate`: an indented `Error:` row per broken
        # hypothesis/experiment link; `sys.exit(1)` afterwards says "errors found".
        ("hdd_commands.py", "hdd_validate"),
    }
)


def _sibling_refusals_in(source: str, name: str) -> list[str]:
    """#1099: a red print with a non-zero exit LATER in the same function — in a
    sibling or enclosing block (red rows in a `for`, then `if invalid: sys.exit(1)`),
    which the same-block rule above can't see. Report commands are allowlisted."""
    found = []
    for node in ast.walk(ast.parse(source, filename=name)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if (name, node.name) in REPORT_COMMANDS:
            continue
        stmts = list(_own_statements(node))
        exits = [s.lineno for s in stmts if _is_nonzero_exit(s)]
        for stmt in stmts:
            if _is_red_print(stmt) and any(line > stmt.lineno for line in exits):
                found.append(f"{name}:{stmt.lineno} ({node.name})")
    return found


def _hand_written_refusals(path: Path) -> list[str]:
    source = path.read_text()
    return sorted(
        set(_refusals_in(source, path.name)) | set(_sibling_refusals_in(source, path.name))
    )


@pytest.mark.parametrize(
    "snippet",
    [
        'console.print("[red]Unknown type: x[/red]")\nsys.exit(1)\n',
        'console.print(f"  [red]Failed: {m}[/red]")\nraise SystemExit(1)\n',
        'err_console.print(f"[red]{e}[/red]", soft_wrap=True)\nsys.exit(2)\n',
        'console.print(f"[red]{e}[/red]")\nctx.exit(1)\n',
        'console.print("[red]Unknown preset[/red]")\nconsole.print("[dim]hint[/dim]")\n'
        "sys.exit(1)\n",
        'def f():\n    if x:\n        console.print("[red]nope[/red]")\n        sys.exit(1)\n',
        'try:\n    pass\nexcept E:\n    console.print(f"[red]{e}[/red]")\n    sys.exit(1)\n',
        # #1108: any leading tag whose style words include `red`
        'console.print("[bold red]Error: x[/bold red]")\nsys.exit(1)\n',
        'console.print(f"  [red bold]{e}[/red bold]")\nsys.exit(1)\n',
        'err_console.print("[bold red on white]nope[/]")\nraise SystemExit(2)\n',
        # #1114: Rich's other reds
        'console.print("[bright_red]Error: x[/]")\nsys.exit(1)\n',
        'console.print("[bold dark_red]x[/]")\nsys.exit(1)\n',
        'console.print("[red1]x[/red1]")\nsys.exit(1)\n',
        'console.print("[red3 on black]x[/]")\nsys.exit(1)\n',
        # #1117: any case, and Rich's other `…_red` names
        'console.print("[RED]Error[/]")\nsys.exit(1)\n',
        'console.print("[Bright_Red]x[/]")\nsys.exit(1)\n',
        'console.print("[orange_red1]x[/]")\nsys.exit(1)\n',
        'console.print("[bold indian_red]x[/]")\nsys.exit(1)\n',
    ],
    ids=["plain", "fstring-indented-raise", "err-console", "ctx-exit", "hint-between",
         "nested-if", "except-handler", "bold-red", "red-bold", "bold-red-on-white",
         "bright-red", "dark-red", "red1", "red3-on-black",
         "upper-red", "mixed-case-bright-red", "orange-red1", "indian-red"],
)
def test_sweep_finds_a_hand_written_refusal(snippet: str) -> None:
    assert _refusals_in(snippet, "s.py") != [], snippet


@pytest.mark.parametrize(
    "snippet",
    [
        # a report line that doesn't exit (validate's DUPLICATE ID rows)
        'console.print(f"[red]DUPLICATE ID:[/red] {i}")\nconsole.print("  File 1")\n',
        # red, then other work before the exit: not a bare refusal pair
        'console.print("[red]x[/red]")\nfor p in ps:\n    pass\nsys.exit(1)\n',
        # not red
        'console.print("[yellow]warn[/yellow]")\nsys.exit(1)\n',
        # red only later in the text
        'console.print(f"Status: [red]{s}[/red]")\nsys.exit(1)\n',
        # already through refuse()
        '_refuse(e, "[red]Unknown type: x[/red]")\n',
        # another object's print
        'table.print("[red]x[/red]")\nsys.exit(1)\n',
        # #1108: style words are matched whole, not as substrings of `red`
        'console.print("[redact]x[/redact]")\nsys.exit(1)\n',
        'console.print("[bred]x[/bred]")\nsys.exit(1)\n',
        'console.print("[bold reddish]x[/]")\nsys.exit(1)\n',
        'console.print("[bright_redx]x[/]")\nsys.exit(1)\n',
        'console.print("[red1a]x[/]")\nsys.exit(1)\n',
        # #1117: numeric colours are out of the sweep's scope, by ruling
        'console.print("[#ff0000]x[/]")\nsys.exit(1)\n',
        'console.print("[color(9)]x[/]")\nsys.exit(1)\n',
        'console.print("[rgb(255,0,0)]x[/]")\nsys.exit(1)\n',
        # a red tag only after a leading non-red tag
        'console.print("[bold]x[/bold] [red]y[/red]")\nsys.exit(1)\n',
    ],
    ids=["report-row", "work-between", "not-red", "red-later", "refuse", "other-print",
         "redact", "bred", "bold-reddish", "bright-redx", "red1a", "hex-red", "color-9", "rgb-red",
         "red-in-second-tag"],
)
def test_sweep_leaves_non_refusals_alone(snippet: str) -> None:
    assert _refusals_in(snippet, "s.py") == [], snippet


# #1099: the `list --priority` shape before #1098 r1 F1 — red rows in a `for`,
# the exit in a later sibling `if`
_SIBLING_SHAPE = (
    "def {name}(invalid):\n"
    "    for value in invalid:\n"
    '        console.print(f"[red]{{escape(value)}}[/red]", soft_wrap=True)\n'
    "    if invalid:\n"
    "        sys.exit(1)\n"
)


@pytest.mark.parametrize(
    "snippet",
    [
        _SIBLING_SHAPE.format(name="list_items"),
        # the exit in an enclosing block, after the red print's `if`
        "def f(ok):\n    if not ok:\n        err_console.print(\"[red]bad[/red]\")\n"
        "    cleanup()\n    raise SystemExit(2)\n",
        "def f(ctx, xs):\n    for x in xs:\n        if x:\n"
        "            console.print(\"[red]x[/red]\")\n    ctx.exit(1)\n",
    ],
    ids=["list-priority-shape", "enclosing-raise", "nested-loop-ctx-exit"],
)
def test_sweep_finds_a_red_print_whose_exit_sits_in_a_sibling_block(snippet: str) -> None:
    assert _sibling_refusals_in(snippet, "s.py") != [], snippet


@pytest.mark.parametrize(
    ("file", "func"),
    [("cli.py", "validate"), ("hdd_commands.py", "hdd_validate")],
)
def test_sibling_sweep_skips_the_report_commands(file: str, func: str) -> None:
    """The same shape in a report command: the rows are output, the exit a verdict."""
    assert _sibling_refusals_in(_SIBLING_SHAPE.format(name=func), file) == []


@pytest.mark.parametrize(
    ("file", "func"),
    [
        ("epic_commands.py", "validate"),  # #1108: an `epic validate` isn't exempt
        ("hdd_commands.py", "validate"),
        ("cli.py", "hdd_validate"),
        ("s.py", "validate"),
    ],
)
def test_allowlist_is_keyed_by_file_and_function(file: str, func: str) -> None:
    """#1108: a report command's NAME in another file is swept like any function."""
    assert _sibling_refusals_in(_SIBLING_SHAPE.format(name=func), file) != []


@pytest.mark.parametrize(
    "snippet",
    [
        # the exit belongs to a DIFFERENT function
        'def a():\n    console.print("[red]row[/red]")\n'
        "def b():\n    sys.exit(1)\n",
        # a nested function's exit isn't the outer function's
        'def a():\n    console.print("[red]row[/red]")\n'
        "    def b():\n        sys.exit(1)\n    return b\n",
        # the exit comes BEFORE the red print
        'def a(x):\n    if x:\n        sys.exit(1)\n    console.print("[red]row[/red]")\n',
        # a successful exit is not a refusal
        'def a():\n    for r in rs:\n        console.print("[red]row[/red]")\n'
        "    sys.exit(0)\n",
    ],
    ids=["exit-in-other-function",
         "exit-in-nested-function", "exit-before", "exit-zero"],
)
def test_sibling_sweep_leaves_non_refusals_alone(snippet: str) -> None:
    assert _sibling_refusals_in(snippet, "s.py") == [], snippet


def test_no_hand_written_red_refusal_remains() -> None:
    found = [hit for name in SWEPT for hit in _hand_written_refusals(PKG / name)]
    assert found == [], "`[red]…` line + exit outside refuse(): " + ", ".join(found)
