---
name: pairit
description: XP pair work for ONE yurtle-kanban issue. A test partner in a fresh context writes regression tests from the issue body and proves them red; the driver writes code to green without touching those tests; a DISTINCT `claude -p` reviewer session posts a verdict on the PR; the driver merges the PR once CI is green. Ported from rachael-lab's pairit (2026-09-24) onto GitHub PRs — this repo never pushes to main directly.
argument-hint: "<issue number>"
disable-model-invocation: false
allowed-tools: Bash(.venv/bin/*), Bash(python3 *), Bash(git *), Bash(gh *), Bash(claude *), Agent
---

# pairit: tests from a partner, code from the driver, one reviewer, merge the PR

**Why a partner writes the tests:** tests written in the same context as the code share its blind spots.
Tests written first by another context, from the issue, do not.

**The check** is what CI runs; run it from the worktree:
```bash
.venv/bin/python -m pytest -q && .venv/bin/ruff check src/
```
pytest's `pythonpath = ["src"]` means a worktree's tests import that worktree's `src`, even with the shared
`.venv`. To run the CLI from a worktree, use `PYTHONPATH=/tmp/yk-<N>/src .venv/bin/yurtle-kanban …`.

```text
0. BRANCH  a worktree + branch from origin/main
1. TESTS   the test partner (a fresh Agent sub-agent) writes pytest tests from the issue → commit T, proven RED
2. CODE    the driver writes code to GREEN without editing T's tests, adds a CHANGELOG entry, runs the check
3. REVIEW  push, open the PR, and a DISTINCT `claude -p` session posts a verdict comment (max 2 rounds)
4. MERGE   verdict approve at head + CI green → `gh pr merge`; the issue closes through `Fixes #N`
```

**0. Branch.** Use `fix/` for a bug, `feat/` for a feature and `chore/` for anything else.
```bash
git fetch -q origin main
git worktree add -b fix/<N>-<slug> /tmp/yk-<N> origin/main
ln -s "$PWD/.venv" /tmp/yk-<N>/.venv
```
Every later step uses the literal path `/tmp/yk-<N>`.

**1. The partner's brief** (Agent tool, fresh context). Give it these lines verbatim:
- *"Read issue #<N> (`gh issue view <N>`). Write tests for what the issue SAYS — its repro and its Expected
  section — not for how you would fix it. Work only in /tmp/yk-<N>."*
- *"pytest, in the existing `tests/test_<module>.py` that owns the behaviour, as a NEW class named after the
  issue (e.g. `TestMoveAssignMissingKey`, docstring ending `(#<N>)`). Reproduce the issue's own commands
  through `CliRunner` where it gives CLI steps. Add a negative control, where one applies, that must stay
  green. Use the file's existing fixtures."*
- *"Commit the tests ALONE as `#<N>: tests (red)`. Run them and confirm they are RED for the RIGHT reason (an
  assertion, never an import or fixture error), and that the rest of the suite is still green. Return the
  sha, the paths and the failing test names."*

If the issue can't be reproduced on `origin/main` (it's already fixed, or the repro is wrong), the partner
says so and commits nothing. Comment the finding on the issue, then close it or label it `needs-decision`.

**2. Code to green.** Never edit T's test files; if a test is wrong, send it back to the partner. Keep the
change the smallest one that fixes the issue. Match the surrounding code: type hints, comment density,
existing helpers (`_add_or_update_frontmatter_field`, `PRIORITIES`, …). Add an entry under
`## [Unreleased]` → `### Fixed` (or `### Added`) in `CHANGELOG.md` that names `(#<N>)`. Run the check. The
80 ruff findings in `tests/` are known, and CI lints only `src/`.

**3. The review, by a DISTINCT session.**
```bash
git -C /tmp/yk-<N> push -q -u origin HEAD
gh pr create --head <branch> --title "<type>: <what> (#<N>)" --body "Fixes #<N>. …"
claude --dangerously-skip-permissions -p "<brief>" < /dev/null
```
End the PR body with the attribution line from the session's system reminder.

The brief tells the reviewer to:
- review PR #<P>, branch `<branch>` at tip `<SHA>`, in its OWN scratch worktree
  (`git worktree add /tmp/yk-rev-<P> <SHA>` plus the `.venv` symlink), read-only on the branch;
- check that the tests in `<T>` match the ISSUE, not the code;
- confirm `git diff <T>..<SHA> -- <T's test files>` is empty;
- confirm the code doesn't special-case the tests;
- mutate the code at two points and see a test go red each time;
- re-run the issue's own repro against the branch (`PYTHONPATH=…/src .venv/bin/yurtle-kanban …`);
- check every caller of each changed function for regressions, and confirm the check is green;
- post ONE PR comment (`gh pr comment <P> --body-file …`) whose first line is `reviewed-at-sha: <SHA>` and
  second line `verdict: approve|changes`, followed by findings with `file:line` and a failure scenario.
  Non-blocking findings are marked `(follow-up)`.

**At most two rounds.** After a `changes` verdict, fix the findings as a new commit (step 2), push and review
again. A second `changes` stops the item. Comment why on the issue, then park both the issue and the PR:
`gh issue edit <N> --add-label needs-decision` and `gh pr edit <P> --add-label needs-decision`. The picker
skips a held PR, so the loop moves on and doesn't reopen it.

The verdict comment is posted under the same GitHub account as the PR, so GitHub can't enforce reviewer ≠
author. The distinct `claude -p` session is what makes the review independent, and the comment's author
proves nothing.

**4. Merge** (from the main checkout):
```bash
gh pr checks <P> --watch                      # all green
git worktree remove --force /tmp/yk-<N>       # FIRST: the local branch is checked out there, so
                                              # `--delete-branch` would fail after the merge
gh pr merge <P> --merge --delete-branch       # deletes the remote and local branch
git checkout -q main && git pull -q
gh issue view <N> --json state --jq .state    # CLOSED (via "Fixes #N")
```
Merge only with an `approve` verdict at the PR's CURRENT head sha. Any commit after the verdict needs a new
verdict. Done means the merge is on `origin/main` and the issue is closed.

**Carry the findings.** Every `(follow-up)` finding becomes an issue (`gh issue create --label bug …`,
unassigned) with a verified repro, cross-linked to the PR.
