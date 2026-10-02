"""`yurtle-kanban upgrade-check [PATH] [--json]`: scan a repo for 2.x usages 3.x changed (#1231).

Read-only and heuristic. It reads scripts (`*.py`, `*.sh`, Makefiles, shebang
scripts, CI workflow YAML) and docs (`*.md`) under PATH and reports, per finding,
the file, line, kind, the old form, a suggestion and a confidence:

- `removed-form`: a yurtle-kanban invocation using a form 3.0.0 removed (#580):
  `move -a`, `create --assignee/-a/--description/-d`, `comment --author/-a` and
  `comment ID TEXT`, `next --assignee/-a`, `list -a`. Found in shell command
  strings (`yurtle-kanban move …`, `$YK move …`) on any line, and in Python
  argument lists whose first element is a yurtle-kanban executable
  (`[YK, 'move', iid, 'in_progress', '-a', agent]`).
- `status-check`: a raw comparison on a canonical status name that the board's
  theme renames (`status == 'in_progress'` on a nautical board, where 3.x writes
  `underway`). Only when the nearest `.kanban/config.yaml` (PATH or above it,
  up to the git root or $HOME) uses such a theme; with none, a note says so.
- `resolution-value`: a board item's front matter `resolution: obsolete` or
  `resolution: merged`, values #581 dropped from the vocabulary (2.x had no
  `--resolution` flag). The one thing read in an item file (high).
- `actor`: a `comment`/`move` call with no `--agent` in a file that never names
  `YURTLE_AGENT`; 3.x falls back to git `user.name`, then refuses (low).

Docs, comments, docstrings and backtick-quoted mentions are `low` confidence:
people copy them, but they are not run. A skill's (`skills/**/SKILL.md`) fenced
and command lines are the exception: agents run them, so they are `high`; a
skill's inline-code command in prose (an "Atomic claim:" sentence quoting
`yurtle-kanban move …`) is `medium`. `.git`, virtualenvs and conda envs,
`.direnv` and `node_modules` are skipped, and the board's own item files (and
any work-item file, known by its `id:`/`status:` front matter) are read only
for their front matter `resolution:`.

What it never checks is `NOT_CHECKED`, printed in `--help`, the header, a clean
run and `--json`: a clean run is not "safe to upgrade" (see UPGRADING.md).
"""

from __future__ import annotations

import ast
import json
import os
import re
import stat
import subprocess
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import click

# the forms #580 removed: subcommand -> {old option: its 3.x replacement}
REMOVED: dict[str, dict[str, str]] = {
    "move": {"-a": "--assign"},
    "create": {
        "-a": "--assign", "--assignee": "--assign", "-d": "--body", "--description": "--body",
    },
    "comment": {"-a": "--agent", "--author": "--agent"},
    "next": {"-a": "--agent", "--assignee": "--agent"},
    "list": {"-a": "--assignee"},
}
# the resolution values #581 dropped from the vocabulary, as a 2.x item's front
# matter carries them (2.x had no `--resolution` flag): the 3.x set is completed,
# superseded, duplicate, wont_do; each one's replacement, as UPGRADING.md words it
REMOVED_RESOLUTIONS: dict[str, str] = {
    "obsolete": "`wont_do` (a dead dependency: its dependents stop being pickable) "
                "or `superseded --superseded-by ID`",
    "merged": "`superseded --superseded-by ID` or `duplicate --superseded-by ID`",
}
# the subcommands scanned: the #580 removals'
SUBCOMMANDS: tuple[str, ...] = tuple(REMOVED)
ACTOR_COMMANDS = ("comment", "move")
# options of the scanned subcommands that take a value (beyond the removed ones,
# which all do), so an option's value is never counted as a positional argument
VALUE_OPTIONS: dict[str, frozenset[str]] = {
    "move": frozenset({"-m", "--message", "--assign", "--agent", "-e", "--export-board",
                       "--closed-by", "--resolution", "--superseded-by"}),
    "create": frozenset({"-p", "--priority", "--assign", "--body", "--body-file", "--tags"}),
    "comment": frozenset({"--body", "--body-file", "--agent"}),
    "next": frozenset({"--agent"}),
    "list": frozenset({"-s", "--status", "-t", "--type", "--assignee", "-p", "--priority",
                       "-b", "--board", "--resolution", "--agent", "--older-than",
                       "--stale-after"}),
}
# shell variables taken to hold the yurtle-kanban executable on any line
ALIAS_VARS = frozenset({"YK", "YK_BIN", "YK_EXE", "KANBAN", "KANBAN_BIN", "YURTLE_KANBAN"})
SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn", ".venv", "venv", "site-packages", "node_modules",
    "__pycache__", ".tox", ".nox", ".mypy_cache", ".ruff_cache", ".pytest_cache",
    ".direnv",
})
# a directory holding one of these is an environment, not the repo's code:
# a virtualenv (`pyvenv.cfg`) or a conda env (`conda-meta/`)
ENV_MARKERS = ("pyvenv.cfg", "conda-meta")
MAX_BYTES = 2_000_000

