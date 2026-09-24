#!/usr/bin/env python3
"""yk_next.py: what should THIS session do next in yurtle-kanban? One pass, then stop.

This repo's work lives on GitHub (issues and PRs), not on a kanban board. In order:

1. RESUME PR     my own open PR that needs something: changes requested, CI red, a merge
                 conflict, no review at its head sha, or approved + green (so: merge it).
                 CI still running is WAIT.
2. REVIEW PR     another author's open PR with no verdict at its head sha (reviewer != author).
3. RESUME ISSUE  an open issue assigned to me that no open PR fixes yet.
4. CLAIMED ISSUE the first open, unassigned issue that no open PR fixes, carries no hold label,
                 and whose "depends on #N" / "blocked by #N" issues are all closed.
                 `bug` first, then the lower number.
   A claim is `gh issue edit N --add-assignee @me`, then a re-read: another assignee means a
   peer got there first, so un-assign and take the next one.
5. NOTHING READY

A review verdict is a PR comment whose first two lines are `reviewed-at-sha: <sha>` and
`verdict: approve|changes` (pairit step 3). It only counts at the PR's CURRENT head.

Usage: python3 .claude/skills/yk-next/yk_next.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import re
import socket
import subprocess
import sys

HOSTS = {"m4-mini": "Mini", "mini": "Mini", "m5": "M5", "spark": "DGX"}
HOLD = {"needs-decision", "question", "wontfix", "duplicate", "invalid", "blocked", "on-hold"}
VERDICT = re.compile(
    r"\Areviewed-at-sha:\s*([0-9a-f]{7,40})\s*\nverdict:\s*(approve|changes)\b", re.I,
)
CI_FAILED = {"FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE"}
DEPENDS = re.compile(r"(?:depends on|blocked by|requires)\s+#(\d+)", re.I)
BRANCH_ISSUE = re.compile(r"(?:^|/)(\d+)-")
PR_FIELDS = (
    "number,title,author,headRefName,headRefOid,isDraft,mergeable,"
    "statusCheckRollup,comments,closingIssuesReferences"
)


def gh(*args: str) -> str:
    r = subprocess.run(["gh", *args], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"ERROR: gh {' '.join(args)}: {(r.stderr or r.stdout).strip()[:300]}")
    return r.stdout


def gh_json(*args: str) -> list | dict:
    return json.loads(gh(*args))


def agent_name() -> str:
    h = socket.gethostname().lower()
    return next((v for k, v in HOSTS.items() if k in h), h)


def verdict_at_head(pr: dict) -> str | None:
    """The latest verdict posted for the PR's current head sha, or None."""
    head = pr["headRefOid"]
    found = None
    for c in pr.get("comments") or []:
        m = VERDICT.match((c.get("body") or "").strip())
        if m and head.startswith(m.group(1).lower()):
            found = m.group(2).lower()
    return found


def ci_state(pr: dict) -> str:
    """green | red | pending, from the status-check rollup (no checks at all counts as green)."""
    state = "green"
    for c in pr.get("statusCheckRollup") or []:
        concl = (c.get("conclusion") or c.get("state") or "").upper()
        status = (c.get("status") or "COMPLETED").upper()
        if concl in CI_FAILED:
            return "red"
        if status != "COMPLETED" or concl in ("PENDING", "EXPECTED", ""):
            state = "pending"
    return state


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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print the decision, claim nothing")
    a = ap.parse_args()

    me = gh("api", "user", "--jq", ".login").strip()
    print(f"AGENT: {agent_name()}  (gh: {me})")

    prs = gh_json("pr", "list", "--state", "open", "--limit", "100", "--json", PR_FIELDS)
    prs.sort(key=lambda p: p["number"])
    fixed_by_open_pr: set[int] = set()
    for pr in prs:
        fixed_by_open_pr |= {i["number"] for i in pr.get("closingIssuesReferences") or []}
        m = BRANCH_ISSUE.search(pr["headRefName"])
        if m:
            fixed_by_open_pr.add(int(m.group(1)))

    # 1. my own open PRs
    mine = [(p, my_pr_state(p)) for p in prs if p["author"]["login"] == me]
    for p, state in mine:
        print(f"  my PR #{p['number']:<4} {state:17} {p['title'][:70]}")
    actionable = [(p, s) for p, s in mine if s != "wait-ci"]
    if actionable or mine:
        p, state = (actionable or mine)[0]
        print(f"\nRESUME PR #{p['number']} [{state}] — {p['title']}\n  branch: {p['headRefName']}")
        return

    # 2. other authors' PRs that have no verdict at their head
    for p in prs:
        if p["author"]["login"] != me and not p.get("isDraft") and verdict_at_head(p) is None:
            print(f"\nREVIEW PR #{p['number']} by {p['author']['login']} — {p['title']}"
                  f"\n  head: {p['headRefOid'][:12]}  branch: {p['headRefName']}")
            return

    issues = gh_json("issue", "list", "--state", "open", "--limit", "300",
                     "--json", "number,title,labels,assignees,body")
    open_nums = {i["number"] for i in issues}

    # 3. an issue I already hold, with no PR yet
    for i in sorted(issues, key=lambda i: i["number"]):
        if me in {x["login"] for x in i["assignees"]} and i["number"] not in fixed_by_open_pr:
            print(f"\nRESUME ISSUE #{i['number']} — {i['title']}")
            return

    # 4. claim a new one
    cands = []
    for i in issues:
        labels = {lbl["name"] for lbl in i["labels"]}
        why = []
        if i["assignees"]:
            why.append("assigned to " + ",".join(x["login"] for x in i["assignees"]))
        if i["number"] in fixed_by_open_pr:
            why.append("an open PR fixes it")
        if labels & HOLD:
            why.append("held: " + ",".join(sorted(labels & HOLD)))
        deps = {int(n) for n in DEPENDS.findall(i.get("body") or "")}
        waits = sorted((deps & open_nums) - {i["number"]})
        if waits:
            why.append("waits on " + ",".join(f"#{n}" for n in waits))
        cands.append((0 if "bug" in labels else 1, i["number"], i, why))
    cands.sort(key=lambda c: c[:2])

    print("CANDIDATES:")
    for _, n, i, why in cands:
        print(f"  #{n:<4} {'SKIP: ' + '; '.join(why) if why else 'ok':40.40}  {i['title'][:70]}")

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
