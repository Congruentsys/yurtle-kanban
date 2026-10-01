#!/usr/bin/env python3
"""yk_next.py: what should THIS session do next in yurtle-kanban? One pass, then stop.

This repo's work lives on GitHub (issues and PRs), not on a kanban board. In order:

1. RESUME PR     my own open PR that needs something: changes requested, CI red, a merge
                 conflict, no review at its head sha, or approved + green (so: merge it).
                 CI still running is WAIT. A PR that is a draft, or carries a hold label (on
                 itself or on the issue it fixes), is SKIPPED — that is how pairit parks a PR
                 whose finding needs a decision without wedging the loop. A head with the
                 driver's fixes comment after ONE review round counts as reviewed (#987).
2. EXTERNAL PR   an open PR by a non-member (#1195; .claude/skills/external-pr/SKILL.md):
                 MERGE EXTERNAL PR  approve at head + CI green + not escalated (or
                                    `captain-approved` by the Captain AFTER that verdict);
                 RUN CI EXTERNAL PR approve at head, its fork run waits for approval (and
                                    it doesn't touch .github/);
                 REVIEW EXTERNAL PR no member verdict at its head.
                 Each pick prints its next commands: the diff and checkout, the fork-run
                 approval, safe_merge.sh, the thank-you comment with the version it ships in.
                 Not picked, listed: WAIT CAPTAIN (escalated, no `captain-approved`), a
                 `changes` verdict (waiting on the author), `proposed-reject` (the Captain
                 closes it), a draft or held one, CI pending or red, a conflict.
3. REVIEW PR     another FLEET author's open PR with no verdict (or fixes comment) at its
                 head sha (reviewer != author). Review only: its author merges it.
4. RELEASE DUE   an external PR merged since the latest `v*` tag and no open PR titled
                 `chore: release v…`: patch if the unreleased changelog.d fragments are only
                 Fixed/Security, minor if any Added/Changed/Deprecated. A Removed or
                 breaking fragment prints RELEASE NEEDS CAPTAIN instead and picks nothing.
5. RESUME ISSUE  an open issue assigned to me that no open PR fixes yet, not held, not waiting.
   (`--skip-prs` jumps straight to 6, for claiming the next issue while a PR is in review.)
6. CLAIMED ISSUE the first open, unassigned issue that no open PR fixes, carries no hold label,
                 and whose "depends on #N" / "blocked by #N" issues are all closed.
                 `bug` first, then the lower number.
   A claim is `gh issue edit N --add-assignee @me`, then a re-read: another assignee means a
   peer got there first, so un-assign and take the next one.
7. NOTHING READY

An EXTERNAL PR's author association is not OWNER/MEMBER/COLLABORATOR. It is ESCALATED
(waits for the Captain) when labelled `captain-approval`, when the latest member verdict has
a line `class: captain…`, or when it touches the release/CI/security path (ESCALATE_*, by
its new or previous name, from REST `pulls/<P>/files`). safe_merge.sh's gate asks
`yk_next.py --escalation <P>`, so these definitions exist once.

A review verdict is a PR comment whose first two lines are `reviewed-at-sha: <sha>` and
`verdict: approve|changes` (pairit step 3). It only counts at the PR's CURRENT head.

Identity is the gh login, so run ONE loop per GitHub account: two sessions on one account would
both resume the same PR, and the claim race cannot tell them apart.

Usage: python3 .claude/skills/yk-next/yk_next.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import re
import socket
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime

HOSTS = {"m4-mini": "Mini", "mini": "Mini", "m5": "M5", "spark": "DGX"}
HOLD = {"needs-decision", "question", "wontfix", "duplicate", "invalid", "blocked", "on-hold"}
# read exactly as safe_merge.sh reads them (#991): the first two lines of the body, a
# trailing \r dropped, full lowercase 40-hex shas, nothing else on the line
VERDICT = re.compile(r"reviewed-at-sha: ([0-9a-f]{40})\nverdict: (approve|changes)")
# pairit's one review round (#987): the driver's comment after fixing a `changes` verdict's findings
FIXES = re.compile(r"fixes-at-sha: ([0-9a-f]{40})\nfor-review-at: ([0-9a-f]{40})")
MEMBERS = {"OWNER", "MEMBER", "COLLABORATOR"}
CI_FAILED = {"FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE"}
# "depends on #5", "Depends on: #5", "blocked by #8, #9", "depends on #6, #7, and #8",
# "Blocked-by: #17", "depends on **#12**" (markdown emphasis), "depends on #6 #7"
DEPENDS = re.compile(
    r"(?:depends[\s-]+on|blocked[\s-]+by|requires)[\s*_`:]*"
    # the list continues on the same line only: a `* #4` bullet below is not part of it
    r"(#\d+(?:[ \t*_`]*(?:(?:,|and|&|or)[ \t*_`]*)*#\d+)*)",
    re.I,
)
PR_FIELDS = (
    "number,title,author,labels,headRefName,headRefOid,isDraft,mergeable,"
    "statusCheckRollup,comments,closingIssuesReferences,isCrossRepository"
)

# --- the external-PR process (#1195, Captain 2026-10-01) ---
# The ONE definition: safe_merge.sh asks `yk_next.py --escalation <P>` rather than keep a copy.
# The release/CI/security path, and whatever runs on fleet machines or in the release: an
# external PR touching it (by its new OR its previous name) waits for the Captain.
ESCALATE_DIRS = (".github/", ".claude/", ".kanban/", "scripts/", "skills/release/")
ESCALATE_FILES = {"pyproject.toml", "src/yurtle_kanban/__init__.py", "CLAUDE.md",
                  "AGENT-QUICK-REF.md"}
CAPTAIN_APPROVAL = "captain-approval"  # escalated: the fleet sets it and waits
CAPTAIN_APPROVED = "captain-approved"  # the Captain's yes: ONLY the Captain sets it
# The Captain's GitHub login(s), the ONE definition (the gate asks --escalation). Confirmed by
# the Captain, 2026-10-01. hankh95 is ALSO the M5 agent's account, so the actor check can't
# tell them apart: a fleet session on M5 must never add `captain-approved` (a documented limit).
CAPTAINS = frozenset({"hankh95"})
PROPOSED_REJECT = "proposed-reject"    # the fleet proposes; only the Captain closes
CLASS_CAPTAIN = re.compile(r"(?m)^class: captain")  # the reviewer's third verdict line
RELEASE_TITLE = "chore: release v"
FRAGMENT_NAME = re.compile(r"(\d+)(?:-.*)?\.md")      # as scripts/assemble_changelog.py
SECTION_LINE = re.compile(r"<!-- section: (\w+) -->")
MINOR_SECTIONS = {"Added", "Changed", "Deprecated"}  # Fixed/Security alone: a patch
BREAKING = re.compile(r"(?<![-\w])breaking\b", re.I)  # "non-breaking" isn't


def gh(*args: str) -> str:
    r = subprocess.run(["gh", *args], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"ERROR: gh {' '.join(args)}: {(r.stderr or r.stdout).strip()[:300]}")
    return r.stdout


def gh_json(*args: str) -> list | dict:
    return json.loads(gh(*args))


def git(*args: str) -> str:
    r = subprocess.run(["git", *args], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"ERROR: git {' '.join(args)}: {(r.stderr or r.stdout).strip()[:300]}")
    return r.stdout


def association(number: int) -> str:
    """The PR author's association; `gh pr list --json` doesn't expose it."""
    return gh("api", f"repos/{{owner}}/{{repo}}/pulls/{number}",
              "--jq", ".author_association").strip()


def is_external(pr: dict) -> bool:
    """A non-member's PR. Only a fork can carry one (a non-member can't push a branch
    here), so the association is fetched for fork PRs only: one gh call each."""
    return bool(pr.get("isCrossRepository")) and association(pr["number"]) not in MEMBERS


def pr_paths(number: int) -> list[str]:
    """Every path the PR touches, from REST `pulls/<P>/files` (paginated, so no 100-file
    cap): each file's `filename` and, for a rename, its `previous_filename` too."""
    out = gh("api", "--paginate", f"repos/{{owner}}/{{repo}}/pulls/{number}/files",
             "--jq", ".[] | .filename, (.previous_filename // empty)")
    return [line for line in out.splitlines() if line]