_SUB = "|".join(SUBCOMMANDS)
# an invocation in a shell string: the exe (a path ending in yurtle-kanban,
# `python -m yurtle_kanban[.cli]`, or a `$VAR`/`${VAR}`/`{var}`), then a subcommand
_INVOKE = re.compile(
    r"(?:(?<![\w-])(?:[\w./~-]*/)?yurtle-kanban"
    r"|python3?\s+-m\s+yurtle_kanban(?:\.cli)?"
    r"|\$\{?(?P<var>[A-Za-z_]\w*)\}?"
    r"|\{(?P<pyvar>[A-Za-z_]\w*)\})"
    rf"\s+(?P<sub>{_SUB})(?![\w-])"
)
_SHELL_TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"|\'[^\']*\'|\S+')
_ALIAS_ASSIGN = re.compile(r"^\s*(?:export\s+)?([A-Za-z_]\w*)=\S*yurtle-kanban", re.M)
_STATUS_NAME_CONTEXT = re.compile(r"status|\bst\b", re.I)
_STATE_CONTEXT = re.compile(r"state|stat", re.I)


@dataclass(frozen=True)
class Finding:
    file: str
    line: int
    kind: str
    old: str
    suggestion: str
    confidence: str


@dataclass
class _Tok:
    text: str
    line: int
    literal: bool = True  # False: a Python expression (a name, an f-string), shown as source
    star: bool = False  # a `*args` element: what it holds is unknown


def _short(text: str, limit: int = 160) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


# --- one invocation's arguments --------------------------------------------------


def _analyse(sub: str, toks: list[_Tok]) -> tuple[list[tuple[int, str]], bool, bool]:
    """The removed forms in one invocation's arguments (after the subcommand):
    ([(line, suggestion)], names --agent, uses a removed actor option)."""
    removed = REMOVED.get(sub, {})
    takes = VALUE_OPTIONS[sub]
    issues: list[tuple[int, str]] = []
    positionals: list[_Tok] = []
    has_agent = removed_actor = star = False
    i = 0
    while i < len(toks):
        tok = toks[i]
        text = tok.text
        if tok.star:
            star = True
            i += 1
            continue
        if not tok.literal or not text.startswith("-") or text == "-":
            positionals.append(tok)
            i += 1
            continue
        if text == "--":
            positionals.extend(toks[i + 1:])
            break
        flag, eq, value = text.partition("=")
        attached = bool(eq)
        if "/" in flag:  # a doc's `--author/-a`: the first spelling
            flag = flag.split("/")[0]
        if not attached and not flag.startswith("--") and len(flag) > 2 and flag[:2] in removed:
            flag, value, attached = flag[:2], flag[2:], True  # `-aAir`
        if flag == "--agent":
            has_agent = True
        new = removed.get(flag)
        if not attached and (new is not None or flag in takes) and i + 1 < len(toks):
            value = toks[i + 1].text
            i += 1
        if new is not None:
            if new == "--agent":
                removed_actor = True
            shown = f" {value}" if value else ""
            issues.append((
                tok.line,
                f"`{sub} {flag}` was removed in 3.0 (#580): use `{new}{shown}`",
            ))
        i += 1
    if sub == "comment" and not star and len(positionals) >= 2:
        issues.append((
            positionals[1].line,
            "`comment ID TEXT` (positional text) was removed in 3.0 (#580): use "
            f"`comment ID --body {positionals[1].text}` (or `--body-file -`)",
        ))
    return issues, has_agent, removed_actor


