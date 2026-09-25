# Yurtle Kanban — Claude Code Instructions

Yurtle Kanban is a file-based kanban system using Yurtle (Turtle RDF in Markdown) with git as the database. It's the work-tracking CLI used across all Congruent Systems projects.

## Project Overview

- **Language:** Python 3.11+
- **Build:** `pip install -e ".[dev]"`
- **Tests:** `pytest`
- **CLI Entry:** `yurtle-kanban` (installed via pyproject.toml console_scripts)

## Development Practices

### Branch + PR Pattern (Required)

All implementation work goes through feature branches and pull requests:

1. Create a feature branch: `git checkout -b feat-short-description`
2. Do all implementation work on the branch — **never push directly to main**
3. Run tests: `pytest`
4. Push and create PR: `gh pr create`
5. Get review from another developer/agent before merging

After merge, clean up:
```bash
git branch -d feat-short-description
git push origin --delete feat-short-description
```

### Testing

Run `pytest` before committing. Tests must pass before creating a PR.

```bash
pytest              # All tests
pytest -v           # Verbose
pytest tests/test_board.py  # Specific module
```

### Code Quality

- Always use type hints
- Prefer editing existing files over creating new ones
- Don't create files unless necessary

### Versioning

Semantic versioning. Version locations must stay in sync:
- `pyproject.toml` → `version`
- `yurtle_kanban/__init__.py` → `__version__`

## Multi-Agent Coordination

Multiple Claude Code agents may work on this project. Each machine has its own `~/.claude/CLAUDE.md` with agent identity.

| Agent | GitHub | Platform |
|-------|--------|----------|
| **M5** | hankh95 | MacBook Pro M5 |
| **DGX** | hankh959 | DGX Spark |
| **Mini** | hankh1844 | Mac Mini M4 |

## Related Projects

- **nusy-product-team** — Primary consumer of yurtle-kanban
- **noesis-ship** — Uses yurtle-kanban for work tracking
- **carclaw** — Uses yurtle-kanban for work tracking (software theme, TASK-XXX)
- **noesis-ships-comm** — Uses yurtle-kanban for work tracking
- **rachael-lab** — Uses yurtle-kanban for work tracking
