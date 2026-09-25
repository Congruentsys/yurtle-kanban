"""#251: the CLI escapes control characters, not just Rich markup, in error text.

Follow-up from the review of PR #248 (#234). `cli.py` and `hdd_commands.py` print
exception text as `escape(str(e))`. Rich's `escape()` only escapes markup, so ESC and
newline from a repo file go out raw: a cloned repo can clear the screen or forge output
lines, the injection #215/#234 closed for logging.

Decided behaviour: wherever the CLI prints exception text (or other repo-file-derived text
in an error/warning line) through Rich, control characters are escaped repr-style (`\\x1b`,
`\\n`) as well as markup. Each case prints text carrying `\\x1b[2J\\nFORGED`:

1. `move` blocked by a config gate whose `message:` carries it (`.kanban/config.yaml`).
2. `move` / `rank` / `show` of an ID from argv carrying it (ESC is valid UTF-8, so the
   root guard lets it through).
3. `literature create --idea` when updating the parent raises with it (hdd_commands).
4. Output checked both through CliRunner (the module console writes raw, non-tty) and
   through `cli.console` swapped for a `Console(force_terminal=True)`.

Controls: printable error text is unchanged (`Item not found: NOPE-1` exactly); markup is
still escaped (`[bold]` printed literally); the #220 config value error (its message uses
`!r`) already carries no raw ESC. Static: no bare `escape(str(e))` left in cli.py or
hdd_commands.py.
"""

from __future__ import annotations

import io
import re
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner
from rich.console import Console

from yurtle_kanban import cli, hdd_commands
from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import main
from yurtle_kanban.service import KanbanService

SRC = Path(cli.__file__).resolve().parent
EVIL = "X\x1b[2J\nFORGED"
EVIL_YAML = "X\\e[2J\\nFORGED"  # YAML double-quoted escapes for the same text

# --- helpers --------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)


def _repo(tmp_path: Path, config: str) -> Path:
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@t.com")
    _git(tmp_path, "config", "user.name", "T")
    (tmp_path / ".kanban").mkdir()
    (tmp_path / ".kanban" / "config.yaml").write_text(config)
    item = tmp_path / "work" / "FEAT-001.md"
    item.parent.mkdir(parents=True)
    item.write_text(
        '---\nid: FEAT-001\ntitle: "t"\ntype: feature\nstatus: backlog\n'
        "priority: medium\ncreated: 2026-09-25\n---\n\n# FEAT-001: t\n"
    )
    return tmp_path


def _gate_config(message: str) -> str:
    return (
        "kanban:\n  theme: software\n  paths:\n    root: work/\n  gates:\n"
        '    "* -> ready":\n      - id: g\n        check: item.assignee\n'
        f'        message: "{message}"\n'
    )


PLAIN = "kanban:\n  theme: software\n  paths:\n    root: work/\n"


@pytest.fixture(autouse=True)
def _clean() -> None:
    config_mod._theme_cache.clear()


def _run(repo: Path, args: list[str], monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.chdir(repo)
    result = CliRunner().invoke(main, args)
    assert result.exception is None or isinstance(result.exception, SystemExit), result.output
    return result.output


def _run_tty(repo: Path, args: list[str], monkeypatch: pytest.MonkeyPatch) -> str:
    """Run with every module console forced to a terminal, capturing what it writes."""
    buf = io.StringIO()
    tty = Console(file=buf, force_terminal=True, width=200)
    monkeypatch.setattr(cli, "console", tty)
    monkeypatch.setattr(hdd_commands, "console", tty)
    _run(repo, args, monkeypatch)
    return buf.getvalue()


def _assert_escaped(out: str) -> None:
    plain = re.sub(r"\x1b\[[0-9;]*m", "", out)  # Rich's own colour codes
    assert "\x1b" not in plain, f"raw ESC reached the terminal: {out!r}"
    assert not any(line.startswith("FORGED") for line in plain.splitlines()), repr(out)
    assert "\\x1b[2J\\nFORGED" in plain, repr(out)


# --- 1. repo-file text: a gate message from .kanban/config.yaml -----------------------


def test_gate_message_from_config_is_escaped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, _gate_config(EVIL_YAML))
    _assert_escaped(_run(repo, ["move", "FEAT-001", "ready", "--no-commit"], monkeypatch))


def test_gate_message_from_config_is_escaped_on_a_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, _gate_config(EVIL_YAML))
    args = ["move", "FEAT-001", "ready", "--no-commit"]
    _assert_escaped(_run_tty(repo, args, monkeypatch))


# --- 2. an ID from argv ---------------------------------------------------------------


@pytest.mark.parametrize(
    "args",
    [
        ["move", EVIL, "in_progress", "--no-commit"],
        ["rank", EVIL, "1", "--no-commit"],
        ["show", EVIL],
    ],
    ids=["move", "rank", "show"],
)
def test_argv_id_in_error_is_escaped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, args: list[str]
) -> None:
    repo = _repo(tmp_path, PLAIN)
    _assert_escaped(_run(repo, args, monkeypatch))


def test_argv_id_in_error_is_escaped_on_a_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, PLAIN)
    _assert_escaped(_run_tty(repo, ["move", EVIL, "in_progress", "--no-commit"], monkeypatch))


# --- 3. hdd_commands: the parent-update warning ---------------------------------------


def test_hdd_parent_update_warning_is_escaped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, PLAIN)

    def boom(self: KanbanService, *args: object, **kwargs: object) -> bool:
        raise RuntimeError(f"bad turtle in parent: {EVIL}")  # text read from the parent

    monkeypatch.setattr(KanbanService, "update_parent_turtle_block", boom)
    args = ["literature", "create", "Survey", "--idea", "IDEA-R-001"]
    out = _run_tty(repo, args, monkeypatch)
    assert "could not update" in out, repr(out)
    _assert_escaped(out)


# --- controls -------------------------------------------------------------------------


def test_printable_error_text_is_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, PLAIN)
    out = _run(repo, ["move", "NOPE-1", "in_progress", "--no-commit"], monkeypatch)
    assert "Error: Item not found: NOPE-1\n" in out


def test_markup_is_still_escaped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo(tmp_path, _gate_config("need [bold]owner[/bold]"))
    out = _run(repo, ["move", "FEAT-001", "ready", "--no-commit"], monkeypatch)
    assert "Gate check failed: need [bold]owner[/bold]" in out


def test_config_value_error_carries_no_raw_esc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path, f'kanban:\n  paths:\n    ignore: {{"{EVIL_YAML}": 1}}\n')
    out = _run_tty(repo, ["list"], monkeypatch)
    assert "ignore:" in out, repr(out)
    _assert_escaped(out)


# --- static ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["cli.py", "hdd_commands.py"])
def test_no_bare_escape_of_exception_text(name: str) -> None:
    text = (SRC / name).read_text()
    hits = [n for n, line in enumerate(text.splitlines(), 1) if "escape(str(e))" in line]
    assert not hits, f"{name}: bare escape(str(e)) at lines {hits}"