def latest_verdict(pr: dict) -> str:
    """The body of the latest member comment starting `reviewed-at-sha:`, or ''."""
    bodies = [c.get("body") or "" for c in pr.get("comments") or []
              if c.get("authorAssociation", "MEMBER") in MEMBERS]
    return next((b for b in reversed(bodies) if b.startswith("reviewed-at-sha:")), "")


def escalation(pr: dict, paths: list[str]) -> list[str]:
    """Why an external PR waits for the Captain; empty when it is routine. `paths` is
    pr_paths(): size alone never escalates (the Captain's choice)."""
    labels = {lbl["name"] for lbl in pr.get("labels") or []}
    why = []
    if CAPTAIN_APPROVAL in labels:
        why.append(f"label {CAPTAIN_APPROVAL}")
    if CLASS_CAPTAIN.search(latest_verdict(pr).replace("\r", "")):
        why.append("verdict class: captain")
    hits = list(dict.fromkeys(f for f in paths
                              if f in ESCALATE_FILES or f.startswith(ESCALATE_DIRS)))
    if hits:
        why.append("touches " + ", ".join(hits[:5]) + (" …" if len(hits) > 5 else ""))
    return why


def gh_time(ts: str) -> datetime:
    """A GitHub timestamp (`2026-10-01T00:09:03Z`), comparable; Python 3.9 reads no `Z`."""
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def approved_at(pr: dict) -> str | None:
    """When the latest member `approve` verdict at the PR's CURRENT head was posted."""
    times = [c.get("createdAt") or "" for c in pr.get("comments") or []
             if c.get("authorAssociation", "MEMBER") in MEMBERS
             and (m := VERDICT.fullmatch("\n".join(
                 line.removesuffix("\r") for line in (c.get("body") or "").split("\n")[:2])))
             and m.group(1) == pr["headRefOid"] and m.group(2) == "approve"]
    return times[-1] if times else None


