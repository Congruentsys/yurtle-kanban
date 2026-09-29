"""Issue #1085 — epic "not found" errors escape the user-supplied ID on stderr.

Found while fixing #1080 (PR #1084). ``epic_commands.py`` raised
``click.ClickException(f"... {fold_id(some_id)} not found")`` without ``safe()``
(#251), so ``yurtle-kanban epic add EPIC-001 $'X\\x1b[2J\\nfake line'`` put a raw
ESC sequence and a newline on stderr: the error could clear the screen or forge a
line. ``test_270``'s ``test_epic_add_not_found_warning_is_escaped`` reads only the
module console buffer, so it missed this path.

Reds: the three epic paths (``epic show``, ``epic add`` with an unknown epic, and
``epic add`` with an unknown item) run with an ID holding ESC + newline; stderr must
hold the escaped text (``\\x1b`` / ``\\n``), no raw ESC and no forged line. The
runner passes ``color=True`` so click does not strip ANSI itself, as it would not
on a real terminal.

Static sweep, the rule pinned here: in every module under ``src/``, a
``ClickException`` whose message is an f-string interpolates only
``escape_nonprintable(...)`` or ``safe(...)`` calls (wrapping ``fold_id(x)`` is
fine). No allowlist. Click prints a ClickException as plain text, not Rich markup,
so the fixed sites use ``escape_nonprintable()``: ``safe()``'s markup escape would
add a stray backslash to a legitimate ``[...]``. Control: an ID holding ``[#x]``
(folded ``[#X]``, a form Rich's ``escape()`` does backslash, unlike ``[X]``) shows
verbatim, no backslash.
"""

from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from yurtle_kanban import cli
from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import main

SRC = Path(cli.__file__).resolve().parent
EVIL = "X\x1b[2J\nFORGED"
PLAIN = "kanban:\n  theme: software\n  paths:\n    root: work/\n"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)


def _item(repo: Path, rel: str, item_id: str, item_type: str = "feature") -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'---\nid: "{item_id}"\ntitle: "t"\ntype: {item_type}\nstatus: backlog\n'
        f"priority: medium\ncreated: 2026-09-29\n---\n\n# t\n"
    )


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    config_mod._theme_cache.clear()
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@t.com")
    _git(tmp_path, "config", "user.name", "T")
    (tmp_path / ".kanban").mkdir()
    (tmp_path / ".kanban" / "config.yaml").write_text(PLAIN)
    _item(tmp_path, "work/FEAT-001.md", "FEAT-001")
    _item(tmp_path, "work/EPIC-001.md", "EPIC-001", "epic")
    monkeypatch.chdir(tmp_path)
    yield tmp_path
    config_mod._theme_cache.clear()


def _stderr(args: list[str]) -> str:
    result = CliRunner().invoke(main, args, color=True)
    assert result.exit_code == 1, (result.output, result.stderr, result.exception)
    return re.sub(r"\x1b\[[0-9;]*m", "", result.stderr)  # colour codes only


@pytest.mark.parametrize(
    "args",
    [
        ["epic", "show", EVIL],
        ["epic", "add", EVIL, "FEAT-001"],
        ["epic", "add", "EPIC-001", EVIL],
    ],
    ids=["show-epic", "add-epic", "add-item"],
)
def test_epic_not_found_error_is_escaped_on_stderr(repo: Path, args: list[str]) -> None:
    err = _stderr(args)
    assert "not found" in err, repr(err)
    assert "\x1b" not in err, f"raw ESC reached stderr: {err!r}"
    assert not any(line.startswith("FORGED") for line in err.splitlines()), repr(err)
    assert "\\x1b[2J\\nFORGED" in err, repr(err)


def test_printable_id_is_unchanged(repo: Path) -> None:
    err = _stderr(["epic", "show", "EPIC-404"])
    assert "Error: EPIC-404 not found\n" in err, repr(err)


@pytest.mark.parametrize(
    "args",
    [
        ["epic", "show", "A[#x]"],
        ["epic", "add", "A[#x]", "FEAT-001"],
        ["epic", "add", "EPIC-001", "A[#x]"],
    ],
    ids=["show-epic", "add-epic", "add-item"],
)
def test_brackets_in_id_are_verbatim_no_backslash(repo: Path, args: list[str]) -> None:
    # plain text, not markup: `safe()` would print `A\[#X]` here
    err = _stderr(args)
    assert "A[#X] not found\n" in err, repr(err)
    assert "\\" not in err, repr(err)


# --- static ---------------------------------------------------------------------------


def _click_exception_fstrings() -> list[tuple[str, int, list[str]]]:
    """(file, line, interpolated exprs) of each `ClickException(f"...")` in src/."""
    found = []
    for path in sorted(SRC.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if not (isinstance(node, ast.Call) and node.args):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name != "ClickException" or not isinstance(node.args[0], ast.JoinedStr):
                continue
            values = [p.value for p in node.args[0].values if isinstance(p, ast.FormattedValue)]
            found.append((path.name, node.lineno, values))
    return [
        (name, lineno, [ast.unparse(v) for v in values if not _is_safe(v)])
        for name, lineno, values in found
    ]


def _is_safe(expr: ast.expr) -> bool:
    return (
        isinstance(expr, ast.Call)
        and isinstance(expr.func, ast.Name)
        # plain text: escape_nonprintable, never safe()'s markup escape ([steer] on #1085)
        and expr.func.id == "escape_nonprintable"
    )


def test_click_exception_fstrings_interpolate_only_safe() -> None:
    sites = _click_exception_fstrings()
    assert len(sites) >= 3, f"ClickException f-strings not found (check is vacuous): {sites}"
    bad = [(name, lineno, exprs) for name, lineno, exprs in sites if exprs]
    assert not bad, f"ClickException values not routed through escape_nonprintable()/safe(): {bad}"
