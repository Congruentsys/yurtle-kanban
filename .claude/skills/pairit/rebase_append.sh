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
# This script resolves ONLY those safe shapes, from git's index stages, and only
# when all three stages (base, upstream, commit) exist:
#   - the commit's side only APPENDED to the base  →  upstream + the appended text;
#   - CHANGELOG.md where the commit only ADDED lines  →  upstream's text + those
#     lines, above the first release heading after `## [Unreleased]`.
# Anything else — modify/delete, add/add, edits, binary files — stops with a
# non-zero exit and the rebase left stopped for a human. It also refuses to run on
# main, on a dirty worktree, or without a fresh fetch, and it only reports success
# when the branch really sits on origin/main.
#
# Usage (from the feature checkout):  bash .claude/skills/pairit/rebase_append.sh
set -u
GITC=(git -c core.commentChar=';')

in_rebase() {
  [ -d "$(git rev-parse --git-path rebase-merge)" ] || [ -d "$(git rev-parse --git-path rebase-apply)" ]
}

branch=$(git branch --show-current)
if [ -z "$branch" ] || [ "$branch" = main ]; then
  echo "refusing: run on a feature branch (current: ${branch:-detached HEAD})"; exit 1
fi
if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "refusing: the worktree has uncommitted changes"; exit 1
fi
git fetch -q origin main || { echo "refusing: git fetch origin main failed"; exit 1; }

"${GITC[@]}" rebase origin/main >/dev/null 2>&1

for _ in $(seq 1 50); do
  in_rebase || break
  python3 - <<'PY' || { echo "UNRESOLVABLE conflict: rebase left stopped for a human"; exit 1; }
import re
import subprocess
import sys

raw = subprocess.run(["git", "ls-files", "-u", "-z"], capture_output=True, check=True).stdout
stages: dict[bytes, set[str]] = {}
for entry in raw.split(b"\0"):
    if not entry:
        continue
    meta, path = entry.split(b"\t", 1)
    stages.setdefault(path, set()).add(meta.split()[2].decode())


def show(stage: str, path: bytes) -> str:
    out = subprocess.run(["git", "show", f":{stage}:".encode() + path], capture_output=True, check=True)
    return out.stdout.decode("utf-8")  # a binary file raises → unresolvable


resolved: dict[bytes, str] = {}
for path, have in stages.items():
    if have != {"1", "2", "3"}:  # modify/delete, add/add: never guess
        sys.exit(1)
    base, up, mine = show("1", path), show("2", path), show("3", path)
    name = path.decode("utf-8")
    if name == "CHANGELOG.md" or name.endswith("/CHANGELOG.md"):
        base_lines = base.splitlines(keepends=True)
        mine_lines = mine.splitlines(keepends=True)
        if any(line not in mine_lines for line in base_lines):
            sys.exit(1)  # the commit edited or removed a line, not a pure addition
        base_set = set(base_lines)
        added = [line for line in mine_lines if line not in base_set]
        head = up.find("## [Unreleased]")
        nxt = re.compile(r"^## \[", re.MULTILINE).search(up, head + 1) if head != -1 else None
        if nxt is None:
            sys.exit(1)
        resolved[path] = (
            up[: nxt.start()].rstrip("\n") + "\n" + "".join(added).rstrip("\n") + "\n\n" + up[nxt.start():]
        )
    elif mine.startswith(base):
        resolved[path] = up.rstrip("\n") + "\n\n\n" + mine[len(base):].lstrip("\n")
    else:
        sys.exit(1)

# only write once every conflicted file has a safe resolution
for path, text in resolved.items():
    with open(path, "wb") as fh:
        fh.write(text.encode("utf-8"))
    subprocess.run(["git", "add", "--", path], check=True)
PY
  GIT_EDITOR=true "${GITC[@]}" rebase --continue >/dev/null 2>&1
done

if in_rebase; then
  echo "rebase stopped: resolve by hand, then: git -c core.commentChar=';' rebase --continue"
  exit 1
fi
if ! git merge-base --is-ancestor origin/main HEAD; then
  echo "not rebased: $branch does not contain origin/main (did git rebase refuse to start?)"; exit 1
fi
git status | head -1