def _actor_note(sub: str) -> str:
    return (
        f"`{sub}` with no `--agent`: 3.x records the actor from `--agent`, then "
        "`$YURTLE_AGENT`, then git `user.name`, and refuses when none is set; pass "
        "`--agent NAME` or set YURTLE_AGENT where this runs"
    )


def _invocation_findings(
    rel: str, line: int, sub: str, toks: list[_Tok], old: str, confidence: str,
    names_agent_env: bool, actor_notes: bool,
) -> Iterator[Finding]:
    """The findings of one invocation of `sub` at `line`; `toks` are its arguments."""
    issues, has_agent, removed_actor = _analyse(sub, toks)
    for at, suggestion in issues:
        yield Finding(rel, at, "removed-form", _short(old), suggestion, confidence)
    if (
        actor_notes and sub in ACTOR_COMMANDS and not has_agent and not removed_actor
        and not names_agent_env and not any(t.star for t in toks)
    ):
        yield Finding(rel, line, "actor", _short(old), _actor_note(sub), "low")


# --- shell strings (every file type) ----------------------------------------------


def _rest(line: str, start: int, end: int) -> str:
    """The invocation's arguments: from `end` to the end of the shell command —
    a `;`, `|`, `&`, `)`, backtick or ` #`, or the quote the invocation sits in."""
    before = line[:start]
    enclosing = None
    odd = [q for q in ('"', "'") if before.count(q) % 2 == 1]
    if odd:  # the quote opened first is the outer one
        enclosing = min(odd, key=before.rfind)
    out: list[str] = []
    quote = None
    for c in line[end:]:
        if quote:
            if c == quote:
                quote = None
        elif c == enclosing or c in ";|&`)" or (c == "#" and (not out or out[-1].isspace())):
            break
        elif c in "\"'":
            quote = c
        out.append(c)
    return "".join(out)


_PROSE_WORD = re.compile(r"[a-z]+[,.:;!?)]*")


def _prose(sub: str, toks: list[_Tok]) -> bool:
    """`yurtle-kanban comment failed (rc=1)`: a message naming the command, not a
    call. An item ID, a placeholder or a variable is never a bare lowercase word;
    `create` takes a lowercase type first, so there the title must be one too."""
    words = 2 if sub == "create" else 1
    if len(toks) < words:
        return False
    return all(_PROSE_WORD.fullmatch(t.text) for t in toks[:words])


def _in_prose_backticks(before: str, shell: bool) -> bool:
    """The match opens inside a backtick quote that is prose, not code. In a doc or
    a Python string a backtick is always a quote. In a shell script an unescaped
    backtick outside single quotes is command substitution, which runs the command;
    only a `\\`` or a backtick inside single quotes is a literal (prose) one."""
    if not shell:
        return before.count("`") % 2 == 1
    prose = False
    single = double = escaped = False
    for c in before:
        if escaped:
            escaped = False
            if c == "`":
                prose = not prose
        elif single:
            if c == "'":
                single = False
            elif c == "`":
                prose = not prose
        elif c == "\\":
            escaped = True
        elif c == '"':
            double = not double
        elif c == "'" and not double:  # a quote inside "…" is a literal
            single = True
    return prose


_FENCE = re.compile(r"^\s*(```|~~~)")
_LIST_MARKER = re.compile(r"^\s*(?:(?:[-*+]|\d+[.)])\s+)?(?:\$\s+)?`?")


def _is_skill(rel: str) -> bool:
    """`.claude/skills/**/SKILL.md` or `skills/**/SKILL.md`: an agent's skill."""
    parts = rel.split("/")
    return parts[-1] == "SKILL.md" and "skills" in parts[:-1]


def _skill_code_lines(lines: list[str]) -> set[int]:
    """A skill's lines agents execute: inside a code fence, or a line that is a
    command (a list item `yurtle-kanban move …` in backticks, `$ yurtle-kanban …`),
    not prose."""
    out: set[int] = set()
    fenced = False
    for lineno, line in enumerate(lines, 1):
        if _FENCE.match(line):
            fenced = not fenced
            continue
        start = m.end() if (m := _LIST_MARKER.match(line)) else 0
        if fenced or _INVOKE.match(line, start):
            out.add(lineno)
    return out


