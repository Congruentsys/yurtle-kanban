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
        for arg in argv[1:]:
            text = _str(arg)
            if text is None or not text.startswith("-"):
                continue
            found.setdefault((sub, _normalize(text)), []).append(at)
            if text.startswith("--format="):
                for ph in _PLACEHOLDER.findall(text.split("=", 1)[1]):
                    found.setdefault((sub, f"%{ph}"), []).append(at)
    return found


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
