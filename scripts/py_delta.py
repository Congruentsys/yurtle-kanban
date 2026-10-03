#!/usr/bin/env python3
"""Which pytest files can a change affect? A fail-closed answer (#1248).

    py_delta.py --config <file> <base> <tip>      # cwd = the repo root

Reads `git diff --no-renames -z --name-only <base> <tip>` and routes every changed path through
the config. The script is generic (stdlib only, no repository-specific path); each repository
supplies its own config.

Config: line oriented; `#` comment lines and blank lines are ignored. Each rule is one of

    <path-glob> -> <test-glob> [<test-glob> ...]
    <path-glob> -> FULL        # the whole check
    <path-glob> -> NONE        # no tests

The FIRST matching rule wins per changed path. Globs are `fnmatch.fnmatchcase` on the
repo-relative path (`*` crosses `/`). Test globs expand against the TIP tree. Anything else on a
line is malformed, and a malformed line anywhere makes the whole run cannot-assess.

The LAST stdout line is exactly one of (#1248)

    py-delta: subset <sorted unique test files>   rc 0  (bare `py-delta: subset` = no tests)
    py-delta: full <why>                          rc 1
    py-delta: cannot-assess <why>                 rc 2

A caller may skip tests ONLY on rc 0 AND an exact `py-delta: subset` last line.
"""
from __future__ import annotations

import fnmatch
import os
import subprocess
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

RC_SUBSET, RC_FULL, RC_CANNOT = 0, 1, 2

# Build and pytest-configuration files change what every test does (#1248).
ALWAYS_FULL_NAMES = frozenset(
    {"Makefile", "GNUmakefile", "makefile", "conftest.py", "pytest.ini", "pyproject.toml",
     "setup.cfg", "setup.py", "tox.ini"}
)
ALWAYS_FULL_PREFIXES = ("requirements",)

SYMLINK_MODE = "120000"
UNSAFE_CATEGORIES = frozenset({"Cc", "Cs", "Zl", "Zp"})  # controls, surrogates, line breaks


class VerdictError(Exception):
    """Raised to stop with a verdict (#1248)."""

    def __init__(self, kind: str, why: str) -> None:
        super().__init__(why)
        self.kind = kind
        self.why = why


def full(why: str) -> VerdictError:
    return VerdictError("full", why)


def cannot(why: str) -> VerdictError:
    return VerdictError("cannot-assess", why)


def escape(text: str) -> str:
    """Escape every control / line-break / surrogate character so a `why` stays one line."""
    out: list[str] = []
    for ch in text:
        if unicodedata.category(ch) in UNSAFE_CATEGORIES:
            code = ord(ch)
            if 0xDC80 <= code <= 0xDCFF:  # an undecodable byte, via surrogateescape (#1248)
                out.append(f"\\x{code - 0xDC00:02x}")
            elif code <= 0xFF:
                out.append(f"\\x{code:02x}")
            else:
                out.append(f"\\u{code:04x}")
        else:
            out.append(ch)
    return "".join(out)


def has_unsafe(text: str) -> bool:
    return any(unicodedata.category(ch) in UNSAFE_CATEGORIES for ch in text)


@dataclass
class Rule:
    glob: str
    kind: str  # "FULL", "NONE" or "TESTS"
    tests: list[str] = field(default_factory=list)