def captain_approval(pr: dict) -> tuple[bool, str]:
    """(approved?, why). `captain-approved` counts only when its LATEST `labeled` event (REST
    issue events: a server timestamp and actor nobody can forge) is newer than the approve
    verdict at the current head, and its actor is in CAPTAINS. A new head needs a new
    verdict, which postdates the label, so the PR waits for the Captain again."""
    if CAPTAIN_APPROVED not in {lbl["name"] for lbl in pr.get("labels") or []}:
        return False, f"no {CAPTAIN_APPROVED}"
    verdict = approved_at(pr)
    if not verdict:
        return False, f"{CAPTAIN_APPROVED}, but no member approve verdict at the head"
    events = gh("api", "--paginate", f"repos/{{owner}}/{{repo}}/issues/{pr['number']}/events",
                "--jq", f'.[] | select(.event == "labeled" and .label.name == "{CAPTAIN_APPROVED}")'
                ' | .created_at + " " + .actor.login').split("\n")
    last = [e.split() for e in events if e.strip()]
    if not last:
        return False, f"{CAPTAIN_APPROVED}, but no labeled event for it"
    when, actor = last[-1][0], last[-1][-1]
    if actor not in CAPTAINS:
        return False, f"{CAPTAIN_APPROVED} was added by {actor}, not the Captain"
    if gh_time(when) <= gh_time(verdict):
        return False, (f"{CAPTAIN_APPROVED} ({when}) predates the approve verdict at the "
                       f"current head ({verdict}): the Captain hasn't seen this head")
    return True, f"{CAPTAIN_APPROVED} by {actor}"


