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


def argv_requests_json(args: list[str]) -> bool:
    """True when `--json` is among the options of `args` (before any `--`): for a
    refusal before the command has parsed its own options (#877)."""
    return "--json" in (args[: args.index("--")] if "--" in args else args)


def json_refusal(message: object, **extra: Any) -> NoReturn:
    """A `--json` refusal: exactly one JSON object on stdout,
    `{"success": false, "error": <message>}` plus `extra`, and exit 1 (#877)."""
    click.echo(json.dumps({"success": False, "error": str(message), **extra}))
    sys.exit(1)


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


def safe(value: object) -> str:
    """Text for a Rich markup string in an error or warning line: markup escaped,
    and control characters (ESC, newlines) shown as `\\x1b` / `\\n`, so text from a
    repo file or argv can't clear the screen or forge output lines (#251)."""
    return escape(escape_nonprintable(str(value)))


def pull_note(result: dict[str, Any]) -> str:
    """The one line every `--push` create prints when the item landed on the
    remote's default branch but not in this checkout (a feature branch, detached
    HEAD, diverged main): where it is, and to pull; first, when a parent's
    uncommitted edit would block that pull, to commit or stash it (#585, #625, #674)."""
    from .service import pull_note_text

    note = pull_note_text(result.get("branch") or "main", result.get("dirty_parent"))
    return f"[yellow]  {safe(note)}[/yellow]"
