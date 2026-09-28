"""
Kanban Service - Core operations for managing work items.

This service provides the business logic for:
- Loading/saving work items from Yurtle files
- State transitions with validation
- WIP limit enforcement
- Item creation and updates
"""

from __future__ import annotations

import copy
import dataclasses
import fnmatch
import json
import logging
import os
import random
import re
import subprocess
import time
import unicodedata
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

import yaml
from rdflib import RDF, RDFS, Graph, Literal, Namespace, URIRef

from ._graph_iri import set_self_iri
from ._logging import get_logger
from .config import KanbanConfig, _fold_status_name, _status_names, _under
from .hooks import HookContext, HookEngine, HookEvent
from .inputs import GIT_ENV, check_identity, resolve_actor, same_actor
from .sync import Change, Mutate, NoOp, Outcome, Read, Refuse

if TYPE_CHECKING:
    from .config import BoardConfig
    from .gates import GateResult
from .models import (
    ID_PREFIX_FORM,
    PRIORITIES,
    Board,
    Column,
    Comment,
    InputRefused,
    MissingFile,
    WorkItem,
    WorkItemStatus,
    WorkItemType,
    check_encodable,
    fold_id,
    id_prefix,
    turtle_string,
    turtle_unescape,
    unknown_priority_message,
    yaml_flow_list,
    yaml_quote,
    yaml_scalar,
)
from .turtle_builder import PREFIXES
from .workflow import DEFAULT_TRANSITIONS, WorkflowConfig, WorkflowParser

logger = get_logger("yurtle-kanban")  # escapes control characters (#215)


class GitCommitError(InputRefused):
    """Git refused a kanban commit (#584), e.g. a pre-commit hook said no. The
    message carries git's (or the hook's) own output. A refusal, never a bug
    (#786): every CLI command's error path and MCP report it without a traceback."""


@dataclass
class _Reader:
    """A `sync.Read`: `read` over the tree at commit `rev` (None: the working tree)."""

    rev: str | None
    read: Callable[[str], str | None]

    def __call__(self, rel: str) -> str | None:
        return self.read(rel)


@dataclass
class _Claimed:
    """What a claim changed, for the hooks it fires once it has landed (#574)."""

    item_id: str
    item_type: str
    title: str
    old_status: str
    new_status: str
    assignee: str
    old_assignee: str = ""


@dataclass
class DepNode:
    """One unmet dependency, as `unmet_dependencies` walks it (#575): `status` is its
    native status (None when it is on no board), `state` its `dependency_state`
    and `children` its own unmet dependencies. #577 renders it."""

    id: str
    status: str | None
    assignee: str | None
    state: str
    children: list[DepNode] = dataclasses.field(default_factory=list)


@dataclass(frozen=True)
class _Edits:
    """An `update`'s field-level edits (#576), as `update_item_changes` and
    `update_item_push` take them (#574 §5)."""

    title: str | None = None
    priority: str | None = None
    assignee: str | None = None
    description: str | None = None
    tags: list[str] | None = None
    add_tags: list[str] | None = None
    remove_tags: list[str] | None = None
    depends_on: list[str] | None = None
    add_depends_on: list[str] | None = None
    remove_depends_on: list[str] | None = None
    related: list[str] | None = None
    allow_unknown: bool = False

    @property
    def editing_deps(self) -> bool:
        return (
            self.depends_on is not None
            or bool(self.add_depends_on)
            or bool(self.remove_depends_on)
        )


class _CasRefusedError(Exception):
    """A compare-and-swap commit (#585, #590) that can't be built; its message is
    the failure the caller reports."""


def _parse_allocations(text: str | None, where: str) -> list[Any]:
    """The records of an `_ID_ALLOCATIONS.json` read from `where`: `text` None (no
    file) starts a fresh list; a file that exists but is not a JSON list is refused,
    never replaced — rewriting it would drop every earlier allocation (#818)."""
    if text is None:
        return []
    try:
        records = json.loads(text)
    except ValueError:
        records = None
    if not isinstance(records, list):
        raise InputRefused(
            f"{where} is not a valid JSON list of allocations: fix it or remove it "
            "(a missing file starts a fresh list); nothing was changed"
        )
    return records


def _twin_key(name: str) -> str:
    """A path as a case- and normalization-insensitive filesystem (APFS) compares
    it (#869): NFC, case folded, NFC again. `Café` NFC and NFD are one name, and
    so are `Straße` and `STRASSE` (casefold's `ß` -> `ss`, as APFS folds it)."""
    return unicodedata.normalize("NFC", unicodedata.normalize("NFC", name).casefold())


class _TreeUnreadableError(ValueError):
    """A commit's board files can't all be read (#814): `git ls-tree` or `git
    cat-file` failed, or a board file couldn't be read as a blob (#832). A count
    taken from such a read would be short, so the caller refuses instead; its
    message names the cause. A ValueError, so `update --push`'s refusal path
    reports it as it reports a bad dependency."""


# HDD namespace objects (derived from turtle_builder.PREFIXES, single source of truth)
_HYP = Namespace(PREFIXES["hyp"])
_PAPER_NS = Namespace(PREFIXES["paper"])
_EXPR = Namespace(PREFIXES["expr"])
_MEASURE = Namespace(PREFIXES["measure"])
_IDEA = Namespace(PREFIXES["idea"])
_LIT = Namespace(PREFIXES["lit"])

# HDD type aliases for backfill (normalize variant names to canonical types)
_TYPE_ALIASES: dict[str, str] = {"secondary-hypothesis": "hypothesis"}

# HDD types eligible for turtle block backfill
_BACKFILL_TYPES = frozenset({"idea", "literature", "paper", "hypothesis", "experiment", "measure"})

# RDF class URIs for each HDD type
_HDD_TYPE_CLASSES: dict[str, URIRef] = {
    "idea": _IDEA.Idea,
    "literature": _LIT.Literature,
    "paper": _PAPER_NS.Paper,
    "hypothesis": _HYP.Hypothesis,
    "experiment": _EXPR.Experiment,
    "measure": _MEASURE.Measure,
}

_PAPER_PREFIX_RE = re.compile(r"^[Pp]aper", re.IGNORECASE)


def _normalize_paper_num(raw: str | int) -> str:
    """Extract the numeric part from a paper field value.

    Handles: 130, "130", "Paper130", "paper130", "PAPER130".
    """
    return _PAPER_PREFIX_RE.sub("", str(raw))


# First meaningful line of a Turtle (not YAML) frontmatter block: `@prefix`,
# `@base`, or SPARQL-style `PREFIX` / `BASE`, after optional blank/`#` lines
_TURTLE_FRONTMATTER = re.compile(
    r"\A(?:[ \t]*(?:#[^\n]*)?\n)*[ \t]*"
    # SPARQL-style forms must look like Turtle (`PREFIX ex: <…>`, `BASE <…>`), so a
    # YAML key such as `base : x` or `Base url: x` isn't taken for Turtle (#158)
    r"(?:@prefix\b|@base\b|(?i:prefix)[ \t]+[\w.-]*:[ \t]*<|(?i:base)[ \t]+<)"
)

# Turtle literal escaping lives in models (one escaper for every literal, #141)
_turtle_string = turtle_string
_turtle_unescape = turtle_unescape