def escalation_report(number: int, pr: dict | None = None) -> dict:
    """The gate's question (`--escalation <P>`), answered from the ONE definition above:
    {"number", "association", "external", "why", "captain_approved", "captain"}. `pr` is
    the gate's own `gh pr view` JSON (labels and comments, read once at the head it
    pinned); else it is read here."""
    if pr is None:
        pr = gh_json("pr", "view", str(number), "--json", "headRefOid,labels,comments")
    pr = {**pr, "number": number}
    assoc = association(number)
    external = assoc not in MEMBERS
    why = escalation(pr, pr_paths(number)) if external else []
    ok, captain = captain_approval(pr) if why else (False, "")
    return {"number": number, "association": assoc, "external": external, "why": why,
            "captain_approved": ok, "captain": captain}


def fragments(ref: str, names: list[str] | None = None) -> dict[str, str]:
    """{name: text} of the changelog.d/ fragments at `ref` (all of them, or `names`)."""
    if names is None:
        names = [n.rsplit("/", 1)[-1] for n in git(
            "ls-tree", "--name-only", ref, "changelog.d/").split()]
    return {n: git("show", f"{ref}:changelog.d/{n}")
            for n in sorted(names) if FRAGMENT_NAME.fullmatch(n)}


def release_bump(texts: dict[str, str]) -> tuple[str, str]:
    """(patch | minor | major, why) from unreleased changelog.d/ fragments {name: text}."""
    sections: dict[str, list[str]] = {}
    major = []
    for name, text in texts.items():
        first, _, rest = text.partition("\n")
        m = SECTION_LINE.fullmatch(first.strip())
        sec = m.group(1) if m else "?"
        sections.setdefault(sec, []).append(name)
        if sec == "Removed" or BREAKING.search(rest):
            major.append(f"{name} ({'Removed' if sec == 'Removed' else 'breaking'})")
    seen = "; ".join(f"{k}: {', '.join(v)}" for k, v in sorted(sections.items()))
    if major:
        return "major", "breaking: " + ", ".join(major)
    if not sections:
        return "patch", ("no changelog.d fragments: the release PR adds one per merged external "
                         "PR (crediting @author); its section decides the bump")
    if set(sections) - {"Fixed", "Security"}:
        return "minor", seen
    return "patch", seen


def latest_tag() -> str | None:
    tags = git("tag", "-l", "v*", "--sort=-v:refname").split()
    return tags[0] if tags else None


def next_version(tag: str, bump: str) -> str:
    """`v3.0.0` + patch → `3.0.1`, + minor → `3.1.0`, + major → `4.0.0`."""
    major, minor, patch = (int(x) for x in re.findall(r"\d+", tag)[:3])
    if bump == "major":
        return f"{major + 1}.0.0"
    return f"{major}.{minor + 1}.0" if bump == "minor" else f"{major}.{minor}.{patch + 1}"


def waiting_runs(pr: dict) -> list[str]:
    """Ids of the fork's workflow runs at the head that wait for approval; one gh call,
    made only when the rollup shows ACTION_REQUIRED."""
    if not any((c.get("conclusion") or "").upper() == "ACTION_REQUIRED"
               for c in pr.get("statusCheckRollup") or []):
        return []
    return gh("api", f"repos/{{owner}}/{{repo}}/actions/runs?head_sha={pr['headRefOid']}"
              "&status=action_required", "--jq", ".workflow_runs[].id").split()


def touches_github(pr: dict) -> bool:
    return any(f.startswith(".github/") for f in pr.get("paths") or [])


def print_run_approval(pr: dict) -> None:
    """The exact command for each waiting fork run, or why there is none."""
    for run in waiting_runs(pr):
        if touches_github(pr):
            print(f"  fork run {run} waits: do NOT approve it (the PR touches .github/); "
                  "escalate to the Captain")
        else:
            print(f"  fork run {run} waits — only after reading the whole diff; never if "
                  f"escalated by .github/:\n    gh api -X POST "
                  f"repos/{{owner}}/{{repo}}/actions/runs/{run}/approve")