def _shell_findings(
    rel: str, lines: list[str], aliases: set[str], low_lines: set[int], doc: bool,
    names_agent_env: bool, shell: bool, code_lines: frozenset[int] | set[int] = frozenset(),
) -> Iterator[Finding]:
    for lineno, line in enumerate(lines, 1):
        for m in _INVOKE.finditer(line):
            var = m.group("var") or m.group("pyvar")
            if var and var.upper() not in aliases and var not in aliases and not (
                "yurtle-kanban" in line or "yurtle_kanban" in line
            ):
                continue
            rest = _rest(line, m.start(), m.end())
            # a doc's `[--agent A]`: the brackets are not part of the argument
            toks = [_Tok(w, lineno) for t in _SHELL_TOKEN.findall(rest) if (w := t.strip("[]"))]
            if _prose(m.group("sub"), toks):
                continue
            stripped = line.lstrip()
            comment = stripped.startswith(("#", "//")) and not stripped.startswith("#!")
            # a skill's command line is run by agents: not low for being in a doc
            code = lineno in code_lines
            low = (
                (doc and not code) or comment or lineno in low_lines
                or (not code and _in_prose_backticks(line[: m.start()], shell=shell))
            )
            yield from _invocation_findings(
                rel, lineno, m.group("sub"), toks, m.group(0) + rest,
                "low" if low else "high", names_agent_env, actor_notes=not low,
            )


# --- Python argument lists ---------------------------------------------------------


def _is_exe(node: ast.expr) -> bool:
    """`node` names the yurtle-kanban executable: `YK`, `exe`, `self.yk`,
    'yurtle-kanban', '.venv/bin/yurtle-kanban', os.path.join(..., 'yurtle-kanban')."""
    if isinstance(node, ast.Name):
        name = node.id
    elif isinstance(node, ast.Attribute):
        name = node.attr
    else:
        return any(
            isinstance(n, ast.Constant) and isinstance(n.value, str)
            and n.value.rstrip("/").rsplit("/", 1)[-1] == "yurtle-kanban"
            for n in ast.walk(node)
        )
    low = name.lower()
    return low in {"yk", "exe", "yk_exe", "yk_bin"} or "kanban" in low


def _str(node: ast.expr) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _list_invocation(node: ast.List | ast.Tuple) -> tuple[str, list[ast.expr]] | None:
    """(subcommand, the argument nodes after it) when `node` is a yurtle-kanban argv."""
    elts = node.elts
    if len(elts) < 2:
        return None
    if _is_exe(elts[0]):
        rest = elts[1:]
    elif (
        len(elts) >= 4 and _str(elts[1]) == "-m"
        and (_str(elts[2]) or "").split(".")[0] == "yurtle_kanban"
    ):
        rest = elts[3:]  # [sys.executable, '-m', 'yurtle_kanban.cli', ...]
    else:
        return None
    sub = _str(rest[0]) if rest else None
    if sub not in SUBCOMMANDS:
        return None
    return sub, rest[1:]


def _docstring_lines(tree: ast.AST) -> set[int]:
    """Lines inside a bare string statement (a docstring): prose, not code."""
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and _str(node.value) is not None:
            out.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
    return out


def _python_list_findings(
    rel: str, src: str, tree: ast.AST, names_agent_env: bool
) -> Iterator[Finding]:
    for node in ast.walk(tree):
        if not isinstance(node, (ast.List, ast.Tuple)):
            continue
        found = _list_invocation(node)
        if found is None:
            continue
        sub, args = found
        toks = []
        for elt in args:
            text = _str(elt)
            if isinstance(elt, ast.Starred):
                toks.append(_Tok("*", elt.lineno, literal=False, star=True))
            elif text is not None:
                toks.append(_Tok(text, elt.lineno))
            else:
                toks.append(_Tok(ast.get_source_segment(src, elt) or "…", elt.lineno, False))
        yield from _invocation_findings(
            rel, node.lineno, sub, toks, ast.get_source_segment(src, node) or "", "high",
            names_agent_env, actor_notes=True,
        )


# --- status comparisons --------------------------------------------------------------