def git_toplevel(cwd: Path) -> Path | None:
    """`git rev-parse --show-toplevel` from `cwd`, or None when git can't say
    (not a repo, or no git on PATH). One helper for the service and `init` (#198)."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
            stdin=subprocess.DEVNULL,
            env={**os.environ, **GIT_ENV},
        ).stdout.strip()
    except (subprocess.CalledProcessError, OSError):
        return None
    return Path(out) if out else None


def _scalar_text(value: Any) -> Any:
    """A number or boolean read from YAML as text; None, strings and lists as
    they are (#206)."""
    if isinstance(value, bool):  # YAML's spelling, not Python's `True` (#225)
        return "true" if value else "false"
    return str(value) if isinstance(value, (int, float)) else value


def _list_text(value: Any) -> list[Any] | dict[Any, Any]:
    """A frontmatter list field as text entries (#653): a comma-separated string
    is split, a single scalar is one entry, each entry is read as text
    (`2026` -> '2026', `yes` -> 'true' as #225 reads it, `2026-01-01` as
    written, a nested list or mapping as its YAML flow text), and null entries
    are dropped. A mapping-valued field stays a mapping."""
    if value is None:
        return []
    if isinstance(value, str):
        return [v.strip() for v in value.split(",")]
    if isinstance(value, dict):
        return value  # not a scalar: as it is (#675)
    if not isinstance(value, list):
        value = [value]  # `tags: 2026` is one entry (#653)
    return [_entry_text(v) for v in value if v is not None]


def _entry_text(value: Any) -> Any:
    """One list entry as text: `_scalar_text`; a YAML date as written
    (`2026-01-01`) and a timestamp in ISO form (`Z` becomes `+00:00`); a nested
    list or mapping as its YAML flow text (`[b, c]`, `{k: v}`, #675)."""
    if isinstance(value, (date, datetime)):
        return value.isoformat() if isinstance(value, datetime) else str(value)
    if isinstance(value, (list, dict)):
        return yaml.safe_dump(
            value, default_flow_style=True, sort_keys=False, allow_unicode=True, width=10**9
        ).strip()
    return _scalar_text(value)


_MAX_FIELD_NODES = 10_000  # values in one frontmatter field, aliases expanded (#277)


def _expanded_size(value: Any, limit: int) -> int:
    """How many values `value` holds with every alias expanded, counting a shared
    node once per use; stops counting past `limit`. Memoised per node, so a
    billion-laughs DAG costs its unique nodes, not its expansion. Call it only on
    non-cyclic values (#277)."""
    sizes: dict[int, int] = {}
    stack: list[tuple[Any, bool]] = [(value, False)]
    while stack:
        node, expanded = stack.pop()
        if not isinstance(node, (list, tuple, dict)) or id(node) in sizes:
            continue
        children = [*node.keys(), *node.values()] if isinstance(node, dict) else list(node)
        if not expanded:
            stack.append((node, True))
            stack.extend((c, False) for c in children)
            continue
        total = 1
        for child in children:
            total += sizes.get(id(child), 1)
            if total > limit:
                break
        sizes[id(node)] = min(total, limit + 1)
    return sizes.get(id(value), 1)


def _container_ids(values: Any) -> set[int]:
    """ids of every list/dict reachable from `values`, each node visited once, so a
    shared-alias DAG costs its unique nodes (#296). Call on non-cyclic values."""
    seen: set[int] = set()
    todo = [v for v in values if isinstance(v, (list, tuple, dict))]
    while todo:
        node = todo.pop()
        if id(node) in seen:
            continue
        seen.add(id(node))
        children = [*node.keys(), *node.values()] if isinstance(node, dict) else node
        todo.extend(c for c in children if isinstance(c, (list, tuple, dict)))
    return seen


def _is_cyclic(value: Any) -> bool:
    """True when a YAML value contains itself: an anchor used inside its own
    node. A node shared by several fields, or used twice, is not a cycle (#262).
    Iterative, so deep but finite nesting can't hit the recursion limit."""
    on_path: set[int] = set()
    done: set[int] = set()  # each shared node explored once (no billion-laughs blowup)
    # (node, children iterator); a node leaves the path when its children run out
    stack: list[tuple[Any, Any]] = []

    def enter(node: Any) -> bool:
        """Push a container; True if it closes a cycle."""
        if not isinstance(node, (list, tuple, dict)) or id(node) in done:
            return False
        if id(node) in on_path:
            return True
        on_path.add(id(node))
        children = [*node.keys(), *node.values()] if isinstance(node, dict) else node
        stack.append((node, iter(children)))
        return False

    if enter(value):
        return True
    while stack:
        node, children = stack[-1]
        child = next(children, _DONE)
        if child is _DONE:
            stack.pop()
            on_path.discard(id(node))
            done.add(id(node))
        elif enter(child):
            return True
    return False


_DONE = object()  # sentinel: a node's children are exhausted


class LineEndings:
    """A text file's original line endings, so an edit can keep them (#128, #151).

    Lines an edit leaves unchanged keep their exact original ending; changed or
    added lines take the file's majority ending (`\n` on a tie), so a mixed file
    is not rewritten wholesale to one ending.
    """

    def __init__(self, lines: list[str], endings: list[str]) -> None:
        self.lines = lines  # LF-normalised line contents, without their endings
        self.endings = endings  # the original ending of each line ("" for none)
        crlf = sum(1 for e in endings if e == "\r\n")
        lf = sum(1 for e in endings if e == "\n")
        self.majority = "\r\n" if crlf > lf else "\n"

    @classmethod
    def read(cls, raw: str) -> tuple[str, LineEndings]:
        parts = re.split(r"(\r\n|\r|\n)", raw)
        lines, endings = parts[0::2], parts[1::2] + [""]
        return "\n".join(lines), cls(lines, endings)

    def apply(self, text: str) -> str:
        """Re-end LF `text`: untouched lines keep their ending, others the majority."""
        used = {e for e in self.endings if e}
        if len(used) <= 1:
            # One ending throughout (every real file): no per-line mapping needed,
            # and difflib is quadratic on long runs of identical lines
            eol = used.pop() if used else self.majority
            return text.replace("\n", eol) if eol != "\n" else text
        new_lines = text.split("\n")
        new_endings = [self.majority] * (len(new_lines) - 1) + [""]

        def keep(i: int, j: int) -> None:  # new line j is old line i, unchanged
            if j < len(new_lines) - 1 and self.endings[i]:
                new_endings[j] = self.endings[i]

        self._match(0, len(self.lines), new_lines, 0, len(new_lines), keep)

        # A kept lone `\r` right before an empty line ending `\n` would serialise
        # as one `\r\n` and lose that line on re-read (#202): end it `\r` too
        for j in range(len(new_lines) - 2):
            if new_endings[j] == "\r" and not new_lines[j + 1] and new_endings[j + 1] == "\n":
                new_endings[j + 1] = "\r"
        return "".join(line + end for line, end in zip(new_lines, new_endings))

    # Past this many line pairs, a segment is split on lines unique to both sides
    # (patience diff) instead of going to difflib, which is quadratic (#202); kept
    # small so many mid-sized segments stay cheap too (#213)
    _DIFFLIB_MAX_PAIRS = 50_000

    def _match(
        self,
        a_lo: int,
        a_hi: int,
        new: list[str],
        b_lo: int,
        b_hi: int,
        keep: Any,
    ) -> None:
        """Call keep(i, j) for each old line i matched to new line j; the pairs
        increase in both i and j.

        An edit touches a few lines: the unchanged prefix and suffix map line for
        line and only the changed middle is diffed (#181). A segment too large for
        difflib is split on anchors (lines that occur once on each side, longest
        increasing run), and so are the gaps between them, from a work stack, while
        a budget of anchor scanning linear in the input lasts (#213). Past that, or
        with no anchors (e.g. all identical lines), a segment maps position for
        position. No recursion, and the time stays linear (#202).
        """
        old = self.lines
        budget = 4 * ((a_hi - a_lo) + (b_hi - b_lo)) + 1000  # lines of anchor scanning
        work = [(a_lo, a_hi, b_lo, b_hi)]
        while work:
            a_lo, a_hi, b_lo, b_hi = work.pop()
            while a_lo < a_hi and b_lo < b_hi and old[a_lo] == new[b_lo]:
                keep(a_lo, b_lo)
                a_lo, b_lo = a_lo + 1, b_lo + 1
            while a_lo < a_hi and b_lo < b_hi and old[a_hi - 1] == new[b_hi - 1]:
                a_hi, b_hi = a_hi - 1, b_hi - 1
                keep(a_hi, b_hi)
            if a_lo == a_hi or b_lo == b_hi:
                continue
            if (a_hi - a_lo) * (b_hi - b_lo) <= self._DIFFLIB_MAX_PAIRS:
                import difflib

                matcher = difflib.SequenceMatcher(
                    a=old[a_lo:a_hi], b=new[b_lo:b_hi], autojunk=False
                )
                for tag, i1, i2, j1, _ in matcher.get_opcodes():
                    if tag == "equal":
                        for k in range(i2 - i1):
                            keep(a_lo + i1 + k, b_lo + j1 + k)
                continue
            cost = (a_hi - a_lo) + (b_hi - b_lo)
            anchors: list[tuple[int, int]] = []
            if budget >= cost:
                budget -= cost
                anchors = self._anchors(old, a_lo, a_hi, new, b_lo, b_hi)
            if not anchors:
                for k in range(min(a_hi - a_lo, b_hi - b_lo)):
                    if old[a_lo + k] == new[b_lo + k]:
                        keep(a_lo + k, b_lo + k)
                continue
            for i, j in anchors:
                work.append((a_lo, i, b_lo, j))
                keep(i, j)
                a_lo, b_lo = i + 1, j + 1
            work.append((a_lo, a_hi, b_lo, b_hi))

    @staticmethod
    def _anchors(
        old: list[str], a_lo: int, a_hi: int, new: list[str], b_lo: int, b_hi: int
    ) -> list[tuple[int, int]]:
        """(i, j) pairs of lines unique on both sides, longest run in order."""
        from bisect import bisect_left
        from collections import Counter

        count_a = Counter(old[a_lo:a_hi])
        count_b = Counter(new[b_lo:b_hi])
        where_b = {new[j]: j for j in range(b_lo, b_hi) if count_b[new[j]] == 1}
        pairs = [
            (i, where_b[old[i]])
            for i in range(a_lo, a_hi)
            if count_a[old[i]] == 1 and old[i] in where_b
        ]
        # longest increasing subsequence of j (patience sorting)
        tails: list[int] = []  # j at the end of the best run of each length
        tail_idx: list[int] = []  # index into pairs of that end
        back = [-1] * len(pairs)
        for n, (_, j) in enumerate(pairs):
            k = bisect_left(tails, j)
            back[n] = tail_idx[k - 1] if k else -1
            if k == len(tails):
                tails.append(j)
                tail_idx.append(n)
            else:
                tails[k], tail_idx[k] = j, n
        run: list[tuple[int, int]] = []
        n = tail_idx[-1] if tail_idx else -1
        while n != -1:
            run.append(pairs[n])
            n = back[n]
        return run[::-1]


def pull_note_text(branch: str, dirty: str | None = None) -> str:
    """Where a `--push` create landed when it isn't in this checkout (a feature
    branch, detached HEAD, diverged main), and to pull: the one wording the CLI
    line and the service message share (#625, #637). `dirty` names a file the
    pull would overwrite, whose uncommitted edit must be dealt with first (#674)."""
    note = f"Pushed to origin/{branch}; not in this checkout yet: pull {branch} to see it"
    if dirty is None:
        return note
    return f"{note}; commit or stash your edit to {dirty} before pulling"


def _created_and_pushed_message(
    item_id: str, branch: str, title: str, *, local: bool, dirty: str | None = None
) -> str:
    """The service result message for a `--push` create: where it landed, and the
    pull note when this checkout doesn't have it yet (#637), without doubling a
    title's own closing period (#660)."""
    message = f"Created and pushed {item_id} to origin/{branch}: {title}"
    if local:
        return message
    return f"{message.removesuffix('.')}. {pull_note_text(branch, dirty)}"


class KanbanService:
    """Service for managing kanban work items.

    A config loaded from a file by `KanbanConfig.load` keeps its own `repo_root`. One
    with no `repo_root` (built directly, or returned by `load` for a missing file) is
    bound to this service's repo: the service fills `config.repo_root` in place, so a
    bare `KanbanConfig()` reused for a second repo keeps resolving themes in the first
    (#313, #326).
    """

    def __init__(
        self,
        config: KanbanConfig,
        repo_root: Path | str,
        hooks_config: Path | str | None = None,
    ):
        self.config = config
        # absolute: git runs with cwd=repo_root, so a relative root would double
        # every path handed to `git add` (#198); `.absolute()` keeps symlinks as given
        self.repo_root = Path(repo_root).absolute()
        # status-name lookups memoised per board until the next scan (#448, #454)
        self._status_names_cache: dict[str | None, Any] = {}
        # board themes memoised per board within a scan scope (#665)
        self._board_theme_cache: dict[str, dict | None] = {}
        self._scanning = False
        if getattr(config, "repo_root", None) is None:
            # a config with no repo_root (built directly, or from load for a missing
            # file) resolves themes in this service's repo, not the cwd; one loaded from
            # a file keeps its own (#287, #300, #341)
            config.repo_root = self.repo_root
        self._items: dict[str, WorkItem] = {}
        # IDs the last scan found in more than one file, with every file; `_items`
        # keeps only one of them, so this is recorded before the merge (#576)
        self.duplicate_ids: dict[str, list[Path]] = {}  # keyed upper-case (#732)
        self._folded_items: dict[str, WorkItem] = {}
        # Files that look like items (start with `---`) but don't parse, with a
        # reason; the CLI reports them instead of dropping them silently (#139)
        self.parse_warnings: list[tuple[Path, str]] = []
        self._parse_warned: set[tuple[Path, str]] = set()  # the same, for lookups (#921)
        self._git_top: Path | None = None  # `git rev-parse --show-toplevel`, cached
        self._board: Board | None = None
        self._workflow_parser = WorkflowParser(self.repo_root / ".kanban")
        self._workflows: dict[str, WorkflowConfig] = {}
        self._hook_engine = HookEngine(
            hooks_config or (self.repo_root / ".kanban" / "hooks" / "kanban-hooks.yurtle.md"),
            repo_root=self.repo_root,  # hook actions run in the repo, not the cwd (#347)
        )
        self._hook_engine.set_callback("create_item", self._hook_create_item)
        # `_judge_at`: the commit this service judges by the config of (None: the
        # local config), and the last commit's judge (#831)
        self._judged_rev: str | None = None
        self._judges: dict[str, KanbanService] = {}

    def _get_board_for_item(self, item: WorkItem) -> BoardConfig | None:
        """Get the board configuration for a work item."""
        if not self.config.is_multi_board or not item.file_path:
            return None
        return self.config.get_board_for_path(item.file_path, self.repo_root)

    def _load_board_theme(
        self, board_config: BoardConfig | None,
    ) -> dict | None:
        """Load the theme for a board configuration.

        Returns the full theme dict, or None if no board or no theme.
        Centralises theme loading so callers don't duplicate the work.
        """
        if not board_config:
            return None

        from .config import _load_builtin_theme

        return _load_builtin_theme(
            board_config.preset, self.repo_root, self.config.theme_source
        )

    def _scan_board_theme(self, board: BoardConfig) -> dict | None:
        """`board`'s theme, loaded once per board within a scan scope (#665)."""
        if not self._scanning:
            return self._load_board_theme(board)
        if board.name not in self._board_theme_cache:
            self._board_theme_cache[board.name] = self._load_board_theme(board)
        return self._board_theme_cache[board.name]

    def _get_reverse_status_mapping(
        self, board_config: BoardConfig | None,
        theme: dict | None = None,
    ) -> dict[str, str]:
        """Get reverse mapping from canonical status to board-native name.

        Inverts the theme's ``status_mappings``; where a theme maps two names to one
        status, the last one listed wins (the name `move` writes).
        For HDD: ``{backlog: draft, in_progress: active, ...}``
        For nautical: ``{backlog: harbor, ready: provisioning, ...}``
        With no theme or no ``status_mappings``: an empty dict.
        """
        if theme is None:
            theme = self._load_board_theme(board_config)
        if not theme or "status_mappings" not in theme:
            return {}

        # Invert: {native: canonical} -> {canonical: native}. A target is resolved
        # first, so `in-progress` and `in_progress` are one status (#659); a target
        # that names no status is skipped, as `legal_status_names` skips it
        reverse: dict[str, str] = {}
        for native, target in theme["status_mappings"].items():
            try:
                reverse[WorkItemStatus.from_string(str(target)).value] = native
            except ValueError:
                continue
        return reverse

    def _item_reverse_status_mapping(self, item: WorkItem) -> dict[str, str]:
        """canonical → the item's theme's own status name: the item's board's theme,
        or on a single board the configured theme (#439)."""
        board_config, theme = self._item_theme(item)
        return self._get_reverse_status_mapping(board_config, theme)

    def legal_status_names(self, item: WorkItem) -> dict[str, WorkItemStatus]:
        """Every status name `move` accepts for `item` → its status: the six
        canonical names plus the item's own theme's names, no other theme's (#587)."""
        _, theme = self._item_theme(item)
        return _status_names(theme)

    def listed_status_names(self, item: WorkItem) -> list[str]:
        """The statuses to list when `move` refuses a name for `item`: each canonical
        status once, in workflow order, spelt as the item's theme names it (canonical
        where the theme doesn't rename it). Canonical names and aliases still resolve;
        they just aren't listed (#643)."""
        native: dict[WorkItemStatus, str] = {}
        for canonical, name in self._item_reverse_status_mapping(item).items():
            try:
                native[WorkItemStatus.from_string(str(canonical))] = str(name)
            except ValueError:
                continue
        return [native.get(s, s.value) for s in WorkItemStatus]

    def resolve_status_name(self, item: WorkItem, name: str) -> WorkItemStatus | None:
        """`name` as a status of `item`'s theme (case-insensitive, `-`/space → `_`),
        or None when the item's theme has no such name (#587)."""
        return self.legal_status_names(item).get(_fold_status_name(name))

    def status_label(self, item: WorkItem) -> str:
        """The item's status as its theme names it (hdd `draft` for backlog), for
        people; machine output keeps the canonical `item.status.value` (#439)."""
        return self._item_reverse_status_mapping(item).get(
            item.status.value, item.status.value
        )

    def _get_board_transitions(
        self, board_config: BoardConfig | None,
        theme: dict | None = None,
    ) -> dict[str, list[str]] | None:
        """Get board-specific state transitions.

        Returns the transitions dict from the board's theme, or None
        if not defined. Every entry is a list of status names, whichever way the
        theme dict arrived, so `move` and the offer agree (#474, #480).
        """
        if theme is None:
            theme = self._load_board_theme(board_config)
        if not theme or "transitions" not in theme:
            return None

        from .config import _clean_transitions

        # name what the warning is about: the board, else the theme's own name (#492)
        meta = theme.get("theme")
        name = meta.get("name") if isinstance(meta, dict) else None
        if board_config is not None:
            where = f"board {board_config.name} (theme {board_config.preset})"
        elif isinstance(name, str):
            where = f"theme {name}"
        else:
            where = "theme"
        return _clean_transitions(theme["transitions"], where)

    def scan(self) -> list[WorkItem]:
        """Scan configured paths for work items."""
        with self._scan_scope():
            return self._scan()

    @contextmanager
    def _scan_scope(self) -> Iterator[None]:
        """Theme lookups inside use one memo, fresh for the outermost scope; a nested
        scope keeps the outer one's (#448, #454, #459)."""
        outer = self._scanning
        if not outer:
            self._status_names_cache.clear()  # themes may have changed
            self._board_theme_cache.clear()
        self._scanning = True
        try:
            yield
        finally:
            self._scanning = outer

    def _index_item(self, item: WorkItem) -> None:
        """Put a scanned item in `_items`, recording its ID in `duplicate_ids` when
        another file already holds it (#576), IDs compared folded (`fold_id`) so
        `exp-5` and `EXP-5` are one ID (#732, #817): the dict keeps one, silently."""
        key = fold_id(item.id)
        prior = self._folded_items.get(key)
        if prior is not None and prior.file_path.resolve() != item.file_path.resolve():
            files = self.duplicate_ids.setdefault(key, [prior.file_path])
            if item.file_path not in files:
                files.append(item.file_path)
        self._folded_items[key] = item
        self._items[item.id] = item

    def _scan(self) -> list[WorkItem]:
        self._items.clear()
        self.duplicate_ids = {}
        self._folded_items = {}
        self.parse_warnings = []
        self._parse_warned = set()

        if self.config.is_multi_board:
            # Each board applies its OWN ignore patterns to its own path, exactly
            # as `board` does, so list/show/move agree with it (#124)
            for board in self.config.boards:
                for item in self._scan_board(board):
                    self._index_item(item)
            return list(self._items.values())

        work_paths = [Path(p) for p in self.config.get_work_paths()]
        for scan_path in work_paths:
            full_path = _under(self.repo_root, scan_path)
            if full_path.exists():
                for item in self._scan_directory(full_path):
                    self._index_item(item)

        # The board always scans the type folders it writes into (#113), even
        # when the configured scan_paths leave one out
        for type_dir in self._placement_dirs():
            # Only folders inside the repo; an absolute root elsewhere is scanned
            # (or not) exactly as its configured work paths say
            if not type_dir.exists() or not type_dir.is_relative_to(self.repo_root):
                continue
            rel = type_dir.relative_to(self.repo_root)
            if not any(rel == s or s in rel.parents for s in work_paths):
                for item in self._scan_directory(type_dir):
                    self._index_item(item)

        return list(self._items.values())

    def _placement_dirs(self) -> set[Path]:
        """Every folder `create` can write into on a single board (#113)."""
        return {self._get_type_directory(t) for t in WorkItemType}

    def _scan_directory(self, directory: Path) -> list[WorkItem]:
        """Scan a directory for Yurtle work items."""
        items = []

        for md_file in directory.rglob("*.md"):
            # Check ignore patterns
            if self._should_ignore(md_file):
                continue

            item = self._parse_file(md_file)
            if item:
                items.append(item)

        return items

    def _repo_relative(self, path: Path, root: Path | None = None) -> Path | None:
        """`path` relative to `root` (default: the repo root), or None when it lies
        outside. `..` is normalised first (`R/../O/x` is outside R), and a path
        reached through a symlinked directory (macOS `/tmp` → `/private/tmp`) is
        tried again with its directory resolved, never the file itself: an item
        file that is a symlink stays where it is linked from (#174). "Outside" is
        lexical: an in-repo directory that is a symlink to elsewhere counts as
        inside; no fleet repo has such a board (measured for #501)."""
        root = self.repo_root if root is None else root
        # a relative path is relative to the cwd: made absolute first, so a bare
        # `..` can't pass as inside (#511)
        normal = Path(os.path.abspath(path))
        try:
            return normal.relative_to(os.path.abspath(root))
        except ValueError:
            pass
        # resolve the normalised path's parent, so `R/..` can't reappear as
        # `<resolved R>/..` (#501)
        try:
            return (normal.parent.resolve() / normal.name).relative_to(root.resolve())
        except (ValueError, OSError):
            return None

    def _git_toplevel(self) -> Path:
        """The git work tree holding the repo root; `.kanban/` may sit in a
        subdirectory of it. Falls back to the repo root when git can't say."""
        if self._git_top is None:
            self._git_top = git_toplevel(self.repo_root) or self.repo_root
        return self._git_top

    def _ignore_key(self, path: Path) -> str:
        """What ignore patterns match: repo-relative inside the repo; a board root
        outside it (#156) matches its absolute path (fnmatch's `*` spans `/`, so
        `**/archive/**` still works)."""
        rel = self._repo_relative(path)
        return str(rel) if rel is not None else str(path)

    def _outside_git(self, path: Path) -> bool:
        """True when `path` lies outside the git work tree holding the repo root:
        the one test of "outside" for both where a file goes and whether git
        commits it (#174, #478)."""
        return self._repo_relative(path, self._git_toplevel()) is None

    def _hdd_board(self) -> BoardConfig | None:
        """The board holding the folder new hypotheses are written to, found through
        `_get_type_directory` itself so the two can't drift (#114, #478)."""
        folder = self._get_type_directory(WorkItemType.HYPOTHESIS)
        return self.config.get_board_for_path(folder, self.repo_root)

    def _outside_repo(self, *paths: Path) -> bool:
        """True, with a warning, when any of `paths` lies outside the git repository,
        so git can't commit it (#174): the caller skips git instead of failing late."""
        top = self._git_toplevel()
        outside = [str(p) for p in paths if self._outside_git(p)]
        if outside:
            logger.warning(
                f"Not committed: {', '.join(outside)} is outside the git repository at {top}"
            )
        return bool(outside)

    def _should_ignore(self, path: Path) -> bool:
        """Check a path against the single-board ``paths.ignore`` patterns.

        Single-board only. Multi-board scanning applies each board's own
        ``BoardConfig.ignore`` via ``_should_ignore_for_board`` (#124), so
        ``paths.ignore`` does not govern it (#129).
        """
        path_str = self._ignore_key(path)
        for pattern in self.config.paths.ignore:
            if fnmatch.fnmatch(path_str, pattern):
                return True
        return False

    def _parse_file(self, file_path: Path) -> WorkItem | None:
        """Parse a markdown file for work item data."""
        try:
            with file_path.open(newline="") as f:
                raw = f.read()
        except Exception as e:
            self._parse_failed(file_path, e)
            return None
        return self._parse_text(file_path, raw)

    def _parse_text(self, file_path: Path, raw: str) -> WorkItem | None:
        """Parse an item's text, as read with `newline=""`. `file_path` names it, for
        its board and theme; it need not exist, so an item at a fetched commit
        parses too (#574)."""
        try:
            if "\r" in raw and "\n" not in raw:
                # old Mac line endings: skipped, as the base id scan skips them (#661)
                if raw.startswith("---") and not file_path.name.startswith("_TEMPLATE"):
                    self._warn_parse(
                        file_path, "old Mac line endings (CR only): convert to LF"
                    )
                return None
            # universal newlines, as read_text() gives
            content = raw.replace("\r\n", "\n").replace("\r", "\n")

            # Parse frontmatter
            frontmatter = self._parse_frontmatter(content)
            if not isinstance(frontmatter, dict) or not frontmatter:
                self._note_unparseable(file_path, content)
                return None
            # the graph guard below reuses this parse and its verdicts (#311)
            parsed = dict(frontmatter)
            too_large: list[Any] = []
            for key in list(frontmatter):
                value = frontmatter[key]
                if _is_cyclic(value):
                    # a YAML anchor that contains itself (`tags: &a [x, *a]`) can't be
                    # serialised or rendered: drop the field, keep the item (#262). The
                    # graph parser copes with cycles on its own
                    why = "is cyclic (a YAML anchor that contains itself)"
                elif _expanded_size(value, _MAX_FIELD_NODES) > _MAX_FIELD_NODES:
                    # shared aliases nested a few levels deep (a "billion laughs") expand
                    # to more than any rendering or export can hold (#277)
                    why = f"is too large (over {_MAX_FIELD_NODES:,} values once aliases expand)"
                    too_large.append(key)
                else:
                    continue
                logger.warning(f"{file_path}: frontmatter field `{key}` {why}; ignored")
                del frontmatter[key]

            # Get required fields
            item_id = frontmatter.get("id")
            if not item_id:
                # Generate from filename, folded as every ID is compared (#904)
                item_id = fold_id(file_path.stem).replace("-", "_")
            item_id = str(_scalar_text(item_id))  # `id: 42` / `id: true` are text (#179, #245)

            item_type_str = frontmatter.get("type", "task")
            if item_type_str in (None, "") or isinstance(item_type_str, (int, float)):
                # `type:` empty or `type: 5` falls back; a list still warns (#179)
                item_type_str = "task"
            try:
                item_type = WorkItemType.from_string(item_type_str)
            except ValueError:
                # Try theme mapping
                item_type = self._map_theme_type(item_type_str, file_path)
                if not item_type:
                    return None

            status_str = frontmatter.get("status", "backlog")
            if status_str in (None, "") or isinstance(status_str, (int, float)):
                # `status:` empty or numeric falls back; a list still warns (#179)
                status_str = "backlog"
            try:
                status = WorkItemStatus.from_string(status_str)
            except ValueError:
                # Try theme mapping
                status = self._map_theme_status(status_str, file_path)
                if not status:
                    status = WorkItemStatus.BACKLOG

            # Get title
            title = frontmatter.get("title", file_path.stem.replace("-", " ").title())
            if title is not None and not isinstance(title, str):
                title = str(_scalar_text(title))  # `title: 2024` / `true` (#179, #245)

            # Parse optional fields
            # YAML reads `assignee: 5` / `priority: 1` as numbers; these are text
            # everywhere they're shown (#179, #206). A list stays a list.
            priority = _scalar_text(frontmatter.get("priority"))
            assignee = _scalar_text(frontmatter.get("assignee"))
            tags = _list_text(frontmatter.get("tags", []))

            depends_on = _list_text(frontmatter.get("depends_on", []))

            related = _list_text(frontmatter.get("related", []))

            created = None
            if "created" in frontmatter:
                created_val = frontmatter["created"]
                if isinstance(created_val, date):
                    created = created_val
                elif isinstance(created_val, str):
                    try:
                        created = date.fromisoformat(created_val)
                    except ValueError:
                        pass

            # Extract description (content after frontmatter, before yurtle block)
            description = self._extract_description(content)
            comments = self._parse_comments(self._split_comments(content)[1])

            # Parse priority rank and value summary
            priority_rank = frontmatter.get("priority_rank")
            if priority_rank is not None:
                try:
                    priority_rank = int(priority_rank)
                except (ValueError, TypeError):
                    priority_rank = None
            value_summary = _scalar_text(frontmatter.get("value_summary"))

            # Parse resolution fields
            resolution = frontmatter.get("resolution")
            if resolution is not None:
                valid_resolutions = {
                    "completed", "superseded", "wont_do",
                    "duplicate", "obsolete", "merged",
                }
                if resolution not in valid_resolutions:
                    logger.warning(
                        f"Unknown resolution '{resolution}' in {file_path}. "
                        f"Valid values: {', '.join(sorted(valid_resolutions))}"
                    )
            superseded_by = _list_text(frontmatter.get("superseded_by", []))

            compute_requirement = frontmatter.get("compute_requirement")

            # Collect remaining frontmatter keys into metadata
            _parsed_keys = {
                "id", "title", "type", "status", "priority", "assignee",
                "created", "tags", "depends_on", "related", "resolution",
                "superseded_by", "priority_rank", "value_summary",
                "compute_requirement",
            }
            metadata = {
                k: v for k, v in frontmatter.items()
                if k not in _parsed_keys and v is not None
            }
            # Preserve original status string for theme-aware rendering
            metadata["_original_status"] = status_str

            # Parse RDF graph from frontmatter + fenced blocks. _parse_graph blanks the
            # too-large fields found above, reusing this parse (#277, #296, #311), or
            # skips the graph when one sits under a non-text key
            graph = self._parse_graph(content, (parsed, too_large))

            return WorkItem(
                id=item_id,
                title=title,
                item_type=item_type,
                status=status,
                file_path=file_path,
                priority=priority,
                assignee=assignee,
                created=created,
                tags=tags,
                depends_on=depends_on,
                related=related,
                description=description,
                comments=comments,
                metadata=metadata,
                resolution=resolution,
                superseded_by=superseded_by,
                graph=graph,
                priority_rank=priority_rank,
                value_summary=value_summary,
                compute_requirement=compute_requirement,
            )

        except Exception as e:
            self._parse_failed(file_path, e, raw)
            return None

    def _warn_parse(self, file_path: Path, reason: str) -> None:
        """Record a file's parse warning once: a file read again (an outside
        board's, on each push attempt) says it once, not per read (#879)."""
        warning = (file_path, reason)
        # a set beside the list, so a board of thousands of broken files isn't
        # quadratic (#921); reset with it, and rebuilt should a reset miss it
        if len(self._parse_warned) != len(self.parse_warnings):
            self._parse_warned = set(self.parse_warnings)
        if warning not in self._parse_warned:
            self._parse_warned.add(warning)
            self.parse_warnings.append(warning)

    def _parse_failed(
        self, file_path: Path, e: Exception, raw: str | None = None
    ) -> None:
        """A file that can't be read, or crashes after its frontmatter parsed, is
        reported like any unparseable item, not dropped silently (#158). `raw` is
        its text when it was read."""
        logger.debug(f"Failed to parse {file_path}: {e}")
        # only files that look like items, as for #139's warnings: they start
        # with `---` and aren't templates (a non-UTF-8 plain note stays silent)
        if raw is not None:
            looks_like_item = raw.startswith("---")
        else:
            try:
                looks_like_item = file_path.read_bytes().startswith(b"---")
            except OSError:
                looks_like_item = False
        if looks_like_item and not file_path.name.startswith("_TEMPLATE"):
            # a RecursionError is YAML nested past what the parser can walk: say
            # that, not Python's internals (#280). Frontmatter too deep to parse is
            # caught earlier (#297); one arriving here comes from a later step,
            # which in practice is still a too-deep value being walked (#309)
            reason = (
                "frontmatter nested too deeply to parse"
                if isinstance(e, RecursionError)
                else f"{type(e).__name__}: {e}"
            )
            self._warn_parse(file_path, reason)

    def _split_frontmatter(self, content: str) -> tuple[str, str] | None:
        """Split content into (frontmatter text, everything after the closing `---`).

        Uses the same line-anchored match as the frontmatter writers, so a `---`
        inside a value (`title: "A --- B"`) is not taken as a delimiter (#103).
        """
        match = self._FRONTMATTER_RE.match(content)
        if not match:
            return None
        return match.group(1), content[match.end() :]

    def _note_unparseable(self, file_path: Path, content: str) -> None:
        """Record why a file that looks like an item (starts with `---`) didn't
        parse (#139). Plain notes and `_TEMPLATE*` files stay silent."""
        reason = self._unparseable_reason(file_path, content)
        if reason is not None:
            self._warn_parse(file_path, reason)

    def _unparseable_reason(self, file_path: Path, content: str) -> str | None:
        """Why frontmatter that is there doesn't parse, or None when there is none
        to report (no `---`, a `_TEMPLATE*` file, Turtle frontmatter) (#139, #188)."""
        if not content.startswith("---") or file_path.name.startswith("_TEMPLATE"):
            return None
        split = self._split_frontmatter(content)
        if split is not None and _TURTLE_FRONTMATTER.match(split[0]):
            # Yurtle frontmatter may be Turtle, not YAML (`@prefix …`): a document,
            # not a broken item
            return None
        if split is None:
            first_newline = content.find("\n")
            later = content[first_newline + 1 :] if first_newline != -1 else ""
            reason = (
                "opening line is not a plain `---` (or `--- # comment`)"
                if re.search(r"^---", later, re.MULTILINE)
                else "no closing ---"
            )
        else:
            try:
                data = yaml.safe_load(split[0])
            except RecursionError:
                return "frontmatter nested too deeply to parse"  # as the scan says (#280, #297)
            except yaml.YAMLError as e:
                reason = "YAML error: " + " ".join(str(e).split())[:120]
            else:
                reason = (
                    "frontmatter is empty"
                    if not data
                    else "frontmatter is not a key: value mapping"
                )
        return reason

    def _parse_frontmatter(self, content: str) -> dict[str, Any] | None:
        """Parse YAML frontmatter from markdown content; None unless it is a mapping
        (a YAML list or scalar is as unusable as broken YAML to every caller, #321)."""
        split = self._split_frontmatter(content)
        if split is None:
            return None

        try:
            data = yaml.safe_load(split[0])
        except (yaml.YAMLError, RecursionError):  # too deep counts as unparseable (#297)
            return None
        return data if isinstance(data, dict) else None

    def _graph_safe_text(
        self, content: str, guarded: tuple[dict[Any, Any], list[Any]] | None = None
    ) -> str | None:
        """`content` with every frontmatter field the graph parser must not expand
        blanked to `[]`: a too-large one (#277), and any field that aliases an anchor
        defined inside one, which would otherwise be left undefined (#296). None when
        such a field has a key that can't be located in the text (`5:`, `true:`).
        Every caller of the graph parser gets this, not just the scan (#296).

        `guarded` is the scan's own (frontmatter, too-large keys) for this content, so
        the scan parses and sizes each file once (#311); without it, this checks itself.

        Known limits, all only costing triples, never an item (#311, #322): a kept field
        sharing a container with a dropped one only through an anchor defined outside
        the dropped field is blanked too; and an alias of a *scalar* anchor inside a
        dropped field (`tags: [&s foo, ...]`, `owner: *s`) isn't tracked, nor is a
        cyclic kept field aliasing a dropped anchor (`cyc: &c [*a0, *c]`), so the graph
        parser meets an undefined alias and that item gets an empty graph (0 triples)."""
        if guarded is not None:
            frontmatter, too_large = guarded
            blank = list(too_large)
        else:
            frontmatter = self._parse_frontmatter(content)
            if not isinstance(frontmatter, dict):
                return content
            blank = [
                key for key, value in frontmatter.items()
                if not _is_cyclic(value)
                and _expanded_size(value, _MAX_FIELD_NODES) > _MAX_FIELD_NODES
            ]
        if not blank:
            return content
        # the containers inside the dropped values (each walked once) ...
        inside = _container_ids(frontmatter[key] for key in blank)
        # ... and any other field sharing one of them (an alias of a dropped anchor)
        blank += [
            key for key, value in frontmatter.items()
            if key not in blank and not _is_cyclic(value)
            and _container_ids([value]) & inside
        ]
        if not all(isinstance(key, str) for key in blank):
            return None
        for key in blank:
            content = self._add_or_update_frontmatter_field(content, key, "[]")
        return content

    def _parse_graph(
        self, content: str, guarded: tuple[dict[Any, Any], list[Any]] | None = None
    ) -> Graph | None:
        """Parse RDF graph from file content using yurtle-rdflib.

        Returns an rdflib.Graph with triples from both YAML/Turtle frontmatter
        and fenced ```turtle/```yurtle blocks in the markdown body.
        Returns None if parsing fails.

        The IRI `<>` resolved to (the cwd at parse time) is recorded for the
        returned graph, so a later merge maps it to the item; adding triples with
        `+=` keeps that parse-time IRI (#404, #413, #421).
        """
        safe_text = self._graph_safe_text(content, guarded)
        if safe_text is None:
            return None
        content = safe_text
        try:
            import yurtle_rdflib
            # Suppress rdflib URI warnings for placeholder URIs like
            # y3:tool_experience_{hash} that appear in prose/examples (#59).
            # rdflib uses logging.warning(), not warnings.warn().
            rdflib_logger = logging.getLogger("rdflib.term")
            old_level = rdflib_logger.level
            rdflib_logger.setLevel(logging.ERROR)
            try:
                doc = yurtle_rdflib.parse_yurtle(content)
                # the IRI `<>` got here (the parser resolves it against the cwd);
                # see the docstring (#404, #413, #421)
                set_self_iri(doc.graph, Path.cwd().as_uri() + "/")
            finally:
                rdflib_logger.setLevel(old_level)
            return doc.graph
        except Exception as e:
            logger.debug(f"Failed to parse graph: {e}")
            return None

    def _split_comments(self, content: str) -> tuple[str, str]:
        """Split an item's text after the frontmatter into (body, comments section).

        The section starts at the first `## Comments` line outside fenced code
        (#583) and runs to the end; it is "" when the item has none (#605).
        """
        split = self._split_frontmatter(content)
        if split is not None:
            content = split[1]
        cut = self._find_line_outside_fences(content, 0, self._COMMENTS_RE)
        return (content, "") if cut < 0 else (content[:cut], content[cut:])

    # `### author (YYYY-MM-DD HH:MM)`, as add_comment writes it
    _COMMENT_HEAD_RE = re.compile(
        r"### (.+) \((\d{4}-\d\d-\d\d \d\d:\d\d)\)[ \t]*$", re.MULTILINE
    )

    _KNOWLEDGE_BLOCK_RE = re.compile(r"```(?:yurtle|turtle).*?```", re.DOTALL)
    # a line add_comment escaped: backslashes then `###`
    _ESCAPED_HEAD_RE = re.compile(r"\\+###")

    @classmethod
    def _escape_comment_line(cls, line: str) -> str:
        """Escape a comment-text line that could read back as a comment heading:
        a line of backslashes then `###` gets one more backslash in front (#605).
        The text stays visible; the parser takes the backslash off again."""
        return "\\" + line if re.match(r"\\*###", line) else line

    @classmethod
    def _unescape_comment_line(cls, line: str) -> str:
        """Undo `_escape_comment_line` (#605)."""
        return line[1:] if cls._ESCAPED_HEAD_RE.match(line) else line

    def _parse_comments(self, section: str) -> list[Comment]:
        """The comments in a `## Comments` section, in file order (#605).

        Each starts at a `### author (YYYY-MM-DD HH:MM)` line outside fenced code
        and runs to the next one; its text is the lines between, without the
        blank lines around them. Text before the first heading is never dropped:
        it reads as a leading comment with author "" and no date (#644).
        """
        comments: list[Comment] = []
        pos = section.find("\n") + 1 if section else 0  # past the `## Comments` line
        if pos <= 0:
            return comments
        first = pos
        starts: list[tuple[int, int, re.Match[str]]] = []  # (line start, text start, head)
        while (at := self._find_line_outside_fences(section, pos, self._COMMENT_HEAD_RE)) >= 0:
            eol = section.find("\n", at)
            nxt = len(section) if eol < 0 else eol + 1
            head = self._COMMENT_HEAD_RE.match(section, at)
            if head:
                starts.append((at, nxt, head))
            pos = nxt
        preamble = self._comment_text(section[first : starts[0][0] if starts else len(section)])
        if preamble:
            comments.append(Comment(content=preamble, author="", created_at=None))
        for i, (_, text_start, head) in enumerate(starts):
            text_end = starts[i + 1][0] if i + 1 < len(starts) else len(section)
            try:
                when = datetime.strptime(head.group(2), "%Y-%m-%d %H:%M")
            except ValueError:
                continue  # not a real timestamp: not a comment heading
            text = self._comment_text(section[text_start:text_end])
            comments.append(Comment(content=text, author=head.group(1), created_at=when))
        return comments

    def _comment_text(self, text: str) -> str:
        """A comment's text as stored: unescaped, without the blank lines around it."""
        # a knowledge block (the kb:statusChange history a later `move`
        # appends) is never comment text
        text = self._KNOWLEDGE_BLOCK_RE.sub("", text)
        lines = [self._unescape_comment_line(ln) for ln in text.split("\n")]
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        return "\n".join(lines)

    def _extract_description(self, content: str) -> str | None:
        """Extract description from markdown content: the text after the frontmatter
        and before the `## Comments` section, without knowledge blocks or the H1.
        Comments are their own field (`item.comments`), never part of it (#605)."""
        content, _ = self._split_comments(content)

        # Remove yurtle and turtle knowledge blocks
        content = self._KNOWLEDGE_BLOCK_RE.sub("", content)

        # Remove the heading (title): only a `# ` H1 on the first non-blank line,
        # so a `# ` line in a code block or a leading `## Background` is kept (#583)
        lines = content.strip().split("\n")
        if lines and lines[0].startswith("# "):
            lines = lines[1:]

        description = "\n".join(lines).strip()
        return description if description else None

    def _map_theme_type(
        self, type_str: str, file_path: Path | None = None
    ) -> WorkItemType | None:
        """Map theme-specific type to standard type, through the theme of the board
        `file_path` is on (#652); with no board, the single board's theme, or every
        board's, first wins, on multi-board (#665)."""
        # First try direct enum match (handles HDD types that are in the enum)
        try:
            return WorkItemType.from_string(type_str)
        except ValueError:
            pass

        board = None
        if self.config.is_multi_board and file_path is not None:
            board = self.config.get_board_for_path(file_path, self.repo_root)
        if board is not None:
            themes = [self._scan_board_theme(board)]
        elif self.config.is_multi_board:
            themes = [self._scan_board_theme(b) for b in self.config.boards]
        else:
            themes = [self.config.get_theme()]
        for theme in themes:
            for type_id in (theme or {}).get("item_types") or {}:
                if type_id == type_str.lower():
                    # Map common nautical types
                    mapping = {
                        "expedition": WorkItemType.FEATURE,
                        "voyage": WorkItemType.EPIC,
                        "directive": WorkItemType.TASK,
                        "hazard": WorkItemType.BUG,
                        "signal": WorkItemType.IDEA,
                    }
                    return mapping.get(type_id, WorkItemType.TASK)
        return None

    def _map_theme_status(
        self, status_str: str, file_path: Path | None = None
    ) -> WorkItemStatus | None:
        """Map a theme-specific status to a standard status through the item's own
        board's theme only (#439, #448); another theme's names read as unknown (#633)."""
        # `move` and `create` write the theme's own names (hdd `abandoned` is
        # blocked), so a scan must read them back (#439) — through the item's own
        # board, so two themes can give one name different meanings (#448)
        return self._theme_status_names(file_path).get(_fold_status_name(status_str))

    def _single_board_theme(self) -> dict | None:
        """The configured theme: memoised within a scan scope, the current one
        outside a scope (#448, #459, #463)."""
        if not self._scanning:
            return self.config.get_theme()  # the current theme outside a scan (#459)
        cache = self._status_names_cache
        if "__theme__" not in cache:
            cache["__theme__"] = self.config.get_theme()
        return cache["__theme__"]

    def _theme_status_names(self, file_path: Path | None) -> dict[str, WorkItemStatus]:
        """native name → status for the theme of the board `file_path` is on (the
        single board's theme without boards; every board's, first wins, when the
        board is unknown). Memoised per board within a scan scope (#448, #459)."""
        board = None
        if self.config.is_multi_board and file_path is not None:
            board = self.config.get_board_for_path(file_path, self.repo_root)
        return self._board_status_names(board)

    def _board_status_names(self, board: BoardConfig | None) -> dict[str, WorkItemStatus]:
        """native name → status for `board`'s theme (the single board's theme without
        boards; every board's, first wins, for None on multi-board). Memoised per
        board within a scan scope (#448, #459)."""
        key = board.name if board else None
        cache = self._status_names_cache if self._scanning else {}  # (#459)
        if key in cache:
            return cache[key]
        if board is not None:
            themes = [self._scan_board_theme(board)]
        elif self.config.is_multi_board:
            themes = [self._scan_board_theme(b) for b in self.config.boards]
        else:
            themes = [self._single_board_theme()]
        names: dict[str, WorkItemStatus] = {}
        for theme in themes:
            for native, canonical in ((theme or {}).get("status_mappings") or {}).items():
                try:
                    status = WorkItemStatus.from_string(str(canonical))
                except ValueError:
                    continue
                names.setdefault(_fold_status_name(str(native)), status)
        cache[key] = names
        return names

    def get_board(self, board_name: str | None = None) -> Board:
        """Get the kanban board with all items.

        In multi-board mode, specify board_name to get a specific board.
        Without arguments, returns the default board.

        Args:
            board_name: Name of the board (multi-board mode) or None for default

        Returns:
            Board object with items and column configuration
        """
        # Multi-board mode
        if self.config.is_multi_board:
            return self._get_board_multi(board_name)

        # Single-board mode (cached)
        if self._board is None:
            self._board = self._board_of(None, self.scan())
        return self._board

    def _board_of(self, board_config: BoardConfig | None, items: list[WorkItem]) -> Board:
        """The board `board_config` (None: the single board) showing `items`: its
        columns, WIP limits and column mapping (#574: also a fetched tree's)."""
        if board_config is None:
            return Board(
                id="main",
                name=self.config.theme.title() + " Board",
                columns=self._get_columns_from_theme(),
                items=items,
                column_status_map=self._get_column_status_map(None),
            )
        # Get columns from board's preset, then apply WIP overrides
        columns = self._get_columns_from_preset(board_config.preset)
        return Board(
            id=board_config.name,
            name=f"{board_config.name.title()} Board",
            columns=self._apply_wip_overrides(columns, board_config),
            items=items,
            column_status_map=self._get_column_status_map(board_config),
        )

    def _get_board_multi(self, board_name: str | None = None) -> Board:
        """Get a specific board in multi-board mode.

        Args:
            board_name: Name of the board, or None for default/cwd-matched board

        Returns:
            Board object for the specified board
        """
        board_config = self._named_board(board_name)
        if not board_config:
            # No board found, return empty board
            return Board(
                id="empty",
                name="No Board Configured",
                columns=[],
                items=[],
            )

        # Scan items for this board only
        return self._board_of(board_config, self._scan_board(board_config))

    def _named_board(self, board_name: str | None) -> BoardConfig | None:
        """The board `board_name` names, else the default; with no name, the board
        you're standing in, else the default (multi-board)."""
        if board_name:
            board_config = self.config.get_board(board_name)
            if not board_config:
                # Warn user and fall back to default
                logger.warning(f"Board '{board_name}' not found, falling back to default")
                board_config = self.config.get_default_board()
            return board_config
        # the board you're standing in: deliberately the process cwd, so running
        # a command inside a board's folder picks that board (#348)
        cwd = Path.cwd()
        board_config = self.config.get_board_for_path(cwd, self.repo_root)
        return board_config or self.config.get_default_board()

    def _scan_board(self, board_config: BoardConfig) -> list[WorkItem]:
        """Scan a specific board for work items.

        Args:
            board_config: Configuration for the board to scan

        Returns:
            List of work items found in the board's path
        """
        with self._scan_scope():  # get_board/get_items scan here directly (#459)
            return self._scan_board_items(board_config)

    def _scan_board_items(self, board_config: BoardConfig) -> list[WorkItem]:
        items = []
        board_path = _under(self.repo_root, board_config.path)

        if board_path.exists():
            for md_file in board_path.rglob("*.md"):
                # Check board-specific ignore patterns
                if self._should_ignore_for_board(md_file, board_config):
                    continue

                item = self._parse_file(md_file)
                if item:
                    items.append(item)

        return items

    def _should_ignore_for_board(self, path: Path, board_config: BoardConfig) -> bool:
        """Check if a path should be ignored for a specific board.

        Args:
            path: File path to check
            board_config: Board configuration with ignore patterns

        Returns:
            True if the path should be ignored
        """
        path_str = self._ignore_key(path)

        for pattern in board_config.ignore:
            if fnmatch.fnmatch(path_str, pattern):
                return True
        return False

    def _get_columns_from_preset(
        self,
        preset: str,
        wip_limit_overrides: dict[str, int] | None = None,
    ) -> list[Column]:
        """Get column definitions from a preset/theme.

        Args:
            preset: Name of the preset (e.g., 'software', 'nautical', 'hdd')
            wip_limit_overrides: Optional per-column WIP limit overrides from
                board config. Keys are column IDs (e.g., 'in_progress',
                'underway'). Overrides the theme's default wip_limit values.

        Returns:
            List of Column objects
        """
        from .config import _load_builtin_theme

        theme = _load_builtin_theme(preset, self.repo_root, self.config.theme_source)
        overrides = wip_limit_overrides or {}
        columns = []

        if theme and "columns" in theme:
            for col_id, col_def in theme["columns"].items():
                # Apply override: check col_id directly, then its display name
                wip_limit = col_def.get("wip_limit")
                if col_id in overrides:
                    wip_limit = overrides[col_id]
                else:
                    # Also check the mapped status value (e.g. "underway" maps
                    # to WorkItemStatus.IN_PROGRESS whose value is "in_progress")
                    for override_key, override_val in overrides.items():
                        if override_key == col_def.get("name", "").lower().replace(" ", "_"):
                            wip_limit = override_val
                            break
                columns.append(
                    Column(
                        id=col_id,
                        name=col_def.get("name", col_id.title()),
                        order=col_def.get("order", 0),
                        wip_limit=wip_limit,
                        description=col_def.get("description"),
                    )
                )
        else:
            # Default columns
            # the same defaults as a single board's (#402)
            columns = [
                Column(id="backlog", name="Backlog", order=1),
                Column(id="ready", name="Ready", order=2, wip_limit=overrides.get("ready", 5)),
                Column(
                    id="in_progress",
                    name="In Progress",
                    order=3,
                    wip_limit=overrides.get("in_progress", 3),
                ),
                Column(id="review", name="Review", order=4, wip_limit=overrides.get("review", 2)),
                Column(id="done", name="Done", order=5),
            ]

        return sorted(columns, key=lambda c: c.order)

    def _apply_wip_overrides(
        self, columns: list[Column], board_config: BoardConfig
    ) -> list[Column]:
        """Apply board-level WIP limit overrides to columns.

        Merges overrides from:
        1. BoardConfig.wip_limits (YAML config)
        2. Yurtle WIP policy file (.yurtle-kanban/wip-policy.md)

        The Yurtle policy takes precedence over YAML config, which takes
        precedence over theme defaults.
        """
        from .config import load_wip_policy

        # Collect overrides: YAML first, then Yurtle on top
        wip_overrides = board_config.wip_limits

        # Board-level unlimited: clear all WIP limits
        if wip_overrides is None:
            for col in columns:
                col.wip_limit = None
                col.type_wip_limits = None
            return columns

        # Load Yurtle policy if available
        config_dir = self.repo_root / ".yurtle-kanban"
        yurtle_policy = load_wip_policy(config_dir)
        if yurtle_policy and board_config.name in yurtle_policy:
            board_policy = yurtle_policy[board_config.name]
            if board_policy is None:
                # Yurtle policy says this board is unlimited
                for col in columns:
                    col.wip_limit = None
                    col.type_wip_limits = None
                return columns
            # Merge: Yurtle policy overrides YAML config
            merged = dict(wip_overrides)
            merged.update(board_policy)
            wip_overrides = merged

        if not wip_overrides:
            return columns

        for col in columns:
            if col.id not in wip_overrides:
                continue

            override = wip_overrides[col.id]

            if override is None:
                # Explicitly unlimited column
                col.wip_limit = None
                col.type_wip_limits = None
            elif isinstance(override, int):
                # Legacy aggregate limit
                col.wip_limit = override
            elif isinstance(override, dict):
                # Per-type limits
                col.type_wip_limits = {}
                for type_name, limit in override.items():
                    col.type_wip_limits[type_name] = limit
                # Clear legacy limit when per-type is set
                col.wip_limit = None

        return columns

    def _get_column_status_map(
        self, board_config: BoardConfig | None,
    ) -> dict[str, WorkItemStatus]:
        """column id → status for one board: the six canonical names plus that
        board's own theme `status_mappings` (the configured theme on a single
        board), no other theme's (#613, #633). A column neither names has no status."""
        mappings: dict[str, WorkItemStatus] = {s.value: s for s in WorkItemStatus}
        mappings.update(self._board_status_names(board_config))
        return mappings

    def _get_columns_from_theme(self) -> list[Column]:
        """Get column definitions from theme."""
        theme = self.config.get_theme()
        columns = []

        if theme and "columns" in theme:
            for col_id, col_def in theme["columns"].items():
                columns.append(
                    Column(
                        id=col_id,
                        name=col_def.get("name", col_id.title()),
                        order=col_def.get("order", 0),
                        wip_limit=col_def.get("wip_limit"),
                        description=col_def.get("description"),
                    )
                )
        else:
            # Default software columns
            columns = [
                Column("backlog", "Backlog", 1),
                Column("ready", "Ready", 2, wip_limit=5),
                Column("in_progress", "In Progress", 3, wip_limit=3),
                Column("review", "Review", 4, wip_limit=2),
                Column("done", "Done", 5),
            ]

        return sorted(columns, key=lambda c: c.order)

    def get_item(self, item_id: str) -> WorkItem | None:
        """Get a work item by ID."""
        if not self._items:
            self.scan()
        return self._lookup(item_id)

    def _lookup(self, item_id: str) -> WorkItem | None:
        """The cached item for `item_id`: an exact match first, else the one whose
        ID folds to the same (`fold_id`), since `exp-9` and `EXP-9` are one ID
        (#732, #741, #817)."""
        item = self._items.get(item_id)
        if item is None and (folded := self._folded_items.get(fold_id(item_id))):
            item = self._items.get(folded.id)
        return item

    def _current_item(self, item_id: str) -> WorkItem | None:
        """A writer's lookup: the item as its file says NOW (#638).

        Plain reads may serve the cache, but a write is validated against the
        file, so an edit made outside this service (by hand, a `git checkout`)
        since the last scan is respected. Reads one file, not the board; an ID
        the cache doesn't know is looked for with one rescan.
        """
        item = self.get_item(item_id)
        if item is None:
            self.scan()
            return self._lookup(item_id)
        return self._reread_item(item)

    def refuse_duplicate(self, item: WorkItem, action: str) -> None:
        """Refuse `action` ("a move", "an update", ...) on an item whose ID, case
        folded, is on more than one board: which copy it meant is ambiguous
        (#721, #732, #742). Names every file."""
        files = self.duplicate_ids.get(fold_id(item.id))
        if files:
            where = ", ".join(self._display_path(f) for f in files)
            raise InputRefused(
                f"{item.id} is on more than one board ({where}): {action} to it is "
                "ambiguous; fix the duplicate ID first"
            )

    def _writable_item(self, item_id: str, action: str) -> WorkItem:
        """A writer's item: as its file says now (#638), found, and not a duplicated
        ID (#742)."""
        item = self._current_item(item_id)
        if not item:
            raise InputRefused(f"Item not found: {item_id}")
        self.refuse_duplicate(item, action)
        return item

    def _reread_item(self, item: WorkItem) -> WorkItem | None:
        """Re-parse one item's file and put the result in the cache (#638).

        Used before a write's checks and after the write, so the item returned
        and every later read show what is in the file. A file that is gone, or
        no longer holds this ID, falls back to a rescan.
        """
        fresh = None
        if item.file_path.exists():
            with self._scan_scope():
                fresh = self._parse_file(item.file_path)
        if fresh is None or fresh.id != item.id:
            self.scan()
            return self._items.get(item.id)
        self._items[item.id] = fresh
        return fresh

    def get_items(
        self,
        status: WorkItemStatus | None = None,
        item_type: WorkItemType | None = None,
        assignee: str | None = None,
        board: str | None = None,
        priority: list[str] | None = None,
    ) -> list[WorkItem]:
        """Get items with optional filters.

        Args:
            status: Filter by status
            item_type: Filter by type
            assignee: Filter by assignee
            board: Filter to a specific board (multi-board mode only).
                   If None, returns items from all boards (default behavior).
            priority: Filter by priority level(s) (e.g. ["critical", "high"]).
        """
        if board and self.config.is_multi_board:
            board_config = self.config.get_board(board)
            if board_config:
                items = self._scan_board(board_config)
            else:
                items = []
        else:
            if not self._items:
                self.scan()
            items = list(self._items.values())

        if status:
            items = [i for i in items if i.status == status]
        if item_type:
            items = [i for i in items if i.item_type == item_type]
        if assignee:
            items = [i for i in items if i.assignee == assignee]
        if priority:
            items = [i for i in items if (i.priority or "medium") in priority]

        return sorted(items, key=lambda i: (-i.priority_score, -i.numeric_id))

    def _get_type_directory(self, item_type: WorkItemType, board_name: str | None = None) -> Path:
        """Get the directory for placing a file of this item type.

        Resolution order:
        1. Theme item_types[type].path (explicit per-type directory)
           In multi-board mode, searches all boards' themes for the type.
        2. PathConfig attributes (features, bugs, epics, tasks)
        3. scan_paths keyword match (e.g., "expeditions/" for expedition type)
        4. The type's own folder under the board root (``<root>/<plural>/``)

        Placement is the board's job, never the agent's: each type goes into its
        own named folder, and ``scan()`` always covers the folders written here
        (#113, decided in #109).
        """
        # Priority 1: Theme-defined path, kept where the board scans it (#102),
        # else its own folder under the board root (#113)
        if self.config.is_multi_board:
            board_root = self._landing_board(item_type, board_name)
            type_def = self._board_type_def(board_root, item_type)
            if board_root is not None and "path" in type_def:
                return self._scanned_type_dir(
                    type_def["path"], [board_root.get_path()], board_root.path,
                )
            root = board_root.path if board_root else "work/"
        else:
            # inside a scan, the scan's memoised theme; otherwise the current one, so
            # an override written since the last scan places new files at once (#454)
            theme = self._single_board_theme() if self._scanning else self.config.get_theme()
            if theme and "item_types" in theme:
                type_def = theme["item_types"].get(item_type.value, {})
                if "path" in type_def:
                    return self._scanned_type_dir(
                        type_def["path"], self.config.get_work_paths(), self._board_root(),
                    )
            root = self._board_root()

        # Priority 2: Legacy PathConfig attributes (features, bugs, epics, tasks)
        type_path = getattr(self.config.paths, item_type.value + "s", None)
        if type_path:
            return _under(self.repo_root, type_path)

        # Priority 3: Match scan_paths by type keyword
        plural = self._type_folder(item_type)
        for scan_path in self.config.paths.scan_paths:
            if plural in scan_path.lower() or item_type.value in scan_path.lower():
                return _under(self.repo_root, scan_path)

        # Priority 4: the type's own named folder under the board root (#113)
        return _under(self.repo_root, root) / plural

    def _landing_board(
        self, item_type: WorkItemType, board_name: str | None = None
    ) -> BoardConfig | None:
        """Multi-board: the board a new item of `item_type` lands on. The named board;
        else the default_board first, then the others in config order, taking the
        first whose theme gives the type a path (#114); a type no board's theme gives
        a path goes to the named board, else the default_board (where unrouted work
        goes, #144), else the first board."""
        if board_name:
            boards = [self.config.get_board(board_name)]
        else:
            default = self.config.get_default_board()
            boards = ([default] if default else []) + [
                b for b in self.config.boards if b is not default
            ]
        for board in boards:
            if board is not None and "path" in self._board_type_def(board, item_type):
                return board
        return (
            (self.config.get_board(board_name) if board_name else None)
            or (None if board_name else self.config.get_default_board())
            or (self.config.boards[0] if self.config.boards else None)
        )

    def _board_type_def(self, board: BoardConfig | None, item_type: WorkItemType) -> dict:
        """`board`'s theme's definition of `item_type` ({} when it has none)."""
        theme = (
            board.get_theme(self.repo_root, self.config.theme_source)
            if board is not None else None
        )
        return ((theme or {}).get("item_types") or {}).get(item_type.value) or {}

    def _board_root(self) -> str:
        """The folder a single board's type folders live under (#113).

        ``root`` when it contains every scan path (``kanban-work/`` scanning just
        ``kanban-work/features/``); otherwise the directory the board really scans
        (#94: a default ``init`` says ``root: work/`` but scans ``kanban-work/*``).
        """
        root = self.config.paths.root
        # `~` and absolute spellings of one place are the same place (#494)
        scans = [Path(p).expanduser() for p in self.config.paths.scan_paths]
        top = Path(root).expanduser() if root else None
        if top and all(top == s or top in s.parents for s in scans):
            return root
        return self.config._single_board_path()

    @staticmethod
    def _type_folder(item_type: WorkItemType) -> str:
        """The folder name a type lives in: its plural (``features``, ``hypotheses``)."""
        irregular_plurals = {"hypothesis": "hypotheses", "literature": "literature"}
        return irregular_plurals.get(item_type.value, item_type.value + "s")

    def _scanned_type_dir(self, theme_path: str, scanned: list[Path], root: str | None) -> Path:
        """Place a theme's per-type directory where the board scans it (#102).

        Theme paths are written against the theme's default root (e.g.
        ``kanban-work/features/``). One that a scanned path contains is kept, which
        covers every board laid out like its theme, including a default ``init``
        (``root: work/`` but ``kanban-work/*`` scan paths). Otherwise its type
        folder moves under the board root (#113: its own named folder, never nested
        inside another type's), and ``scan()`` covers that folder, so a created item
        is never invisible to its board.
        """
        path = Path(theme_path)

        def is_scanned(p: Path) -> bool:
            return any(p == s or s in p.parents for s in scanned)

        if is_scanned(path):
            return _under(self.repo_root, path)
        base = Path(root or "work/")
        type_folder = Path(*path.parts[1:]) if len(path.parts) > 1 else path
        return _under(self.repo_root, base) / type_folder

    @staticmethod
    def _slugify(title: str) -> str:
        """Convert title to filename-safe slug."""
        slug = re.sub(r"[^a-zA-Z0-9\s-]", "", title)
        slug = re.sub(r"\s+", "-", slug.strip())
        return slug[:50]  # Limit length

    def create_item(
        self,
        item_type: WorkItemType,
        title: str,
        path: Path | None = None,
        priority: str = "medium",
        assignee: str | None = None,
        description: str | None = None,
        tags: list[str] | None = None,
        content: str | None = None,
        item_id: str | None = None,
    ) -> WorkItem:
        """Create a new work item.

        Args:
            content: Pre-rendered file content (e.g., from TemplateEngine).
                     When provided, writes this instead of item.to_markdown().
            item_id: Explicit ID to use instead of auto-allocating.
                     Useful for HDD types with non-standard ID formats
                     (e.g., H130.1, EXPR-130, PAPER-130).
        """
        priority = self._normalize_priority(priority) or "medium"
        self._check_text(
            title=title, description=description, assignee=assignee, tags=tags, content=content
        )
        self._check_no_comments_heading(description)
        self._check_no_comments_heading(content, rendered=True)  # templated (#666)
        # Generate or use provided ID
        if item_id is None:
            prefix = self._get_type_prefix(item_type)
            item_id = self._format_id(prefix, self._get_next_id_number(prefix))

        # Determine file path
        if path is None:
            type_dir = self._get_type_directory(item_type)
            slug = self._slugify(title)
            filename = f"{item_id}-{slug}.md" if slug else f"{item_id}.md"
            path = type_dir / filename

        # Ensure directory exists
        path.parent.mkdir(parents=True, exist_ok=True)

        # Create work item
        item = WorkItem(
            id=item_id,
            title=title,
            item_type=item_type,
            status=WorkItemStatus.BACKLOG,
            file_path=path,
            priority=priority,
            assignee=assignee,
            created=date.today(),
            description=description,
            tags=tags or [],
        )

        # Write file — use pre-rendered content if provided
        if content is not None:
            file_content = self._apply_priority(content, priority)
        else:
            file_content = item.to_markdown()
        # the file says the theme's own initial status (hdd `draft`), as move writes
        # it; it still scans as backlog (#439)
        native = self._item_reverse_status_mapping(item).get(item.status.value)
        if native and native != item.status.value:
            file_content = self._add_or_update_frontmatter_field(file_content, "status", native)
        path.write_text(file_content)

        # Parse RDF graph from written content
        item.graph = self._parse_graph(file_content)

        # Add to cache
        self._index_item(item)  # the folded index too (#741)

        # Fire hooks (after successful create)
        self._fire_create_hook(item)

        return item

    def _has_remote(self) -> bool:
        """Check if git remote 'origin' is configured."""
        try:
            return "origin" in self._git_run("remote").stdout.split()
        except Exception:
            return False

    def create_item_and_push(
        self,
        item_type: WorkItemType,
        title: str,
        priority: str = "medium",
        assignee: str | None = None,
        description: str | None = None,
        tags: list[str] | None = None,
        max_retries: int = 3,
        content: str | None = None,
        item_id: str | None = None,
        render: Callable[[str], str] | None = None,
        id_prefix: str | None = None,
        parent: str | None = None,
    ) -> dict[str, Any]:
        """Atomically create a work item, commit, and push to remote.

        Single-command flow that prevents ID conflicts:
        1. Pull latest from remote (if remote exists)
        2. Scan for highest ID
        3. Create item file + update _ID_ALLOCATIONS.json
        4. Commit both files
        5. Push to remote (if remote exists)
        6. On push failure: reset, pull --rebase, retry with new ID

        If no remote is configured, gracefully degrades to local
        allocate + create + commit (no push, no retry needed).

        Args:
            content: Pre-rendered file content (e.g., from TemplateEngine).
            item_id: Explicit ID (bypasses auto-allocation). For HDD types
                     with non-standard formats (H130.1, EXPR-130, PAPER-130).
            render: Renders the file content for an allocated id. When given,
                    `item_id` (if any) is only the local scan's guess: with a
                    remote the id is allocated again against each fetched base,
                    and the content rendered for it (#590).
            id_prefix: The id prefix to allocate in, when it is not the type's own
                       (IDEA-R, IDEA-F).
            parent: The id of an HDD parent to link the item from (its turtle
                    block's inverse reference). With a remote the link is made to
                    the parent as the fetched base holds it, in the item's own
                    commit; a parent this board has but the base lacks is refused
                    (#645).

        Returns:
            dict with 'success', 'item', 'id', 'pushed', 'parent_linked' and
            'message' keys
        """
        priority = self._normalize_priority(priority) or "medium"
        prefix = id_prefix or self._get_type_prefix(item_type)
        item_id_local = item_id
        if render is not None:
            # rendered for the id the local scan picks; with a remote, again per base
            item_id_local = item_id or self._format_id(prefix, self._get_next_id_number(prefix))
            content = render(item_id_local)
            item_id = None
        self._check_text(
            title=title, description=description, assignee=assignee, tags=tags, content=content
        )
        self._check_no_comments_heading(description)
        self._check_no_comments_heading(content, rendered=True)  # templated (#666)
        # a duplicated parent: which copy gets the link is ambiguous; refused before
        # anything is written, on every path (#754)
        if parent is not None and (held := self.get_item(parent)) is not None:
            try:
                self.refuse_duplicate(held, "a parent link")
            except ValueError as e:
                return self._push_failed(f"{e}; nothing was created")

        # A board outside the git repository can't be committed or pushed (#174):
        # create the item and stop, before any pull or ID-allocation record
        if self._outside_repo(self._get_type_directory(item_type)):
            item = self.create_item(
                item_type,
                title,
                priority=priority,
                assignee=assignee,
                description=description,
                tags=tags,
                content=content,
                item_id=item_id_local,
            )
            parent_state = None if parent is None else self.link_parent(
                parent, item_type.value, item.id
            )
            linked = parent_state == "added"
            return {
                "success": True,
                "item": item,
                "id": item.id,
                "pushed": False,
                "committed": False,
                "parent_linked": linked,
                "parent_state": None if linked else parent_state,  # parsed once (#750)
                "message": (
                    f"Created {item.id}; not committed: the board is outside "
                    "the git repository"
                ),
            }

        # the allocation record names who allocated: no actor, nothing is written (#620)
        try:
            actor = resolve_actor(None, cwd=self.repo_root)
        except InputRefused as e:
            return self._push_failed(str(e))

        if self._has_remote():
            return self._create_on_default_branch(
                item_type,
                title,
                priority=priority,
                assignee=assignee,
                description=description,
                tags=tags,
                max_retries=max_retries,
                content=content,
                item_id=item_id,
                actor=actor,
                render=render,
                id_prefix=id_prefix,
                parent=parent,
            )

        # No remote: allocate, create and commit locally (no push, no retry needed)
        self._items.clear()
        self.scan()
        current_id = item_id or self._format_id(prefix, self._get_next_id_number(prefix))
        if render is not None and current_id != item_id_local:
            content = render(current_id)
            self._check_rendered(content)
        item, text = self._new_item(
            item_type, title, current_id, priority, assignee, description, tags, content
        )
        # the parent's link, worked out before anything is written (#674)
        edit, parent_state = (None, None) if parent is None else self._parent_link_edit(
            parent, item_type.value, current_id
        )
        in_commit = edit is not None and not self._outside_repo(edit[0].file_path)
        state = self._git_state(edit[0].file_path) if in_commit else None
        if state is not None:
            shown = self._repo_relative(edit[0].file_path, self._git_toplevel())
            first = (
                f"{parent} is not committed: commit {shown}"
                if state == "untracked"
                else f"{parent} has uncommitted edits: commit or stash your edit to {shown}"
            )  # a file git never tracked has no "edits" (#693)
            return self._push_failed(
                f"{first} first, so the link's commit holds only the link; nothing was created"
            )
        # a corrupt allocation file is refused before the item is written (#818)
        lock_file = self.repo_root / ".kanban" / "_ID_ALLOCATIONS.json"
        allocations = self._local_allocations(lock_file)
        file_path = item.file_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(text)

        lock_file.parent.mkdir(parents=True, exist_ok=True)
        lock_file.write_text(self._with_allocation(allocations, current_id, actor))

        paths = [file_path, lock_file]
        linked = edit is not None
        if edit is not None:
            self._apply_parent_link(*edit)
            if in_commit:
                paths.append(edit[0].file_path)  # the link in the item's own commit (#645)
        try:
            self._commit_paths(paths, f"Create {current_id}: {title}")
        except GitCommitError as e:
            return self._push_failed(str(e))

        self._index_item(item)  # the folded index too (#741, #751)
        self._fire_create_hook(item)
        return {
            "success": True,
            "item": item,
            "id": current_id,
            "pushed": False,
            "parent_linked": linked,
            "parent_state": parent_state,  # why no link, parsed once (#750)
            "message": f"Created and committed {current_id}: {title} (no remote configured)",
        }

    def _create_on_default_branch(
        self,
        item_type: WorkItemType,
        title: str,
        priority: str,
        assignee: str | None,
        description: str | None,
        tags: list[str] | None,
        max_retries: int,
        content: str | None,
        item_id: str | None,
        actor: str,
        render: Callable[[str], str] | None = None,
        id_prefix: str | None = None,
        parent: str | None = None,
    ) -> dict[str, Any]:
        """`create --push` with a remote (#585): fetch and push ONE explicit ref, the
        remote's default branch. The commit is built on the freshly fetched
        `origin/<default>` in a temporary index, so the user's worktree, index and
        branch are never touched while it races; a lost race refetches and retries
        with a new id, and a failure of any kind leaves nothing behind. Only when the
        push has landed, and HEAD is on the default branch, is the checkout
        fast-forwarded to it. Unless `item_id` is explicit, the id is allocated
        against each fetched base (#590), and `render` (when given) renders the
        item's text for that id, checked like any other text (#641); an explicit
        `item_id` that a fetched base already holds, by the same number in the same
        id space, is refused, naming the file that holds it (#634, #641). A `parent`'s
        inverse reference is added to the parent as that base holds it, in the
        same commit, so a lost race rebuilds it on the new base (#645)."""
        made: dict[str, Any] = {}

        def build(base: str) -> tuple[dict[Path, str], str]:
            # `base`'s own config allocates, places and finds the parent (#865)
            try:
                judge = self._judge_at(base)
            except _TreeUnreadableError as e:
                raise _CasRefusedError(f"{e}; nothing was created") from None
            prefix = id_prefix or judge._get_type_prefix(item_type)
            if item_id is not None and (rival := judge._holder_at(base, item_id)):
                raise _CasRefusedError(
                    f"{item_id} is already taken on the default branch by {rival}; "
                    "nothing was created"
                )
            current_id = item_id or self._next_id_at(base, prefix)
            text_in = render(current_id) if render is not None else content
            if render is not None:
                try:
                    self._check_rendered(text_in)
                except InputRefused as e:
                    raise _CasRefusedError(f"{e}; nothing was created") from None
            item, text = judge._new_item(
                item_type, title, current_id, priority, assignee, description, tags, text_in
            )
            item_rel = self._repo_relative(item.file_path, self._git_toplevel())
            if item_rel is None:
                raise _CasRefusedError(
                    f"{item.file_path} is outside the git repository at {self._git_toplevel()}"
                )
            # whatever the ids say, never replace a file the base already has there,
            # nor sit beside one whose name differs only in case: on a
            # case-insensitive filesystem they are one file (#788). One listing of
            # the whole tree serves this and the folder check below (#903); a twin
            # in a folder spelled another way is the folder check's to name.
            listed = self._tree_names(base)
            folder = item_rel.parent.as_posix()
            folder = "" if folder == "." else folder
            twin = next(
                (
                    p for p in listed
                    if p.rpartition("/")[0] == folder
                    and _twin_key(p) == _twin_key(item_rel.as_posix())
                ),
                None,
            )
            if twin is not None:
                raise _CasRefusedError(
                    f"{twin} already exists on the default branch (creating "
                    f"{current_id} as {item_rel.as_posix()} would replace it); "
                    "nothing was created"
                )
            blobs = {item_rel: text, **self._allocation_blob(base, current_id, actor)}
            # and no folder on a written path may be another folder spelled in
            # another case: a case-insensitive filesystem merges them (#834)
            twin = self._folder_twin_refusal(
                listed, list(blobs), f"creating {current_id}", nothing="created"
            )
            if twin is not None:
                raise _CasRefusedError(twin)
            linked, parent_state = ({}, None) if parent is None else judge._parent_link_blob(
                base, parent, item_type.value, current_id
            )
            made.update(
                item=item, id=current_id, parent_linked=bool(linked), linked=linked,
                parent_state=parent_state,
            )
            return {**blobs, **linked}, f"Create {current_id}: {title}"

        def landed(branch: str, local: bool) -> dict[str, Any]:
            item, current_id = made["item"], made["id"]
            if local:
                self._index_item(item)  # the folded index too (#741, #751)
            self._fire_create_hook(item)
            # a parent edited here blocks the pull that brings its link (#674)
            top = self._git_toplevel()
            dirty = None if local else next(
                (rel.as_posix() for rel in made["linked"] if self._uncommitted(top / rel)),
                None,
            )
            message = _created_and_pushed_message(
                current_id, branch, title, local=local, dirty=dirty
            )
            return {
                "success": True,
                "item": item,
                "id": current_id,
                "pushed": True,
                "local": local,
                "branch": branch,
                "parent_linked": made["parent_linked"],
                # why no link, from the copy it was built against (#724)
                "parent_state": made.get("parent_state"),
                "dirty_parent": dirty,
                "message": message,
            }

        return self._cas_on_default_branch(build, landed, max_retries, "item")

    def _cas_on_default_branch(
        self,
        build: Callable[[str], tuple[dict[Path, str], str]],
        landed: Callable[[str, bool], dict[str, Any]],
        max_retries: int,
        what: str,
    ) -> dict[str, Any]:
        """Compare-and-swap one commit onto the remote's default branch (#585, #590).

        Each attempt fetches `origin/<default>`, calls `build(base)` for the files to
        write (repo-relative path -> text) and the commit message, commits them on
        `base` in a temporary index (the user's worktree, index and branch are never
        touched) and pushes `<sha>:refs/heads/<default>`. Only a lost race (a
        non-fast-forward rejection) is retried, on a fresh base; any other refusal,
        an unreachable remote or a timeout fails at once with git's own words and
        leaves nothing behind. Once the push lands, a checkout on the default branch
        is fast-forwarded and `landed(branch, local)` makes the result."""
        failed = self._push_failed
        branch = "main"
        try:
            branch, known = self._resolve_default()
            return self._race_to_branch(branch, build, landed, max_retries, what, known=known)
        except _CasRefusedError as e:
            return failed(str(e))
        except subprocess.TimeoutExpired as e:
            if "push" in [str(a) for a in (e.cmd or [])]:
                return failed(
                    f"Timed out pushing to origin/{branch}: the push may have landed. "
                    f"Check origin/{branch} (git fetch origin, then look for the {what}) "
                    "before creating it again"
                )
            return failed(
                f"Timed out talking to origin ({' '.join(map(str, e.cmd or []))}); "
                "nothing was created"
            )
        except OSError as e:
            return failed(f"Could not run git for origin/{branch}: {e}; nothing was created")

    @staticmethod
    def _push_failed(message: str) -> dict[str, Any]:
        """The result of a `create --push` that created nothing. Git's multi-line
        stderr is folded onto one line, so no caller prints a literal `\\n` (#603)."""
        return {
            "success": False, "item": None, "id": None, "pushed": False,
            "message": " ".join(message.split()),
        }

    def _default_branch(self) -> str:
        """The remote's default branch (see `_resolve_default`)."""
        return self._resolve_default()[0]

    def _resolve_default(self) -> tuple[str, bool]:
        """(the remote's default branch, whether it is known): `origin/HEAD` when set
        locally, else what the remote itself says (`ls-remote --symref`); else a
        guess of `main`, which is never recorded as origin/HEAD (#585, #685, #698)."""
        known = self._git_run("symbolic-ref", "--short", "refs/remotes/origin/HEAD")
        if known.returncode == 0 and known.stdout.strip().startswith("origin/"):
            return known.stdout.strip().removeprefix("origin/"), True
        asked = self._git_run("ls-remote", "--symref", "origin", "HEAD")
        match = re.search(r"^ref: refs/heads/(\S+)\s+HEAD$", asked.stdout, re.M)
        return (match.group(1), True) if match else ("main", False)

    def _fetch_default(
        self, branch: str, *, record: bool
    ) -> subprocess.CompletedProcess[str]:
        """Fetch exactly `origin/<branch>` (#585); when `record` (the branch is known,
        not guessed), also record it locally as `origin/HEAD`, as `git clone` does,
        so the offline `_fetched_default` finds it (#661, #698)."""
        fetch = self._git_run(
            "fetch", "origin", f"+refs/heads/{branch}:refs/remotes/origin/{branch}"
        )
        if fetch.returncode == 0 and record:
            ref = f"refs/remotes/origin/{branch}"
            known = self._git_run("symbolic-ref", "-q", "refs/remotes/origin/HEAD")
            if known.returncode != 0 or known.stdout.strip() != ref:
                self._git_run("symbolic-ref", "refs/remotes/origin/HEAD", ref)
        return fetch

    def _race_to_branch(
        self,
        branch: str,
        build: Callable[[str], tuple[dict[Path, str], str]],
        landed: Callable[[str, bool], dict[str, Any]],
        max_retries: int,
        what: str,
        *,
        known: bool,
    ) -> dict[str, Any]:
        """The fetch / build / push loop of `_cas_on_default_branch`; `known` says
        whether `branch` may be recorded as origin/HEAD (#698)."""
        failed = self._push_failed
        attempts = max(1, max_retries)
        last_err = ""
        for attempt in range(attempts):
            fetch = self._fetch_default(branch, record=known)
            if fetch.returncode != 0:
                return failed(
                    f"Could not fetch origin/{branch} from the remote: "
                    f"{fetch.stderr.strip()} (nothing was created)"
                )
            base = self._git_run("rev-parse", f"refs/remotes/origin/{branch}").stdout.strip()

            self._items.clear()
            self.scan()
            blobs, message = build(base)

            sha, error = self._commit_on(
                base, blobs, message, refusal_suffix="(nothing was created)"
            )
            if sha is None:
                return failed(error or "Git commit failed")

            push = self._git_run("push", "origin", f"{sha}:refs/heads/{branch}")
            if push.returncode != 0:
                err = last_err = push.stderr.strip()
                if "[rejected]" not in err or not (
                    "fetch first" in err or "non-fast-forward" in err
                ):
                    return failed(
                        f"The remote refused the push to origin/{branch}: {err} "
                        "(nothing was created)"
                    )
                logger.warning(
                    f"Push to origin/{branch} rejected (attempt {attempt + 1}): {err}"
                )
                continue

            # It has landed: from here on nothing may turn this into a failure (#603)
            return landed(branch, self._fast_forward_to(branch, sha))

        # Best effort: leave origin/<default> showing the commits that beat us
        with suppress(subprocess.TimeoutExpired, OSError):
            self._fetch_default(branch, record=known)
        return failed(
            f"Failed to create the {what}: the push to origin/{branch} was rejected "
            f"{attempts} time(s) (lost the race to another writer each attempt); "
            f"nothing was left behind — retry. Last rejection: {last_err}"
        )

    def _commit_on(
        self,
        base: str,
        blobs: dict[Path, str],
        message: str,
        *,
        refusal_suffix: str,
        skip_empty: bool = False,
    ) -> tuple[str | None, str | None]:
        """Build one commit on `base` writing `blobs` (repo-relative path -> text,
        its bytes exactly), without touching the worktree, the index or any ref
        (#585, #574): a temporary index, the user's pre-commit hook run against it
        (#584), then `commit-tree -p base`. Returns (sha, None), or (None, why), why
        ending in `refusal_suffix` when the hook refused (#805). With `skip_empty`,
        a tree equal to `base`'s makes no commit and runs no hook: (base, None)."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "GIT_INDEX_FILE": str(Path(tmp) / "index")}
            steps = [self._git_run("read-tree", base, env=env)]
            for n, (rel, body) in enumerate(blobs.items()):
                # from a file, not stdin: git never reads stdin (#580);
                # --no-filters hashes the bytes exactly as --stdin did
                body_path = Path(tmp) / f"blob-{n}"
                body_path.write_bytes(body.encode("utf-8"))
                blob = self._git_run(
                    "hash-object", "-w", "--no-filters", "--", str(body_path)
                )
                steps += [blob, self._git_run(
                    "update-index", "--add", "--cacheinfo",
                    f"100644,{blob.stdout.strip()},{rel.as_posix()}", env=env,
                )]
            tree = self._git_run("write-tree", env=env)
            steps.append(tree)
            if skip_empty and all(s.returncode == 0 for s in steps):
                was = self._git_run("rev-parse", f"{base}^{{tree}}")
                if was.returncode == 0 and was.stdout.strip() == tree.stdout.strip():
                    return base, None  # the base already holds it (#805)
            # commit-tree runs no hooks: run pre-commit against this index (#584)
            if all(s.returncode == 0 for s in steps):
                hook = self._git_run(
                    "hook", "run", "--ignore-missing", "pre-commit", env=env, timeout=None,
                    user_locale=True,  # its output is only shown (#826)
                )
                if hook.returncode != 0:
                    return None, (
                        f"Git commit failed: the pre-commit hook refused it: "
                        f"{self._git_output(hook)} {refusal_suffix}"
                    )
            commit = self._git_run(
                "commit-tree", tree.stdout.strip(), "-p", base, "-m", message
            )
            steps.append(commit)
        bad = next((s for s in steps if s.returncode != 0), None)
        if bad is not None:
            return None, f"Git commit failed: {bad.stderr.strip()}"
        return commit.stdout.strip(), None

    def _fast_forward_to(self, branch: str, sha: str) -> bool:
        """After a push of `sha` to origin/`branch` has landed: fast-forward the
        checkout when it is on `branch`. Never fails (#603): a checkout that can't
        be fast-forwarded just isn't updated, and False says so."""
        try:
            head = self._git_run("symbolic-ref", "--quiet", "--short", "HEAD")
            return head.stdout.strip() == branch and (
                self._git_run("merge", "--ff-only", "--quiet", sha).returncode == 0
            )
        except (subprocess.TimeoutExpired, OSError) as e:
            logger.warning(f"Pushed {sha[:12]}, but the local checkout was not updated: {e}")
            return False

    def sync_and_push(
        self,
        mutate: Mutate,
        *,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[float, float], float] = random.uniform,
        seam: Callable[[int], None] | None = None,
        attempts: int = 5,
    ) -> Outcome:
        """The one kanban push (#574): a compare-and-swap of one commit onto
        origin's default branch that never touches the worktree, index or branch.

        Each attempt fetches `origin/<default>` and calls `mutate(read, attempt)`,
        where `read(path)` is that base's LF text (None when absent). A `Change` is
        committed on the base in a temporary index (`_commit_on`), `seam(attempt)`
        runs, and the commit is pushed. A non-fast-forward rejection sleeps
        `jitter(0.1, 1.0) * (attempt + 1)` and retries on a fresh base; after
        `attempts` it is "busy". A `Refuse` with a holder after such a rejection is
        "lost". Any other refusal is "push_refused" and a remote that can't be
        reached "unreachable", neither retried. A `Change` whose tree equals the
        base's is "noop" (#805). With no `origin`, the change is made and committed
        here only ("local"). So is a `Change` naming a file outside the repository,
        decided per Change before origin is contacted (#805): when a board lies
        outside, `mutate` is first asked against the working tree, and when it read
        or wrote a file outside, that answer (Change, NoOp or Refuse) stands, made
        here without touching origin; otherwise the compare-and-swap asks again
        against origin's tree (mutate is pure), so an in-repo Change keeps it.
        So a `mutate` that reads a file on an external board, even one that turns
        out missing, makes an in-repo write go local (#833): read external items
        from the working tree directly, as `_dependency_board_at` does."""
        if not self._has_remote():
            return self._sync_locally(mutate)
        if self._board_outside_repo():
            asked, eols_here, read_here = self._ask_working_tree(mutate)
            wrote = asked.files if isinstance(asked, Change) else {}
            if self._any_outside([*read_here, *wrote]):
                # about an external item: the working tree's answer, whatever it is
                return self._write_locally(asked, eols_here)
        branch = "main"
        rejected = False
        attempts = max(1, attempts)
        tried = 1  # the attempt under way, for an outcome raised mid-attempt (#805)
        try:
            branch, known = self._resolve_default()
            for attempt in range(attempts):
                tried = attempt + 1
                fetch = self._fetch_default(branch, record=known)
                if fetch.returncode != 0:
                    said = " ".join(self._git_output(fetch).split())
                    return Outcome(
                        "unreachable",
                        f"Could not fetch origin/{branch}: {said}; nothing was changed",
                        attempts=attempt + 1,
                    )
                base = self._git_run(
                    "rev-parse", f"refs/remotes/origin/{branch}"
                ).stdout.strip()
                eols: dict[str, LineEndings] = {}
                result = mutate(self._reader_at(base, eols), attempt)
                if not isinstance(result, Change):
                    return self._not_changed(result, rejected, attempt + 1)
                blobs = {
                    Path(rel): eols[rel].apply(text) if rel in eols else text
                    for rel, text in result.files.items()
                }
                sha, error = self._commit_on(
                    base, blobs, result.message,
                    refusal_suffix="(nothing was changed)", skip_empty=True,
                )
                if sha is None:
                    return Outcome("refused", error or "Git commit failed", attempts=attempt + 1)
                if sha == base:
                    return Outcome(
                        "noop",
                        f"{result.message}: origin/{branch} already holds it; nothing to push",
                        attempts=attempt + 1, data=result.data,
                    )
                if seam is not None:
                    seam(attempt)
                push = self._git_run("push", "origin", f"{sha}:refs/heads/{branch}")
                if push.returncode == 0:
                    return self._won(branch, sha, result, attempt + 1)
                err = self._git_output(push)
                if "[rejected]" in err and ("fetch first" in err or "non-fast-forward" in err):
                    rejected = True
                    logger.warning(
                        f"Push to origin/{branch} rejected (attempt {attempt + 1}): {err}"
                    )
                    if attempt + 1 < attempts:
                        sleep(jitter(0.1, 1.0) * (attempt + 1))
                    continue
                if any(
                    mark in err
                    for mark in ("[remote rejected]", "[rejected]", "hook declined",
                                 "protected", "denied")
                ):
                    return Outcome(
                        "push_refused",
                        f"The remote refused the push to origin/{branch}: "
                        f"{' '.join(err.split())}; nothing was changed",
                        attempts=attempt + 1,
                    )
                return Outcome(
                    "unreachable",
                    f"Could not push to origin/{branch}: {' '.join(err.split())}; "
                    "nothing was changed",
                    attempts=attempt + 1,
                )
        except subprocess.TimeoutExpired as e:
            if "push" in [str(a) for a in (e.cmd or [])]:
                return Outcome(
                    "unreachable",
                    f"Timed out pushing to origin/{branch}: the push may have landed. "
                    f"Fetch origin/{branch} and check before trying again",
                    attempts=tried,
                )
            return Outcome(
                "unreachable",
                f"Timed out talking to origin ({' '.join(map(str, e.cmd or []))}); "
                "nothing was changed",
                attempts=tried,
            )
        except OSError as e:
            return Outcome(
                "unreachable",
                f"Could not run git for origin/{branch}: {e}; nothing was changed",
                attempts=tried,
            )
        with suppress(subprocess.TimeoutExpired, OSError):
            self._fetch_default(branch, record=known)  # show what beat us
        return Outcome(
            "busy",
            f"origin/{branch} is busy: the push was rejected {attempts} time(s), other "
            "commits landing each time; nothing was changed — retry",
            attempts=attempts,
        )

    def _reader_at(self, base: str, eols: dict[str, LineEndings]) -> Read:
        """`read(path)` for `mutate`: the file as commit `base` holds it, as LF text,
        or None when it doesn't; its line endings go into `eols`, so the write keeps
        them (#128, #574)."""

        def read(rel: str) -> str | None:
            # the blob's own bytes: text mode would turn a CRLF file into LF (#128)
            shown = subprocess.run(
                ["git", "cat-file", "blob", f"{base}:{Path(rel).as_posix()}"],
                cwd=self.repo_root,
                capture_output=True,
                timeout=30,
                stdin=subprocess.DEVNULL,
                env={**os.environ, **GIT_ENV},
            )
            if shown.returncode != 0:
                return None
            text, eols[rel] = LineEndings.read(shown.stdout.decode("utf-8"))
            return text

        return _Reader(base, read)

    @staticmethod
    def _not_changed(result: NoOp | Refuse, rejected: bool, attempts: int) -> Outcome:
        """The outcome of a `NoOp` or `Refuse` from `mutate` (#574): a refusal with a
        holder after a rejected push lost the race to that holder."""
        if isinstance(result, NoOp):
            return Outcome("noop", result.message, attempts=attempts)
        if rejected and result.holder:
            return Outcome(
                "lost", f"Lost to {result.holder}: {result.message}", attempts=attempts
            )
        return Outcome("refused", result.message, attempts=attempts)

    def _won(self, branch: str, sha: str, change: Change, attempts: int) -> Outcome:
        """The outcome of a push that landed; nothing here may turn it into a
        failure (#603)."""
        message = f"{change.message}: pushed to origin/{branch}"
        if not self._fast_forward_to(branch, sha):
            message += (
                f". Your checkout does not show this yet: pull {branch}, and start "
                f"feature branches from origin/{branch}"
            )
        return Outcome("won", message, sha=sha, attempts=attempts, data=change.data)

    def _board_outside_repo(self) -> bool:
        """True when a board root lies outside the git repository, so its files
        can't be pushed (#174, #574)."""
        if self.config.is_multi_board:
            roots = [_under(self.repo_root, b.path) for b in self.config.boards]
        else:
            roots = list(self._placement_dirs())
        return any(self._outside_git(r) for r in roots)

    def _sync_locally(self, mutate: Mutate) -> Outcome:
        """`sync_and_push` with no remote, or for a Change naming a file outside the
        repository (#574, #805): `read` is the working tree, and a `Change` is
        written, keeping line endings, and committed alone (#584). No commit made
        is "noop"; a refused commit keeps the edit in the working tree (#805)."""
        asked, eols, _ = self._ask_working_tree(mutate)
        return self._write_locally(asked, eols)

    def _ask_working_tree(
        self, mutate: Mutate
    ) -> tuple[Change | NoOp | Refuse, dict[str, LineEndings], list[str]]:
        """`mutate(read, 0)` with `read` the working tree (rev None); the line
        endings of each file it read, for `_write_locally`; and every path it asked
        for, found or not (#574, #805)."""
        top = self._git_toplevel()
        eols: dict[str, LineEndings] = {}
        asked: list[str] = []

        def read(rel: str) -> str | None:
            asked.append(rel)
            path = top / rel
            if not path.is_file():
                return None
            text, eols[rel] = self._read_item_text(path)
            return text

        return mutate(_Reader(None, read), 0), eols, asked

    def _any_outside(self, rels: list[str]) -> bool:
        """True when any of `rels` (relative to the work tree) lies outside the git
        repository, which origin can't hold (#805)."""
        top = self._git_toplevel()
        return any(self._outside_git(top / rel) for rel in rels)

    def _write_locally(
        self, result: Change | NoOp | Refuse, eols: dict[str, LineEndings]
    ) -> Outcome:
        """The local outcome of `result`, a working-tree answer from
        `_ask_working_tree` (#574, #805)."""
        top = self._git_toplevel()
        if not isinstance(result, Change):
            return self._not_changed(result, False, 1)
        paths = []
        for rel, text in result.files.items():
            path = top / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            self._write_item_text(path, text, eols.get(rel, "\n"))
            paths.append(path)
        if self._outside_repo(*paths):
            return Outcome(
                "local",
                f"{result.message}: written, not committed: outside the git repository",
                attempts=1, data=result.data,
            )
        try:
            made = self._commit_paths(paths, result.message)
        except GitCommitError as e:
            return Outcome(
                "refused",
                f"{e} (the edit is kept in the working tree, uncommitted)",
                attempts=1,
            )
        if not made:
            return Outcome(
                "noop", f"{result.message}: the working tree already holds it",
                attempts=1, data=result.data,
            )
        sha = self._git_run("rev-parse", "HEAD").stdout.strip()
        return Outcome(
            "local", f"{result.message}: no remote: committed here, local only",
            sha=sha, attempts=1, data=result.data,
        )

    def _next_id_at(self, base: str, prefix: str) -> str:
        """The next id in `prefix`'s space: past the scanned board and past what
        commit `base` holds (#590, #634), at the board paths `base`'s own config
        names (`_judge_at`, #865). Raises `_CasRefusedError` when that config
        doesn't load."""
        try:
            judge = self._judge_at(base)
        except _TreeUnreadableError as e:
            raise _CasRefusedError(f"{e}; nothing was created") from None
        num = max(self._scanned_next_id_number(prefix), judge._next_id_number_at(base, prefix))
        return self._format_id(prefix, num)

    @staticmethod
    def _id_sep(prefix: str) -> str:
        """What follows `prefix` in its ids: nothing for a paper-scoped `H130.`
        (`H130.2`), else a dash (`EXP-007`) (#634)."""
        return "" if prefix.endswith(".") else "-"

    @classmethod
    def _format_id(cls, prefix: str, num: int) -> str:
        """Id number `num` in `prefix`'s space: `H130.2`, or `EXP-007` (#634)."""
        return f"{prefix}-{num:03d}" if cls._id_sep(prefix) else f"{prefix}{num}"

    @staticmethod
    def _id_space(item_id: str) -> tuple[str, int] | None:
        """`item_id` as (id space, number): the id minus its trailing number, less
        a dash (`EXP-003` -> (`EXP`, 3), `IDEA-R-004` -> (`IDEA-R`, 4), `H130.2` ->
        (`H130.`, 2)), or None when it ends in no number (#641)."""
        match = re.fullmatch(r"(.*?)(\d+)", item_id)
        if match is None:
            return None
        return match.group(1).removesuffix("-"), int(match.group(2))

    @staticmethod
    def _id_key(item_id: str) -> tuple[str, int] | None:
        """`item_id` as (the text before its trailing number, separator included,
        number), for comparing ids: `EXP-3` and `EXP-003` are (`EXP-`, 3), `EXP3`
        is (`EXP`, 3), so they differ (#661). The text is folded (`fold_id`): `exp-3`
        is `EXP-3` (#732, #764, #817). None when it ends in no number."""
        match = re.fullmatch(r"(.*?)(\d+)", item_id)
        return None if match is None else (fold_id(match.group(1)), int(match.group(2)))

    @staticmethod
    def _stem_holds(stem: str, key: tuple[str, int]) -> bool:
        """Whether a filename stem starts with the id `key` names (`_id_key`):
        its text, then its number, then no more of an id (`EXP-003-Title` and
        `EXP-003.v2` hold (`EXP-`, 3); `H1.2-Title`, a paper-scoped id, does not hold
        (`H`, 1)) (#661, #685)."""
        text, num = key  # `_id_key` folds the text; so is the stem's head (#764, #817)
        rest = KanbanService._stem_rest(stem, text)
        match = re.match(r"(\d+)(?!\d|\.\d)", rest) if rest is not None else None
        return match is not None and int(match.group(1)) == num

    @classmethod
    def _stem_id(cls, stem: str, prefix: str) -> int | None:
        """The id number a filename stem starts with in `prefix`'s space
        (`EXP-608-Some-Title` -> 608), or None; case folded, `exp-608-…` too (#752)."""
        rest = cls._stem_rest(stem, prefix + cls._id_sep(prefix))
        match = re.match(r"(\d+)", rest) if rest is not None else None
        return int(match.group(1)) if match else None

    @staticmethod
    def _stem_rest(stem: str, head: str) -> str | None:
        """What follows `head` in a filename stem that starts with it, folded
        (`fold_id`), else None (#765, #775, #817). Both sides are compared
        decomposed (NFD), so the stem's own head is the slice as long as the folded
        `head`: every spelling of `ΐX-` (NFC, decomposed, upper-cased) lines up with
        `Ϊ́X-`, while a letter whose upper case is more letters never does, `ß-12`
        holding no `SS-` id (#775, #792). Folding never changes an ASCII digit."""
        want = fold_id(head)
        decomposed, width = (
            unicodedata.normalize("NFD", stem), len(unicodedata.normalize("NFD", want))
        )
        return decomposed[width:] if fold_id(decomposed[:width]) == want else None

    def _holder_at(self, rev: str, item_id: str) -> str | None:
        """The file under the work paths at commit `rev` that holds `item_id`, or
        None (#634). Ids are the same when the text before their number, separator
        included, and the number are: `EXP-3` is `EXP-003` (#641), but `EXP3` is not
        (#661). The file whose frontmatter `id:` is the ID wins; a filename counts
        only for a file with no `id:` of its own, since the board names an item by
        its `id:` and never by a lookalike outline or another item's file (#788)."""
        names, ids = self._ids_at(rev)
        holders = self._holders_at(rev, item_id, ids)
        if holders:
            return holders[0]
        key = self._id_key(item_id)
        with_id = {path for path, _ in ids}
        for name in names:
            if name in with_id:
                continue  # it holds its own `id:` only (#788)
            # the stem's head folded, `m-042` holds `M-042` (#732, #764, #817); only
            # what lines up with the ID: `ß` is no `SS` (#775, #792)
            stem = Path(name).stem
            rest = self._stem_rest(stem, item_id)
            if (rest is not None and rest[:1] in ("", "-")) or (
                key is not None and self._stem_holds(stem, key)
            ):
                return name
        return None

    def _holders_at(
        self,
        rev: str,
        item_id: str,
        ids: list[tuple[str, str]] | None = None,
        *,
        loaded: bool = False,
    ) -> list[str]:
        """The files at commit `rev` whose frontmatter `id:` is `item_id` (case
        folded, `EXP-3` is `EXP-003`). A filename is not an id: a file without one
        is `STEM_WITH_UNDERSCORES` to the board, so an outline or draft named after
        an item is no copy of it. More than one is a duplicated ID there, as
        `duplicate_ids` counts it (#754). `loaded`: only those the board would
        load, its ignore patterns applied as a scan applies them (#856) — for the
        item acted on; the id space (allocation, collisions) sees every file."""
        if ids is None:
            ids = self._ids_at(rev)[1]
        key = self._id_key(item_id)
        folded = fold_id(item_id)
        return list(dict.fromkeys(
            path for path, found in ids
            if (fold_id(found) == folded or (key is not None and self._id_key(found) == key))
            and (not loaded or self._board_loads(path))
        ))

    def _board_loads(self, rel: str) -> bool:
        """Whether a scan would load the file at `rel` (from the work tree's top)
        rather than ignore it (#856): on a multi-board config, a board whose root
        holds it and whose own `ignore` doesn't cover it; else a work path or
        placement dir that holds it (#919), and `paths.ignore`."""
        top = self._git_toplevel()
        path = top / rel
        if not self.config.is_multi_board:
            roots = [_under(self.repo_root, p) for p in self.config.get_work_paths()]
            roots += self._placement_dirs()
            heads = [
                head.as_posix() for root in roots
                if (head := self._repo_relative(root, top)) is not None
            ]
            return not self._should_ignore(path) and any(
                head in ("", ".") or rel.startswith(head.rstrip("/") + "/") for head in heads
            )
        for board in self.config.boards:
            root = self._repo_relative(_under(self.repo_root, board.path), top)
            if root is None:
                continue
            head = root.as_posix()
            if head not in ("", ".") and not rel.startswith(head.rstrip("/") + "/"):
                continue
            if not self._should_ignore_for_board(path, board):
                return True
        return False

    def _tree_names(self, rev: str) -> list[str]:
        """Every path in commit `rev`'s tree, folders (`-t`) as well as files, from
        the top."""
        listed = self._git_z("ls-tree", "-r", "-t", "-z", "--name-only", "--full-tree", rev)
        return [name for name in listed.stdout.split("\0") if name]

    @staticmethod
    def _folder_spellings(listed: list[str]) -> dict[str, set[str]]:
        """Every folder of `listed` (a tree's paths, `_tree_names`), grouped by
        `_twin_key`: the spellings a case-insensitive filesystem merges (#834,
        #869)."""
        folders: dict[str, set[str]] = {}
        for name in listed:
            parts = name.split("/")[:-1]
            for n in range(1, len(parts) + 1):
                folder = "/".join(parts[:n])
                folders.setdefault(_twin_key(folder), set()).add(folder)
        return folders

    @classmethod
    def _folder_case_twin(cls, listed: list[str], rels: list[Path]) -> str | None:
        """The folder in `listed`, in its own spelling, that one of `rels`' folders
        names in a different case, else None (#834)."""
        folders = cls._folder_spellings(listed)
        for rel in rels:
            parts = rel.as_posix().split("/")[:-1]
            for n in range(1, len(parts) + 1):
                folder = "/".join(parts[:n])
                others = sorted(folders.get(_twin_key(folder), set()) - {folder})
                if others:
                    return others[0]
        return None

    @classmethod
    def _folder_twin_refusal(
        cls, listed: list[str], rels: list[Path], doing: str, nothing: str = "changed"
    ) -> str | None:
        """Why writing `rels` onto the tree `listed` would leave folders a
        case-insensitive filesystem merges, else None (#834, #869, #870): a written
        folder spelled unlike the one the tree holds, or the tree already holding
        a folder in two spellings (then a write into either is refused). `doing`
        names the write ("creating EXP-042"); the message ends "nothing was
        `nothing`"."""
        folders = cls._folder_spellings(listed)
        for rel in rels:
            parts = rel.as_posix().split("/")[:-1]
            for n in range(1, len(parts) + 1):
                folder = "/".join(parts[:n])
                spellings = folders.get(_twin_key(folder), set())
                if len(spellings) > 1:
                    both = " and ".join(f"{f}/" for f in sorted(spellings))
                    return (
                        f"the default branch already has case-twin folders {both} "
                        f"(one folder on a case-insensitive filesystem): fix that "
                        f"before {doing}; nothing was {nothing}"
                    )
                if spellings and folder not in spellings:
                    existing = next(iter(spellings))
                    return (
                        f"{existing}/ is already on the default branch in that spelling "
                        f"({doing} would add a folder that differs only in case); "
                        f"nothing was {nothing}"
                    )
        return None

    def _ids_at(self, rev: str) -> tuple[list[str], list[tuple[str, str]]]:
        """The `.md` files under the work paths and placement dirs at commit `rev`
        (the roots a scan walks, as `_items_at` reads them, #954), and each `id:` in
        the leading frontmatter block of one as (path, id) (#590, #634); an `id:`
        line in the body or a code block is not an id (#641)."""
        top = self._git_toplevel()
        roots = [_under(self.repo_root, p) for p in self.config.get_work_paths()]
        roots += sorted(self._placement_dirs())
        rels = sorted({
            rel.as_posix() for root in roots
            if (rel := self._repo_relative(root, top)) is not None
        })
        if not rels:
            return [], []
        # -z: names raw, never quoted (a quoted `"d/\303\237.md"` isn't `.md`, #808)
        listed = self._git_z(
            "ls-tree", "-r", "-z", "--name-only", "--full-tree", rev, "--", *rels
        )
        names = [name for name in listed.stdout.split("\0") if name.endswith(".md")]
        # frontmatter ids too: `notes.md` may say `id: EXP-007` (#590)
        specs = [
            f":(top,glob){'' if rel in ('', '.') else rel.rstrip('/') + '/'}**/*.md"
            for rel in rels
        ]
        # the `---` lines too, numbered: a file's frontmatter is line 1's `---` up
        # to the next line starting with `---`, as the parser reads it
        grep = self._git_run(
            # --full-name: repo-rooted paths, as ls-tree gives, from a board in a
            # subdirectory too (#788)
            "grep", "-z", "-n", "-I", "--full-name", "-E", "^(---|id:[[:space:]])",
            rev, "--", *specs, text=False,
        )
        ids = []
        state: dict[str, bool] = {}  # path -> still inside its frontmatter
        # raw bytes, split on \n only: text mode turns a lone \r into \n, and
        # splitlines() breaks on \x85 and U+2028/9 too, which YAML reads as line
        # breaks but git grep does not (#830)
        records = grep.stdout.decode("utf-8", "surrogateescape").split("\n")
        for line in records:
            if not line:
                continue
            where, _, rest = line.partition("\0")
            number, _, text = rest.partition("\0")
            path = where.removeprefix(f"{rev}:")
            if path not in state:
                state[path] = number == "1" and text.startswith("---")
                continue
            if not state[path]:
                continue
            if text.startswith("---"):
                state[path] = False
            elif found := re.match(r"id:\s*[\"']?([^\"'\s]+)", text):
                ids.append((path, found.group(1)))
        return names, ids

    def _allocation_blob(self, base: str, current_id: str, actor: str) -> dict[Path, str]:
        """`_ID_ALLOCATIONS.json` as commit `base` has it, plus a record for
        `current_id` allocated by `actor`, keyed by its repo-relative path."""

        lock_rel = self._repo_relative(
            self.repo_root / ".kanban" / "_ID_ALLOCATIONS.json", self._git_toplevel()
        )
        if lock_rel is None:
            raise _CasRefusedError(f"{self.repo_root} is outside the git repository")
        shown = self._git_run("show", f"{base}:{lock_rel.as_posix()}")
        try:
            allocations = _parse_allocations(
                shown.stdout if shown.returncode == 0 else None,
                f"{lock_rel.as_posix()} on the default branch",
            )
        except InputRefused as e:
            raise _CasRefusedError(str(e)) from None  # nothing is pushed (#818)
        return {lock_rel: self._with_allocation(allocations, current_id, actor)}

    @staticmethod
    def _local_allocations(lock_file: Path) -> list[Any]:
        """The checkout's own allocation records: none when `lock_file` is missing,
        and a file that exists but isn't a JSON list is refused (#818)."""
        return _parse_allocations(
            lock_file.read_text() if lock_file.exists() else None, str(lock_file)
        )

    def _git_run(
        self,
        *args: str,
        env: dict[str, str] | None = None,
        timeout: float | None = 30,
        user_locale: bool = False,
        text: bool = True,
    ) -> subprocess.CompletedProcess[Any]:
        """Run one git command in the repo root, capturing text output. Never
        interactive (#585): stdin is closed — always, so git can't eat text the CLI
        was piped (#580) — and git may not prompt for credentials, so a remote that
        wants a password fails instead of hanging on the terminal. Commands that run
        the user's hooks pass `timeout=None`: a hook may legitimately take longer
        (#584). Git runs under LC_ALL=C, since its messages are matched (#806);
        `user_locale` keeps the caller's locale for output that is only shown, a
        hook's (#826)."""
        fixed = (
            {"GIT_TERMINAL_PROMPT": GIT_ENV["GIT_TERMINAL_PROMPT"]} if user_locale else GIT_ENV
        )
        return subprocess.run(
            ["git", *args],
            cwd=self.repo_root,
            capture_output=True,
            text=text,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            env={**(os.environ if env is None else env), **fixed},
        )

    def _git_z(self, *args: str) -> subprocess.CompletedProcess[str]:
        """`_git_run` for a `-z` listing (#859): read raw and decoded as UTF-8 with
        `surrogateescape`, as #830's grep is, so a lone `\r` in a filename is kept
        (text mode would turn it into `\n`) and every listing spells a name alike."""
        done = self._git_run(*args, text=False)

        def text(out: bytes | str | None, errors: str) -> str:
            if isinstance(out, bytes):
                return out.decode("utf-8", errors)
            return out or ""

        return subprocess.CompletedProcess(
            done.args, done.returncode,
            text(done.stdout, "surrogateescape"), text(done.stderr, "replace"),
        )

    def _next_id_number_at(self, rev: str, prefix: str) -> int:
        """Next id number for `prefix` as commit `rev` sees it: item filenames and
        frontmatter ids under the work paths, plus the allocation records committed
        there (#585, #590)."""
        import json

        top = self._git_toplevel()
        names, ids = self._ids_at(rev)
        max_num = 0
        for name in names:
            max_num = max(max_num, self._stem_id(Path(name).stem, prefix) or 0)
        for _, found in ids:
            max_num = max(max_num, self._number_in_space(found, prefix))  # (#752, #765)
        lock_rel = self._repo_relative(self.repo_root / ".kanban" / "_ID_ALLOCATIONS.json", top)
        if lock_rel is not None:
            shown = self._git_run("show", f"{rev}:{lock_rel.as_posix()}")
            if shown.returncode == 0:
                try:
                    max_num = max(max_num, self._max_allocated(json.loads(shown.stdout), prefix))
                except Exception:
                    pass
        return max_num + 1

    def _new_item(
        self,
        item_type: WorkItemType,
        title: str,
        current_id: str,
        priority: str,
        assignee: str | None,
        description: str | None,
        tags: list[str] | None,
        content: str | None,
    ) -> tuple[WorkItem, str]:
        """The new backlog item and the text of its file (not written)."""
        type_dir = self._get_type_directory(item_type)
        slug = self._slugify(title)
        filename = f"{current_id}-{slug}.md" if slug else f"{current_id}.md"
        item = WorkItem(
            id=current_id,
            title=title,
            item_type=item_type,
            status=WorkItemStatus.BACKLOG,
            file_path=type_dir / filename,
            priority=priority,
            assignee=assignee,
            created=date.today(),
            description=description,
            tags=tags or [],
        )
        if content is not None:
            return item, self._apply_priority(content, priority)
        return item, item.to_markdown()

    def _with_allocation(
        self, allocations: list[dict[str, Any]], current_id: str, actor: str
    ) -> str:
        """`allocations` plus a record for `current_id` (last 100), as JSON text,
        `allocated_by` the resolved `actor` (#620).
        The record's `prefix` is the id space it was allocated in: the id minus its
        number (`EXP`, `IDEA-R`, `H130.`), for explicit and auto ids alike (#641)."""
        import json

        space, number = self._id_space(current_id) or (current_id, 0)
        allocations = [
            *allocations,
            {
                "id": current_id,
                "prefix": space,
                "number": number,
                "allocated_at": datetime.now().isoformat(),
                "allocated_by": actor,
            },
        ]
        return json.dumps(allocations[-100:], indent=2)

    def _get_type_prefix(self, item_type: WorkItemType) -> str:
        """Get ID prefix for item type: in multi-board mode from the theme of the
        board a new item lands on (#665), or when that theme doesn't define the type,
        the first board (config order) whose theme does (#688); else the configured
        theme. An empty definition (`task: {}` or `task:`) doesn't define the type,
        in either mode, as `_landing_board` reads it (#700)."""
        if self.config.is_multi_board:
            landing = self._landing_board(item_type)
            boards = [landing] if landing is not None else []
            boards += [b for b in self.config.boards if b is not landing]
            defs = (self._board_type_def(board, item_type) for board in boards)
        else:
            theme = self.config.get_theme() or {}
            defs = iter([(theme.get("item_types") or {}).get(item_type.value) or {}])
        type_def = next((d for d in defs if d), None)
        if type_def:
            return type_def.get("id_prefix", item_type.value[:4].upper())
        # Default prefixes (software + nautical + HDD themes)
        prefixes = {
            # Software theme
            WorkItemType.FEATURE: "FEAT",
            WorkItemType.BUG: "BUG",
            WorkItemType.EPIC: "EPIC",
            WorkItemType.ISSUE: "ISSUE",
            WorkItemType.TASK: "TASK",
            WorkItemType.IDEA: "IDEA",
            # Nautical theme
            WorkItemType.EXPEDITION: "EXP",
            WorkItemType.VOYAGE: "VOY",
            WorkItemType.DIRECTIVE: "DIR",
            WorkItemType.HAZARD: "HAZ",
            WorkItemType.SIGNAL: "SIG",
            WorkItemType.CHORE: "CHORE",
            # HDD theme
            WorkItemType.LITERATURE: "LIT",
            WorkItemType.PAPER: "PAPER",
            WorkItemType.HYPOTHESIS: "H",
            WorkItemType.EXPERIMENT: "EXPR",
            WorkItemType.MEASURE: "M",
        }
        return prefixes.get(item_type, "ITEM")

    @classmethod
    def _number_in_space(cls, item_id: str, prefix: str) -> int:
        """`item_id`'s number when it is in `prefix`'s id space: the text before
        its number is exactly the prefix and its separator, case folded
        (`idea-r-3` is in `IDEA-R`, not `IDEA`; `H130.2` is in `H130.`, not `H`;
        a dashless `EXP3` is in no dashed space, as #661 has it), else 0 (#752,
        #765, #776)."""
        key = cls._id_key(item_id)  # its text is folded (#764)
        head = fold_id(prefix + cls._id_sep(prefix))  # one fold (#817)
        return key[1] if key is not None and key[0] == head else 0

    @classmethod
    def _max_allocated(cls, allocations: list[dict[str, Any]], prefix: str) -> int:
        """Highest number `allocations` records in `prefix`'s id space, judged by
        each record's id alone (#641) through `_number_in_space`: `H130.7` counts in
        `H130.`, never in the dashed `H` space, whatever its `prefix` field says, and
        a dashless `EXP003` counts in no dashed space (#776)."""
        return max(
            (cls._number_in_space(str(alloc.get("id", "")), prefix) for alloc in allocations),
            default=0,
        )  # one id-space rule for every source (#752, #765, #776)

    def _fetched_default(self) -> str | None:
        """The already-fetched `refs/remotes/origin/<default>`, when this clone has
        one; read locally, never from the network (#641). `origin/HEAD` names it
        when set; a `git remote add` clone, or a clone of an empty remote, has none
        (and an explicit-refspec fetch never makes one), so then `origin/main`,
        `origin/master`, or the only branch fetched from origin."""
        known = self._git_run("symbolic-ref", "--short", "refs/remotes/origin/HEAD")
        head = known.stdout.strip() if known.returncode == 0 else ""
        if head.startswith("origin/"):
            return f"refs/remotes/{head}"
        listed = self._git_run("for-each-ref", "--format=%(refname)", "refs/remotes/origin/")
        refs = [r for r in listed.stdout.split() if r != "refs/remotes/origin/HEAD"]
        for ref in ("refs/remotes/origin/main", "refs/remotes/origin/master"):
            if ref in refs:
                return ref
        return refs[0] if len(refs) == 1 else None

    def _get_next_id_number(self, prefix: str, base: str | None = None) -> int:
        """Next id number for `prefix`: past the scanned board and past `base`, by
        default the already-fetched origin/<default> (when there is one), so a
        local create or `next-id --no-sync` never re-issues an id already on origin
        (#641). No network is used; if git fails reading the base, the local scan
        stands, with a warning."""
        num = self._scanned_next_id_number(prefix)
        try:
            ref = base or self._fetched_default()
            return num if ref is None else max(num, self._next_id_number_at(ref, prefix))
        except (subprocess.TimeoutExpired, OSError) as e:
            logger.warning(f"Could not read the fetched default branch for {prefix}: {e}")
            return num

    def _scanned_next_id_number(self, prefix: str) -> int:
        """Next id number for `prefix` as this checkout sees it.

        Scans all three sources of truth:
        1. _ID_ALLOCATIONS.json (allocated but possibly not yet on disk)
        2. Item IDs from frontmatter (via self._items)
        3. Filenames directly (to catch files without proper frontmatter)

        Returns max across all sources + 1.
        """
        import json

        if not self._items:
            self.scan()

        max_num = 0

        # Source 1: Check _ID_ALLOCATIONS.json for previously allocated IDs
        lock_file = self.repo_root / ".kanban" / "_ID_ALLOCATIONS.json"
        if lock_file.exists():
            try:
                max_num = self._max_allocated(json.loads(lock_file.read_text()), prefix)
            except (json.JSONDecodeError, Exception):
                pass

        # Source 2: IDs from parsed items, in prefix's own id space (#765)
        for existing_id in self._items.keys():
            max_num = max(max_num, self._number_in_space(existing_id, prefix))  # (#765)

        # Source 3: Scan filenames directly to catch files without frontmatter
        for scan_path in self.config.get_work_paths():
            full_path = _under(self.repo_root, scan_path)
            if full_path.exists():
                for md_file in full_path.rglob("*.md"):
                    max_num = max(max_num, self._stem_id(md_file.stem, prefix) or 0)

        return max_num + 1

    def get_next_unparented_hypothesis_id(self) -> str:
        """Get the next id for a hypothesis that belongs to no paper.

        Returns the ordinary `H-NNN` form every other work type uses, allocated
        through the same three-source scan as everything else — so an unparented
        hypothesis is not a special case, it is a normal item.

        The two id spaces do not collide: paper-scoped ids are `H{paper}.{n}`,
        allocated by the same `_get_next_id_number` in the `H{paper}.` space, and
        every source filters on the id space (`H-` here, `H130.` there; allocation
        records by their id alone, #641). The non-collision still depends on
        `--paper` being a non-negative int: `--paper=-1` would yield `H-1.1`, which
        lands squarely in the dashed space.
        """
        return f"H-{self._get_next_id_number('H'):03d}"

    @staticmethod
    def _is_deliberately_unparented(item_id: str) -> bool:
        """True if this hypothesis id declares that it belongs to no paper.

        Paper-scoped ids are `H{paper}.{n}` — a DOT. So an id in the DASHED
        space is unparented BY ITS FORM, not by an omission someone should fix,
        and the validators must not call it orphaned.

        This subsumes the pre-existing `H-DRAFT...` convention rather than
        sitting beside it: that spelling also starts with `H-`, and it meant the
        same thing — a hypothesis deliberately not attached to a paper.
        """
        return item_id.startswith("H-")

    # ------------------------------------------------------------------
    # Parent turtle block auto-update (EXP-1026)
    # ------------------------------------------------------------------

    # Inverse relationships: when a child HDD item is created, what triple
    # to add to the parent's turtle block.
    _INVERSE_RELATIONS: dict[str, dict[str, str]] = {
        "hypothesis": {
            "predicate_ns": "paper",
            "predicate_local": "hasHypothesis",
            "child_prefix": "hyp",
        },
        "experiment": {
            "predicate_ns": "hyp",
            "predicate_local": "hasExperiment",
            "child_prefix": "expr",
        },
        "literature": {
            "predicate_ns": "idea",
            "predicate_local": "hasLiterature",
            "child_prefix": "lit",
        },
    }

    # Regex to find the first fenced ```turtle or ```yurtle block.
    _TURTLE_BLOCK_RE = re.compile(
        r"(```(?:turtle|yurtle)\s*\r?\n)(.*?)(^```)",
        re.DOTALL | re.MULTILINE,
    )

    def update_parent_turtle_block(
        self,
        parent_id: str,
        child_type: str,
        child_id: str,
    ) -> bool:
        """Update a parent item's turtle block with an inverse reference to a child.

        When a child HDD item is created (hypothesis, experiment, literature),
        this method adds a triple to the parent's turtle block so the parent
        knows about its children. The file is changed in the working tree only:
        with `--push` the link rides in the child's own compare-and-swap commit
        instead (`create_item_and_push(parent=...)`, #645).

        Uses rdflib for correct Turtle parsing, triple addition, and
        serialization. Requires yurtle-rdflib to be installed.

        Args:
            parent_id: ID of the parent item (e.g., "PAPER-130", "H130.1").
            child_type: Type of the child item ("hypothesis", "experiment",
                        "literature").
            child_id: ID of the child item (e.g., "H130.1", "EXPR-130").

        Returns:
            True if the parent was updated, False otherwise.
        """
        return self.link_parent(parent_id, child_type, child_id) == "added"

    def link_parent(self, parent_id: str, child_type: str, child_id: str) -> str:
        """`update_parent_turtle_block`, saying what happened: 'added', or why no
        link was written ('missing', 'no-relation', 'no-block', 'unparseable',
        'linked'), from the one parse that tried (#750)."""
        edit, state = self._parent_link_edit(parent_id, child_type, child_id)
        if edit is None:
            assert state is not None  # a None edit always says why (#766)
            return state
        self._apply_parent_link(*edit)
        return "added"

    def _parent_link_edit(
        self, parent_id: str, child_type: str, child_id: str
    ) -> tuple[tuple[WorkItem, str, LineEndings] | None, str | None]:
        """The parent item, its file's text with the inverse reference to `child_id`
        added, and its line endings, without writing anything (#674); or None and
        why there is nothing to add, as `parent_link_state` names it (#750)."""
        self._check_text(child_id=child_id)  # before the parent is rewritten (#239)
        if child_type not in self._INVERSE_RELATIONS:
            logger.debug(f"No inverse relation defined for child type: {child_type}")
            return None, "no-relation"

        parent = self._current_item(parent_id)  # the file now (#638)
        if parent is None or not parent.file_path.exists():
            # the CLI says it; no warning as well (#724)
            logger.debug(f"Parent {parent_id} not found — skipping inverse reference")
            return None, "missing"
        self.refuse_duplicate(parent, "a parent link")  # which copy? (#754)

        content, eol = self._read_item_text(parent.file_path)
        new_content, state = self._linked_parent_text(
            content, parent_id, child_type, child_id
        )
        return (None, state) if new_content is None else ((parent, new_content, eol), None)

    def _apply_parent_link(self, parent: WorkItem, new_content: str, eol: LineEndings) -> None:
        """Write a `_parent_link_edit`, and refresh the parent's graph and cache (#638)."""
        self._write_item_text(parent.file_path, new_content, eol)
        parent.graph = self._parse_graph(new_content)
        self._reread_item(parent)

    def _uncommitted(self, path: Path) -> bool:
        """True when git shows `path` as changed from HEAD or untracked (#674)."""
        return self._git_state(path) is not None

    def _git_state(self, path: Path) -> str | None:
        """'untracked' (never committed: `??`, added as new `A `/`AM`, or intent-to-add
        ` A`), 'changed' (a committed file's edit, a staged rename included), or None
        when git shows `path` clean (#674, #693, #705)."""
        shown = self._git_run("status", "--porcelain", "--", str(path))
        if shown.returncode != 0 or not shown.stdout.strip():
            return None
        code = shown.stdout[:2]
        if code == "??":
            return "untracked"
        if "A" in code:
            # `A` under a pathspec is also how a staged rename's new path shows
            # (`git mv`): that file IS committed, under its old name (#705 review)
            # -z: raw paths, as git would C-quote non-ASCII ones (#718); fields run
            # status, path[, new path] — a rename's destination is two after R…
            renames = self._git_z(
                "diff", "--no-relative", "--cached", "-M", "--name-status", "-z", "HEAD"
            )
            rel = self._repo_relative(path, self._git_toplevel())
            fields, dests, i = renames.stdout.split("\0"), set(), 0
            while i < len(fields) - 1:  # records: status, path[, new path]
                status = fields[i]
                if status[:1] in ("R", "C"):
                    if status[:1] == "R" and i + 2 < len(fields):
                        dests.add(fields[i + 2])
                    i += 3
                else:
                    i += 2
            if rel is not None and rel.as_posix() in dests:
                return "changed"
            # a rename below git's 50% similarity is D + A: git itself calls it a
            # new file, and so does this (#718)
            return "untracked"
        return "changed"

    def parent_link_state(self, parent_id: str, child_type: str, child_id: str) -> str:
        """Why no inverse reference would be written for `child_id` on `parent_id`:
        'missing' (on no board), 'no-relation' (the child type has none),
        'no-block' (the parent has no turtle block), 'unparseable' (the block
        can't be parsed or has no URI subject, #737), 'linked' (already there),
        or 'addable' (#724). The relation first, as `_parent_link_edit` (#766)."""
        if child_type not in self._INVERSE_RELATIONS:
            return "no-relation"
        parent = self._current_item(parent_id)
        if parent is None or not parent.file_path.exists():
            return "missing"
        content = parent.file_path.read_text(encoding="utf-8").replace("\r\n", "\n")
        _, state = self._linked_parent_text(content, parent_id, child_type, child_id)
        return state or "addable"

    def _linked_parent_text(
        self, content: str, parent_id: str, child_type: str, child_id: str
    ) -> tuple[str | None, str | None]:
        """A parent's LF text with the inverse reference to `child_id` added to its
        turtle block (#645), or None with why there is nothing to add:
        'no-relation', 'no-block', 'unparseable' or 'linked' (#724, #737)."""
        relation = self._INVERSE_RELATIONS.get(child_type)
        if relation is None:
            logger.debug(f"No inverse relation defined for child type: {child_type}")
            return None, "no-relation"

        match = self._TURTLE_BLOCK_RE.search(content)
        if not match:
            # the CLI says it (parent_link_state), no warning as well (#724)
            logger.debug(f"No turtle block in {parent_id} — skipping inverse reference")
            return None, "no-block"

        # Build rdflib URIs for the predicate and child object
        pred_ns = Namespace(PREFIXES[relation["predicate_ns"]])
        predicate = pred_ns[relation["predicate_local"]]
        child_ns = Namespace(PREFIXES[relation["child_prefix"]])
        child_uri = child_ns[child_id]

        # Modify the turtle block using rdflib
        new_inner, state = self._modify_turtle_block(
            match.group(2), predicate, child_uri, parent_id
        )
        if state is not None:
            return None, state

        # Replace the turtle block in the file
        new_block = match.group(1) + new_inner + "\n" + match.group(3)
        return content[: match.start()] + new_block + content[match.end() :], None

    def _parent_link_blob(
        self, base: str, parent_id: str, child_type: str, child_id: str
    ) -> tuple[dict[Path, str], str | None]:
        """The parent's file as commit `base` holds it, with the inverse reference to
        `child_id` added, keyed by its repo-relative path; empty when there is
        nothing to add. A parent this board has but `base` doesn't hold is refused:
        the link can only ride in the child's commit when the parent is already
        there (#645). A parent that exists nowhere is skipped, as it is locally.
        Also returns why nothing was added, read from `base`'s copy: 'missing',
        'no-relation', 'no-block', 'unparseable' or 'linked', else None (#724, #737).
        A parent `base` holds in more than one file is refused (#754). The
        relation is checked first, as locally (#777); origin's ids are read once
        (#796); and the parent is only a file whose frontmatter `id:` is it, never a
        lookalike named after it, as the board sees it (#819)."""
        if child_type not in self._INVERSE_RELATIONS:
            return {}, "no-relation"
        ids = self._ids_at(base)[1]
        holders = self._holders_at(base, parent_id, ids, loaded=True)  # (#856)
        if len(holders) > 1:
            raise _CasRefusedError(
                f"{parent_id} is on more than one board on origin/{self._default_branch()} "
                f"({', '.join(holders)}): a parent link to it is ambiguous; fix the "
                "duplicate ID first; nothing was created"
            )
        held = holders[0] if holders else None
        if held is None and self.get_item(parent_id) is None:
            # the CLI says it; no warning as well (#724)
            logger.debug(f"Parent {parent_id} not found — skipping inverse reference")
            return {}, "missing"
        if held is None:
            branch = self._default_branch()
            local = self.get_item(parent_id)
            rel = None if local is None else self._repo_relative(
                local.file_path, self._git_toplevel()
            )
            if rel is not None and self._git_run(
                "log", "-1", "--format=%H", base, "--", f":(top){rel.as_posix()}"
            ).stdout.strip():
                raise _CasRefusedError(
                    f"{parent_id} is not on origin/{branch} (it was removed there); "
                    "nothing was created"
                )
            raise _CasRefusedError(
                f"{parent_id} is not on origin/{branch}, so {child_type} "
                f"{child_id} can't be linked to it there: push {parent_id} first; "
                "nothing was created"
            )
        # the blob's own bytes: text mode would turn a CRLF file into LF (#128)
        shown = subprocess.run(
            ["git", "cat-file", "blob", f"{base}:{held}"],
            cwd=self.repo_root,
            capture_output=True,
            timeout=30,
            stdin=subprocess.DEVNULL,
            env={**os.environ, **GIT_ENV},
        )
        if shown.returncode != 0:
            raise _CasRefusedError(
                f"Could not read {held} on the default branch: "
                f"{shown.stderr.decode('utf-8', 'replace').strip()}; nothing was created"
            )
        try:
            content, eol = LineEndings.read(shown.stdout.decode("utf-8"))
        except UnicodeDecodeError as e:
            raise _CasRefusedError(
                f"{held} on the default branch is not UTF-8 ({e}); nothing was created"
            ) from None
        new_content, state = self._linked_parent_text(content, parent_id, child_type, child_id)
        if new_content is None:
            return {}, state
        return {Path(held): eol.apply(new_content)}, None

    # a prefixed name's local part this writes: PN_LOCAL's plain subset (#812)
    _PLAIN_LOCAL_RE = re.compile(r"[A-Za-z0-9_](?:[A-Za-z0-9_.\-]*[A-Za-z0-9_\-])?")
    _BASE_DECL_RE = re.compile(r"^\s*(?:@base\b|(?i:base)\s*<)", re.MULTILINE)

    # Turtle's PN_LOCAL (#910): name characters, `:`, `%xx` and `\`-escapes; a `.`
    # only inside, never last (a statement's `.` follows)
    _PLX = r"%[0-9A-Fa-f]{2}|\\[_~.\-!$&'()*+,;=/?#@%]"
    _PN_LOCAL = rf"(?:(?:[\w:]|{_PLX})(?:(?:[\w.:-]|{_PLX})*(?:[\w:-]|{_PLX}))?)?"
    # a turtle block's tokens, in order (#876, #910): strings and comments only to be
    # skipped; `@prefix`/`@base` and SPARQL `PREFIX`/`BASE` directives, whose
    # bindings apply from where they are written; IRI refs and prefixed names
    _TURTLE_TERM_RE = re.compile(
        r'"""(?:[^"\\]|\\.|"(?!""))*"""'
        r"|'''(?:[^'\\]|\\.|'(?!''))*'''"
        r'|"(?:[^"\\\n]|\\.)*"'
        r"|'(?:[^'\\\n]|\\.)*'"
        r"|#[^\n]*"
        r"|(?<![\w:-])(?:@prefix|(?i:prefix))\s+([A-Za-z][\w.-]*)?:\s*<([^<>\"{}|^`\\\s]*)>"
        r"|(?<![\w:-])(?:@base|(?i:base))\s+<([^<>\"{}|^`\\\s]*)>"
        r"|<([^<>\"{}|^`\\\s]*)>"
        rf"|(?<![\w.:-])([A-Za-z][\w.-]*)?:({_PN_LOCAL})"
    )

    @classmethod
    def _first_written(
        cls, text: str, subjects: list[URIRef], namespaces: dict[str, Any], base_uri: str
    ) -> URIRef:
        """Of `subjects`, the one whose term is written first in the block `text`
        (#876, #910). The block is read in order: strings and comments are skipped,
        each `@prefix`/`@base` (or SPARQL `PREFIX`/`BASE`) binds from where it is
        written, IRI refs resolve against the base then in force (else
        `base_uri`), and prefixed names, with Turtle's full local-name grammar,
        against the prefixes then declared. `<#PAPER-10>` is no `<#PAPER-1>`, a
        comment naming a subject doesn't count, and an empty local name is no
        wildcard. None written (a subject rdflib derived): the first in IRI order.
        `namespaces` (rdflib's final bindings) resolves a prefix used before any
        declaration the reader saw."""
        wanted = {str(s): s for s in subjects}
        base = base_uri
        bound: dict[str, str] = {}

        def resolve(ref: str) -> str:
            if ref.startswith("#"):
                return base.split("#")[0] + ref
            if re.match(r"[A-Za-z][\w+.-]*:", ref):
                return ref
            return urljoin(base, ref)

        for match in cls._TURTLE_TERM_RE.finditer(text):
            pfx, pfx_iri, base_iri, ref, prefix, name = match.groups()
            if pfx_iri is not None:
                bound[pfx or ""] = resolve(pfx_iri)
                continue
            if base_iri is not None:
                base = resolve(base_iri)
                continue
            if ref is not None:
                iri = resolve(ref)
            elif name is not None:
                ns = bound.get(prefix or "")
                if ns is None and (prefix or "") in namespaces:
                    ns = str(namespaces[prefix or ""])
                if ns is None:
                    continue
                iri = ns + re.sub(r"\\(.)", r"\1", name)  # `\-` is `-`; `%41` stays
            else:
                continue
            if iri in wanted:
                return wanted[iri]
        return subjects[0]

    def _modify_turtle_block(
        self,
        turtle_content: str,
        predicate_uri: Any,
        child_uri: Any,
        parent_id: str | None = None,
    ) -> tuple[str, str | None]:
        """A turtle block's inner text with `<subject> <predicate> <child> .`
        appended as one line (#812).

        rdflib parses the block, to find its subject and to see whether the triple
        is already there. The subject is the one whose local name (after `#` or the
        last `/`) is `parent_id`, compared case-folded (#838); with none, the one
        written first; the block itself is never re-serialized, so every
        original line stays as written. The subject is written `<#ID>` when it is
        `urn:yurtle:block#ID` (and the block declares no base), in full otherwise;
        the predicate and child use a prefix the block declares for their
        namespace, else the full IRI.

        Args:
            turtle_content: Inner content of a fenced turtle block (between
                            the ``` fences, not including them), LF.
            predicate_uri: rdflib URIRef for the predicate to add.
            child_uri: rdflib URIRef for the child object to add.
            parent_id: the parent's ID, whose own subject gets the triple.

        Returns:
            Tuple of (new_content, state). state is None when the triple was
            added, 'linked' when it already exists, and 'unparseable' when the
            block can't be parsed or has no URI subject (#737). new_content does
            not end in a newline: the caller puts one before the fence.
        """
        base_uri = "urn:yurtle:block"
        g = Graph(bind_namespaces="none")  # only the prefixes the block declares
        try:
            g.parse(data=turtle_content, format="turtle", publicID=base_uri)
        except Exception as e:
            # the CLI says it (parent_link_state), no warning as well (#737)
            logger.debug(f"Failed to parse turtle block for modification: {e}")
            return turtle_content, "unparseable"

        # rdflib lists subjects in hash order (#838): pick by name, then by text
        subjects = sorted({s for s in g.subjects() if isinstance(s, URIRef)}, key=str)
        if not subjects:
            return turtle_content, "unparseable"

        def local(uri: URIRef) -> str:
            return re.split(r"[#/]", str(uri))[-1]

        own = [
            s for s in subjects
            if parent_id is not None and local(s).casefold() == parent_id.casefold()
        ]
        subject = own[0] if own else self._first_written(
            turtle_content, subjects, dict(g.namespaces()), base_uri
        )

        # Idempotency: skip if triple already present
        if (subject, predicate_uri, child_uri) in g:
            return turtle_content, "linked"

        declared = [
            (name, str(ns))
            for name, ns in g.namespaces()
            if re.search(
                rf"(?<![\w@:-])(?:@prefix|(?i:prefix))\s+{re.escape(name)}:\s*<{re.escape(str(ns))}>",
                turtle_content,
            )
        ]

        def term(uri: URIRef) -> str:
            for name, ns in declared:
                local = str(uri)[len(ns):]
                if str(uri).startswith(ns) and self._PLAIN_LOCAL_RE.fullmatch(local):
                    return f"{name}:{local}"
            return f"<{uri}>"

        fragment = str(subject)[len(base_uri) + 1:]
        if (
            str(subject).startswith(base_uri + "#")
            and ">" not in fragment
            and not self._BASE_DECL_RE.search(turtle_content)
        ):
            subject_text = f"<#{fragment}>"
        else:
            subject_text = f"<{subject}>"

        line = f"{subject_text} {term(predicate_uri)} {term(child_uri)} ."
        head = turtle_content if turtle_content.endswith("\n") else turtle_content + "\n"
        return head + line, None

    def _merge_into_existing_block(
        self, content: str, match: re.Match, missing: Graph
    ) -> str:
        """Merge missing triples into an existing fenced turtle block.

        Parses the existing block with rdflib, adds the missing triples,
        serializes back, and replaces the block in place.
        """
        base_uri = URIRef("urn:yurtle:block")
        g = Graph()
        try:
            g.parse(data=match.group(2), format="turtle", publicID=str(base_uri))
        except Exception as e:
            logger.warning(f"Failed to parse existing block for merge: {e}")
            # Fall back to inserting a new block
            block = self._serialize_as_turtle_block(missing)
            return self._insert_turtle_block(content, block)

        # Add missing triples (subjects already normalized by caller)
        for s, p, o in missing:
            g.add((s, p, o))

        # Bind prefixes and serialize
        for name, uri in PREFIXES.items():
            g.bind(name, Namespace(uri))
        result = g.serialize(format="turtle", base=base_uri)
        if isinstance(result, bytes):
            result = result.decode("utf-8")
        lines = result.strip().split("\n")
        lines = [line for line in lines if not line.startswith("@base ")]
        merged_inner = "\n".join(lines) + "\n"

        # Replace the old block content with the merged content
        return content[:match.start(2)] + merged_inner + content[match.end(2):]

    # ------------------------------------------------------------------
    # Backfill: add turtle blocks to HDD files that lack them
    # ------------------------------------------------------------------

    def backfill_turtle_blocks(
        self, dry_run: bool = False
    ) -> list[dict[str, Any]]:
        """Scan HDD items and add missing turtle knowledge blocks.

        Uses graph diffing: builds an "expected" rdflib Graph from each file's
        YAML frontmatter, compares against the file's existing graph (parsed
        by yurtle-rdflib), and serializes only the missing triples as a new
        fenced turtle block.

        Args:
            dry_run: If True, report what would change without modifying files.

        Returns:
            List of result dicts with keys: path, id, type, action,
            triples_added.
        """
        results: list[dict[str, Any]] = []
        for item in self.get_items():
            content, eol = self._read_item_text(item.file_path)
            frontmatter = self._parse_frontmatter(content)
            if not frontmatter:
                continue

            raw_type = frontmatter.get("type", "")
            hdd_type = _TYPE_ALIASES.get(raw_type, raw_type)
            if hdd_type not in _BACKFILL_TYPES:
                continue

            # Build expected graph from frontmatter fields
            want = self._build_expected_graph(hdd_type, frontmatter)
            if len(want) == 0:
                continue

            # Parse existing graph (frontmatter TTL + fenced blocks)
            have_raw = self._parse_graph(content) or Graph()

            # Normalize subjects: parse_yurtle resolves <#ID> relative to
            # the file path (file:///.../#ID), but _build_expected_graph
            # uses the synthetic base (urn:yurtle:block#ID).  Remap any
            # subject whose fragment matches the item ID so the diff works.
            item_id = str(frontmatter.get("id", ""))
            target_subject = URIRef(f"urn:yurtle:block#{item_id}")
            have = Graph()
            for s, p, o in have_raw:
                if str(s).endswith(f"#{item_id}"):
                    have.add((target_subject, p, o))
                else:
                    have.add((s, p, o))

            # Diff: triples we want but don't have
            missing = want - have
            if len(missing) == 0:
                results.append({
                    "path": str(item.file_path),
                    "id": item.id,
                    "type": hdd_type,
                    "action": "up_to_date",
                    "triples_added": 0,
                })
                continue

            if not dry_run:
                # If file already has a turtle block, merge into it;
                # otherwise insert a new one.
                match = self._TURTLE_BLOCK_RE.search(content)
                if match:
                    new_content = self._merge_into_existing_block(
                        content, match, missing,
                    )
                else:
                    block = self._serialize_as_turtle_block(missing)
                    new_content = self._insert_turtle_block(content, block)
                self._write_item_text(item.file_path, new_content, eol)
                item.graph = self._parse_graph(new_content) or Graph()
                self._reread_item(item)  # the cache holds the file now (#638)

            results.append({
                "path": str(item.file_path),
                "id": item.id,
                "type": hdd_type,
                "action": "backfill" if not dry_run else "would_backfill",
                "triples_added": len(missing),
            })

        return results

    def _build_expected_graph(
        self, hdd_type: str, frontmatter: dict[str, Any]
    ) -> Graph:
        """Build an rdflib Graph of expected triples from frontmatter fields.

        Maps YAML frontmatter relationship fields to the RDF triples that
        TurtleBlockBuilder would generate for a new item of this type.
        """
        g = Graph()
        item_id = str(frontmatter.get("id", ""))
        if not item_id:
            return g

        # Use synthetic base URI for relative <#ID> subjects
        subject = URIRef(f"urn:yurtle:block#{item_id}")
        title = frontmatter.get("title", "")

        # Type triple (all HDD types)
        rdf_class = _HDD_TYPE_CLASSES.get(hdd_type)
        if rdf_class:
            g.add((subject, RDF.type, rdf_class))

        # Label (all types)
        if title:
            g.add((subject, RDFS.label, Literal(title)))

        # Type-specific relationship triples
        if hdd_type == "hypothesis":
            paper = frontmatter.get("paper")
            if paper:
                paper_num = _normalize_paper_num(paper)
                g.add((subject, _HYP.paper, _PAPER_NS[f"PAPER-{paper_num}"]))
            target = frontmatter.get("target")
            if target:
                g.add((subject, _HYP.target, Literal(str(target))))
            measures = frontmatter.get("measures")
            if measures:
                for m in (measures if isinstance(measures, list) else [measures]):
                    g.add((subject, _HYP.measuredBy, _MEASURE[str(m)]))
            source_idea = frontmatter.get("source_idea")
            if source_idea:
                g.add((subject, _HYP.sourceIdea, _IDEA[str(source_idea)]))
            literature = frontmatter.get("literature")
            if literature:
                for lit in (literature if isinstance(literature, list) else [literature]):
                    g.add((subject, _HYP.informedBy, _LIT[str(lit)]))

        elif hdd_type == "experiment":
            paper = frontmatter.get("paper")
            if paper:
                paper_num = _normalize_paper_num(paper)
                g.add((subject, _EXPR.paper, _PAPER_NS[f"PAPER-{paper_num}"]))
            hypotheses = frontmatter.get("hypotheses", [])
            if hypotheses:
                first_hyp = hypotheses[0] if isinstance(hypotheses, list) else hypotheses
                g.add((subject, _EXPR.hypothesis, _HYP[str(first_hyp)]))
            measures = frontmatter.get("measures")
            if measures:
                for m in (measures if isinstance(measures, list) else [measures]):
                    g.add((subject, _EXPR.measure, _MEASURE[str(m)]))

        elif hdd_type == "measure":
            unit = frontmatter.get("unit")
            if unit:
                g.add((subject, _MEASURE.unit, Literal(str(unit))))
            category = frontmatter.get("category")
            if category:
                g.add((subject, _MEASURE.category, Literal(str(category))))

        elif hdd_type == "literature":
            source_idea = frontmatter.get("source_idea")
            if source_idea:
                g.add((subject, _LIT.explores, _IDEA[str(source_idea)]))

        return g

    def _serialize_as_turtle_block(self, graph: Graph) -> str:
        """Serialize an rdflib Graph as a fenced ```turtle block.

        Uses rdflib's Turtle serializer with HDD prefix bindings.
        Same pattern as _modify_turtle_block(): synthetic base URI,
        strip @base declaration from output.

        Works on a copy to avoid mutating the caller's graph.
        """
        base_uri = URIRef("urn:yurtle:block")

        # Work on a copy to avoid mutating the input graph
        work = Graph() + graph

        # Bind all HDD prefixes for clean serialization
        for name, uri in PREFIXES.items():
            work.bind(name, Namespace(uri))

        result = work.serialize(format="turtle", base=base_uri)
        if isinstance(result, bytes):
            result = result.decode("utf-8")

        # Strip @base declaration (not used in our blocks)
        lines = result.strip().split("\n")
        lines = [line for line in lines if not line.startswith("@base ")]
        inner = "\n".join(lines)

        return f"```turtle\n{inner}\n```"

    def _insert_turtle_block(self, content: str, block: str) -> str:
        """Insert a fenced turtle block after YAML frontmatter.

        Places the block between the closing --- and the first # heading.
        """
        split = self._split_frontmatter(content)
        if split is None:
            return content + "\n\n" + block + "\n"

        after_frontmatter = split[1]
        head = content[: len(content) - len(after_frontmatter)]
        return head + "\n\n" + block + "\n" + after_frontmatter.lstrip("\n")

    def allocate_next_id(
        self,
        prefix: str,
        sync_remote: bool = True,
        commit_allocation: bool = True,
    ) -> dict[str, Any]:
        """Allocate the next available ID for a prefix with git synchronization.

        This method prevents duplicate IDs when multiple agents create work items
        concurrently. With a remote (and sync_remote and commit_allocation), the
        allocation record is committed onto the fetched default branch and pushed
        as a compare-and-swap; a lost race retries with a new id (#590). Otherwise
        the record is committed locally.

        Args:
            prefix: The ID prefix (e.g., "EXP", "FEAT", "BUG")
            sync_remote: Whether to fetch latest from remote first
            commit_allocation: Whether to commit the allocation lock file

        Returns:
            dict with 'id', 'prefix', 'number', and 'success' keys
        """

        self._check_text(prefix=prefix)  # before any write or commit (#219)
        # a malformed prefix allocates nothing (#802); one NFC spelling (#817)
        # the one fold, NFC(upper(NFC)): upper-casing can undo NFC (`ΐ`) (#817)
        prefix = fold_id(self._check_prefix(prefix))
        actor = ""
        if commit_allocation:
            # the record names who allocated: no actor, nothing is written (#620)
            try:
                actor = resolve_actor(None, cwd=self.repo_root)
            except InputRefused as e:
                return {"success": False, "id": None, "prefix": prefix, "number": None,
                        "message": str(e)}

        # With a remote, claim the id by compare-and-swap on the default branch, as
        # `create --push` does (#590): never the checked-out branch, index or tree
        if sync_remote and commit_allocation and self._has_remote():
            made: dict[str, Any] = {}

            def build(base: str) -> tuple[dict[Path, str], str]:
                made["id"] = self._next_id_at(base, prefix)
                blobs = self._allocation_blob(base, made["id"], actor)
                # the record's folders are guarded as a create's are (#870)
                twin = self._folder_twin_refusal(
                    self._tree_names(base), list(blobs), f"allocating {made['id']}"
                )
                if twin is not None:
                    raise _CasRefusedError(twin)
                return blobs, f"Allocate ID: {made['id']}"

            def landed(branch: str, local: bool) -> dict[str, Any]:
                space = self._id_space(made["id"])  # `H130.2` has no dash (#655)
                num = space[1] if space else None
                return {
                    "success": True,
                    "id": made["id"],
                    "prefix": prefix,
                    "number": num,
                    "message": f"Allocated {made['id']} on origin/{branch}",
                }

            result = self._cas_on_default_branch(build, landed, 3, "allocation")
            if not result["success"]:
                return {"success": False, "id": None, "prefix": prefix, "number": None,
                        "message": result["message"]}
            return result

        # Otherwise locally: the fetched default branch (refetched first when
        # syncing) only raises the floor; `--no-sync` reads what is already
        # fetched, with no network (#641)
        fetched = None
        if sync_remote and self._has_remote():
            try:
                branch, known = self._resolve_default()
                if self._fetch_default(branch, record=known).returncode == 0:
                    fetched = f"refs/remotes/origin/{branch}"
            except (subprocess.TimeoutExpired, OSError) as e:
                logger.warning(f"Git fetch failed: {e}")
        self._items.clear()
        self.scan()
        next_num = self._get_next_id_number(prefix, fetched)
        item_id = self._format_id(prefix, next_num)

        if commit_allocation:
            lock_file = self.repo_root / ".kanban" / "_ID_ALLOCATIONS.json"
            allocations = self._local_allocations(lock_file)  # corrupt: refused (#818)
            lock_file.parent.mkdir(parents=True, exist_ok=True)
            lock_file.write_text(self._with_allocation(allocations, item_id, actor))
            try:
                self._commit_paths([lock_file], f"Allocate ID: {item_id}")
            except GitCommitError as e:
                # a refused commit is an error (#584)
                return {"success": False, "id": None, "prefix": prefix, "number": None,
                        "message": str(e)}

        return {
            "success": True,
            "id": item_id,
            "prefix": prefix,
            "number": next_num,
            "message": f"Allocated {item_id}",
        }

    def move_item(
        self,
        item_id: str,
        new_status: WorkItemStatus,
        commit: bool = True,
        message: str | None = None,
        validate_workflow: bool = True,
        assignee: str | None = None,
        skip_wip_check: bool = False,
        closed_by: str | None = None,
        skip_gates: bool = False,
        gate_context: dict[str, Any] | None = None,
        actor: str | None = None,
        take_over: bool = False,
    ) -> WorkItem:
        """Move a work item to a new status.

        Args:
            item_id: The work item ID to move
            new_status: The target status
            commit: Whether to git commit the change
            message: Optional commit message
            validate_workflow: Whether to validate against workflow rules
            assignee: Optional assignee to set (e.g., 'Claude-M5', 'Claude-DGX')
            skip_wip_check: Whether to skip WIP limit validation
            closed_by: Optional URI recording what triggered this move
                (e.g., a PR URL). Written as kb:closedBy in the TTL block.
            skip_gates: Whether to skip transition gate checks
            gate_context: Extra context for gate evaluation (e.g., CLI flags
                like ``{"self_reviewed": True}``)
            actor: Who is moving it, recorded as kb:by. Resolved through
                `resolve_actor` (explicit, then $YURTLE_AGENT, then git user.name);
                never the assignee, which is not defaulted either (#580).
            take_over: Move an item someone else holds in progress, recording
                kb:takenOverFrom (#574 §4). It needs an explicit actor (`actor` or
                $YURTLE_AGENT; git user.name is not enough). The actor becomes
                the holder unless `assignee` is given. Gates, WIP and legality
                still apply.

        The holder guard (#574 §4): an item whose canonical status is in progress
        and whose assignee is not the actor is refused unless `take_over`; `--force`
        (`validate_workflow`/`skip_wip_check`/`skip_gates`) does not override it.
        It is local and advisory; `claim` is the authoritative gate.
        """
        self._check_text(assignee=assignee, message=message, closed_by=closed_by)  # (#239)
        if take_over:
            try:
                actor = resolve_actor(actor, allow_git_fallback=False, cwd=self.repo_root)
            except InputRefused as e:
                raise InputRefused(f"--take-over needs an explicit actor: {e}") from None
        else:
            actor = resolve_actor(actor, cwd=self.repo_root)
        item = self._writable_item(item_id, "a move")  # the file now (#638, #742)

        old_status = item.status
        taken_over_from = self._holder_guard(item, actor, take_over)
        if taken_over_from is not None and not assignee:
            # a take-over makes the actor the holder, as `claim --take-over` does;
            # an explicit `--assign` still wins (#574 §4)
            assignee = actor

        # Rules, gates and WIP judge the PROPOSED item — a copy carrying the new
        # status and assignee — so `--assign` can satisfy an assignee check; the
        # cached item changes only once the file is written (#586).
        changes: dict[str, Any] = {"status": new_status, "updated": datetime.now()}
        if assignee:
            changes["assignee"] = assignee
        proposed = replace(item, **changes)

        # Validate transition using workflow if available; the state machine
        # reads the source state off item.status, so it gets the proposed item
        # still at old_status
        if validate_workflow:
            valid, error_msg = self._validate_transition(
                replace(proposed, status=old_status), new_status
            )
            if not valid:
                raise InputRefused(error_msg)

        # Check WIP limits (unless skipped)
        if not skip_wip_check:
            board_config = self._wip_board_config(item)
            board = self.get_board(board_name=board_config.name if board_config else None)
            refusal = self._wip_refusal(item, new_status, board, board_config)
            if refusal:
                raise InputRefused(refusal)

        # Evaluate transition gates (unless skipped)
        gates_skipped = False
        if not skip_gates:
            gate_results = self._evaluate_gates(
                proposed, old_status, new_status, gate_context or {}
            )
            blocking = [
                r for r in gate_results
                if not r.passed and r.severity == "blocking"
            ]
            if blocking:
                msgs = "; ".join(r.message for r in blocking)
                raise InputRefused(f"Gate check failed: {msgs}")
        elif self._has_gates_configured(item):
            # Only record gates_skipped when gates actually exist
            gates_skipped = True

        # Update file with status history, then the cached item
        forced = not validate_workflow
        self._update_item_file_with_history(
            proposed, old_status, new_status, assignee,
            actor=actor, forced=forced, closed_by=closed_by,
            gates_skipped=gates_skipped, taken_over_from=taken_over_from,
        )
        for name, value in changes.items():
            setattr(item, name, value)
        item = self._reread_item(item) or item  # the cache holds the file now (#638)

        # Git commit if requested
        if commit:
            commit_msg = message
            if not commit_msg:
                commit_msg = f"Move {item.id} to {new_status.value}"  # the file's (#751)
                if forced:
                    commit_msg += " (forced)"
                if assignee:
                    commit_msg += f" (assigned to {assignee})"
                if taken_over_from is not None:
                    commit_msg += f" (taken over from {taken_over_from or 'no holder'})"
            self._git_commit(item.file_path, commit_msg)

        # Fire hooks (after successful move)
        self._hook_engine.trigger(
            HookEvent.STATUS_CHANGE,
            HookContext(
                event=HookEvent.STATUS_CHANGE,
                item_id=item.id,
                item_type=item.item_type.value,
                title=item.title,
                old_status=old_status.value,
                new_status=new_status.value,
                assignee=item.assignee,
                forced=forced,
            ),
        )

        # Fire ASSIGNED hook when an assignee is set
        if assignee:
            self._hook_engine.trigger(
                HookEvent.ASSIGNED,
                HookContext(
                    event=HookEvent.ASSIGNED,
                    item_id=item.id,
                    item_type=item.item_type.value,
                    title=item.title,
                    new_status=new_status.value,
                    assignee=assignee,
                ),
            )

        # Fire BLOCKED hook when item moves to blocked status
        if new_status.value == "blocked":
            self._hook_engine.trigger(
                HookEvent.BLOCKED,
                HookContext(
                    event=HookEvent.BLOCKED,
                    item_id=item.id,
                    item_type=item.item_type.value,
                    title=item.title,
                    new_status="blocked",
                    assignee=item.assignee,
                ),
            )

        return item

    @staticmethod
    def _holder_guard(item: WorkItem, actor: str, take_over: bool) -> str | None:
        """`move`'s holder guard (#574 §4): refuse moving an item someone else holds
        in progress (canonical status), unless `take_over`. Returns the holder a
        take-over records as kb:takenOverFrom, else None: `""` for an in-progress item
        with no holder, the one other thing a take-over overrides (#823)."""
        if item.status != WorkItemStatus.IN_PROGRESS:
            return None
        held = item.assignee
        holder = (held if isinstance(held, str) else str(held or "")).strip()
        if not holder:
            return "" if take_over else None
        if same_actor(holder, actor):
            return None
        if take_over:
            return holder
        raise InputRefused(
            f"{item.id} is held by {holder}; if you are {holder} pass --agent {holder} "
            "or set YURTLE_AGENT; to take it over use --take-over"
        )

    def _wip_board_config(self, item: WorkItem) -> BoardConfig | None:
        """The board whose WIP limits a move of `item` answers to (multi-board);
        None on a single board."""
        if self.config.is_multi_board and item.file_path:
            return self.config.get_board_for_path(item.file_path, self.repo_root)
        return None

    @staticmethod
    def _wip_columns(
        board: Board, new_status: WorkItemStatus, item: WorkItem,
        board_config: BoardConfig | None,
    ) -> list[Column]:
        """The columns showing `new_status` whose WIP limit `item` answers to: none
        when its type is exempt; a limit of 0 is "no limit" (#402, #411, #574)."""
        exempt_types = set(board_config.wip_exempt_types) if board_config else set()
        item_type_str = item.item_type.value
        if item_type_str in exempt_types:
            return []
        # the same column→status lookup the board uses (#87, #442)
        return [
            col for col in board.columns
            if board.column_status(col.id) == new_status and (
                col.get_wip_limit(item_type_str) if col.type_wip_limits is not None
                else col.wip_limit
            )
        ]

    def _wip_refusal(
        self, item: WorkItem, new_status: WorkItemStatus, board: Board,
        board_config: BoardConfig | None,
    ) -> str | None:
        """Why moving `item` to `new_status` would break a WIP limit on `board`, or
        None. Shared by `move` and `claim`, which passes a fetched tree's board
        (#574)."""
        exempt_types = set(board_config.wip_exempt_types) if board_config else set()
        item_type_str = item.item_type.value
        for col in self._wip_columns(board, new_status, item, board_config):
            if col.type_wip_limits is not None:
                # Per-type WIP check
                # the moving item never holds a slot against itself
                type_count = len([
                    i for i in board.get_items_by_status_and_type(new_status, item.item_type)
                    if i.id != item.id
                ])
                limit = col.get_wip_limit(item_type_str)
                if limit and type_count >= limit:
                    return (
                        f"WIP limit reached for {item_type_str}s "
                        f"in {col.name} on {board.name} "
                        f"({type_count}/{limit})"
                    )
            else:
                # Aggregate WIP check — exclude exempt types from count
                items_in_status = [
                    i for i in board.get_items_by_status(new_status)
                    if i.id != item.id and i.item_type.value not in exempt_types
                ]
                current_count = len(items_in_status)
                if col.wip_limit and current_count >= col.wip_limit:
                    return (
                        f"WIP limit reached for {col.name} on "
                        f"{board.name} "
                        f"({current_count}/{col.wip_limit})"
                    )
        return None

    def claim_item(
        self,
        item_id: str,
        *,
        actor: str,
        take_over: bool = False,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[float, float], float] = random.uniform,
        seam: Callable[[int], None] | None = None,
    ) -> Outcome:
        """Claim `item_id` for `actor`: move it to in progress with `actor` holding
        it, as one compare-and-swap on origin's default branch (#574 §3).

        Every attempt of `sync_and_push` judges the item as the FETCHED tree has it
        (`_claim_change`), so a claim that lost the race reads the winner. Hooks
        (`STATUS_CHANGE`, `ASSIGNED`) fire once, after the claim has landed
        (`won` or `local`), for the winner only. `seam`, `sleep` and `jitter` are
        `sync_and_push`'s.

        WIP limits, board paths and ignore patterns (#831), and the item's parse,
        the move's legality (theme and workflows) and the status written (#865),
        are judged by origin's config at the fetched commit (`_judge_at`), or the
        local one when origin has none; gate checks still read the local working
        tree."""
        actor = check_identity(actor, "--agent")

        def mutate(read: Read, attempt: int) -> Change | NoOp | Refuse:
            return self._claim_change(read, item_id, actor, take_over)

        outcome = self.sync_and_push(mutate, sleep=sleep, jitter=jitter, seam=seam)
        if outcome.kind == "local":  # the working tree changed under the cache
            self._items.clear()
            self._board = None
        if outcome.kind in ("won", "local") and isinstance(outcome.data, _Claimed):
            self._fire_claim_hooks(outcome.data)
        return outcome

    def _claim_change(
        self, read: Read, item_id: str, actor: str, take_over: bool
    ) -> Change | NoOp | Refuse:
        """`claim`'s `mutate` (#574 §3): the claim rules, in order, against the
        item as `read` has it — already yours; held by another (a `holder`, so a
        lost race says "lost to"); in progress with no holder; `pickable`'s
        clauses (#575: ready, dependencies met in `read`'s tree); then legality,
        workflow rules, WIP (counted in `read`'s tree) and gates on the PROPOSED
        item (#586). `take_over` overrides the two holder refusals and records
        `kb:takenOverFrom`; an item already in progress then changes holder only."""
        found = self._item_target(read, item_id, "a claim")
        if isinstance(found, Refuse):
            return found
        rel, text, item = found
        # `_item_target` read the judge already: this is its cached copy
        judge = self if read.rev is None else self._judge_at(read.rev)
        old_status = item.status
        in_progress = WorkItemStatus.IN_PROGRESS
        held = item.assignee
        holder = (held if isinstance(held, str) else str(held or "")).strip()
        mine = bool(holder) and same_actor(holder, actor)

        if mine and old_status == in_progress:
            return NoOp(f"{item.id} is already yours ({judge.status_label(item)})")
        # the one pickable predicate (#575), dependencies judged in `read`'s tree;
        # #574's two holder refusals keep their wording and carry its reason
        try:
            index = self._dep_index_at(read) if item.depends_on else None
        except _TreeUnreadableError as e:
            return Refuse(str(e))
        _, reason = judge._pickable(item, actor, index=index)
        if holder and not mine and not take_over:
            also = "" if reason == f"held by {holder}" else f" ({reason})"
            return Refuse(
                f"{item.id} is held by {holder}{also}; to take it over use claim --take-over",
                holder=holder,
            )
        if not holder and old_status == in_progress and not take_over:
            return Refuse(
                f"{item.id} is in progress with no holder; use claim --take-over ({reason})"
            )
        # --take-over skips clause 2 and accepts an item in progress ([steer] 2)
        ok, reason = judge._pickable(item, actor, take_over=take_over, index=index)
        if not ok:
            message = f"Can't claim {item.id}: {reason}"
            if old_status not in (WorkItemStatus.READY, in_progress):
                # and where it may go from here, as `move` says (#574)
                try:
                    valid, error = judge._validate_transition(
                        replace(item, assignee=actor), in_progress
                    )
                except _TreeUnreadableError as e:
                    return Refuse(str(e))
                message += "" if valid else f"; {error}"
            return Refuse(message)

        proposed = replace(item, status=in_progress, assignee=actor, updated=datetime.now())
        if old_status != in_progress:
            try:
                valid, error = judge._validate_transition(
                    replace(proposed, status=old_status), in_progress
                )
            except _TreeUnreadableError as e:  # origin's workflows (#865)
                return Refuse(str(e))
            if not valid:
                return Refuse(error)
            try:
                refusal = self._wip_refusal_in(read, proposed)
            except _TreeUnreadableError as e:  # never count an unreadable tree (#814)
                return Refuse(str(e))
            if refusal:
                return Refuse(refusal)
            blocking = [
                r for r in self._evaluate_gates(proposed, old_status, in_progress, {})
                if not r.passed and r.severity == "blocking"
            ]
            if blocking:
                return Refuse(
                    f"Gate check failed: {'; '.join(r.message for r in blocking)}"
                )

        # recorded only when the take-over overrode something: a holder, or an
        # in-progress item with no holder; the same rule as `move` (#823)
        overrode = (bool(holder) and not mine) or (
            not holder and item.status == WorkItemStatus.IN_PROGRESS
        )
        taken = holder if take_over and overrode else None
        new_text = judge._history_text(  # origin's name for in progress (#865)
            text, proposed, in_progress, actor, actor=actor, taken_over_from=taken
        )
        message = f"Claim {item.id} for {actor}"
        if taken is not None:
            message += f" (taken over from {taken or 'no holder'})"
        claimed = _Claimed(
            item_id=item.id, item_type=item.item_type.value, title=item.title,
            old_status=old_status.value, new_status=in_progress.value, assignee=actor,
            old_assignee=holder,
        )
        return Change({rel: new_text}, message, data=claimed)

    def _item_target(
        self, read: Read, item_id: str, action: str
    ) -> tuple[str, str, WorkItem] | Refuse:
        """(path relative to the git work tree, LF text, parsed item) of `item_id`
        in `read`'s tree: the fetched commit's, else the working tree's (#574). An
        ID held by more than one file there is refused, as every writer refuses it
        (#742, #754): which copy `action` ("a claim", "an update") meant is
        ambiguous."""
        top = self._git_toplevel()
        judge = self
        if read.rev is None:
            try:
                current = self._writable_item(item_id, action)  # refuse_duplicate
            except ValueError as e:
                return Refuse(str(e))
            rel = Path(os.path.relpath(current.file_path, top)).as_posix()
        else:
            try:  # found where origin's config puts the boards (#831)
                judge = self._judge_at(read.rev)
                holders = judge._holders_at(
                    read.rev, item_id, loaded=True  # the board's item (#856)
                )
            except _TreeUnreadableError as e:
                return Refuse(str(e))
            if len(holders) > 1:
                return Refuse(
                    f"{item_id} is on more than one board ({', '.join(holders)}): "
                    f"{action} to it is ambiguous; fix the duplicate ID first"
                )
            if not holders:
                return Refuse(f"Item not found on origin: {item_id}")
            rel = holders[0]
        text = read(rel)
        if text is None:
            return Refuse(f"Item not found: {item_id} ({rel} is gone)")
        item = judge._parse_text(top / rel, text)  # by origin's theme (#865)
        if item is None or (
            fold_id(item.id) != fold_id(item_id)
            and (self._id_key(item.id) is None or self._id_key(item.id) != self._id_key(item_id))
        ):
            return Refuse(f"Item not found: {item_id} ({rel} does not hold it)")
        return rel, text, item

    def _wip_refusal_in(self, read: Read, proposed: WorkItem) -> str | None:
        """`_wip_refusal` for `proposed` with the items counted in `read`'s tree:
        the fetched commit's, else the working tree's (#574). A fetched tree is
        parsed only when a WIP limit applies. A fetched tree is judged by its own
        config (`_judge_at`, #831)."""
        if read.rev is not None and (judge := self._judge_at(read.rev)) is not self:
            return judge._wip_refusal_in(read, proposed)
        board_config = self._wip_board_config(proposed)
        if read.rev is None:
            board = self.get_board(board_name=board_config.name if board_config else None)
            return self._wip_refusal(proposed, proposed.status, board, board_config)
        if self.config.is_multi_board:
            board_config = board_config or self._named_board(None)
            if board_config is None:
                return None
        board = self._board_of(board_config, [])
        if not self._wip_columns(board, proposed.status, proposed, board_config):
            return None
        board.items = self._items_at(read.rev, board_config)
        return self._wip_refusal(proposed, proposed.status, board, board_config)

    def _judge_at(self, rev: str) -> KanbanService:
        """This service as commit `rev`'s own `.kanban/config.yaml` judges (#831):
        a shallow copy with that config (its themes read from `rev`'s
        `.kanban/themes/`) and fresh memos, so the local service is untouched.
        `self` when `rev` holds no config (the local one judges) or when `self`
        already judges `rev`. Raises `_TreeUnreadableError` when `rev`'s config
        doesn't load: never judge by a config origin doesn't have."""
        if self._judged_rev == rev:
            return self
        if rev in self._judges:
            return self._judges[rev]
        rel = self._repo_relative(self.repo_root / ".kanban", self._git_toplevel())
        text = None if rel is None else self._blob_at(rev, f"{rel.as_posix()}/config.yaml")
        judge = self
        if rel is not None and text is not None:
            kanban = rel.as_posix()

            def themes(name: str) -> tuple[str, str] | None:
                path = f"{kanban}/themes/{name}.yaml"
                found = self._blob_at(rev, path)
                return None if found is None else (f"{rev}:{path}", found)

            try:
                config = KanbanConfig.from_text(text, self.repo_root, themes)
            except (ValueError, yaml.YAMLError) as e:
                raise _TreeUnreadableError(
                    f"origin's {kanban}/config.yaml at {rev[:12]} does not load "
                    f"({' '.join(str(e).split())}); refusing rather than judge by "
                    "another config"
                ) from e
            judge = copy.copy(self)
            judge.config = config
            judge._judged_rev = rev
            judge._judges = {}
            judge._status_names_cache = {}
            judge._board_theme_cache = {}
            judge._scanning = False
            judge._items = {}
            judge._folded_items = {}
            judge.duplicate_ids = {}
            judge.parse_warnings = []
            judge._parse_warned = set()  # its own, not self's (#921)
            judge._board = None
            judge._workflows = {}
            # its workflows are `rev`'s too, never the working tree's (#865)
            judge._workflow_parser = WorkflowParser(
                self.repo_root / ".kanban",
                source=lambda: self._files_at(rev, f"{kanban}/workflows/"),
            )
        self._judges = {rev: judge}  # the last fetched commit's only
        return judge

    def _files_at(
        self, rev: str, folder: str, suffix: str = ".md"
    ) -> list[tuple[str, str]]:
        """(`rev:path`, text) of each regular `suffix` file directly in `folder`
        (from the work tree's top, ending in `/`) at commit `rev`, in listing
        order: read as `_blobs_at` reads a board (#832, #865). As the working-tree
        reader does, other files (a `.DS_Store`) are never read and a non-UTF-8
        one is skipped with a warning. Raises `_TreeUnreadableError` when git
        can't read them."""
        listed = self._git_z("ls-tree", "-z", "--full-tree", rev, "--", folder)
        if listed.returncode != 0:
            raise _TreeUnreadableError(
                f"Can't read {folder} at {rev}: git ls-tree failed "
                f"({listed.stderr.strip() or f'exit {listed.returncode}'})"
            )
        oids: dict[str, str] = {}  # the listing's object ids, read as they are (#880)
        for entry in listed.stdout.split("\0"):
            meta, tab, name = entry.partition("\t")
            parts = meta.split(" ")
            if (
                tab and len(parts) == 3 and parts[0] in ("100644", "100755")
                and name.endswith(suffix)
            ):
                oids.setdefault(name, parts[2])
        names = list(oids)
        texts = self._blobs_at(rev, oids)
        for name in names:
            if name not in texts:
                logger.warning(f"{name} at {rev} is not UTF-8 text; skipped")
        return [(f"{rev}:{name}", texts[name]) for name in names if name in texts]

    def _blob_at(self, rev: str, rel: str) -> str | None:
        """The text of `rel` (from the work tree's top) as commit `rev` holds it,
        None when it holds no regular file there. Raises `_TreeUnreadableError`
        when git can't list `rev` (#814, #831)."""
        listed = self._git_z("ls-tree", "-z", "--full-tree", rev, "--", rel)
        if listed.returncode != 0:
            raise _TreeUnreadableError(
                f"Can't read {rel} at {rev}: git ls-tree failed "
                f"({listed.stderr.strip() or f'exit {listed.returncode}'})"
            )
        entry = listed.stdout.split("\0")[0]
        meta, tab, name = entry.partition("\t")
        if not tab or name != rel or meta.split(" ")[0] not in ("100644", "100755"):
            return None
        shown = subprocess.run(
            ["git", "cat-file", "blob", meta.split(" ")[2]],
            cwd=self.repo_root,
            capture_output=True,
            timeout=30,
            stdin=subprocess.DEVNULL,
            env={**os.environ, **GIT_ENV},
        )
        if shown.returncode != 0:
            raise _TreeUnreadableError(
                f"Can't read {rel} at {rev}: git cat-file failed "
                f"({shown.stderr.decode(errors='replace').strip()})"
            )
        try:
            return shown.stdout.decode("utf-8")
        except UnicodeDecodeError as e:
            raise _TreeUnreadableError(f"{rel} at {rev} is not UTF-8 text") from e

    def _items_at(self, rev: str, board_config: BoardConfig | None) -> list[WorkItem]:
        """The items of `board_config` (None: the single board) as commit `rev`
        holds them, parsed from their text as a scan parses files, ignore patterns
        applied (#574). Raises `_TreeUnreadableError` when they can't all be read
        (#814): a short list would under-count."""
        top = self._git_toplevel()
        if board_config is not None:
            roots = [_under(self.repo_root, board_config.path)]
        else:
            roots = [_under(self.repo_root, p) for p in self.config.get_work_paths()]
            roots += sorted(self._placement_dirs())
        rels = sorted({
            rel.as_posix() for root in roots
            if (rel := self._repo_relative(root, top)) is not None
        })
        if not rels:
            return []
        listed = self._git_z(  # -z: names raw, never quoted (#808)
            "ls-tree", "-r", "-z", "--full-tree", rev, "--", *rels
        )
        if listed.returncode != 0:  # an empty listing would count zero (#814)
            raise _TreeUnreadableError(
                f"Can't read the board at {rev}: git ls-tree failed "
                f"({listed.stderr.strip() or f'exit {listed.returncode}'}); "
                "refusing rather than judge an empty board"
            )
        # only regular files are items (#814): a symlink (120000) or gitlink is
        # skipped, as a scan skips it, and never read as a blob
        regular: dict[str, str] = {}
        for entry in listed.stdout.split("\0"):
            meta, tab, name = entry.partition("\t")
            parts = meta.split(" ")
            if tab and len(parts) == 3 and parts[0] in ("100644", "100755"):
                regular.setdefault(name, parts[2])
        oids: dict[str, str] = {}
        for name, oid in regular.items():
            path = top / name
            if not name.endswith(".md") or (
                self._should_ignore_for_board(path, board_config) if board_config
                else self._should_ignore(path)
            ):
                continue
            oids[name] = oid
        items: dict[str, WorkItem] = {}
        with self._scan_scope():
            for name, raw in self._blobs_at(rev, oids).items():
                item = self._parse_text(top / name, raw)
                if item is not None:
                    items[item.id] = item
        return list(items.values())

    def _blobs_at(self, rev: str, oids: dict[str, str]) -> dict[str, str]:
        """Each file of `oids` (a path from the work tree's top -> its blob's object
        id, as `_items_at`'s `ls-tree` of commit `rev` lists it, regular files only)
        as its text, as a file read with `newline=""` gives it. A non-UTF-8 file is
        left out.

        Read as the blobs themselves (#832): one `cat-file --batch` prints them,
        fed the object names from a temporary file since git never reads our stdin
        (#580). No `.gitattributes` applies, so an `export-subst` or
        `export-ignore` can't change what is counted, as they could through `git
        archive`. The object ids come from the caller's listing, so git lists the
        tree once (#880).

        Fails closed (#814): raises `_TreeUnreadableError` when git fails or any
        object can't be read — a missing file would silently not be counted."""
        import tempfile

        if not oids:
            return {}
        refusing = "refusing rather than judge a partial board"
        wanted = list(dict.fromkeys(oids.values()))
        with tempfile.TemporaryFile() as feed:
            feed.write("".join(f"{o}\n" for o in wanted).encode("ascii"))
            feed.seek(0)
            done = subprocess.run(
                ["git", "cat-file", "--batch"],
                cwd=self._git_toplevel(),
                capture_output=True,
                timeout=60,
                stdin=feed,
                env={**os.environ, **GIT_ENV},
            )
        if done.returncode != 0:
            err = done.stderr.decode("utf-8", "replace").strip() if done.stderr else ""
            raise _TreeUnreadableError(
                f"Can't read the board files at {rev}: git cat-file failed "
                f"({err or f'exit {done.returncode}'}); {refusing}"
            )
        contents: dict[str, bytes] = {}
        out, at = done.stdout, 0
        for oid in wanted:  # "<oid> blob <size>\n<content>\n", in the order asked
            end = out.find(b"\n", at)
            header = out[at:end].split(b" ") if end >= 0 else []
            if (
                len(header) != 3 or header[0] != oid.encode("ascii")
                or header[1] != b"blob" or not header[2].isdigit()
                or out[end + 1 + int(header[2]):end + 2 + int(header[2])] != b"\n"
            ):  # another object, or output cut short of its stated size (#880)
                raise _TreeUnreadableError(
                    f"Can't read the board files at {rev}: git cat-file could not "
                    f"read object {oid}; {refusing}"
                )
            size = int(header[2])
            contents[oid] = out[end + 1:end + 1 + size]
            at = end + 1 + size + 1
        blobs: dict[str, str] = {}
        for name in oids:
            with suppress(UnicodeDecodeError):
                blobs[name] = contents[oids[name]].decode("utf-8")
        return blobs

    def _fire_claim_hooks(self, claimed: _Claimed) -> None:
        """The hooks of a claim that landed (#574): STATUS_CHANGE when the status
        changed, and ASSIGNED when the holder changed (#814) — the same actor, as
        `same_actor` compares, is no change."""
        if claimed.old_status != claimed.new_status:
            self._hook_engine.trigger(
                HookEvent.STATUS_CHANGE,
                HookContext(
                    event=HookEvent.STATUS_CHANGE,
                    item_id=claimed.item_id,
                    item_type=claimed.item_type,
                    title=claimed.title,
                    old_status=claimed.old_status,
                    new_status=claimed.new_status,
                    assignee=claimed.assignee,
                ),
            )
        if claimed.old_assignee and same_actor(claimed.old_assignee, claimed.assignee):
            return
        self._hook_engine.trigger(
            HookEvent.ASSIGNED,
            HookContext(
                event=HookEvent.ASSIGNED,
                item_id=claimed.item_id,
                item_type=claimed.item_type,
                title=claimed.title,
                new_status=claimed.new_status,
                assignee=claimed.assignee,
            ),
        )

    def _fire_create_hook(self, item: WorkItem) -> None:
        """Fire on_create hooks after successful item creation."""
        self._hook_engine.trigger(
            HookEvent.ITEM_CREATED,
            HookContext(
                event=HookEvent.ITEM_CREATED,
                item_id=item.id,
                item_type=item.item_type.value,
                title=item.title,
                new_status=item.status.value,
                assignee=item.assignee,
            ),
        )

    def _hook_create_item(
        self,
        item_type: str,
        title: str,
        priority: str = "medium",
        tags: list[str] | None = None,
    ) -> dict[str, str] | None:
        """Callback for the hook ``create_item`` action.

        Creates a work item **locally only** (no git commit or push).
        This is intentional: hooks must be lightweight and side-effect-safe.
        The calling service (e.g., Bosun) is responsible for pushing if
        the item needs to reach the remote repo.

        Returns a dict with ``item_id`` and ``file_path`` on success,
        or ``None`` on failure.
        """
        if _is_cyclic(tags):
            logger.warning("Hook create_item: `tags` is cyclic (a YAML anchor that "
                           "contains itself); creating the item without tags (#262)")
            tags = None
        try:
            wit = WorkItemType.from_string(item_type)
            item = self.create_item(
                item_type=wit,
                title=title,
                priority=priority,
                tags=tags or [],
            )
            return {"item_id": item.id, "file_path": str(item.file_path)}
        except Exception as e:
            logger.warning(f"Hook create_item failed: {e}")
            return None

    def _validate_transition(self, item: WorkItem, new_status: WorkItemStatus) -> tuple[bool, str]:
        """Validate a status transition: legal per `legal_next` (the one source
        `states` and the offer render, #589), then the per-type workflow's rules.

        Returns:
            Tuple of (is_valid, error_message)
        """
        board_config, theme = self._item_theme(item)
        legal = self.legal_next(board_config, theme, item.item_type.value, item.status)
        if new_status not in legal:
            reverse = self._get_reverse_status_mapping(board_config, theme)
            from_native = reverse.get(item.status.value, item.status.value)
            to_native = reverse.get(new_status.value, new_status.value)
            targets = ", ".join(reverse.get(s.value, s.value) for s in legal) or "none"
            return False, (
                f"Illegal move {item.id}: {from_native} → {to_native}. "
                f"Legal from {from_native}: {targets}."
            )

        # legal moves still answer to a workflow's content rules (assignee, ...)
        if not self._get_board_transitions(board_config, theme):
            workflow = self._workflow_parser.load_workflow(item.item_type.value)
            if workflow:
                return self._workflow_parser.check_rules(item, new_status, workflow)
        return True, ""

    def _evaluate_gates(
        self,
        item: WorkItem,
        old_status: WorkItemStatus,
        new_status: WorkItemStatus,
        context: dict[str, Any],
    ) -> list[GateResult]:
        """Evaluate transition gates from board config.

        Returns list of GateResult objects (both passed and failed).
        Supports both v2 multi-board (per-board gates) and v1 single-board
        (top-level gates) configurations.
        """
        gate_configs = self._gate_configs(self._get_board_for_item(item))
        if not gate_configs:
            return []

        from .gates import GateEvaluator

        evaluator = GateEvaluator(gate_configs)
        return evaluator.evaluate(item, old_status.value, new_status.value, context)

    def _gate_configs(self, board_config: BoardConfig | None) -> dict[str, list[dict]]:
        """The gates a move on `board_config` answers to: the board's own (v2), else
        the top-level (v1) `gates`."""
        if board_config and board_config.gates:
            return board_config.gates
        return self.config.gates or {}

    def gate_ids(
        self, board_config: BoardConfig | None,
        from_status: WorkItemStatus, to_status: WorkItemStatus,
    ) -> list[str]:
        """Ids of the gates `move` evaluates for this transition (#573)."""
        gate_configs = self._gate_configs(board_config)
        if not gate_configs:
            return []

        from .gates import GateEvaluator

        return GateEvaluator(gate_configs).gate_ids(from_status.value, to_status.value)

    def _has_gates_configured(self, item: WorkItem) -> bool:
        """Check if any gates are configured for this item's board."""
        board_config = self._get_board_for_item(item)
        if board_config and board_config.gates:
            return True
        return bool(self.config.gates)

    def get_workflow(self, item_type: str) -> WorkflowConfig | None:
        """Get the workflow for a specific item type."""
        return self._workflow_parser.load_workflow(item_type)

    def _item_theme(self, item: WorkItem) -> tuple[BoardConfig | None, dict | None]:
        """The item's board and its theme; on a single board, the configured theme."""
        board_config = self._get_board_for_item(item)
        theme = (
            self._load_board_theme(board_config) if board_config
            else self.config.get_theme()
        )
        return board_config, theme

    def get_allowed_transitions(self, item: WorkItem) -> list[str]:
        """The statuses `item` may move to (canonical values), from `legal_next`:
        what `move` accepts, by construction (#457, #589)."""
        board_config, theme = self._item_theme(item)
        return [
            s.value
            for s in self.legal_next(board_config, theme, item.item_type.value, item.status)
        ]

    def legal_next(
        self,
        board_config: BoardConfig | None,
        theme: dict | None,
        item_type: str | None,
        from_status: WorkItemStatus,
    ) -> list[WorkItemStatus]:
        """The one lifecycle source (#589): the statuses legal from `from_status`.

        The theme's `transitions` when it has them; else the per-type workflow's
        state graph when `.kanban/workflows/` has one for `item_type` (a state the
        workflow doesn't know has no legal next: fail closed); else the default
        table. Lifecycle only: gates, WIP limits and workflow rules may still
        refuse a legal move.
        """
        board_transitions = self._get_board_transitions(board_config, theme)
        if board_transitions:
            # every name, source and target, is resolved to its status first, as
            # `move` resolves a typed name: `doing` and `wip` both mean in_progress
            # whichever wins the reverse map (#683); theme order; a name that is no
            # status is skipped (the theme load warned about it)
            names = _status_names(theme)
            allowed: list[WorkItemStatus] = []
            for source, targets in board_transitions.items():
                if names.get(_fold_status_name(str(source))) != from_status:
                    continue
                for target in targets:
                    status = names.get(_fold_status_name(target))
                    if status is not None and status not in allowed:
                        allowed.append(status)
            return allowed
        workflow = self._workflow_parser.load_workflow(item_type) if item_type else None
        if workflow:
            state = workflow.get_state(from_status.value)
            if state is None:
                return []
            targets: list[WorkItemStatus] = []
            for target in state.allowed_transitions:
                target_state = workflow.get_state(target)
                if target_state is None:
                    continue
                try:
                    status = WorkItemStatus.from_string(target_state.id)
                except ValueError:
                    continue
                if status not in targets:
                    targets.append(status)
            return targets
        return list(DEFAULT_TRANSITIONS.get(from_status, []))

    def next_statuses(self, item: WorkItem) -> list[tuple[str, str]]:
        """(canonical, native) for each status `item` may move to, in order (#573)."""
        reverse = self._item_reverse_status_mapping(item)
        return [(s, reverse.get(s, s)) for s in self.get_allowed_transitions(item)]

    def lifecycle(
        self, board_config: BoardConfig | None, item_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """`legal_next` for every status of a board (None: the single board) as
        `states` renders it: each state the theme names or the lifecycle reaches,
        with its native name, canonical name, legal next and their gate ids (#573)."""
        theme = (
            self._load_board_theme(board_config) if board_config
            else self.config.get_theme()
        )
        reverse = self._get_reverse_status_mapping(board_config, theme)
        nexts = {
            s: self.legal_next(board_config, theme, item_type, s) for s in WorkItemStatus
        }
        shown = {s for s, targets in nexts.items() if targets or s.value in reverse}
        shown.update(t for targets in nexts.values() for t in targets)
        closed = self._closed_columns(theme)
        return [
            {
                "name": reverse.get(s.value, s.value),
                "canonical": s.value,
                "terminal": not nexts[s],
                # `is_finished` for an item in this state (#575)
                "finished": s == WorkItemStatus.DONE or bool(
                    closed & {s.value, _fold_status_name(reverse.get(s.value, s.value))}
                ),
                "next": [
                    {
                        "name": reverse.get(t.value, t.value),
                        "canonical": t.value,
                        "gates": self.gate_ids(board_config, s, t),
                    }
                    for t in nexts[s]
                ],
            }
            for s in WorkItemStatus
            if s in shown
        ]

    # The opening of the status-history block this method writes: the one canonical
    # ```yurtle fence; a hand-written ```yurtle block is not history (#576)
    _HISTORY_OPEN_RE = re.compile(
        r"```yurtle\n@prefix kb: <https://yurtle\.dev/kanban/> \.\n"
        r"@prefix xsd: <http://www\.w3\.org/2001/XMLSchema#> \.\n\n<> kb:statusChange"
    )

    def _update_item_file_with_history(
        self,
        item: WorkItem,
        old_status: WorkItemStatus,
        new_status: WorkItemStatus,
        assignee: str | None = None,
        forced: bool = False,
        closed_by: str | None = None,
        gates_skipped: bool = False,
        *,
        actor: str,
        taken_over_from: str | None = None,
    ) -> None:
        """Update file and append status change to yurtle knowledge block.

        Status history is stored in TTL (Turtle RDF) format:
        ```yurtle
        @prefix kb: <https://yurtle.dev/kanban/> .
        @prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

        <> kb:statusChange [
            kb:status kb:ready ;
            kb:at "2024-01-15T10:30:00"^^xsd:dateTime ;
            kb:by "Claude-M5" ;
        ] .
        ```

        When forced=True, an additional kb:forcedMove triple is recorded.
        When closed_by is set, a kb:closedBy triple records the triggering
        artifact (e.g., a PR URL), making closure provenance graph-queryable.
        When gates_skipped=True, a kb:gatesSkipped triple is recorded.
        kb:by is the actor (who moved it), never the assignee (#580), resolved once
        by the caller (#630).
        """
        content, eol = self._read_item_text(item.file_path)
        content = self._history_text(
            content, item, new_status, assignee, actor=actor, forced=forced,
            closed_by=closed_by, gates_skipped=gates_skipped,
            taken_over_from=taken_over_from,
        )
        self._write_item_text(item.file_path, content, eol)

    def _history_text(
        self,
        content: str,
        item: WorkItem,
        new_status: WorkItemStatus,
        assignee: str | None = None,
        *,
        actor: str,
        forced: bool = False,
        closed_by: str | None = None,
        gates_skipped: bool = False,
        taken_over_from: str | None = None,
    ) -> str:
        """`content` (an item's LF text) moved to `new_status`: the frontmatter
        status and assignee edits and the status-history node that
        `_update_item_file_with_history` writes, text to text, so `claim` makes the
        same edit to a fetched file (#574). `taken_over_from` records
        `kb:takenOverFrom` (`""`: there was no holder)."""
        # Determine board-native status name (e.g., 'active' for HDD), on a single
        # board too (#439)
        native_status = self._item_reverse_status_mapping(item).get(
            new_status.value, new_status.value,
        )

        # Update frontmatter for status and assignee (use board-native name).
        # Add the key when absent — an update-only write would report a
        # move/assignment it never recorded (issue #97).
        content = self._add_or_update_frontmatter_field(content, "status", native_status)
        if assignee:
            content = self._add_or_update_frontmatter_field(
                content, "assignee", yaml_scalar(assignee),
            )

        # Create TTL status change entry (use canonical name for RDF consistency)
        timestamp = datetime.now().isoformat(timespec="seconds")
        ttl_entry = f'''    kb:status kb:{new_status.value} ;
    kb:at "{timestamp}"^^xsd:dateTime ;
    kb:by "{_turtle_string(actor)}" ;'''
        if forced:
            ttl_entry += '\n    kb:forcedMove "true"^^xsd:boolean ;'
        if gates_skipped:
            ttl_entry += '\n    kb:gatesSkipped "true"^^xsd:boolean ;'
        if closed_by:
            # Sanitize: reject characters that could break TTL string syntax
            if re.search(r'[\n\r\\" <>]', closed_by):
                raise InputRefused(
                    f"Invalid value for closed_by: contains disallowed characters: {closed_by!r}"
                )
            ttl_entry += f'\n    kb:closedBy <{closed_by}> ;'
        if taken_over_from is not None:
            ttl_entry += f'\n    kb:takenOverFrom "{_turtle_string(taken_over_from)}" ;'

        # Check if yurtle block with status changes exists
        # Match block with prefix declarations and statusChange predicates
        match = re.search(
            self._HISTORY_OPEN_RE.pattern + r"(.*?)\.\n```", content, re.DOTALL
        )

        if match:
            # Append to existing block - add new blank node
            existing = match.group(1).rstrip()
            # Remove trailing period and add comma for new entry
            new_block = f"""```yurtle
@prefix kb: <https://yurtle.dev/kanban/> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

<> kb:statusChange{existing.rstrip(" ;")},
  [
{ttl_entry}
  ] .
```"""
            content = content[: match.start()] + new_block + content[match.end() :]
        else:
            # Add new yurtle block at end
            new_block = f"""```yurtle
@prefix kb: <https://yurtle.dev/kanban/> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

<> kb:statusChange [
{ttl_entry}
  ] .
```"""
            content = content.rstrip() + "\n\n" + new_block + "\n"
        return content

    # Frontmatter is closed by the first line that STARTS with `---` (the same
    # lines the parser accepts, e.g. `--- # end`). Splitting on the substring
    # instead would cut inside a value such as `title: "A --- B"`.
    # The opening line may carry a YAML comment (`--- # generated`, #116): YAML
    # allows one after a space, not directly after `---` or after a tab. The two
    # branches can't both claim trailing spaces, so a long unclosed opener fails
    # in linear time (an optional comment followed by `[ \t\r]*` backtracked
    # quadratically).
    _FRONTMATTER_RE = re.compile(
        r"\A---(?: +#[^\n]*|[ \t\r]*)\n(.*?)^---", re.DOTALL | re.MULTILINE,
    )

    @staticmethod
    def _read_item_text(path: Path) -> tuple[str, LineEndings]:
        """Read an item file as LF text plus its line endings (#128, #151).

        `read_text()` silently turns CRLF into LF, so every edit used to rewrite a
        CRLF file as LF. Edits work on LF text; `_write_item_text` puts each
        untouched line's own ending back and gives changed lines the file's
        majority ending. A lone `\r` is a line break too, as it is for
        read_text() and the frontmatter parser; otherwise an edit's `.*` would
        span it and drop the key after it.
        """
        return LineEndings.read(path.read_bytes().decode("utf-8"))

    @staticmethod
    def _write_item_text(path: Path, text: str, eol: LineEndings | str) -> None:
        """Write LF text back with the file's line endings (#128, #151)."""
        if isinstance(eol, str):
            out = text.replace("\n", eol) if eol != "\n" else text
        else:
            out = eol.apply(text)
        path.write_bytes(out.encode("utf-8"))

    # libyaml's loader when present: the layout check parses up to three times (#249)
    _YAML_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    # a column-0 key whose value is a keep-chomping block scalar (`|+`, `>+`, `|2+`)
    # (the key may not contain `#`, so a `: |+` inside a comment doesn't count, #268)
    _KEEP_LAST = re.compile(r"^[^\s#-][^#\n]*?:[ \t]*[|>](?:\d?\+|\+\d)[ \t]*(?:#.*)?$")

    @classmethod
    def _value_preserving(cls, original: str, body: str, gap: str, line: str) -> str:
        """Place `line` before the trailing blank run (#188) or after it, where a
        `|+`/`>+` last value owns that run (#211): the first layout whose YAML keeps
        every value `original` had wins (#232). Frontmatter that doesn't parse falls
        back to looking at the last column-0 key (#249)."""
        before, after = body + line + gap, body + gap + line
        try:
            old = yaml.load(original, Loader=cls._YAML_LOADER)
        except yaml.YAMLError:
            # the last column-0 line decides: a `# comment` there ends any block
            # scalar before the blank run (#268)
            last = [ln for ln in body.splitlines() if ln[:1] not in ("", " ", "\t", "-")]
            keep = last and not last[-1].startswith("#") and cls._KEEP_LAST.match(last[-1])
            return after if keep else before
        if not isinstance(old, dict):
            return before
        for layout in (before, after):
            try:
                new = yaml.load(layout, Loader=cls._YAML_LOADER)
            except yaml.YAMLError:
                continue
            # one dict comparison: equal objects compare by identity first, so a
            # `.nan` (one shared object in PyYAML) doesn't make every layout fail
            if isinstance(new, dict) and {k: new.get(k) for k in old} == old:
                return layout
        return before

    @staticmethod
    def _trailing_comment(head: str) -> str:
        """The trailing `  # comment` of a key line's value, with the whitespace
        before it, or "" (#619). A `#` inside a quoted value or not preceded by
        whitespace isn't a comment: the text before a candidate must parse as a
        complete value. Only a head that has whitespace + `#` is parsed (#249)."""
        if not re.search(r"[ \t]#", head):
            return ""
        for candidate in re.finditer(r"[ \t]+#", head):
            try:
                yaml.load(f"k: {head[: candidate.start()]}", Loader=KanbanService._YAML_LOADER)
            except (yaml.YAMLError, TypeError, ValueError):
                continue
            return head[candidate.start() :]
        return ""

    @classmethod
    def _block_list_lines(cls, rest: str, dash: str, items: list[str]) -> str:
        """Render `items` as block-list lines (each with a leading newline) under a
        key whose old value lines are `rest` (#596, #619).

        Comment (and blank) lines in the old list are kept as written, anchored to
        the item that follows them: they go right before that item wherever it
        lands, and are dropped with it when it's removed. A kept item keeps its
        old line, trailing comment and spelling included. An old item matches by
        its text, so a `- 2026` or `- yes` YAML reads as a non-string still
        matches the string `"2026"` or `"yes"` (#639). When an item spans several
        lines or doesn't parse, every item is written fresh, keeping the comment
        lines before the first item; comments between or inside items are dropped,
        having nothing safe to anchor to (#695).
        """
        old_lines = rest.split("\n")[1:]  # `rest` starts with the newline
        lead: list[str] = []
        for line in old_lines:
            if line.strip() and not line.lstrip().startswith("#"):
                break
            lead.append(line)
        fresh = "".join(f"\n{line}" for line in lead) + "".join(
            f"\n{dash}{yaml_scalar(v)}" for v in items
        )
        entries: list[tuple[set[str], str, list[str]]] = []  # (texts, line, comments)
        pending: list[str] = []
        indent: int | None = None
        for line in old_lines:
            item = re.match(r"[ \t]*-(?:[ \t]+(.*))?$", line)
            if not line.strip() or line.lstrip().startswith("#"):
                pending.append(line)
            elif item:
                # a bare `-`, or an item deeper or shallower than the others, is
                # part of a nested block entry: no safe anchoring (#675)
                here = len(line) - len(line.lstrip())
                if not (item.group(1) or "").strip() or indent not in (None, here):
                    return fresh
                indent = here
                text = cls._strip_line_comment(item.group(1) or "")
                try:
                    parsed = yaml.load(f"k: {text}", Loader=cls._YAML_LOADER)["k"]
                except (yaml.YAMLError, TypeError, ValueError):
                    return fresh
                # the parsed text (`"a b"` -> `a b`, `2026` -> `2026`) or, for a
                # non-string, the text as written (`yes`, `null`, #639)
                keys = (
                    {parsed}
                    if isinstance(parsed, str)
                    # as written, as Python spells it, and as the reader does (#653)
                    else {str(parsed), text.strip(), str(_entry_text(parsed))}
                )
                entries.append((keys, line, pending))
                pending = []
            else:
                return fresh  # a multi-line item: no safe anchoring
        out: list[str] = []
        for value in items:
            hit = next((e for e in entries if value in e[0]), None)
            if hit is None:
                out.append(f"{dash}{yaml_scalar(value)}")
                continue
            entries.remove(hit)
            out.extend(hit[2])
            out.append(hit[1])
        return "".join(f"\n{line}" for line in out)

    @classmethod
    def _strip_line_comment(cls, text: str) -> str:
        """`text` without its trailing `  # comment` (#619)."""
        comment = cls._trailing_comment(text)
        return text[: len(text) - len(comment)] if comment else text

    def _add_or_update_frontmatter_field(
        self, content: str, field: str, value: str, items: list[str] | None = None,
    ) -> str:
        """Add or update a field in the frontmatter.

        If the field exists, update it. If not, insert it before the closing ---.
        An existing value is replaced whole: its first line plus any continuation
        lines (indented lines, or `- item` lines, which YAML allows at column 0
        under a key), so a block list or folded value leaves nothing behind (#105).
        The key may be quoted (`'title':`, `"priority":`); it is matched and kept
        as written, so an edit never appends a duplicate key (#596).

        `items`, for a list field: when the existing value is a block list, the
        list is rewritten as a block list with the same `- ` indentation instead
        of as `value` (the flow form); a flow list or a new key gets `value` (#596).
        """
        match = self._FRONTMATTER_RE.match(content)
        if not match:
            return content

        frontmatter = match.group(1)
        # A run of blank lines or column-0 `#` comments belongs to the value only
        # when a continuation line follows it (a paragraph break inside a `|`
        # block scalar, a comment inside a block list, #128); blank lines and
        # comments before the next key or the closing `---` are kept.
        key = re.escape(field)
        pattern = (
            rf"^(?P<key>{key}|'{key}'|\"{key}\")[ \t]*:(?P<head>.*)"
            r"(?P<rest>(?:(?:\n[ \t]*|\n#.*)*\n(?:[ \t]+\S.*|-(?:[ \t].*)?))*)$"
        )

        def replace(m: re.Match[str]) -> str:
            # a trailing comment on the key line is kept, with its spacing (#619)
            comment = self._trailing_comment(m.group("head"))
            if items:
                # a block list (`key:` then `- item` lines) stays a block list
                head = m.group("head")[: len(m.group("head")) - len(comment)].strip()
                # the dash of the value's FIRST item line (a bare `-` included), with
                # its own spacing, so a fresh write keeps the key's indent; only when
                # that first non-blank, non-comment line is an item (#695)
                first = next(
                    (ln for ln in m.group("rest").split("\n")
                     if ln.strip() and not ln.lstrip().startswith("#")),
                    "",
                )
                dash = re.match(r"([ \t]*-)(?:([ \t]+)(?=\S)|[ \t]*$)", first)
                if not head and dash:
                    spaced = dash.group(1) + (dash.group(2) or " ")
                    lines = self._block_list_lines(m.group("rest"), spaced, items)
                    return f"{m.group('key')}:{comment}{lines}"
            return f"{m.group('key')}: {value}{comment}"

        if re.search(pattern, frontmatter, flags=re.MULTILINE):
            # Field exists — update it
            frontmatter = re.sub(pattern, replace, frontmatter, flags=re.MULTILINE)
        else:
            # Field doesn't exist — append after the last key, keeping the blank
            # lines before the closing `---` where they are (#188). Only whole blank
            # lines are split off: trailing spaces on the last line can be part of
            # a block scalar's value (#232).
            lines = frontmatter.splitlines(keepends=True)
            k = len(lines)
            while k and not lines[k - 1].strip():
                k -= 1
            body, gap = "".join(lines[:k]), "".join(lines[k:])
            if body and not body.endswith("\n"):
                body += "\n"
            line = f"{field}: {value}\n"
            # no blank run: both layouts are the same text, nothing to parse (#249)
            frontmatter = (
                self._value_preserving(frontmatter, body, gap, line) if gap else body + line
            )

        return content[: match.start(1)] + frontmatter + content[match.end(1) :]

    @staticmethod
    def _check_text(**fields: object) -> None:
        """Refuse user text that can't be written as UTF-8 (#172)."""
        for field, value in fields.items():
            check_encodable(field, value)

    @staticmethod
    def _normalize_priority(priority: object) -> str | None:
        """Lowercase a priority and reject anything outside PRIORITIES (#125).

        Every write path (CLI, MCP, hooks, direct callers) goes through the
        service, so this is the one place priorities are validated. Reading
        stays permissive: existing items may carry legacy values (`P0`,
        `normal`, `backlog`), which still load and score.
        """
        if priority is None:
            return None
        if not isinstance(priority, str):  # e.g. `priority: 1` in a hooks config (#153)
            raise InputRefused(
                unknown_priority_message(priority)
            )
        normalized = priority.strip().lower()
        if normalized not in PRIORITIES:
            raise InputRefused(
                unknown_priority_message(priority)
            )
        return normalized

    def _apply_priority(self, content: str, priority: str | None) -> str:
        """Write the requested priority into pre-rendered template content.

        Templates hardcode a priority (or omit it), so without this a
        caller's --priority is silently dropped (issue #99). Sets the
        frontmatter field, adding it if absent, and rewrites any
        ``kb:priority`` triple the template carries so the two agree.
        """
        if not priority:
            return content
        content = self._add_or_update_frontmatter_field(content, "priority", priority)
        return re.sub(
            r"(kb:priority\s+kb:)[\w-]+", lambda m: m.group(1) + priority, content,
        )

    def _git_commit(self, file_path: Path, message: str) -> None:
        """Commit one item file; raises GitCommitError if git refuses (#584)."""
        if self._outside_repo(file_path):
            return
        self._commit_paths([file_path], message)

    def _commit_paths(self, paths: list[Path], message: str) -> bool:
        """Commit exactly `paths`, with the user's hooks (#584). True when a commit
        was made; False when nothing of ours changed (#614).

        `git commit --only` commits just these paths: whatever else the user has
        staged stays staged and out of the commit, and unstaged work is untouched.
        No change to commit is not an error. A refusal (a pre-commit hook saying no)
        raises GitCommitError carrying git's output; the edit stays in the working
        tree for the user to commit once the hook is satisfied.
        """
        rels = [str(p) for p in paths]
        add = self._git_run("add", "--", *rels)
        if add.returncode != 0:
            raise GitCommitError(f"Git commit failed ({message}): {self._git_output(add)}")
        staged = self._git_run(
            "diff", "--no-relative", "--cached", "--quiet", "HEAD", "--", *rels
        )
        if staged.returncode == 0:
            return False  # nothing of ours changed
        done = self._git_run(
            "commit", "--only", "-m", message, "--", *rels, timeout=None,
            user_locale=True,  # runs the user's pre-commit; only shown (#848)
        )
        if done.returncode != 0:
            raise GitCommitError(f"Git commit failed ({message}): {self._git_output(done)}")
        return True

    @staticmethod
    def _git_output(done: subprocess.CompletedProcess[str]) -> str:
        """What a failed git command (or the hook it ran) said, stderr and stdout."""
        said = [t.strip() for t in (done.stderr, done.stdout) if t and t.strip()]
        return "\n".join(said) or f"git exited {done.returncode}"

    @classmethod
    def _unclosed_fence_line(cls, text: str) -> int | None:
        """The 1-based line of a code fence that is never closed, else None, by the
        rules `_find_line_outside_fences` uses (#720)."""
        fence: str | None = None
        opened = 0
        for number, line in enumerate(text.split("\n"), start=1):
            marker = cls._FENCE_RE.match(line)
            if not marker:
                continue
            if fence is None:
                fence, opened = marker.group(1), number
            elif marker.group(1)[0] == fence[0] and len(marker.group(1)) >= len(fence) \
                    and not line[len(marker.group(1)):].strip():
                fence = None
        return opened if fence is not None else None

    def _check_no_comments_heading(self, text: str | None, *, rendered: bool = False) -> None:
        """Refuse text with a `## Comments` line outside fenced code: it would become
        the item's comments section (#605), on update or create (#644). The text is a
        description, or with `rendered` a templated create's whole rendered content,
        which is checked whole, since a user field can reach its body raw (#666). No shipped
        template has a `## Comments` line, so such a line always came from input.
        Refuse one with an unclosed fence too: it would swallow the status-history
        block, and the next body edit would delete the history (#720)."""
        what = "The rendered content" if rendered else "A description"
        if text is not None and (line := self._unclosed_fence_line(text)):
            # a rendered item's line counts the template's lines too: say so (#787)
            of = " of the rendered item file" if rendered else ""
            raise InputRefused(
                f"{what} can't leave a code fence open (the fence on line {line}{of} "
                "is never closed): close it, or it would swallow the status history."
            )
        if text is not None and self._find_line_outside_fences(
            text, 0, self._COMMENTS_RE
        ) >= 0:
            raise InputRefused(
                f"{what} can't contain a `## Comments` line outside a code block: "
                "that heading starts the comments section. Use add_comment for comments."
            )

    def _check_rendered(self, content: str | None) -> None:
        """The checks a templated create's content gets, run again on each re-render
        (per fetched base, #590): writable text (#641), no forged comments (#666)."""
        self._check_text(content=content)
        self._check_no_comments_heading(content, rendered=True)

    def add_comment(
        self,
        item_id: str,
        content: str,
        author: str,
        commit: bool = True,
    ) -> WorkItem:
        """Add a comment to a work item.

        Like the description, a comment's text never holds knowledge: a ```yurtle
        or ```turtle fence in it is stripped when the comment is read back (#644).
        """
        self._check_text(comment=content, author=author)
        item = self._writable_item(item_id, "a comment")  # the file now (#638, #742)

        comment = Comment(content=content, author=author)
        item.comments.append(comment)
        item.updated = datetime.now()

        # Update file (comments go in a special section)
        self._update_item_with_comment(item, comment)
        item = self._reread_item(item) or item  # the cache holds the file now (#638)

        if commit:
            self._git_commit(
                item.file_path,
                f"Add comment to {item.id}",  # the file's spelling (#751)
            )

        return item

    def _update_item_with_comment(self, item: WorkItem, comment: Comment) -> None:
        """Update item file to include new comment."""
        content, eol = self._read_item_text(item.file_path)

        # Add comment section if not exists (a `## Comments` line in a code
        # block doesn't count, #583)
        if self._find_line_outside_fences(content, 0, self._COMMENTS_RE) < 0:
            content += "\n\n## Comments\n"

        # Add comment
        timestamp = (comment.created_at or datetime.now()).strftime("%Y-%m-%d %H:%M")
        # a heading-shaped line in the text is escaped, so it can't read back as
        # a second comment; the parser unescapes it (#605)
        text = "\n".join(self._escape_comment_line(ln) for ln in comment.content.split("\n"))
        content += f"\n### {comment.author} ({timestamp})\n\n{text}\n"

        self._write_item_text(item.file_path, content, eol)

    def get_status_history(self, item_id: str) -> list[dict[str, Any]]:
        """Get status history for an item.

        Returns list of dicts: [{'status': str, 'at': datetime, 'by': str}, ...]

        Parses TTL (Turtle RDF) format in yurtle blocks:
        ```yurtle
        @prefix kb: <https://yurtle.dev/kanban/> .
        @prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

        <> kb:statusChange [
            kb:status kb:ready ;
            kb:at "2024-01-15T10:30:00"^^xsd:dateTime ;
            kb:by "Claude-M5" ;
        ] .
        ```
        """
        item = self.get_item(item_id)
        if not item:
            return []

        content = item.file_path.read_text()

        # Parse yurtle block with TTL format
        import re

        # Find yurtle blocks
        yurtle_blocks = re.findall(r"```yurtle\n(.*?)```", content, re.DOTALL)
        if not yurtle_blocks:
            return []

        history = []

        for block in yurtle_blocks:
            # Find all blank nodes with statusChange data
            # Pattern matches: kb:status kb:XXX ; kb:at "..." ; kb:by "..." ;
            # Optional: kb:forcedMove "true"^^xsd:boolean ;
            entry_pattern = (
                r'kb:status kb:(\w+)\s*;\s*'
                r'kb:at "([^"]+)"(?:\^\^xsd:dateTime)?'
                r'\s*;\s*kb:by "((?:[^"\\]|\\.)*)"'
            )
            for entry_match in re.finditer(entry_pattern, block):
                try:
                    entry: dict[str, Any] = {
                        "status": entry_match.group(1),
                        "at": datetime.fromisoformat(entry_match.group(2)),
                        "by": _turtle_unescape(entry_match.group(3)),
                        "forced": False,
                    }
                    # Check for forcedMove triple in the surrounding blank node
                    # Look ahead from the match end for kb:forcedMove within the same node
                    rest = block[entry_match.end():]
                    # The forced triple appears before the next ']' (end of blank node)
                    node_end = rest.find("]")
                    if node_end != -1:
                        node_rest = rest[:node_end]
                        if 'kb:forcedMove "true"' in node_rest:
                            entry["forced"] = True
                    history.append(entry)
                except ValueError:
                    pass

        return history

    def get_flow_metrics(self, item_id: str) -> dict[str, Any]:
        """Calculate flow metrics for an item.

        Returns:
            cycle_time: Time from in_progress to done (working time)
            lead_time: Time from ready to done (total queue + work time)
            time_in_status: Dict of status -> hours spent
        """
        history = self.get_status_history(item_id)
        if not history:
            return {"error": "No status history found"}

        metrics: dict[str, Any] = {
            "item_id": item_id,
            "transitions": len(history),
            "time_in_status": {},
            "cycle_time_hours": None,
            "lead_time_hours": None,
        }

        # Calculate time in each status
        for i, entry in enumerate(history):
            status = entry["status"]
            start_time = entry["at"]

            # End time is next transition or now
            if i + 1 < len(history):
                end_time = history[i + 1]["at"]
            else:
                end_time = datetime.now()

            hours = (end_time - start_time).total_seconds() / 3600
            metrics["time_in_status"][status] = metrics["time_in_status"].get(status, 0) + hours

        # Calculate cycle time (in_progress to done)
        in_progress_time = None
        done_time = None
        for entry in history:
            if entry["status"] == "in_progress" and in_progress_time is None:
                in_progress_time = entry["at"]
            if entry["status"] == "done":
                done_time = entry["at"]

        if in_progress_time and done_time:
            metrics["cycle_time_hours"] = (done_time - in_progress_time).total_seconds() / 3600

        # Calculate lead time (ready to done)
        ready_time = None
        for entry in history:
            if entry["status"] == "ready" and ready_time is None:
                ready_time = entry["at"]

        if ready_time and done_time:
            metrics["lead_time_hours"] = (done_time - ready_time).total_seconds() / 3600

        return metrics

    def get_board_metrics(self) -> dict[str, Any]:
        """Calculate aggregate flow metrics for the board."""
        board = self.get_board()

        total_cycle_time = 0
        total_lead_time = 0
        cycle_time_count = 0
        lead_time_count = 0
        total_time_in_status: dict[str, float] = {}

        for item in board.items:
            metrics = self.get_flow_metrics(item.id)
            if "error" not in metrics:
                if metrics["cycle_time_hours"]:
                    total_cycle_time += metrics["cycle_time_hours"]
                    cycle_time_count += 1
                if metrics["lead_time_hours"]:
                    total_lead_time += metrics["lead_time_hours"]
                    lead_time_count += 1
                for status, hours in metrics.get("time_in_status", {}).items():
                    total_time_in_status[status] = total_time_in_status.get(status, 0) + hours

        return {
            "total_items": len(board.items),
            "items_with_history": cycle_time_count,
            "avg_cycle_time_hours": total_cycle_time / cycle_time_count
            if cycle_time_count
            else None,
            "avg_lead_time_hours": total_lead_time / lead_time_count if lead_time_count else None,
            "total_time_in_status": total_time_in_status,
        }

    def get_blocked_items(self) -> list[WorkItem]:
        """Get all blocked items."""
        return self.get_items(status=WorkItemStatus.BLOCKED)

    def get_my_items(self, assignee: str) -> list[WorkItem]:
        """Get items assigned to a specific person."""
        return self.get_items(assignee=assignee)

    # --- finished, the dependency walk and pickable (#575) -----------------------

    def _native_status(self, item: WorkItem, theme: dict | None) -> str:
        """The status name `item`'s file gives, when it is one of its theme's names for
        its status; else its theme's name for that status (`status_label`)."""
        original = item.metadata.get("_original_status")
        if isinstance(original, str) and (
            _status_names(theme).get(_fold_status_name(original)) == item.status
        ):
            return original
        return self.status_label(item)

    @staticmethod
    def _closed_columns(theme: dict | None) -> set[str]:
        """The folded names of `theme`'s columns marked `closed: true` (#575)."""
        columns = (theme or {}).get("columns") or {}
        return {_fold_status_name(k) for k, d in columns.items() if d.get("closed") is True}

    def is_finished(self, item: WorkItem) -> bool:
        """Canonical done, or a native status whose theme column is marked
        `closed: true`, e.g. hdd `abandoned` (#575)."""
        if item.status == WorkItemStatus.DONE:
            return True
        _, theme = self._item_theme(item)
        closed = self._closed_columns(theme)
        native = _fold_status_name(self._native_status(item, theme))
        return bool(closed & {item.status.value, native})

    def _dep_index(self) -> dict[str, WorkItem]:
        """Every board's items by folded ID (`fold_id`): where a dependency is found."""
        if not self._items:
            self.scan()
        return {fold_id(i.id): i for i in self._items.values()}

    def _dep_index_at(self, read: Read) -> dict[str, WorkItem]:
        """`_dep_index` in `read`'s tree: every board at the fetched commit, as its
        own config places them (`_judge_at`), else the working tree's. Raises
        `_TreeUnreadableError` when that tree can't be read."""
        if read.rev is None:
            return self._dep_index()
        judge = self._judge_at(read.rev)
        boards = judge.config.boards if judge.config.is_multi_board else [None]
        return {fold_id(i.id): i for b in boards for i in judge._items_at(read.rev, b)}

    def _dep_graph(self, index: dict[str, WorkItem]) -> dict[str, list[str]]:
        """`dependency_graph` over `index`'s items."""
        return {key: self._id_list(item.depends_on) for key, item in index.items()}

    def _dep_state(
        self, dep_id: str, index: dict[str, WorkItem], graph: dict[str, list[str]]
    ) -> tuple[str, WorkItem | None, list[str] | None]:
        """(state, the item, the cycle when `cycle`) of dependency `dep_id`."""
        key = fold_id(dep_id.strip())
        dep = index.get(key)
        if dep is None:
            return "unknown", None, None
        if self.is_finished(dep):
            return ("met" if dep.status == WorkItemStatus.DONE else "dead"), dep, None
        cycle = self.find_cycle(key, graph)  # #576's walk; cycle beats unfinished
        if cycle:
            return "cycle", dep, cycle
        return "unfinished", dep, None

    def dependency_state(self, dep_id: str) -> str:
        """`met` (finished and done), `dead` (finished, not done: hdd `abandoned`),
        `unknown` (on no board), `cycle` (on a dependency cycle) or `unfinished`,
        looked up across every board, IDs folded (#575)."""
        index = self._dep_index()
        return self._dep_state(dep_id, index, self._dep_graph(index))[0]

    def unmet_dependencies(self, item: WorkItem) -> list[DepNode]:
        """`item`'s dependencies that are not `met`, each with its own unmet ones
        below it (#575). Cycle-safe: a node already on the path is listed, not
        walked again. #577 renders this; it adds no logic of its own."""
        index = self._dep_index()
        return self._unmet(item, index, self._dep_graph(index), {fold_id(item.id)})

    def _unmet(
        self, item: WorkItem, index: dict[str, WorkItem],
        graph: dict[str, list[str]], path: set[str],
    ) -> list[DepNode]:
        nodes = []
        for dep_id in self._id_list(item.depends_on):
            state, dep, _ = self._dep_state(dep_id, index, graph)
            if state == "met":
                continue
            node = DepNode(
                id=dep.id if dep else dep_id,
                status=self.status_label(dep) if dep else None,
                assignee=dep.assignee if dep else None,
                state=state,
            )
            if dep is not None and state in ("unfinished", "cycle") and dep_id not in path:
                node.children = self._unmet(dep, index, graph, path | {dep_id})
            nodes.append(node)
        return nodes

    def pickable(self, item: WorkItem, actor: str | None) -> tuple[bool, str]:
        """Whether `actor` (None: no identity) may pick `item` up, and why not (#575).
        The one predicate `claim`, `claim --next`, `next`, `list --pickable` and
        MCP `kanban_suggest_next` share. Clauses in order, the first failing one's
        reason returned:

        1. canonical status `ready` (hdd has none: never pickable);
        2. no assignee, or `actor` is it (`same_actor`);
        3. every `depends_on` is `met`.
        """
        return self._pickable(item, actor)

    def _pickable(
        self, item: WorkItem, actor: str | None, *, take_over: bool = False,
        index: dict[str, WorkItem] | None = None,
        graph: dict[str, list[str]] | None = None,
    ) -> tuple[bool, str]:
        """`pickable`, with dependencies looked up in `index` (default: every board
        here). `take_over` (`claim --take-over`) skips clause 2 and accepts an
        in-progress item under clause 1 (#575 [steer] 2)."""
        allowed = {WorkItemStatus.READY}
        if take_over:
            allowed.add(WorkItemStatus.IN_PROGRESS)
        if item.status not in allowed:
            return False, f"status {self.status_label(item)} is not a ready status"
        held = item.assignee
        holder = (held if isinstance(held, str) else str(held or "")).strip()
        if holder and not take_over and not (actor and same_actor(holder, actor)):
            return False, f"held by {holder}"
        deps = self._id_list(item.depends_on)
        if deps:
            index = self._dep_index() if index is None else index
            graph = self._dep_graph(index) if graph is None else graph
            for dep_id in deps:
                state, dep, cycle = self._dep_state(dep_id, index, graph)
                if state == "met":
                    continue
                detail = {
                    "unfinished": "unfinished",
                    "unknown": "unknown ID",
                    "dead": f"dead: {self.status_label(dep)}" if dep else "dead",
                    "cycle": f"cycle: {' → '.join(cycle or [])}",
                }[state]
                return False, f"waiting on {dep.id if dep else dep_id} ({detail})"
        return True, "pickable"

    @staticmethod
    def pick_order(item: WorkItem) -> tuple[bool, int, int, int, str]:
        """The pick order (#575 §5): `priority_rank` ascending (unranked last), then
        priority descending, then the oldest numeric ID, then the ID."""
        rank = item.priority_rank
        return (
            rank is None, rank if rank is not None else 0,
            -item.priority_score, item.numeric_id, item.id,
        )

    def pick_report(
        self, actor: str | None, items: list[WorkItem] | None = None
    ) -> tuple[list[WorkItem], list[tuple[WorkItem, str]]]:
        """(the pickable items, each canonical-`ready` item that is not with its
        reason), both in pick order, of `items` (default: every board) (#575)."""
        index = self._dep_index()
        graph = self._dep_graph(index)
        picks: list[WorkItem] = []
        refused: list[tuple[WorkItem, str]] = []
        for item in sorted(self.get_items() if items is None else items, key=self.pick_order):
            ok, reason = self._pickable(item, actor, index=index, graph=graph)
            if ok:
                picks.append(item)
            elif item.status == WorkItemStatus.READY:
                refused.append((item, reason))
        return picks, refused

    @staticmethod
    def _as_datetime(value: Any) -> datetime | None:
        """A frontmatter date or datetime (or ISO text) as a naive UTC datetime."""
        if isinstance(value, str):
            try:
                value = datetime.fromisoformat(value.strip())
            except ValueError:
                return None
        if isinstance(value, datetime):
            if value.tzinfo is not None:
                value = value.astimezone(timezone.utc).replace(tzinfo=None)
            return value
        if isinstance(value, date):
            return datetime.combine(value, datetime.min.time())
        return None

    def _entered_order(self, item: WorkItem) -> tuple[bool, datetime, int, str]:
        """Oldest first by when `item` entered its current status (#575 [steer] 3):
        its status history (`kb:at`), else frontmatter `updated:`, else `created:`,
        then the numeric ID. `WorkItem.updated` is only the parse time."""
        names = self.legal_status_names(item)
        entered = [
            at for entry in self.get_status_history(item.id)
            if names.get(_fold_status_name(str(entry["status"]))) == item.status
            and (at := self._as_datetime(entry["at"])) is not None
        ]
        when = max(entered) if entered else (
            self._as_datetime(item.metadata.get("updated")) or self._as_datetime(item.created)
        )
        return (when is None, when or datetime.min, item.numeric_id, item.id)

    def next_item(self, actor: str | None) -> tuple[WorkItem, str, str] | None:
        """(item, kind, reason) for `next` (#575 §6), or None: `actor`'s own
        in-progress item first (`resume`, the one that entered in progress first;
        `review` waits on a reviewer), else the first pickable item (`pick`).
        Offline and advisory: `claim` is the authoritative gate."""
        if actor:
            own = [
                i for i in self.get_items(status=WorkItemStatus.IN_PROGRESS)
                if isinstance(i.assignee, str) and i.assignee.strip()
                and same_actor(i.assignee, actor)
            ]
            if own:
                return min(own, key=self._entered_order), "resume", "resume your in-progress item"
        picks, _ = self.pick_report(actor)
        if not picks:
            return None
        top = picks[0]
        rank = f"rank {top.priority_rank}, " if top.priority_rank is not None else ""
        return top, "pick", f"top pickable: {rank}priority {top.priority or 'medium'}"

    def update_item(
        self,
        item_id: str,
        title: str | None = None,
        priority: str | None = None,
        assignee: str | None = None,
        description: str | None = None,
        tags: list[str] | None = None,
        commit: bool = True,
        message: str | None = None,
        *,
        add_tags: list[str] | None = None,
        remove_tags: list[str] | None = None,
        depends_on: list[str] | None = None,
        add_depends_on: list[str] | None = None,
        remove_depends_on: list[str] | None = None,
        related: list[str] | None = None,
        allow_unknown: bool = False,
    ) -> WorkItem:
        """Update a work item's properties (not status - use move_item for that).

        Args:
            item_id: The work item ID to update
            title: New title (optional)
            priority: New priority level (optional)
            assignee: New assignee (optional)
            description: New description (optional)
            tags: New tags list (optional, replaces existing)
            commit: Whether to git commit the change
            message: Optional commit message
            add_tags ... allow_unknown: the tag and dependency edits of
                `update_item_changes`
        """
        item, _ = self.update_item_changes(
            item_id, title=title, priority=priority, assignee=assignee,
            description=description, tags=tags, add_tags=add_tags,
            remove_tags=remove_tags, depends_on=depends_on,
            add_depends_on=add_depends_on, remove_depends_on=remove_depends_on,
            related=related, allow_unknown=allow_unknown, commit=commit, message=message,
        )
        return item

    def update_item_changes(
        self,
        item_id: str,
        *,
        title: str | None = None,
        priority: str | None = None,
        assignee: str | None = None,
        description: str | None = None,
        tags: list[str] | None = None,
        add_tags: list[str] | None = None,
        remove_tags: list[str] | None = None,
        depends_on: list[str] | None = None,
        add_depends_on: list[str] | None = None,
        remove_depends_on: list[str] | None = None,
        related: list[str] | None = None,
        allow_unknown: bool = False,
        commit: bool = True,
        message: str | None = None,
    ) -> tuple[WorkItem, list[str]]:
        """`update_item`, also returning what changed, as the commit message names it
        (`["depends_on +EXP-3 -EXP-2", "priority high"]`; empty for a no-op).

        Lists: `tags` / `depends_on` replace the list, then `add_*` append what is
        missing (in order) and `remove_*` drop. Dependency and related IDs are
        upper-cased, and compared upper-cased with the file's, so an entry equal
        ignoring case is no change (#576, #721). A blank tag is refused (#721), and so
        is an item whose ID is on more than one board (#721). A dependency this edit
        adds is refused when it is the item itself, an ID on more than one board, an
        ID on no board (unless `allow_unknown`), or a step on a path back to this item
        (a cycle). Every refusal is a ValueError raised before anything is written.
        """
        edits = self._checked_edits(_Edits(
            title=title, priority=priority, assignee=assignee, description=description,
            tags=tags, add_tags=add_tags, remove_tags=remove_tags, depends_on=depends_on,
            add_depends_on=add_depends_on, remove_depends_on=remove_depends_on,
            related=related, allow_unknown=allow_unknown,
        ))
        if edits.editing_deps:
            self.scan()  # the whole graph as the files say now (#638)
        item = self._writable_item(item_id, "an update")

        content, eol = self._read_item_text(item.file_path)
        content, changes = self._edited_text(item, content, edits)
        if not changes:
            return item, []  # Nothing to update: no write, no commit

        self._write_item_text(item.file_path, content, eol)
        item = self._reread_item(item) or item  # the cache holds the file now (#638)

        # Git commit if requested: the item file only (#584)
        if commit:
            self._git_commit(
                item.file_path,
                message or f"Update {item.id}: {', '.join(changes)}",  # (#751)
            )

        return item, changes

    def _checked_edits(self, edits: _Edits) -> _Edits:
        """`edits` with its priority normalized, after the checks that need no item
        (#576, #721): a bad priority, title or text, a `## Comments` heading in the
        body, a blank tag. Raises InputRefused."""
        priority = self._normalize_priority(edits.priority)
        if edits.title is not None:
            self._check_title(edits.title)
        self._check_text(
            title=edits.title, description=edits.description, assignee=edits.assignee,
            tags=edits.tags, add_tags=edits.add_tags, remove_tags=edits.remove_tags,
            depends_on=edits.depends_on, add_depends_on=edits.add_depends_on,
            remove_depends_on=edits.remove_depends_on, related=edits.related,
        )
        self._check_no_comments_heading(edits.description)
        for tag in [*(edits.tags or []), *(edits.add_tags or [])]:
            if not str(tag).strip():
                raise InputRefused("A tag is empty: give a tag")
        return replace(edits, priority=priority)

    def _edited_text(
        self,
        item: WorkItem,
        content: str,
        edits: _Edits,
        board: tuple[dict[str, list[str]], dict[str, list[Path]]] | None = None,
    ) -> tuple[str, list[str]]:
        """`content`, the LF text of `item`'s file, with `edits` applied, and the
        changes as the commit message names them (empty when nothing changed). A
        pure text-to-text edit: nothing is read or written (#574 §5). New
        dependencies are checked against `board`, a (dependency graph, duplicated
        IDs) pair, else against the scanned board (#576). Raises InputRefused.

        Field-level edits only, like rank_item (#583): every line the update
        doesn't touch - unknown keys, the native status, the history block,
        comments - stays byte-for-byte."""
        new_tags: list[str] | None = None
        if edits.tags is not None or edits.add_tags or edits.remove_tags:
            new_tags = list(item.tags if edits.tags is None else edits.tags)
            new_tags += [t for t in dict.fromkeys(edits.add_tags or []) if t not in new_tags]
            new_tags = [t for t in new_tags if t not in (edits.remove_tags or [])]
        new_deps: list[str] | None = None
        if edits.editing_deps:
            new_deps = self._id_list(
                item.depends_on if edits.depends_on is None else edits.depends_on
            )
            new_deps += [
                d for d in self._id_list(edits.add_depends_on or []) if d not in new_deps
            ]
            dropped = set(self._id_list(edits.remove_depends_on or []))
            new_deps = [d for d in new_deps if d not in dropped]
            self._check_new_dependencies(item, new_deps, edits.allow_unknown, board)
        new_related = None if edits.related is None else self._id_list(edits.related)

        original = content
        changes: list[str] = []
        title, priority, assignee = edits.title, edits.priority, edits.assignee

        if title is not None and title != item.title:
            content = self._add_or_update_frontmatter_field(content, "title", yaml_quote(title))
            content = self._replace_h1(content, title)
            changes.append("title")

        if priority is not None and priority != item.priority:
            content = self._add_or_update_frontmatter_field(content, "priority", priority)
            changes.append(f"priority {priority}")

        if assignee is not None and assignee != item.assignee:
            content = self._add_or_update_frontmatter_field(
                content, "assignee", yaml_scalar(assignee) if assignee else "null"
            )
            changes.append("assignee")

        if edits.description is not None:
            updated = self._replace_body(content, edits.description)
            if updated != content:
                content = updated
                changes.append("description")

        for key, old, new in (
            ("tags", item.tags, new_tags),
            ("depends_on", item.depends_on, new_deps),
            ("related", item.related, new_related),
        ):
            same = old == new if key == "tags" else self._id_list(old) == new
            if new is not None and not same:
                content = self._add_or_update_frontmatter_field(
                    content, key, yaml_flow_list(new), items=new
                )
                changes.append(self._list_change(key, old, new))

        if not changes or content == original:
            return original, []
        return content, changes

    def update_item_push(
        self,
        item_id: str,
        *,
        title: str | None = None,
        priority: str | None = None,
        assignee: str | None = None,
        description: str | None = None,
        tags: list[str] | None = None,
        add_tags: list[str] | None = None,
        remove_tags: list[str] | None = None,
        depends_on: list[str] | None = None,
        add_depends_on: list[str] | None = None,
        remove_depends_on: list[str] | None = None,
        related: list[str] | None = None,
        allow_unknown: bool = False,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[float, float], float] = random.uniform,
        seam: Callable[[int], None] | None = None,
    ) -> Outcome:
        """`update_item_changes`' edits as one compare-and-swap commit on origin's
        default branch (#574 §5), applied to the item as the FETCHED tree has it,
        so a rival's change to another line survives and a rival's edit of the
        same field is overwritten (last writer wins). Dependencies are checked
        against the fetched board. Refusals are `refused` (there is no holder to
        lose to); an edit the fetched item already has is `noop`. `seam`, `sleep`
        and `jitter` are `sync_and_push`'s."""
        try:
            edits = self._checked_edits(_Edits(
                title=title, priority=priority, assignee=assignee, description=description,
                tags=tags, add_tags=add_tags, remove_tags=remove_tags, depends_on=depends_on,
                add_depends_on=add_depends_on, remove_depends_on=remove_depends_on,
                related=related, allow_unknown=allow_unknown,
            ))
        except ValueError as e:
            return Outcome("refused", str(e))

        def mutate(read: Read, attempt: int) -> Change | NoOp | Refuse:
            return self._update_change(read, item_id, edits)

        outcome = self.sync_and_push(mutate, sleep=sleep, jitter=jitter, seam=seam)
        if outcome.kind == "local":  # the working tree changed under the cache
            self._items.clear()
            self._board = None
        return outcome

    def _update_change(self, read: Read, item_id: str, edits: _Edits) -> Change | NoOp | Refuse:
        """`update --push`'s `mutate` (#574 §5): `edits` applied to the item as
        `read`'s tree has it, new dependencies checked against that tree's board."""
        if read.rev is None and edits.editing_deps:
            self.scan()  # the whole graph as the files say now (#638)
        found = self._item_target(read, item_id, "an update")
        if isinstance(found, Refuse):
            return found
        rel, text, item = found
        try:
            board = (
                self._dependency_board_at(read.rev)
                if edits.editing_deps and read.rev is not None else None
            )
            new_text, changes = self._edited_text(item, text, edits, board)
        except ValueError as e:
            return Refuse(str(e))
        if not changes:
            return NoOp(f"{item.id} already says so: no changes")
        return Change({rel: new_text}, f"Update {item.id}: {', '.join(changes)}")  # (#751)

    def _dependency_board_at(
        self, rev: str
    ) -> tuple[dict[str, list[str]], dict[str, list[Path]]]:
        """(`dependency_graph`, `duplicate_ids`) as commit `rev` has the boards
        (#574, #576): what `_check_new_dependencies` checks a pushed edit against,
        the boards as `rev`'s own config places them (`_judge_at`, #831)."""
        if (judge := self._judge_at(rev)) is not self:
            return judge._dependency_board_at(rev)
        configs = list(self.config.boards) if self.config.is_multi_board else [None]
        # origin can't hold a board outside the repository: its items are the
        # working tree's, beside `rev`'s in-repo ones (#833)
        outside = self._items_outside_repo()
        graph = {
            fold_id(i.id): self._id_list(i.depends_on)
            for c in configs for i in self._items_at(rev, c)
        }
        graph.update((fold_id(i.id), self._id_list(i.depends_on)) for i in outside)
        top = self._git_toplevel()
        files: dict[str, list[Path]] = {}
        found_at = [(top / path, found) for path, found in self._ids_at(rev)[1]]
        seen: dict[str, set[Path]] = {}
        for path, found in [*found_at, *((i.file_path, i.id) for i in outside)]:
            # one file however it is reached (`/tmp` vs `/private/tmp`, a
            # symlinked board), as the scan compares (#879)
            real = path.resolve()
            if real in seen.setdefault(fold_id(found), set()):
                continue
            seen[fold_id(found)].add(real)
            files.setdefault(fold_id(found), []).append(path)
        return graph, {i: f for i, f in files.items() if len(f) > 1}

    def _items_outside_repo(self) -> list[WorkItem]:
        """The working tree's items on boards that lie outside the git repository
        (#833), each board applying its own ignore patterns as a scan does."""
        items: list[WorkItem] = []
        with self._scan_scope():
            if self.config.is_multi_board:
                for board in self.config.boards:
                    if self._outside_git(_under(self.repo_root, board.path)):
                        items += self._scan_board(board)
            else:
                for p in self.config.get_work_paths():
                    root = _under(self.repo_root, p)
                    if root.exists() and self._outside_git(root):
                        items += self._scan_directory(root)
        return items

    @staticmethod
    def _check_title(title: str) -> None:
        """Refuse a title that is empty, blank or more than one line (#576)."""
        if not title.strip():
            raise InputRefused("The title is empty: give a title")
        if "\n" in title or "\r" in title:
            raise InputRefused("The title has a line break: a title is one line")

    @staticmethod
    def _check_prefix(prefix: str) -> str:
        """`prefix`, NFC-normalized; refused when no ID could have it (#802, #817).
        One grammar, shared with theme loading (#816)."""
        normal = id_prefix(prefix)
        if normal is None:
            raise InputRefused(f"{prefix!r} is not an ID prefix: a prefix is {ID_PREFIX_FORM}")
        return normal

    @staticmethod
    def _id_list(ids: list[Any]) -> list[str]:
        """Item IDs as written and looked up: stripped, folded (`fold_id`), blanks
        and repeats dropped, order kept (#576, #817). A bare string is refused: it would be
        written as a list of its characters (#719)."""
        if isinstance(ids, str):
            raise ValueError(f"expected a list of item IDs, got the string {ids!r}")
        return list(dict.fromkeys(s for i in ids if (s := fold_id(str(i).strip()))))

    @staticmethod
    def _list_change(key: str, old: list[Any], new: list[str]) -> str:
        """`depends_on +EXP-3 -EXP-2`: a list edit as the commit message names it;
        just `key` when nothing changed. IDs compare folded; tags as written."""
        norm = str if key == "tags" else fold_id
        old_ids = [str(i) for i in old]
        old_keys = {norm(i) for i in old_ids}
        new_keys = {norm(i) for i in new}
        edits = [f"+{i}" for i in new if norm(i) not in old_keys]
        edits += [f"-{i}" for i in old_ids if norm(i) not in new_keys]
        return " ".join([key, *edits])

    def _check_new_dependencies(
        self,
        item: WorkItem,
        new_deps: list[str],
        allow_unknown: bool,
        board: tuple[dict[str, list[str]], dict[str, list[Path]]] | None = None,
    ) -> None:
        """Refuse the targets this edit adds to `item.depends_on` (#576): the item
        itself, an ID on more than one board, an ID on no board (unless
        `allow_unknown`), or one that leads back to the item. Edges the item already
        has are not re-checked, so an unrelated edit on an item already in a cycle,
        or with a dangling target, still goes through. `board` is the (dependency
        graph, duplicated IDs) to check against, e.g. a fetched commit's (#574);
        default the scanned board's."""
        me = fold_id(item.id)
        had = set(self._id_list(item.depends_on))
        added = [d for d in new_deps if d not in had]
        if not added:
            return
        if board is None:
            graph = self.dependency_graph()
            duplicated = {fold_id(i): files for i, files in self.duplicate_ids.items()}
        else:
            graph, duplicated = dict(board[0]), board[1]
        for target in added:
            if target == me:
                raise InputRefused(f"{me} can't depend on itself")
            if target in duplicated:
                where = ", ".join(self._display_path(f) for f in duplicated[target])
                raise InputRefused(
                    f"{target} is on more than one board ({where}): a dependency on it "
                    "is ambiguous; fix the duplicate ID first"
                )
            if target not in graph and not allow_unknown:
                raise InputRefused(
                    f"{target} is on no board: check the ID, or allow an item outside "
                    "this repo with --allow-unknown"
                )
        graph[me] = new_deps
        cycle = self.find_cycle(me, graph, via=added)
        if cycle:
            raise InputRefused(
                f"{me} can't depend on {cycle[1]}: it closes a dependency "
                f"cycle: {' → '.join(cycle)}"
            )

    def _display_path(self, path: Path) -> str:
        """`path` relative to the repo when it is inside it."""
        with suppress(ValueError):
            return path.relative_to(self.repo_root).as_posix()
        return str(path)

    def dependency_graph(self) -> dict[str, list[str]]:
        """Each item's `depends_on` targets, keyed by item ID, over every board (#576).

        IDs are folded (`fold_id`). A target on no board is kept in its item's list and has
        no key of its own. Reused by `find_cycle`, `validate`, #575 and #577.
        """
        if not self._items:
            self.scan()
        return {
            fold_id(item.id): self._id_list(item.depends_on) for item in self._items.values()
        }

    def find_cycle(
        self,
        start: str,
        graph: dict[str, list[str]] | None = None,
        *,
        via: list[str] | None = None,
    ) -> list[str] | None:
        """A dependency path from `start` back to itself, as
        `["EXP-1", "EXP-3", "EXP-1"]`, or None (#576).

        `graph` defaults to `dependency_graph()`. `via` limits the first step to
        these targets, so an edit checks only the cycles its new edges close. A cycle
        elsewhere in the graph, not through `start`, is not reported.
        """
        graph = self.dependency_graph() if graph is None else graph
        start = fold_id(start)
        dead: set[str] = set()  # nodes with no path back to `start`
        for first in graph.get(start, []) if via is None else self._id_list(via):
            path = self._path_back(graph, first, start, dead)
            if path is not None:
                return [start, *path]
        return None

    @staticmethod
    def _path_back(
        graph: dict[str, list[str]], source: str, target: str, dead: set[str]
    ) -> list[str] | None:
        """A path `[source, ..., target]` along dependency edges, or None; `dead`
        collects the nodes found to have none (iterative DFS: no recursion limit)."""
        if source == target:
            return [source]
        if source in dead:
            return None
        dead.add(source)
        path = [source]
        stack = [iter(graph.get(source, []))]
        while stack:
            step = next(stack[-1], None)
            if step is None:
                stack.pop()
                path.pop()
            elif step == target:
                return [*path, step]
            elif step not in dead:
                dead.add(step)
                path.append(step)
                stack.append(iter(graph.get(step, [])))
        return None

    def dependency_cycles(self) -> list[list[str]]:
        """Dependency cycles, each once, starting at its smallest ID (#576).

        `find_cycle` gives one cycle per start node, so a node on two cycles may
        report only one of them: every node on some cycle is on a reported one,
        but not every cycle is reported (#721).
        """
        graph = self.dependency_graph()
        cycles: dict[tuple[str, ...], list[str]] = {}
        for node in sorted(graph):
            cycle = self.find_cycle(node, graph)
            if cycle:
                ring = cycle[:-1]
                first = ring.index(min(ring))
                ring = ring[first:] + ring[:first]
                cycles.setdefault(tuple(ring), [*ring, ring[0]])
        return list(cycles.values())

    def dangling_dependencies(self) -> list[tuple[str, str]]:
        """`(item, target)` for each `depends_on` target that is on no board (#576)."""
        graph = self.dependency_graph()
        return [
            (item_id, dep)
            for item_id, deps in sorted(graph.items())
            for dep in deps
            if dep not in graph
        ]

    @classmethod
    def _body_start(cls, content: str) -> int:
        """Offset just past the frontmatter's closing `---` line (0 without frontmatter)."""
        match = cls._FRONTMATTER_RE.match(content)
        if not match:
            return 0
        eol = content.find("\n", match.end())
        return len(content) if eol < 0 else eol + 1

    _FENCE_RE = re.compile(r"^(`{3,}|~{3,})")

    @classmethod
    def _find_line_outside_fences(
        cls,
        content: str,
        start: int,
        pattern: re.Pattern[str],
        block: re.Pattern[str] | None = None,
    ) -> int:
        """Offset of the first line at or after `start` that matches `pattern` and is
        not inside a fenced code block, or -1 (#583). A matching fence opener counts.
        With `block`, the text from that line on must also match it (#576)."""
        fence: str | None = None  # the open fence's marker, e.g. "```"
        pos = start
        while pos < len(content):
            eol = content.find("\n", pos)
            end = len(content) if eol < 0 else eol
            line = content[pos:end]
            if fence is None and pattern.match(line) and (
                block is None or block.match(content, pos)
            ):
                return pos
            marker = cls._FENCE_RE.match(line)
            if marker:
                if fence is None:
                    fence = marker.group(1)
                elif marker.group(1)[0] == fence[0] and len(marker.group(1)) >= len(fence) \
                        and not line[len(marker.group(1)):].strip():
                    fence = None  # a closing fence carries no info string
            pos = end + 1
        return -1

    _KNOWLEDGE_OPEN_RE = re.compile(r"```(?:yurtle|turtle)")

    def _leading_knowledge_end(self, content: str) -> int:
        """Offset just past the last ```yurtle/```turtle block that opens the body
        (blank lines between blocks allowed), else just past the frontmatter. These
        are the blocks `_extract_description` discards before it looks for the H1
        (#583); a block runs to the next ``` the way the parser's regex does."""
        pos = end_of_blocks = self._body_start(content)
        while pos < len(content):
            eol = content.find("\n", pos)
            end = len(content) if eol < 0 else eol
            if not content[pos:end].strip():
                pos = end + 1
                continue
            if not self._KNOWLEDGE_OPEN_RE.match(content, pos):
                break
            close = content.find("```", pos + 3)
            if close < 0:
                break  # unclosed: the parser keeps it, so it's body
            eol = content.find("\n", close)
            pos = end_of_blocks = len(content) if eol < 0 else eol + 1
        return end_of_blocks

    def _h1_span(self, content: str) -> tuple[int, int] | None:
        """The `# Title` line: the first non-blank line after the frontmatter and any
        leading knowledge blocks, and only when it is a level-1 heading (#583).
        Never a `# ` line inside code."""
        pos = self._leading_knowledge_end(content)
        while pos < len(content):
            eol = content.find("\n", pos)
            end = len(content) if eol < 0 else eol
            if content[pos:end].strip():
                return (pos, end) if content.startswith("# ", pos) else None
            pos = end + 1
        return None

    def _replace_h1(self, content: str, title: str) -> str:
        """Rewrite only the `# Title` line after the frontmatter (#583); without one,
        nothing after the frontmatter changes."""
        span = self._h1_span(content)
        if span is None:
            return content
        # one line: a newline in the title would end the heading
        heading = "# " + " ".join(title.splitlines())
        return content[: span[0]] + heading + content[span[1] :]

    _COMMENTS_RE = re.compile(r"## Comments[ \t]*$")
    # The body ends at the `## Comments` line or the canonical status-history
    # block, outside fenced code (#576): the line test, then the text from there on
    _BODY_END_LINE_RE = re.compile(r"(?:```yurtle|## Comments)")
    _BODY_END_RE = re.compile(
        r"## Comments[ \t]*(?:\n|\Z)|" + _HISTORY_OPEN_RE.pattern
    )

    @classmethod
    def body_span(cls, text: str) -> tuple[int, int]:
        """The item body in `text` (an item file as LF text), as `(start, end)`
        offsets (#576): from just past the frontmatter's closing `---` line to the
        first `## Comments` line or canonical status-history ```yurtle block outside
        fenced code, else the end of the text. The H1 and any hand-written knowledge
        block are inside the span; the history block and comments are not. The one
        definition of "body", for `update --body` and #578's body hash.
        """
        start = cls._body_start(text)
        end = cls._find_line_outside_fences(
            text, start, cls._BODY_END_LINE_RE, cls._BODY_END_RE
        )
        return start, len(text) if end < 0 else end

    def swallowed_fence_line(self, content: str) -> int | None:
        """The file line of a body code fence that runs over what follows the body,
        else None (#727). `content` is LF text. Two cases count:

        - the canonical status-history opener lies inside `body_span` (normally
          only an unclosed fence lets the span run past it);
        - the span holds a never-closed fence with a real `## Comments` line after
          it (comments with no history yet).

        A `## Comments` line quoted inside a CLOSED fence is body, not a swallow.
        Two refusals are conservative, and safe: a body ending in an unclosed fence
        that quotes `## Comments` with no real comments, and a closed fence quoting
        the full canonical history opener. Both are refused (and reported by
        `validate`) until the fence is closed or the quote reworded (#736)."""
        start = self._body_start(content)
        span_end = self.body_span(content)[1]
        for match in re.finditer(r"(?m)^```yurtle$", content[start:]):
            offset = start + match.start()
            if offset >= span_end:
                break  # the span stops at (or before) the history: nothing swallowed
            if self._HISTORY_OPEN_RE.match(content, offset):
                inner = self._unclosed_fence_line(content[start:offset]) or 1
                return content[:start].count("\n") + inner
        # comments with no history yet: an unclosed fence above `## Comments`
        # swallows them too (#727 review)
        span = content[start:span_end]
        opened = self._unclosed_fence_line(span)
        if opened is not None:
            below = "\n".join(span.split("\n")[opened:])
            if any(self._COMMENTS_RE.match(line) for line in below.split("\n")):
                return content[:start].count("\n") + opened
        return None

    def swallowed_what(self, content: str, line: int) -> str:
        """What follows a swallowing fence on file line `line` (#743): 'the status
        history and comments', 'the status history' or 'the comments'. A quoted
        `## Comments` counts as comments: the two can't be told apart (#736)."""
        after = "\n".join(content.split("\n")[line:])
        history = any(
            self._HISTORY_OPEN_RE.match(after, m.start())
            for m in re.finditer(r"(?m)^```yurtle$", after)
        )
        comments = any(self._COMMENTS_RE.match(ln) for ln in after.split("\n"))
        if history and comments:
            return "the status history and comments"
        return "the status history" if history else "the comments"

    def _replace_body(self, content: str, description: str) -> str:
        """Replace the part of the body span (`body_span`) after the H1, or after the
        frontmatter and any leading knowledge blocks when there is no H1 (#583, #576).

        Returns `content` unchanged when the span, or the description the parser
        reads from the file, already equals `description`, so a read-modify-write
        that sends the parsed description back is a no-op.
        """
        if description.strip() == (self._extract_description(content) or ""):
            return content
        if (line := self.swallowed_fence_line(content)) is not None:
            raise InputRefused(
                f"The body's code fence on line {line} runs over "
                f"{self.swallowed_what(content, line)} after it: close that fence by "
                "hand, or reword a quoted heading inside it, first. A body edit now "
                "would delete them (#727, #743, #758)."
            )
        h1 = self._h1_span(content)
        start = self._leading_knowledge_end(content)
        if h1:
            start = h1[1] + 1 if h1[1] < len(content) else len(content)
        end = max(self.body_span(content)[1], start)
        tail = end < len(content)
        if content[start:end].strip() == description.strip():
            return content
        text = description.strip("\n")
        span = f"\n{text}\n" if text else ""
        if tail:
            span += "\n"  # a blank line before the fence / comments
        if start == len(content) and content and not content.endswith("\n"):
            span = "\n" + span  # an H1 without a final newline
        return content[:start] + span + content[end:]

    def rank_item(
        self,
        item_id: str,
        rank: int,
        value_summary: str | None = None,
        commit: bool = True,
        message: str | None = None,
    ) -> WorkItem:
        """Set the priority rank for a work item.

        Args:
            item_id: The work item ID to rank
            rank: Priority rank (lower = higher priority, 1 = top)
            value_summary: Optional brief value statement
            commit: Whether to git commit the change
            message: Optional commit message
        """
        if rank < 1:
            raise InputRefused(f"Rank must be >= 1, got {rank}")
        self._check_text(value_summary=value_summary)  # before any write (#219)

        item = self._writable_item(item_id, "a rank")  # the file now (#638, #742)

        item.priority_rank = rank
        if value_summary is not None:
            item.value_summary = value_summary

        # Update file using field-level updates (preserves existing content)
        content, eol = self._read_item_text(item.file_path)
        content = self._add_or_update_frontmatter_field(content, "priority_rank", str(rank))
        if value_summary is not None:
            # yaml_quote escapes \\, newlines and every YAML-unsafe character (#162)
            content = self._add_or_update_frontmatter_field(
                content, "value_summary", yaml_quote(value_summary)
            )
        self._write_item_text(item.file_path, content, eol)
        item = self._reread_item(item) or item  # the cache holds the file now (#638)

        if commit:
            self._git_commit(
                item.file_path,
                message or f"Rank {item.id} as #{rank}",  # the file's spelling (#751)
            )

        return item

    def get_ranked_items(self, status: WorkItemStatus | None = None) -> list[WorkItem]:
        """Get items sorted by priority_rank (ranked items first, then by priority_score).

        Items with priority_rank are sorted ascending (1 = highest).
        Items without priority_rank follow, sorted by priority_score descending.
        """
        items = self.get_items(status=status)
        items = [i for i in items if i.status != WorkItemStatus.DONE]

        ranked = [i for i in items if i.priority_rank is not None]
        unranked = [i for i in items if i.priority_rank is None]

        ranked.sort(key=lambda i: i.priority_rank)
        # unranked already sorted by priority_score from get_items()

        return ranked + unranked

    # ------------------------------------------------------------------
    # Experiment run tracking (Phase 3)
    # ------------------------------------------------------------------

    def create_experiment_run(
        self,
        expr_id: str,
        being: str,
        params: dict[str, str] | None = None,
        run_by: str | None = None,
    ) -> Path:
        """Create a timestamped experiment run folder with config.yaml.

        Args:
            expr_id: Experiment ID (e.g., EXPR-130)
            being: Being name/version (e.g., santiago-toddler-v12.4)
            params: Optional key=value parameters
            run_by: Who started the run (`resolve_actor`: explicit, then
                $YURTLE_AGENT, then git user.name; none is a ValueError)

        Returns:
            Path to the created run folder.
        """
        # Validate expr_id to prevent path traversal
        # Allow dotted sub-IDs like EXPR-131.5 (common for sub-experiments)
        # fullmatch: `$` would accept a trailing newline (#183)
        if not re.fullmatch(r"[A-Za-z]+-[A-Za-z0-9]+(?:\.[A-Za-z0-9]+)*", expr_id):
            raise InputRefused(
                f"Invalid experiment ID format: {expr_id!r} — "
                "expected PREFIX-ID (e.g., EXPR-130 or EXPR-131.5)"
            )
        # before the run folder exists (#219)
        self._check_text(being=being, run_by=run_by)
        for key, val in (params or {}).items():
            self._check_text(**{"params key": key, f"params[{key!r}]": val})

        # who started the run: --agent, then $YURTLE_AGENT, then git user.name; no
        # actor is refused before the run folder exists (#620)
        run_by = resolve_actor(run_by, cwd=self.repo_root, flag="--agent/--run-by")

        # Look up the experiment to get hypothesis link
        item = self.get_item(expr_id)
        hypothesis = ""
        if item and item.file_path and item.file_path.exists():
            content = item.file_path.read_text()
            fm = self._parse_frontmatter(content)
            # a file rewritten since the scan may no longer parse to a mapping: the
            # run is still recorded, with no hypothesis link (#309)
            if isinstance(fm, dict):
                # `hypothesis:` left empty is no link, not `null` (#321)
                hypothesis = fm.get("hypothesis") or ""

        # Create timestamped folder (microseconds to avoid collisions)
        now = datetime.now()
        timestamp = now.strftime("%Y-%m-%dT%H%M%S") + f"-{now.microsecond:06d}"
        runs_dir = self.repo_root / "research" / "runs" / expr_id / timestamp
        runs_dir.mkdir(parents=True, exist_ok=True)

        # Write config.yaml
        config_data: dict[str, Any] = {
            "experiment": expr_id,
            "hypothesis": hypothesis,
            "being": being,
            "created": now.isoformat(timespec="seconds"),
            "run_by": run_by,
            "status": "running",
        }
        if params:
            config_data["params"] = params

        config_path = runs_dir / "config.yaml"
        config_path.write_text(yaml.dump(config_data, default_flow_style=False, sort_keys=False))

        return runs_dir

    def get_experiment_runs(self, expr_id: str) -> list[dict[str, Any]]:
        """Get all runs for an experiment, sorted by date descending.

        Args:
            expr_id: Experiment ID (e.g., EXPR-130)

        Returns:
            List of run metadata dicts with keys: timestamp, being, status,
            outcome, run_by, params, run_path.
        """
        # Validate expr_id to prevent path traversal
        # Allow dotted sub-IDs like EXPR-131.5 (common for sub-experiments)
        # fullmatch: `$` would accept a trailing newline (#183)
        if not re.fullmatch(r"[A-Za-z]+-[A-Za-z0-9]+(?:\.[A-Za-z0-9]+)*", expr_id):
            raise InputRefused(
                f"Invalid experiment ID format: {expr_id!r} — "
                "expected PREFIX-ID (e.g., EXPR-130 or EXPR-131.5)"
            )
        runs_base = self.repo_root / "research" / "runs" / expr_id
        if not runs_base.exists():
            return []

        runs = []
        for run_dir in sorted(runs_base.iterdir(), reverse=True):
            if not run_dir.is_dir():
                continue
            config_path = run_dir / "config.yaml"
            if not config_path.exists():
                continue

            try:
                config_data = yaml.safe_load(config_path.read_text()) or {}
            except yaml.YAMLError:
                continue
            if not isinstance(config_data, dict):
                # one bad run must not break the listing (#338)
                logger.warning(f"{config_path} is not a mapping; run skipped")
                continue

            run_info: dict[str, Any] = {
                "timestamp": config_data.get("created", run_dir.name),
                "being": config_data.get("being", ""),
                "status": config_data.get("status", "unknown"),
                "run_by": config_data.get("run_by", ""),
                "params": config_data.get("params", {}),
                "run_path": run_dir,
            }

            # Check for metrics.json
            metrics_path = run_dir / "metrics.json"
            if metrics_path.exists():
                import json

                try:
                    metrics = json.loads(metrics_path.read_text())
                    run_info["outcome"] = metrics.get("outcome", "")
                    run_info["summary"] = metrics.get("summary", "")
                except (json.JSONDecodeError, OSError):
                    pass

            runs.append(run_info)

        return runs

    def update_run_status(
        self,
        run_path: Path,
        status: str,
        outcome: str | None = None,
    ) -> None:
        """Update the status (and optionally outcome) of an experiment run.

        Args:
            run_path: Path to the run folder
            status: New status (e.g., "running", "complete", "failed")
            outcome: Optional outcome string (e.g., "VALIDATED", "REFUTED")
        """
        self._check_text(status=status, outcome=outcome)  # before any write (#239)
        config_path = run_path / "config.yaml"
        if not config_path.exists():
            raise MissingFile(f"No config.yaml in {run_path}")  # (#803)

        config_data = yaml.safe_load(config_path.read_text()) or {}
        if not isinstance(config_data, dict):
            raise InputRefused(f"{config_path} is not a mapping; run status not updated (#338)")
        config_data["status"] = status
        if outcome is not None:
            config_data["outcome"] = outcome

        config_path.write_text(
            yaml.dump(config_data, default_flow_style=False, sort_keys=False)
        )

    # ------------------------------------------------------------------
    # HDD Registry & Validation (Phase 4)
    # ------------------------------------------------------------------

    def _get_hdd_frontmatter(self, item: WorkItem) -> dict[str, Any]:
        """Read raw frontmatter for an HDD item (includes paper, hypothesis, etc.)."""
        if not item.file_path or not item.file_path.exists():
            return {}
        content = item.file_path.read_text()
        return self._parse_frontmatter(content) or {}

    def get_hdd_cross_references(self) -> dict[str, Any]:
        """Build a full cross-reference map of all HDD items.

        Returns a dict with keys: papers, hypotheses, experiments, measures,
        ideas, literature, orphaned. Each value is a list of dicts.
        """
        hdd_types = {
            WorkItemType.PAPER, WorkItemType.HYPOTHESIS, WorkItemType.EXPERIMENT,
            WorkItemType.MEASURE, WorkItemType.IDEA, WorkItemType.LITERATURE,
        }
        all_items = self.get_items()
        hdd_items = [i for i in all_items if i.item_type in hdd_types]

        # Group by type
        by_type: dict[str, list[WorkItem]] = {
            "paper": [], "hypothesis": [], "experiment": [],
            "measure": [], "idea": [], "literature": [],
        }
        for item in hdd_items:
            type_key = item.item_type.value
            if type_key in by_type:
                by_type[type_key].append(item)

        # Build cross-reference maps
        # Paper → hypotheses
        paper_hyps: dict[str, list[str]] = {}
        # Hypothesis → experiments
        hyp_exps: dict[str, list[str]] = {}
        # Hypothesis → paper
        hyp_paper: dict[str, str] = {}
        # Experiment → hypothesis
        exp_hyp: dict[str, str] = {}
        # Idea → literature
        idea_lits: dict[str, list[str]] = {}
        # Literature → idea
        lit_idea: dict[str, str] = {}

        for item in by_type["hypothesis"]:
            fm = self._get_hdd_frontmatter(item)
            paper_ref = fm.get("paper", "")
            if paper_ref:
                paper_id = str(paper_ref)
                if not paper_id.startswith("PAPER-"):
                    paper_id = f"PAPER-{paper_id}"
                hyp_paper[item.id] = paper_id
                paper_hyps.setdefault(paper_id, []).append(item.id)

        for item in by_type["experiment"]:
            fm = self._get_hdd_frontmatter(item)
            hyp_ref = fm.get("hypothesis", "")
            if hyp_ref:
                exp_hyp[item.id] = str(hyp_ref)
                hyp_exps.setdefault(str(hyp_ref), []).append(item.id)

        for item in by_type["literature"]:
            fm = self._get_hdd_frontmatter(item)
            idea_ref = fm.get("source_idea", "") or fm.get("idea", "")
            if idea_ref:
                lit_idea[item.id] = str(idea_ref)
                idea_lits.setdefault(str(idea_ref), []).append(item.id)

        # Build result
        result: dict[str, Any] = {
            "papers": [],
            "hypotheses": [],
            "experiments": [],
            "measures": [],
            "ideas": [],
            "literature": [],
            "orphaned": [],
        }

        for p in by_type["paper"]:
            result["papers"].append({
                "id": p.id, "title": p.title, "status": p.status.value,
                "hypotheses": paper_hyps.get(p.id, []),
            })

        for h in by_type["hypothesis"]:
            fm = self._get_hdd_frontmatter(h)
            result["hypotheses"].append({
                "id": h.id, "title": h.title, "status": h.status.value,
                "paper": hyp_paper.get(h.id, ""),
                "target": fm.get("target", ""),
                "experiments": hyp_exps.get(h.id, []),
            })

        for e in by_type["experiment"]:
            runs = self.get_experiment_runs(e.id)
            last_outcome = ""
            if runs:
                last_outcome = runs[0].get("outcome", runs[0].get("summary", ""))
            result["experiments"].append({
                "id": e.id, "title": e.title, "status": e.status.value,
                "hypothesis": exp_hyp.get(e.id, ""),
                "runs": len(runs),
                "last_outcome": last_outcome,
            })

        for m in by_type["measure"]:
            fm = self._get_hdd_frontmatter(m)
            result["measures"].append({
                "id": m.id, "title": m.title,
                "unit": fm.get("unit", ""),
                "category": fm.get("category", ""),
            })

        for idea_item in by_type["idea"]:
            result["ideas"].append({
                "id": idea_item.id, "title": idea_item.title,
                "status": idea_item.status.value,
                "literature": idea_lits.get(idea_item.id, []),
            })

        for lit in by_type["literature"]:
            result["literature"].append({
                "id": lit.id, "title": lit.title, "status": lit.status.value,
                "idea": lit_idea.get(lit.id, ""),
            })

        # Orphaned items. A paper is OPTIONAL (issue #77), so "has no paper" is
        # not by itself orphanhood — see _is_deliberately_unparented. An
        # experiment need not test a filed hypothesis either; a DANGLING
        # reference is still caught, as an error, further down.
        for h in by_type["hypothesis"]:
            paper = hyp_paper.get(h.id, "")
            if not paper and not self._is_deliberately_unparented(h.id):
                result["orphaned"].append({
                    "id": h.id, "reason": "No paper assignment",
                })

        return result

    def validate_hdd_links(self) -> dict[str, Any]:
        """Validate bidirectional links between HDD items.

        Returns a validation report dict with keys: errors, warnings, summary.
        """
        xrefs = self.get_hdd_cross_references()

        # Collect all known IDs
        all_ids: set[str] = set()
        for section in ("papers", "hypotheses", "experiments", "measures", "ideas", "literature"):
            for item in xrefs[section]:
                all_ids.add(item["id"])

        errors: list[dict[str, str]] = []
        warnings: list[dict[str, str]] = []

        # Check hypotheses → paper links
        for h in xrefs["hypotheses"]:
            paper = h.get("paper", "")
            if not paper:
                if not self._is_deliberately_unparented(h["id"]):
                    warnings.append({
                        "id": h["id"],
                        "issue": "hypothesis missing paper link",
                    })
            elif paper not in all_ids:
                errors.append({
                    "id": h["id"],
                    "issue": f"references {paper} (not found)",
                })

        # Check experiments → hypothesis links. An experiment that names NO
        # hypothesis is a legitimate standing state (issue #77) — a dangling
        # reference to one that does not exist is still an error below.
        for e in xrefs["experiments"]:
            hyp = e.get("hypothesis", "")
            if hyp and hyp not in all_ids:
                errors.append({
                    "id": e["id"],
                    "issue": f"references {hyp} (not found)",
                })

        # Check literature → idea links (warning, not error)
        for lit in xrefs["literature"]:
            idea = lit.get("idea", "")
            if idea and idea not in all_ids:
                warnings.append({
                    "id": lit["id"],
                    "issue": f"references {idea} (not found)",
                })

        # Check for unused measures
        used_measures: set[str] = set()
        for h in xrefs["hypotheses"]:
            item = self.get_item(h["id"])
            if item and item.graph:
                for triple_obj in item.get_knowledge_triples(_HYP.measuredBy):
                    # Extract measure ID from URI
                    obj_str = str(triple_obj)
                    if "/" in obj_str:
                        used_measures.add(obj_str.split("/")[-1])
                    else:
                        used_measures.add(obj_str)

        for m in xrefs["measures"]:
            if m["id"] not in used_measures:
                warnings.append({
                    "id": m["id"],
                    "issue": "measure not referenced by any hypothesis",
                })

        summary = {
            "papers": len(xrefs["papers"]),
            "hypotheses": len(xrefs["hypotheses"]),
            "experiments": len(xrefs["experiments"]),
            "measures": len(xrefs["measures"]),
            "ideas": len(xrefs["ideas"]),
            "literature": len(xrefs["literature"]),
            "errors": len(errors),
            "warnings": len(warnings),
            "orphaned": len(xrefs["orphaned"]),
        }

        return {
            "errors": errors,
            "warnings": warnings,
            "orphaned": xrefs["orphaned"],
            "summary": summary,
        }

    # ------------------------------------------------------------------
    # HDD Cross-Board Dependency Engine (Phase 5)
    # ------------------------------------------------------------------

    def _resolve_implements(
        self, fm: dict[str, Any],
    ) -> tuple[list[str], dict[str, str], bool]:
        """Parse implements field and resolve expedition statuses.

        Returns (implements_list, implements_status, all_dev_done).
        """
        implements_raw = fm.get("implements", [])
        if isinstance(implements_raw, str):
            implements_list = [implements_raw]
        elif isinstance(implements_raw, list):
            implements_list = [str(x) for x in implements_raw]
        else:
            implements_list = []

        implements_status: dict[str, str] = {}
        all_dev_done = True
        for exp_id in implements_list:
            dev_item = self.get_item(exp_id)
            if dev_item:
                implements_status[exp_id] = dev_item.status.value
                if dev_item.status.value != "done":
                    all_dev_done = False
            else:
                implements_status[exp_id] = "not_found"
                all_dev_done = False

        return implements_list, implements_status, all_dev_done

    def _compute_readiness(
        self,
        implements_list: list[str],
        all_dev_done: bool,
        runs: list[dict[str, Any]],
    ) -> tuple[str, bool, bool, bool]:
        """Determine experiment readiness from dev status and training runs.

        Returns (readiness, has_completed_run, has_running_run, has_metrics).
        """
        has_completed_run = any(
            r.get("status") == "complete" for r in runs
        )
        has_running_run = any(
            r.get("status") == "running" for r in runs
        )
        has_metrics = any(
            r.get("outcome") or r.get("summary") for r in runs
        )

        if not implements_list:
            readiness = "no_dev_dependency"
        elif not all_dev_done:
            readiness = "blocked_by_dev"
        elif has_completed_run and has_metrics:
            readiness = "needs_analysis"
        elif has_completed_run:
            readiness = "training_complete"
        elif has_running_run:
            readiness = "training_in_progress"
        else:
            readiness = "ready_for_training"

        return readiness, has_completed_run, has_running_run, has_metrics

    def build_cross_board_graph(self) -> dict[str, Any]:
        """Build a cross-board dependency graph spanning research and dev boards.

        Traverses: Paper → Hypothesis → Experiment → Expedition (dev board)
                                                    → Training Runs

        Returns a dict with:
          - experiments: list of experiment nodes with full dependency chains
          - dev_blockers: expedition IDs that block research experiments
          - ready_for_training: experiments where all dev work is done
          - training_in_progress: experiments with running training
          - needs_analysis: experiments with completed training
        """
        xrefs = self.get_hdd_cross_references()

        # Build paper → hypothesis lookup (paper_id → [hyp_ids])
        paper_hyps: dict[str, list[str]] = {}
        hyp_paper: dict[str, str] = {}
        for h in xrefs["hypotheses"]:
            paper = h.get("paper", "")
            if paper:
                paper_hyps.setdefault(paper, []).append(h["id"])
                hyp_paper[h["id"]] = paper

        # Build hypothesis → experiment lookup (hyp_id → [expr_ids])
        hyp_exps: dict[str, list[str]] = {}
        for e in xrefs["experiments"]:
            hyp = e.get("hypothesis", "")
            if hyp:
                hyp_exps.setdefault(hyp, []).append(e["id"])

        # For each experiment, resolve implements → dev board expedition statuses
        experiment_nodes: list[dict[str, Any]] = []
        dev_blockers: list[str] = []
        ready_for_training: list[str] = []
        training_in_progress: list[str] = []
        needs_analysis: list[str] = []

        for exp_info in xrefs["experiments"]:
            expr_id = exp_info["id"]
            item = self.get_item(expr_id)
            if not item:
                continue

            fm = self._get_hdd_frontmatter(item)
            impl_list, impl_status, all_done = self._resolve_implements(fm)

            runs = self.get_experiment_runs(expr_id)
            readiness, _, _, _ = self._compute_readiness(
                impl_list, all_done, runs,
            )

            # Calculate downstream impact
            hyp_id = exp_info.get("hypothesis", "")
            paper_id = hyp_paper.get(hyp_id, "")
            blocking_chain: list[str] = []
            if hyp_id:
                blocking_chain.append(hyp_id)
            if paper_id:
                blocking_chain.append(paper_id)

            # Count other experiments sharing the same hypothesis (exclude self)
            sibling_experiments = hyp_exps.get(hyp_id, [])
            other_experiments = max(0, len(sibling_experiments) - 1)
            # Count other hypotheses sharing the same paper (exclude self)
            sibling_hypotheses = paper_hyps.get(paper_id, [])
            other_hypotheses = max(0, len(sibling_hypotheses) - 1)

            downstream_impact = other_experiments + other_hypotheses

            node: dict[str, Any] = {
                "experiment_id": expr_id,
                "title": exp_info.get("title", ""),
                "status": exp_info.get("status", ""),
                "hypothesis_id": hyp_id,
                "paper_id": paper_id,
                "implements": impl_list,
                "implements_status": impl_status,
                "readiness": readiness,
                "runs": len(runs),
                "last_run_status": runs[0].get("status", "") if runs else "",
                "last_outcome": runs[0].get("outcome", "") if runs else "",
                "downstream_impact": downstream_impact,
                "blocking_chain": blocking_chain,
                "compute_requirement": fm.get("compute_requirement", ""),
                "assignee": item.assignee or "",
            }
            experiment_nodes.append(node)

            # Categorize
            if readiness == "blocked_by_dev":
                for eid, status in impl_status.items():
                    if status != "done":
                        dev_blockers.append(eid)
            elif readiness == "ready_for_training":
                ready_for_training.append(expr_id)
            elif readiness == "training_in_progress":
                training_in_progress.append(expr_id)
            elif readiness == "needs_analysis":
                needs_analysis.append(expr_id)

        # Sort experiments by downstream impact (high first), then readiness priority
        readiness_order = {
            "ready_for_training": 0,
            "training_in_progress": 1,
            "blocked_by_dev": 2,
            "needs_analysis": 3,
            "training_complete": 4,
            "no_dev_dependency": 5,
        }
        experiment_nodes.sort(
            key=lambda n: (
                readiness_order.get(n["readiness"], 9),
                -n["downstream_impact"],
            )
        )

        return {
            "experiments": experiment_nodes,
            "dev_blockers": sorted(set(dev_blockers)),
            "ready_for_training": ready_for_training,
            "training_in_progress": training_in_progress,
            "needs_analysis": needs_analysis,
            "summary": {
                "total_experiments": len(experiment_nodes),
                "ready_for_training": len(ready_for_training),
                "blocked_by_dev": sum(
                    1 for n in experiment_nodes
                    if n["readiness"] == "blocked_by_dev"
                ),
                "training_in_progress": len(training_in_progress),
                "needs_analysis": len(needs_analysis),
                "dev_blockers": len(set(dev_blockers)),
            },
        }

    def get_experiment_readiness(self, expr_id: str) -> dict[str, Any]:
        """Check a single experiment's readiness for training.

        Returns a dict with: readiness, implements, implements_status,
        runs, blocking_chain, compute_requirement.
        """
        item = self.get_item(expr_id)
        if not item:
            return {"error": f"{expr_id} not found", "readiness": "unknown"}

        fm = self._get_hdd_frontmatter(item)
        impl_list, impl_status, all_done = self._resolve_implements(fm)

        runs = self.get_experiment_runs(expr_id)
        readiness, has_completed_run, _, has_metrics = (
            self._compute_readiness(impl_list, all_done, runs)
        )

        # Build blocking chain from frontmatter
        hyp_id = fm.get("hypothesis", "")
        paper_id = fm.get("paper", "")
        blocking_chain: list[str] = []
        if hyp_id:
            blocking_chain.append(str(hyp_id))
        if paper_id:
            blocking_chain.append(str(paper_id))

        return {
            "experiment_id": expr_id,
            "title": item.title,
            "readiness": readiness,
            "implements": impl_list,
            "implements_status": impl_status,
            "runs": len(runs),
            "has_completed_run": has_completed_run,
            "has_metrics": has_metrics,
            "blocking_chain": blocking_chain,
            "compute_requirement": fm.get("compute_requirement", ""),
            "assignee": item.assignee or "",
        }

    def get_critical_path(
        self,
        agent: str | None = None,
        ready_only: bool = False,
        dev_blockers_only: bool = False,
    ) -> list[dict[str, Any]]:
        """Get the prioritized critical path for research experiments.

        Traverses Paper → Hypothesis → Experiment → Expedition (dev board)
        to determine what's ready for training, what's blocked, and what
        has the highest downstream impact.

        Args:
            agent: Filter to experiments relevant to this agent/assignee.
            ready_only: Only return experiments ready for training.
            dev_blockers_only: Only return dev board items blocking research.

        Returns:
            List of critical path items sorted by readiness then downstream impact.
        """
        graph = self.build_cross_board_graph()
        experiments = graph["experiments"]

        # Filter by agent if specified
        if agent:
            agent_lower = agent.lower()
            experiments = [
                e for e in experiments
                if e.get("assignee", "").lower() == agent_lower
            ]

        # Filter by readiness
        if ready_only:
            experiments = [
                e for e in experiments
                if e["readiness"] == "ready_for_training"
            ]

        if dev_blockers_only:
            # Return the dev board items that block research
            blocker_ids = graph["dev_blockers"]
            blockers: list[dict[str, Any]] = []
            for bid in blocker_ids:
                dev_item = self.get_item(bid)
                if dev_item:
                    # Count how many experiments this blocker unblocks
                    unblocks = [
                        e["experiment_id"] for e in graph["experiments"]
                        if bid in e.get("implements", [])
                        and e["readiness"] == "blocked_by_dev"
                    ]
                    blockers.append({
                        "expedition_id": bid,
                        "title": dev_item.title,
                        "status": dev_item.status.value,
                        "assignee": dev_item.assignee or "",
                        "unblocks_experiments": unblocks,
                        "impact": len(unblocks),
                    })
            blockers.sort(key=lambda b: -b["impact"])
            return blockers

        return experiments
