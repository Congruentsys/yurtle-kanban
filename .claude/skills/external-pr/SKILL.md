---
name: external-pr
description: Carry ONE external PR (author not OWNER/MEMBER/COLLABORATOR) through the Captain's #1195 process — triage, fork CI, a distinct-session review with a `class:` line, then merge + release, escalate to the Captain, ask the author for changes, or propose a reject. The picker hands it out as REVIEW EXTERNAL PR / MERGE EXTERNAL PR / RELEASE DUE.
argument-hint: "<PR number>"
disable-model-invocation: false
allowed-tools: Bash(.venv/bin/*), Bash(python3 *), Bash(git *), Bash(gh *), Bash(claude *), Agent
---

# external-pr: review, escalate, propose-reject, merge and release

**The Captain's ruling (2026-10-01, #1195).** The fleet reviews, merges and releases external PRs,
within these limits:
- **Escalate** a breaking change (a major bump) or anything on the release/CI/security path. The Captain
  approves by adding `captain-approved` to the PR. **The fleet never sets `captain-approved`.**
- **Reject** is proposed: label `proposed-reject` plus a reason comment. **Never close an external PR**;
  only the Captain closes.
- **Publish** right after each external merge: Fixed → patch, Added/Changed → minor. **Never cut a major**;
  it waits for the Captain.

The definitions live in `.claude/skills/yk-next/yk_next.py` and `safe_merge.sh` applies the same ones:
- **external**: `gh api repos/{owner}/{repo}/pulls/<P> --jq .author_association` is not OWNER, MEMBER or
  COLLABORATOR.
- **escalated**: label `captain-approval`, OR the latest member verdict has a line `class: captain…`, OR the
  PR touches `.github/**`, `skills/release/**`, `.claude/skills/**`, `pyproject.toml`,
  `src/yurtle_kanban/__init__.py` or `scripts/check_release_version.py`.

```text
1. TRIAGE   external? escalated, and why? (the picker prints both)
2. FORK CI  read the WHOLE diff, then approve the waiting run; never for .github/ changes
3. REVIEW   a DISTINCT `claude -p` session posts the verdict, with a `class:` third line
4. OUTCOME  approve+routine → merge + release | approve+captain → escalate | changes → ask the author
            | reject → propose it
```

**Thank the submitter at every outcome**, as open-source practice. Keep it short:

| when | comment (`gh pr comment <P> --body "…"`) |
|---|---|
| merged | `Thanks @login — merged; this ships in vX.Y.Z.` (the picker prints it, version included) |
| published | `Released in vX.Y.Z on PyPI — thanks again!` (RELEASE DUE prints it) |
| changes | `Thanks @login for this! A few things before it can merge:` then the findings |
| proposed-reject | `Thanks @login for the effort here. <why>. The Captain makes the final call.` |

The changelog credits them too: the fragment's entry ends `Thanks @login (#<P>)`.

**The picker prints the commands.** Each external pick names its next steps: `REVIEW EXTERNAL PR` the diff,
the checkout and any waiting fork run's approval; `RUN CI EXTERNAL PR` that approval for an approved PR;
`MERGE EXTERNAL PR` the `safe_merge.sh` call and the thank-you; `RELEASE DUE` the version, the assemble
command, a missing fragment and the post-publish notes. Run them as printed.

**1. Triage.**
```bash
gh pr view <P> --json number,title,author,labels,files,changedFiles,headRefOid,isCrossRepository,body
gh api repos/{owner}/{repo}/pulls/<P> --jq .author_association
gh pr diff <P>
```
Note which escalation rule applies, if any. A PR labelled `proposed-reject` is the Captain's: skip it.

**2. Fork CI.** A fork's first runs wait for approval (`action_required`). Read the whole diff first, every
file, looking for anything that runs at install, import or test time and reaches the network, secrets or the
filesystem outside the repo. Only then approve:
```bash
gh api "repos/{owner}/{repo}/actions/runs?head_sha=<HEAD>&status=action_required" --jq '.workflow_runs[].id'
gh api -X POST repos/{owner}/{repo}/actions/runs/<id>/approve
```
**Never approve a run for a PR that touches `.github/`.** Escalate it instead (step 4, approve+captain), even
before review; the Captain decides whether its CI runs.

**3. Review, by a distinct session.** Check the head out read-only in a scratch worktree
(`git fetch -q origin pull/<P>/head && git worktree add /tmp/yk-rev-<P> <HEAD>` plus the `.venv` symlink).
Build the brief from explicit values (PR number, the full head sha), as pairit step 3 does, and launch
`claude --dangerously-skip-permissions -p "$(cat <brief>)" < /dev/null`. The brief tells the reviewer to:
- review PR #<P> at `<HEAD>` against the issue it fixes and the repo's goals;
- run the check (`.venv/bin/python -m pytest -q` on the named test files, `.venv/bin/ruff check src/`);
- judge **breaking changes**: CLI flags or commands removed or renamed, `--json` or MCP output shape, file
  formats (`.kanban/`, work-item frontmatter, config), documented behaviour someone may rely on;
- post ONE PR comment whose first lines are exactly:
  ```text
  reviewed-at-sha: <HEAD>
  verdict: approve|changes
  class: routine            (or: class: captain (<reason>))
  ```
  then the findings with `file:line`. `class: captain` for a breaking change, the release/CI/security path,
  or anything else that needs the Captain's authority.

A `reject` is the reviewer's recommendation in the findings (out of scope, a duplicate, or a design that
conflicts with a ruling); the verdict line stays `changes`.

**4. Outcomes.**
- **approve + routine**, CI green → `bash .claude/skills/pairit/safe_merge.sh <P>` (it reads the fork head
  from `refs/pull/<P>/head` and refuses anything escalated without `captain-approved`), the merged thank-you,
  then **Release**.
- **approve + captain** (or escalated by label or path) → escalate and move on:
  ```bash
  gh issue create --label captain-approval --title "chore: Captain approval for external PR #<P>" \
    --body "<what it changes; why escalated; the verdict comment's link; recommended answer>"
  gh pr edit <P> --add-label captain-approval
  ```
  The picker lists it as `WAIT CAPTAIN` until the Captain adds `captain-approved`; then it picks
  `MERGE EXTERNAL PR` and you merge and release as above.
- **changes** → ONE comment to the author, opening with thanks, then every finding, concretely. Wait for a
  new head; the picker re-offers the PR for review at it. The fleet never pushes to a contributor's branch.
- **reject** → `gh pr edit <P> --add-label proposed-reject` and one comment that thanks them for the effort,
  gives the reason (out of scope / duplicate of #N / conflicts with the ruling in #N) and says the Captain
  makes the final call. Never close it.

**Release** (the picker prints `RELEASE DUE — <bump>` once an external PR has merged since the last tag and
no `chore: release v…` PR is open). Follow `skills/release/SKILL.md` for a **patch or minor** only:
- a release PR `chore: release vX.Y.Z`, built with `python scripts/assemble_changelog.py X.Y.Z`. If a merged
  external PR has no `changelog.d/` fragment, the release PR adds one first, crediting the author
  (`… Thanks @login (#<P>)`), and an existing one gains that credit; its section decides the bump;
- reviewed by a distinct `claude -p` session (pairit step 3), merged with `safe_merge.sh`;
- tag the merge commit, push the tag, `gh release create vX.Y.Z` with notes under GitHub's **125,000-char**
  cap: the version's CHANGELOG section, or, if over, a short summary plus a link to `CHANGELOG.md` (#1191);
- confirm `publish.yml` ran green and the version is on PyPI, then post the published note on each PR.

`RELEASE NEEDS CAPTAIN` (a Removed or breaking fragment: a major) → open a chore issue labelled
`captain-approval` naming the fragments and the PRs. **Never cut a major.**

**Boundaries.** Never set `captain-approved`. Never close an external PR. Never approve fork CI before reading
the whole diff, and never for `.github/` changes. Never cut a major release. A fleet member's PR is still
review-only (its author merges it).