def release_due(prs: list[dict]) -> bool:
    """Step 4: print RELEASE DUE (and return True) when an external PR merged since the
    latest `v*` tag and no release PR is open. A major prints RELEASE NEEDS CAPTAIN."""
    if any((p.get("title") or "").startswith(RELEASE_TITLE) for p in prs):
        return False
    merged = gh_json("pr", "list", "--state", "merged", "--limit", "100", "--json",
                     "number,title,author,isCrossRepository,mergeCommit,mergedAt,files")
    forks = [m for m in merged if m.get("isCrossRepository") and m.get("mergedAt")
             and (m.get("mergeCommit") or {}).get("oid")]
    if not forks:
        return False
    git("fetch", "-q", "origin", "main", "--tags")
    tag = latest_tag()
    if not tag:
        return False
    since = set(git("log", "--merges", "--format=%H", f"{tag}..origin/main").split())
    ext = [m for m in forks
           if m["mergeCommit"]["oid"] in since and association(m["number"]) not in MEMBERS]
    if not ext:
        return False
    names = ", ".join(f"#{m['number']} by {m['author']['login']}" for m in ext)
    bump, why = release_bump(fragments("origin/main"))
    if bump == "major":
        print(f"\n  RELEASE NEEDS CAPTAIN — {names} merged since {tag}; {why}. A major is "
              "the Captain's: open a chore issue labelled captain-approval, never cut it.")
        return False
    v = next_version(tag, bump)
    print(f"\nRELEASE DUE — {bump} after {tag} → v{v}: external PRs merged since: {names}"
          f"\n  fragments: {why}")
    for m in ext:
        if m.get("files") is not None and not any(
                f["path"].startswith("changelog.d/") for f in m["files"]):
            print(f"  #{m['number']} has no changelog.d fragment: add changelog.d/<issue>.md "
                  f"to the release PR, ending `Thanks @{m['author']['login']} (#{m['number']})`")
    print(f"  release PR `{RELEASE_TITLE}{v}`: python scripts/assemble_changelog.py {v}, set "
          f"{v} in pyproject.toml and src/yurtle_kanban/__init__.py"
          "\n  follow .claude/skills/external-pr/SKILL.md (Release) and skills/release/SKILL.md"
          "\n  after PyPI publishes, on each PR:")
    for m in ext:
        print(f"    gh pr comment {m['number']} --body "
              f"\"Released in v{v} on PyPI — thanks again!\"")
    return True


def agent_name() -> str:
    h = socket.gethostname().lower()
    return next((v for k, v in HOSTS.items() if k in h), h)


def verdict_at_head(pr: dict) -> str | None:
    """The PR head's verdict as safe_merge.sh judges it (#991), or None. Every member
    comment whose body starts `reviewed-at-sha:` or `fixes-at-sha:` is decisive, and the
    LATEST one decides: an `approve`/`changes` naming the head exactly, or the driver's
    fixes comment at the head for an earlier `reviewed-at-sha:` line's sha that isn't the
    head (`fixed`, #987). Any other decisive comment (a stale, prefix, uppercase or
    malformed one) leaves the head unreviewed, as the gate refuses it. The gate's other
    check, that the reviewed sha is an ancestor of the head, needs git and is left to it.
    (A comment without `authorAssociation`, as in tests, counts.)"""
    head = pr["headRefOid"]
    reviewed: set[str] = set()
    found = None
    for c in pr.get("comments") or []:
        body = c.get("body") or ""
        if c.get("authorAssociation", "MEMBER") not in MEMBERS or not body.startswith(
            ("reviewed-at-sha:", "fixes-at-sha:")
        ):
            continue
        lines = [line.removesuffix("\r") for line in body.split("\n")[:2]]
        if lines[0].startswith("reviewed-at-sha: "):
            reviewed.add(lines[0].removeprefix("reviewed-at-sha: "))
        found = None
        if m := VERDICT.fullmatch("\n".join(lines)):
            found = m.group(2) if m.group(1) == head else None
        elif (f := FIXES.fullmatch("\n".join(lines))) and (
            f.group(1) == head and f.group(2) != head and f.group(2) in reviewed
        ):
            found = "fixed"
    return found


