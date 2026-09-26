"""Every command a shipped skill PRINTS must be a command the CLI ACCEPTS.

A skill is executed, not read. A human skims a wrong command and frowns; an agent
runs it. So a skill whose commands have drifted from the CLI is worse than no
skill at all, and nothing in the suite noticed the drift until someone ran the
commands by hand (issue #80).

What this guard covers, and what it deliberately does not:

  covered      the subcommand path exists, at any depth; every flag the line
               passes, long or short, is one that command actually declares
               (read from click, so a hidden alias counts)
  not covered  whether the command SUCCEEDS against a real board — that needs a
               fixture per command and would couple this to board state. Flag
               and subcommand drift is the failure that has actually happened
               twice, and it is statically decidable from --help.

The extraction is deliberately narrow: a line that STARTS with `yurtle-kanban`
(after an optional `$` prompt and `VAR=value` env prefixes), or a `yurtle-kanban
...` backtick span. A URL or a flag NAMED in prose is a mention, not a use, and
only a use is a promise about what the CLI accepts. Narrow must not mean blind:
every other line that mentions `yurtle-kanban` is on the NOT_COMMANDS allow-list
with a reason (issue #476). That mention check only sees the name
`yurtle-kanban`: a shell alias would slip past it, and none exists today (#488).

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

# Optional `$` prompt, then optional `VAR=value` env prefixes, then the command.
_ENV = r"(?:[A-Z_][A-Z0-9_]*=\S*\s+)*"
INVOCATION = re.compile(r"^\s*\$?\s*" + _ENV + r"yurtle-kanban(?=\s|$)(.*)$")
BACKTICK_INVOCATION = re.compile(r"`(" + _ENV + r"yurtle-kanban(?:\s[^`]*)?)`")

# A quoted argument is one value, whatever it looks like: `--params "--x=1"`.
QUOTED = re.compile(r"\"[^\"]*\"|'[^']*'")

# `-f`, `-abc`, `--flag`, `--flag=value` — not `-1` or a bare `-`.
OPTION_TOKEN = re.compile(r"^--?[A-Za-z]")

# A trailing shell comment (`--force  # Skip WIP limit check`) is prose, not flags.
SHELL_COMMENT = re.compile(r"\s+#\s.*$")

# Render --help unwrapped. At click's default 80 columns an Examples paragraph is
# re-flowed and a flag can be split across lines (`--ready-for-` / `training`);
# wide enough, each paragraph is one line and each option name is intact.
HELP_WIDTH = 10_000

# Side-by-side examples on one line are separated by a run of 3+ spaces (the
# docstring indentation that click keeps when it re-flows a paragraph).
EXAMPLE_SEPARATOR = re.compile(r"\s{3,}")

def _parse(line):
    """The argv words after `yurtle-kanban` in a printed command, or None.

    Quoted arguments collapse to one placeholder word, so a value that looks like
    a flag is not read as one.
    """
    m = INVOCATION.match(line)
    if not m:
        return None
    rest = SHELL_COMMENT.sub("", QUOTED.sub("ARG", m.group(1)))
    return tuple(rest.split())


def _commands_in(line):
    """Every command a line prints: the whole line, or each backtick span."""
    parsed = _parse(line)
    if parsed is not None:
        return [parsed]
    return [p for span in BACKTICK_INVOCATION.findall(line) if (p := _parse(span)) is not None]


def _help_for(parts):
    """The `--help` text of `yurtle-kanban <parts>`, or None if that is not a command."""
    res = CliRunner().invoke(
        main, [*parts, "--help"], terminal_width=HELP_WIDTH, max_content_width=HELP_WIDTH
    )
    return res.output if res.exit_code == 0 else None


def _declared_options(cmd):
    """{option string: param} for everything `cmd` accepts — from click, not --help.

    Reading the params rather than the rendered `Options:` section counts a
    `hidden=True` alias (click accepts it; --help does not show it) and can never
    be fooled by an Example that prints the very flag it is supposed to check.
    """
    declared = {}
    for param in cmd.get_params(click.Context(cmd)):
        if isinstance(param, click.Option):
            for opt in (*param.opts, *param.secondary_opts):
                declared[opt] = param
    return declared


def _takes_value(param):
    return not (param.is_flag or param.count)


def _needs(param):
    """`requires a value` / `requires 2 values` — what a value-taking `param` is missing."""
    return "requires a value" if param.nargs <= 1 else f"requires {param.nargs} values"


def _rejection(*words, root=main):
    """Why the CLI would reject `yurtle-kanban <words>`, or None if it accepts it.

    The one definition of "a command the CLI accepts" shared by every surface.
    Words are consumed the way click does: while at a group, a word names a
    subcommand (so any depth resolves); an option is checked against the command
    it is given to, and consumes its value words; anything else is an argument.
    `--` ends options for the command it is given to — at a group the next word
    is still a subcommand, whose own options are parsed as usual.
    """
    cmd, path, i = root, [], 0
    options_ended = False
    while i < len(words):
        word = words[i]
        i += 1
        where = " ".join(["yurtle-kanban", *path])
        if word == "--" and not options_ended:
            options_ended = True
            continue
        if isinstance(cmd, click.Group) and (options_ended or not OPTION_TOKEN.match(word)):
            if word not in cmd.commands:
                return f"`{where} {word}` is not a subcommand"
            cmd = cmd.commands[word]
            path.append(word)
            options_ended = False
            continue
        if options_ended or not OPTION_TOKEN.match(word):
            continue  # a positional argument of a leaf command
        declared = _declared_options(cmd)
        if word.startswith("--"):
            name, eq, _ = word.partition("=")
            param = declared.get(name)
            if param is None:
                return f"`{name}` is not accepted by `{where}`"
            if not _takes_value(param):
                if eq:
                    return f"`{name}` does not take a value in `{where}`"
                continue
            wanted = max(param.nargs, 1) - (1 if eq else 0)
        else:
            # `-fm msg`, `-fmmsg`: flags, until a value-taking option; the rest of
            # the cluster is its first value, and it takes more words if it needs them.
            wanted = 0
            for j, ch in enumerate(word[1:], start=2):
                name, param = f"-{ch}", declared.get(f"-{ch}")
                if param is None:
                    return f"`{name}` is not accepted by `{where}`"
                if _takes_value(param):
                    wanted = max(param.nargs, 1) - (1 if word[j:] else 0)
                    break
        if i + wanted > len(words):
            return f"`{name}` {_needs(param)} in `{where}`"
        i += wanted
    return None


def _skill_files():
    if not SKILLS_DIR.is_dir():  # pragma: no cover - packaging accident
        pytest.fail(f"skills/ not found at {SKILLS_DIR}")
    return sorted(SKILLS_DIR.rglob("SKILL.md"))


def _invocations():
    """(file, lineno, argv words) for every command a skill prints."""
    out = []
    for path in _skill_files():
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            for words in _commands_in(line):
                out.append((path, lineno, words))
    return out


def _flags(words):
    return [w for w in words if OPTION_TOKEN.match(w)]


def test_extraction_is_not_vacuous():
    """A guard that matched nothing would pass forever."""
    invocations = _invocations()
    assert len(invocations) >= 20, (
        f"only {len(invocations)} invocations extracted from "
        f"{len(_skill_files())} skill files — the extractor is probably broken"
    )
    # and it must be finding flags, or the flag assertion below is vacuous too
    assert sum(len(_flags(w)) for _, _, w in invocations) >= 10


def _case_id(case):
    """`skills/status/SKILL.md:34 list` — the file:line a failure must send you to."""
    path, lineno, words = case
    rel = path.relative_to(SKILLS_DIR.parent)
    return f"{rel}:{lineno} {' '.join(words[:2])}"


@pytest.mark.parametrize(
    "path,lineno,words",
    _invocations(),
    ids=[_case_id(c) for c in _invocations()],
)
def test_skill_command_is_accepted_by_the_cli(path, lineno, words):
    rel = path.relative_to(SKILLS_DIR.parent)
    problem = _rejection(*words)
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


def _help_chunks(help_for=_help_for):
    """(help path, chunk) for every example-sized piece of every --help, root included."""
    out = []
    for parts in [(), *_command_paths()]:
        help_text = help_for(parts)
        assert help_text is not None, f"`yurtle-kanban {' '.join(parts)} --help` failed"
        for line in help_text.splitlines():
            for chunk in EXAMPLE_SEPARATOR.split(line.strip()):
                out.append((parts, chunk))
    return out


def _help_examples(help_for=_help_for):
    """(help path, example, argv words) for every example `--help` prints."""
    return [
        (parts, chunk, words)
        for parts, chunk in _help_chunks(help_for)
        for words in _commands_in(chunk)
    ]


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
    assert sum(len(_flags(w)) for *_, w in examples) >= 30
    # `_rejection` resolves any depth (test_depth_three_command_path), but no real
    # depth-3 path exists to walk. When one lands, raise this bound deliberately.
    assert max(len(p) for p in paths) <= 2, "a depth-3 command path now exists"


def test_side_by_side_examples_are_split():
    """Two examples on one help line are two examples, not one with the other's flags."""
    line = "  yurtle-kanban history --since 2026-01-01     yurtle-kanban history --by-assignee"
    chunks = [_parse(c) for c in EXAMPLE_SEPARATOR.split(line.strip())]
    assert chunks == [("history", "--since", "2026-01-01"), ("history", "--by-assignee")]


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
    _parts, _example, words = bad[0]
    assert _rejection(*words) == (
        "`--fortnight` is not accepted by `yurtle-kanban history`"
    )


