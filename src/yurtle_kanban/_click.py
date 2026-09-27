"""Click group that shows refused input as a one-line error (#239)."""

from __future__ import annotations

from typing import Any

import click
from rich.markup import escape

from ._logging import escape_nonprintable
from .models import InputRefused


class Group(click.Group):
    """A `click.Group` whose commands turn a refused input (`InputRefused`: text
    that can't be written as UTF-8, a forged `## Comments` line, ...) into a clean
    `Error:` line (#239, #666). Any other exception keeps its traceback, so a real
    bug still surfaces (#183)."""

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
