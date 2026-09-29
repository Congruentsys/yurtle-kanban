"""Click group that shows refused input as a one-line error (#239)."""

from __future__ import annotations

import json
import sys
from typing import Any, NoReturn

import click
from rich.markup import escape

from ._logging import escape_nonprintable
from .models import InputRefused


def json_requested(ctx: click.Context | None = None) -> bool:
    """True when the running command (or a parent) was given `--json` (#877)."""
    ctx = ctx or click.get_current_context(silent=True)
    while ctx is not None:
        if ctx.params.get("as_json"):
            return True
        ctx = ctx.parent
    return False


def argv_requests_json(args: list[str], root: click.Command | None = None) -> bool:
    """True when `--json` is among the options of `args` (before any `--`): for a
    refusal before the command has parsed its own options (#877). Given the
    `root` command, `args` is walked as click reads it, so a `--json` that is the
    value of the option before it (`--assignee --json`) is no request (#929)."""
    args = args[: args.index("--")] if "--" in args else args
    if root is None:
        return "--json" in args
    cmd, i = root, 0
    while i < len(args):
        arg = args[i]
        if arg == "--json":
            return True
        if arg.startswith("-") and "=" not in arg:
            nxt = args[i + 1] if i + 1 < len(args) else None
            i += 1 if _value_follows(cmd, arg, nxt) else 0  # its value is never an option
        elif isinstance(cmd, click.Group) and arg in cmd.commands:
            cmd = cmd.commands[arg]
        i += 1
    return False


def _value_follows(cmd: click.Command, arg: str, nxt: str | None = None) -> bool:
    """Whether option token `arg` of `cmd` takes the NEXT argument (`nxt`) as its
    value, read as click reads it (#971, #1021):
    - an option that takes a value takes the next argument;
    - an optional-value option (`is_flag=False` with a `flag_value`) takes it only
      when it doesn't look like an option, else it gets its `flag_value`;
    - a short cluster (`-vn`) is flags up to the first letter that takes a value,
      which takes the rest of the token, or the next argument when none is left;
      an unknown letter stops the walk there, as click fails on it."""
    def kind(name: str) -> str | None:
        for p in cmd.params:
            if isinstance(p, click.Option) and name in p.opts + p.secondary_opts:
                if p.is_flag or p.count:
                    return "flag"
                return "optional" if getattr(p, "_flag_needs_value", False) else "value"
        return None

    def takes_next(name: str) -> bool:
        k = kind(name)
        if k == "optional":
            return nxt is not None and not nxt.startswith("-")
        return k == "value"

    if arg.startswith("--") or len(arg) <= 2:
        return takes_next(arg)
    for n, letter in enumerate(arg[1:], 1):
        k = kind(f"-{letter}")
        if k is None:
            return False  # click fails here, before any later letter
        if k in ("value", "optional"):
            return n == len(arg) - 1 and takes_next(f"-{letter}")
    return False


def json_refusal(message: object, *, exit_code: int = 1, **extra: Any) -> NoReturn:
    """A `--json` refusal: exactly one JSON object on stdout,
    `{"success": false, "error": <message>}` plus `extra`, and exit 1 (#877), or
    `exit_code`: 2 for a usage error, as click exits (#929)."""
    click.echo(json.dumps({"success": False, "error": str(message), **extra}))
    sys.exit(exit_code)


class Command(click.Command):
    """A command whose `InputRefused`, under `--json`, is a JSON refusal on stdout
    (#877); without `--json` it reaches the group's `Error:` line unchanged."""

    def invoke(self, ctx: click.Context) -> Any:
        try:
            return super().invoke(ctx)
        except InputRefused as e:
            if not json_requested(ctx):
                raise
            json_refusal(e)


class Group(click.Group):
    """A `click.Group` whose commands turn a refused input (`InputRefused`: text
    that can't be written as UTF-8, a forged `## Comments` line, ...) into a clean
    `Error:` line (#239, #666), or under `--json` one JSON object on stdout (#877,
    via `Command`). Any other exception keeps its traceback, so a real bug still
    surfaces (#183)."""

    command_class = Command

    def invoke(self, ctx: click.Context) -> Any:
        try:
            return super().invoke(ctx)
        except InputRefused as e:
            raise click.ClickException(str(e)) from None
        except click.UsageError as e:
            # a bad option, value or subcommand under --json: click's message as
            # the JSON refusal, still exit 2 (#929); the argv the root recorded
            root = ctx.find_root()
            argv = root.meta.get("yurtle_kanban.argv")
            if argv is None or not argv_requests_json(argv, root.command):
                raise
            json_refusal(e.format_message(), exit_code=e.exit_code)


def safe(value: object) -> str:
    """Text for a Rich markup string in an error or warning line: markup escaped,
    and control characters (ESC, newlines) shown as `\\x1b` / `\\n`, so text from a
    repo file or argv can't clear the screen or forge output lines (#251)."""
    return escape(escape_nonprintable(str(value)))


def refuse(e: object, plain: str | None = None, console: Any = None) -> NoReturn:
    """A refusal, exit 1, shared by every command (#877, #962). With `--json`: one
    JSON object on stdout, `{"success": false, "error": <e>}`. Without: `plain` (a
    Rich markup line) when given, else `e` as one red `Error:` line (#580), on the
    caller's module `console` when given (tests swap it for a terminal)."""
    if json_requested():
        json_refusal(e)
    if console is None:
        from rich.console import Console

        console = Console()
    console.print(plain if plain is not None else f"[red]Error: {safe(e)}[/red]", soft_wrap=True)
    sys.exit(1)


def pull_note(result: dict[str, Any]) -> str:
    """The one line every `--push` create prints when the item landed on the
    remote's default branch but not in this checkout (a feature branch, detached
    HEAD, diverged main): where it is, and to pull; first, when a parent's
    uncommitted edit would block that pull, to commit or stash it (#585, #625, #674)."""
    from .service import pull_note_text

    note = pull_note_text(result.get("branch") or "main", result.get("dirty_parent"))
    return f"[yellow]  {safe(note)}[/yellow]"