def ci_state(pr: dict) -> str:
    """green | red | pending, from the status-check rollup.

    No checks at all is `pending`: this repo always runs CI, and right after a push the rollup
    is empty until Actions registers its runs.
    """
    rollup = pr.get("statusCheckRollup") or []
    state = "green" if rollup else "pending"
    for c in rollup:
        concl = (c.get("conclusion") or c.get("state") or "").upper()
        status = (c.get("status") or "COMPLETED").upper()
        if concl in CI_FAILED:
            return "red"
        if status != "COMPLETED" or concl in ("PENDING", "EXPECTED", ""):
            state = "pending"
    return state


def depends_on(body: str) -> set[int]:
    return {int(n) for grp in DEPENDS.findall(body or "") for n in re.findall(r"#(\d+)", grp)}


def held(labels: set[str]) -> list[str]:
    return ["held: " + ",".join(sorted(labels & HOLD))] if labels & HOLD else []


def my_pr_state(pr: dict) -> str:
    verdict, ci = verdict_at_head(pr), ci_state(pr)
    if pr.get("isDraft"):
        return "draft"
    if pr.get("mergeable") == "CONFLICTING":
        return "conflict"
    if verdict == "changes":
        return "changes-requested"
    if ci == "red":
        return "ci-red"
    if verdict is None:
        return "needs-review"
    return "ready-to-merge" if ci == "green" else "wait-ci"


