"""No shipped skill may teach a push to main, or a merge performed on main.

CLAUDE.md:19 — "Do all implementation work on the branch — **never push directly
to main**".

This is the sharpest failure mode a skill has. A wrong PATH errors and stops the
reader. This one SUCCEEDS, and bypasses review to do it — and the skill that
shipped it was the review skill, whose entire job is the gate being bypassed.

It shipped twice: the nautical review skill (fixed in #74) and its software twin,
which #74 did not touch because nothing pointed at it. A guard is what makes
"fixed in one theme" mean "fixed", so the next twin cannot survive its sibling's
repair.

Scope note — this checks the SHIPPED skills only. The repo's own workflow files
are not skills and are not consumed by anyone else's agent.
"""

import re
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"

# `git push [flags] <remote> main` — the thing forbidden. `--force-with-lease origin
# <branch>` is fine, so the branch name is what decides, not the flags.
# `main` must be a whole refspec TOKEN (#88): preceded by whitespace, `:`, `+` or a
# quote (optionally via `refs/heads/`), and followed by whitespace, end of line, a
# closing quote or backtick, a comment or a shell operator — never by `/`, `-` or
# `:`. Only the part of the line before a `#` comment counts, so `main` mentioned in
# a comment is not a push.
# #465: the line may START with markdown that puts a command on it — list markers
# (`-`, `*`, `1.`), a blockquote `>`, an inline-code backtick, a `$` prompt — but
# never with prose, so "we never git push to main" is not a command. Global options
# (`-C dir`, `-c k=v`, `--git-dir dir`, `--no-pager`) may sit between `git` and
# `push`. Each option token has exactly ONE parse — a dash, an optional second dash,
# then a letter; a separate value never starts with `-` — so the group is linear
# (round 2: `--?[\w-]+` read `--x` two ways and went 2^N on a run of options). A dry run —
# `--dry-run` or a short flag cluster containing `n` — pushes nothing, but only when
# it belongs to THIS command, i.e. before any `;`, `&` or `|`.
# The cluster is read ONCE — "has an `n`" is a lookahead, then `[A-Za-z]+` takes the
# whole run — so `-nnnn…1` is linear (round 3: `[A-Za-z]*n[A-Za-z]*` split it k ways).
# #472: this matches ONE shell segment; `refuses_push_to_main` splits the line on
# `&&`, `||`, `;`, `|` and `&` first, so a push chained after `cd x &&` is checked.
# A segment may open a subshell `(` or a brace group `{` (#489), and the command may
# sit behind `sudo`/`env` (with options) and `KEY=val` assignments — each prefix token
# starts differently (a word, a dash, `NAME=`), so the prefix group has one parse and
# stays linear.
# `-n` right after `-o`/`--push-option` is that option's VALUE, not a dry run.
# #489: also `command [-p]`, `nohup`, `time [-p]`, `exec`, `!`, and the compound-
# command keywords `then`/`do`/`else` that open a segment (`if x; then git push …`).
# Each is a distinct word, so the prefix group keeps its single parse. Inside a short
# cluster `o` takes the REST as its value (`-on` pushes with option "n"), so a
# cluster is a dry run only when an `n` comes before any `o`. git may be named by
# an absolute path (`/usr/bin/git`), whose `/`-delimited parts split one way only.
_CMD_PREFIX = (
    r"(?:(?:sudo|env)\s+(?:(?:(?:-u|--user|-C|--chdir)\s+[^\s-]\S*"
    r"|--?[A-Za-z][\w-]*(?:=\S+)?)\s+)*"
    r"|(?:command|time)\s+(?:-p\s+)*|(?:nohup|exec|then|do|else)\s+|!\s+"
    r"|[A-Za-z_]\w*=(?:'[^']*'|\"[^\"]*\"|[^\s'\"]\S*|)\s+)*"
)
_LEAD = r"^\s*(?:(?:[-*>`$({]|\d+[.)])\s*)*"
_GIT = (
    r"(?:(?:/[\w.+-]+)*/)?git\s+(?:(?:(?:-[Cc]|--(?:git-dir|work-tree|namespace))\s+[^\s-]\S*"
    r"|--?[A-Za-z][\w-]*(?:=\S+)?)\s+)*"
)
PUSH_TO_MAIN = re.compile(
    _LEAD + _CMD_PREFIX + _GIT + r"push\b"
    r"(?![^\n]*(?:--dry-run"
    r"|(?<!\s-o)(?<!--push-option)\s-(?=[A-Za-np-z]*n)[A-Za-z]+(?![\w-])))"
    r"[^\n]*(?<=[\s:+'\"])(?:refs/heads/)?main(?=[\s#;&|'\"`)]|$)"
)

