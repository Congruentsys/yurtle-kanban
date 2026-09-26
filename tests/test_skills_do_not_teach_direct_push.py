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

Out of reach of a linear, line-at-a-time text guard (#507), by design:
- quotes that span lines — each line's quotes are paired on that line alone;
- heredoc bodies — they are scanned as if they were commands;
- `xargs` (and other commands that run their arguments as a command);
- git aliases — `git pm` may well mean `push origin main`;
- refs held in variables — `git pull origin $BASE` is refused as another branch,
  which is the safe default.
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
# #507: `if`/`elif`/`while`/`until` lead a segment too (the condition runs), and so
# does `builtin`. git may be named relatively (`./git`, `../bin/git`, `~/bin/git`). A
# `{` opens a brace group only when a blank follows — `{git` is one word to bash.
_CMD_PREFIX = (
    r"(?:(?:sudo|env)\s+(?:(?:(?:-u|--user|-C|--chdir)\s+[^\s-]\S*"
    r"|--?[A-Za-z][\w-]*(?:=\S+)?)\s+)*"
    r"|(?:command|time)\s+(?:-p\s+)*"
    r"|(?:nohup|exec|builtin|then|do|else|if|elif|while|until)\s+|!\s+"
    r"|[A-Za-z_]\w*=(?:'[^']*'|\"[^\"]*\"|[^\s'\"]\S*|)\s+)*"
)
_LEAD = r"^\s*(?:(?:[-*>`$(]|\{(?=\s)|\d+[.)])\s*)*"
_GIT = (
    r"(?:(?:~|\.\.?)?(?:/[\w.+-]+)*/)?git\s+(?:(?:(?:-[Cc]|--(?:git-dir|work-tree|namespace))\s+[^\s-]\S*"
    r"|--?[A-Za-z][\w-]*(?:=\S+)?)\s+)*"
)
# #507: the dry-run test walks the push's arguments TOKEN by token (a quoted string
# is one token), so a `-n` inside a quoted value is no flag, and a short cluster that
# ENDS in `o` (`-o`, `-fo`) or `--push-option` swallows the next token as its value.
# The token kinds start differently and the walk tries the dry-run flag first at each
# boundary, so every boundary is visited once: linear.
# #529: a token is a run of plain characters, `\\x` escapes and quoted strings in
# any order, so `-o'a -n'`, `--push-option="a -n"` and `-o a\\ -n` are ONE token each.
# Each piece starts with a different character, so a token still has one parse. The
# long options `--receive-pack`, `--exec` and `--repo` take a separate value as well.
_TOKEN = r"(?:[^\s'\"\\]|\\.|'[^']*'|\"[^\"]*\")+"
_OPT_WITH_VALUE = r"(?:-[A-Za-np-z]*o|--(?:push-option|receive-pack|exec|repo))(?!\S)"
_DRY_RUN = r"(?:--dry-run|-(?=[A-Za-np-z]*n)[A-Za-z]+)(?![\w-])"
PUSH_TO_MAIN = re.compile(
    _LEAD
    + _CMD_PREFIX
    + _GIT
    + r"push\b"
    + r"(?!(?:\s+(?:"
    + _OPT_WITH_VALUE
    + r"\s+"
    + _TOKEN
    + r"|(?!"
    + _OPT_WITH_VALUE
    + r")"
    + _TOKEN
    + r"))*?\s+"
    + _DRY_RUN
    + r")"
    + r"[^\n]*(?<=[\s:+'\"])(?:refs/heads/)?main(?=[\s#;&|'\"`)]|$)"
)

# #485: the other half of the recipe — a checkout of main, then a merge, is how you
# end up merging ON main. Both are read per shell segment, with the same lead and
# prefixes as a push. A checkout is OF main only when `main` is its last token (after
# flags that take no value): `-b feat main` lands on feat, `main -- file` stays put.
# #502: `'main'`/`"main"` is main. `-B main <start>` / `-C main <start>` (and `-b`/`-c`)
# land on main whatever follows; `--detach`/`-d` never does. A checkout with a `--`
# token restores files (CHECKOUT_PATHS) — it neither opens nor closes the window.
# Landing on main is not only `git merge`: `git rebase <x>`, `git cherry-pick <x>` and
# `git pull <remote> <branch>` count too — but syncing does not: a bare `git pull`,
# `git pull <remote>`, `git pull <remote> main`, a bare `git rebase`, `git rebase
# [<remote>/]main`, and flag-only forms like `--continue`/`--abort`. Every flag token
# has one parse and each lookahead is a bounded check, so the patterns stay linear.
# #507: `refs/heads/main` (and `refs/remotes/<r>/main`) is main too. `-t`/`--track
# <remote>/main` creates and lands on main. `git pull` flags that take a separate
# value (`-X ours`, `-s x`, `--depth 1`, …) keep it, so the value is never read as the
# remote. `reset --hard|--soft|--keep|--merge <x>` and `am` on main land work there;
# `update-ref refs/heads/main …` moves main with no checkout at all, so it is refused
# wherever it appears.
_QMAIN = r"(?:'main'|\"main\"|main)"
_FLAGS = r"(?:--?[A-Za-z][\w-]*(?:=\S+)?\s+)*"
_NOT_MAIN_REF = r"(?!['\"]?(?:refs/heads/|refs/remotes/[\w.-]+/|[\w.-]+/)?main['\"]?(?:[\s`)]|$))"
_PULL_VALUE_FLAG = (
    r"(?:-[Xsj]|--(?:strategy|strategy-option|depth|deepen|jobs|upload-pack"
    r"|shallow-since|shallow-exclude|server-option|negotiation-tip))"
)
_PULL_FLAGS = (
    r"(?:(?:"
    + _PULL_VALUE_FLAG
    + r"\s+[^\s-]\S*|(?!"
    + _PULL_VALUE_FLAG
    + r"(?![\w=-]))--?[A-Za-z][\w-]*(?:=\S+)?)\s+)*"
)
_RESET_MODE = r"--(?:hard|soft|keep|merge)(?![\w-])"
# #529: a reset onto the upstream (`@{u}`, `@{upstream}`, `FETCH_HEAD`) is a sync, like
# `origin/main`; onto `HEAD`, `@`, `ORIG_HEAD` or an ancestor of them (`HEAD~2^`) it is
# local cleanup. Neither lands work on main.
_RESET_NOT_LANDING = (
    r"(?!['\"]?(?:(?:HEAD|ORIG_HEAD|@)(?:[~^]\d*)*|FETCH_HEAD|@\{(?:u|upstream)\})"
    r"['\"]?(?:[\s`)]|$))"
)
CHECKOUT_MAIN = re.compile(
    _LEAD
    + _CMD_PREFIX
    + _GIT
    + r"(?:checkout|switch)\s+(?:(?!--detach(?![\w-])|-d(?![\w-]))--?[A-Za-z][\w-]*(?:=\S+)?\s+)*"
    + r"(?:-[BbCc]\s+"
    + _QMAIN
    + r"(?=[\s`)]|$)"
    + r"|(?:-t|--track(?:=\S+)?)\s+['\"]?(?:refs/remotes/)?[\w.-]+/main['\"]?(?=[\s`)]|$)|"
    + _QMAIN
    + r"[`)\s]*$)"
)
CHECKOUT_PATHS = re.compile(_LEAD + _CMD_PREFIX + _GIT + r"checkout\s+(?:\S+\s+)*?--(?:\s|$)")
CHECKOUT = re.compile(_LEAD + _CMD_PREFIX + _GIT + r"(?:checkout|switch)\b")
LANDS = re.compile(
    _LEAD
    + _CMD_PREFIX
    + _GIT
    + r"(?:merge(?![\w-])"
    + r"|(?:rebase|cherry-pick)\s+"
    + _FLAGS
    + _NOT_MAIN_REF
    + r"['\"]?[^\s'\"`)-]"
    + r"|pull\s+"
    + _PULL_FLAGS
    + r"['\"]?[^\s'\"-]\S*\s+"
    + _PULL_FLAGS
    + _NOT_MAIN_REF
    + r"['\"]?[^\s'\"`)-]"
    + r"|reset\s+(?:(?!"
    + _RESET_MODE
    + r")--?[A-Za-z][\w-]*(?:=\S+)?\s+)*"
    + _RESET_MODE
    + r"\s+"
    + _FLAGS
    + _NOT_MAIN_REF
    + _RESET_NOT_LANDING
    + r"['\"]?[^\s'\"`)-]"
    + r"|am(?![\w-])"
    + r"(?!\s+--(?:abort|continue|skip|quit|retry|resolved|show-current-patch)(?![\w-])))"
)
UPDATE_REF_MAIN = re.compile(
    _LEAD
    + _CMD_PREFIX
    + _GIT
    + r"update-ref\s+(?:(?:-m\s+(?:'[^']*'|\"[^\"]*\"|[^\s'\"-]\S*)"
    + r"|(?!-m(?![\w-]))--?[A-Za-z][\w-]*(?:=\S+)?)\s+)*"
    + r"['\"]?refs/heads/main['\"]?(?=[\s`)]|$)"
)
# #529: `git branch -f main [<x>]` resets main, and `git branch -C|-M [<old>] main`
# copies or moves a branch ONTO main — both move main with no checkout, like
# update-ref. `-f feat main` and `-C main feat` move another branch instead.
_BRANCH_FORCE = r"(?:-f|--force)(?![\w-])"
_BRANCH_ONTO = r"-[CM](?![\w-])"
BRANCH_MOVES_MAIN = re.compile(
    _LEAD
    + _CMD_PREFIX
    + _GIT
    + r"branch\s+(?:(?!"
    + _BRANCH_FORCE
    + r"|"
    + _BRANCH_ONTO
    + r")--?[A-Za-z][\w-]*(?:=\S+)?\s+)*(?:"
    + _BRANCH_FORCE
    + r"\s+"
    + _FLAGS
    + _QMAIN
    + r"(?=[\s`)]|$)|"
    + _BRANCH_ONTO
    + r"\s+"
    + _FLAGS
    + r"(?:['\"]?[^\s'\"-]\S*\s+)?"
    + _QMAIN
    + r"[`)\s]*$)"
)


def _scan(line: str) -> tuple[list[str], bool]:
    """Split one line into shell segments; also say whether it continues (`\\` at end).

    #489: one left-to-right pass that knows quotes. Inside '…' or "…" (with `\\"`
    escapes) a `#` is not a comment and `&&`/`||`/`;`/`|`/`&` do not split; outside,
    a `\\` escapes the next character and a `#` at a word start begins a comment.
    #507: a word starts after an UNESCAPED blank or after `;`, `&`, `|`, `(` or `)`,
    so `\\ #x` is no comment and `true;#x` is one; ANSI-C `$'…'` takes `\\'` as an
    escaped quote (it can close only at a `'` behind an even run of backslashes). A
    quote with no partner later on the line is a literal character, so an apostrophe
    in prose never hides the rest of the line. The partner test is an index compare
    against the last `'` and the last unescaped `"`, so the pass stays linear.
    """
    n = len(line)
    last_sq = line.rfind("'")
    last_dq = -1
    last_ansi = -1  # last `'` behind an even run of backslashes: can close `$'…'`
    escaped = False
    run = 0
    for i, c in enumerate(line):
        if escaped:
            escaped = False
        elif c == "\\":
            escaped = True
        elif c == '"':
            last_dq = i
        if c == "'" and run % 2 == 0:
            last_ansi = i
        run = run + 1 if c == "\\" else 0
    segments = []
    start = i = 0
    escaped_at = -1  # the last character a `\\` escaped
    while i < n:
        c = line[i]
        if c == "\\":
            if i == n - 1:
                return segments + [line[start:i]], True
            escaped_at = i + 1
            i += 2
        elif c == "'" and i and line[i - 1] == "$" and escaped_at != i - 1 and last_ansi > i:
            i += 1
            while i < n and line[i] != "'":
                i += 2 if line[i] == "\\" else 1
            i += 1
        elif c == "'" and last_sq > i:
            i = line.index("'", i + 1) + 1
        elif c == '"' and last_dq > i:
            i += 1
            while i < n and line[i] != '"':
                i += 2 if line[i] == "\\" else 1
            i += 1
        elif c == "#" and (
            i == 0 or (escaped_at != i - 1 and (line[i - 1].isspace() or line[i - 1] in ";&|()"))
        ):
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
    the next MERGE_WINDOW lines, a checkout of anything else closes it, a pathspec
    checkout does neither (#502), and a merge, rebase, cherry-pick, pull of another
    branch, `reset --hard <x>` or `am` inside the window lands work on main. An
    `update-ref refs/heads/main` (#507) or `branch -f main` / `-C|-M … main` (#529)
    moves main directly and is refused anywhere.
    """
    offenders = []
    opened = None  # 0-based line of the checkout of main whose window is open
    for n, text, segments in _commands(lines):
        hit = False
        for seg in segments:
            if CHECKOUT_PATHS.match(seg):
                continue
            if UPDATE_REF_MAIN.match(seg) or BRANCH_MOVES_MAIN.match(seg):
                hit = True
            elif CHECKOUT_MAIN.match(seg):
                opened = n
            elif CHECKOUT.match(seg):
                opened = None
            elif LANDS.match(seg) and opened is not None and n - opened <= MERGE_WINDOW:
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
    # #507: `if`/`while`/`until`/`elif` open a segment too — the condition runs
    ("if git push origin main; then echo ok; fi", True),
    ("while ! git push origin main; do sleep 1; done", True),
    ("until git push origin main; do :; done", True),
    ("if x; then :; elif git push origin main; then :; fi", True),
    # #507: a cluster ENDING in `o` takes the next token as its value — not a dry run
    ("git push -fo -n origin main", True),
    ("git push -uo -n origin main", True),
    # #507: a quoted `-n` is part of a value, not a flag
    ('git push -o "a -n" origin main', True),
    ("git push -o 'x -n' origin main", True),
    ('git push --push-option "a -n" origin main', True),
    # #507: an escaped space is not a word break, so the `#` after it is no comment
    ("echo \\ #x && git push origin main", True),
    # #507: ANSI-C `$'…'` quotes take `\'` as an escaped quote
    ("$'\\'' && git push origin main; echo 'x'", True),
    ("echo $'a\\'b' && git push origin main; echo 'c'", True),
    # #507: relative and builtin ways to name git
    ("./git push origin main", True),
    ("../bin/git push origin main", True),
    ("~/bin/git push origin main", True),
    ("builtin command git push origin main", True),
    # #507 controls
    ("if git push origin feat; then :; fi", False),
    ("iffy git push origin main", False),
    ("while true; do git push origin feature/x; done", False),
    ("git push -fo x -n origin main", False),
    ("git push -of -n origin main", False),
    ('git push -n -o "a b" origin main', False),
    ("echo \\#x && git push origin feature/x", False),
    ("true;# && git push origin main", False),
    ("true&&# git push origin main", False),
    ("{git push origin main;}", False),
    ("./gitx push origin main", False),
    ("~/bin/git push origin feature/x", False),
    ("builtin command git push origin feat", False),
    # #529: quotes and escapes mid-token keep a `-n` inside the value
    ("git push -o'a -n' origin main", True),
    ('git push -o"a -n" origin main', True),
    ("git push --push-option='a -n' origin main", True),
    ("git push -o a\\ -n origin main", True),
    # #529: long push options that take a separate value swallow it
    ("git push --receive-pack -n origin main", True),
    ("git push --exec -n origin main", True),
    ("git push --repo -n origin main", True),
    # #529 controls — a real `-n` after such values is still a dry run
    ("git push -o'a b' -n origin main", False),
    ("git push -o a\\ b -n origin main", False),
    ("git push --receive-pack x -n origin main", False),
    ("git push --exec=x -n origin main", False),
    ("git push --repo origin -n main", False),
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
    # #507
    "if while until elif " * 1000 + "git pull",
    "git push " + "-fo x " * 2000 + "origin feature/x",
    "git push " + '"a -n" ' * 2000 + "origin feature/x",
    "git push " + '"' * 3001 + " origin feature/x",
    "git push " + "-fo " * 3000 + "origin feature/x",
    "git push -" + "f" * 5000 + "o -n origin feature/x",
    "echo " + "\\ " * 3000 + "#x",
    "$'" + "\\'" * 3000 + " && git pull",
    "$'" * 3000,
    "./" * 3000 + "git pull",
    "~/" + "a/" * 3000 + "git pull",
    "true;#" * 2000,
    "{git " * 2000,
    # #529
    "git push " + "-o'a -n' " * 2000 + "origin feature/x",
    "git push -o " + "a\\ " * 3000,
    "git push " + "'" * 3001 + " origin feature/x",
    "git push -o" + "'x'" * 3000 + " origin feature/x",
    "git push " + "--repo " * 3000 + "x",
    "git push " + "\\" * 5001,
    "git push " + "a'" * 3000 + " -n",
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
    # #502: a quoted `main` is main — it opens the window, and never closes it
    ("git checkout 'main' && git merge x", [1]),
    ('git checkout "main"\ngit merge x', [2]),
    ("git switch 'main' && git merge x", [1]),
    ("git checkout main && git checkout 'main' && git merge x", [1]),
    ("- `git checkout 'main' && git merge x`", [1]),
    # #502: `-B`/`-C` (and `-b`/`-c`) name the branch you land on
    ("git checkout -B main origin/main && git merge x", [1]),
    ("git checkout -B main && git merge x", [1]),
    ("git switch -C main origin/main\ngit merge x", [2]),
    ("git checkout -b main && git merge x", [1]),
    ("git checkout -q -B 'main' origin/main && git merge x", [1]),
    # #502: a pathspec checkout restores files — it neither opens nor closes the window
    ("git checkout main\ngit checkout -- file.txt\ngit merge x", [3]),
    ("git checkout main && git checkout main -- f && git merge x", [1]),
    ("git checkout main && git checkout HEAD -- a b && git merge x", [1]),
    ("git checkout main && git checkout feat -- f && git merge x", [1]),
    # #502: pull of another branch, rebase and cherry-pick on main land work on main too
    ("git checkout main && git pull origin feat", [1]),
    ("git checkout main\ngit pull --rebase origin feat", [2]),
    ("git switch main && git pull upstream feature/x", [1]),
    ("git checkout main && git pull origin 'feat'", [1]),
    ("git checkout main && git rebase feat", [1]),
    ("git checkout main && git rebase -i HEAD~3", [1]),
    ("git checkout main && git cherry-pick abc123", [1]),
    ("git checkout main\ngit cherry-pick -x abc123", [2]),
    # #502 (landed in #489): `command`/`time`/`nohup` prefixes
    ("command git checkout main && time git merge x", [1]),
    ("nohup git checkout main\ncommand git rebase feat", [2]),
    ("time git switch main && nohup git cherry-pick abc", [1]),
    # #502 controls
    ("git checkout 'main-x' && git merge x", []),
    ("git checkout main && git checkout 'feat' && git merge x", []),
    ("git checkout --detach main && git merge x", []),
    ("git switch --detach main && git merge x", []),
    ("git switch -d main && git merge x", []),
    ("git checkout -B feat main && git merge x", []),
    ("git switch -c feat main && git merge x", []),
    ("git checkout -- file.txt && git merge x", []),
    ("git checkout main && git pull", []),
    ("git checkout main && git pull origin main", []),
    ("git checkout main && git pull --ff-only origin main", []),
    ("git checkout main && git pull origin", []),
    ("git checkout main && git pull 'origin' 'main'", []),
    ("git checkout main && git rebase", []),
    ("git checkout main && git rebase origin/main", []),
    ("git checkout main && git rebase --continue", []),
    ("git checkout main && git cherry-pick --abort", []),
    ("git checkout feat && git rebase main", []),
    ("git checkout feat && git cherry-pick abc", []),
    ("git checkout feat && git pull origin feat", []),
    ("git rebase feat && git checkout main", []),
    # #507: shared leads, git names and scanner fixes reach the merge guard
    ("if git checkout main; then git merge x; fi", [1]),
    ("git checkout main\nuntil git merge x; do sleep 1; done", [2]),
    ("./git checkout main && ~/bin/git merge x", [1]),
    ("builtin command git checkout main && git merge x", [1]),
    ("echo \\ #x && git checkout main && git merge x", [1]),
    ("git checkout main; echo $'\\'' && git merge x; echo 'y'", [1]),
    # #507: `-t`/`--track <remote>/main` creates and lands on main
    ("git checkout -t origin/main && git merge x", [1]),
    ("git checkout --track origin/main\ngit merge x", [2]),
    ("git switch --track origin/main && git merge x", [1]),
    # #507: `reset --hard <x>` and `am` on main land work on main
    ("git checkout main && git reset --hard feat", [1]),
    ("git checkout main\ngit reset -q --hard origin/feat", [2]),
    ("git checkout main && git am 0001.patch", [1]),
    ("git checkout main && git am < series.mbox", [1]),
    ("git checkout main\ngit am -3 patches/x.patch", [2]),
    # #507: `update-ref refs/heads/main` moves main with no checkout at all
    ("git update-ref refs/heads/main feat", [1]),
    ("git update-ref -m 'land' refs/heads/main feat", [1]),
    ("git checkout feat && git update-ref refs/heads/main HEAD", [1]),
    ("git update-ref -d refs/heads/main", [1]),
    # #507 controls — pull flags that take a value, refs/heads/main, other branches
    ("git checkout main && git pull -X ours origin main", []),
    ("git checkout main && git pull -s recursive origin main", []),
    ("git checkout main && git pull --strategy ours origin main", []),
    ("git checkout main && git pull --depth 1 origin", []),
    ("git checkout main && git pull --depth 1 origin main", []),
    ("git checkout main && git rebase refs/heads/main", []),
    ("git checkout main && git pull origin refs/heads/main", []),
    ("git checkout -b feat -t origin/main && git merge x", []),
    ("git checkout -t origin/feat && git merge x", []),
    ("git checkout --track origin/main-x && git merge x", []),
    ("git checkout main && git reset --hard", []),
    ("git checkout main && git reset --hard origin/main", []),
    ("git checkout main && git reset --hard HEAD", []),
    ("git checkout main && git reset file.txt", []),
    ("git checkout feat && git reset --hard main", []),
    ("git checkout main && git am --abort", []),
    ("git checkout feat && git am 0001.patch", []),
    ("git update-ref refs/heads/feat main", []),
    ("git update-ref refs/heads/main-x x", []),
    ("{git checkout main;} && git merge x", []),
    ("true;# git checkout main && git merge x", []),
    # #529: `git branch -f main <x>` / `-C`/`-M … main` move main with no checkout
    ("git branch -f main feat", [1]),
    ("git branch --force main feat", [1]),
    ("git branch -q -f main origin/feat", [1]),
    ("git branch -f main", [1]),
    ("git branch -C main", [1]),
    ("git branch -C feat main", [1]),
    ("git branch -M feat main", [1]),
    ("git checkout feat && git branch -f 'main' HEAD", [1]),
    ("git checkout main && git reset --hard feat~1", [1]),
    # #529 controls
    ("git branch -f feat main", []),
    ("git branch -C main feat", []),
    ("git branch -f main-x x", []),
    ("git branch main", []),
    ("git branch -D main", []),
    ("git branch --contains main", []),
    ("git checkout main && git reset --hard @{u}", []),
    ("git checkout main && git reset --hard @{upstream}", []),
    ("git checkout main && git reset --hard '@{u}'", []),
    ("git checkout main && git reset --hard FETCH_HEAD", []),
    ("git checkout main && git reset --hard ORIG_HEAD", []),
    ("git checkout main && git reset --hard HEAD~1", []),
    ("git checkout main && git reset --hard HEAD^", []),
    ("git checkout main && git reset --hard HEAD~2^", []),
    ("git checkout main && git am --resolved", []),
    # #536: a short cluster holding `f` forces; with `C`/`M` (or `f` plus `m`/`c`) it
    # copies or moves ONTO the last name
    ("git branch -fq main feat", [1]),
    ("git branch -qf main", [1]),
    ("git branch -fm feat main", [1]),
    ("git branch -mf feat main", [1]),
    ("git branch -cf feat main", [1]),
    ("git branch -qC feat main", [1]),
    ("git branch -Mq feat main", [1]),
    ("git checkout main && git am --reject x.patch", [1]),
    ("git checkout main && git am -k x.patch", [1]),
    # #536 controls — another branch forced, or a delete
    ("git branch -fq feat main", []),
    ("git branch -qfm main feat", []),
    ("git branch -fq main-x", []),
    ("git branch -Df main", []),
    ("git branch -fd main", []),
    # #536: `@{push}` and `<remote>/HEAD` are the upstream — a reset onto them syncs
    ("git checkout main && git reset --hard @{push}", []),
    ("git checkout main && git reset --hard origin/HEAD", []),
    ("git checkout main && git reset --hard 'origin/HEAD'", []),
    ("git checkout main && git reset --hard upstream/HEAD", []),
    ("git checkout main && git reset --hard refs/remotes/origin/HEAD", []),
    # #536: `am` in resume mode (any of --continue/--resolved/-r/--skip/--abort/…,
    # wherever it sits) applies nothing — git ignores patch arguments there
    ("git checkout main && git am --resolved x.patch", []),
    ("git checkout main && git am -3 --continue x.patch", []),
    ("git checkout main && git am x.patch --skip", []),
    ("git checkout main && git am -r", []),
    ("git checkout main && git am -3r", []),
    ("git checkout main && git am --show-current-patch=diff", []),
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
    # #502
    "git checkout " + "-- " * 3000 + "x",
    "git checkout " + "x " * 3000 + "--x",
    "git checkout " + "--detach " * 3000 + "main",
    "git switch " + "-C " * 3000 + "main",
    "git checkout " + "'main' " * 3000,
    "git checkout main\ngit pull " + "-a " * 3000 + "x",
    "git checkout main && git pull " + "o " * 3000,
    "git checkout main && git rebase " + "--a=b " * 3000,
    "git checkout main && git cherry-pick " + "-x " * 3000,
    "git checkout main && git rebase " + "a/" * 3000 + "main",
    # #507
    "git checkout main && git pull " + "-X ours " * 2000 + "origin main",
    "git checkout main && git pull " + "-X " * 3000,
    "git checkout main && git reset " + "--hard " * 3000,
    "git checkout main && git reset " + "-q " * 3000 + "--hard x",
    "git update-ref " + "-m x " * 3000 + "refs/heads/main x",
    "git update-ref " + "-m " * 3000,
    "git checkout " + "-t " * 3000 + "origin/main",
    "git checkout main && git rebase " + "refs/heads/" * 2000 + "main",
    "git checkout main && git am " + "--abort " * 3000,
    # #529
    "git branch " + "-f " * 3000 + "main",
    "git branch -C " + "x " * 3000,
    "git branch " + "-q " * 3000 + "-C main",
    "git branch " + "-C " * 3000 + "x",
    "git checkout main && git reset --hard HEAD" + "~1" * 3000,
    "git checkout main && git reset --hard HEAD" + "^" * 5000 + "x",
    # #536
    "git branch -" + "q" * 5000 + "f main",
    "git branch -" + "f" * 5000 + "x main",
    "git branch -" + "f" * 5000 + " x",
    "git branch " + "-fq " * 3000 + "x",
    "git branch -" + "m" * 5000 + " x main",
    "git checkout main && git am " + "x.patch " * 3000,
    "git checkout main && git am -" + "3" * 5000,
    "git checkout main && git reset --hard " + "a." * 3000 + "/HEAD",
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
