"""Issue #917 (Captain's ruling, option 2): a static check of the git floor.

The README states the oldest git yurtle-kanban supports. Nothing ran that git, so a new
git feature in `src/` could raise the real floor without anyone noticing. This test reads
`src/` by AST, collects every git subcommand, flag and `--format` placeholder it passes as
a literal, and checks that:

1. each one is listed in GIT_FEATURES with the git version that introduced it, and
2. the README's "git X.Y or later" is the highest version among the features src uses.

Offline: no git is run. It is only as good as the table, so a new entry must carry the
version from git's release notes (RelNotes/<version>.txt), not a guess.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
README = ROOT / "README.md"

Version = tuple[int, int]

# Everything below existed by git 2.0 (2014-05) unless it says otherwise. Features that
# predate 2.0 are pinned at BASELINE rather than their exact introduction: the floor is
# far above it, so only the post-2.0 entries can ever decide it.
BASELINE: Version = (2, 0)

# Keys: a subcommand ("diff"), a flag of that subcommand (("diff", "--no-relative")), or
# a `--format` placeholder of it (("log", "%aI")). Flags are normalized: `--opt=value`
# is `--opt`, a short flag with its value attached (`-G^status:`) is `-G`, and `-<n>`
# (a count, `-1`) is `-<n>`.
#
# `git init -b` / `--initial-branch` (2.28) is used by the TESTS only, so it is not
# here; the README names it for contributors, and 2.28 < 2.36 leaves the floor alone.
GIT_FEATURES: dict[str | tuple[str, str], Version] = {
    # -- the ones that set or approach the floor --------------------------------------
    "hook": (2, 36),  # `git hook run` is new in 2.36.0 (the builtin hook runner)
    ("hook", "--ignore-missing"): (2, 36),  # same release as `git hook run` itself
    ("diff", "--no-relative"): (2, 28),  # 2.28.0: "--no-relative" to countermand
    #                                      diff.relative (which was also new in 2.28)
    ("ls-remote", "--symref"): (2, 8),  # 2.8.0
    ("rev-parse", "--git-path"): (2, 5),  # 2.5.0
    ("log", "%aI"): (2, 2),  # strict ISO 8601 author date placeholder: 2.2.0
    ("update-index", "--cacheinfo"): BASELINE,  # the "mode,sha,path" comma form src
    #                                             passes is 2.0.0; older gits took 3 args
    # -- subcommands (all long predate 2.0) -------------------------------------------
    "add": BASELINE,
    "cat-file": BASELINE,
    "commit": BASELINE,
    "commit-tree": BASELINE,
    "config": BASELINE,
    "diff": BASELINE,
    "fetch": BASELINE,
    "for-each-ref": BASELINE,
    "grep": BASELINE,
    "hash-object": BASELINE,
    "log": BASELINE,
    "ls-remote": BASELINE,
    "ls-tree": BASELINE,
    "merge": BASELINE,
    "push": BASELINE,
    "read-tree": BASELINE,
    "remote": BASELINE,
    "rev-parse": BASELINE,
    "show": BASELINE,
    "status": BASELINE,
    "symbolic-ref": BASELINE,
    "update-index": BASELINE,
    "write-tree": BASELINE,
    # -- flags that predate 2.0 -------------------------------------------------------
    ("add", "--"): BASELINE,
    ("cat-file", "--batch"): BASELINE,  # 1.5.6
    ("commit", "--"): BASELINE,
    ("commit", "--only"): BASELINE,
    ("commit", "-m"): BASELINE,
    ("commit-tree", "-m"): BASELINE,  # 1.7.12
    ("commit-tree", "-p"): BASELINE,
    ("config", "--get"): BASELINE,
    ("diff", "--"): BASELINE,
    ("diff", "--cached"): BASELINE,
    ("diff", "--name-status"): BASELINE,
    ("diff", "--quiet"): BASELINE,
    ("diff", "-M"): BASELINE,
    ("diff", "-z"): BASELINE,
    ("for-each-ref", "--format"): BASELINE,
    ("for-each-ref", "%(refname)"): BASELINE,
    ("grep", "--"): BASELINE,
    ("grep", "--full-name"): BASELINE,
    ("grep", "-E"): BASELINE,
    ("grep", "-I"): BASELINE,  # 1.7.x; conservatively "by 2.0"
    ("grep", "-n"): BASELINE,
    ("grep", "-z"): BASELINE,
    ("hash-object", "--"): BASELINE,
    ("hash-object", "--no-filters"): BASELINE,  # 1.6.1
    ("hash-object", "-w"): BASELINE,
    ("log", "--"): BASELINE,
    ("log", "--format"): BASELINE,
    ("log", "--name-only"): BASELINE,
    ("log", "--relative"): BASELINE,  # 1.5.5
    ("log", "-<n>"): BASELINE,
    ("log", "-G"): BASELINE,  # 1.7.4
    ("log", "-z"): BASELINE,
    ("log", "%H"): BASELINE,
    ("log", "%h"): BASELINE,
    ("log", "%s"): BASELINE,
    ("log", "%x01"): BASELINE,  # %xNN hex escape: 1.6.x
    ("ls-tree", "--"): BASELINE,
    ("ls-tree", "--full-tree"): BASELINE,  # 1.6.x
    ("ls-tree", "--name-only"): BASELINE,
    ("ls-tree", "-r"): BASELINE,
    ("ls-tree", "-t"): BASELINE,
    ("ls-tree", "-z"): BASELINE,
    ("merge", "--ff-only"): BASELINE,  # 1.6.6
    ("merge", "--quiet"): BASELINE,
    ("rev-parse", "--quiet"): BASELINE,
    ("rev-parse", "--show-toplevel"): BASELINE,  # 1.7.0
    ("rev-parse", "--verify"): BASELINE,
    ("status", "--"): BASELINE,
    ("status", "--porcelain"): BASELINE,  # 1.7.0
    ("symbolic-ref", "--quiet"): BASELINE,
    ("symbolic-ref", "--short"): BASELINE,  # 1.7.10
    ("symbolic-ref", "-q"): BASELINE,
    ("update-index", "--add"): BASELINE,
}

# The project's own wrappers whose positional args are git's argv minus "git":
# `GitService._git_run`, `GitService._git_z`, and `hdd_commands`' local `git(...)`.
GIT_HELPERS = frozenset({"_git_run", "_git_z", "git"})

# Flags whose value is a pretty-format string, given as `--format=X` or `--format X`.
_FORMAT_FLAGS = frozenset({"--format", "--pretty"})

_PLACEHOLDER = re.compile(r"%(\([^)]*\)|x[0-9a-fA-F]{2}|[ac][A-Za-z]|[A-Za-z])")


def _normalize(flag: str) -> str:
    if flag.startswith("--"):
        return flag.split("=", 1)[0]
    if flag[1:].isdigit():
        return "-<n>"
    return flag[:2]


def _str(node: ast.expr) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _git_argv(call: ast.Call) -> list[ast.expr] | None:
    """The args after "git" when `call` runs git; None when it doesn't."""
    f = call.func
    name = f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else None
    if name in GIT_HELPERS:
        return list(call.args)
    if call.args and isinstance(call.args[0], ast.List):
        elts = call.args[0].elts
        if elts and _str(elts[0]) == "git":
            return list(elts[1:])
    return None


