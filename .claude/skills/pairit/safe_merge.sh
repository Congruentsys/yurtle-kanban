#!/bin/bash
# safe_merge.sh <PR> — pairit step 4: merge a PR only when it is really green (#167).
#
# On PR #165 the driver chained `gh pr checks … --json state` with `&&`; that
# command exits 0 whatever the states are, so the PR merged with a FAILURE and two
# CANCELLED checks and main went red. This script never trusts an exit code for
# the verdict: it reads every check's state and refuses unless ALL of them are
# SUCCESS or SKIPPED (no checks at all is not green either), unless origin/<branch>
# is the PR head, the head merges cleanly with origin/main, and the latest
# `reviewed-at-sha:` verdict is `approve` at that head (#186), or the head is the
# fixed tip of one review round (#987). Only then does it
# remove the PR's worktree (found by branch, wherever it lives) and merge, with
# --match-head-commit so GitHub refuses if the head moved in between.
#
# The external-PR process (#1195, .claude/skills/external-pr/SKILL.md) adds two refusals:
# a `proposed-reject` PR (only the Captain closes it), and an EXTERNAL PR that is ESCALATED
# without the Captain's `captain-approved` label. "External" and "escalated" have ONE
# definition, in yk_next.py: the gate asks `yk_next.py --escalation <PR>` and keeps no copy.
# A fork PR's head is read from refs/pull/<PR>/head, and its branch is never deleted.
#
# Usage (from the main checkout):  bash .claude/skills/pairit/safe_merge.sh <PR>
set -u
PR=${1:?usage: safe_merge.sh <PR>}

# The head (and the verdict comments) are read ONCE, before waiting on and reading
# the checks; every later check and the merge itself are tied to that head
# (--match-head-commit), so a commit pushed meanwhile can't be merged unchecked or
# unreviewed (#186)
pr=$(gh pr view "$PR" --json headRefName,headRefOid,comments,labels,isCrossRepository) || {
  echo "NOT MERGING #$PR: could not read the PR"; exit 1; }
branch=$(printf '%s' "$pr" | jq -r '.headRefName // ""')
head=$(printf '%s' "$pr" | jq -r '.headRefOid // ""')

# #1195. The fleet never sets `captain-approved`; only the Captain does.
labels=$(printf '%s' "$pr" | jq -r '[.labels[]?.name] | join(",")')
has_label() { case ",$labels," in *",$1,"*) return 0 ;; esac; return 1; }
if has_label proposed-reject; then
  echo "NOT MERGING #$PR: it is labelled proposed-reject (the Captain closes it)"; exit 1
fi
fork=$(printf '%s' "$pr" | jq -r '.isCrossRepository // false')
# EXTERNAL and ESCALATED as yk_next.py defines them (files from REST, renames included). The
# association is read for EVERY PR (r1 N4): a bot or App pushes branches here as a non-member.
# The PR JSON read above goes in, so the verdict is about the head pinned here.
yk_next="$(dirname "$0")/../yk-next/yk_next.py"
esc=$(printf '%s' "$pr" | python3 "$yk_next" --escalation "$PR" --pr-json -) || {
  echo "NOT MERGING #$PR: could not read its escalation (yk_next.py --escalation)"; exit 1; }
external=$(printf '%s' "$esc" | jq -r '.external') || {
  echo "NOT MERGING #$PR: could not parse its escalation"; exit 1; }
why=$(printf '%s' "$esc" | jq -r '.why | join("; ")')
# the Captain's yes: `captain-approved`, added BY the Captain AFTER the approve verdict at
# this head (its REST labeled event), never just the label's presence (r1 B1)
approved=$(printf '%s' "$esc" | jq -r '.captain_approved')
captain=$(printf '%s' "$esc" | jq -r '.captain')
if [ "$external" = true ] && [ -n "$why" ] && [ "$approved" != true ]; then
  echo "NOT MERGING #$PR: external PR escalated to the Captain ($why) and not"\
    "captain-approved at this head ($captain; only the Captain adds it)"
  exit 1
fi

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
if [ "$fork" = true ]; then
  # a fork's branch is not on origin; GitHub keeps the PR's head at refs/pull/<PR>/head
  ref="refs/pull/$PR/head"
  if ! git fetch -q origin "+$ref:$ref" || ! at=$(git rev-parse --verify -q "$ref"); then
    echo "NOT MERGING #$PR: could not fetch $ref"; exit 1
  fi
  if [ "$at" != "$head" ]; then
    echo "NOT MERGING #$PR: $ref is at ${at:0:12}, the PR head is ${head:0:12}"; exit 1
  fi
elif [ -z "$branch" ] || ! at=$(git rev-parse --verify -q "refs/remotes/origin/$branch"); then
  echo "NOT MERGING #$PR: origin/$branch not found (deleted, or a fork's branch)"; exit 1
fi
if [ "$fork" != true ] && [ "$at" != "$head" ]; then
  echo "NOT MERGING #$PR: origin/$branch is at ${at:0:12}, the PR head is ${head:0:12}"; exit 1
fi
git merge-tree --write-tree origin/main "$head" >/dev/null 2>&1
rc=$?
case $rc in
  0) ;;
  1) echo "NOT MERGING #$PR: $branch conflicts with origin/main (rebase it first)"; exit 1 ;;
  *) echo "NOT MERGING #$PR: git merge-tree failed (exit $rc; needs git 2.38+)"; exit 1 ;;
esac

