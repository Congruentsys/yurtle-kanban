"""Click group that shows refused input as a one-line error (#239)."""

from __future__ import annotations

from typing import Any

import click

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