def extract(source: str, where: str = "<src>") -> dict[str | tuple[str, str], list[str]]:
    """Every git feature a module uses as a literal -> the places that use it. A call
    whose subcommand isn't a literal (a helper forwarding `*args`) is skipped: its
    callers are where the literals are."""
    found: dict[str | tuple[str, str], list[str]] = {}
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        argv = _git_argv(node)
        if not argv or (sub := _str(argv[0])) is None:
            continue
        at = f"{where}:{node.lineno}"
        found.setdefault(sub, []).append(at)
        rest = argv[1:]
        i = 0
        while i < len(rest):
            text = _str(rest[i])
            i += 1
            if text is None or not text.startswith("-"):
                continue
            found.setdefault((sub, _normalize(text)), []).append(at)
            fmt: str | None = None
            if text.split("=", 1)[0] in _FORMAT_FLAGS:
                if "=" in text:  # --format=%H / --pretty=%H
                    fmt = text.split("=", 1)[1]
                elif i < len(rest):  # --format %H: the value is the next argument
                    fmt = _str(rest[i])
                    i += 1  # consumed: a value is never a flag, even if it starts "-"
            for ph in _PLACEHOLDER.findall(fmt or ""):
                found.setdefault((sub, f"%{ph}"), []).append(at)
    return found


