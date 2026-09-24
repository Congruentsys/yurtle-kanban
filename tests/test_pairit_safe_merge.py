"""pairit's merge step refuses unless every check is green and the head merges cleanly (#167).

On PR #165 the driver chained `gh pr checks --json state …` with `&&`. That command
exits 0 whatever the states are, so a PR merged with `test (3.10)=FAILURE` and two
CANCELLED checks, and main went red. The issue asks for
`.claude/skills/pairit/safe_merge.sh <PR>`, which must:

1. wait on `gh pr checks --watch`;
2. refuse unless EVERY check is SUCCESS (or SKIPPED), naming any that aren't;
3. refuse unless the head merges cleanly with origin/main;
4. remove the worktree, then run `gh pr merge --merge --delete-branch`.

Every test here puts a STUB `gh` first on PATH and uses a throwaway git repo with a
local bare origin — the real `gh` and the real remote are never touched. The stub
answers the way the real `gh` would and logs every call it receives, so a test can
assert "no `pr merge` call" rather than trusting an exit code (the #165 failure).

The stub's `--watch` always exits 0, so a script that trusts `--watch`'s exit status
instead of reading the states is caught.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PAIRIT_DIR = REPO_ROOT / ".claude" / "skills" / "pairit"
SCRIPT = PAIRIT_DIR / "safe_merge.sh"
SKILL_MD = PAIRIT_DIR / "SKILL.md"

PR = "4242"
BRANCH = "fix/4242-thing"

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


# --------------------------------------------------------------------------- stub gh

STUB_GH = r'''#!__PYTHON__
"""Stub `gh` for #167 tests. Logs every call as one JSON line; never touches a network."""
import json, os, shutil, subprocess, sys

args = sys.argv[1:]
log = os.environ["STUB_GH_LOG"]
checks = json.loads(os.environ.get("STUB_GH_CHECKS", "[]"))
worktree = os.environ.get("STUB_GH_WORKTREE", "")


def record(extra=None):
    entry = {"argv": args}
    if extra:
        entry.update(extra)
    with open(log, "a") as fh:
        fh.write(json.dumps(entry) + "\n")


def opt(name):
    for i, a in enumerate(args):
        if a == name and i + 1 < len(args):
            return args[i + 1]
        if a.startswith(name + "="):
            return a.split("=", 1)[1]
    return None


BUCKET = {
    "SUCCESS": "pass", "SKIPPED": "skipping", "NEUTRAL": "skipping",
    "FAILURE": "fail", "ERROR": "fail", "TIMED_OUT": "fail", "ACTION_REQUIRED": "fail",
    "STARTUP_FAILURE": "fail", "STALE": "fail",
    "CANCELLED": "cancel",
    "PENDING": "pending", "IN_PROGRESS": "pending", "QUEUED": "pending",
    "WAITING": "pending", "REQUESTED": "pending", "EXPECTED": "pending",
}


def emit(data):
    """Print `data` like gh does: filtered to --json fields, then through --jq (raw output)."""
    fields = opt("--json")
    if fields:
        want = [f for f in fields.split(",") if f]
        if isinstance(data, list):
            data = [{k: row.get(k) for k in want} for row in data]
        else:
            data = {k: data.get(k) for k in want}
    text = json.dumps(data)
    jq = opt("--jq") or opt("-q")
    tmpl = opt("--template") or opt("-t")
    if tmpl:
        print("stub gh: --template unsupported", file=sys.stderr)
        sys.exit(2)
    if jq:
        jq_bin = shutil.which("jq")
        if not jq_bin:
            print("stub gh: --jq needs jq on PATH", file=sys.stderr)
            sys.exit(2)
        out = subprocess.run([jq_bin, "-r", jq], input=text, capture_output=True, text=True)
        sys.stdout.write(out.stdout)
        sys.stderr.write(out.stderr)
        sys.exit(out.returncode)
    print(text)


def check_rows():
    rows = []
    for c in checks:
        state = c["state"]
        rows.append({
            "name": c["name"], "state": state, "bucket": BUCKET.get(state, "pending"),
            "workflow": "CI", "event": "pull_request", "description": "",
            "link": "https://example.invalid/run/1", "startedAt": "", "completedAt": "",
        })
    return rows