def _help_case_id(case):
    """`hdd critical-path: yurtle-kanban hdd critical-path --json`."""
    parts, example, *_ = case
    return f"{' '.join(parts)}: {example}"


@pytest.mark.parametrize(
    "parts,example,words",
    _help_examples(),
    ids=[_help_case_id(c) for c in _help_examples()],
)
def test_help_example_is_accepted_by_the_cli(parts, example, words):
    problem = _rejection(*words)
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
        # #488: `--` at a group ends the group's options, not subcommand resolution
        ("yurtle-kanban -- bogus", "`yurtle-kanban bogus` is not a subcommand"),
        ("yurtle-kanban -- list --bogus", "`--bogus` is not accepted by `yurtle-kanban list`"),
        ("yurtle-kanban -- list --status done", None),
        ("yurtle-kanban move EXP-1 done -- --not-an-option", None),
        # #488: a value given to a flag
        (
            "yurtle-kanban move EXP-1 done --force=yes",
            "`--force` does not take a value in `yurtle-kanban move`",
        ),
        ("yurtle-kanban move EXP-1 done --force", None),
        # #488: a value-taking option at the end of the line, with no value
        ("yurtle-kanban list --status", "`--status` requires a value in `yurtle-kanban list`"),
        ("yurtle-kanban list --status=", None),
        # #488: a short cluster ending in a value-taking option takes the next word
        ("yurtle-kanban move EXP-1 done -fm msg", None),
        ("yurtle-kanban move EXP-1 done -fm --not-an-option", None),
        ("yurtle-kanban move EXP-1 done -fmmsg", None),
        ("yurtle-kanban move EXP-1 done -fm", "`-m` requires a value in `yurtle-kanban move`"),
        # #488: nargs=2 (`--between`, planted below): `--opt=a b` is both values
        ("yurtle-kanban history --between=a --not-an-option", None),
        ("yurtle-kanban history --between a --not-an-option", None),
        (
            "yurtle-kanban history --between a",
            "`--between` requires 2 values in `yurtle-kanban history`",
        ),
        (
            "yurtle-kanban history --between=a",
            "`--between` requires 2 values in `yurtle-kanban history`",
        ),
        # #516: after `--` at a group, click's resolve_command re-parses an
        # option-looking word as the group's option: an eager one (--version,
        # --help) runs, an unknown one is "No such option", and any other known
        # one falls through to "No such command"
        ("yurtle-kanban -- --version", None),
        ("yurtle-kanban -- --help", None),
        ("yurtle-kanban hdd -- --help", None),
        ("yurtle-kanban -- --version list", None),
        ("yurtle-kanban -- --bogus", "`--bogus` is not accepted by `yurtle-kanban`"),
        ("yurtle-kanban hdd -- --bogus", "`--bogus` is not accepted by `yurtle-kanban hdd`"),
        ("yurtle-kanban -- --version --bogus", "`--bogus` is not accepted by `yurtle-kanban`"),
        ("yurtle-kanban hdd -- --quiet", "`yurtle-kanban hdd --quiet` is not a subcommand"),
        ("yurtle-kanban hdd -- --quiet --help", None),
        ("yurtle-kanban hdd --quiet validate", None),
    ],
)
def test_guard_verdicts(monkeypatch, line, expected):
    # No real option takes nargs=2, and no group has a non-eager option: plant
    # one of each (hidden) so the rows above exercise them.
    history = main.commands["history"]
    between = click.Option(["--between"], nargs=2, hidden=True)
    monkeypatch.setattr(history, "params", [*history.params, between])
    hdd = main.commands["hdd"]
    quiet = click.Option(["--quiet"], is_flag=True, hidden=True)
    monkeypatch.setattr(hdd, "params", [*hdd.params, quiet])
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
    re.compile(r"^allowed-tools:.*Bash\(yurtle-kanban \*\)"): "an allowed-tools permission glob",
    re.compile(r"^pip index versions yurtle-kanban(?=\s|$)"): "the package name, passed to pip",
    re.compile(r"^## yurtle-kanban "): "a markdown heading",
    re.compile(r"^Initialize yurtle-kanban in "): "init's one-line description",
}


