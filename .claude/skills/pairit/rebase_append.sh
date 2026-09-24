#!/bin/bash
# rebase_append.sh — rebase the current branch onto origin/main for pairit (#143).
#
# Parallel PRs append at the same two places: a new test class at the end of a
# tests/*.py file, and a new entry under `## [Unreleased]` in CHANGELOG.md. Plain
# `git rebase` mis-handles both:
#   - git aligns lines two appended classes share (`@staticmethod`, `def _run`),
#     splits the conflict mid-class, and "keep both sides" yields broken Python;
#   - on a conflicted pick git re-opens the message with `#` as comment char and
#     drops a `#<N>: tests (red)` subject.
# This script resolves ONLY those safe shapes, from git's index stages:
#   - the commit's side only APPENDED to the merge base  →  upstream + the
#     commit's appended text;
#   - CHANGELOG.md  →  upstream's text + the commit's new lines, inserted above
#     the first release heading after `## [Unreleased]`.
# Anything else stops with a non-zero exit, the rebase left stopped for a human.
#
# Usage (from the feature checkout):  bash .claude/skills/pairit/rebase_append.sh
set -u
GITC=(git -c core.commentChar=';')

git fetch -q origin main
"${GITC[@]}" rebase origin/main >/dev/null 2>&1

for _ in $(seq 1 50); do
  git status | grep -q "rebase in progress" || break
  for f in $(git diff --name-only --diff-filter=U); do
    python3 - "$f" <<'PY' || { echo "UNRESOLVABLE: $f (rebase left stopped)"; exit 1; }
import pathlib
import re
import subprocess
import sys

f = sys.argv[1]


def show(stage: int) -> str:
    return subprocess.run(
        ["git", "show", f":{stage}:{f}"], capture_output=True, text=True
    ).stdout


base, up, mine = show(1), show(2), show(3)

if f == "CHANGELOG.md" or f.endswith("/CHANGELOG.md"):
    base_lines = set(base.splitlines(keepends=True))
    added = [line for line in mine.splitlines(keepends=True) if line not in base_lines]
    head = up.find("## [Unreleased]")
    nxt = re.compile(r"^## \[", re.MULTILINE).search(up, head + 1) if head != -1 else None
    if nxt is None:
        sys.exit(1)
    out = up[: nxt.start()].rstrip("\n") + "\n" + "".join(added).rstrip("\n") + "\n\n" + up[nxt.start() :]
elif mine.startswith(base):
    # the commit only appended; upstream may have changed anything
    out = up.rstrip("\n") + "\n\n\n" + mine[len(base):].lstrip("\n")
else:
    sys.exit(1)

pathlib.Path(f).write_text(out)
PY
    git add "$f"
  done
  GIT_EDITOR=true "${GITC[@]}" rebase --continue >/dev/null 2>&1
done

if git status | grep -q "rebase in progress"; then
  echo "rebase stopped: resolve by hand, then: git -c core.commentChar=';' rebase --continue"
  exit 1
fi
git status | head -1