@dataclass
class _Renamed:
    """Canonical status names the board's themes rename: canonical -> native names."""

    natives: dict[str, list[str]]
    themes: list[str]

    def suggestion(self, names: Iterable[str]) -> str:
        parts = [f"`{n}` is `{'`/`'.join(self.natives[n])}`" for n in names]
        return (
            f"on this repo's {'/'.join(self.themes)} board 3.x writes the theme's own "
            f"status name ({'; '.join(parts)}): compare the canonical `status` from "
            "`list --json`/`show --json`, or accept the native name too"
        )


def _status_findings_py(
    rel: str, src: str, tree: ast.AST, renamed: _Renamed, low_lines: set[int]
) -> Iterator[Finding]:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        operands = [node.left, *node.comparators]
        for op, a, b in zip(node.ops, operands, operands[1:]):
            if not isinstance(op, (ast.Eq, ast.NotEq, ast.In, ast.NotIn)):
                continue
            for lit, other in ((a, b), (b, a)):
                names = _literal_names(lit)
                if names is None:
                    continue
                hit = sorted(
                    n for n in names
                    # a check that accepts any native name already works
                    if n in renamed.natives and not set(renamed.natives[n]) & names
                )
                if not hit:
                    continue
                other_src = ast.get_source_segment(src, other) or ""
                if _STATUS_NAME_CONTEXT.search(other_src):
                    confidence = "high"
                elif _STATE_CONTEXT.search(other_src):
                    confidence = "low"
                else:
                    continue
                if node.lineno in low_lines:
                    confidence = "low"
                yield Finding(
                    rel, node.lineno, "status-check",
                    _short(ast.get_source_segment(src, node) or ""),
                    renamed.suggestion(hit), confidence,
                )
                break


def _literal_names(node: ast.expr) -> set[str] | None:
    text = _str(node)
    if text is not None:
        return {text}
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)) and node.elts:
        values = [_str(e) for e in node.elts]
        if all(v is not None for v in values):
            return {v for v in values if v is not None}
    return None


def _status_line_findings(
    rel: str, lines: list[str], renamed: _Renamed, python: bool, doc: bool,
    low_lines: set[int],
) -> Iterator[Finding]:
    names = "|".join(sorted(map(re.escape, renamed.natives), key=len, reverse=True))
    # `status: in_progress` (front matter written or grepped by code); in Python
    # only inside a string (no quote between `status` and the colon)
    literal = re.compile(
        # `\\n` before it: a front-matter line inside a one-line string literal
        r"(?:(?<!\w)|(?<=\\n))"
        + (r"status:\s*['\"]?" if python else r"status['\"]?\s*:\s*['\"]?")
        + rf"(?P<name>{names})(?![\w-])"
    )
    # a shell/Make comparison: `[ "$st" = "in_progress" ]`, `== in_progress`
    compare = re.compile(
        rf"(?:==|!=|(?<![\w=!<>])=(?!=))\s*['\"]?(?P<name>{names})(?![\w-])"
    )
    for lineno, line in enumerate(lines, 1):
        m = literal.search(line)
        if m is None and not python and _STATUS_NAME_CONTEXT.search(line):
            m = compare.search(line)
        if m is None:
            continue
        # in Python a `status: …` string is as often a fixture writing front matter
        # (which 3.x still reads) as a pattern grepping it: low
        low = doc or python or lineno in low_lines or line.lstrip().startswith("#")
        yield Finding(
            rel, lineno, "status-check", _short(line.strip()),
            renamed.suggestion([m.group("name")]), "low" if low else "high",
        )


# --- the repo: config, files ----------------------------------------------------------


CRASHED = 3  # exit code of an unexpected error: 1 is findings, 2 a usage error
NO_CONFIG_NOTE = "no .kanban config found: status checks skipped"


def _config_root(start: Path) -> Path | None:
    """The nearest directory from `start` up that holds `.kanban/config.yaml`, so
    `upgrade-check scripts/` still knows the board's theme. The walk stops at a
    git root (a directory holding `.git`) or the filesystem root."""
    for d in (start, *start.parents):
        if os.path.isfile(d / ".kanban" / "config.yaml"):
            return d
        if os.path.exists(d / ".git"):
            return None
    return None


