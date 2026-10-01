# ruff: noqa: F811  (the borrowed `yk` fixture)
"""Issue #1195: the external-PR process (Captain's ruling, 2026-10-01).

An EXTERNAL PR is one whose author's association is not OWNER/MEMBER/COLLABORATOR.
The fleet reviews, merges and releases it, within limits:

- ESCALATED: label `captain-approval`, OR the latest member verdict carries a line
  `class: captain…`, OR the PR touches `.github/**`, `skills/release/**`,
  `pyproject.toml`, `src/yurtle_kanban/__init__.py`, `scripts/check_release_version.py`
  or `.claude/skills/**`. An escalated PR waits for the Captain's `captain-approved`.
- PROPOSED-REJECT: label `proposed-reject`; the picker skips it, the gate refuses it.

Picker (`yk_next.py`), after RESUME PR: REVIEW EXTERNAL PR (no verdict at head),
WAIT CAPTAIN (listed, not picked), MERGE EXTERNAL PR (approve + green + routine or
Captain-approved), a `changes` verdict skipped; then the fleet's REVIEW PR; then
RELEASE DUE (an external PR merged since the latest `v*` tag, no open release PR),
with the bump from the unreleased `changelog.d/` fragments; a breaking/Removed
fragment prints RELEASE NEEDS CAPTAIN and picks nothing.

Gate (`safe_merge.sh`): refuses an escalated external PR without `captain-approved`,
and a `proposed-reject` PR; fleet PRs are unchanged.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

from tests import test_pairit_safe_merge as base
from tests.test_yk_next_picker import ME, PEER, issue, pr, yk  # noqa: F401 (fixture)
from tests.test_yk_next_picker import verdict as picker_verdict

EXT = "PandaHUN777"
HEAD = "c" * 40
TAG_MERGE = "d" * 40  # the merge commit of an external PR, after the tag
OLD_MERGE = "e" * 40  # a merge commit before the tag


# --------------------------------------------------------------------------- picker harness


def ext_pr(number: int, *, files: tuple[str, ...] = ("src/yurtle_kanban/board.py",),
           labels: tuple[str, ...] = (), comments: tuple[str, ...] = (),
           checks: list | None = None, author: str = EXT) -> dict:
    p = pr(number, author=author, labels=labels, head=HEAD, checks=checks,
           comments=list(comments))
    p["title"] = f"fix: an outside contribution {number}"
    p["isCrossRepository"] = True
    p["files"] = [{"path": f, "additions": 1, "deletions": 0} for f in files]
    p["changedFiles"] = len(files)
    return p


def merged(number: int, oid: str, *, author: str = EXT) -> dict:
    return {
        "number": number, "title": f"fix: merged outside contribution {number}",
        "author": {"login": author}, "isCrossRepository": True,
        "mergeCommit": {"oid": oid}, "mergedAt": "2026-10-01T00:09:03Z",
    }


def approve_at_head(extra: str = "") -> str:
    return f"reviewed-at-sha: {HEAD}\nverdict: approve\n{extra}\nnotes"


def run_picker(
    yk, monkeypatch, capsys, prs: list[dict], issues: list[dict], *,
    assoc: dict[int, str] | None = None,
    merged_prs: list[dict] = (),
    tags: str = "v3.0.0\nv2.2.0\n",
    merges_since_tag: tuple[str, ...] = (),
    fragments: dict[str, str] | None = None,
) -> str:
    """Run the picker in --dry-run with gh AND git stubbed; nothing touches a network."""
    assoc = assoc or {}
    fragments = fragments or {}

    def fake_gh(*args: str) -> str:
        if args[:2] == ("api", "user"):
            return ME + "\n"
        if args and args[0] == "api" and any("actions/runs" in a for a in args):
            assert any("status=action_required" in a and HEAD in a for a in args), args
            return "777\n"  # the fork run waiting for approval
        if args and args[0] == "api":
            m = next((re.search(r"pulls/(\d+)$", a) for a in args if re.search(r"pulls/(\d+)$", a)),
                     None)
            assert m, f"unexpected gh api call: {args}"
            n = int(m.group(1))
            known = {p["number"]: p for p in [*prs, *merged_prs]}
            default = "CONTRIBUTOR" if known.get(n, {}).get("author", {}).get("login") == EXT \
                else "MEMBER"
            return assoc.get(n, default) + "\n"
        raise AssertionError(f"unexpected gh call in dry-run: {args}")

    def fake_gh_json(*args: str):
        if args[:2] == ("pr", "list"):
            state = args[args.index("--state") + 1] if "--state" in args else "open"
            if state == "merged":
                return [dict(p) for p in merged_prs]
            return [dict(p) for p in prs]
        if args[:2] == ("issue", "list"):
            return [dict(i) for i in issues]
        raise AssertionError(f"unexpected gh_json call: {args}")

    def fake_git(*args: str) -> str:
        if args[0] == "fetch":
            return ""
        if args[0] == "tag":
            return tags
        if args[0] == "log":
            return "".join(f"{s}\n" for s in merges_since_tag)
        if args[0] == "ls-tree":
            return "changelog.d/README.md\n" + "".join(f"changelog.d/{n}\n" for n in fragments)
        if args[0] == "show":
            name = args[-1].rsplit("/", 1)[-1]
            return fragments[name]
        raise AssertionError(f"unexpected git call: {args}")

    monkeypatch.setattr(yk, "gh", fake_gh)
    monkeypatch.setattr(yk, "gh_json", fake_gh_json)
    monkeypatch.setattr(yk, "git", fake_git, raising=False)  # absent before #1195
    monkeypatch.setattr(sys, "argv", ["x", "--dry-run"])
    yk.main()
    return capsys.readouterr().out


SPARE = [issue(50)]  # something to claim once every PR step passes


# --------------------------------------------------------------------------- a. review


def test_a_unreviewed_external_pr_is_reviewed(yk, monkeypatch, capsys) -> None:
    out = run_picker(yk, monkeypatch, capsys, [ext_pr(1300)], SPARE)
    assert "REVIEW EXTERNAL PR #1300" in out, out
    assert ".claude/skills/external-pr/SKILL.md" in out, out
    assert "WOULD CLAIM" not in out, out


def test_a_external_comes_before_a_fleet_review(yk, monkeypatch, capsys) -> None:
    prs = [pr(1299, author=PEER), ext_pr(1300)]
    out = run_picker(yk, monkeypatch, capsys, prs, SPARE)
    assert "REVIEW EXTERNAL PR #1300" in out, out
    assert "REVIEW PR #1299" not in out, out


def test_a_own_pr_still_comes_first(yk, monkeypatch, capsys) -> None:
    prs = [pr(1298, author=ME), ext_pr(1300)]
    out = run_picker(yk, monkeypatch, capsys, prs, SPARE)
    assert "RESUME PR #1298" in out, out
    assert "REVIEW EXTERNAL PR" not in out, out


def test_a_reports_escalation_by_path(yk, monkeypatch, capsys) -> None:
    p = ext_pr(1300, files=(".github/workflows/publish.yml", "tests/test_x.py"))
    out = run_picker(yk, monkeypatch, capsys, [p], SPARE)
    assert "REVIEW EXTERNAL PR #1300" in out, out
    assert "escalated" in out.lower(), out
    assert ".github/workflows/publish.yml" in out, out


def test_a_routine_pr_is_reported_not_escalated(yk, monkeypatch, capsys) -> None:
    out = run_picker(yk, monkeypatch, capsys, [ext_pr(1300)], SPARE)
    assert "not escalated" in out.lower(), out


def test_a_member_from_a_fork_is_a_fleet_pr(yk, monkeypatch, capsys) -> None:
    """Association decides, not the fork: a MEMBER's fork PR is the fleet's REVIEW PR."""
    p = ext_pr(1300, author=PEER)
    out = run_picker(yk, monkeypatch, capsys, [p], SPARE, assoc={1300: "MEMBER"})
    assert "REVIEW PR #1300" in out, out
    assert "EXTERNAL" not in out, out


def test_proposed_reject_is_skipped(yk, monkeypatch, capsys) -> None:
    p = ext_pr(1300, labels=("proposed-reject",))
    out = run_picker(yk, monkeypatch, capsys, [p], SPARE)
    assert "REVIEW EXTERNAL PR #1300" not in out, out
    assert "MERGE EXTERNAL PR #1300" not in out, out
    assert "REVIEW PR #1300" not in out, out
    assert "WOULD CLAIM ISSUE #50" in out, out


# --------------------------------------------------------------------------- b./c. wait or merge


def test_c_approved_routine_green_is_merged(yk, monkeypatch, capsys) -> None:
    p = ext_pr(1300, comments=(approve_at_head("class: routine"),))
    out = run_picker(yk, monkeypatch, capsys, [p], SPARE)
    assert "MERGE EXTERNAL PR #1300" in out, out


def test_c_approved_but_ci_pending_is_not_merged(yk, monkeypatch, capsys) -> None:
    p = ext_pr(1300, comments=(approve_at_head("class: routine"),),
               checks=[{"status": "IN_PROGRESS", "conclusion": ""}])
    out = run_picker(yk, monkeypatch, capsys, [p], SPARE)
    assert "MERGE EXTERNAL PR #1300" not in out, out


@pytest.mark.parametrize("why", ["path", "label", "class"])
def test_b_escalated_waits_for_the_captain(yk, monkeypatch, capsys, why) -> None:
    files = ("pyproject.toml",) if why == "path" else ("src/yurtle_kanban/board.py",)
    labels = ("captain-approval",) if why == "label" else ()
    extra = "class: captain (changes the --json shape)" if why == "class" else "class: routine"
    p = ext_pr(1300, files=files, labels=labels, comments=(approve_at_head(extra),))
    out = run_picker(yk, monkeypatch, capsys, [p], SPARE)
    assert "WAIT CAPTAIN #1300" in out, out
    assert "MERGE EXTERNAL PR #1300" not in out, out
    assert "WOULD CLAIM ISSUE #50" in out, out  # not a pick: the picker carries on


@pytest.mark.parametrize("path", [
    ".github/workflows/ci.yml", "skills/release/SKILL.md", "pyproject.toml",
    "src/yurtle_kanban/__init__.py", "scripts/check_release_version.py",
    ".claude/skills/pairit/safe_merge.sh",
])
def test_b_every_escalation_path(yk, monkeypatch, capsys, path) -> None:
    p = ext_pr(1300, files=("tests/test_x.py", path), comments=(approve_at_head(),))
    out = run_picker(yk, monkeypatch, capsys, [p], SPARE)
    assert "WAIT CAPTAIN #1300" in out, out


@pytest.mark.parametrize("path", ["skills/other/SKILL.md", "src/yurtle_kanban/cli.py",
                                  "docs/github/x.md", "pyproject.toml.bak"])
def test_c_near_miss_paths_are_routine(yk, monkeypatch, capsys, path) -> None:
    p = ext_pr(1300, files=(path,), comments=(approve_at_head(),))
    out = run_picker(yk, monkeypatch, capsys, [p], SPARE)
    assert "MERGE EXTERNAL PR #1300" in out, out


def test_c_captain_approved_unblocks(yk, monkeypatch, capsys) -> None:
    p = ext_pr(1300, files=(".github/workflows/publish.yml",),
               labels=("captain-approval", "captain-approved"),
               comments=(approve_at_head("class: captain (release path)"),))
    out = run_picker(yk, monkeypatch, capsys, [p], SPARE)
    assert "MERGE EXTERNAL PR #1300" in out, out
    assert "WAIT CAPTAIN #1300" not in out, out


# --------------------------------------------------------------------------- d. changes


def test_d_changes_waits_on_the_author(yk, monkeypatch, capsys) -> None:
    p = ext_pr(1300, comments=(picker_verdict(HEAD, "changes"),))
    out = run_picker(yk, monkeypatch, capsys, [p], SPARE)
    assert "REVIEW EXTERNAL PR #1300" not in out, out
    assert "MERGE EXTERNAL PR #1300" not in out, out
    assert "REVIEW PR #1300" not in out, out
    assert "WOULD CLAIM ISSUE #50" in out, out


def test_d_a_new_head_after_changes_is_reviewed_again(yk, monkeypatch, capsys) -> None:
    p = ext_pr(1300, comments=(picker_verdict("b" * 40, "changes"),))
    out = run_picker(yk, monkeypatch, capsys, [p], SPARE)
    assert "REVIEW EXTERNAL PR #1300" in out, out


# --------------------------------------------------------------------------- RELEASE DUE


def _release(yk, monkeypatch, capsys, fragments: dict[str, str], **kw) -> str:
    kw.setdefault("merged_prs", [merged(1193, TAG_MERGE)])
    kw.setdefault("merges_since_tag", (TAG_MERGE,))
    return run_picker(yk, monkeypatch, capsys, kw.pop("prs", []), SPARE,
                      fragments=fragments, **kw)


def test_release_due_patch(yk, monkeypatch, capsys) -> None:
    out = _release(yk, monkeypatch, capsys,
                   {"1192.md": "<!-- section: Fixed -->\n- a fix (#1192)\n",
                    "1180.md": "<!-- section: Security -->\n- a hardening (#1180)\n"})
    assert "RELEASE DUE" in out, out
    assert "#1193" in out, out
    assert re.search(r"\bpatch\b", out), out
    assert "WOULD CLAIM" not in out, out


def test_release_due_minor(yk, monkeypatch, capsys) -> None:
    out = _release(yk, monkeypatch, capsys,
                   {"1192.md": "<!-- section: Fixed -->\n- a fix (#1192)\n",
                    "1194.md": "<!-- section: Added -->\n- a feature (#1194)\n"})
    assert "RELEASE DUE" in out, out
    assert re.search(r"\bminor\b", out), out


def test_release_due_minor_on_changed(yk, monkeypatch, capsys) -> None:
    out = _release(yk, monkeypatch, capsys,
                   {"1194.md": "<!-- section: Changed -->\n- a change (#1194)\n"})
    assert re.search(r"\bminor\b", out), out


@pytest.mark.parametrize("frag", [
    "<!-- section: Removed -->\n- dropped `--old` (#1194)\n",
    "<!-- section: Changed -->\n- **Breaking:** `list --json` renames `id` (#1194)\n",
])
def test_release_needs_captain_for_a_major(yk, monkeypatch, capsys, frag) -> None:
    out = _release(yk, monkeypatch, capsys,
                   {"1192.md": "<!-- section: Fixed -->\n- a fix (#1192)\n", "1194.md": frag})
    assert "RELEASE NEEDS CAPTAIN" in out, out
    assert "RELEASE DUE" not in out, out
    assert "WOULD CLAIM ISSUE #50" in out, out  # not a pick


def test_no_release_while_a_release_pr_is_open(yk, monkeypatch, capsys) -> None:
    rel = pr(1196, author=PEER, comments=[picker_verdict("a" * 40, "approve")])
    rel["title"] = "chore: release v3.0.1"
    out = _release(yk, monkeypatch, capsys,
                   {"1192.md": "<!-- section: Fixed -->\n- a fix (#1192)\n"}, prs=[rel])
    assert "RELEASE DUE" not in out, out
    assert "WOULD CLAIM ISSUE #50" in out, out


def test_no_release_without_an_external_merge_since_the_tag(yk, monkeypatch, capsys) -> None:
    out = _release(yk, monkeypatch, capsys,
                   {"1192.md": "<!-- section: Fixed -->\n- a fix (#1192)\n"},
                   merged_prs=[merged(1193, OLD_MERGE)], merges_since_tag=(TAG_MERGE,))
    assert "RELEASE DUE" not in out, out
    assert "WOULD CLAIM ISSUE #50" in out, out


def test_no_release_for_a_fleet_merge(yk, monkeypatch, capsys) -> None:
    out = _release(yk, monkeypatch, capsys,
                   {"1192.md": "<!-- section: Fixed -->\n- a fix (#1192)\n"},
                   merged_prs=[merged(1191, TAG_MERGE, author=PEER)],
                   assoc={1191: "MEMBER"})
    assert "RELEASE DUE" not in out, out


def test_release_comes_before_resuming_an_issue(yk, monkeypatch, capsys) -> None:
    out = run_picker(yk, monkeypatch, capsys, [], [issue(51, assignees=(ME,))],
                     merged_prs=[merged(1193, TAG_MERGE)], merges_since_tag=(TAG_MERGE,),
                     fragments={"1192.md": "<!-- section: Fixed -->\n- a fix (#1192)\n"})
    assert "RELEASE DUE" in out, out
    assert "RESUME ISSUE #51" not in out, out


# --------------------------------------------------------------------------- the skills


SKILLS = Path(__file__).resolve().parents[2] / ".claude" / "skills"


def test_external_pr_skill_exists_and_names_the_limits() -> None:
    text = (SKILLS / "external-pr" / "SKILL.md").read_text()
    for needle in ("captain-approved", "captain-approval", "proposed-reject", "class: captain",
                   "class: routine", "safe_merge", "actions/runs", ".github/"):
        assert needle in text, needle


def test_release_skill_is_model_invocable() -> None:
    text = (SKILLS.parent.parent / "skills" / "release" / "SKILL.md").read_text()
    assert "disable-model-invocation: true" not in text
    assert "#1195" in text


# --------------------------------------------------------------------------- safe_merge


WRAPPER = r'''#!__PYTHON__
"""#1195 wrapper: serves the external-PR fields of `pr view` and the author association;
everything else goes to the #167 stub (gh-base)."""
import json, os, sys

