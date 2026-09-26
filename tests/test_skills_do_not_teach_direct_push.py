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
# A segment may open a subshell `(`, and the command may sit behind `sudo`/`env`
# (with options) and `KEY=val` assignments — each prefix token starts differently
# (a word, a dash, `NAME=`), so the prefix group has one parse and stays linear.
# `-n` right after `-o`/`--push-option` is that option's VALUE, not a dry run.
_CMD_PREFIX = (
    r"(?:(?:sudo|env)\s+(?:(?:(?:-u|--user|-C|--chdir)\s+[^\s-]\S*"
    r"|--?[A-Za-z][\w-]*(?:=\S+)?)\s+)*"
    r"|[A-Za-z_]\w*=(?:'[^']*'|\"[^\"]*\"|[^\s'\"]\S*|)\s+)*"
)
PUSH_TO_MAIN = re.compile(
    r"^\s*(?:(?:[-*>`$(]|\d+[.)])\s*)*"
    + _CMD_PREFIX
    + r"git\s+(?:(?:(?:-[Cc]|--(?:git-dir|work-tree|namespace))\s+[^\s-]\S*"
    r"|--?[A-Za-z][\w-]*(?:=\S+)?)\s+)*push\b"
    r"(?![^#;&|\n]*(?:--dry-run"
    r"|(?<!\s-o)(?<!--push-option)\s-(?=[A-Za-z]*n)[A-Za-z]+(?![\w-])))"
    r"[^#\n]*(?<=[\s:+'\"])(?:refs/heads/)?main(?=[\s#;&|'\"`)]|$)"
)

# `git checkout main` immediately preceding a merge is the other half of the recipe:
# it is how you end up ON main with something to push.
CHECKOUT_MAIN = re.compile(r"^\s*\$?\s*git\s+checkout\s+main\s*$")
MERGE = re.compile(r"^\s*\$?\s*git\s+merge\b")


# A shell comment starts at a word boundary: `main#x` is still a word, `x # y` is not.
_COMMENT = re.compile(r"(?<!\S)#")
_SEGMENT_SEP = re.compile(r"&&|\|\||[;&|]")


def refuses_push_to_main(line: str) -> bool:
    """The guard applied to one skill line: any shell segment that pushes to main."""
    code = _COMMENT.split(line, maxsplit=1)[0]
    return any(PUSH_TO_MAIN.match(seg) for seg in _SEGMENT_SEP.split(code))


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
]


@pytest.mark.parametrize("line,refused", PUSH_TO_MAIN_CASES, ids=[c[0] for c in PUSH_TO_MAIN_CASES])
def test_push_to_main_guard_table(line, refused):
    assert refuses_push_to_main(line) is refused, (
        f"PUSH_TO_MAIN {'missed' if refused else 'falsely refused'}: {line!r}"
    )


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
]

# The child imports the guard FUNCTION (#472), so line splitting is timed too.
_TIMED_MATCH = (
    "import sys, time\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "from test_skills_do_not_teach_direct_push import refuses_push_to_main\n"
    "t = time.perf_counter()\n"
    "refuses_push_to_main(sys.argv[2])\n"
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
    offenders = [
        (n, line.strip())
        for n, line in enumerate(path.read_text().splitlines(), start=1)
        if refuses_push_to_main(line)
    ]
    assert not offenders, (
        f"{path.relative_to(SKILLS_DIR.parent)} teaches a push to main: "
        + "; ".join(f"line {n}: {t}" for n, t in offenders)
        + ". Merge through a pull request instead (see skills/nautical/review/SKILL.md)."
    )


@pytest.mark.parametrize("path", _skill_files(), ids=lambda p: str(p.relative_to(SKILLS_DIR)))
def test_skill_does_not_merge_on_main(path):
    """`git checkout main` followed by `git merge` within a few lines."""
    lines = path.read_text().splitlines()
    for n, line in enumerate(lines):
        if not CHECKOUT_MAIN.match(line):
            continue
        window = lines[n + 1 : n + 6]
        merges = [w.strip() for w in window if MERGE.match(w)]
        assert not merges, (
            f"{path.relative_to(SKILLS_DIR.parent)}:{n + 1} checks out main and then merges "
            f"({merges[0]}). That is the local half of a direct-to-main landing; "
            f"merge through a pull request instead."
        )