def _load_config(root: Path) -> Any | None:
    path = root / ".kanban" / "config.yaml"
    if not os.path.isfile(path):  # False, not an exception, on an unreadable .kanban
        return None
    from yurtle_kanban.config import KanbanConfig

    try:
        return KanbanConfig.load(path)
    except Exception:  # a config 3.x refuses is not this scan's business
        return None


def _renamed(config: Any | None) -> _Renamed | None:
    if config is None:
        return None
    if config.is_multi_board:
        themes = [(b.preset, b.get_theme(config.repo_root)) for b in config.boards]
    else:
        themes = [(config.theme, config.get_theme())]
    natives: dict[str, list[str]] = {}
    used: list[str] = []
    for name, theme in themes:
        mappings = (theme or {}).get("status_mappings") or {}
        renames = [
            (str(native), str(canonical)) for native, canonical in mappings.items()
            if str(native) != str(canonical)
        ]
        if renames and name not in used:
            used.append(name)
        for native, canonical in renames:
            natives.setdefault(canonical, [])
            if native not in natives[canonical]:
                natives[canonical].append(native)
    return _Renamed(natives, used) if natives else None


def _item_dirs(config: Any | None, root: Path) -> list[Path]:
    """The board directories, resolved: `.md` files in them are the board's items."""
    if config is None:
        return []
    paths = [str(p) for p in config.get_work_paths()]
    for board in config.boards:
        paths.extend(board.scan_paths)
    out = []
    for p in paths:
        try:
            out.append((root / Path(p).expanduser()).resolve())
        except OSError:
            continue
    return out


_FRONT_MATTER = re.compile(r"\A---\r?\n(.*?)^---\s*$", re.S | re.M)


def _is_item(path: Path, src: str, root: Path, item_dirs: list[Path]) -> bool:
    """A work item, not a doc: a `.md` in one of the board's directories, or any
    `.md` whose front matter names an `id:` and a `status:` (an item of a board
    at the repo root, or of another repo's board vendored here)."""
    if path.suffix != ".md":
        return False
    real = path.resolve()
    if any(d != root and d in real.parents for d in item_dirs):
        return True
    fm = _FRONT_MATTER.match(src)
    return bool(fm) and all(
        re.search(rf"^{key}:", fm.group(1), re.M) for key in ("id", "status")
    )


_RESOLUTION_LINE = re.compile(
    r"^resolution:\s*(?P<q>['\"]?)(?P<value>[\w-]+)(?P=q)\s*(?:#.*)?$"
)


def _item_findings(rel: str, src: str) -> Iterator[Finding]:
    """An item's front matter `resolution: obsolete|merged`: a value #581 dropped
    from the vocabulary. The only thing read in an item: its body is the board's
    notes, never scanned for CLI forms."""
    fm = _FRONT_MATTER.match(src)
    if fm is None:
        return
    first = src.count("\n", 0, fm.start(1)) + 1  # the line of the front matter's first key
    for offset, line in enumerate(fm.group(1).splitlines()):
        m = _RESOLUTION_LINE.match(line.rstrip())
        if m is None or (value := m.group("value")) not in REMOVED_RESOLUTIONS:
            continue
        yield Finding(
            rel, first + offset, "resolution-value", _short(line.strip()),
            f"`resolution: {value}` is a value #581 dropped from the vocabulary (3.x has "
            f"completed, superseded, duplicate, wont_do): use {REMOVED_RESOLUTIONS[value]}",
            "high",
        )


def _file_kind(path: Path, root: Path) -> str | None:
    """'py', 'sh' (any shell-ish text: scripts, Makefiles, CI workflows), 'md' or None."""
    name, suffix = path.name, path.suffix.lower()
    if suffix == ".py":
        return "py"
    if suffix in (".sh", ".bash", ".zsh", ".mk") or name in ("Makefile", "GNUmakefile", "makefile"):
        return "sh"
    if suffix == ".md":
        return "md"
    if suffix in (".yml", ".yaml"):
        parts = path.relative_to(root).parts
        if name == ".gitlab-ci.yml" or parts[:2] == (".github", "workflows"):
            return "sh"
        return None
    if not suffix:
        try:
            with path.open("rb") as f:
                first = f.readline(200)
        except OSError:
            return None
        if first.startswith(b"#!"):
            if b"python" in first:
                return "py"
            if any(s in first for s in (b"sh", b"bash", b"zsh")):
                return "sh"
    return None