# #485: the other half of the recipe — a checkout of main, then a merge, is how you
# end up merging ON main. Both are read per shell segment, with the same lead and
# prefixes as a push. A checkout is OF main only when `main` is its last token (after
# flags that take no value): `-b feat main` lands on feat, `main -- file` stays put.
CHECKOUT_MAIN = re.compile(
    _LEAD + _CMD_PREFIX + _GIT + r"(?:checkout|switch)\s+(?:--?[A-Za-z][\w-]*\s+)*main[`'\")\s]*$"
)
CHECKOUT = re.compile(_LEAD + _CMD_PREFIX + _GIT + r"(?:checkout|switch)\b")
MERGE = re.compile(_LEAD + _CMD_PREFIX + _GIT + r"merge(?![\w-])")


def _scan(line: str) -> tuple[list[str], bool]:
    """Split one line into shell segments; also say whether it continues (`\\` at end).

    #489: one left-to-right pass that knows quotes. Inside '…' or "…" (with `\\"`
    escapes) a `#` is not a comment and `&&`/`||`/`;`/`|`/`&` do not split; outside,
    a `\\` escapes the next character and a `#` at a word start begins a comment. A
    quote with no partner later on the line is a literal character, so an apostrophe
    in prose never hides the rest of the line. The partner test is an index compare
    against the last `'` and the last unescaped `"`, so the pass stays linear.
    """
    n = len(line)
    last_sq = line.rfind("'")
    last_dq = -1
    escaped = False
    for i, c in enumerate(line):
        if escaped:
            escaped = False
        elif c == "\\":
            escaped = True
        elif c == '"':
            last_dq = i
    segments = []
    start = i = 0
    while i < n:
        c = line[i]
        if c == "\\":
            if i == n - 1:
                return segments + [line[start:i]], True
            i += 2
        elif c == "'" and last_sq > i:
            i = line.index("'", i + 1) + 1
        elif c == '"' and last_dq > i:
            i += 1
            while i < n and line[i] != '"':
                i += 2 if line[i] == "\\" else 1
            i += 1
        elif c == "#" and (i == 0 or line[i - 1].isspace()):
            n = i
        elif c in ";&|":
            segments.append(line[start:i])
            i += 2 if line[i : i + 2] in ("&&", "||") else 1
            start = i
        else:
            i += 1
    segments.append(line[start:n])
    return segments, False


def _commands(lines: list[str]) -> Iterator[tuple[int, str, list[str]]]:
    """Yield (0-based start line, text, segments) per command, continuations joined (#489).

    Each physical line is scanned once to learn whether it continues; a joined command
    is scanned once more as a whole, so the work stays linear in the text.
    """
    i = 0
    while i < len(lines):
        start = i
        segments, continues = _scan(lines[i])
        if not continues or i + 1 == len(lines):
            yield start, lines[i], segments
            i += 1
            continue
        parts = []
        while continues and i + 1 < len(lines):
            parts.append(lines[i][:-1])
            i += 1
            _, continues = _scan(lines[i])
        parts.append(lines[i])
        i += 1
        text = "".join(parts)
        yield start, text, _scan(text)[0]


def refuses_push_to_main(line: str) -> bool:
    """The guard applied to one skill line: any shell segment that pushes to main."""
    return any(PUSH_TO_MAIN.match(seg) for seg in _scan(line)[0])


def pushes_to_main(lines: list[str]) -> list[tuple[int, str]]:
    """The push guard applied to a skill: every (1-based line, text) that pushes to main."""
    return [
        (n + 1, text.strip())
        for n, text, segments in _commands(lines)
        if any(PUSH_TO_MAIN.match(seg) for seg in segments)
    ]


