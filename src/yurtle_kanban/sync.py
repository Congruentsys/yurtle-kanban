"""The one kanban push: a worktree-free compare-and-swap onto origin's default
branch (#574, spec §2, design A).

`KanbanService.sync_and_push(mutate)` fetches `origin/<default>`, asks `mutate`
what to change given that base, builds the commit in a temporary index and pushes
it; a lost race refetches and asks again. The user's worktree, index and branch are
never touched. These are the values `mutate` returns and the `Outcome` it gets back.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Protocol


class ExitCode(IntEnum):
    """The process exit code of each outcome (#574 B4): distinct, so a script can
    tell losing a race from an unreachable remote."""

    OK = 0
    REFUSED = 1
    LOST = 3
    UNREACHABLE = 4
    BUSY = 5
    PUSH_REFUSED = 6


# `next --json` and `claim --next` with nothing to offer (#575): an exit code, not an
# outcome of `sync_and_push`, so it stays out of `ExitCode`
NOTHING_PICKABLE = 7


_EXIT_CODES: dict[str, ExitCode] = {
    "won": ExitCode.OK,
    "local": ExitCode.OK,
    "noop": ExitCode.OK,
    "refused": ExitCode.REFUSED,
    "lost": ExitCode.LOST,
    "unreachable": ExitCode.UNREACHABLE,
    "busy": ExitCode.BUSY,
    "push_refused": ExitCode.PUSH_REFUSED,
}


@dataclass
class Outcome:
    """How a `sync_and_push` ended (#574). `kind` is one of won (pushed), local (no
    remote: committed here only), noop, refused, lost (to `holder`, after a push
    was rejected), unreachable, busy (rejected every attempt) or push_refused (the
    remote said no for another reason, not retried). `sha` is the commit made,
    `attempts` how many were used, `data` the winning Change's `data`."""

    kind: str
    message: str
    sha: str | None = None
    attempts: int = 0
    data: Any = None

    @property
    def exit_code(self) -> ExitCode:
        return _EXIT_CODES[self.kind]


@dataclass
class Change:
    """Write `files` (path relative to the git work tree -> LF text; line endings
    of a file `read` returned are kept) in one commit with `message`."""

    files: dict[str, str]
    message: str
    data: Any = None


@dataclass
class NoOp:
    """Nothing to do: the base already says so. Exit 0, nothing pushed."""

    message: str


@dataclass
class Refuse:
    """Don't change anything. With a `holder`, after a lost race, it is "lost to
    `holder`" (exit 3); otherwise "refused" (exit 1)."""

    message: str
    holder: str | None = None


class Read(Protocol):
    """What `mutate` reads through: `read(path)` is the file's LF text in the tree
    being changed (None when absent). `rev` is that tree's commit, the fetched
    `origin/<default>`, or None for the working tree (no remote), so a mutate can
    look past the paths it names, e.g. count WIP there (#574)."""

    rev: str | None

    def __call__(self, rel: str) -> str | None: ...


Mutate = Callable[[Read, int], "Change | NoOp | Refuse"]

__all__ = [
    "NOTHING_PICKABLE", "Change", "ExitCode", "Mutate", "NoOp", "Outcome", "Read", "Refuse",
]