def _walk(root: Path) -> Iterator[Path]:
    """Regular files under `root`. An unreadable directory is skipped, never raised:
    `os.path.exists` / `lstat` under `try` return no answer instead of an error."""
    for dirpath, dirnames, filenames in os.walk(root, onerror=lambda _err: None):
        here = Path(dirpath)
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in SKIP_DIRS
            and not any(os.path.exists(here / d / m) for m in ENV_MARKERS)
        )
        for name in sorted(filenames):
            path = here / name
            try:
                mode = path.lstat().st_mode  # lstat: a symlink is not followed
            except OSError:
                continue
            if stat.S_ISREG(mode):
                yield path


@dataclass
class ScanResult:
    findings: list[Finding]
    notes: list[str]
    skipped: int = 0  # scanned-kind files not read: over MAX_BYTES, or binary (a NUL)


def scan(root: Path) -> ScanResult:
    """Every finding under `root`, sorted by file and line, and notes on what
    was not checked. Reads only."""
    root = root.resolve()
    notes: list[str] = []
    config_root = _config_root(root)
    if config_root is None:
        notes.append(NO_CONFIG_NOTE)
    config = _load_config(config_root) if config_root is not None else None
    board_root = config_root or root
    renamed = _renamed(config)
    item_dirs = _item_dirs(config, board_root)
    findings: list[Finding] = []
    skipped = 0
    for path in _walk(root):
        kind = _file_kind(path, root)
        if kind is None:
            continue
        try:
            if path.stat().st_size > MAX_BYTES:
                skipped += 1
                continue
            data = path.read_bytes()
        except OSError:
            continue
        if b"\x00" in data:  # a binary file under a code suffix
            skipped += 1
            continue
        # utf-8 whatever the locale: the same repo scans the same everywhere
        src = data.decode("utf-8", errors="replace")
        rel = path.relative_to(root).as_posix()
        if _is_item(path, src, board_root, item_dirs):
            findings.extend(_item_findings(rel, src))
            continue
        findings.extend(_scan_file(rel, src, kind, renamed))
    unique = {(f.file, f.line, f.kind, f.suggestion): f for f in findings}
    ordered = sorted(unique.values(), key=lambda f: (f.file, f.line, f.kind, f.suggestion))
    return ScanResult(ordered, notes, skipped)


def _scan_file(rel: str, src: str, kind: str, renamed: _Renamed | None) -> Iterator[Finding]:
    lines = src.splitlines()
    doc = kind == "md"
    names_agent_env = "YURTLE_AGENT" in src
    aliases = set(ALIAS_VARS) | set(_ALIAS_ASSIGN.findall(src))
    tree: ast.AST | None = None
    low_lines: set[int] = set()
    if kind == "py":
        try:
            tree = ast.parse(src)
        except (SyntaxError, ValueError):
            tree = None
        if tree is not None:
            low_lines = _docstring_lines(tree)
            aliases |= {
                t.id for node in ast.walk(tree) if isinstance(node, ast.Assign)
                and any(
                    isinstance(c, ast.Constant) and isinstance(c.value, str)
                    and c.value.endswith("yurtle-kanban") for c in ast.walk(node.value)
                )
                for t in node.targets if isinstance(t, ast.Name)
            }
    code_lines = _skill_code_lines(lines) if doc and _is_skill(rel) else set()
    yield from _shell_findings(
        rel, lines, aliases, low_lines, doc, names_agent_env, shell=kind == "sh",
        code_lines=code_lines,
    )
    if tree is not None:
        yield from _python_list_findings(rel, src, tree, names_agent_env)
    if renamed is not None:
        if tree is not None:
            yield from _status_findings_py(rel, src, tree, renamed, low_lines)
        yield from _status_line_findings(
            rel, lines, renamed, python=kind == "py", doc=doc, low_lines=low_lines
        )


# --- the command -----------------------------------------------------------------------


def _default_root() -> Path:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True,
            check=False, stdin=subprocess.DEVNULL, timeout=30,  # every git call: #580, #880
        )
    except (OSError, subprocess.TimeoutExpired):
        return Path.cwd()
    return Path(out.stdout.strip()) if out.returncode == 0 and out.stdout.strip() else Path.cwd()


