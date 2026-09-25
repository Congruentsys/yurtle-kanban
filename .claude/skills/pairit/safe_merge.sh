#!/bin/bash
# safe_merge.sh <PR> — pairit step 4: merge a PR only when it is really green (#167).
#
# On PR #165 the driver chained `gh pr checks … --json state` with `&&`; that
# command exits 0 whatever the states are, so the PR merged with a FAILURE and two
# CANCELLED checks and main went red. This script never trusts an exit code for
# the verdict: it reads every check's state and refuses unless ALL of them are
# SUCCESS or SKIPPED (no checks at all is not green either), unless origin/<branch>
# is the PR head, the head merges cleanly with origin/main, and the latest
# `reviewed-at-sha:` verdict is `approve` at that head (#186). Only then does it
# remove the PR's worktree (found by branch, wherever it lives) and merge, with
# --match-head-commit so GitHub refuses if the head moved in between.
#
# Usage (from the main checkout):  bash .claude/skills/pairit/safe_merge.sh <PR>
set -u
PR=${1:?usage: safe_merge.sh <PR>}

# The head (and the verdict comments) are read ONCE, before waiting on and reading
# the checks; every later check and the merge itself are tied to that head
# (--match-head-commit), so a commit pushed meanwhile can't be merged unchecked or
# unreviewed (#186)
pr=$(gh pr view "$PR" --json headRefName,headRefOid,comments) || {
  echo "NOT MERGING #$PR: could not read the PR"; exit 1; }
branch=$(printf '%s' "$pr" | jq -r '.headRefName // ""')
head=$(printf '%s' "$pr" | jq -r '.headRefOid // ""')

gh pr checks "$PR" --watch >/dev/null 2>&1   # wait only; its exit code decides nothing

checks=$(gh pr checks "$PR" --json name,state) || {
  echo "NOT MERGING #$PR: could not read its checks"; exit 1; }
n=$(printf '%s' "$checks" | jq 'length') || { echo "NOT MERGING #$PR: could not parse its checks"; exit 1; }
if [ "$n" = 0 ]; then
  echo "NOT MERGING #$PR: no checks reported (not green)"; exit 1
fi
# filtered inside jq, so a check name with a tab or newline can't garble it (#186)
bad=$(printf '%s' "$checks" | jq -r '.[] | select(.state != "SUCCESS" and .state != "SKIPPED")
  | "\(.name | gsub("[\t\n]"; " "))=\(.state)"') || {
  echo "NOT MERGING #$PR: could not parse its checks"; exit 1; }
if [ -n "$bad" ]; then
  echo "NOT MERGING #$PR: checks not green:"
  printf '%s\n' "$bad" | sed 's/^/  /'   # quoted: check names contain spaces
  exit 1
fi

git fetch -q --prune origin || { echo "NOT MERGING #$PR: git fetch failed"; exit 1; }
if [ -z "$branch" ] || ! at=$(git rev-parse --verify -q "refs/remotes/origin/$branch"); then
  echo "NOT MERGING #$PR: origin/$branch not found (deleted, or a fork's branch)"; exit 1
fi
if [ "$at" != "$head" ]; then
  echo "NOT MERGING #$PR: origin/$branch is at ${at:0:12}, the PR head is ${head:0:12}"; exit 1
fi
git merge-tree --write-tree origin/main "$head" >/dev/null 2>&1
rc=$?
case $rc in
  0) ;;
  1) echo "NOT MERGING #$PR: $branch conflicts with origin/main (rebase it first)"; exit 1 ;;
  *) echo "NOT MERGING #$PR: git merge-tree failed (exit $rc; needs git 2.38+)"; exit 1 ;;
esac

# pairit's rule: merge only with an `approve` verdict at the CURRENT head. The latest
# verdict comment decides; a later `changes` overrides an earlier approve.
# Only verdicts from the repo's own people count: on a public repo anyone can comment
verdict=$(printf '%s' "$pr" | jq -r '[.comments[]?
  | select(.authorAssociation == "OWNER" or .authorAssociation == "MEMBER"
      or .authorAssociation == "COLLABORATOR")
  | .body | select(startswith("reviewed-at-sha:"))]
  | last // "" | split("\n") | .[0:2] | map(sub("\r$"; "")) | join("\n")')
if [ "$verdict" != "reviewed-at-sha: $head"$'\n'"verdict: approve" ]; then
  latest=$(printf '%s' "$verdict" | tr '\n' ' ')
  echo "NOT MERGING #$PR: no approve verdict at $head (latest verdict: ${latest:-none})"
  exit 1
fi

# the local branch can't be deleted while a worktree has it checked out
wt=$(git worktree list --porcelain | awk -v b="refs/heads/$branch" '
  /^worktree / { path = substr($0, 10) } $0 == "branch " b { print path }')
if [ -n "$wt" ]; then
  # --force would discard uncommitted work with it, and a merge refused after this
  # point (the head moved) would leave it gone for nothing: refuse first (#208)
  dirty=""
  if [ -d "$wt" ]; then   # a registered worktree whose directory is gone holds nothing
    # explicit flags: a user's status.showUntrackedFiles=no or submodule settings
    # must not hide work that --force would delete (#229)
    dirty=$(git -C "$wt" status --porcelain --untracked-files=normal \
      --ignore-submodules=none 2>/dev/null) || {
      echo "NOT MERGING #$PR: could not read the status of worktree $wt"; exit 1; }
  fi
  if [ -n "$dirty" ]; then
    echo "NOT MERGING #$PR: worktree $wt has uncommitted changes (commit or remove them):"
    printf '%s\n' "$dirty" | sed 's/^/  /'
    exit 1
  fi
  git worktree remove --force "$wt" || { echo "NOT MERGING #$PR: could not remove worktree $wt"; exit 1; }
fi

gh pr merge "$PR" --merge --delete-branch --match-head-commit "$head" || { echo "merge of #$PR failed"; exit 1; }
echo "merged #$PR"
