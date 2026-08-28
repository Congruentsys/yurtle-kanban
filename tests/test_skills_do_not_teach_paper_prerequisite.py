"""No shipped skill may present a paper as a PREREQUISITE of a slash command.

PR #90 corrected `/hypothesis PAPER-XXX "claim"` to `/hypothesis "claim"`:
a paper is optional across the HDD family, and the CLI has never required one. That
fix is DOC-ONLY and was unpinned — measured on #90's head, replanting the exact
retired form left the suite at 786 passed, identical to the unmutated run.

This is the third instance of one principle, and the first two already have guards:

    test_skill_commands_execute.py          (#85) a printed command must be one the CLI accepts
    test_skills_do_not_teach_direct_push.py (#86) no skill may teach a push to main
    this file                               (#90) no skill may teach a retired ARGUMENT form

The first is the near-miss: its extractor keys on `yurtle-kanban <sub>` invocations,
so it genuinely covers the three CLI lines #90 ADDS and cannot see the three `/slash`
lines #90 CORRECTS. The CLI never sees a slash form, so no CLI-shaped guard ever will.

Why this matters more than a stale doc: `init` GENERATES these files into a new user's
repo. A silent revert here ships wrong instructions into every repo scaffolded
afterwards, and the reader has no way to know the CLI disagrees.

USE-vs-MENTION IS LOAD-BEARING HERE. A bare scan for `PAPER-XXX` goes RED on
skills/hdd/hypothesis/SKILL.md:54 — `no paper — never \`PAPER-XXX\`, and never a number
you chose:` — which is the rule being TAUGHT, not violated. So the match is anchored to
a paper token in COMMAND-ARGUMENT position directly after a slash command, never to the
token anywhere on the line.

# detector-validated: plants `/hypothesis PAPER-XXX "claim"` in a shipped SKILL.md and
# asserts this arm fires naming file:line; separately asserts the negated mention at
# skills/hdd/hypothesis/SKILL.md:54 does NOT fire. Both directions, or it is a one-way
# filter that will under-match next time.
"""

import re
from pathlib import Path

import pytest

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"

# A slash command whose FIRST positional argument is a paper token. The anchor is the
# command, not the token: `never `PAPER-XXX`` has the token and no command, and
# `H{paper}.{n}` has neither in command position.
SLASH_CMDS = "hypothesis|experiment|measure|literature|idea|paper"
# `<paper[A-Za-z0-9_-]*>` and not `<paper>`: the original required `>` immediately
# after `paper`, so every suffixed placeholder slipped through. `<paper-number>` is the
# spelling this repo actually shipped in hdd/hypothesis/SKILL.md until PR #79, so a
# revert to it is the likeliest real regression -- and it passed the guard.
#
# The suffix class covers the arg-hint styles that actually occur: `-number`, a bare
# digit (`<paper-N>`), and snake_case (`<paper_number>`). Its false-positive surface was
# measured against every line of every shipped SKILL.md: 0 new matches (#93 NOTE 1).
#
# It still stops at the closing bracket -- no `>`, `|`, whitespace or `.` -- so it cannot
# reach into surrounding prose, and the literal `paper` stays case-SENSITIVE on purpose:
# see test_the_uppercase_placeholder_is_a_KNOWN_boundary_not_an_oversight for why
# `<PAPER-NUMBER>` is a decision rather than a gap. The mention-list test is the control.
PAPER_TOKEN = r"(?:PAPER-[A-Za-z0-9-]+|\{paper\}|<paper[A-Za-z0-9_-]*>)"
RETIRED_PREREQ = re.compile(rf"/(?:{SLASH_CMDS})\s+`?{PAPER_TOKEN}")


def _skill_files():
    assert SKILLS_DIR.is_dir(), f"skills/ not found at {SKILLS_DIR}"
    files = sorted(SKILLS_DIR.rglob("SKILL.md"))
    assert files, "no SKILL.md found — the glob is broken, not the skills"
    return files


