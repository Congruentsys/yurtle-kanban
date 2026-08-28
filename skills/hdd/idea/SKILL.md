---
name: idea
description: Capture a research idea for HDD workflow (Phase 0)
disable-model-invocation: true
allowed-tools: Bash(yurtle-kanban *), Write, Read, Glob
argument-hint: "<research question or observation>"
---

# Capture Research Idea

Capture a raw research question or observation as the first step of HDD.

## Overview

Ideas are the starting point of Hypothesis-Driven Development. Capture the thought
(30 seconds) before it's lost. The idea can later become:
- A literature review (LIT-XXX)
- A hypothesis (H{paper}.{n})
- An experiment (EXPR-{paper})

## Steps

### 1. Create Idea via yurtle-kanban

```bash
# Create idea with atomic push (multi-agent safe)
yurtle-kanban create idea "$ARGUMENTS" --push
```

If not using yurtle-kanban, create manually:

### 2. Manual Creation

Create `research/ideas/IDEA-R-{N}.md`:

```markdown
---
id: IDEA-R-{N}
type: idea
title: "$ARGUMENTS"
status: draft
created: YYYY-MM-DD
source: "agent|human|observation|reading"
---

# IDEA-R-{N}: $ARGUMENTS

## The Question

$ARGUMENTS

## Why This Matters

[Quick note on why this is worth exploring]

## Initial Thoughts

- [Any immediate hypotheses or directions]

## Next Steps

- [ ] Literature review needed? `/literature IDEA-R-{N} "topic"`
- [ ] Ready to formalize? Create hypothesis
```

### 3. Commit

```bash
git add research/ideas/IDEA-R-{N}.md
git commit -m "idea(HDD): capture IDEA-R-{N}"
```

## Examples

**Good ideas (research questions):**
- "Would caching reduce p99 latency significantly?"
- "Can static analysis catch more bugs than tests alone?"
- "Does pair programming improve code quality measurably?"

**Not ideas (use expeditions/features instead):**
- "Fix the login bug"
- "Add dark mode"

## Routing

| Idea Type | Where It Goes |
|-----------|---------------|
| Research question | -> Literature -> Hypothesis -> Paper |
| Technical observation | -> May become research or expedition |
| Feature request | -> Use `/feature` or `/expedition` |

## Output

Confirm creation:
1. Show the idea ID (IDEA-R-{N})
2. Suggest `/literature IDEA-R-{N} "topic"` for literature review
