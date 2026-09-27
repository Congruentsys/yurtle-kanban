"""Click group that shows refused input as a one-line error (#239)."""

from __future__ import annotations

from typing import Any

import click
from rich.markup import escape

from ._logging import escape_nonprintable
from .models import InvalidText


class Group(click.Group):
    """A `click.Group` whose commands turn `InvalidText` (text that can't be
    written as UTF-8) into a clean `Error:` line. Any other exception keeps its
    traceback, so a real bug still surfaces (#183)."""

    def invoke(self, ctx: click.Context) -> Any:
        try:
            return super().invoke(ctx)
        except InvalidText as e:
            raise click.ClickException(str(e)) from None


def safe(value: object) -> str:
    """Text for a Rich markup string in an error or warning line: markup escaped,
    and control characters (ESC, newlines) shown as `\\x1b` / `\\n`, so text from a
    repo file or argv can't clear the screen or forge output lines (#251)."""
    return escape(escape_nonprintable(str(value)))


def pull_note(result: dict[str, Any]) -> str:
    """The one line every `--push` create prints when the item landed on the
    remote's default branch but not in this checkout (a feature branch, detached
    HEAD, diverged main): where it is, and to pull (#585, #625)."""
    branch = result.get("branch") or "main"
    return (
        f"[yellow]  Pushed to origin/{safe(branch)}; not in this checkout yet: "
        f"pull {safe(branch)} to see it[/yellow]"
    )
