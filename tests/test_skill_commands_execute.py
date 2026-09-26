"""Every command a shipped skill PRINTS must be a command the CLI ACCEPTS.

A skill is executed, not read. A human skims a wrong command and frowns; an agent
runs it. So a skill whose commands have drifted from the CLI is worse than no
skill at all, and nothing in the suite noticed the drift until someone ran the
commands by hand (issue #80).

What this guard covers, and what it deliberately does not:

  covered      the subcommand exists; every long flag the skill passes is a flag
               that subcommand actually accepts
  not covered  whether the command SUCCEEDS against a real board — that needs a
               fixture per command and would couple this to board state. Flag
               and subcommand drift is the failure that has actually happened
               twice, and it is statically decidable from --help.

The extraction is deliberately narrow: only lines inside a fenced block that
START with `yurtle-kanban`. A URL or a flag NAMED in prose is a mention, not a
use, and only a use is a promise about what the CLI accepts.

The CLI's own `--help` output is the second surface (issue #89): the Examples
in each command's help print invocations too, and `--help` is what an agent
reads when it has not read the repo. Both surfaces go through ONE definition of
"a command the CLI accepts" — `_rejection` below — so they cannot drift apart.
"""

import re
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from yurtle_kanban.cli import main

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"

# `yurtle-kanban <sub> [<sub2>] ...` at the start of a line, inside or outside a
# fence — a leading `$` prompt is tolerated, a leading `#` comment is not.
INVOCATION = re.compile(r"^\s*\$?\s*yurtle-kanban\s+([a-z][a-z0-9-]*)(?:\s+([a-z][a-z0-9-]*))?(.*)$")
LONG_FLAG = re.compile(r"(?<![\w-])--[a-z][a-z0-9-]*")

# A trailing shell comment (`--force  # Skip WIP limit check`) is prose, not flags.
SHELL_COMMENT = re.compile(r"\s+#\s.*$")

# Flags handled by the shell/user rather than by click, or documented placeholders.
IGNORED_FLAGS = frozenset({"--help"})

# Render --help unwrapped. At click's default 80 columns an Examples paragraph is
# re-flowed and a flag can be split across lines (`--ready-for-` / `training`);
# wide enough, each paragraph is one line and each option name is intact.
HELP_WIDTH = 10_000

# Side-by-side examples on one line are separated by a run of 3+ spaces (the
# docstring indentation that click keeps when it re-flows a paragraph).
EXAMPLE_SEPARATOR = re.compile(r"\s{3,}")

# The option-name column of an `Options:` row: `  -m, --message TEXT   help...`.
OPTION_ROW = re.compile(r"^  (\S.*?)(?:\s{2,}|$)")


def _parse(line):
    """`(sub, sub2, [long flags])` for a printed `yurtle-kanban ...` command, else None."""
    m = INVOCATION.match(line)
    if not m:
        return None
    sub, sub2, rest = m.group(1), m.group(2), SHELL_COMMENT.sub("", m.group(3) or "")
    return sub, sub2, [f for f in LONG_FLAG.findall(rest) if f not in IGNORED_FLAGS]


def _help_for(parts):
    """The `--help` text of `yurtle-kanban <parts>`, or None if that is not a command."""
    res = CliRunner().invoke(
        main, [*parts, "--help"], terminal_width=HELP_WIDTH, max_content_width=HELP_WIDTH
    )
    return res.output if res.exit_code == 0 else None


def _declared_flags(help_text):
    """Long flags named in the `Options:` section — NOT anywhere in the help.

    The Examples above `Options:` print flags too; matching against the whole help
    would let an example vouch for itself and the epilog guard could never fail.
    """
    _, sep, options = help_text.partition("\nOptions:\n")
    assert sep, "help text has no Options: section"
    flags = set()
    for row in options.splitlines():
        m = OPTION_ROW.match(row)
        if m:
            flags.update(LONG_FLAG.findall(m.group(1)))
    return flags


def _rejection(sub, sub2, flags):
    """Why the CLI would reject `yurtle-kanban sub [sub2] <flags>`, or None if it accepts it.

    The one definition of "a command the CLI accepts" shared by every surface.
    """
    # Resolve the longest subcommand path that exists: `hypothesis create` before
    # `hypothesis`, so a flag is checked against the command that receives it.
    parts, help_text = (sub,), _help_for((sub,))
    if help_text is None:
        return f"`yurtle-kanban {sub}` is not a subcommand"
    if sub2 is not None:
        deeper = _help_for((sub, sub2))
        if deeper is not None:
            parts, help_text = (sub, sub2), deeper
        elif isinstance(main.commands[sub], click.Group):
            # `board research` is a command plus an argument; `hdd bogus` is a
            # group given a subcommand it does not have.
            return f"`yurtle-kanban {sub} {sub2}` is not a subcommand"

    declared = _declared_flags(help_text)
    for flag in flags:
        if flag not in declared:
            return f"`{flag}` is not accepted by `yurtle-kanban {' '.join(parts)}`"
    return None