# How far after a checkout of main a `git merge` still counts as a merge ON main.
MERGE_WINDOW = 5


def merges_on_main(lines: list[str]) -> list[tuple[int, str]]:
    """The merge guard applied to a skill: every (1-based line, text) that merges on main.

    One pass over the segments in order: a checkout of main opens a window through
    the next MERGE_WINDOW lines, a checkout of anything else closes it, and a merge
    inside the window is a merge on main.
    """
    offenders = []
    opened = None  # 0-based line of the checkout of main whose window is open
    for n, text, segments in _commands(lines):
        hit = False
        for seg in segments:
            if CHECKOUT_MAIN.match(seg):
                opened = n
            elif CHECKOUT.match(seg):
                opened = None
            elif MERGE.match(seg) and opened is not None and n - opened <= MERGE_WINDOW:
                hit = True
        if hit:
            offenders.append((n + 1, text.strip()))
    return offenders


# Self-tests of the guard itself (#88). A guard that silently misses a form is worse
# than none: it certifies the skills clean. `main` must be matched as a whole refspec
# TOKEN, not as a word anywhere on the line — `feature/main-thing` has word boundaries
# around `main` too, and is the case a naive "just drop the `$`" fix breaks.
PUSH_TO_MAIN_CASES = [
    # (line, should the guard refuse it?)
    ("git push origin main", True),
    ("git push -u origin main", True),
    ("git push origin HEAD:main", True),
    ("git push --force origin main", True),
    ("$ git push origin main", True),
    ("    git push origin main", True),
    # #88: anything after `main` used to escape the end-of-line anchor
    ("git push origin main --force", True),
    ("git push origin main -f", True),
    ("git push origin main  # land it", True),
    ("git push origin main # land it", True),
    ("git push origin main#land it", True),
    ("git push origin main && echo done", True),
    ("git push origin +main", True),
    ("git push origin HEAD:refs/heads/main", True),
    ("git push origin feature:main", True),
    # never refused: other refs
    ("git push origin v2.1.0", False),
    ("git push origin feature/x", False),
    ("git push origin HEAD", False),
    ("git push -u origin chore/release-vX.Y.Z", False),
    ("git push --force-with-lease origin feature/feat-XXX-branch", False),
    # never refused: `main` only as part of a longer name
    ("git push origin mainline", False),
    ("git push origin main-branch", False),
    ("git push origin feature/main-thing", False),
    ("git push origin feature/main", False),
    ("git push origin my-main", False),
    ("git push origin main-thing --force", False),
    # never refused: `main` only as a SOURCE ref, or only in a comment
    ("git push origin main:feature/x", False),
    ("git push origin origin/main:feature/x", False),
    ("git push origin feature/x  # never push main", False),
    # never refused: dry runs push nothing
    ("git push --dry-run origin main", False),
    ("git push origin main --dry-run", False),
    # not a push line at all
    ("git pull origin main", False),
    ("Never run git push origin main.", False),
    # #465: quoted refspecs are the same push
    ("git push origin 'main'", True),
    ('git push origin "main"', True),
    ("git push origin 'HEAD:main'", True),
    ("git push origin 'main-x'", False),
    ('git push origin "feature/main"', False),
    # #465: global git options before `push`
    ("git -C dir push origin main", True),
    ("git -c k=v push origin main", True),
    ("git --no-pager push origin main", True),
    ("git -C dir push origin feature/x", False),
    # #465: command lines behind a markdown prefix
    ("- git push origin main", True),
    ("* git push origin main", True),
    ("`git push origin main`", True),
    ("> git push origin main", True),
    ("1. git push origin main", True),
    ("- `git push origin main`", True),
    ("> $ git push origin main", True),
    ("- git push origin feature/x", False),
    ("`git push origin main-thing`", False),
    # #465: prose stays prose — only a line that STARTS with the command is one
    ("we never git push to main", False),
    ("- we never git push to main", False),
    ("> Never run `git push origin main`.", False),
    ("Do not run `git push origin main` here.", False),
    # #465: the short dry-run `-n` pushes nothing either
    ("git push -n origin main", False),
    ("git push origin main -n", False),
    ("git push -nf origin main", False),
    # ...but a `-n` belonging to a LATER command does not exempt the push
    ("git push origin main && echo -n done", True),
    ("git push origin main; git log --dry-run", True),
    # `-n` must be a flag, not part of a word
    ("git push --no-verify origin main", True),
    # #465 round 2: global options that take a separate value
    ("git --git-dir .git push origin main", True),
    ("git --work-tree dir push origin main", True),
    ("git --git-dir=.git --work-tree dir -C sub push origin main", True),
    ("git --git-dir .git push origin feature/x", False),
    # #472: a push chained after another command is still a push — every `&&`, `||`,
    # `;`, `|` and `&` segment is a command of its own
    ("cd x && git push origin main", True),
    ('cd "$WORKTREE" && git push origin main', True),
    ("git fetch origin && git push origin main", True),
    ("git commit -m wip; git push origin main", True),
    ("git commit -m wip ; git push origin main", True),
    ("false || git push origin main", True),
    ("yes | git push origin main", True),
    ("sleep 1 & git push origin main", True),
    ("git add -A && git commit -m x && git push -u origin main", True),
    ("- `cd x && git push origin main`", True),
    ("$ cd x && git push origin HEAD:main", True),
    ("(cd x && git push origin main)", True),
    # #472: command prefixes that still run the push
    ("sudo git push origin main", True),
    ("sudo -u deploy git push origin main", True),
    ("env git push origin main", True),
    ("env -i PATH=/usr/bin git push origin main", True),
    ("GIT_TRACE=1 git push origin main", True),
    ("GIT_TRACE=1 GIT_CURL_VERBOSE=1 git push origin main", True),
    ("GIT_SSH_COMMAND='ssh -i key' git push origin main", True),
    ("cd x && sudo git push origin main", True),
    # #472: `-n` as the VALUE of `-o` / `--push-option` is not a dry run
    ("git push -o -n origin main", True),
    ("git push --push-option -n origin main", True),
    # controls — chained, prefixed or optioned, but not a push to main
    ("cd x && git push origin feat-branch", False),
    ("git fetch origin main && git push origin feature/x", False),
    ("git checkout main && git pull", False),
    ("git pull origin main; git push origin HEAD", False),
    ("sudo git push origin feature/x", False),
    ("GIT_TRACE=1 git push origin feature/x", False),
    ("git push -o ci.skip origin feat", False),
    ("git push --push-option=ci.skip origin feat", False),
    ("cd x && git push --dry-run origin main", False),
    ("cd x && git push -n origin main", False),
    ("git push -o ci.skip -n origin main", False),
    ("echo git push origin main", False),
    ("git log --oneline | grep main", False),
    ("git push origin feature/x  # then && git push origin main", False),
    ("we never git push to main; ever", False),
    # #489: a `#` or a separator inside quotes is not a comment and not a segment break
    ('git commit -m "fix #472" && git push origin main', True),
    ("git commit -m 'fix #472' && git push origin main", True),
    ('git tag -a v1 -m "v1 # notes"; git push origin main', True),
    ('git commit -m "it\'s done" && git push origin main', True),
    ('git commit -m "a \\" # b" && git push origin main', True),
    ('git push -o "ci #1" origin main', True),
    # #489: an unterminated quote is a literal character — it never hides a push
    ("echo don't && git push origin main", True),
    ('echo "oops && git push origin main', True),
    # #489: more command prefixes, an absolute git path, and compound-command keywords
    ("command git push origin main", True),
    ("command -p git push origin main", True),
    ("nohup git push origin main &", True),
    ("time git push origin main", True),
    ("time -p git push origin main", True),
    ("exec git push origin main", True),
    ("! git push origin main", True),
    ("/usr/bin/git push origin main", True),
    ("sudo /usr/local/bin/git push origin main", True),
    ("nohup /usr/bin/git -C x push origin main", True),
    ("if git fetch; then git push origin main; fi", True),
    ("for r in a b; do git push origin main; done", True),
    ("if false; then :; else git push origin main; fi", True),
    ("then sudo git push origin main", True),
    # #489: `o` takes the rest of a short cluster as its value, so `-on` is not a dry run
    ("git push -on origin main", True),
    ("git push -fon origin main", True),
    ("git push -o n origin main", True),
    ("git push --push-option=n origin main", True),
    ("git push --push-option=-n origin main", True),
    # #489 controls — quoted text, prefixes and clusters that are still not a push to main
    ('git commit -m "x; git push origin main"', False),
    ("git commit -m 'x && git push origin main'", False),
    ('git commit -m "git push origin main"', False),
    ('git push origin feature/x # "quoted" && git push origin main', False),
    ("echo 'a # b' # git push origin main", False),
    ("git push origin feature/x  # it's && git push origin main", False),
    ("command git push origin feat", False),
    ("/usr/bin/git push origin feature/x", False),
    ("/usr/bin/gitx push origin main", False),
    ("then git push origin feature/x", False),
    ("do not git push to main", False),
    ("git push -nfo x origin main", False),
    ("git push -fn origin main", False),
    ("git push -n -o x origin main", False),
    # #489 round 2: a `{ …; }` brace group opens a segment like a subshell does
    ("{ git push origin main; }", True),
    ("{ git push origin main; } && echo ok", True),
    ("cd x && { git push origin main; }", True),
    ("{ echo; } && git push origin feature/x", False),
    ("{ git push origin feature/x; }", False),
    ("{ echo hi; }", False),
]


