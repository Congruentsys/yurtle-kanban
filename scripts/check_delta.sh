#!/usr/bin/env bash
# scripts/check_delta.sh [BASE] — a LOCAL fast check (#1248). Run from the repo root.
#
# Runs only the tests scripts/py_delta.py vouches for (routed by scripts/py-delta.conf) on
# $(git merge-base BASE HEAD)..HEAD, then ruff; anything py_delta cannot vouch for runs the full
# suite. BASE defaults to origin/main. GitHub CI still runs the full suite on every Python; this
# script does not change the merge gate.
#
# CHECK_DELTA_DRY_RUN=1 prints the decision line and exits 0 without running pytest or ruff.
set -uo pipefail

base="${1:-origin/main}"
py=".venv/bin/python"
[ -x "$py" ] || py="python3"
ruff=".venv/bin/ruff"
[ -x "$ruff" ] || ruff="ruff"

decision=""
files=()

if ! git rev-parse --git-dir >/dev/null 2>&1; then
    decision="check-delta: FULL (cannot-assess not a git checkout)"
elif [ -n "$(git status --porcelain)" ]; then
    # py_delta compares commits: uncommitted work cannot be assessed (#1248)
    decision="check-delta: FULL (cannot-assess dirty working tree)"
elif ! mb="$(git merge-base "$base" HEAD 2>/dev/null)" || [ -z "$mb" ]; then
    decision="check-delta: FULL (cannot-assess no merge base with $base)"
else
    out="$("$py" scripts/py_delta.py --config scripts/py-delta.conf "$mb" HEAD 2>/dev/null)"
    rc=$?
    last="$(printf '%s\n' "$out" | tail -n 1)"
    # Skip anything ONLY on rc 0 AND an exact `py-delta: subset` last line (#1248)
    if [ "$rc" -eq 0 ] && [[ "$last" =~ ^py-delta:\ subset(\ [^[:space:]]+)*$ ]]; then
        read -r -a files <<<"${last#py-delta: subset}"
        decision="check-delta: SUBSET${files[*]:+ ${files[*]}}"
    else
        decision="check-delta: FULL (${last:-py-delta gave no verdict (rc $rc)})"
    fi
fi

# Keep the decision a single printable line (#1248)
printf '%s\n' "$(printf '%s' "$decision" | tr -d '\000-\037\177')"

if [ "${CHECK_DELTA_DRY_RUN:-}" = "1" ]; then
    exit 0
fi

status=0
if [[ "$decision" == "check-delta: SUBSET"* ]]; then
    if [ "${#files[@]}" -gt 0 ]; then
        env -u PYTEST_ADDOPTS "$py" -m pytest -q "${files[@]}" || status=1
    fi
else
    "$py" -m pytest -q || status=1
fi
"$ruff" check src/ tests/ || status=1
exit "$status"
