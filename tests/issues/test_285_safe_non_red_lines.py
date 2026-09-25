"""#285: the non-red context lines print repo-derived text through `_click.safe()`.

Follow-up from the review of PR #282 (#270). These lines used plain `escape()`, which
escapes Rich markup only, so ESC, newline and bidi characters from a repo file name,
frontmatter or argv still reached the terminal raw:

1. `validate`: the `File 1:` / `File 2:` (duplicate id) and `File:` (filename mismatch)
   path lines. The path is a real file whose NAME carries ESC + newline.
2. hdd `_update_parent`: the dim `Updated {parent_id} with inverse reference` line.
3. `hdd critical-path`: the `Chain:`, `Implements:` and `Assignee:` lines (ids, status
   and assignee as the service hands them over from frontmatter).
4. The hdd `create` commands' `File:` line, with and without `--push`: the item is
   created under a type directory whose name carries ESC + newline.

Each is captured through every module console swapped for a
`Console(force_terminal=True)`, and each assertion looks at the one line under test.
Controls: printable output is unchanged, and `[bold]` in a value is still literal.
"""

from __future__ import annotations

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

EVIL = "X\x1b[2J\nFORGED"
ESCAPED = "X\\x1b[2J\\nFORGED"  # what `safe()` shows for EVIL
EVIL_YAML = "X\\e[2J\\nFORGED"  # YAML double-quoted escapes for the same text

PLAIN = "kanban:\n  theme: software\n  paths:\n    root: work/\n"
HDD = (
    "kanban:\n  theme: hdd\n  paths:\n    root: research/\n    scan_paths:\n"
    "      - research/papers/\n      - research/hypotheses/\n"
)

# --- helpers --------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)


def _item(repo: Path, rel: str, item_id: str, item_type: str = "feature") -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'---\nid: "{item_id}"\ntitle: "t"\ntype: {item_type}\nstatus: backlog\n'
        f"priority: medium\ncreated: 2026-09-25\n---\n\n# t\n"
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
    tty = Console(file=buf, force_terminal=True, width=400)
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


def _line(out: str, needle: str) -> str:
    """The one output line that starts (after indent) with `needle`."""
    lines = [ln for ln in _plain(out).splitlines() if ln.lstrip().startswith(needle)]
    assert lines, f"expected line missing: {needle!r} in {out!r}"
    assert len(lines) == 1, f"{needle!r} printed {len(lines)} times: {out!r}"
    return lines[0]


def _assert_line_escaped(out: str, needle: str) -> None:
    line = _line(out, needle)
    assert "\x1b" not in line, f"raw ESC on the {needle!r} line: {line!r}"
    assert ESCAPED in line, f"{needle!r} line doesn't show the escaped text: {line!r}"
    plain = _plain(out)
    assert not any(ln.startswith("FORGED") for ln in plain.splitlines()), repr(out)


def _assert_all_escaped(out: str, needle: str) -> None:
    _assert_line_escaped(out, needle)
    assert "\x1b" not in _plain(out), f"raw ESC reached the terminal: {out!r}"


# --- 1. validate: File 1 / File 2 / File path lines -----------------------------------


def _doubled(monkeypatch: pytest.MonkeyPatch) -> None:
    """The scan keys items by id, so a duplicate id reaches `validate` only when
    `get_items` returns it twice: make it do so."""
    real = KanbanService.get_items

    def get_items(self: KanbanService, *args: Any, **kwargs: Any) -> list[Any]:
        items = real(self, *args, **kwargs)
        return items + items

    monkeypatch.setattr(KanbanService, "get_items", get_items)


def _evil_named_file(repo: Path) -> None:
    # a real file whose name carries ESC + newline (macOS and Linux both allow it);
    # its stem doesn't start with its id, so it's also a FILENAME MISMATCH
    _item(repo, f"work/{EVIL}.md", "FEAT-010")


@pytest.mark.parametrize("needle", ["File 1:", "File 2:", "File:"])
def test_validate_path_lines_are_escaped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, needle: str
) -> None:
    repo = _repo(tmp_path)
    _evil_named_file(repo)
    _doubled(monkeypatch)
    out = _run(repo, ["validate"], monkeypatch)
    # sanity: the fixture really reached both reports
    assert "DUPLICATE ID:" in out and "FILENAME MISMATCH:" in out, repr(out)
    evil_lines = [ln for ln in _plain(out).splitlines() if ln.lstrip().startswith(needle)]
    assert evil_lines, f"expected line missing: {needle!r} in {out!r}"
    hits = [ln for ln in evil_lines if "FORGED" in ln or "\x1b" in ln]
    assert hits, f"no {needle!r} line names the evil file: {out!r}"
    for ln in hits:
        assert "\x1b" not in ln, f"raw ESC on the {needle!r} line: {ln!r}"
        assert ESCAPED in ln, f"{needle!r} line doesn't show the escaped text: {ln!r}"


def test_validate_path_lines_forge_no_output_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    _evil_named_file(repo)
    _doubled(monkeypatch)
    plain = _plain(_run(repo, ["validate"], monkeypatch))
    assert "\x1b" not in plain, f"raw ESC reached the terminal: {plain!r}"
    assert not any(ln.startswith("FORGED") for ln in plain.splitlines()), repr(plain)


# --- 2. hdd: the dim `Updated {parent_id}` line ---------------------------------------