def ships_in(pr: dict) -> str:
    """The release an external PR will ship in: the latest tag bumped by the unreleased
    fragments on origin/main plus the PR's own (fetched from refs/pull/<N>/head)."""
    tag = latest_tag()
    if not tag:
        return "the next release"
    own = [f.removeprefix("changelog.d/") for f in pr.get("paths") or []
           if f.startswith("changelog.d/") and "/" not in f[12:]]
    texts = fragments("origin/main")
    if own:
        git("fetch", "-q", "origin", f"pull/{pr['number']}/head")
        texts.update(fragments("FETCH_HEAD", own))
    bump, _ = release_bump(texts)
    return "the next release (a major: the Captain's)" if bump == "major" \
        else f"v{next_version(tag, bump)}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print the decision, claim nothing")
    ap.add_argument(
        "--skip-prs", action="store_true",
        help="go straight to claiming a new issue (pipelining while a PR is in review); "
        "every claim rule still applies",
    )
    ap.add_argument("--escalation", type=int, metavar="PR",
                    help="print the gate's escalation verdict for PR as JSON and exit "
                    "(safe_merge.sh's one source of the definitions)")
    ap.add_argument("--pr-json", metavar="FILE",
                    help="with --escalation: the PR's `gh pr view` JSON ('-' for stdin)")
    a = ap.parse_args()

    if a.escalation is not None:
        given = None
        if a.pr_json == "-":
            given = json.load(sys.stdin)
        elif a.pr_json:
            with open(a.pr_json) as fh:
                given = json.load(fh)
        print(json.dumps(escalation_report(a.escalation, given)))
        return

    me = gh("api", "user", "--jq", ".login").strip()
    print(f"AGENT: {agent_name()}  (gh: {me})")

    prs = gh_json("pr", "list", "--state", "open", "--limit", "100", "--json", PR_FIELDS)
    prs.sort(key=lambda p: p["number"])
    issues = gh_json("issue", "list", "--state", "open", "--limit", "300",
                     "--json", "number,title,labels,assignees,body")
    issues.sort(key=lambda i: i["number"])
    open_nums = {i["number"] for i in issues}
    issue_labels = {i["number"]: {lbl["name"] for lbl in i["labels"]} for i in issues}

    def issues_fixed_by(pr: dict) -> set[int]:
        # GitHub's own link (pairit always writes `Fixes #N`). Guessing from branch names
        # misreads `chore/100-col-lint` or a date branch and silently skips that issue.
        return {i["number"] for i in pr.get("closingIssuesReferences") or []}

    def issue_blockers(i: dict) -> list[str]:
        why = held(issue_labels[i["number"]])
        # `depends on #<PR>` waits while that PR is open too (#996)
        waits = sorted((depends_on(i.get("body") or "") & (open_nums | open_prs)) - {i["number"]})
        if waits:
            why.append("waits on " + ",".join(f"#{n}" for n in waits))
        return why

    fixed_by_open_pr: set[int] = set()
    for pr in prs:
        fixed_by_open_pr |= issues_fixed_by(pr)
    # an open PR whose TITLE names #N works on it, `Fixes` or not ("(#967, part 1)", #996);
    # a body mention doesn't count: bodies cite rulings and sibling issues all the time
    in_progress: dict[int, str] = {}
    for pr in prs:
        for n in map(int, re.findall(r"#(\d+)\b", pr.get("title") or "")):
            in_progress.setdefault(
                n, f"in progress in PR #{pr['number']} by {pr['author']['login']}")
    open_prs = {pr["number"] for pr in prs}

    if a.skip_prs:
        return claim(a, me, issues, fixed_by_open_pr, issue_blockers, issue_labels, in_progress)

    # 1. my own open PRs — a draft or a held PR (or one whose issue is held) is parked, not resumed
    mine = []
    for p in (p for p in prs if p["author"]["login"] == me):
        labels = {lbl["name"] for lbl in p.get("labels") or []}
        for n in issues_fixed_by(p):
            labels |= issue_labels.get(n, set())
        skip = held(labels) + (["draft"] if p.get("isDraft") else [])
        state = "SKIP: " + "; ".join(skip) if skip else my_pr_state(p)
        print(f"  my PR #{p['number']:<4} {p['title'][:60]}  [{state}]")
        if not skip:
            mine.append((p, state))
    if mine:
        p, state = next(((p, s) for p, s in mine if s != "wait-ci"), mine[0])
        print(f"\nRESUME PR #{p['number']} [{state}] — {p['title']}\n  branch: {p['headRefName']}")
        return

    # 2. external PRs (#1195): merge an approved one before reviewing a new one
    external = [p for p in prs if p["author"]["login"] != me and is_external(p)]
    picks: list[tuple[int, dict, list[str]]] = []
    for p in external:
        p["paths"] = pr_paths(p["number"])
        labels = {lbl["name"] for lbl in p.get("labels") or []}
        verdict, ci, why = verdict_at_head(p), ci_state(p), escalation(p, p["paths"])
        esc = "escalated: " + "; ".join(why) if why else "not escalated"
        skip = held(labels) + (["draft"] if p.get("isDraft") else [])
        if PROPOSED_REJECT in labels:
            state = "SKIP: proposed-reject (the Captain closes it)"
        elif skip:
            state = "SKIP: " + "; ".join(skip)
        elif verdict is None:
            state = "needs-review"
            picks.append((2, p, why))
        elif verdict == "changes":
            state = "SKIP: changes requested, waiting on the author's new head"
        elif p.get("mergeable") == "CONFLICTING":
            state = "SKIP: conflict, waiting on the author"
        elif ci != "green" and any(
                (c.get("conclusion") or "").upper() == "ACTION_REQUIRED"
                for c in p.get("statusCheckRollup") or []) and not touches_github(p):
            state = "approved, fork run waits"
            picks.append((1, p, why))
        elif ci != "green":
            state = f"SKIP: approved, CI {ci}"
        elif why and not (cap := captain_approval(p))[0]:
            state = "WAIT CAPTAIN"
            print(f"  WAIT CAPTAIN #{p['number']} by {p['author']['login']} — {esc}; "
                  f"{cap[1]}; only the Captain adds {CAPTAIN_APPROVED}, after the review "
                  "at the current head")
        else:
            state = "ready-to-merge"
            picks.append((0, p, why))
        print(f"  external PR #{p['number']:<4} by {p['author']['login']}  {p['title'][:50]}"
              f"  [{state}]  ({esc})")
    if picks:
        kind, p, why = min(picks, key=lambda t: (t[0], t[1]["number"]))
        n, login = p["number"], p["author"]["login"]
        esc = "escalated: " + "; ".join(why) if why else "not escalated"
        if kind == 0:
            print(f"\nMERGE EXTERNAL PR #{n} by {login} — {p['title']}"
                  f"\n  head: {p['headRefOid'][:12]}  ({esc}{', captain-approved' if why else ''})"
                  f"\n  bash .claude/skills/pairit/safe_merge.sh {n}"
                  f"\n  then: gh pr comment {n} --body "
                  f"\"Thanks @{login} — merged; this ships in {ships_in(p)}.\""
                  "\n  then pick again (RELEASE DUE); .claude/skills/external-pr/SKILL.md")
        elif kind == 1:
            print(f"\nRUN CI EXTERNAL PR #{n} by {login} — approved at its head, its fork run "
                  f"waits\n  head: {p['headRefOid'][:12]}  ({esc})")
            print_run_approval(p)
        else:
            print(f"\nREVIEW EXTERNAL PR #{n} by {login} — {p['title']}"
                  f"\n  head: {p['headRefOid'][:12]}  ({esc})"
                  "\n  follow .claude/skills/external-pr/SKILL.md (triage, fork CI, review)"
                  f"\n  gh pr diff {n}"
                  f"\n  git fetch -q origin pull/{n}/head && "
                  f"git worktree add /tmp/yk-rev-{n} {p['headRefOid']}")
            print_run_approval(p)
        return
    external_nums = {p["number"] for p in external}

    # 3. other FLEET authors' PRs that have no verdict at their head (review only)
    for p in prs:
        if (p["author"]["login"] != me and p["number"] not in external_nums
                and not p.get("isDraft") and verdict_at_head(p) is None):
            print(f"\nREVIEW PR #{p['number']} by {p['author']['login']} — {p['title']}"
                  f"\n  head: {p['headRefOid'][:12]}  branch: {p['headRefName']}")
            return

    # 4. an external PR merged since the last tag: cut the release (patch/minor only)
    if release_due(prs):
        return

    # 5. an issue I already hold, with no PR yet — unless it has since been held or blocked
    for i in issues:
        if me in {x["login"] for x in i["assignees"]} and i["number"] not in fixed_by_open_pr:
            why = issue_blockers(i)
            if why:
                print(f"  my issue #{i['number']:<4} SKIP: {'; '.join(why)}  {i['title'][:60]}")
                continue
            print(f"\nRESUME ISSUE #{i['number']} — {i['title']}")
            return

    claim(a, me, issues, fixed_by_open_pr, issue_blockers, issue_labels, in_progress)