def _skill_files():
    if not SKILLS_DIR.is_dir():  # pragma: no cover - packaging accident
        pytest.fail(f"skills/ not found at {SKILLS_DIR}")
    return sorted(SKILLS_DIR.rglob("SKILL.md"))


def _invocations():
    """(file, lineno, subcommand-path, [long flags]) for every command a skill prints."""
    out = []
    for path in _skill_files():
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            parsed = _parse(line)
            if parsed:
                out.append((path, lineno, *parsed))
    return out


def test_extraction_is_not_vacuous():
    """A guard that matched nothing would pass forever."""
    invocations = _invocations()
    assert len(invocations) >= 20, (
        f"only {len(invocations)} invocations extracted from "
        f"{len(_skill_files())} skill files — the extractor is probably broken"
    )
    # and it must be finding flags, or the flag assertion below is vacuous too
    assert sum(len(f) for _, _, _, _, f in invocations) >= 10


def _case_id(case):
    """`skills/status/SKILL.md:34 list` — the file:line a failure must send you to."""
    path, lineno, sub, sub2, _flags = case
    rel = path.relative_to(SKILLS_DIR.parent)
    return f"{rel}:{lineno} {sub}{'/' + sub2 if sub2 else ''}"


@pytest.mark.parametrize(
    "path,lineno,sub,sub2,flags",
    _invocations(),
    ids=[_case_id(c) for c in _invocations()],
)
def test_skill_command_is_accepted_by_the_cli(path, lineno, sub, sub2, flags):
    rel = path.relative_to(SKILLS_DIR.parent)
    problem = _rejection(sub, sub2, flags)
    assert problem is None, (
        f"{rel}:{lineno} — {problem}. A skill is executed, not read: "
        f"an agent following this line gets a usage error."
    )


# --- surface 2: the CLI's own --help Examples (issue #89) ------------------------


def _command_paths(group=main, prefix=()):
    """Every subcommand path, nested groups included: `("hdd", "critical-path")`."""
    for name in sorted(group.commands):
        cmd = group.commands[name]
        yield (*prefix, name)
        if isinstance(cmd, click.Group):
            yield from _command_paths(cmd, (*prefix, name))


def _help_examples(help_for=_help_for):
    """(help path, example, sub, sub2, [long flags]) for every example `--help` prints."""
    out = []
    for parts in _command_paths():
        help_text = help_for(parts)
        assert help_text is not None, f"`yurtle-kanban {' '.join(parts)} --help` failed"
        for line in help_text.splitlines():
            for chunk in EXAMPLE_SEPARATOR.split(line.strip()):
                parsed = _parse(chunk)
                if parsed:
                    out.append((parts, chunk, *parsed))
    return out


def test_help_example_extraction_is_not_vacuous():
    """The walk must reach nested groups and find flags, or the guard below is hollow."""
    paths = list(_command_paths())
    examples = _help_examples()
    helps_with_examples = {parts for parts, *_ in examples}
    assert ("hdd", "critical-path") in paths and ("epic", "create") in paths, (
        "the command walk is not descending into nested groups"
    )
    assert len(examples) >= 60, f"only {len(examples)} --help examples extracted"
    assert any(len(parts) > 1 for parts in helps_with_examples), "no nested-group examples"
    assert sum(len(f) for *_, f in examples) >= 30


def test_side_by_side_examples_are_split():
    """Two examples on one help line are two examples, not one with the other's flags."""
    line = "  yurtle-kanban history --since 2026-01-01     yurtle-kanban history --by-assignee"
    chunks = [_parse(c) for c in EXAMPLE_SEPARATOR.split(line.strip())]
    assert chunks == [("history", None, ["--since"]), ("history", None, ["--by-assignee"])]


def test_guard_rejects_a_planted_bad_example():
    """Proof the guard has teeth: a drifted example in a help text is caught."""

    def planted(parts):
        text = _help_for(parts)
        if parts == ("history",):
            text = text.replace(
                "yurtle-kanban history --week",
                "yurtle-kanban history --week     yurtle-kanban history --fortnight",
            )
        return text

    bad = [e for e in _help_examples(planted) if "--fortnight" in e[-1]]
    assert len(bad) == 1
    _parts, _example, sub, sub2, flags = bad[0]
    assert _rejection(sub, sub2, flags) == (
        "`--fortnight` is not accepted by `yurtle-kanban history`"
    )


def _help_case_id(case):
    """`hdd critical-path: yurtle-kanban hdd critical-path --json`."""
    parts, example, *_ = case
    return f"{' '.join(parts)}: {example}"


@pytest.mark.parametrize(
    "parts,example,sub,sub2,flags",
    _help_examples(),
    ids=[_help_case_id(c) for c in _help_examples()],
)
def test_help_example_is_accepted_by_the_cli(parts, example, sub, sub2, flags):
    problem = _rejection(sub, sub2, flags)
    assert problem is None, (
        f"`yurtle-kanban {' '.join(parts)} --help` prints `{example}` — {problem}. "
        f"--help is what an agent reads first: it runs this and gets a usage error."
    )