@pytest.mark.parametrize("line,refused", PUSH_TO_MAIN_CASES, ids=[c[0] for c in PUSH_TO_MAIN_CASES])
def test_push_to_main_guard_table(line, refused):
    assert refuses_push_to_main(line) is refused, (
        f"PUSH_TO_MAIN {'missed' if refused else 'falsely refused'}: {line!r}"
    )


# #489: a backslash-continued command is ONE command — the lines are joined before
# the check, and an offender is reported at the line the command starts on.
PUSHES_TO_MAIN_TEXT_CASES = [
    # (skill text, the 1-based lines the guard must refuse)
    ("git push origin \\\nmain", [1]),
    ("git push \\\n  origin \\\n  main", [1]),
    ("cd x && \\\ngit push origin main", [1]),
    ("- git push origin \\\n  main", [1]),
    ("echo x\ngit push \\\norigin main", [2]),
    ("echo a \\\n\ngit push origin main", [3]),
    ("git push origin main\ngit push origin main", [1, 2]),
    # controls
    ("git push origin feature/x \\\n  --force", []),
    ("git push origin feat \\\n&& echo main", []),
    ("git push origin feature/x\nmain", []),
    ("git push origin feature/x # \\\ngit push origin main", [2]),
]


@pytest.mark.parametrize(
    "text,refused", PUSHES_TO_MAIN_TEXT_CASES, ids=[c[0] for c in PUSHES_TO_MAIN_TEXT_CASES]
)
def test_push_to_main_guard_text_table(text, refused):
    got = [n for n, _ in pushes_to_main(text.splitlines())]
    assert got == refused, f"pushes_to_main({text!r}) refused lines {got}, expected {refused}"