args = sys.argv[1:]


def record(extra=None):
    entry = {"argv": args}
    entry.update(extra or {})
    with open(os.environ["STUB_GH_LOG"], "a") as fh:
        fh.write(json.dumps(entry) + "\n")


def opt(name):
    for i, a in enumerate(args):
        if a == name and i + 1 < len(args):
            return args[i + 1]
    return None


if args[:2] == ["pr", "view"]:
    record()
    files = json.loads(os.environ.get("STUB_EXT_FILES", "[]"))
    data = {
        "headRefName": os.environ.get("STUB_GH_BRANCH", ""),
        "headRefOid": os.environ.get("STUB_GH_HEAD_SHA", ""),
        "comments": [
            {"author": {"login": "reviewer"},
             "authorAssociation": c.get("association", "MEMBER"), "body": c["body"]}
            for c in json.loads(os.environ.get("STUB_GH_COMMENTS", "[]"))
        ],
        "labels": [{"name": n} for n in json.loads(os.environ.get("STUB_EXT_LABELS", "[]"))],
        "files": [{"path": p, "additions": 1, "deletions": 0} for p in files],
        "changedFiles": len(files),
        "isCrossRepository": os.environ.get("STUB_EXT_CROSS") == "1",
    }
    want = (opt("--json") or "").split(",")
    print(json.dumps({k: data.get(k) for k in want if k}))
    sys.exit(0)

