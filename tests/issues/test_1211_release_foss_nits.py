"""Issue #1211 — follow-ups to the #1201 release-skill split.

1. `skills/release-foss/SKILL.md` runs `wc -m` (step 8) and `pip index versions` (step 9)
   but its `allowed-tools` granted only git, grep and gh, so those steps prompted.
2. AGENT-QUICK-REF.md and the `done` skills named only `/release`, which is now the
   INTERNAL flow; a public repo needs `/release-foss`.
3. The repo-local release skill's credit step pointed at release-foss's `OWNER/REPO`
   placeholder instead of naming this repo.
4. README: a public consumer re-running `init` gets the internal `/release`, so the
   upgrade note tells them to switch to `/release-foss`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
FOSS = REPO / "skills" / "release-foss" / "SKILL.md"
LOCAL = REPO / ".claude" / "skills" / "release-yurtle-kanban" / "SKILL.md"
QUICK_REF = REPO / "AGENT-QUICK-REF.md"
DONE_SKILLS = [
    REPO / "skills" / "software" / "done" / "SKILL.md",
    REPO / "skills" / "nautical" / "done" / "SKILL.md",
]


def allowed_tools(path: Path) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("allowed-tools:"):
            return line
    raise AssertionError(f"{path.relative_to(REPO)} has no allowed-tools line")


# The programs release-foss's steps run: every pipeline stage of its bash blocks, plus
# the registry checks its step 9 table tells you to run.
FOSS_PROGRAMS = (
    "git",
    "grep",
    "gh",
    "wc",
    "head",
    "sort",
    "tr",
    "paste",
    "pip",
    "npm view",
    "cargo search",
)


@pytest.mark.parametrize("program", FOSS_PROGRAMS)
def test_release_foss_allowed_tools_cover_what_it_runs(program: str) -> None:
    line = allowed_tools(FOSS)
    assert re.search(rf"Bash\({re.escape(program)}( [^)]*)? ?\*\)", line), (
        f"release-foss runs `{program}` but allowed-tools does not grant it: {line}"
    )


@pytest.mark.parametrize(
    "program", ["wc -m", "pip index versions", "npm view", "cargo search", "sort -u", "paste -sd"]
)
def test_release_foss_still_runs_it(program: str) -> None:
    """Control: the allowed-tools entries above are for commands the skill really runs."""
    assert program in FOSS.read_text(encoding="utf-8")


def test_agent_quick_ref_names_release_foss() -> None:
    body = QUICK_REF.read_text(encoding="utf-8")
    assert "/release-foss" in body, "AGENT-QUICK-REF names only the internal /release"


@pytest.mark.parametrize("path", DONE_SKILLS, ids=["software", "nautical"])
def test_done_skills_name_release_foss(path: Path) -> None:
    body = path.read_text(encoding="utf-8")
    assert "/release patch" in body
    assert "/release-foss" in body, f"{path.relative_to(REPO)} names only /release"


def test_local_credit_command_names_this_repo() -> None:
    body = LOCAL.read_text(encoding="utf-8")
    step4 = body[body.index("### 4.") : body.index("### 5.")]
    assert "gh search prs --repo Congruentsys/yurtle-kanban" in step4, (
        "the local credit step must inline the command for this repo"
    )
    assert "OWNER/REPO" not in body
    assert "authorAssociation" in step4 and 'git log "$TAG"..HEAD' in step4


def test_readme_tells_public_consumers_to_switch_on_reinit() -> None:
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    paragraphs = re.split(r"\n\s*\n", readme)
    hits = [
        p
        for p in paragraphs
        if "/release-foss" in p and "init" in p and "public" in p.lower() and "internal" in p
    ]
    assert hits, "README has no upgrade note: re-running init on a public repo → /release-foss"
