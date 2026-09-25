# Theme Reference

yurtle-kanban supports multiple themes to match your team's vocabulary and workflow style.

## Theme Comparison

| Concept | software | nautical | spec |
|---------|----------|----------|------|
| **Large initiative** | Epic | Voyage | RFC |
| **Deliverable work** | Feature | Expedition | Spec |
| **Problem/defect** | Bug | Hazard | Issue |
| **Small work unit** | Task | Task | Task |
| **Research/idea** | Idea | Signal | Spike |
| **Maintenance** | Chore | Chore | ADR |

## Status Mapping

| Stage | software | nautical | spec |
|-------|----------|----------|------|
| **Not started** | backlog | harbor | draft |
| **Prioritized** | ready | provisioning | proposed |
| **Active work** | in_progress | underway | implementing |
| **Validation** | review | approaching | review |
| **Complete** | done | arrived | accepted |
| **Blocked** | blocked | blocked | blocked |

## Theme Details

### Software Theme (default)

Standard software development terminology. Best for teams familiar with agile/scrum.

| Type | Prefix | Description |
|------|--------|-------------|
| Feature | FEAT- | New functionality |
| Bug | BUG- | Defects to fix |
| Epic | EPIC- | Large multi-feature initiatives |
| Task | TASK- | Small work items |
| Idea | IDEA- | Proposals for consideration |
| Chore | CHORE- | Maintenance and cleanup |

### Nautical Theme

Maritime metaphor for project work. Used by NuSy and teams who prefer the voyage metaphor.

| Type | Prefix | Description |
|------|--------|-------------|
| Voyage | VOY- | Strategic multi-month goals |
| Expedition | EXP- | Time-boxed deliverable work |
| Hazard | HAZ- | Problems requiring resolution |
| Signal | SIG- | Observations and opportunities |
| Chore | CHORE- | Maintenance and cleanup |

**Why Nautical?**
- Voyages have clear destinations (goals)
- Expeditions have defined scope and timeframes
- Hazards are actively navigated around
- The ship metaphor encourages thinking about resources, crew, and coordination

### Spec Theme

Specification-driven development. Best for teams using RFCs, ADRs, and formal specs.

| Type | Prefix | Description |
|------|--------|-------------|
| RFC | RFC- | Request for Comments - proposals |
| Spec | SPEC- | Specifications for implementation |
| Issue | ISSUE- | Problems or bugs |
| Task | TASK- | Implementation work |
| Spike | SPIKE- | Research and investigation |
| ADR | ADR- | Architecture Decision Records |

**Why Spec-Driven?**
- Clear separation between design (RFC/Spec) and implementation (Task)
- ADRs document why decisions were made
- Spikes explicitly allocate time for research
- RFCs encourage discussion before commitment

## Choosing a Theme

| If your team... | Use |
|-----------------|-----|
| Uses agile/scrum terminology | `software` |
| Prefers metaphorical thinking | `nautical` |
| Does formal design-first development | `spec` |
| Has existing vocabulary | Create a custom theme |

## Custom Themes

Create your own theme in `.kanban/themes/my-theme.yaml`:

```yaml
name: My Theme
description: Custom theme for my team

item_types:
  story:
    id_prefix: STORY
    name: User Story
    description: A user-facing feature

  defect:
    id_prefix: DEF
    name: Defect
    description: Something broken

columns:
  backlog:
    name: Backlog
    order: 1
  doing:
    name: Doing
    order: 2
    wip_limit: 3
  done:
    name: Done
    order: 3
```

`wip_limit` is a whole number: `0` (or leaving it out) means no limit, and a negative
value is ignored with a warning. A theme with no `columns` gets the default columns
(Backlog, Ready 5, In Progress 3, Review 2, Done) on single and multi-board setups alike.