if args and args[0] == "api" and any(a.rstrip("/").split("/")[-2:-1] == ["pulls"] for a in args):
    record()
    print(os.environ.get("STUB_EXT_ASSOC", "MEMBER"))
    sys.exit(0)

here = os.path.dirname(os.path.abspath(__file__))
os.execv(os.path.join(here, "gh-base"), [os.path.join(here, "gh-base"), *args])
'''


class ExtSandbox(base.Sandbox):
    """The #167 sandbox, with a gh that knows labels, files, forks and associations."""

    def __init__(self, tmp_path: Path, *, fork: bool = True) -> None:
        super().__init__(tmp_path, conflict=False)
        stub = self.stub_dir / "gh"
        stub.rename(self.stub_dir / "gh-base")
        stub.write_text(WRAPPER.replace("__PYTHON__", sys.executable))
        stub.chmod(0o755)
        self.fork = fork
        if fork:
            # GitHub keeps a fork PR's head at refs/pull/<N>/head on the base repo
            base._git(self.worktree, "push", "-q", "origin", f"HEAD:refs/pull/{base.PR}/head")

    def run_ext(self, *, labels: tuple[str, ...] = (), files: tuple[str, ...] = ("feature.txt",),
                assoc: str = "CONTRIBUTOR", comments: list[dict] | None = None):
        return self.run(
            base.GREEN, comments,
            # a fork's branch is not on origin
            branch="panda/feature" if self.fork else base.BRANCH,
            extra_env={
                "STUB_EXT_LABELS": json.dumps(list(labels)),
                "STUB_EXT_FILES": json.dumps(list(files)),
                "STUB_EXT_CROSS": "1" if self.fork else "0",
                "STUB_EXT_ASSOC": assoc,
            },
        )


needs_tools = base.pytestmark


def _out(r) -> str:
    return r.stdout + r.stderr


@needs_tools
def test_gate_merges_a_routine_external_fork_pr(tmp_path: Path) -> None:
    sb = ExtSandbox(tmp_path)
    r = sb.run_ext()
    assert r.returncode == 0, _out(r)
    merges = sb.merge_calls()
    assert len(merges) == 1, sb.calls()
    # a fork's branch name is the fork's: never delete a same-named branch here
    assert "--delete-branch" not in merges[0]["argv"], merges


@needs_tools
@pytest.mark.parametrize("why", ["path", "label", "class"])
def test_gate_refuses_escalated_external_without_approval(tmp_path: Path, why: str) -> None:
    sb = ExtSandbox(tmp_path)
    files = (".github/workflows/publish.yml",) if why == "path" else ("feature.txt",)
    labels = ("captain-approval",) if why == "label" else ()
    comments = [base.verdict(sb.head_sha, "approve")]
    if why == "class":
        comments = [{"body": f"reviewed-at-sha: {sb.head_sha}\nverdict: approve\n"
                             "class: captain (breaking --json shape)\n\nok",
                     "association": "MEMBER"}]
    r = sb.run_ext(labels=labels, files=files, comments=comments)
    assert r.returncode != 0, _out(r)
    assert "captain-approved" in _out(r), _out(r)
    assert sb.merge_calls() == [], sb.calls()


@needs_tools
def test_gate_merges_escalated_external_with_captain_approved(tmp_path: Path) -> None:
    sb = ExtSandbox(tmp_path)
    r = sb.run_ext(labels=("captain-approval", "captain-approved"),
                   files=(".github/workflows/publish.yml",))
    assert r.returncode == 0, _out(r)
    assert len(sb.merge_calls()) == 1, sb.calls()


@needs_tools
def test_gate_refuses_proposed_reject(tmp_path: Path) -> None:
    sb = ExtSandbox(tmp_path)
    r = sb.run_ext(labels=("proposed-reject",))
    assert r.returncode != 0, _out(r)
    assert "proposed-reject" in _out(r), _out(r)
    assert sb.merge_calls() == [], sb.calls()


@needs_tools
def test_gate_fleet_pr_touching_ci_is_unaffected(tmp_path: Path) -> None:
    """A fleet PR (same repo) is never escalated by path, and no association call is made."""
    sb = ExtSandbox(tmp_path, fork=False)
    r = sb.run_ext(files=(".github/workflows/ci.yml", "pyproject.toml"), assoc="MEMBER")
    assert r.returncode == 0, _out(r)
    merges = sb.merge_calls()
    assert len(merges) == 1, sb.calls()
    assert "--delete-branch" in merges[0]["argv"], merges
    assert not [c for c in sb.calls() if c["argv"][:1] == ["api"]], sb.calls()


@needs_tools
def test_gate_member_fork_pr_is_not_escalated(tmp_path: Path) -> None:
    sb = ExtSandbox(tmp_path)
    r = sb.run_ext(files=(".github/workflows/ci.yml",), assoc="COLLABORATOR")
    assert r.returncode == 0, _out(r)
    assert len(sb.merge_calls()) == 1, sb.calls()



# --------------------------------------------------------------------------- actionable picks
# Captain's additions to #1195: each pick prints the exact next command(s), and the
# submitter is thanked on merge, after publish, and credited in the changelog.

WAITING_RUN = [{"status": "COMPLETED", "conclusion": "ACTION_REQUIRED"}]


def test_merge_pick_prints_the_safe_merge_command(yk, monkeypatch, capsys) -> None:
    p = ext_pr(1300, comments=(approve_at_head("class: routine"),))
    out = run_picker(yk, monkeypatch, capsys, [p], SPARE)
    assert "bash .claude/skills/pairit/safe_merge.sh 1300" in out, out
    assert "gh pr comment 1300" in out, out
    assert f"Thanks @{EXT} — merged; this ships in v3.0.1." in out, out


def test_review_pick_prints_the_diff_and_checkout_commands(yk, monkeypatch, capsys) -> None:
    out = run_picker(yk, monkeypatch, capsys, [ext_pr(1300)], SPARE)
    assert "gh pr diff 1300" in out, out
    assert "pull/1300/head" in out and HEAD in out, out


def test_review_pick_prints_the_run_approval_after_the_diff(yk, monkeypatch, capsys) -> None:
    out = run_picker(yk, monkeypatch, capsys, [ext_pr(1300, checks=WAITING_RUN)], SPARE)
    assert "REVIEW EXTERNAL PR #1300" in out, out
    assert "gh api -X POST repos/{owner}/{repo}/actions/runs/777/approve" in out, out
    assert "only after reading the whole diff" in out, out


def test_never_prints_a_run_approval_for_github_changes(yk, monkeypatch, capsys) -> None:
    p = ext_pr(1300, files=(".github/workflows/ci.yml",), checks=WAITING_RUN)
    out = run_picker(yk, monkeypatch, capsys, [p], SPARE)
    assert "REVIEW EXTERNAL PR #1300" in out, out
    assert "/approve" not in out, out


def test_approved_pr_with_a_waiting_run_is_picked_to_run_ci(yk, monkeypatch, capsys) -> None:
    p = ext_pr(1300, comments=(approve_at_head("class: routine"),), checks=WAITING_RUN)
    out = run_picker(yk, monkeypatch, capsys, [p], SPARE)
    assert "RUN CI EXTERNAL PR #1300" in out, out
    assert "actions/runs/777/approve" in out, out
    assert "WOULD CLAIM" not in out, out


def test_release_due_prints_version_commands_and_thanks(yk, monkeypatch, capsys) -> None:
    out = _release(yk, monkeypatch, capsys,
                   {"1192.md": "<!-- section: Fixed -->\n- a fix (#1192)\n"})
    assert "v3.0.1" in out, out
    assert "scripts/assemble_changelog.py 3.0.1" in out, out
    assert "chore: release v3.0.1" in out, out
    assert "gh pr comment 1193" in out, out
    assert "Released in v3.0.1 on PyPI — thanks again!" in out, out  # after publish


def test_release_due_minor_version(yk, monkeypatch, capsys) -> None:
    out = _release(yk, monkeypatch, capsys,
                   {"1194.md": "<!-- section: Added -->\n- a feature (#1194)\n"})
    assert "v3.1.0" in out, out


def test_release_due_flags_a_merged_pr_without_a_fragment(yk, monkeypatch, capsys) -> None:
    m = merged(1193, TAG_MERGE)
    m["files"] = [{"path": "src/yurtle_kanban/board.py"}]
    out = _release(yk, monkeypatch, capsys, {}, merged_prs=[m])
    assert "changelog.d/" in out and "#1193" in out, out
    assert "no changelog.d fragment" in out.lower(), out
    assert f"Thanks @{EXT} (#1193)" in out, out  # the credit to put in it


def test_skill_thanks_the_submitter() -> None:
    text = (SKILLS / "external-pr" / "SKILL.md").read_text()
    assert "Thanks @" in text
    assert "Captain makes the final call" in text