if args[:2] == ["auth", "status"]:
    record()
    sys.exit(0)

if len(args) >= 2 and args[0] == "pr" and args[1] == "checks":
    rows = check_rows()
    watching = "--watch" in args
    record({"watch": watching})
    if opt("--json"):
        emit(rows)
        sys.exit(0)
    for r in rows:
        print(f"{r['name']}\t{r['bucket']}\t1s\t{r['link']}\t")
    if watching:
        sys.exit(0)          # deliberately 0 whatever the states: a script must read them
    if not rows:
        print(f"no checks reported on the '{os.environ.get('STUB_GH_BRANCH', '')}' branch",
              file=sys.stderr)
        sys.exit(1)
    buckets = {r["bucket"] for r in rows}
    if buckets & {"fail", "cancel"}:
        sys.exit(1)
    if "pending" in buckets:
        sys.exit(8)
    sys.exit(0)

if len(args) >= 2 and args[0] == "pr" and args[1] == "view":
    record()
    conflict = os.environ.get("STUB_GH_CONFLICT") == "1"
    emit({
        "number": int(os.environ.get("STUB_GH_PR", "0")),
        "headRefName": os.environ.get("STUB_GH_BRANCH", ""),
        "headRefOid": os.environ.get("STUB_GH_HEAD_SHA", ""),
        "baseRefName": "main",
        "state": "OPEN",
        "isDraft": False,
        "mergeable": "CONFLICTING" if conflict else "MERGEABLE",
        "mergeStateStatus": "DIRTY" if conflict else "CLEAN",
        "url": "https://example.invalid/pull/" + os.environ.get("STUB_GH_PR", "0"),
        "title": "stub PR",
    })
    sys.exit(0)

if len(args) >= 2 and args[0] == "pr" and args[1] == "merge":
    record({"MERGED": True, "worktree_exists_at_merge": bool(worktree) and os.path.exists(worktree)})
    print("MERGED")
    sys.exit(0)