def claim(
    a: argparse.Namespace,
    me: str,
    issues: list[dict],
    fixed_by_open_pr: set[int],
    issue_blockers: Callable[[dict], list[str]],
    issue_labels: dict[int, set[str]],
    in_progress: dict[int, str],
) -> None:
    """Claim the first eligible issue (step 6); every claim rule applies."""
    # 6. claim a new one
    cands = []
    for i in issues:
        why = []
        if i["assignees"]:
            why.append("assigned to " + ",".join(x["login"] for x in i["assignees"]))
        if i["number"] in fixed_by_open_pr:
            why.append("an open PR fixes it")
        elif i["number"] in in_progress:
            why.append(in_progress[i["number"]])
        why += issue_blockers(i)
        cands.append((0 if "bug" in issue_labels[i["number"]] else 1, i["number"], i, why))
    cands.sort(key=lambda c: c[:2])

    print("CANDIDATES:")
    for _, n, i, why in cands:
        status = "SKIP: " + "; ".join(why) if why else "ok"
        print(f"  #{n:<4} {i['title'][:60]}  [{status}]")

    for _, n, i, why in cands:
        if why:
            continue
        if a.dry_run:
            print(f"\nWOULD CLAIM ISSUE #{n} — {i['title']}")
            return
        gh("issue", "edit", str(n), "--add-assignee", "@me")
        view = gh_json("issue", "view", str(n), "--json", "assignees")
        now = {x["login"] for x in view["assignees"]}
        if now - {me}:
            gh("issue", "edit", str(n), "--remove-assignee", "@me")
            print(f"  claim of #{n} lost the race to {','.join(sorted(now - {me}))} — next")
            continue
        print(f"\nCLAIMED ISSUE #{n} — {i['title']}")
        return

    print("\nNOTHING READY — every open issue is held, assigned, waiting, or already has a PR.")


if __name__ == "__main__":
    main()
