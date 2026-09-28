"""Shims that make one `KanbanService._git_run` call fail, chosen by its caller (#892).

The caller is the frame that called `_git_run`, skipping past the `-z` reader
`_git_z` (#859), which only wraps `_git_run` for NUL-separated output.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Any

import pytest

from yurtle_kanban.service import KanbanService


def break_git_in(
    monkeypatch: pytest.MonkeyPatch, caller: str, subcommand: str, stderr: str
) -> list[tuple[str, ...]]:
    """`git <subcommand>` run through `_git_run` by the method `caller` exits 128 with
    `stderr`; every other git call runs for real. Returns the args of each failed call."""
    seen: list[tuple[str, ...]] = []
    real_git_run = KanbanService._git_run

    def git_run(self: KanbanService, *args: str, **kwargs: Any) -> Any:
        frame = sys._getframe(1)
        if frame.f_code.co_name == "_git_z" and frame.f_back is not None:
            frame = frame.f_back
        if args[:1] == (subcommand,) and frame.f_code.co_name == caller:
            seen.append(args)
            return subprocess.CompletedProcess(["git", *args], 128, "", stderr)
        return real_git_run(self, *args, **kwargs)

    monkeypatch.setattr(KanbanService, "_git_run", git_run)
    return seen
