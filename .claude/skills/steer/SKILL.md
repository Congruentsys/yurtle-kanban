---
name: steer
description: The outer loop above /yk-loop. Sweep every issue or PR that is waiting on a decision, classify each (a measurement settles it → measure; the goals or an existing user ruling settle it → decide now; it changes a FEATURE or GOAL, or needs human authority → escalate), DECIDE everything in the first two buckets, and put only the third to the user — batched, each with a recommended default. Run it before holding anything as `needs-decision`, and whenever the decision queue is non-empty. Ported from nusy-product-team's /steer (2026-09-25).
---

# steer: decide what data and goals can decide; escalate only the rest

The loop used to stall on questions it could answer itself. This pass answers them. **Only a decision that no
measurement, no goal and no earlier ruling can settle goes to the user.**

## Goals (the north star for every decision)

yurtle-kanban is the work-tracking CLI the fleet runs on. In priority order:

- **G1 — correctness and data safety.** Never lose, corrupt or silently rewrite an item file; never commit,
  push or merge something unintended; refuse bad input cleanly instead of half-writing.
- **G2 — fleet-consumer compatibility.** The consumers (nusy-product-team, noesis-ship, carclaw,
  noesis-ships-comm, rachael-lab) keep working: behaviour they rely on does not change silently.
- **G3 — simplicity.** The smallest change that meets G1/G2; no speculative options; one way to do a thing.

## The classifier (apply to every pending decision, in order)

1. **Bucket 1 — could a measurement settle it?** → **measure.** Scan the fleet repos READ ONLY
   (`/Users/hankh19/Projects/{carclaw,noesis-ship,noesis-ships-comm,nusy-product-team,rachael-lab}` — never
   write there), time it, count it, reproduce it. The data decides; "no consumer has this shape" is data.
2. **Bucket 2 — do the goals, or an EXISTING user ruling, settle it?** → **decide now.** Pick the option that best serves
   G1, then G2, then G3, weighing impact × reversibility. Applying an existing ruling (for example #109's "each
   type only goes into its named folder") to a new case is bucket 2, never bucket 3.
3. **Bucket 3 — does it change a FEATURE or a GOAL, or need human authority?** → **escalate.** A feature change alters what
   the CLI does for users who rely on it (a new command, workflow, theme type or transport; a behaviour change a
   consumer actually uses — measured, not guessed); a goal change alters G1–G3; human authority means the
   release/version/publishing path, another repo, money, or a legal or ethical call.

> Before escalating, **name the trigger** — the feature or goal it changes, or the authority it needs. If you
> can't name one, it is bucket 1 or 2: decide it.

## One pass

```text
1. SWEEP     gh issue list --label needs-decision; any issue the triage step would hold; PRs parked needs-decision
2. RULINGS   read each thread (gh issue view <N> --comments) — a user ruling already there makes it bucket 2
3. CLASSIFY  bucket 1 → measure, then decide on the data; bucket 2 → decide; bucket 3 → collect
4. RECORD    on every item you decide: a comment `[steer] bucket-N: <decision> — <goal/ruling/data basis>`,
             then gh issue edit <N> --remove-label needs-decision --add-assignee @me so /yk-loop builds it;
             comment every sibling issue the ruling changes, saying what it changes for that issue
5. ESCALATE  ONE batched message to the user (under /yk-loop: its final stop report) listing only the bucket-3 items, each with its named trigger
             and a recommended default, so each answer is a quick confirm
6. REPORT    list every bucket-1/2 decision as "decided — open to veto"; a decision is never silent
```

If there are zero bucket-3 items, escalate nothing and say the queue is clear; /yk-loop carries on.

## Guardrails

- **Decide aggressively in buckets 1–2**; over-escalation is the failure this loop exists to fix. But do not
  launder a bucket-3 call as a decision: when genuinely unsure which option is safe for users' data, or when it
  touches the release path or another repo, it is bucket 3. A data-loss bug with a clear fix is bucket 2 (G1).
- **Every decision is recorded** on its issue (comment + basis) and reversible where possible; the user can veto
  any of them from the report.
- **A decision is not a merge.** The work it unblocks still goes through /pairit: tests by a partner, a
  distinct-session review, `safe_merge.sh`.
- The other fleet repos are **read-only** — measure there, never write.