# #465 round 2: the guard runs on every line of every skill, so it must be linear on
# ANY line — an option token with two parses (`--x` as `--`+`x` or `-`+`-x`) made the
# global-options group 2^N on a line of N options that never reaches `push`. Each
# line runs in a child process with a hard timeout: `re` cannot be interrupted
# in-process, and a hung guard must fail, not hang the suite.
ADVERSARIAL_LINES = [
    "git " + "--no-pager " * 60 + "log",
    "git " + "--a=b " * 60 + "pul",
    "git " + "-c " * 60 + "x",
    "git " + "-c -c " * 60 + "push origin feature/x",
    "git " + "--git-dir " * 60 + "x",
    "- " * 200 + "git pull",
    "git push " + "-a " * 500 + "origin feature/x",
    "git push " + "x" * 5000,
    "git push " + "n" * 5000 + " origin feature/x",
    "1" * 5000 + " git push origin feature/x",
    "git push -" + "n" * 5000 + "1 origin main",
    "git push -" + "n" * 5000 + "- origin feature",
    # #472: long chains, prefix runs and option-value runs
    "cd x && " * 2000 + "git push origin feature/x",
    ";" * 5000 + "git push origin feature/x",
    "|" * 5000 + " git pull",
    "sudo " * 2000 + "git pull",
    "A=b " * 2000 + "git pull",
    "env " + "-u x " * 2000 + "git pull",
    "sudo " + "-u -u " * 2000 + "git pull",
    "A='" + "x " * 2000 + "git pull",
    "git push " + "-o -n " * 2000 + "origin feature/x",
    "(" * 5000 + "git pull",
    # #489: quotes (balanced, unbalanced, escaped), continuations, new prefixes,
    # absolute paths and `o`-clusters
    '"' * 5000 + " git push origin feature/x",
    "'" * 5001 + " && git push origin feature/x",
    '"a #' * 2000,
    "'#" * 3000 + "&& git pull",
    'x "' + '\\"' * 3000,
    'x "' + "&& # " * 2000,
    "git push origin \\\n" * 3000 + "feature/x",
    "\\\n" * 5000,
    "cd x && \\\n" * 2000 + "git pull",
    "command nohup time exec ! " * 1000 + "git pull",
    "then do else " * 1000 + "git pull",
    "command -p " * 2000 + "git pull",
    "/usr" * 3000 + "/git pull",
    "/a/" * 3000 + "gitx push origin main",
    "git push -" + "a" * 5000 + "1 origin main",
    "git push -" + "o" * 5000 + "n origin feature/x",
    "git push " + "-ao " * 2000 + "origin feature/x",
    "{ " * 5000 + "git pull",
    "{" * 5000 + " git push origin feature/x",
]