# pairit's rule: merge with an `approve` verdict at the CURRENT head, or, after ONE
# review round, with the driver's fixes comment at the head (#987): its first lines are
# `fixes-at-sha: <head>` / `for-review-at: <R>`, a verdict at R came before it, and R is
# an ancestor of the head. The latest verdict or fixes comment decides; a later
# `changes` overrides. Only the repo's own people count: on a public repo anyone can comment.
# An EDITED decisive comment (includesCreatedEdit, #1195 r3) still decides but is no verdict:
# editing keeps createdAt, so it never approves, nor names a reviewed sha (yk_next agrees, #991)
decisive=$(printf '%s' "$pr" | jq -r '[.comments[]?
  | select(.authorAssociation == "OWNER" or .authorAssociation == "MEMBER"
      or .authorAssociation == "COLLABORATOR")
  | select(.body | startswith("reviewed-at-sha:") or startswith("fixes-at-sha:"))
  | if .includesCreatedEdit then "(edited: no verdict)"
    else .body | split("\n") | .[0:2] | map(sub("\r$"; "")) | join("\n") end]')
verdict=$(printf '%s' "$decisive" | jq -r 'last // ""')
ok=""
if [ "$verdict" = "reviewed-at-sha: $head"$'\n'"verdict: approve" ]; then
  ok=approve
elif [ "${verdict%%$'\n'*}" = "fixes-at-sha: $head" ]; then
  # R: the full 40-hex sha, with a verdict at exactly R before it, and a PROPER ancestor
  # of the head: `changes` at the head itself is never "fixed" by a comment alone
  r=$(printf '%s' "${verdict#*$'\n'}" | sed -n 's/^for-review-at: *\([0-9a-f]\{40\}\) *$/\1/p')
  if [ -n "$r" ] && [ "$r" != "$head" ] && printf '%s' "$decisive" | jq -e --arg r "$r" '
      map(select(split("\n")[0] == "reviewed-at-sha: \($r)")) | length > 0' >/dev/null &&
    git merge-base --is-ancestor "$r" "$head" 2>/dev/null; then
    ok=fixed
  fi
fi
if [ -z "$ok" ]; then
  latest=$(printf '%s' "$verdict" | tr '\n' ' ')
  echo "NOT MERGING #$PR: no approve verdict at $head (latest verdict: ${latest:-none})"
  exit 1
fi

if [ "$fork" = true ]; then
  # the branch name is the fork's own: no worktree here is the PR's, and --delete-branch
  # would delete a same-named LOCAL branch (a fork's `main`, say)
  gh pr merge "$PR" --merge --match-head-commit "$head" || { echo "merge of #$PR failed"; exit 1; }
  echo "merged #$PR"
  exit 0
fi

# the local branch can't be deleted while a worktree has it checked out
# a worktree in the middle of a rebase of the branch has a detached HEAD, so the
# branch lookup below misses it; its rebase state still names the branch (#247)
while IFS= read -r path; do
  gd=$(git -C "$path" rev-parse --git-dir 2>/dev/null) || continue
  case $gd in /*) ;; *) gd="$path/$gd" ;; esac
  for state in rebase-merge rebase-apply; do
    if [ "$(cat "$gd/$state/head-name" 2>/dev/null)" = "refs/heads/$branch" ]; then
      echo "NOT MERGING #$PR: worktree $path is in the middle of a rebase of $branch"\
        "(finish or abort it first)"
      exit 1
    fi
  done
done < <(git worktree list --porcelain | sed -n 's/^worktree //p')

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
  # skip-worktree / assume-unchanged files hide their edits from status (#247)
  hidden=""
  if [ -d "$wt" ]; then
    hidden=$(git -C "$wt" ls-files -v 2>/dev/null | awk '
      /^s / { print "skip-worktree + assume-unchanged: " substr($0, 3); next }
      /^S / { print "skip-worktree: " substr($0, 3); next }
      /^[a-z] / { print "assume-unchanged: " substr($0, 3) }')
  fi
  if [ -n "$hidden" ]; then
    # name every cause that is present (#265, #276): a real sparse checkout
    # (config on AND patterns set) explains skip-worktree entries; a hand-set flag,
    # or assume-unchanged, has to be cleared file by file
    sparse=""
    # patterns set: `sparse-checkout list` prints them, but a cone that keeps only
    # root files prints nothing while its file still holds `/*` and `!/*/` (#288)
    sparse_file=$(git -C "$wt" rev-parse --git-path info/sparse-checkout 2>/dev/null)
    case $sparse_file in /*) ;; ?*) sparse_file="$wt/$sparse_file" ;; esac
    if [ "$(git -C "$wt" config --bool core.sparseCheckout 2>/dev/null)" = true ] &&
      { [ -n "$(git -C "$wt" sparse-checkout list 2>/dev/null)" ] ||
        [ -s "$sparse_file" ]; }; then
      sparse=yes
    fi
    echo "NOT MERGING #$PR: worktree $wt has files whose edits git hides:"
    printf '%s\n' "$hidden" | sed 's/^/  /'
    if [ -n "$sparse" ] && printf '%s\n' "$hidden" | grep -q '^skip-worktree'; then
      echo "  - it is a sparse checkout; its files outside the cone are skip-worktree"\
        "(run git sparse-checkout disable in it)"
    fi
    # anchored: a file whose path contains "assume-unchanged" isn't one (#288)
    if printf '%s\n' "$hidden" | grep -qE '^(assume-unchanged|skip-worktree \+ assume-unchanged):' ||
      { [ -z "$sparse" ] && printf '%s\n' "$hidden" | grep -q '^skip-worktree'; }; then
      echo "  - hand-set flags (clear the flag): git update-index --no-skip-worktree"\
        "/ --no-assume-unchanged on the files above"
    fi
    exit 1
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
