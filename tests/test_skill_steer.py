"""Tests for the `/steer` skill ported into yurtle-kanban, and yk-loop's triage hand-off to it.

The skill is a document, so these tests read it as text (as test_yk_next_picker.py reads pairit).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
STEER = REPO / ".claude/skills/steer/SKILL.md"
YK_LOOP = REPO / ".claude/skills/yk-loop/SKILL.md"
FLEET_REPOS = ("carclaw", "noesis-ship", "noesis-ships-comm", "nusy-product-team", "rachael-lab")


@pytest.fixture
def steer() -> str:
    assert STEER.is_file(), f"{STEER} missing"
    return STEER.read_text()


def frontmatter(text: str) -> str:
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    assert m, "SKILL.md has no --- frontmatter block"
    return m.group(1)


def test_frontmatter_name_and_description(steer):
    fm = frontmatter(steer)
    assert re.search(r"^name:\s*steer\s*$", fm, re.M)
    m = re.search(r"^description:\s*(.+)$", fm, re.M)
    assert m and m.group(1).strip(), "frontmatter needs a non-empty description"


def test_three_buckets_in_order(steer):
    low = steer.lower()
    pos = [re.search(rf"bucket[ -]{n}", low) for n in (1, 2, 3)]
    assert all(pos), "all of bucket 1, bucket 2, bucket 3 must be named"
    starts = [p.start() for p in pos]
    assert starts == sorted(starts), "buckets must appear in order 1, 2, 3"


def test_bucket_contents(steer):
    low = steer.lower()
    assert "measure" in low  # bucket 1: measurement settles it
    assert "decide now" in low  # bucket 2
    assert "existing" in low and "ruling" in low  # an EXISTING user ruling settles it
    assert "escalate" in low  # bucket 3
    for word in ("feature", "goal", "authority"):
        assert word in low, f"bucket 3 criteria must mention {word!r}"


def test_bucket_one_measures_fleet_repos_read_only(steer):
    for repo in FLEET_REPOS:
        assert repo in steer, f"fleet repo {repo!r} not listed for measurement"
    assert re.search(r"read[- ]only", steer, re.I), "other fleet repos must be read-only"


def test_goals_section(steer):
    assert "Goals" in steer
    for g in ("G1", "G2"):
        assert g in steer, f"{g} missing from goals"
    low = steer.lower()
    assert "correctness" in low or "data safety" in low
    assert "compatib" in low  # G2 fleet-consumer compatibility


def test_name_the_trigger(steer):
    assert "trigger" in steer.lower()


def test_recording_convention(steer):
    assert "[steer] bucket-" in steer, "decisions are recorded with a `[steer] bucket-` comment"
    assert "needs-decision" in steer
    assert re.search(r"--remove-label\s+needs-decision", steer), \
        "a decided item must have its needs-decision label removed"


def test_uses_github_not_nk(steer):
    assert "gh " in steer, "the decision queue is GitHub issues/PRs via gh"


def test_one_batched_escalation_with_default(steer):
    low = steer.lower()
    assert "batch" in low, "bucket-3 items go out as ONE batched escalation"
    assert "recommend" in low and "default" in low, "each escalated item carries a default"


def test_report_open_to_veto(steer):
    assert "veto" in steer.lower()


@pytest.mark.parametrize("forbidden", ["NATS", "captain-decision", "campaign-watcher",
                                       "hypothesize"])
def test_no_fleet_board_references(steer, forbidden):
    assert forbidden.lower() not in steer.lower()


def test_no_nk_commands(steer):
    assert not re.search(r"(?<![\w-])nk\s", steer), "steer must not use `nk` commands"


def yk_triage() -> str:
    text = YK_LOOP.read_text()
    m = re.search(r"\*\*3\. Triage\.\*\*(.*?)(?=\n\*\*[^*\n]+\*\*)", text, re.S)
    assert m, "yk-loop triage step not found"
    return m.group(1)


def test_yk_loop_triage_uses_steer():
    assert "steer" in yk_triage().lower(), "yk-loop triage must hand decisions to /steer"


def test_yk_loop_holds_only_bucket_three():
    tri = yk_triage().lower()
    assert "needs-decision" in tri
    assert re.search(r"bucket[ -]3", tri), "only a bucket-3 item is held with needs-decision"


def yk_overview_triage() -> str:
    text = YK_LOOP.read_text()
    m = re.search(r"^\s*3\. TRIAGE(.*?)^\s*4\. LAND", text, re.S | re.M)
    assert m, "yk-loop repeat: block step 3 (TRIAGE) not found"
    return m.group(1)


def test_yk_loop_overview_triage_steers_and_builds():
    step = yk_overview_triage().lower()
    assert "steer" in step, "overview step 3 must name the /steer classifier"
    assert "build" in step, "overview step 3 must say a bucket-1/2 decision is built"
    assert "never built" not in step, "overview step 3 still says a decision is held, never built"


def test_yk_loop_hold_bullets_are_candidates():
    assert "candidate" in yk_triage().lower(), "the hold bullets are candidates, not holds"


def test_yk_loop_reliance_is_measured_by_fleet_scan():
    tri = " ".join(yk_triage().lower().split())
    sentences = [s for s in re.split(r"(?<=[.;])\s", tri) if "rely on" in s]
    assert sentences, "triage must still discuss behaviour someone may rely on"
    assert any("fleet" in s and ("measur" in s or "scan" in s) for s in sentences), \
        "'rely on' must be measured first with a read-only fleet scan"
