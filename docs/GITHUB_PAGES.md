# Shared Project Kanban with GitHub

yurtle-kanban lets you run a **shared kanban board** for your project using only markdown files and GitHub. No external services, no databases - just files in your repo.

## Two Options

| Option | Best For | Visibility |
|--------|----------|------------|
| **In-Repo Markdown** | Private repos | Same as repo |
| **GitHub Pages** | Public repos | Public (or Enterprise for private) |

---

## Option 1: In-Repo Markdown (Recommended for Private Repos)

The board is auto-generated as a markdown file committed to your repo. View it directly in GitHub UI.

### Why Use This?

- **Private by default** - Board visibility matches repo visibility
- **No GitHub Pages setup** - Just add the workflow
- **Works on all plans** - No Enterprise required
- **Git history** - Every board update is a commit

### Setup

#### Step 1: Create the Kanban Config

Create `.kanban/config.yaml` in your project root:

```yaml
kanban:
  theme: software  # or 'nautical' for ship-themed naming

  paths:
    root: work/
    # Or scan multiple directories:
    # scan_paths:
    #   - work/features/
    #   - work/bugs/

  ignore:
    - "**/archive/**"
    - "**/templates/**"
```

#### Step 2: Add the GitHub Action

Create `.github/workflows/kanban-board.yml`:

```yaml
name: Update Kanban Board

on:
  push:
    branches: [main]
    paths:
      - 'work/**/*.md'
      - '.kanban/**'
  workflow_dispatch:

permissions:
  contents: write

jobs:
  update-board:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install yurtle-kanban
        run: pip install git+https://github.com/Congruentsys/yurtle-kanban.git

      - name: Generate board markdown
        run: |
          yurtle-kanban export --format markdown --output KANBAN-BOARD.md

      - name: Commit updated board
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add KANBAN-BOARD.md
          git diff --staged --quiet || git commit -m "chore: update kanban board [skip ci]"
          git push
```

#### Step 3: Create Your First Work Item

Create `work/FEAT-001.md`:

```markdown
---
id: FEAT-001
type: feature
title: "Add user authentication"
status: backlog
priority: high
---

# Add User Authentication

Implement OAuth2 login flow...
```

#### Step 4: Push and View

```bash
git add .
git commit -m "Add kanban board"
git push
```

The workflow generates `KANBAN-BOARD.md` in your repo root. View it in GitHub!

### Optional: Use a Subdirectory with Symlink

To keep the generated file with your kanban work items:

```yaml
# In your workflow:
- name: Generate board markdown
  run: |
    yurtle-kanban export --format markdown --output kanban-work/KANBAN-BOARD.md
```

Then create a symlink in repo root:

```bash
ln -s kanban-work/KANBAN-BOARD.md KANBAN-BOARD.md
git add KANBAN-BOARD.md kanban-work/KANBAN-BOARD.md
git commit -m "Add kanban board with symlink"
```

---

## Option 2: GitHub Pages (Public Repos or Enterprise)

Deploy an interactive HTML board to GitHub Pages.

**Note:** Private GitHub Pages requires GitHub Enterprise. On other plans, Pages are always public even for private repos.

### Setup

#### Step 1: Create the Kanban Config

Same as Option 1 - create `.kanban/config.yaml`.

#### Step 2: Add the GitHub Action

Create `.github/workflows/kanban-board.yml`:

```yaml
name: Update Kanban Board

on:
  push:
    branches: [main]
    paths:
      - 'work/**/*.md'
      - '.kanban/**'
  workflow_dispatch:

permissions:
  contents: read
  pages: write
  id-token: write

concurrency:
  group: "pages"
  cancel-in-progress: false

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install yurtle-kanban
        run: pip install git+https://github.com/Congruentsys/yurtle-kanban.git

      - name: Generate board
        run: |
          mkdir -p docs/board
          yurtle-kanban export --format html --output docs/board/index.html
          yurtle-kanban export --format json --output docs/board/board.json

      - uses: actions/configure-pages@v4

      - uses: actions/upload-pages-artifact@v3
        with:
          path: 'docs/board'

  deploy:
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    runs-on: ubuntu-latest
    needs: build
    steps:
      - uses: actions/deploy-pages@v4
```

#### Step 3: Enable GitHub Pages

1. Go to your repository on GitHub
2. Click **Settings** > **Pages**
3. Under "Build and deployment", set Source to **GitHub Actions**
4. Click **Save**

#### Step 4: Push and View

After the workflow runs, your board is live at:

```
https://<username>.github.io/<repo>/
```

---

## Work Item Format

Each work item is a markdown file with YAML frontmatter:

```markdown
---
id: FEAT-001              # Unique identifier
type: feature             # feature, bug, task, epic, etc.
title: "Short title"      # Displayed on card
status: in_progress       # backlog, ready, in_progress, review, done
priority: high            # critical, high, medium, low
assignee: username        # Optional
tags: [api, backend]      # Optional
created: 2026-01-13       # Optional
---

# Full Title

Description, acceptance criteria, notes...
```

### Valid Statuses

- `backlog` - Not started
- `ready` - Ready to work on
- `in_progress` - Currently being worked on
- `review` - In review/testing
- `done` - Complete

---

## Themes

### Software Theme (Default)

Item types: feature, bug, task, epic, issue, idea

### Nautical Theme

```yaml
kanban:
  theme: nautical
```

Item types: expedition, voyage, directive, hazard, signal

---

## Troubleshooting

### Board is empty

1. Check that work items have valid frontmatter (id, type, title, status)
2. Verify paths in `.kanban/config.yaml` match your directory structure
3. Run locally to debug: `yurtle-kanban list`

### Workflow fails

1. Check the Actions tab for error messages
2. Verify permissions are set correctly in workflow
3. Ensure yurtle-kanban installs successfully

### Changes not appearing

1. Push must be to `main` branch (or update workflow trigger)
2. File must be in a path that triggers the workflow
3. For in-repo markdown: check for `[skip ci]` preventing loops

---

## Local Development

Preview the board without pushing:

```bash
# Install
pip install git+https://github.com/Congruentsys/yurtle-kanban.git

# Terminal board
yurtle-kanban board

# Generate files
yurtle-kanban export --format markdown --output KANBAN-BOARD.md
yurtle-kanban export --format html --output board.html
```

---

## Integration with Claude Code

Claude Code can manage your kanban via the MCP server:

```json
// Add to claude_desktop_config.json
{
  "mcpServers": {
    "kanban": {
      "command": "yurtle-kanban-mcp"
    }
  }
}
```

Then Claude can:
- "What's in progress?"
- "Add a bug for the login issue"
- "Mark FEAT-042 as done"
