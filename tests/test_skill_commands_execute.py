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
"""

import re
from pathlib import Path

import pytest
from click.testing import CliRunner

from yurtle_kanban.cli import main

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"

# `yurtle-kanban <sub> [<sub2>] ...` at the start of a line, inside or outside a
# fence — a leading `$` prompt is tolerated, a leading `#` comment is not.
INVOCATION = re.compile(r"^\s*\$?\s*yurtle-kanban\s+([a-z][a-z0-9-]*)(?:\s+([a-z][a-z0-9-]*))?(.*)$")
LONG_FLAG = re.compile(r"(?<![\w-])--[a-z][a-z0-9-]*")

# Flags handled by the shell/user rather than by click, or documented placeholders.
IGNORED_FLAGS = frozenset({"--help"})


def _skill_files():
    if not SKILLS_DIR.is_dir():  # pragma: no cover - packaging accident
        pytest.fail(f"skills/ not found at {SKILLS_DIR}")
    return sorted(SKILLS_DIR.rglob("SKILL.md"))


def _invocations():
    """(file, lineno, subcommand-path, [long flags]) for every command a skill prints."""
    out = []
    for path in _skill_files():
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            m = INVOCATION.match(line)
            if not m:
                continue
            sub, sub2, rest = m.group(1), m.group(2), m.group(3) or ""
            flags = [f for f in LONG_FLAG.findall(rest) if f not in IGNORED_FLAGS]
            out.append((path, lineno, sub, sub2, flags))
    return out


def _help_for(runner, parts):
    res = runner.invoke(main, list(parts) + ["--help"])
    return res.output if res.exit_code == 0 else None


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
    runner = CliRunner()
    rel = path.relative_to(SKILLS_DIR.parent)

    # Resolve the longest subcommand path that exists: `hypothesis create` before
    # `hypothesis`, so a flag is checked against the command that receives it.
    parts, help_text = (sub,), _help_for(runner, (sub,))
    if sub2 is not None:
        deeper = _help_for(runner, (sub, sub2))
        if deeper is not None:
            parts, help_text = (sub, sub2), deeper

    assert help_text is not None, f"{rel}:{lineno} — `yurtle-kanban {sub}` is not a subcommand"

    for flag in flags:
        assert flag in help_text, (
            f"{rel}:{lineno} — `{flag}` is not accepted by "
            f"`yurtle-kanban {' '.join(parts)}`. A skill is executed, not read: "
            f"an agent following this line gets a usage error."
        )