def test_the_scan_is_not_vacuous():
    """A pattern that matched nothing anywhere, or a glob that found nothing, passes forever."""
    files = _skill_files()
    assert len(files) >= 10, f"only {len(files)} skills found; expected the full shipped set"
    assert all(f.read_text().strip() for f in files)
    # the pattern must be capable of matching the form it exists to refuse
    assert RETIRED_PREREQ.search('/hypothesis PAPER-XXX "claim" --target ">=85%"')
    assert RETIRED_PREREQ.search("/experiment PAPER-104 --hypothesis H-1")


def test_the_placeholder_suffix_spellings_are_caught():
    """`<paper>` is not the only placeholder this repo has actually shipped.

    The original character class required `>` immediately after `paper`, so any
    suffixed placeholder slipped through -- and `<paper-number>` is not
    hypothetical: it is the literal spelling `hdd/hypothesis/SKILL.md` carried in
    its argument-hint until PR #79 changed it. A revert to that form is the most
    likely real regression, and it passed the guard.

    These sit in their own test rather than inside the non-vacuity one because
    they assert a different property: that one is "the pattern can match at all",
    this one is "the pattern covers the spellings that shipped".
    """
    for must_catch in (
        '/hypothesis <paper-number> "claim"',
        "/experiment <paper-num> x",
        '/hypothesis <paper> "claim"',
        '/hypothesis PAPER-XXX "claim"',
        # Reviewer-measured residuals (#93 NOTE 1): a bare digit suffix and the snake_case
        # arg-hint style, both of which the lowercase-and-hyphen class missed.
        '/hypothesis <paper-N> "claim"',
        '/hypothesis <paper_number> "claim"',
    ):
        assert RETIRED_PREREQ.search(must_catch), f"retired prerequisite form NOT caught: {must_catch!r}"


def test_the_uppercase_placeholder_is_a_KNOWN_boundary_not_an_oversight():
    """`<PAPER-NUMBER>` is NOT caught, deliberately, and this pins that as a decision.

    Catching it needs the literal `paper` case-folded, which widens the pattern into
    a different risk class than a character-class change: `re.I` on the whole
    expression would also fold the slash commands and `PAPER-`, and nobody has
    measured that false-positive surface. Recorded as a boundary so the next reader
    finds a decision rather than a gap -- and so that if someone DOES fold the case,
    this test fails and makes them state the new coverage on purpose.
    """
    assert not RETIRED_PREREQ.search('/hypothesis <PAPER-NUMBER> "claim"'), (
        "the uppercase placeholder is now caught -- that is a widening, not a bug fix: "
        "measure its false-positive surface against every shipped SKILL.md and update "
        "this test deliberately"
    )


def test_the_pattern_does_not_match_the_rule_being_taught():
    """USE vs MENTION — the negation at hdd/hypothesis/SKILL.md:54 is correct text.

    If this ever fails, the guard has become a one-way filter that would force an
    author to delete the very sentence teaching the rule.
    """
    for mention in (
        "no paper — never `PAPER-XXX`, and never a number you chose:",
        "H{paper}.{n}:     [Statement of testable claim] - Target: [threshold]",
        "| Hypothesis | H{paper}.{n} | TBD |",
        'gh pr create --title "feat(EXPR-{nnn}): Validated H{paper}.{n}"',
        "Add `--paper <n>` once a paper actually exists; the ids become `H{paper}.{n}`",
        "| `/hypothesis` | Formalize testable claim |",
    ):
        assert not RETIRED_PREREQ.search(mention), f"false positive on a legitimate mention: {mention!r}"


@pytest.mark.parametrize("path", _skill_files(), ids=lambda p: str(p.relative_to(SKILLS_DIR)))
def test_skill_does_not_teach_a_paper_prerequisite(path):
    offenders = [
        (n, line.strip())
        for n, line in enumerate(path.read_text().splitlines(), start=1)
        if RETIRED_PREREQ.search(line)
    ]
    assert not offenders, (
        f"{path.relative_to(SKILLS_DIR.parent)} teaches a paper as a PREREQUISITE of a slash "
        "command: " + "; ".join(f"line {n}: {t}" for n, t in offenders)
        + ". A paper is OPTIONAL across the HDD family (#78/#90) and the CLI has never "
        "required one — drop the paper argument, e.g. `/hypothesis \"claim\" --target \">=85%\"`."
    )
