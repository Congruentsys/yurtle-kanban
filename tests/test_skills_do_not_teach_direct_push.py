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
from pathlib import Path

import pytest

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"

# `git push [flags] <remote> main` — the thing forbidden. `--force-with-lease origin
# <branch>` is fine, so the branch name is what decides, not the flags.
PUSH_TO_MAIN = re.compile(r"^\s*\$?\s*git\s+push\b(?![^\n]*--dry-run)[^\n]*\bmain\b\s*$")

# `git checkout main` immediately preceding a merge is the other half of the recipe:
# it is how you end up ON main with something to push.
CHECKOUT_MAIN = re.compile(r"^\s*\$?\s*git\s+checkout\s+main\s*$")
MERGE = re.compile(r"^\s*\$?\s*git\s+merge\b")


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
        if PUSH_TO_MAIN.match(line)
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