# "git" strings in src/ that are not argv, each with why (#1129): the backstop skips them
NOT_ARGV = {
    ("src/yurtle_kanban/service.py", 'when, source = git.get(item.file_path.resolve()), "git"'):
        "aging()'s since_source label",
}


def _call_name(call: ast.Call) -> str | None:
    f = call.func
    return f.attr if isinstance(f, ast.Attribute) else f.id if isinstance(f, ast.Name) else None


def unread_git_lists(
    source: str, where: str = "<src>", allow: Iterable[tuple[str, str]] = ()
) -> list[str]:
    """#1129: every "git" string constant that is not element 0 of a git argv list
    `_git_argv` reads -> its place, unless (where, its stripped line) is in `allow`.
    A command kept in a variable (`cmd = ["git", ...]; run(cmd)`), concatenated,
    passed as `args=[...]`, as a tuple, behind an alias (`GIT = "git"`), after
    another program (`["env", "git", ...]`) or as a wrapper's list argument is
    invisible to `extract`, so a new git feature used that way would keep the floor
    check green. So is a shell command line: `run("git log ...", shell=True)` and
    `"git log".split()` are flagged too."""
    tree = ast.parse(source)
    lines = source.splitlines()
    allowed = set(allow)
    read: set[int] = set()
    places: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # only a direct list argv is read: a wrapper's arguments follow "git" (#1129 F2)
        first = node.args[0] if node.args else None
        if (
            _call_name(node) not in GIT_HELPERS
            and isinstance(first, ast.List)
            and first.elts
            and _str(first.elts[0]) == "git"
        ):
            read.add(id(first.elts[0]))
        shell = any(
            k.arg == "shell" and isinstance(k.value, ast.Constant) and k.value.value is True
            for k in node.keywords
        )
        line = first.values[0] if isinstance(first, ast.JoinedStr) and first.values else first
        if shell and line is not None and (_str(line) or "").startswith("git "):
            places.add(node.lineno)
        f = node.func
        if isinstance(f, ast.Attribute) and f.attr == "split":
            if (_str(f.value) or "").startswith("git "):
                places.add(node.lineno)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and node.value == "git" and id(node) not in read:
            places.add(node.lineno)
    return [
        f"{where}:{n}" for n in sorted(places)
        if (where, lines[n - 1].strip()) not in allowed
    ]


def extract_src() -> dict[str | tuple[str, str], list[str]]:
    found: dict[str | tuple[str, str], list[str]] = {}
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        for key, ats in extract(path.read_text(encoding="utf-8"), rel).items():
            found.setdefault(key, []).extend(ats)
    return found


def unknown(found: Iterable[str | tuple[str, str]]) -> list[str | tuple[str, str]]:
    return sorted((k for k in found if k not in GIT_FEATURES), key=str)


def readme_floor() -> Version:
    m = re.search(r"git (\d+)\.(\d+) or later", README.read_text(encoding="utf-8"))
    assert m, "README.md no longer states 'git X.Y or later' (#917 checks it)"
    return int(m[1]), int(m[2])


def test_every_git_feature_src_uses_is_in_the_table() -> None:
    found = extract_src()
    missing = unknown(found)
    assert not missing, (
        "src/ uses git features that GIT_FEATURES in tests/issues/test_917_git_floor.py "
        "doesn't list. Add each with the git version that introduced it (see git's "
        "RelNotes); if it is newer than the README's floor, raise the floor too:\n"
        + "\n".join(f"  {k!r}  at {', '.join(found[k])}" for k in missing)
    )


def test_the_readme_states_the_highest_version_src_needs() -> None:
    found = extract_src()
    needed = max(GIT_FEATURES[k] for k in found if k in GIT_FEATURES)
    setters = sorted(str(k) for k in found if GIT_FEATURES.get(k) == needed)
    assert readme_floor() == needed, (
        f"README.md says git {'.'.join(map(str, readme_floor()))} or later, but src/ "
        f"needs git {'.'.join(map(str, needed))} (set by {', '.join(setters)})"
    )


