#!/bin/bash
# safe_merge.sh <PR> — pairit step 4: merge a PR only when it is really green (#167).
#
# On PR #165 the driver chained `gh pr checks … --json state` with `&&`; that
# command exits 0 whatever the states are, so the PR merged with a FAILURE and two
# CANCELLED checks and main went red. This script never trusts an exit code for
# the verdict: it reads every check's state and refuses unless ALL of them are
# SUCCESS or SKIPPED (no checks at all is not green either), and unless the head
# merges cleanly with origin/main. Only then does it remove the PR's worktree
# (found by branch, wherever it lives) and merge.
#
# Usage (from the main checkout):  bash .claude/skills/pairit/safe_merge.sh <PR>
set -u
PR=${1:?usage: safe_merge.sh <PR>}

gh pr checks "$PR" --watch >/dev/null 2>&1   # wait only; its exit code decides nothing

states=$(gh pr checks "$PR" --json name,state --jq '.[] | "\(.state)\t\(.name)"') || {
  echo "NOT MERGING #$PR: could not read its checks"; exit 1; }
if [ -z "$states" ]; then
  echo "NOT MERGING #$PR: no checks reported (not green)"; exit 1
fi
bad=$(printf '%s\n' "$states" | awk -F'\t' '$1 != "SUCCESS" && $1 != "SKIPPED" { print $2 "=" $1 }')
if [ -n "$bad" ]; then
  echo "NOT MERGING #$PR: checks not green:"
  printf '%s\n' "$bad" | sed 's/^/  /'   # quoted: check names contain spaces
  exit 1
fi

branch=$(gh pr view "$PR" --json headRefName --jq .headRefName) || {
  echo "NOT MERGING #$PR: could not read its branch"; exit 1; }
git fetch -q origin || { echo "NOT MERGING #$PR: git fetch failed"; exit 1; }
if ! git merge-tree --write-tree origin/main "origin/$branch" >/dev/null 2>&1; then
  echo "NOT MERGING #$PR: $branch conflicts with origin/main (rebase it first)"; exit 1
fi

# the local branch can't be deleted while a worktree has it checked out
wt=$(git worktree list --porcelain | awk -v b="refs/heads/$branch" '
  /^worktree / { path = substr($0, 10) } $0 == "branch " b { print path }')
if [ -n "$wt" ]; then
  git worktree remove --force "$wt" || { echo "NOT MERGING #$PR: could not remove worktree $wt"; exit 1; }
fi

gh pr merge "$PR" --merge --delete-branch || { echo "merge of #$PR failed"; exit 1; }
echo "merged #$PR"