def test_update_parent_line_is_escaped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_update_parent` is reached from the create commands with a parent id; the CLI's
    own template refuses an id carrying ESC before it gets there, so the helper is
    driven directly with the id it would be handed."""
    buf = io.StringIO()
    monkeypatch.setattr(
        hdd_commands, "console", Console(file=buf, force_terminal=True, width=400)
    )

    class _Service:
        def update_parent_turtle_block(self, *args: Any, **kwargs: Any) -> bool:
            return True

    hdd_commands._update_parent(_Service(), EVIL, "literature", "LIT-001", push=False)
    _assert_all_escaped(buf.getvalue(), "Updated")


# --- 3. hdd critical-path: Chain / Implements / Assignee ------------------------------


def _experiment(**over: Any) -> dict[str, Any]:
    exp: dict[str, Any] = {
        "experiment_id": "EXPR-001",
        "title": "t",
        "readiness": "blocked_by_dev",
        "hypothesis_id": "H1.1",
        "paper_id": "PAPER-1",
        "implements": ["EXP-001"],
        "implements_status": {"EXP-001": "in_progress"},
        "assignee": "Mini",
        "runs": 0,
        "downstream_impact": 0,
    }
    exp.update(over)
    return exp


def _critical_path(
    repo: Path, monkeypatch: pytest.MonkeyPatch, exp: dict[str, Any]
) -> str:
    def get_critical_path(self: KanbanService, **kwargs: Any) -> list[dict[str, Any]]:
        return [exp]

    monkeypatch.setattr(KanbanService, "get_critical_path", get_critical_path)
    return _run(repo, ["hdd", "critical-path"], monkeypatch)


@pytest.mark.parametrize(
    ("over", "needle"),
    [
        ({"hypothesis_id": "H1" + EVIL}, "Chain:"),
        ({"paper_id": "PAPER-1" + EVIL}, "Chain:"),
        (
            {"implements": [EVIL], "implements_status": {EVIL: "in_progress"}},
            "Implements:",
        ),
        ({"implements_status": {"EXP-001": EVIL}}, "Implements:"),
        ({"assignee": EVIL}, "Assignee:"),
    ],
    ids=["chain-hypothesis", "chain-paper", "implements-id", "implements-status", "assignee"],
)
def test_critical_path_lines_are_escaped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, over: dict[str, Any], needle: str
) -> None:
    repo = _repo(tmp_path, HDD)
    out = _critical_path(repo, monkeypatch, _experiment(**over))
    _assert_all_escaped(out, needle)


# --- 4. hdd create: the File: line ----------------------------------------------------

CREATE = [
    ["idea", "create", "T"],
    ["literature", "create", "T"],
    ["paper", "create", "130", "T"],
    ["hypothesis", "create", "S", "--paper", "130"],
    ["experiment", "create", "--title", "T"],
    ["measure", "create", "T", "--unit", "u", "--category", "c"],
]
CREATE_IDS = ["idea", "literature", "paper", "hypothesis", "experiment", "measure"]


def _evil_type_dir(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """New items land in a real directory whose name carries ESC + newline."""
    evil_dir = repo / "research" / ("d" + EVIL)

    def type_dir(self: KanbanService, *args: Any, **kwargs: Any) -> Path:
        return evil_dir

    monkeypatch.setattr(KanbanService, "_get_type_directory", type_dir)


@pytest.mark.parametrize("args", CREATE, ids=CREATE_IDS)
def test_hdd_create_file_line_is_escaped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, args: list[str]
) -> None:
    repo = _repo(tmp_path, HDD)
    _evil_type_dir(repo, monkeypatch)
    out = _run(repo, args, monkeypatch)
    assert "Created" in _plain(out), repr(out)
    _assert_all_escaped(out, "File:")


@pytest.mark.parametrize("args", CREATE, ids=CREATE_IDS)
def test_hdd_create_push_file_line_is_escaped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, args: list[str]
) -> None:
    """`--push` needs a remote; the push is replaced by a real local create that reports
    success, so the command prints the created item's `File:` line."""
    repo = _repo(tmp_path, HDD)
    _evil_type_dir(repo, monkeypatch)

    def push(self: KanbanService, **kwargs: Any) -> dict[str, Any]:
        item = self.create_item(**kwargs)
        return {"success": True, "pushed": True, "id": item.id, "item": item}

    monkeypatch.setattr(KanbanService, "create_item_and_push", push)
    out = _run(repo, [*args, "--push"], monkeypatch)
    assert "Created and pushed" in _plain(out), repr(out)
    _assert_all_escaped(out, "File:")


# --- controls -------------------------------------------------------------------------


def test_printable_validate_output_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    _item(repo, "work/Other-Name.md", "FEAT-010")
    _doubled(monkeypatch)
    out = _plain(_run(repo, ["validate"], monkeypatch))
    work = repo / "work"
    assert f"  File 1: {work / 'FEAT-001.md'}\n" in out, repr(out)
    assert f"  File 2: {work / 'FEAT-001.md'}\n" in out, repr(out)
    assert f"  File: {work / 'Other-Name.md'}\n" in out, repr(out)


def test_printable_critical_path_output_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, HDD)
    out = _plain(_critical_path(repo, monkeypatch, _experiment()))
    assert "    Chain: H1.1 → PAPER-1\n" in out, repr(out)
    assert "    Implements: EXP-001 ✗ (in_progress)\n" in out, repr(out)
    assert "    Assignee: Mini\n" in out, repr(out)


def test_printable_hdd_create_file_line_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, HDD)
    out = _plain(_run(repo, ["idea", "create", "T"], monkeypatch))
    line = _line(out, "File:")
    assert line.startswith("  File: ") and line.endswith("IDEA-R-001-T.md"), repr(line)
    assert Path(line.removeprefix("  File: ")).exists(), repr(line)


def test_markup_in_values_is_still_literal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, HDD)
    out = _plain(
        _critical_path(
            repo, monkeypatch, _experiment(assignee="[bold]x[/bold]", paper_id="[bold]P")
        )
    )
    assert "    Assignee: [bold]x[/bold]\n" in out, repr(out)
    assert "    Chain: H1.1 → [bold]P\n" in out, repr(out)