# The child imports the guard FUNCTION (#472), so line splitting is timed too; it
# passes the text as LINES (#489), so continuation joining is timed as well.
_TIMED_MATCH = (
    "import sys, time\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "from test_skills_do_not_teach_direct_push import pushes_to_main\n"
    "lines = sys.argv[2].splitlines()\n"
    "t = time.perf_counter()\n"
    "pushes_to_main(lines)\n"
    "print(time.perf_counter() - t)\n"
)


@pytest.mark.parametrize(
    "line", ADVERSARIAL_LINES, ids=[f"{c[:24]}...len{len(c)}" for c in ADVERSARIAL_LINES]
)
def test_push_to_main_guard_is_linear(line):
    try:
        out = subprocess.run(
            [sys.executable, "-c", _TIMED_MATCH, str(Path(__file__).resolve().parent), line],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(f"PUSH_TO_MAIN backtracks catastrophically (>5 s) on {line[:40]!r}...")
    elapsed = float(out.stdout)
    assert elapsed < 0.1, f"PUSH_TO_MAIN took {elapsed:.3f} s on {line[:40]!r}..."


# #485: the merge half of the recipe. A checkout of main (`git checkout main` or
# `git switch main`) followed by `git merge` — later on the SAME line, in any `&&`,
# `||`, `;`, `|` or `&` segment, or on any of the next MERGE_WINDOW lines — is a merge
# performed on main. Segments are read as in #472: markdown leads, `sudo`/`env`/
# `KEY=val` prefixes and git global options still make a command. A checkout of any
# other branch in between ends the window; a merge BEFORE the checkout, or `main` only
# as part of a longer name, is not a merge on main.
MERGES_ON_MAIN_CASES = [
    # (skill text, the 1-based lines the guard must refuse)
    # the whole-line form the old guard already caught
    ("git checkout main\ngit merge x", [2]),
    ("git checkout main\ngit pull\ngit merge x", [3]),
    ("git checkout main\n\n\n\n\ngit merge x", [6]),
    ("git checkout main\ngit merge a\ngit merge b", [2, 3]),
    # the shipped shape (skills/hdd/experiment/SKILL.md:152)
    ("git checkout main && git merge exp-{description}", [1]),
    # every segment separator
    ("git checkout main && git merge x", [1]),
    ("git checkout main; git merge x", [1]),
    ("git checkout main ; git merge x", [1]),
    ("git checkout main || git merge x", [1]),
    ("git checkout main | git merge x", [1]),
    ("git checkout main & git merge x", [1]),
    ("git checkout main && git pull && git merge x", [1]),
    ("cd repo && git checkout main && git merge x", [1]),
    ("git checkout main && git pull\ngit merge x", [2]),
    # `git switch main` is the same checkout
    ("git switch main && git merge x", [1]),
    ("git switch main\ngit merge x", [2]),
    # markdown leads, prefixes, global options, flags
    ("- `git checkout main && git merge exp-x`", [1]),
    ("$ git checkout main && git merge x", [1]),
    ("> git checkout main && git merge x", [1]),
    ("1. git checkout main && git merge x", [1]),
    ("(git checkout main && git merge x)", [1]),
    ("git checkout main\n- git merge x", [2]),
    ("git checkout main\n`git merge x`", [2]),
    ("sudo git checkout main && sudo git merge x", [1]),
    ("GIT_TRACE=1 git checkout main && env git merge x", [1]),
    ("git -C repo checkout main && git -C repo merge x", [1]),
    ("git checkout -q main && git merge x", [1]),
    ("git checkout main && git merge --no-ff x", [1]),
    ("git checkout main && git merge x  # land it", [1]),
    # controls — never refused
    ("git checkout main && git pull", []),
    ("git checkout main && git branch -D x", []),
    ("git checkout main\ngit pull\ngit branch -d x", []),
    ("git checkout feat && git merge main", []),
    ("git checkout main\ngit checkout feat\ngit merge main", []),
    ("git checkout main && git checkout feat && git merge main", []),
    ("git checkout main && git switch feat && git merge main", []),
    ("git checkout main-feature && git merge x", []),
    ("git switch main-feature\ngit merge x", []),
    ("git checkout feature/main && git merge x", []),
    ("git checkout origin/main && git merge x", []),
    ("git checkout -b feat main && git merge x", []),
    ("git checkout main -- file.txt && git merge x", []),
    ("git checkout main\n\n\n\n\n\ngit merge x", []),
    ("git merge x && git checkout main", []),
    ("git merge x", []),
    ("git checkout main && git mergetool", []),
    ("git checkout main && git merge-base main x", []),
    ("git checkout main  # then git merge x", []),
    ("git checkout main && echo git merge x", []),
    ("Then git checkout main and git merge x.", []),
    ("We never git checkout main && git merge x", []),
    ("> Never run `git checkout main && git merge x`.", []),
    ("| **VALIDATED** | Merge to main | `git merge` |", []),
    # #489 (shared with #502): the new prefixes, absolute git, keywords, quotes and
    # continuations reach the merge guard too
    ("command git checkout main && nohup git merge x", [1]),
    ("time git checkout main\ngit merge x", [2]),
    ("exec git checkout main\ngit merge x", [2]),
    ("! git checkout main && git merge x", [1]),
    ("/usr/bin/git checkout main && /usr/bin/git merge x", [1]),
    ("if true; then git checkout main; fi && git merge x", [1]),
    ("git checkout main\nfor b in a c; do git merge $b; done", [2]),
    ('git commit -m "wip #1" && git checkout main && git merge x', [1]),
    ("git checkout main \\\n  && git merge x", [1]),
    ("git checkout \\\nmain\ngit merge x", [3]),
    # #489 controls
    ('git commit -m "x; git checkout main" && git merge x', []),
    ("git commit -m 'git checkout main' && git merge x", []),
    ("command git checkout feat && git merge main", []),
    # #489 round 2: brace groups
    ("{ git checkout main; git merge x; }", [1]),
    ("{ git checkout main; }\ngit merge x", [2]),
    ("{ git checkout main; git pull; }", []),
    ("{ echo; } && git merge x", []),
]


@pytest.mark.parametrize(
    "text,refused", MERGES_ON_MAIN_CASES, ids=[c[0] for c in MERGES_ON_MAIN_CASES]
)
def test_merge_on_main_guard_table(text, refused):
    got = [n for n, _ in merges_on_main(text.splitlines())]
    assert got == refused, f"merges_on_main({text!r}) refused lines {got}, expected {refused}"


# #485: the merge guard is timed the same way as the push guard — the child splits
# its argument into lines, so window handling across many lines is timed too.
ADVERSARIAL_MERGE_TEXTS = [
    "git checkout main && " * 2000 + "git pull",
    "git checkout " + "-f " * 2000 + "x",
    "git checkout " + "-f -f " * 2000 + "main-x && git merge x",
    "git switch " + "--q " * 2000 + "x",
    "sudo " * 2000 + "git checkout main",
    "A=b " * 2000 + "git merge x",
    "- " * 2000 + "git checkout main",
    "(" * 5000 + "git checkout main",
    ";" * 5000 + "git merge x",
    "git checkout main\n" * 5000 + "git pull",
    "git checkout main\ngit merge x\n" * 2000,
    "git -C x " * 2000 + "checkout main",
    # #489
    "command nohup time exec ! then do else " * 500 + "git checkout main",
    "/usr/bin/git checkout main \\\n" * 2000 + "&& git merge x",
    'git commit -m "' + "; git checkout main" * 1000,
    "'" * 5001 + " && git checkout main && git merge x",
    "{ " * 5000 + "git checkout main",
]

_TIMED_MERGE = (
    "import sys, time\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "from test_skills_do_not_teach_direct_push import merges_on_main\n"
    "lines = sys.argv[2].splitlines()\n"
    "t = time.perf_counter()\n"
    "merges_on_main(lines)\n"
    "print(time.perf_counter() - t)\n"
)


@pytest.mark.parametrize(
    "text",
    ADVERSARIAL_MERGE_TEXTS,
    ids=[f"{c[:24]}...len{len(c)}" for c in ADVERSARIAL_MERGE_TEXTS],
)
def test_merge_on_main_guard_is_linear(text):
    try:
        out = subprocess.run(
            [sys.executable, "-c", _TIMED_MERGE, str(Path(__file__).resolve().parent), text],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(f"merges_on_main backtracks catastrophically (>5 s) on {text[:40]!r}...")
    elapsed = float(out.stdout)
    assert elapsed < 0.1, f"merges_on_main took {elapsed:.3f} s on {text[:40]!r}..."


def _skill_files():
    assert SKILLS_DIR.is_dir(), f"skills/ not found at {SKILLS_DIR}"
    files = sorted(SKILLS_DIR.rglob("SKILL.md"))
    assert files, "no SKILL.md found — the glob is broken, not the skills"
    return files


def test_the_scan_is_not_vacuous():
    """A pattern that matched nothing anywhere would pass forever."""
    files = _skill_files()
    assert len(files) >= 10, f"only {len(files)} skills found; expected the full shipped set"
    # every skill file must be non-empty and readable, or the scan below is hollow
    assert all(f.read_text().strip() for f in files)


@pytest.mark.parametrize("path", _skill_files(), ids=lambda p: str(p.relative_to(SKILLS_DIR)))
def test_skill_does_not_push_to_main(path):
    offenders = pushes_to_main(path.read_text().splitlines())
    assert not offenders, (
        f"{path.relative_to(SKILLS_DIR.parent)} teaches a push to main: "
        + "; ".join(f"line {n}: {t}" for n, t in offenders)
        + ". Merge through a pull request instead (see skills/nautical/review/SKILL.md)."
    )


@pytest.mark.parametrize("path", _skill_files(), ids=lambda p: str(p.relative_to(SKILLS_DIR)))
def test_skill_does_not_merge_on_main(path):
    """A checkout of main followed by `git merge` — same line or within MERGE_WINDOW lines."""
    offenders = merges_on_main(path.read_text().splitlines())
    assert not offenders, (
        f"{path.relative_to(SKILLS_DIR.parent)} merges on main: "
        + "; ".join(f"line {n}: {t}" for n, t in offenders)
        + ". That is the local half of a direct-to-main landing; "
        "merge through a pull request instead (`gh pr merge`)."
    )
