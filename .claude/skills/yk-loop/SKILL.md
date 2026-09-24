---
name: yk-loop
description: The yurtle-kanban work loop. IN-SESSION it runs the picker (yk-next), carries what it hands you — finishes open PRs, reviews others' PRs, lands issues with pairit — then picks again. Stops on NOTHING READY, a picker error, or the same item picked a third time without progress. Never ScheduleWakeup; never end the turn between items. Ported from rachael-lab's rachael-loop (2026-09-24).
disable-model-invocation: false
allowed-tools: Bash(.venv/bin/*), Bash(python3 *), Bash(git *), Bash(gh *), Bash(claude *), Agent
---

# yk-loop: pick, land, pick again

```text
repeat:
  1. PICK    python3 .claude/skills/yk-next/yk_next.py
  2. STOP?   "NOTHING READY", "ERROR: …", or the same item picked a third time without progress → stop,
             report one line
  3. TRIAGE  (issues only) is this a fix, or a decision? → a decision is commented + held, never built
  4. LAND    through pairit: a PR resumes at the step its state names; an issue starts at step 0
  5. CARRY   a finding that BLOCKS this item is filed and landed first; every other finding is FILED
             (`gh issue create --label bug`), unassigned
  6. goto 1 at once, in this same turn
```

**Before the first pick:** `git checkout -q main && git pull -q`, and make sure the check passes on `main`
(`.venv/bin/python -m pytest -q && .venv/bin/ruff check src/`). A red `main` is the first item: fix it on a
branch through pairit before anything else. If `.venv` is missing, run
`python3.11 -m venv .venv && .venv/bin/pip install -q -e ".[dev]"`.

**3. Triage.** Land an issue only if it has ONE evident correct outcome: a bug with a repro and an Expected
section, or a small change whose shape the issue already fixes. Hold it instead when any of these is true:
- it's a feature that needs a design (a new command, a new workflow, a new theme type, a transport);
- a reasonable maintainer could want two different behaviours, or the fix changes documented behaviour
  someone may rely on;
- it touches the release, versioning or publishing path;
- it would take more than about 400 changed lines, or it can't be tested offline.

To hold it: post one comment (what you found, the options, your recommendation, and the question to
answer), then `gh issue edit <N> --add-label needs-decision --remove-assignee @me`, and pick again.

**One item = one context.** This session stays thin. It picks, triages, spawns the test partner (a fresh
`Agent`) and the reviewer (a distinct `claude -p` session), writes the code or delegates it to an
implementer sub-agent, and merges.

**Report through GitHub.** The PR, its verdict comment and the closed issue are the record. For each landed
item, give the Captain one line (`#N → PR #P merged: <what changed>`) and keep going.

**One loop per GitHub account.** The picker knows you only by your `gh` login, so a second loop (or a
person) working on the same account would resume the same PRs and issues.

**Never `ScheduleWakeup`, and never end the turn to report between items.** The only wait inside the loop is
`gh pr checks <P> --watch`.

**Don't idle while a sub-agent or reviewer works: pipeline.** While a test partner or a `claude -p` reviewer
runs in the background, claim the next issue with `python3 .claude/skills/yk-next/yk_next.py --skip-prs` and
start it in its own worktree. Plain picking would just answer `RESUME PR … [needs-review]` for the PR under
review. `--skip-prs` goes straight to the claim step with every rule intact (bug first, hold labels,
`depends on`, "an open PR fixes it", the claim-race re-read). Never claim with a bare `gh issue edit`. Two
rules keep it safe:
- Don't run two items that edit the same function at once; hold the second until the first merges.
- A regression you introduced jumps the queue: fix it before new work.

**Boundaries.** Never push to `main`, since everything lands by PR (project CLAUDE.md). Never merge another
author's PR; review it and move on. Never bump the version or publish; releases are the Captain's call.