# --- blind spots found reviewing #89 (issue #476) --------------------------------


def _verdict(line):
    """What the guard says about one printed line: a rejection, None, or "unparsed"."""
    parsed = _parse(line)
    return "unparsed" if parsed is None else _rejection(*parsed)


@pytest.mark.parametrize(
    "line,expected",
    [
        # short flags are checked, not skipped
        ("yurtle-kanban move EXP-1 done -f", None),
        ("yurtle-kanban move EXP-1 done -z", "`-z` is not accepted by `yurtle-kanban move`"),
        ("yurtle-kanban move EXP-1 done -m 'msg' -a Mini", None),
        # an env-var prefix is still a command
        ("KANBAN_ROOT=/tmp/b yurtle-kanban list --status done", None),
        (
            "KANBAN_ROOT=/tmp/b yurtle-kanban list --bogus",
            "`--bogus` is not accepted by `yurtle-kanban list`",
        ),
        # a main-level option before any subcommand
        ("yurtle-kanban --version", None),
        ("yurtle-kanban --bogus list", "`--bogus` is not accepted by `yurtle-kanban`"),
        # a quoted value that starts with `--` is a value, not a flag
        ('yurtle-kanban experiment run EXPR-1 --params "--x=1"', None),
        ("yurtle-kanban experiment run EXPR-1 --params '--x=1'", None),
        ("yurtle-kanban history --since=2026-01-01", None),
        # unchanged: a group given a subcommand it lacks; a command plus an argument
        ("yurtle-kanban hdd validat --strict", "`yurtle-kanban hdd validat` is not a subcommand"),
        ("yurtle-kanban board research", None),
    ],
)
def test_guard_verdicts(line, expected):
    assert _verdict(line) == expected


def test_hidden_option_alias_is_accepted(monkeypatch):
    """An alias hidden from Options: is still accepted by click, so the guard accepts it."""
    history = main.commands["history"]
    hidden = click.Option(["--last-week"], is_flag=True, hidden=True)
    monkeypatch.setattr(history, "params", [*history.params, hidden])
    assert _verdict("yurtle-kanban history --last-week") is None


def test_depth_three_command_path(monkeypatch):
    """A group nested in a group: flags go to the leaf, not to the middle group."""

    @click.group()
    def deep():
        pass

    @deep.command()
    @click.option("--x", is_flag=True)
    def leaf(x):
        pass

    monkeypatch.setitem(main.commands["hdd"].commands, "deep", deep)
    assert _verdict("yurtle-kanban hdd deep leaf --x") is None
    assert _verdict("yurtle-kanban hdd deep leaf --y") == (
        "`--y` is not accepted by `yurtle-kanban hdd deep leaf`"
    )
    assert _verdict("yurtle-kanban hdd deep nope") == (
        "`yurtle-kanban hdd deep nope` is not a subcommand"
    )


# A line that names `yurtle-kanban` followed by more text, not inside a URL or path.
MENTION = re.compile(r"(?<![\w/.-])yurtle-kanban\s+\S")

# Mentions that are deliberately NOT commands. Each one needs a reason; anything
# else that mentions `yurtle-kanban` must parse, or the guard is silently blind.
NOT_COMMANDS = {
    re.compile(r"Bash\(yurtle-kanban \*\)"): "an allowed-tools permission glob",
    re.compile(r"pip index versions yurtle-kanban"): "the package name, passed to pip",
    re.compile(r"^## yurtle-kanban "): "a markdown heading",
    re.compile(r"^Initialize yurtle-kanban in "): "init's one-line description",
}


def _commands_in(line):
    """Every command a line prints."""
    parsed = _parse(line)
    return [parsed] if parsed else []


def _mention_lines():
    """(where, text) for every skill line and --help chunk that mentions yurtle-kanban."""
    out = []
    for path in _skill_files():
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            out.append((f"{path.relative_to(SKILLS_DIR.parent)}:{lineno}", line))
    for parts in [(), *_command_paths()]:
        for line in _help_for(parts).splitlines():
            for chunk in EXAMPLE_SEPARATOR.split(line.strip()):
                out.append((f"yurtle-kanban {' '.join(parts)} --help".replace("  ", " "), chunk))
    return [(where, text) for where, text in out if MENTION.search(text)]


def test_every_mention_parses_or_is_allow_listed():
    """No silent skips: a line naming yurtle-kanban is checked, or says why not."""
    mentions = _mention_lines()
    assert len(mentions) >= 100, f"only {len(mentions)} mentions — MENTION is broken"
    blind = [
        f"{where}: {text.strip()}"
        for where, text in mentions
        if not _commands_in(text) and not any(p.search(text) for p in NOT_COMMANDS)
    ]
    assert not blind, "mentions the guard neither checks nor allow-lists:\n" + "\n".join(blind)
    # and every allow-list entry still earns its place
    for pattern, reason in NOT_COMMANDS.items():
        assert any(pattern.search(t) for _, t in mentions), f"stale allow-list: {reason}"