def _allow_listed(text):
    """True if `text` is a NOT_COMMANDS mention."""
    return any(p.search(text) for p in NOT_COMMANDS)


def _mention_lines():
    """(where, text) for every skill line and --help chunk that mentions yurtle-kanban."""
    out = []
    for path in _skill_files():
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            out.append((f"{path.relative_to(SKILLS_DIR.parent)}:{lineno}", line))
    for parts, chunk in _help_chunks():
        out.append((" ".join(["yurtle-kanban", *parts, "--help"]), chunk))
    return [(where, text) for where, text in out if MENTION.search(text)]


@pytest.mark.parametrize(
    "text,allowed",
    [
        ("allowed-tools: Bash(yurtle-kanban *), Bash(git *)", True),
        ("pip index versions yurtle-kanban 2>/dev/null | head -2", True),
        # #488: the same words elsewhere on a line are not the allow-listed use
        ("Then run Bash(yurtle-kanban *) to see", False),
        ("cd x && pip index versions yurtle-kanban", False),
        ("pip index versions yurtle-kanban-extra", False),
        # #516: prose on an allow-listed line must not hide a command mention
        ("allowed-tools: Bash(yurtle-kanban *) — then yurtle-kanban move X done", False),
        ("pip index versions yurtle-kanban 2>/dev/null   # or yurtle-kanban list", False),
        ("## yurtle-kanban HDD Board: run yurtle-kanban hdd validate", False),
        ("## yurtle-kanban HDD Board", True),
    ],
)
def test_allow_list_is_anchored(text, allowed):
    assert _allow_listed(text) is allowed


def test_every_mention_parses_or_is_allow_listed():
    """No silent skips: a line naming yurtle-kanban is checked, or says why not."""
    mentions = _mention_lines()
    assert len(mentions) >= 100, f"only {len(mentions)} mentions — MENTION is broken"
    blind = [
        f"{where}: {text.strip()}"
        for where, text in mentions
        if not _commands_in(text) and not _allow_listed(text)
    ]
    assert not blind, "mentions the guard neither checks nor allow-lists:\n" + "\n".join(blind)
    # and every allow-list entry still earns its place
    for pattern, reason in NOT_COMMANDS.items():
        assert any(pattern.search(t) for _, t in mentions), f"stale allow-list: {reason}"