record({"unsupported": True})
print("stub gh: unsupported call: " + " ".join(args), file=sys.stderr)
sys.exit(1)
'''


# --------------------------------------------------------------------------- git fixture


def _git(cwd: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    )
    return out.stdout.strip()


class Sandbox:
    """A bare origin, a main checkout, and the PR branch checked out in its own worktree."""

    def __init__(self, tmp_path: Path, *, conflict: bool) -> None:
        self.tmp = tmp_path
        self.origin = tmp_path / "origin.git"
        self.checkout = tmp_path / "checkout"
        self.worktree = tmp_path / "wt-4242"
        self.stub_dir = tmp_path / "stubbin"
        self.log = tmp_path / "gh-calls.jsonl"
        self.conflict = conflict

        _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(self.origin))
        _git(tmp_path, "clone", "-q", str(self.origin), str(self.checkout))
        c = self.checkout
        _git(c, "config", "user.name", "Test")
        _git(c, "config", "user.email", "test@example.invalid")
        _git(c, "config", "commit.gpgsign", "false")
        _git(c, "checkout", "-q", "-b", "main")
        (c / "shared.txt").write_text("line one\nline two\nline three\n")
        _git(c, "add", "shared.txt")
        _git(c, "commit", "-q", "-m", "base")
        _git(c, "push", "-q", "-u", "origin", "main")

        # The PR branch, in its own worktree (as pairit step 0 makes it).
        _git(c, "worktree", "add", "-q", "-b", BRANCH, str(self.worktree), "origin/main")
        w = self.worktree
        if conflict:
            (w / "shared.txt").write_text("line one\nline two FROM THE PR\nline three\n")
        else:
            (w / "feature.txt").write_text("new feature\n")
        _git(w, "add", "-A")
        _git(w, "commit", "-q", "-m", "the PR's change")
        _git(w, "push", "-q", "-u", "origin", BRANCH)
        self.head_sha = _git(w, "rev-parse", "HEAD")

        # main moves on after the branch was cut.
        if conflict:
            (c / "shared.txt").write_text("line one\nline two FROM MAIN\nline three\n")
        else:
            (c / "other.txt").write_text("unrelated change on main\n")
        _git(c, "add", "-A")
        _git(c, "commit", "-q", "-m", "main moves on")
        _git(c, "push", "-q", "origin", "main")

        self.stub_dir.mkdir()
        stub = self.stub_dir / "gh"
        stub.write_text(STUB_GH.replace("__PYTHON__", sys.executable))
        stub.chmod(0o755)

    def run(self, checks: list[dict[str, str]]) -> subprocess.CompletedProcess[str]:
        assert SCRIPT.is_file(), f"{SCRIPT.relative_to(REPO_ROOT)} does not exist (#167)"
        env = dict(os.environ)
        env.update(
            PATH=f"{self.stub_dir}{os.pathsep}{env.get('PATH', '')}",
            STUB_GH_LOG=str(self.log),
            STUB_GH_CHECKS=json.dumps(checks),
            STUB_GH_PR=PR,
            STUB_GH_BRANCH=BRANCH,
            STUB_GH_HEAD_SHA=self.head_sha,
            STUB_GH_WORKTREE=str(self.worktree),
            STUB_GH_CONFLICT="1" if self.conflict else "0",
            GIT_TERMINAL_PROMPT="0",
            GH_PROMPT_DISABLED="1",
        )
        for k in ("GH_TOKEN", "GITHUB_TOKEN", "GH_HOST", "GH_REPO"):
            env.pop(k, None)
        return subprocess.run(
            ["bash", str(SCRIPT), PR],
            cwd=self.checkout,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )

    def calls(self) -> list[dict]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines() if line]

    def merge_calls(self) -> list[dict]:
        return [c for c in self.calls() if c["argv"][:2] == ["pr", "merge"]]

    def watched(self) -> bool:
        return any(c["argv"][:2] == ["pr", "checks"] and c.get("watch") for c in self.calls())


def _output(result: subprocess.CompletedProcess[str]) -> str:
    return result.stdout + result.stderr


GREEN = [
    {"name": "test (3.10)", "state": "SUCCESS"},
    {"name": "test (3.11)", "state": "SUCCESS"},
    {"name": "test (3.12)", "state": "SUCCESS"},
    {"name": "optional-docs", "state": "SKIPPED"},
]


def _with(name: str, state: str) -> list[dict[str, str]]:
    return [dict(c, state=state) if c["name"] == name else dict(c) for c in GREEN]


# --------------------------------------------------------------------------- tests


class TestPairitSafeMerge:
    """`safe_merge.sh <PR>` merges only green, cleanly-merging PRs (#167)."""

    def test_all_green_with_a_skipped_check_merges(self, tmp_path: Path) -> None:
        sb = Sandbox(tmp_path, conflict=False)
        result = sb.run(GREEN)

        assert result.returncode == 0, _output(result)
        assert sb.watched(), f"never waited on `gh pr checks --watch`: {sb.calls()}"
        merges = sb.merge_calls()
        assert len(merges) == 1, f"expected one `gh pr merge` call, got {sb.calls()}"
        argv = merges[0]["argv"]
        assert PR in argv, argv
        assert "--merge" in argv, argv
        assert "--delete-branch" in argv, argv

    def test_worktree_is_removed_before_the_merge(self, tmp_path: Path) -> None:
        """Issue step 4: remove the worktree, THEN merge (the branch is checked out there)."""
        sb = Sandbox(tmp_path, conflict=False)
        result = sb.run(GREEN)

        assert result.returncode == 0, _output(result)
        merges = sb.merge_calls()
        assert merges, f"no `gh pr merge` call: {sb.calls()}"
        assert merges[0]["worktree_exists_at_merge"] is False, (
            f"the PR branch's worktree {sb.worktree} still existed when `gh pr merge` ran"
        )

    @pytest.mark.parametrize(
        "state",
        ["FAILURE", "CANCELLED", "PENDING", "IN_PROGRESS"],
    )
    def test_a_non_green_check_refuses_and_names_it(self, tmp_path: Path, state: str) -> None:
        sb = Sandbox(tmp_path, conflict=False)
        result = sb.run(_with("test (3.10)", state))

        assert result.returncode != 0, f"{state} check was accepted:\n{_output(result)}"
        assert "test (3.10)" in _output(result), (
            f"refusal does not name the {state} check:\n{_output(result)}"
        )
        assert not sb.merge_calls(), f"`gh pr merge` was called despite {state}: {sb.calls()}"
        assert sb.worktree.exists(), "worktree removed although the merge was refused"

    def test_the_165_shape_failure_plus_cancelled_refuses(self, tmp_path: Path) -> None:
        """The exact PR #165 states: 3.10 FAILURE, 3.11/3.12 CANCELLED."""
        sb = Sandbox(tmp_path, conflict=False)
        checks = [
            {"name": "test (3.10)", "state": "FAILURE"},
            {"name": "test (3.11)", "state": "CANCELLED"},
            {"name": "test (3.12)", "state": "CANCELLED"},
        ]
        result = sb.run(checks)

        assert result.returncode != 0, _output(result)
        out = _output(result)
        for name in ("test (3.10)", "test (3.11)", "test (3.12)"):
            assert name in out, f"refusal does not name {name!r}:\n{out}"
        assert not sb.merge_calls(), sb.calls()

    def test_no_checks_reported_refuses(self, tmp_path: Path) -> None:
        """'Every check green' over zero checks is not green."""
        sb = Sandbox(tmp_path, conflict=False)
        result = sb.run([])

        assert result.returncode != 0, f"merged with no checks:\n{_output(result)}"
        assert not sb.merge_calls(), sb.calls()
        assert sb.worktree.exists()

    def test_conflict_with_origin_main_refuses(self, tmp_path: Path) -> None:
        sb = Sandbox(tmp_path, conflict=True)
        result = sb.run(GREEN)

        assert result.returncode != 0, f"conflicting PR accepted:\n{_output(result)}"
        assert re.search(r"conflict", _output(result), re.IGNORECASE), _output(result)
        assert not sb.merge_calls(), sb.calls()
        assert sb.worktree.exists(), "worktree removed although the merge was refused"

    def test_conflict_check_leaves_main_checkout_untouched(self, tmp_path: Path) -> None:
        """Probing mergeability must not leave the caller's checkout mid-merge or moved."""
        sb = Sandbox(tmp_path, conflict=True)
        before_head = _git(sb.checkout, "rev-parse", "HEAD")
        before_branch = _git(sb.checkout, "rev-parse", "--abbrev-ref", "HEAD")
        sb.run(GREEN)

        assert _git(sb.checkout, "rev-parse", "HEAD") == before_head
        assert _git(sb.checkout, "rev-parse", "--abbrev-ref", "HEAD") == before_branch
        assert _git(sb.checkout, "status", "--porcelain") == ""
        assert not (sb.checkout / ".git" / "MERGE_HEAD").exists()


class TestPairitSkillUsesSafeMerge:
    """pairit step 4 merges through safe_merge.sh, not a bare `gh pr merge` (#167)."""

    @staticmethod
    def _step4() -> str:
        text = SKILL_MD.read_text()
        start = text.index("**4. Merge**")
        end = text.find("\n**", start + len("**4. Merge**"))
        return text[start:] if end == -1 else text[start:end]

    def test_step4_calls_safe_merge(self) -> None:
        assert "safe_merge.sh" in self._step4(), "pairit step 4 does not use safe_merge.sh"

    def test_step4_has_no_bare_gh_pr_merge_command(self) -> None:
        bare = [
            line.strip()
            for line in self._step4().splitlines()
            if re.match(r"^\s*\$?\s*gh\s+pr\s+merge\b", line)
        ]
        assert not bare, f"pairit step 4 still runs a bare `gh pr merge`: {bare}"

    def test_overview_merge_line_points_at_safe_merge(self) -> None:
        text = SKILL_MD.read_text()
        line = next(l for l in text.splitlines() if re.match(r"^\s*4\.\s+MERGE\b", l))
        assert "gh pr merge`" not in line or "safe_merge" in line, (
            f"the step list still gives a bare `gh pr merge` as the merge: {line!r}"
        )
