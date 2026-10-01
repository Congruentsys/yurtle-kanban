---
name: yk-next
description: ONE pass — what should THIS session do next in yurtle-kanban? Resumes my own open PR that needs something (review, fixes, CI, merge), else carries an external PR (merge an approved one, review a new one), else reviews another fleet author's unreviewed PR, else cuts a due release after an external merge, else resumes my assigned issue, else claims the first ready issue (bug first, then lowest number; held/assigned/waiting/already-PR'd issues skipped). Prints it and STOPS. Ported from rachael-lab's rachael-next (2026-09-24), onto GitHub issues + PRs.
disable-model-invocation: false
allowed-tools: Bash(.venv/bin/python *), Bash(python3 *), Bash(gh *), Bash(git *)
---

# yk-next: the picker

This repo keeps its work on GitHub, not on a kanban board: an issue is the work item and a PR carries it.
Run from the repo root:

```bash
python3 .claude/skills/yk-next/yk_next.py            # claims (assigns the issue to you on GitHub)
python3 .claude/skills/yk-next/yk_next.py --dry-run  # prints the ordered candidates, claims nothing
python3 .claude/skills/yk-next/yk_next.py --skip-prs # jump straight to claiming a NEW issue
```

`--skip-prs` is for pipelining (yk-loop): while one of your PRs is in review, it goes straight to rule 6
(claim), skipping rules 1–5. It doesn't resume your PRs, doesn't review others', and **doesn't resume an
issue already assigned to you**, so it claims a new one even if you hold one with no PR yet. Every claim
rule still applies. Outside yk-loop, use the plain picker.

**The rules** all live in `yk_next.py`, so this file can't drift from them:
1. **Finish before you start.** Your own open PR comes first. The script prints one of these states:
   `changes-requested`, `ci-red`, `conflict`, `needs-review`, `ready-to-merge` or `wait-ci`. A draft,
   or a PR with a hold label on itself or on the issue it fixes, is **parked** (`SKIP`), never resumed.
   That's how pairit sets a PR aside when a finding needs a decision, without the loop getting stuck.
   A head carrying the driver's `fixes-at-sha:` comment (pairit's one review round, #987) counts as
   reviewed: it is `ready-to-merge` once CI is green, never `needs-review` again.
2. **External PRs** (#1195; author not OWNER/MEMBER/COLLABORATOR) follow `.claude/skills/external-pr/SKILL.md`.
   `MERGE EXTERNAL PR #N`: approve at head, CI green, and not escalated or labelled `captain-approved`.
   `REVIEW EXTERNAL PR #N`: no member verdict at its head. Listed, never picked: `WAIT CAPTAIN #N`
   (escalated: label `captain-approval`, a `class: captain` verdict line, or a release/CI/security path),
   a `changes` verdict (waiting on the author's new head), `proposed-reject` (the Captain closes it).
   Only the Captain adds `captain-approved`; the fleet never does.
3. **Review the fleet's work.** Next is another FLEET author's open PR with no verdict at its current head.
   You may review it, because reviewer ≠ author. The author merges it, not you.
4. **Release.** An external PR merged since the latest `v*` tag, and no open `chore: release v…` PR:
   `RELEASE DUE — patch|minor` from the unreleased `changelog.d/` fragments (Fixed/Security only → patch,
   any Added/Changed → minor). A Removed or breaking fragment prints `RELEASE NEEDS CAPTAIN` and picks
   nothing: a major is the Captain's.
5. **Resume before you pick.** An open issue assigned to you that no open PR fixes yet, unless it has
   since been held or depends on an issue that's still open.
6. **Claim.** Take the first open issue that is unassigned, has no open PR fixing it (GitHub's
   `Fixes #N` link; branch names are never guessed at) or naming it in its title (`(#967, part 1)`: in
   progress there; a body mention doesn't count, #996), has no hold label (`needs-decision`,
   `question`, `wontfix`, `duplicate`, `invalid`, `blocked`, `on-hold`), and whose body's
   `depends on #N` / `blocked by #N` / `requires #N` issues (or PRs, #996) are all closed. Lists count: `blocked by #8, #9`.
   Order: `bug` first, then the lower number.
7. **Atomic-enough claim.** Assign `@me`, then re-read the issue. Another assignee means a peer got there
   first, so the script un-assigns you and moves to the next candidate.

**A verdict** is a PR comment whose first two lines are `reviewed-at-sha: <sha>` and
`verdict: approve|changes`. It counts only at the PR's current head, so pushing a new commit resets it,
except for the driver's fixes comment (`fixes-at-sha: <head>` / `for-review-at: <reviewed sha>`), which
carries one round's review to its fixed tip (#987). The picker reads these exactly as `safe_merge.sh` does (#991): the full
lowercase sha, the latest decisive member comment deciding, so a late verdict of an old sha unreviews
the head. One check stays the gate's alone: that a fixes comment's reviewed sha is an ancestor of the
head (it needs git); a fixes comment naming an unrelated sha reads `ready-to-merge` and `safe_merge.sh` refuses it.
A PR with no CI checks reported yet reads as `wait-ci`, never as mergeable.

**One loop per GitHub account.** Identity is the `gh` login. Two sessions on the same account would both
resume the same PR, and the claim race can't tell them apart.

| the output | what to do |
|---|---|
| `RESUME PR #N [changes-requested / ci-red / conflict]` | fix it on its branch (pairit step 2), push, then re-review (step 3) |
| `RESUME PR #N [needs-review]` | pairit step 3 |
| `RESUME PR #N [ready-to-merge]` | pairit step 4 |
| `RESUME PR #N [wait-ci]` | `gh pr checks N --watch`, then pick again |
| `REVIEW PR #N` | pairit step 3, as the reviewer for someone else's PR |
| `REVIEW EXTERNAL PR #N` / `MERGE EXTERNAL PR #N` | `.claude/skills/external-pr/SKILL.md` |
| `RELEASE DUE — <bump>` | the Release section of `external-pr/SKILL.md` (patch/minor only) |
| `RESUME ISSUE #N` / `CLAIMED ISSUE #N` | triage it (yk-loop), then land it with `pairit` |
| `NOTHING READY` / `ERROR: …` | stop |

The issue body is the brief: its repro, its Expected section and its notes.

**Boundaries.** Never merge another FLEET member's PR (review only). External PRs follow
`external-pr/SKILL.md`: the fleet merges and releases them within its limits. Never set `captain-approved`,
never close an external PR, never cut a major release.