HEADER = (
    "upgrade-check is heuristic: it lists likely yurtle-kanban 2.x usages that 3.x "
    "changed; review each (it can miss some and flag some wrongly)."
)
# the 3.0 changes this scan never looks for: a clean run is not "safe to upgrade"
NOT_CHECKED: tuple[str, ...] = (
    "Python API removals (WorkItem.blocks, to_dict()['blocks'], WorkItem's Yurtle-block export, "
    "WorkflowParser.validate_transition, WorkflowConfig.get_allowed_transitions, "
    "KanbanService._commit_and_push_file, the kb:blocks query triple)",
    "refusals moved to stderr (#1080/#1086/#1090): a script grepping stdout for "
    "`Error:` or `Item not found`",
    'the --json refusal shape {"success": false, "error": ...} (#877)',
    "MCP: kanban_get_blocked's shape (blocked_items/count -> {\"items\": ...}) and "
    "kanban_add_comment's \"agent\" default",
    "hand-rolled readers of non-JSON CLI output",
)
NOT_CHECKED_LINE = f"not checked: {'; '.join(NOT_CHECKED)}; see UPGRADING.md"

_HELP = f"""Scan a repo for 2.x usages that 3.x changed (read-only).

    Reads scripts (*.py, *.sh, Makefiles, CI workflows) and docs (*.md) under
    PATH (default: the repo root) and reports each file:line, the old form and
    its 3.x replacement: options 3.0 removed (#580) in shell strings and Python
    argument lists; an item's front matter resolution: obsolete|merged
    (values #581 dropped from the vocabulary); raw status comparisons a
    nautical, hdd or spec board's native status names break; comment/move
    calls with no --agent. Docs and quoted mentions are low confidence, a
    skill's inline-code commands medium. Skips .git, virtualenvs,
    node_modules and, but for their front matter resolution, the board's
    own items. Exit 0 with nothing found, 1 with findings,
    2 on a usage error, 3 when the scan itself fails.

    Not checked (see UPGRADING.md): {'; '.join(NOT_CHECKED)}.
    """


@click.command("upgrade-check", help=_HELP)
@click.argument(
    "path", required=False, type=click.Path(exists=True, file_okay=False, path_type=Path)
)
@click.option(
    "--json", "as_json", is_flag=True, help="One JSON object: findings, heuristic, not_checked"
)
def upgrade_check(path: Path | None, as_json: bool) -> None:
    root = path if path is not None else _default_root()
    try:
        result = scan(root)
    except Exception as e:  # a crash is neither findings (1) nor a usage error (2)
        message = " ".join(f"upgrade-check failed: {type(e).__name__}: {e}".split())
        click.echo(f"Error: {message}", err=True)
        if as_json:  # stdout stays one JSON object, as every refusal (#877)
            click.echo(json.dumps({"success": False, "error": message}, ensure_ascii=False))
        click.get_current_context().exit(CRASHED)
    findings = result.findings
    if as_json:
        click.echo(json.dumps(
            {
                "findings": [asdict(f) for f in findings], "heuristic": True,
                "not_checked": list(NOT_CHECKED), "skipped": result.skipped,
                "notes": result.notes,
            },
            ensure_ascii=False,
        ))
    else:
        for note in result.notes:
            click.echo(f"note: {note}", err=True)
        click.echo(HEADER)
        click.echo(NOT_CHECKED_LINE)
        click.echo(f"scanned: {root}")
        if not findings:
            click.echo(f"no 2.x usages found by this scan; {NOT_CHECKED_LINE}")
        current = None
        for f in findings:
            if f.file != current:
                current = f.file
                click.echo("")
                click.echo(f.file)
            click.echo(f"  {f.line}: [{f.confidence}] {f.kind}: {f.old}")
            click.echo(f"      -> {f.suggestion}")
        if findings:
            files = len({f.file for f in findings})
            high = sum(f.confidence == "high" for f in findings)
            click.echo("")
            click.echo(
                f"{len(findings)} finding(s) in {files} file(s): "
                f"{high} high, {len(findings) - high} low confidence"
            )
        click.echo(f"skipped: {result.skipped} large/binary files")
    click.get_current_context().exit(1 if findings else 0)
