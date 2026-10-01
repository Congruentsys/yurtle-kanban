# Agent Quick Reference Card

Single-page reference for Claude Code agents using yurtle-kanban.

## Session Start

```bash
/sync                          # Check handoffs, reviews, blocked, recommend action
```

## Check Status

```bash
/status                        # Show kanban board
yurtle-kanban list --status review    # Items needing review
yurtle-kanban list --status blocked   # Blocked items
yurtle-kanban stats                   # Board statistics
```

## Pick Up Work

```bash
/work EXP-XXX                  # Start work on expedition
# OR manually (a race-free claim; name yourself with --agent or YURTLE_AGENT):
yurtle-kanban claim EXP-XXX --agent <your-agent-name>
git fetch origin && git checkout -b expedition/exp-XXX-description origin/main
```

## Create New Work

```bash
/expedition "Title here"       # Allocate ID, create file, push
# OR manually:
yurtle-kanban next-id EXP --json      # Get next ID atomically
```

## Complete Work

```bash
/done                          # Tests, commit, PR, update kanban
# OR manually:
git add -A && git commit -m "feat(exp-XXX): Description"
git push origin HEAD
gh pr create --title "feat(exp-XXX): Title" --body "..."
yurtle-kanban move EXP-XXX review --agent <your-agent-name>   # the holder moves it
```

## Review Work

```bash
/review EXP-XXX                # Check tests, docs, approve/request changes
```

## Hand Off Work

```bash
/handoff EXP-XXX agent-b       # Create handoff note, push
```

## Mark Blocked

```bash
/blocked EXP-XXX "Reason here" --unblock-by agent-a
```

## Create Release

```bash
/release patch             # Bug fix: 1.1.0 → 1.1.1
/release minor             # New feature: 1.1.0 → 1.2.0
/release major             # Breaking change: 1.1.0 → 2.0.0
```

`/release` is for an internal repo. A public (FOSS) repo uses `/release-foss` with the
same arguments: it adds the public GitHub release, the registry publish and contributor
credit.

```bash
/release-foss patch        # Public repo: release + GitHub release + registry + credit
```

## Agent Tags

| Tag | Meaning |
|-----|---------|
| `agent-a`, `agent-b`, `agent-c`, `agent-d` | Assigned to specific agent |
| `dgx-required` | Needs GPU/DGX machine |
| `mac-compatible` | Can run on Mac |
| `needs-coordination` | Multi-agent work |
| `any-agent` | Anyone can pick up |

## Agent Capabilities

| Agent | Machine | Focus |
|-------|---------|-------|
| agent-a | DGX | GPU training, heavy compute |
| agent-b | Mac | Architecture, tooling, docs |
| agent-c | DGX | GPU training, heavy compute |
| agent-d | Mac | Architecture, tooling, docs |

## Status Flow

```
ready -> in_progress -> review -> done
              |
              v
           blocked
```

## Branch Naming

```
expedition/exp-XXX-short-description
feature/name-here
chore/name-here
```

Expedition branches are **never deleted** (permanent memory).