def test_the_extractor_sees_known_uses_and_flags_an_unknown_one() -> None:
    found = extract_src()
    # the uses that set the floor today: it can't pass by seeing nothing
    for key in ["hook", ("hook", "--ignore-missing"), ("diff", "--no-relative"),
                ("rev-parse", "--show-toplevel"), ("cat-file", "--batch"), ("log", "%aI")]:
        assert key in found, f"the extractor no longer sees {key!r} in src/"
    snippet = (
        "import subprocess\n"
        "subprocess.run(['git', 'frobnicate', '--zap=1', x])\n"
        "self._git_run('log', '-G^x', '-3', '--format=%H%aI', *rest)\n"
        "git('hook', 'run', '--ignore-missing', name)\n"
        "self._git_z(*args)\n"  # a forwarder: nothing literal to see
        "subprocess.run(['nats', 'pub', '--zap'])\n"  # not git
    )
    got = extract(snippet)
    assert set(got) == {
        "frobnicate", ("frobnicate", "--zap"),
        "log", ("log", "-G"), ("log", "-<n>"), ("log", "--format"), ("log", "%H"),
        ("log", "%aI"),
        "hook", ("hook", "--ignore-missing"),
    }, got
    assert set(unknown(got)) == {"frobnicate", ("frobnicate", "--zap")}


def test_every_git_argv_literal_in_src_is_one_the_extractor_reads() -> None:
    """#1129: the backstop. Every `["git", ...]` in src/ must be an argv `extract` reads;
    any other shape fails here rather than silently escaping the floor check."""
    unread = [
        at
        for path in sorted(SRC.rglob("*.py"))
        for at in unread_git_lists(
            path.read_text(encoding="utf-8"), path.relative_to(ROOT).as_posix(), NOT_ARGV
        )
    ]
    assert not unread, (
        "src/ builds git argv the #917 floor check can't read (a list in a variable, a "
        "concatenation, `args=`...), so a new git feature there would go unnoticed. "
        "Pass the literal `[\"git\", ...]` straight to the call (or use a GIT_HELPERS "
        "wrapper), or teach `_git_argv` in tests/issues/test_917_git_floor.py the new "
        "shape:\n" + "\n".join(f"  {at}" for at in unread)
    )


def test_the_backstop_flags_git_argv_it_cannot_read() -> None:
    snippet = (
        "import subprocess\n"
        "cmd = ['git', 'worktree', 'add', '--orphan']\n"  # 2: in a variable
        "subprocess.run(cmd)\n"
        "subprocess.run(['git', 'x'] + rest)\n"  # 4: concatenated
        "subprocess.run(args=['git', 'status'])\n"  # 5: a keyword
        "subprocess.run(('git', 'status'))\n"  # 6: a tuple, which _git_argv skips
        "subprocess.run(['git', 'status', '--porcelain'])\n"  # read: fine
        "self._git_run('log', '-1')\n"  # a wrapper: fine
        "subprocess.run(['git', *args])\n"  # a forwarder: read (nothing literal)
        "GIT = 'git'\n"  # 10: an alias (r1 F1)
        "subprocess.run(['env', 'X=1', 'git', 'worktree'])\n"  # 11: after a program (r1 F1)
        "self._git_run(['git', 'log', '--new'])\n"  # 12: a wrapper given a list (r1 F2)
        "subprocess.run('git log --format=%H', shell=True)\n"  # 13: a shell line (r1 F3)
        "subprocess.run(f'git log {x}', shell=True)\n"  # 14: an f-string shell line
        "subprocess.run('git log'.split())\n"  # 15: split
        "when, source = x, 'git'\n"  # 16: a label, allowed below
        "print('git exited 1')\n"  # a message, not argv
        "subprocess.run('ls', shell=True)\n"  # not git
    )
    assert unread_git_lists(snippet) == [
        f"<src>:{n}" for n in (2, 4, 5, 6, 10, 11, 12, 13, 14, 15, 16)
    ]
    allow = [("<src>", "when, source = x, 'git'")]
    assert "<src>:16" not in unread_git_lists(snippet, allow=allow)


def test_split_and_pretty_format_placeholders_are_read() -> None:
    snippet = (
        "subprocess.run(['git', 'log', '--format', '%aI'])\n"
        "subprocess.run(['git', 'show', '--pretty=%H%x01'])\n"
        "self._git_run('log', '--pretty', '-%s-', '-1')\n"
    )
    got = extract(snippet)
    assert set(got) == {
        "log", ("log", "--format"), ("log", "%aI"),
        "show", ("show", "--pretty"), ("show", "%H"), ("show", "%x01"),
        ("log", "--pretty"), ("log", "%s"), ("log", "-<n>"),
    }, got