def parse_config(text: str) -> list[Rule]:
    """Parse the config; ANY malformed line is a cannot-assess (#1248)."""
    rules: list[Rule] = []
    for n, raw in enumerate(text.split("\n"), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("->")
        if len(parts) != 2:
            raise cannot(f"config line {n} is malformed (want one '->'): {line}")
        lhs = parts[0].split()
        rhs = parts[1].split()
        if len(lhs) != 1:
            raise cannot(f"config line {n} is malformed (want one path glob): {line}")
        if not rhs:
            raise cannot(f"config line {n} is malformed (no target): {line}")
        if "FULL" in rhs or "NONE" in rhs:
            if len(rhs) != 1:
                raise cannot(f"config line {n} is malformed (FULL/NONE stand alone): {line}")
            rules.append(Rule(lhs[0], rhs[0]))
        else:
            rules.append(Rule(lhs[0], "TESTS", rhs))
    return rules


def git(*args: str) -> bytes:
    """Run git; a failure is a cannot-assess (#1248)."""
    proc = subprocess.run(["git", *args], capture_output=True)
    if proc.returncode != 0:
        msg = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        detail = msg[-1] if msg else f"rc {proc.returncode}"
        raise cannot(f"git {args[0]} failed: {detail}")
    return proc.stdout


def decode(raw: bytes) -> str:
    return raw.decode("utf-8", "surrogateescape")


def resolve_commit(rev: str) -> str:
    if not rev or rev.startswith("-"):
        raise cannot(f"bad revision: {rev!r}")
    out = git("rev-parse", "--verify", "--quiet", "--end-of-options", f"{rev}^{{commit}}")
    sha = out.decode("ascii", "replace").strip()
    if not sha:
        raise cannot(f"bad revision: {rev}")
    return sha


def changed_paths(base: str, tip: str) -> list[str]:
    # --ignore-submodules=none: a gitlink bump must show; --no-relative: always repo-root paths.
    out = git(
        "diff", "--no-renames", "--no-ext-diff", "--no-relative", "--ignore-submodules=none",
        "-z", "--name-only", base, tip, "--",
    )
    return [decode(p) for p in out.split(b"\0") if p]


def tip_tree(tip: str) -> tuple[list[str], list[str]]:
    """(blob paths, symlink paths) tracked in the tip tree."""
    out = git("ls-tree", "-r", "-z", "--full-tree", tip)
    blobs: list[str] = []
    links: list[str] = []
    for entry in out.split(b"\0"):
        if not entry:
            continue
        meta, _, raw_path = entry.partition(b"\t")
        mode, kind, _sha = decode(meta).split(" ")
        path = decode(raw_path)
        if mode == SYMLINK_MODE:
            links.append(path)
        if kind == "blob":
            blobs.append(path)
    return blobs, links


def repo_relative(path: Path, top: Path) -> str | None:
    """`path`'s repo-relative POSIX form, or None when it lies outside the repo."""
    try:
        return path.resolve().relative_to(top).as_posix()
    except ValueError:
        return None


def is_self_or_ancestor(changed: str, guarded: str) -> bool:
    """EQUALS or a proper path ANCESTOR (not a mere string prefix) (#1248)."""
    return guarded == changed or guarded.startswith(changed.rstrip("/") + "/")


def is_always_full(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name in ALWAYS_FULL_NAMES or name.startswith(ALWAYS_FULL_PREFIXES)


def parse_args(argv: list[str]) -> tuple[str, str, str]:
    if len(argv) == 4 and argv[0] == "--config":
        return argv[1], argv[2], argv[3]
    if len(argv) == 3 and argv[0].startswith("--config="):
        return argv[0][len("--config="):], argv[1], argv[2]
    raise cannot("usage: py_delta.py --config <file> <base> <tip>")


def assess(argv: list[str]) -> list[str]:
    """Return the sorted unique test files, or raise a VerdictError (#1248)."""
    config_arg, base_arg, tip_arg = parse_args(argv)
    top_out = git("rev-parse", "--show-toplevel")
    top = Path(decode(top_out).rstrip("\n")).resolve()

    config_path = Path(config_arg)
    try:
        text = config_path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise cannot(f"cannot read config {config_arg}: {exc}") from None
    rules = parse_config(text)

    base = resolve_commit(base_arg)
    tip = resolve_commit(tip_arg)
    changed = changed_paths(base, tip)
    if not changed:
        raise cannot("empty diff")

    for path in changed:
        if has_unsafe(path):
            raise cannot(f"control character in changed path {path}")

    blobs, links = tip_tree(tip)
    if links:
        raise full(f"the tip tracks a symlink: {links[0]}")

    script_rel = repo_relative(Path(__file__), top)
    config_rel = repo_relative(config_path, top)
    script_name = Path(__file__).name

    globs: list[str] = []
    for path in changed:
        if script_rel is not None and is_self_or_ancestor(path, script_rel):
            raise full(f"the routing script changed: {path}")
        if script_rel is None and path.rsplit("/", 1)[-1] in {script_name, "py_delta.py"}:
            raise full(f"a copy of the routing script changed: {path}")
        if config_rel is not None and is_self_or_ancestor(path, config_rel):
            raise full(f"the config in use changed: {path}")
        if is_always_full(path):
            raise full(f"a build or pytest file changed: {path}")
        rule = next((r for r in rules if fnmatch.fnmatchcase(path, r.glob)), None)
        if rule is None:
            raise full(f"no rule matches: {path}")
        if rule.kind == "FULL":
            raise full(f"routed FULL by {rule.glob}: {path}")
        globs.extend(rule.tests)

    selected: set[str] = set()
    for test_glob in dict.fromkeys(globs):
        hits = [b for b in blobs if fnmatch.fnmatchcase(b, test_glob)]
        if not hits:
            raise full(f"test glob {test_glob} matches no file in the tip")
        selected.update(hits)

    for test in selected:
        if has_unsafe(test) or any(ch.isspace() for ch in test):
            raise cannot(f"selected test path holds whitespace or a control character: {test}")
    return sorted(selected)


def emit(line: str) -> None:
    sys.stdout.flush()
    sys.stdout.buffer.write(line.encode("utf-8", "backslashreplace") + b"\n")
    sys.stdout.buffer.flush()


def main(argv: list[str]) -> int:
    try:
        tests = assess(argv)
    except VerdictError as v:
        emit(f"py-delta: {v.kind} {escape(v.why) or 'no reason given'}")
        return RC_FULL if v.kind == "full" else RC_CANNOT
    except Exception as exc:  # any surprise is a cannot-assess (#1248)
        emit(f"py-delta: cannot-assess {escape(f'{type(exc).__name__}: {exc}')}")
        return RC_CANNOT
    emit(" ".join(["py-delta: subset", *tests]))
    return RC_SUBSET


if __name__ == "__main__":
    os.environ.setdefault("GIT_OPTIONAL_LOCKS", "0")
    sys.exit(main(sys.argv[1:]))
