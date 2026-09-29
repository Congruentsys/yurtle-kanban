"""Issue #582: emergency stop, ``control halt|resume|status``.

The spec is the revised issue body (Expected 1-4, Acceptance 1-10). It was reshaped
per the adversarial review into ONE mode, ``halt``, with the old "quiesce"
semantics: new work stops, holders may finish. There is no ``quiesce``.

- CLI ``yurtle-kanban control halt (--reason TEXT | --reason-file PATH|-) [--agent A]``,
  ``control resume [--reason ... | --reason-file ...] [--agent A]`` and
  ``control status [--json]``. The actor comes from ``resolve_actor(--agent)`` with
  the git fallback allowed. ``--reason`` is required for halt, optional for resume.
- State: one repo-wide ``.kanban/control.yaml`` holding ``mode`` (``halt`` or
  ``running``), ``reason``, ``by`` and ``at``. It is written through #574's
  ``sync_and_push`` onto origin's default branch, with the same outcomes and exit
  codes. Any outcome other than won/local exits non-zero and prints ``the halt is
  NOT in effect for other agents``. With no remote it makes a local commit and
  prints a note.
- Reading involves no new fetches. The state comes from the last-fetched
  ``origin/<default>:.kanban/control.yaml``; ``claim`` reads the tree it has just
  fetched. With no remote it comes from the working-tree file. A missing file means
  ``running``. Broken YAML or an unknown ``mode`` counts as HALT (fail closed), and
  the parse error is shown; ``validate`` reports it. If the last fetch is more than
  10 minutes old, commands that act on the state print its age.
- Effects while halted, enforced in the service layer:
  - refused with exit 8: ``claim``, ``claim --next``, ``claim --take-over``, and
    ``move`` to canonical ``in_progress`` (CLI, ``--take-over``, MCP
    ``kanban_move_item``);
  - ``next`` and ``list --pickable`` print the halt and exit 8;
  - still allowed: the holder's move of an in-progress item to
    review/done/blocked/ready, and ``bounce``, ``create``, ``comment``, ``update``,
    ``control`` and read-only commands.
  A refusal reads ``board halted by <by> at <at>: <reason> — run 'yurtle-kanban
  control status'``.
- ``pickable`` gains a clause (#575): while halted it returns ``(False, "board
  halted by ...: <reason>")``, so every consumer of ``pickable`` agrees.
- ``control status --json`` gives ``{"mode", "reason", "by", "at", "source":
  "remote"|"worktree", "fetched_at"}``.

Readings the test partner chose (the driver may challenge them):

a. Where a test only needs a halted board to exist, the halt is HAND-WRITTEN as
   ``.kanban/control.yaml`` and pushed from clone A, never through ``control
   halt``. The file format is then pinned as a read contract, and those tests fail
   on assertions rather than on the missing command. ``control halt`` itself is
   exercised in Acceptance 1, 4, 5, 8 and 9.
b. Refusal wording. Only the stable parts are pinned: ``board halted by <by>``, the
   reason text, and ``control status``. How ``<at>`` is rendered is left open (the
   spec writes the stored ``10:00:00-07:00`` as ``10:00-07:00``). The same goes for
   the pickable reason, where §3 writes ``<at>: <reason>`` and §4 writes ``… :
   <reason>``.
c. Acceptance 10's "claim's reason equals pickable's" follows #575 reading a:
   pickable's reason must be CONTAINED in claim's message, since claim adds context.
d. Exit 8 is pinned at the CLI (``claim``, ``move``, ``next``, ``list
   --pickable``). The kind and ``exit_code`` of the service-level ``Outcome`` for a
   halted claim are left open; the halted claim must not be ``won``/``local``/
   ``noop``.
e. "No git fetch" (Acceptance 2) is checked by spying on ``subprocess.Popen``,
   which ``subprocess.run`` uses. It holds for ``next``, ``list --pickable`` and
   ``control status``: no git ``fetch``, ``pull`` or ``ls-remote`` may run. As a
   second check, B sees the halt only after a plain ``git fetch``.
f. The age of the last fetch (Acceptance 6). Git's own records of the fetch are
   backdated two hours: the remote-tracking ref's reflog entry (the fetch runs with
   ``GIT_COMMITTER_DATE``), and the mtimes of ``FETCH_HEAD`` and the loose ref
   file. Any of those may be the implementation's clock. The printed age must sit
   on a line naming "fetch" and carry a number with a time unit. ``control status
   --json``'s ``fetched_at`` must be within 15 minutes of the backdated time.
g. The resume state. After ``control resume``, a ``.kanban/control.yaml`` that
   still exists on origin must say ``mode: running``. Deleting it is also accepted.
h. The failed halt (Acceptance 5). An unreachable remote must exit 4 and a remote
   whose update hook refuses must exit 6: "the same outcomes and exit codes" as
   #574. Both must print the NOT-in-effect sentence.
i. Broken state (Acceptance 7) is tested against the working-tree file of a repo
   with NO remote, plus one origin-sourced case. The "parse error shown" part is
   pinned as the output naming ``control.yaml``, and naming ``pause`` for the
   unknown mode. ``validate --json`` must carry an issue whose message names
   ``control.yaml``. A well-formed ``mode: running`` file must not produce one.
j. The holder's finishing moves (Expected 3) are tried to review, blocked and
   ready. ``done`` is left out, because the nautical done gates are not this
   issue's.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import CliRunner, Result

from tests.issues.test_574_claim import (
    ITEM,
    ITEM_ID,
    A,
    B,
    frontmatter,
    item_text,
    push_from_a,
    seed,
)
from tests.issues.test_574_sync_and_push import commit_files, dirty_feature_branch, snapshot
from tests.issues.test_575_pickable_next import Repo, make_repo
from tests.issues.test_585_create_push_loop import World, git
from yurtle_kanban.cli import main
from yurtle_kanban.mcp import server as mcp_server

pytestmark = pytest.mark.usefixtures("claim_env")

CONTROL = ".kanban/control.yaml"
REASON = "Incident 42: the deploy pipeline is corrupting item files"
AT = "2026-09-27T10:00:00-07:00"
HALTED = 8
NOT_IN_EFFECT = "the halt is NOT in effect for other agents"
STATUS_KEYS = {"mode", "reason", "by", "at", "source", "fetched_at"}
NETWORK_VERBS = {"fetch", "pull", "ls-remote"}


# --- harness ---------------------------------------------------------------------


def flat(text: str | None) -> str:
    return " ".join((text or "").split())


def run_cli(cwd: Path, monkeypatch: pytest.MonkeyPatch, argv: list[str], **kw: Any) -> Result:
    monkeypatch.chdir(cwd)
    return CliRunner().invoke(main, argv, **kw)


def ok(result: Result) -> None:
    assert result.exit_code == 0, (
        f"exit {result.exit_code}: {result.exception!r}\n{result.output}"
    )


def halted(result: Result, by: str = A, reason: str = REASON) -> None:
    """Exit 8, naming who halted and why (reading b)."""
    out = flat(result.output)
    assert result.exit_code == HALTED, f"exit {result.exit_code}, not 8: {out}"
    assert f"board halted by {by}" in out, out
    assert flat(reason) in out, f"the halt reason is missing: {out}"


def refused_halted(result: Result, by: str = A, reason: str = REASON) -> None:
    """A refusal (claim, move): as `halted`, pointing at `control status`."""
    halted(result, by, reason)
    assert "control status" in flat(result.output), flat(result.output)


def control_yaml(
    mode: str = "halt", reason: str = REASON, by: str = A, at: str = AT
) -> str:
    return f"mode: {mode}\nreason: {json.dumps(reason)}\nby: {by}\nat: {at}\n"


def publish_from_a(world: World, rel: str, text: str | None, message: str) -> None:
    """Commit `text` at `rel` on A's main (None deletes it) and push; B is NOT
    fetched (reading a)."""
    path = world.a / rel
    if text is None:
        path.unlink()
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    git(world.a, "add", "-A")
    git(world.a, "commit", "-m", message)
    git(world.a, "push", "origin", f"HEAD:refs/heads/{world.default}")


def hand_halt(world: World, **kw: Any) -> None:
    """A hand-written halt on origin, fetched by B (not merged into B's tree)."""
    publish_from_a(world, CONTROL, control_yaml(**kw), "halt (by hand)")
    git(world.b, "fetch", "origin")


def as_stamp(value: Any) -> datetime:
    stamp = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    assert stamp.tzinfo is not None, f"no UTC offset: {value!r}"
    return stamp


def remote_control(world: World) -> dict[str, Any] | None:
    if CONTROL not in world.remote_files():
        return None
    data = yaml.safe_load(world.remote_show(CONTROL))
    assert isinstance(data, dict), data
    return data


def status_json(cwd: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    result = run_cli(cwd, monkeypatch, ["control", "status", "--json"])
    ok(result)
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise AssertionError(f"stdout is not JSON ({e}):\n{result.stdout}") from e
    assert isinstance(data, dict), data
    return data


def mcp(root: Path) -> Any:
    return mcp_server.KanbanMCPServer(repo_root=root)


class GitSpy:
    """Records every `git` argv started through `subprocess.Popen` (reading e)."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[list[str]] = []
        real = subprocess.Popen
        spy = self

        class Spying(real):  # type: ignore[misc, valid-type]
            def __init__(self, args: Any, *a: Any, **k: Any) -> None:
                argv = [str(x) for x in args] if isinstance(args, (list, tuple)) else [str(args)]
                if argv and Path(argv[0]).name == "git":
                    spy.calls.append(argv)
                super().__init__(args, *a, **k)

        monkeypatch.setattr(subprocess, "Popen", Spying)

    def network(self) -> list[list[str]]:
        return [argv for argv in self.calls if NETWORK_VERBS & set(argv[1:])]


def local_halted_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, control: str | None,
    items: dict[str, dict[str, Any]] | None = None,
) -> Repo:
    """#575's repo with NO remote: EXP-1 and EXP-2 ready, unassigned, plus
    `control` committed as the working-tree control file (None: no file)."""
    repo = make_repo(
        tmp_path, monkeypatch,
        items or {"EXP-1": {"status": "ready", "rank": 1}, "EXP-2": {"status": "ready"}},
    )
    if control is not None:
        (repo.root / CONTROL).write_text(control)
        repo.commit("control state")
    return repo


# --- the command exists ---------------------------------------------------------------


def test_control_help_names_its_subcommands() -> None:
    result = CliRunner().invoke(main, ["control", "--help"])
    ok(result)
    out = flat(result.output)
    for sub in ("halt", "resume", "status"):
        assert sub in out, f"{sub} missing from control --help:\n{out}"
    assert "quiesce" not in out, "there is no quiesce mode (spec: one mode, halt)"


def test_control_halt_help_names_its_options() -> None:
    result = CliRunner().invoke(main, ["control", "halt", "--help"])
    ok(result)
    out = flat(result.output)
    for option in ("--reason", "--reason-file", "--agent"):
        assert option in out, f"{option} missing from control halt --help:\n{out}"


# --- Acceptance 1: A halts; B's claim, after its own fetch, is refused ----------------------


def test_a1_halt_by_a_refuses_bs_claim(world, monkeypatch) -> None:
    ok(run_cli(world.a, monkeypatch, ["control", "halt", "--reason", REASON, "--agent", A]))

    state = remote_control(world)
    assert state is not None, "the halt is not on origin's default branch"
    assert state["mode"] == "halt"
    assert state["reason"] == REASON
    assert state["by"] == A
    as_stamp(state["at"])
    assert not (world.b / CONTROL).exists(), "fixture: B has not pulled the halt"

    base = world.remote_sha()
    b_before = snapshot(world.b)
    result = run_cli(world.b, monkeypatch, ["claim", ITEM_ID, "--agent", B])

    refused_halted(result)
    assert world.remote_sha() == base, "B's refused claim changed origin"
    assert snapshot(world.b) == b_before
    assert not frontmatter(world.remote_show(ITEM)).get("assignee")


def test_a1_halt_is_one_commit_of_only_the_control_file(world, monkeypatch) -> None:
    a_before = snapshot(world.a)
    base = world.remote_sha()

    ok(run_cli(world.a, monkeypatch, ["control", "halt", "--reason", REASON, "--agent", A]))

    tip = world.remote_sha()
    assert git(world.remote, "rev-list", f"{base}..{tip}").split() == [tip]
    assert commit_files(world.remote, tip) == [CONTROL]
    # a clean checkout on the default branch is fast-forwarded to the tip, as every
    # sync_and_push write does (#574's pinned contract; [steer] on #582)
    assert git(world.a, "rev-parse", "HEAD").strip() in (a_before["head"], tip), (
        "the halt moved A's checkout somewhere other than origin's tip"
    )
    assert git(world.a, "status", "--porcelain").strip() == "", "the halt dirtied A's checkout"


def test_a1_halt_from_a_feature_branch_lands_on_the_default_branch(world, monkeypatch) -> None:
    dirty_feature_branch(world)
    a_before = snapshot(world.a)

    ok(run_cli(world.a, monkeypatch, ["control", "halt", "--reason", REASON, "--agent", A]))

    state = remote_control(world)
    assert state is not None and state["mode"] == "halt", state
    assert git(world.remote, "for-each-ref", "--format=%(refname)", "refs/heads").split() == [
        f"refs/heads/{world.default}"
    ], "the halt went to a branch other than origin's default"
    assert snapshot(world.a) == a_before


def test_a1_claim_take_over_and_claim_next_are_refused(world, monkeypatch) -> None:
    seed(world, "in_progress", A)
    push_from_a(world, {
        "kanban-work/expeditions/EXP-002-y.md": item_text("ready", item_id="EXP-002", title="Y"),
    }, "EXP-002 ready")
    hand_halt(world)
    base = world.remote_sha()

    refused_halted(run_cli(world.b, monkeypatch, [
        "claim", ITEM_ID, "--take-over", "--agent", B,
    ]))
    refused_halted(run_cli(world.b, monkeypatch, ["claim", "--next", "--agent", B]))
    assert world.remote_sha() == base


def test_halt_actor_falls_back_to_git_user_name(world, monkeypatch) -> None:
    user = git(world.a, "config", "user.name").strip()
    assert user, "fixture: A has a git user.name"
    ok(run_cli(world.a, monkeypatch, ["control", "halt", "--reason", REASON]))
    state = remote_control(world)
    assert state is not None and state["by"] == user, state


def test_halt_actor_from_env(world, monkeypatch) -> None:
    monkeypatch.setenv("YURTLE_AGENT", "Mini")
    ok(run_cli(world.a, monkeypatch, ["control", "halt", "--reason", REASON]))
    state = remote_control(world)
    assert state is not None and state["by"] == "Mini", state


# --- Acceptance 2: next / list --pickable read the last-fetched state, never fetch ----------


def test_a2_next_and_list_read_last_fetched_state_without_fetching(world, monkeypatch) -> None:
    publish_from_a(world, CONTROL, control_yaml(), "halt (by hand)")  # B has NOT fetched
    spy = GitSpy(monkeypatch)

    before = run_cli(world.b, monkeypatch, ["next", "--agent", B])
    ok(before)  # B's last-fetched origin/main has no halt: EXP-001 is offered
    assert ITEM_ID in before.output, before.output
    ok(run_cli(world.b, monkeypatch, ["list", "--pickable", "--agent", B]))

    git(world.b, "fetch", "origin")  # the test's own fetch, then only the CLI's are kept
    spy.calls.clear()

    halted(run_cli(world.b, monkeypatch, ["next", "--agent", B]))
    halted(run_cli(world.b, monkeypatch, ["list", "--pickable", "--agent", B]))
    as_json = run_cli(world.b, monkeypatch, ["next", "--agent", B, "--json"])
    assert as_json.exit_code == HALTED, flat(as_json.output)  # its payload is open
    ok(run_cli(world.b, monkeypatch, ["control", "status"]))

    assert spy.network() == [], f"a read ran a network git command: {spy.network()}"
    assert not (world.b / CONTROL).exists(), "fixture: B's worktree never held the halt"


# --- Acceptance 3: what a halt refuses and what it allows -------------------------------------


def test_a3_move_to_in_progress_is_refused(world, monkeypatch) -> None:
    hand_halt(world)
    before = (world.b / ITEM).read_bytes()
    head = git(world.b, "rev-parse", "HEAD").strip()

    refused_halted(run_cli(world.b, monkeypatch, ["move", ITEM_ID, "in_progress", "--agent", B]))
    refused_halted(run_cli(world.b, monkeypatch, [
        "move", ITEM_ID, "in_progress", "--take-over", "--agent", B,
    ]))

    assert (world.b / ITEM).read_bytes() == before
    assert git(world.b, "rev-parse", "HEAD").strip() == head


def test_a3_move_to_native_in_progress_name_is_refused(world, monkeypatch) -> None:
    hand_halt(world)
    before = (world.b / ITEM).read_bytes()
    refused_halted(run_cli(world.b, monkeypatch, ["move", ITEM_ID, "underway", "--agent", B]))
    assert (world.b / ITEM).read_bytes() == before


def test_a3_mcp_move_to_in_progress_is_refused(world, monkeypatch) -> None:
    hand_halt(world)
    before = (world.b / ITEM).read_bytes()
    head = git(world.b, "rev-parse", "HEAD").strip()

    out = mcp(world.b).handle_tool_call(
        "kanban_move_item", {"item_id": ITEM_ID, "new_status": "in_progress", "agent": B}
    )

    assert "error" in out and not out.get("success"), out
    assert f"board halted by {A}" in out["error"], out
    assert REASON in out["error"], out
    assert (world.b / ITEM).read_bytes() == before
    assert git(world.b, "rev-parse", "HEAD").strip() == head


@pytest.mark.parametrize("target", ["review", "blocked", "ready"])
def test_a3_holder_may_finish_under_halt(world, monkeypatch, target) -> None:
    seed(world, "in_progress", B)  # B holds it, and B's tree has it
    hand_halt(world)
    halted(run_cli(world.b, monkeypatch, ["next", "--agent", A]))  # the halt is seen

    result = run_cli(world.b, monkeypatch, ["move", ITEM_ID, target, "--agent", B])

    ok(result)
    assert frontmatter((world.b / ITEM).read_text())["status"] != "in_progress"


def test_a3_create_comment_update_bounce_are_allowed(world, monkeypatch) -> None:
    hand_halt(world)
    halted(run_cli(world.b, monkeypatch, ["next", "--agent", B]))  # the halt is seen

    ok(run_cli(world.b, monkeypatch, ["create", "expedition", "Board halted: file the incident"]))
    ok(run_cli(world.b, monkeypatch, [
        "comment", ITEM_ID, "--body", "Seen the halt; standing by.", "--agent", B,
    ]))
    ok(run_cli(world.b, monkeypatch, ["update", ITEM_ID, "--priority", "high"]))

    base = world.remote_sha()
    ok(run_cli(world.b, monkeypatch, [
        "bounce", ITEM_ID, "--reason", "Unclear while halted", "--agent", B,
    ]))
    assert world.remote_sha() != base, "the bounce did not land on origin"
    assert frontmatter(world.remote_show(ITEM))["bounces"] == 1


def test_a3_create_push_is_allowed(world, monkeypatch) -> None:
    hand_halt(world)
    git(world.b, "reset", "--hard", f"origin/{world.default}")
    halted(run_cli(world.b, monkeypatch, ["next", "--agent", B]))
    before = len(world.remote_items())

    ok(run_cli(world.b, monkeypatch, [
        "create", "expedition", "Board halted: incident", "--push",
    ]))
    assert len(world.remote_items()) == before + 1


def test_a3_read_only_commands_are_allowed(world, monkeypatch) -> None:
    hand_halt(world)
    for argv in (["show", ITEM_ID], ["list"], ["board"], ["states"]):
        ok(run_cli(world.b, monkeypatch, argv))


def test_a3_mcp_suggest_next_offers_nothing(world, monkeypatch) -> None:
    hand_halt(world)
    out = mcp(world.b).handle_tool_call("kanban_suggest_next", {})
    assert not out.get("suggestion"), f"a halted board suggested work: {out}"


# --- Acceptance 4: resume ---------------------------------------------------------------------


def test_a4_resume_lets_claims_through_again(world, monkeypatch) -> None:
    ok(run_cli(world.a, monkeypatch, ["control", "halt", "--reason", REASON, "--agent", A]))
    refused_halted(run_cli(world.b, monkeypatch, ["claim", ITEM_ID, "--agent", B]))

    ok(run_cli(world.a, monkeypatch, [
        "control", "resume", "--reason", "Pipeline fixed", "--agent", A,
    ]))

    state = remote_control(world)
    assert state is None or state["mode"] == "running", state  # reading g
    ok(run_cli(world.b, monkeypatch, ["claim", ITEM_ID, "--agent", B]))
    assert frontmatter(world.remote_show(ITEM))["assignee"] == B


def test_a4_resume_reason_is_optional(world, monkeypatch) -> None:
    hand_halt(world)
    ok(run_cli(world.a, monkeypatch, ["control", "resume", "--agent", A]))
    state = remote_control(world)
    assert state is None or state["mode"] == "running", state
    git(world.b, "fetch", "origin")
    ok(run_cli(world.b, monkeypatch, ["next", "--agent", B]))


# --- Acceptance 5: a halt that did not land says so --------------------------------------------


def test_a5_unreachable_remote_halt_is_not_in_effect(world, monkeypatch) -> None:
    git(world.a, "remote", "set-url", "origin", str(world.a.parent / "missing.git"))
    a_before = snapshot(world.a)
    refs_before = git(world.a, "for-each-ref")
    base = world.remote_sha()

    result = run_cli(world.a, monkeypatch, ["control", "halt", "--reason", REASON, "--agent", A])

    out = flat(result.output)
    assert result.exit_code == 4, f"exit {result.exit_code}: {out}"
    assert NOT_IN_EFFECT in out, out
    assert snapshot(world.a) == a_before, "something was written or committed locally"
    assert git(world.a, "for-each-ref") == refs_before
    assert world.remote_sha() == base


def test_a5_push_refused_halt_is_not_in_effect(world, monkeypatch) -> None:
    hook = world.remote / "hooks" / "update"
    hook.write_text("#!/bin/sh\necho 'kanban-guard: main is frozen' >&2\nexit 1\n")
    hook.chmod(0o755)
    a_before = snapshot(world.a)
    base = world.remote_sha()

    result = run_cli(world.a, monkeypatch, ["control", "halt", "--reason", REASON, "--agent", A])

    out = flat(result.output)
    assert result.exit_code == 6, f"exit {result.exit_code}: {out}"
    assert NOT_IN_EFFECT in out, out
    assert snapshot(world.a) == a_before
    assert world.remote_sha() == base


def test_no_remote_halt_is_a_local_commit_with_a_note(tmp_path, monkeypatch) -> None:
    repo = local_halted_repo(tmp_path, monkeypatch, None)
    head = repo.head()

    result = run_cli(repo.root, monkeypatch, ["control", "halt", "--reason", REASON, "--agent", A])

    ok(result)
    assert "no remote" in flat(result.output).lower(), result.output
    tip = repo.head()
    assert git(repo.root, "rev-list", f"{head}..{tip}").split() == [tip]
    assert commit_files(repo.root, tip) == [CONTROL]
    assert yaml.safe_load((repo.root / CONTROL).read_text())["mode"] == "halt"
    halted(run_cli(repo.root, monkeypatch, ["next", "--agent", A]))


# --- Acceptance 6: an unreachable remote while reading: last-known state, with its age -------------


def _stale_fetch(world: World, age: timedelta) -> datetime:
    """B fetches the halt, and git's records of that fetch are backdated by `age`
    (reading f)."""
    when = datetime.now(timezone.utc) - age
    stamp = int(when.timestamp())
    env = {**os.environ, "GIT_COMMITTER_DATE": f"{stamp} +0000"}
    done = subprocess.run(
        ["git", "fetch", "origin"], cwd=world.b, capture_output=True, text=True, env=env,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    for rel in ("FETCH_HEAD", f"refs/remotes/origin/{world.default}"):
        path = world.b / ".git" / rel
        if path.exists():
            os.utime(path, (stamp, stamp))
    return when


AGE = re.compile(
    r"\b\d+\s*(?:s|m|h|d|secs?|seconds?|mins?|minutes?|hrs?|hours?|days?)\b", re.I
)


def _age_line(output: str) -> str | None:
    for line in output.splitlines():
        if "fetch" in line.lower() and AGE.search(line):
            return line
    return None


def test_a6_unreachable_remote_uses_last_known_state_and_prints_its_age(
    world, monkeypatch
) -> None:
    publish_from_a(world, CONTROL, control_yaml(), "halt (by hand)")
    when = _stale_fetch(world, timedelta(hours=2))
    git(world.b, "remote", "set-url", "origin", str(world.b.parent / "missing.git"))

    for argv in (["next", "--agent", B], ["list", "--pickable", "--agent", B]):
        result = run_cli(world.b, monkeypatch, argv)
        halted(result)
        assert _age_line(result.output), f"{argv}: no age of the last fetch:\n{result.output}"

    status = run_cli(world.b, monkeypatch, ["control", "status"])
    ok(status)
    assert _age_line(status.output), f"control status shows no fetch age:\n{status.output}"

    data = status_json(world.b, monkeypatch)
    assert data["mode"] == "halt" and data["source"] == "remote", data
    fetched = as_stamp(data["fetched_at"])
    assert abs(fetched - when) < timedelta(minutes=15), (
        f"fetched_at {data['fetched_at']} is not the last fetch ({when.isoformat()})"
    )


def test_a6_unreachable_remote_claim_changes_nothing(world, monkeypatch) -> None:
    hand_halt(world)
    git(world.b, "remote", "set-url", "origin", str(world.b.parent / "missing.git"))
    b_before = snapshot(world.b)
    result = run_cli(world.b, monkeypatch, ["claim", ITEM_ID, "--agent", B])
    assert result.exit_code != 0, flat(result.output)
    assert snapshot(world.b) == b_before


# --- Acceptance 7: an unknown mode or broken YAML fails closed ----------------------------------------


BROKEN = {
    "unknown-mode": (control_yaml(mode="pause"), "pause"),
    "broken-yaml": ("mode: [halt\nreason: \"unterminated\n", None),
}


@pytest.mark.parametrize("case", list(BROKEN), ids=list(BROKEN))
def test_a7_bad_state_is_treated_as_halt(tmp_path, monkeypatch, case) -> None:
    text, word = BROKEN[case]
    repo = local_halted_repo(tmp_path, monkeypatch, text)
    head = repo.head()

    for argv in (
        ["next", "--agent", A],
        ["list", "--pickable", "--agent", A],
        ["claim", "EXP-1", "--agent", A],
        ["move", "EXP-1", "in_progress", "--agent", A],
    ):
        result = run_cli(repo.root, monkeypatch, argv)
        out = flat(result.output)
        assert result.exit_code == HALTED, f"{argv}: exit {result.exit_code}: {out}"
        assert "control.yaml" in out, f"{argv}: the parse error is not shown: {out}"
        if word:
            assert word in out, f"{argv}: the unknown mode is not named: {out}"

    assert repo.head() == head
    assert not frontmatter(repo.path("EXP-1").read_text()).get("assignee")
    okay, _ = repo.service().pickable(repo.item("EXP-1"), A)
    assert okay is False


def test_a7_bad_state_on_origin_is_treated_as_halt(world, monkeypatch) -> None:
    publish_from_a(world, CONTROL, control_yaml(mode="pause"), "a bad control file")
    base = world.remote_sha()
    result = run_cli(world.b, monkeypatch, ["claim", ITEM_ID, "--agent", B])
    out = flat(result.output)
    assert result.exit_code == HALTED, f"exit {result.exit_code}: {out}"
    assert "pause" in out, out
    assert world.remote_sha() == base


@pytest.mark.parametrize("case", list(BROKEN), ids=list(BROKEN))
def test_a7_validate_reports_bad_state(tmp_path, monkeypatch, case) -> None:
    repo = local_halted_repo(tmp_path, monkeypatch, BROKEN[case][0])
    result = run_cli(repo.root, monkeypatch, ["validate", "--json"])
    payload = json.loads(result.stdout)
    mine = [i for i in payload.get("issues", []) if "control.yaml" in str(i.get("message", ""))]
    assert mine, f"validate does not report the bad control file: {payload}"


def test_a7_validate_accepts_a_running_state(tmp_path, monkeypatch) -> None:
    repo = local_halted_repo(tmp_path, monkeypatch, "mode: running\n")
    result = run_cli(repo.root, monkeypatch, ["validate", "--json"])
    payload = json.loads(result.stdout)
    assert not [
        i for i in payload.get("issues", []) if "control.yaml" in str(i.get("message", ""))
    ], payload
    ok(run_cli(repo.root, monkeypatch, ["next", "--agent", A]))


def test_absent_control_file_is_running(tmp_path, monkeypatch) -> None:
    repo = local_halted_repo(tmp_path, monkeypatch, None)
    data = status_json(repo.root, monkeypatch)
    assert data["mode"] == "running", data
    assert repo.service().pickable(repo.item("EXP-1"), A) == (True, "pickable")


# --- Acceptance 8: --reason is required for halt ------------------------------------------------------


@pytest.mark.parametrize(
    "argv,stdin",
    [
        (["control", "halt", "--agent", A], None),
        (["control", "halt", "--reason", "x", "--reason-file", "-", "--agent", A], "y\n"),
    ],
    ids=["no-reason", "both-reasons"],
)
def test_a8_halt_reason_is_a_usage_error(world, monkeypatch, argv, stdin) -> None:
    base = world.remote_sha()
    a_before = snapshot(world.a)
    result = run_cli(world.a, monkeypatch, argv, input=stdin)
    out = flat(result.output)
    assert result.exit_code == 2, f"exit {result.exit_code}: {out}"
    assert "--reason" in out, out
    assert world.remote_sha() == base
    assert snapshot(world.a) == a_before


def test_a8_halt_reason_from_stdin(world, monkeypatch) -> None:
    ok(run_cli(
        world.a, monkeypatch, ["control", "halt", "--reason-file", "-", "--agent", A],
        input=REASON + "\n",
    ))
    state = remote_control(world)
    assert state is not None and state["reason"].strip() == REASON, state


# --- Acceptance 9: control status --json ---------------------------------------------------------------


def test_a9_status_json_schema_after_halt(world, monkeypatch) -> None:
    ok(run_cli(world.a, monkeypatch, ["control", "halt", "--reason", REASON, "--agent", A]))
    git(world.b, "fetch", "origin")

    data = status_json(world.b, monkeypatch)

    assert set(data) == STATUS_KEYS, f"keys {sorted(data)} != {sorted(STATUS_KEYS)}"
    assert data["mode"] == "halt"
    assert data["reason"] == REASON
    assert data["by"] == A
    as_stamp(data["at"])
    assert data["source"] == "remote"
    as_stamp(data["fetched_at"])


def test_a9_status_json_running_with_no_file(world, monkeypatch) -> None:
    data = status_json(world.b, monkeypatch)
    assert set(data) == STATUS_KEYS, data
    assert data["mode"] == "running"
    assert data["source"] == "remote"


def test_a9_status_json_worktree_source_without_remote(tmp_path, monkeypatch) -> None:
    repo = local_halted_repo(tmp_path, monkeypatch, control_yaml(by=B))
    data = status_json(repo.root, monkeypatch)
    assert set(data) == STATUS_KEYS, data
    assert data["mode"] == "halt" and data["by"] == B and data["reason"] == REASON, data
    assert data["source"] == "worktree"


def test_a9_status_plain_prints_mode_reason_by_at(world, monkeypatch) -> None:
    hand_halt(world)
    result = run_cli(world.b, monkeypatch, ["control", "status"])
    ok(result)
    out = flat(result.output)
    for part in ("halt", REASON, A, "2026-09-27"):
        assert part in out, f"{part!r} missing from control status: {out}"


# --- Acceptance 10: pickable and claim agree (#575) ------------------------------------------------------


def test_a10_pickable_reason_and_claim_agree(tmp_path, monkeypatch) -> None:
    repo = local_halted_repo(tmp_path, monkeypatch, control_yaml(by=B))
    okay, reason = repo.service().pickable(repo.item("EXP-1"), A)
    assert okay is False, "a halted board left EXP-1 pickable"
    assert reason.startswith(f"board halted by {B}"), reason
    assert REASON in reason, reason
    head = repo.head()

    out = repo.service().claim_item(
        "EXP-1", actor=A, sleep=lambda s: None, jitter=lambda lo, hi: 0.0
    )

    assert out.kind not in ("won", "local", "noop"), f"{out.kind}: {out.message}"
    assert reason in out.message, f"pickable says {reason!r}, claim says {out.message!r}"
    assert repo.head() == head

    result = run_cli(repo.root, monkeypatch, ["claim", "EXP-1", "--agent", A])
    refused_halted(result, by=B)
    assert flat(reason) in flat(result.output), flat(result.output)


def test_a10_every_pickable_consumer_agrees(tmp_path, monkeypatch) -> None:
    repo = local_halted_repo(tmp_path, monkeypatch, control_yaml(by=B))
    svc = repo.service()
    picks, _ = svc.pick_report(A)
    assert picks == [], f"pick_report offered work on a halted board: {[i.id for i in picks]}"
    assert svc.next_item(A) is None or svc.next_item(A)[1] != "pick"

    head = repo.head()
    halted(run_cli(repo.root, monkeypatch, ["claim", "--next", "--agent", A]), by=B)
    assert repo.head() == head
    for item_id in ("EXP-1", "EXP-2"):
        assert not frontmatter(repo.path(item_id).read_text()).get("assignee")
