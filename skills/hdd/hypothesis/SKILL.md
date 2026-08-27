---
name: hypothesis
description: Create a formal hypothesis for HDD workflow
disable-model-invocation: true
allowed-tools: Bash, Write, Read, Grep, Glob
argument-hint: "\"hypothesis statement\" [--paper <n>] [--target \"threshold\"]"
---

# Create Hypothesis

Create a formal, testable hypothesis as part of HDD.

## Overview

A hypothesis is a testable claim with a measurable target. It must be:
- **Falsifiable**: Can be proven wrong
- **Measurable**: Has a specific metric and threshold

**A paper is OPTIONAL.** Do not ask the user to create one, and do not invent a
paper number so the command will run. State the belief first; a paper is
something you write later, if the work ever earns one.

## Hypothesis Format

```
H-{nnn}:          [Statement of testable claim] - Target: [threshold]   # unparented
H{paper}.{n}:     [Statement of testable claim] - Target: [threshold]   # scoped to a paper
```

**Examples:**
- `H-001: Most support tickets come from one feature` - Target: >=60%
- `H42.1: Redis caching reduces p99 latency by >=50%` - Target: >=50%

## Steps

### 1. Does this belong to a paper?

Usually **no** — that is the default and needs no flag. Pass `--paper <n>` only
if the user has already named an existing paper this hypothesis belongs to.

### 2. Create via yurtle-kanban

The id is allocated for you. Do not construct one by hand.

```bash
# The ordinary case — no paper
yurtle-kanban hypothesis create "$STATEMENT" --target "$TARGET"

# Scoped to a paper the user already has
yurtle-kanban hypothesis create "$STATEMENT" --paper "$PAPER_NUMBER" --target "$TARGET"
```

If you must write the file by hand, `paper:` is omitted entirely when there is
no paper — never `PAPER-XXX`, and never a number you chose:

```markdown
---
id: H-001
type: hypothesis
statement: "$STATEMENT"
target: "$TARGET"
status: draft
created: YYYY-MM-DD
---

# H-001: $STATEMENT

## Hypothesis

**Statement:** $STATEMENT

**Target:** $TARGET

**Null Hypothesis:** [What we'd conclude if target NOT met]

## Rationale

[Why we expect this to be true]

## Measurement

| Measure | ID | Unit | How Collected |
|---------|-----|------|---------------|
| [Primary] | M-XXX | [unit] | [method] |

## Related

- Literature: LIT-XXX
- Experiment: TBD (created after hypothesis)
```

### 3. Commit

```bash
git add research/hypotheses/H-001-*.md
git commit -m "hyp(HDD): create H-001"
```

## Hypothesis States

| State | Meaning |
|-------|---------|
| `draft` | Not yet tested |
| `active` | Experiment in progress |
| `complete` | Validated or Refuted |
| `abandoned` | Superseded |

## Good vs Bad Hypotheses

**Good (testable with clear threshold):**
- "Caching reduces p99 latency to <=100ms"
- "Batch processing improves throughput by >=40%"

**Bad (vague, not measurable):**
- "The system will be faster"
- "Users will prefer our approach"
- "It will work better"

## Output

Confirm creation:
1. Show the hypothesis ID the tool allocated (`H-001`, or `H{paper}.{n}` if scoped)
2. Suggest next step: `/experiment <that-id> "design"`
