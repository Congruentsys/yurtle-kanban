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
        if arg.startswith("--") and "=" in arg:
            # `--name=value`: click fails on an option cmd doesn't know, and on a
            # flag (or `--help`) given a value (#1036); a single-dash token with `=`
            # is a short cluster, walked below (`-vn=3` is `-v`, `-n` taking `=3`)
            name = arg.split("=", 1)[0]
            if name == "--json":  # `--json=1`: JSON was asked for, however malformed
                return True
            takes = [
                not (p.is_flag or p.count)
                for p in cmd.params
                if isinstance(p, click.Option) and name in p.opts + p.secondary_opts
            ]
            if not any(takes):
                return "--json" in args[i + 1 :]
        elif arg.startswith("-") and arg != "-":
            nxt = args[i + 1] if i + 1 < len(args) else None
            step = _value_follows(cmd, arg, nxt)
            if step is None:  # click fails here: a later `--json` was still asked for
                return "--json" in args[i + 1 :]
            i += 1 if step else 0  # its value is never an option
        elif isinstance(cmd, click.Group) and arg in cmd.commands:
            cmd = cmd.commands[arg]
        i += 1
    return False


def _help_names(cmd: click.Command) -> list[str]:
    """`--help` and the like: options click adds that are not in `cmd.params`, as
    the command's own `context_settings` name them (#1036)."""
    names = (cmd.context_settings or {}).get("help_option_names")
    return list(names) if names is not None else list(click.Context(cmd).help_option_names)


def _value_follows(cmd: click.Command, arg: str, nxt: str | None = None) -> bool | None:
    """Whether option token `arg` of `cmd` takes the NEXT argument (`nxt`) as its
    value, read as click reads it (#971, #1021):
    - an option that takes a value takes the next argument;
    - an optional-value option (`is_flag=False` with a `flag_value`) takes it only
      when it doesn't look like an option, else it gets its `flag_value`;
    - a short cluster (`-vn`) is flags up to the first letter that takes a value,
      which takes the rest of the token, or the next argument when none is left.
    None when click fails on `arg`: an unknown option, or an unknown letter in a
    cluster before any value-taking one (#1021)."""
    def kind(name: str) -> str | None:
        for p in cmd.params:
            # `_flag_needs_value` is click's own (private) optional-value marker
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
        return None if kind(arg) is None and arg not in _help_names(cmd) else takes_next(arg)
    for n, letter in enumerate(arg[1:], 1):
        k = kind(f"-{letter}")
        if k is None:
            return None  # click fails here, before any later letter
        if k in ("value", "optional"):
            return n == len(arg) - 1 and takes_next(f"-{letter}")
    return False


class HiddenArgument(click.Argument):
    """A positional argument left out of the usage line, as `hidden=True` leaves
    an option out of `--help`: for a deprecated 2.x positional (#1230)."""

    def get_usage_pieces(self, ctx: click.Context) -> list[str]:
        return []


def deprecated(
    command: str,
    new: str,
    value: Any,
    old: dict[str, Any],
    *,
    given: str | None = None,
) -> Any:
    """The value of a 3.x form that 2.x forms #580 removed still map to (#1230).

    `old` maps each 2.x spelling (`-a`, `--assignee`, `ID TEXT`) to the value it
    was given (None when absent); `value` is the 3.x form `new`'s own, and `given`
    names the 3.x spelling actually used, when it isn't `new` (`--body-file`).
    No old form: `value`, unchanged. One old form alone: its value, with ONE line
    on stderr, `yurtle-kanban: <command> <old> is deprecated, use <command> <new>
    (removed in 4.0)`; stdout (and so `--json`) is untouched. An old form with
    the new one, or two old spellings, is a usage error: never a silent pick."""
    used = [name for name, v in old.items() if v is not None]
    if not used:
        return value
    if value is not None:
        used.insert(0, given or new)
    if len(used) > 1:
        both = " and ".join(f"{command} {name}" for name in used)
        raise click.UsageError(
            f"{both} both given: use {command} {new} only",
            ctx=click.get_current_context(silent=True),
        )
    click.echo(
        f"yurtle-kanban: {command} {used[0]} is deprecated, "
        f"use {command} {new} (removed in 4.0)",
        err=True,
    )
    return old[used[0]]


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
            raise click.ClickException(escape_nonprintable(str(e))) from None
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


def refuse(
    e: object, plain: str | None = None, console: Any = None, *, exit_code: int = 1
) -> NoReturn:
    """A refusal, exit 1 (or `exit_code`: 8 for a halted board, #582), shared by
    every command (#877, #962). With `--json`: one JSON object on stdout,
    `{"success": false, "error": <e>}`. Without: `plain` (a Rich markup line)
    when given, else `e` as one red `Error:` line (#580), on stderr (#1080), as
    click's own errors and the `Group` handler's are. Colour and terminal-ness
    are stderr's own; only what the caller's module `console` set explicitly
    (tests swap one forced to a terminal) is carried over, never stdout's
    detected state (`2>err.log` in a terminal gets no escapes)."""
    if json_requested():
        json_refusal(e, exit_code=exit_code)
    from rich.console import Console

    if console is None:
        err = Console(stderr=True)
    else:
        err = Console(
            stderr=True,
            force_terminal=console._force_terminal,
            width=console._width,
        )
    err.print(plain if plain is not None else f"[red]Error: {safe(e)}[/red]", soft_wrap=True)
    sys.exit(exit_code)


def pull_note(result: dict[str, Any]) -> str:
    """The one line every `--push` create prints when the item landed on the
    remote's default branch but not in this checkout (a feature branch, detached
    HEAD, diverged main): where it is, and to pull; first, when a parent's
    uncommitted edit would block that pull, to commit or stash it (#585, #625, #674)."""
    from .service import pull_note_text

    note = pull_note_text(
        result.get("branch") or "main", result.get("dirty_parent"), result.get("ff_why")
    )
    return f"[yellow]  {safe(note)}[/yellow]"
