"""Issue #578: ``bounce ID --reason``, send an ill-defined item back.

The spec is the revised issue body (Expected 1-5, Acceptance 1-9), rewritten after
the adversarial review.

- CLI ``yurtle-kanban bounce ID (--reason TEXT | --reason-file PATH|-) [--agent A]
  [--take-over]``. The actor comes from ``resolve_actor(--agent,
  allow_git_fallback=False)`` and the reason from ``read_text_option``, which is
  required. The item must be unassigned or held by the actor; one held by someone
  else needs ``--take-over``, which records ``kb:takenOverFrom``. A ``done`` or
  otherwise finished item is exit 1.
- The bounce is one commit through #574's ``sync_and_push``, with the same
  outcomes and exit codes. Its ``mutate`` does four things:
  - moves the item to the theme's canonical ``backlog`` status (native name). This
    move is exempt from the transition table and WIP limits, but ``* -> backlog``
    gates still apply. The history node records ``kb:bounced true`` (never
    ``kb:forcedMove``) with ``kb:by`` = actor;
  - clears the assignee;
  - writes the frontmatter stamp ``bounce_sha``, ``bounced_by``, ``bounced_at``
    (ISO-8601 with offset) and ``bounces`` (N+1, absent = 0);
  - appends the comment ``[bounce by A, body-sha:<12 hex>] <reason>``. The
    comment is informational and never read back.
- Body hash (Expected 2): #576's ``body_span`` of the text, normalised (CRLF/CR to
  LF, trailing whitespace stripped per line, leading/trailing blank lines stripped),
  then ``sha256(utf-8).hexdigest()``. The H1 is inside the span. Frontmatter is not.
- ``pickable`` gains a clause: an item is not pickable while ``bounce_sha`` equals
  its current body hash. The reason is ``<ID> carries an unrepaired bounce (body
  unchanged since <A> bounced it at <T>)``.
- ``show`` prints ``Bounced N× (last by A at T)``. ``show --json`` and
  ``list --json`` carry ``bounces`` (0 when absent). ``validate`` reports a
  malformed stamp.

Readings the test partner chose (the driver may challenge them):

a. The hash is computed HERE from ``KanbanService.body_span`` and the normalisation
   (``expected_hash``), so no service helper name is pinned. "Strip leading and
   trailing blank lines" is read as: split on LF, ``rstrip()`` each line, drop
   empty lines at both ends, join with LF. The hashed text therefore has no final
   newline.
b. A bounced item sits in canonical ``backlog``, so ``pickable``'s status clause
   already refuses it there. The bounce clause is only observable on a READY item.
   Acceptance 1 therefore triages the item back to ready with a plain ``move`` (a
   frontmatter and history edit that must not clear the bounce) before the refused
   ``claim``. Whether the bounce clause comes before or after the status clause is
   left open.
c. Acceptance 1's "claim refused with the bounce reason" is read as pickable's
   bounce reason, not the ``--reason`` text: the stamp holds no reason, and the
   comment is never read back. As in #575 (reading a), claim's message must
   CONTAIN pickable's reason (Acceptance 9 says "equals").
d. Only the stable part of the pickable reason is pinned:
   ``"<ID> carries an unrepaired bounce (body unchanged since <A> bounced it at "``.
   The rendering of ``<T>`` is open.
e. Acceptance 8 is run in both directions through #574's seam, wrapping
   ``KanbanService.sync_and_push`` for clone A only, as test_575 does. When A's
   bounce loses to B's claim, A exits 3 ("lost to agent-B", since B holds it now).
   When B's claim loses to A's bounce, the item has NO holder afterwards, and
   #574's ``Refuse`` is "lost" only with a holder. So this module pins one winner
   and a non-zero loser there, but not exit 3 (see the report).
f. Missing or doubled reason options are a click usage error (exit 2), since that
   is ``read_text_option``'s contract. A missing actor is exit 1 naming
   ``--agent`` and ``YURTLE_AGENT``, as claim's refusal does.
g. ``kb:bounced true`` is matched as ``kb:bounced true`` or ``kb:bounced
   "true"^^xsd:boolean`` (the file's ``kb:forcedMove`` style).
h. ``validate --json``: a malformed stamp gives an issue whose ``id`` is the item's
   and whose message names the bad key (``bounce_sha`` / ``bounces``). The issue
   ``type`` string is open.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner, Result

from tests.issues.test_574_claim import (
    ITEM,
    ITEM_ID,
    A,
    B,
    claim,
    fired,
    frontmatter,
    install_hooks,
    item_text,
    nodes_by,
    push_from_a,
    seed,
    service,
)
from tests.issues.test_574_sync_and_push import Recorder, commit_files, snapshot
from tests.issues.test_575_pickable_next import Repo, make_repo
from tests.issues.test_585_create_push_loop import World, git
from yurtle_kanban.cli import main
from yurtle_kanban.config import KanbanConfig, PathConfig
from yurtle_kanban.models import WorkItemStatus
from yurtle_kanban.service import KanbanService

pytestmark = pytest.mark.usefixtures("claim_env")

REASON = "Too vague: which endpoint, and what does done look like?"
UNREPAIRED = "carries an unrepaired bounce"
BOUNCED = re.compile(r'kb:bounced\s+(?:true\b|"true"(?:\^\^xsd:boolean)?)')
HEX64 = re.compile(r"^[0-9a-f]{64}$")


# --- harness ---------------------------------------------------------------------


def expected_hash(text: str) -> str:
    """Expected 2, computed independently of the implementation (reading a)."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    start, end = KanbanService.body_span(text)
    lines = [line.rstrip() for line in text[start:end].split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def flat(text: str | None) -> str:
    return " ".join((text or "").split())


def run_cli(cwd: Path, monkeypatch: pytest.MonkeyPatch, argv: list[str], **kw: Any) -> Result:
    monkeypatch.chdir(cwd)
    return CliRunner().invoke(main, argv, **kw)


def bounce(
    world: World, monkeypatch: pytest.MonkeyPatch, *extra: str, reason: str = REASON,
    agent: str | None = A, item_id: str = ITEM_ID,
) -> Result:
    argv = ["bounce", item_id, "--reason", reason, *extra]
    if agent is not None:
        argv += ["--agent", agent]
    return run_cli(world.a, monkeypatch, argv)


def ok(result: Result) -> None:
    assert result.exit_code == 0, (
        f"exit {result.exit_code}: {result.exception!r}\n{result.output}"
    )


def native(clone: Path, status: WorkItemStatus, item_id: str = ITEM_ID) -> str:
    svc = service(clone)
    item = svc.get_item(item_id)
    assert item is not None
    return svc.status_label(replace(item, status=status))


def comments_section(text: str) -> str:
    m = re.search(r"^## Comments[ \t]*$(.*)\Z", text, re.S | re.M)
    assert m, f"no ## Comments section:\n{text}"
    return m.group(1)


def sync_a(world: World) -> None:
    """A's checkout follows origin (bounce and claim never touch the worktree)."""
    git(world.a, "fetch", "origin")
    git(world.a, "reset", "--hard", f"origin/{world.default}")


def push_a(world: World, message: str = "kanban") -> None:
    git(world.a, "push", "origin", f"HEAD:refs/heads/{world.default}")
    git(world.b, "fetch", "origin")
    git(world.b, "reset", "--hard", f"origin/{world.default}")


def with_stamp(
    text: str, *, sha: str | None = None, by: str = B,
    at: str = "2026-09-27T10:00:00+02:00", count: Any = 1,
) -> str:
    """`text` with a hand-written frontmatter stamp: `sha` defaults to the text's
    own body hash (an unrepaired bounce)."""
    sha = expected_hash(text) if sha is None else sha
    m = re.match(r"\A(---\n.*?\n)(---\n)", text, re.S)
    assert m, text
    stamp = f"bounce_sha: {sha}\nbounced_by: {by}\nbounced_at: {at}\nbounces: {count}\n"
    return m.group(1) + stamp + m.group(2) + text[m.end():]


def local_item(item_id: str, status: str = "ready", body: str = "A description long enough.",
               title: str | None = None, priority: str = "medium", rank: int | None = None,
               assignee: str | None = None) -> str:
    title = title or f"Item {item_id}"
    rank_line = f"priority_rank: {rank}\n" if rank is not None else ""
    who = f"assignee: {assignee}\n" if assignee else ""
    return (
        f"---\nid: {item_id}\ntitle: \"{title}\"\ntype: expedition\nstatus: {status}\n"
        f"priority: {priority}\n{rank_line}{who}---\n\n# {title}\n\n{body}\n"
    )


def local_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, texts: dict[str, str],
    extra: dict[str, dict[str, Any]] | None = None,
) -> Repo:
    """#575's two-board repo with NO remote (nautical work/, hdd research/), each of
    `texts` written verbatim as an expedition and committed."""
    ids = {**{k: {"status": "backlog"} for k in texts}, **(extra or {})}
    repo = make_repo(tmp_path, monkeypatch, ids)
    for item_id, text in texts.items():
        repo.path(item_id).write_bytes(text.encode("utf-8"))
    repo.commit("fixture texts")
    return repo


def pick(repo: Repo, item_id: str, actor: str | None = A) -> tuple[bool, str]:
    return repo.service().pickable(repo.item(item_id), actor)


def assert_unrepaired(repo: Repo, item_id: str, by: str = B) -> str:
    okay, reason = pick(repo, item_id)
    assert okay is False, f"{item_id} is pickable despite an unrepaired bounce"
    prefix = f"{item_id} {UNREPAIRED} (body unchanged since {by} bounced it at "
    assert reason.startswith(prefix), reason
    return reason


def json_payload(result: Result) -> Any:
    ok(result)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise AssertionError(f"stdout is not JSON ({e}):\n{result.stdout}") from e


def seam_on_a(monkeypatch: pytest.MonkeyPatch, world: World, action: Any) -> list[int]:
    """Wrap `sync_and_push` for clone A only: `action(attempt)` runs in its seam;
    returns the attempts seen (#575 reading g)."""
    real = KanbanService.sync_and_push
    seen: list[int] = []
    a_root = world.a.resolve()

    def wrapped(self: KanbanService, mutate: Any, **kw: Any) -> Any:
        kw["sleep"] = lambda s: None
        kw["jitter"] = lambda lo, hi: 0.0
        if Path(self.repo_root).resolve() != a_root:
            return real(self, mutate, **kw)
        inner = kw.get("seam")

        def seam(attempt: int) -> None:
            if inner is not None:
                inner(attempt)
            seen.append(attempt)
            action(attempt)

        kw["seam"] = seam
        return real(self, mutate, **kw)

    monkeypatch.setattr(KanbanService, "sync_and_push", wrapped)
    return seen


# --- the command exists -------------------------------------------------------------------


def test_bounce_help_names_its_options() -> None:
    result = CliRunner().invoke(main, ["bounce", "--help"])
    ok(result)
    out = flat(result.output)
    for option in ("--reason", "--reason-file", "--agent", "--take-over"):
        assert option in out, f"{option} missing from bounce --help:\n{out}"


# --- Acceptance 1: bounce -> claim refused -> body edit -> claim wins ------------------------


def test_a1_bounce_blocks_claim_until_the_body_is_edited(world, monkeypatch) -> None:
    b_out = claim(world.a, A)  # A holds it first: the bounce gives back its own item
    assert b_out.kind == "won", b_out.message

    ok(bounce(world, monkeypatch))

    text = world.remote_show(ITEM)
    fm = frontmatter(text)
    assert fm["status"] == native(world.a, WorkItemStatus.BACKLOG)
    assert not fm.get("assignee"), f"the bounce kept the assignee: {fm}"
    assert fm["bounce_sha"] == expected_hash(text)

    # triage: a plain move back to ready, pushed (reading b)
    sync_a(world)
    ok(run_cli(world.a, monkeypatch, ["move", ITEM_ID, "ready", "--agent", A]))
    push_a(world)
    assert frontmatter(world.remote_show(ITEM))["status"] == native(world.a, WorkItemStatus.READY)

    sync_a(world)
    svc = service(world.a)
    okay, reason = svc.pickable(svc.get_item(ITEM_ID), B)
    assert okay is False
    assert f"{ITEM_ID} {UNREPAIRED} (body unchanged since {A} bounced it at " in reason, reason

    base = world.remote_sha()
    refused = claim(world.b, B)
    assert refused.kind == "refused", refused.message
    assert refused.exit_code == 1
    assert reason in refused.message, (
        f"claim's refusal lacks pickable's reason {reason!r}: {refused.message}"
    )
    assert world.remote_sha() == base

    # the repair, made on origin only: A's and B's checkouts keep the old body
    ok(run_cli(
        world.a, monkeypatch, ["update", ITEM_ID, "--body-file", "-", "--push"],
        input="Endpoint: POST /v1/items. Done when the 201 carries the new ID.\n",
    ))
    assert expected_hash(world.remote_show(ITEM)) != fm["bounce_sha"]

    won = claim(world.b, B)
    assert won.kind == "won", won.message
    after = frontmatter(world.remote_show(ITEM))
    assert after["assignee"] == B
    assert after["bounce_sha"] == fm["bounce_sha"], "the stamp stays until the next bounce"


# --- Acceptance 2: the bounce's own writes leave the hash unchanged ------------------------


HISTORY = (
    "\n```yurtle\n@prefix kb: <https://yurtle.dev/kanban/> .\n"
    "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .\n\n"
    "<> kb:statusChange [\n    kb:status kb:ready ;\n"
    '    kb:at "2026-09-01T09:00:00"^^xsd:dateTime ;\n    kb:by "agent-C" ;\n] .\n```\n'
)
COMMENTS = "\n## Comments\n\n### agent-C (2026-09-01 09:05)\n\nLooks fine to me.\n"


@pytest.mark.parametrize(
    "tail", ["", HISTORY, COMMENTS, HISTORY + COMMENTS],
    ids=["bare", "history", "comments", "history+comments"],
)
def test_a2_bounce_writes_do_not_change_the_hash(world, monkeypatch, tail) -> None:
    if tail:  # "bare" is the seeded item as it is
        push_from_a(world, {ITEM: item_text("ready") + tail}, "EXP-001 with a tail")
    before = expected_hash(world.remote_show(ITEM))
    base = world.remote_sha()

    ok(bounce(world, monkeypatch))

    tip = world.remote_sha()
    assert git(world.remote, "rev-list", f"{base}..{tip}").split() == [tip], (
        "the bounce must be one commit on origin"
    )
    assert commit_files(world.remote, tip) == [ITEM]
    text = world.remote_show(ITEM)
    fm = frontmatter(text)
    assert HEX64.match(str(fm["bounce_sha"])), fm
    assert fm["bounce_sha"] == before, "bounce_sha is not the hash before the bounce"
    assert expected_hash(text) == before, "the bounce's own writes changed the body hash"


def test_a2_stamp_keys_and_comment(world, monkeypatch) -> None:
    ok(bounce(world, monkeypatch))

    text = world.remote_show(ITEM)
    fm = frontmatter(text)
    assert fm["bounced_by"] == A
    assert fm["bounces"] == 1
    at = fm["bounced_at"]
    stamp = at if isinstance(at, datetime) else datetime.fromisoformat(str(at))
    assert stamp.tzinfo is not None, f"bounced_at has no UTC offset: {at!r}"
    notice = f"[bounce by {A}, body-sha:{fm['bounce_sha'][:12]}] {REASON}"
    assert notice in comments_section(text), f"no {notice!r} comment:\n{text}"


def test_a2_reason_file_and_stdin(world, monkeypatch, tmp_path) -> None:
    reason_file = tmp_path / "reason.txt"
    reason_file.write_text("From a file:\nno acceptance criteria.\n")
    ok(run_cli(world.a, monkeypatch, [
        "bounce", ITEM_ID, "--reason-file", str(reason_file), "--agent", A,
    ]))
    assert "] From a file:" in comments_section(world.remote_show(ITEM))

    ok(run_cli(world.a, monkeypatch, [
        "bounce", ITEM_ID, "--reason-file", "-", "--agent", A,
    ], input="From stdin: still no acceptance criteria.\n"))
    assert "] From stdin: still no acceptance criteria." in comments_section(
        world.remote_show(ITEM)
    )


def test_bounce_takes_actor_from_env(world, monkeypatch) -> None:
    monkeypatch.setenv("YURTLE_AGENT", A)
    ok(bounce(world, monkeypatch, agent=None))
    assert frontmatter(world.remote_show(ITEM))["bounced_by"] == A


# --- Acceptance 3: what does and does not clear a bounce ------------------------------------


def _bounced_local(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Repo:
    repo = local_repo(tmp_path, monkeypatch, {"EXP-1": with_stamp(local_item("EXP-1"))})
    assert_unrepaired(repo, "EXP-1")
    return repo


def _edit(repo: Repo, item_id: str, change: Any) -> None:
    p = repo.path(item_id)
    before = p.read_bytes().decode("utf-8")
    after = change(before)
    assert after != before, "fixture: the edit changed nothing"
    p.write_bytes(after.encode("utf-8"))


NOT_REPAIRS = {
    "crlf": lambda t: t.replace("\n", "\r\n"),
    "trailing-whitespace": lambda t: t.replace(
        "A description long enough.\n", "A description long enough.   \t\n"
    ).replace("# Item EXP-1\n", "# Item EXP-1  \n"),
    "leading-and-trailing-blank-lines": lambda t: t.replace("---\n\n# Item", "---\n\n\n\n# Item")
    + "\n\n\n",
    "frontmatter-priority": lambda t: t.replace("priority: medium", "priority: high"),
    "frontmatter-tags-deps": lambda t: t.replace(
        "priority: medium\n", "priority: medium\ntags: [api]\ndepends_on: [EXP-2]\n"
    ),
    "frontmatter-title-key-only": lambda t: t.replace(
        'title: "Item EXP-1"', 'title: "Item EXP-1 renamed"'
    ),
    "history-block-appended": lambda t: t + HISTORY,
}


@pytest.mark.parametrize("change", sorted(NOT_REPAIRS))
def test_a3_edits_that_do_not_clear_the_bounce(tmp_path, monkeypatch, change) -> None:
    repo = local_repo(tmp_path, monkeypatch, {
        "EXP-1": with_stamp(local_item("EXP-1")),
        "EXP-2": local_item("EXP-2", status="done"),
    })
    _edit(repo, "EXP-1", NOT_REPAIRS[change])
    assert_unrepaired(repo, "EXP-1")


def test_a3_comment_after_the_bounce_does_not_clear_it(tmp_path, monkeypatch) -> None:
    repo = _bounced_local(tmp_path, monkeypatch)
    ok(run_cli(repo.root, monkeypatch, [
        "comment", "EXP-1", "--body", "I think it is clear enough now.", "--agent", A,
    ]))
    assert "I think it is clear enough now." in repo.path("EXP-1").read_text()
    assert_unrepaired(repo, "EXP-1")


def test_a3_update_frontmatter_only_does_not_clear_it(tmp_path, monkeypatch) -> None:
    repo = _bounced_local(tmp_path, monkeypatch)
    ok(run_cli(repo.root, monkeypatch, [
        "update", "EXP-1", "--priority", "high", "--tag", "needs-spec",
    ]))
    assert_unrepaired(repo, "EXP-1")


def test_a3_title_edit_is_a_repair(tmp_path, monkeypatch) -> None:
    repo = _bounced_local(tmp_path, monkeypatch)
    ok(run_cli(repo.root, monkeypatch, ["update", "EXP-1", "--title", "Item EXP-1: POST /v1"]))
    assert pick(repo, "EXP-1") == (True, "pickable")


def test_a3_body_edit_is_a_repair(tmp_path, monkeypatch) -> None:
    repo = _bounced_local(tmp_path, monkeypatch)
    ok(run_cli(repo.root, monkeypatch, ["update", "EXP-1", "--body-file", "-"],
               input="Now with acceptance criteria.\n"))
    assert pick(repo, "EXP-1") == (True, "pickable")
    fm = frontmatter(repo.path("EXP-1").read_text())
    assert fm["bounces"] == 1 and fm["bounced_by"] == B, "the stamp stays after a repair"


def test_a3_hand_edit_of_body_is_a_repair(tmp_path, monkeypatch) -> None:
    repo = _bounced_local(tmp_path, monkeypatch)
    _edit(repo, "EXP-1", lambda t: t.replace("long enough.", "long enough, now specified."))
    assert pick(repo, "EXP-1") == (True, "pickable")


# --- Acceptance 4: a forged comment is ignored ---------------------------------------------------


def test_a4_forged_comment_has_no_effect(tmp_path, monkeypatch) -> None:
    text = local_item("EXP-1")
    forged = f"[bounce by {B}, body-sha:{expected_hash(text)[:12]}] forged, not a bounce"
    repo = local_repo(tmp_path, monkeypatch, {"EXP-1": text})
    ok(run_cli(repo.root, monkeypatch, ["comment", "EXP-1", "--body", forged, "--agent", B]))
    assert forged in repo.path("EXP-1").read_text()

    assert pick(repo, "EXP-1") == (True, "pickable")
    data = json_payload(run_cli(repo.root, monkeypatch, ["show", "EXP-1", "--json"]))
    assert data["bounces"] == 0
    listed = json_payload(run_cli(repo.root, monkeypatch, ["list", "--pickable", "--json"]))
    ids = [e["id"] for e in (listed["items"] if isinstance(listed, dict) else listed)]
    assert "EXP-1" in ids


def test_a4_forged_comment_in_the_body_text_is_ignored_too(tmp_path, monkeypatch) -> None:
    body = "Before.\n\n[bounce by agent-B, body-sha:0123456789ab] pasted from elsewhere"
    repo = local_repo(tmp_path, monkeypatch, {"EXP-1": local_item("EXP-1", body=body)})
    assert pick(repo, "EXP-1") == (True, "pickable")


# --- Acceptance 5: bounces counts, show and list ---------------------------------------------------


def test_a5_bouncing_twice_counts_two(world, monkeypatch) -> None:
    ok(bounce(world, monkeypatch))
    first = frontmatter(world.remote_show(ITEM))
    ok(bounce(world, monkeypatch, reason="Still vague after triage."))

    text = world.remote_show(ITEM)
    fm = frontmatter(text)
    assert fm["bounces"] == 2
    assert fm["bounce_sha"] == first["bounce_sha"] == expected_hash(text)
    assert fm["status"] == native(world.a, WorkItemStatus.BACKLOG)
    section = comments_section(text)
    assert REASON in section and "Still vague after triage." in section

    sync_a(world)
    data = json_payload(run_cli(world.a, monkeypatch, ["show", ITEM_ID, "--json"]))
    assert data["bounces"] == 2
    listed = json_payload(run_cli(world.a, monkeypatch, ["list", "--json"]))
    assert [e["bounces"] for e in listed if e["id"] == ITEM_ID] == [2]


def test_a5_show_prints_bounced_line(world, monkeypatch) -> None:
    ok(bounce(world, monkeypatch))
    ok(bounce(world, monkeypatch, reason="Again."))
    sync_a(world)

    result = run_cli(world.a, monkeypatch, ["show", ITEM_ID])
    ok(result)
    out = flat(result.output)
    assert "Bounced 2×" in out, out
    assert f"last by {A}" in out, out


def test_a5_unbounced_items_carry_zero(tmp_path, monkeypatch) -> None:
    repo = local_repo(tmp_path, monkeypatch, {
        "EXP-1": local_item("EXP-1"), "EXP-2": with_stamp(local_item("EXP-2"), count=3),
    })
    data = json_payload(run_cli(repo.root, monkeypatch, ["show", "EXP-1", "--json"]))
    assert data["bounces"] == 0
    listed = {e["id"]: e for e in json_payload(run_cli(repo.root, monkeypatch, ["list", "--json"]))}
    assert listed["EXP-1"]["bounces"] == 0
    assert listed["EXP-2"]["bounces"] == 3
    shown = flat(run_cli(repo.root, monkeypatch, ["show", "EXP-1"]).output)
    assert "Bounced" not in shown, shown


# --- Acceptance 6: a sanctioned transition, not a forced one ---------------------------------------


def test_a6_bounce_from_in_progress_on_default_table(world, monkeypatch) -> None:
    seed(world, "in_progress", A)
    svc = service(world.a)
    legal = [canonical for canonical, _ in svc.next_statuses(svc.get_item(ITEM_ID))]
    assert "backlog" not in legal, f"precondition: in_progress -> backlog is legal: {legal}"
    base = world.remote_sha()

    ok(bounce(world, monkeypatch))

    tip = world.remote_sha()
    assert git(world.remote, "rev-list", f"{base}..{tip}").split() == [tip]
    assert commit_files(world.remote, tip) == [ITEM]
    text = world.remote_show(ITEM)
    fm = frontmatter(text)
    assert fm["status"] == native(world.a, WorkItemStatus.BACKLOG)
    assert not fm.get("assignee"), fm
    mine = nodes_by(text, A)
    node = [n for n in mine if re.search(r"kb:status\s+kb:backlog\b", n)]
    assert node, f'no history node with kb:by "{A}" and kb:status kb:backlog:\n{text}'
    assert BOUNCED.search(node[-1]), f"the bounce node lacks kb:bounced true:\n{node[-1]}"
    assert "kb:forcedMove" not in text, "a bounce recorded a forced move"
    assert "takenOverFrom" not in node[-1], "a bounce of one's own item recorded a take-over"


def test_a6_hdd_active_to_draft(tmp_path, monkeypatch) -> None:
    repo = make_repo(tmp_path, monkeypatch, {"H1.4": {"status": "active", "assignee": A}})
    head = repo.head()

    result = run_cli(repo.root, monkeypatch, [
        "bounce", "H1.4", "--reason", "Hypothesis is not falsifiable yet", "--agent", A,
    ])

    ok(result)
    assert "no remote" in flat(result.output).lower(), result.output
    new_head = repo.head()
    assert git(repo.root, "rev-list", f"{head}..{new_head}").split() == [new_head]
    text = repo.path("H1.4").read_text()
    fm = frontmatter(text)
    assert fm["status"] == "draft"
    assert not fm.get("assignee"), fm
    assert fm["bounces"] == 1 and fm["bounce_sha"] == expected_hash(text)
    assert any(BOUNCED.search(n) for n in nodes_by(text, A)), text
    assert "kb:forcedMove" not in text


def test_a6_bounce_is_exempt_from_wip(world, monkeypatch) -> None:
    folder = Path(ITEM).parent.as_posix()
    config = (
        'version: "2.0"\nboards:\n  - name: development\n    preset: nautical\n'
        "    path: kanban-work/\n    wip_limits:\n      backlog: 1\n"
        "default_board: development\n"
    )
    push_from_a(world, {
        ".kanban/config.yaml": config,
        f"{folder}/EXP-002-y.md": item_text("backlog", None, "EXP-002", "Y"),
        f"{folder}/EXP-003-z.md": item_text("ready", None, "EXP-003", "Z"),
        ITEM: item_text("in_progress", A),
    }, "board: backlog wip 1, full")
    # precondition: a plain (legal) move into the full backlog is refused on WIP
    moved = run_cli(world.a, monkeypatch, ["move", "EXP-003", "backlog", "--agent", A])
    assert moved.exit_code != 0 and "wip" in flat(moved.output).lower(), (
        f"precondition: backlog is not full: {flat(moved.output)}"
    )
    git(world.a, "reset", "--hard", f"origin/{world.default}")

    ok(bounce(world, monkeypatch))
    assert frontmatter(world.remote_show(ITEM))["status"] == native(
        world.a, WorkItemStatus.BACKLOG
    )


def test_a6_backlog_gates_still_apply(world, monkeypatch) -> None:
    config = KanbanConfig(
        theme="nautical",
        paths=PathConfig(
            root="kanban-work/",
            scan_paths=["kanban-work/expeditions/", "kanban-work/signals/"],
        ),
        gates={"* -> backlog": [{
            "id": "self_review", "check": "context.self_reviewed",
            "message": "Backlog gate 578 not satisfied",
        }]},
    )
    config.save(world.a / ".kanban" / "config.yaml")
    push_from_a(world, {}, "board: backlog gate")
    base = world.remote_sha()

    result = bounce(world, monkeypatch)

    assert result.exit_code == 1, flat(result.output)
    assert "Backlog gate 578 not satisfied" in flat(result.output)
    assert world.remote_sha() == base


# --- Acceptance 7: who may bounce ----------------------------------------------------------------


@pytest.mark.parametrize("status", ["ready", "in_progress"])
def test_a7_held_by_other_needs_take_over(world, monkeypatch, status) -> None:
    seed(world, status, B)
    base = world.remote_sha()
    before = snapshot(world.a)

    refused = bounce(world, monkeypatch)

    assert refused.exit_code == 1, flat(refused.output)
    assert re.search(r"held by agent-B", flat(refused.output), re.I), flat(refused.output)
    assert world.remote_sha() == base
    assert snapshot(world.a) == before

    ok(bounce(world, monkeypatch, "--take-over"))
    text = world.remote_show(ITEM)
    fm = frontmatter(text)
    assert not fm.get("assignee") and fm["bounces"] == 1, fm
    taken = [n for n in nodes_by(text, A) if f'kb:takenOverFrom "{B}"' in n]
    assert taken, f'no node with kb:by "{A}" recording kb:takenOverFrom "{B}":\n{text}'
    assert BOUNCED.search(taken[-1]), taken[-1]


def test_a7_held_by_self_needs_no_take_over(world, monkeypatch) -> None:
    seed(world, "in_progress", "Agent-a")  # same_actor: case ignored
    ok(bounce(world, monkeypatch))
    assert "takenOverFrom" not in world.remote_show(ITEM)


@pytest.mark.parametrize("take_over", [False, True])
def test_a7_done_item_is_refused(world, monkeypatch, take_over) -> None:
    seed(world, "done")
    base = world.remote_sha()

    result = bounce(world, monkeypatch, *(["--take-over"] if take_over else []))

    assert result.exit_code == 1, flat(result.output)
    assert world.remote_sha() == base


@pytest.mark.parametrize("item_id", ["H1.1", "H1.2"])  # hdd complete, abandoned
def test_a7_finished_hdd_item_is_refused(tmp_path, monkeypatch, item_id) -> None:
    repo = make_repo(tmp_path, monkeypatch, {
        "H1.1": {"status": "complete"}, "H1.2": {"status": "abandoned"},
    })
    head = repo.head()
    before = repo.path(item_id).read_bytes()

    result = run_cli(repo.root, monkeypatch, ["bounce", item_id, "--reason", "x", "--agent", A])

    assert result.exit_code == 1, flat(result.output)
    assert repo.head() == head
    assert repo.path(item_id).read_bytes() == before


def test_a7_no_actor_is_refused(world, monkeypatch) -> None:
    assert git(world.a, "config", "user.name").strip(), "A must have a git user.name"
    base = world.remote_sha()
    head = git(world.a, "rev-parse", "HEAD").strip()

    result = bounce(world, monkeypatch, agent=None)

    out = flat(result.output)
    assert result.exit_code == 1, out
    assert "--agent" in out and "YURTLE_AGENT" in out, out
    assert world.remote_sha() == base
    assert git(world.a, "rev-parse", "HEAD").strip() == head


@pytest.mark.parametrize(
    "argv,stdin",
    [
        (["bounce", ITEM_ID, "--agent", A], None),
        (["bounce", ITEM_ID, "--reason", "x", "--reason-file", "-", "--agent", A], "y\n"),
    ],
    ids=["no-reason", "both-reasons"],
)
def test_reason_is_required_and_single(world, monkeypatch, argv, stdin) -> None:
    base = world.remote_sha()
    result = run_cli(world.a, monkeypatch, argv, input=stdin)
    assert result.exit_code == 2, f"exit {result.exit_code}: {flat(result.output)}"
    assert "--reason" in flat(result.output), flat(result.output)
    assert world.remote_sha() == base


def test_empty_reason_is_refused(world, monkeypatch) -> None:
    base = world.remote_sha()
    result = bounce(world, monkeypatch, reason="   ")
    assert result.exit_code == 1, flat(result.output)
    assert world.remote_sha() == base


# --- Acceptance 8: bounce racing a claim ------------------------------------------------------------


def test_a8_bounce_loses_to_a_claim(world, monkeypatch, tmp_path) -> None:
    marker = install_hooks(world, tmp_path)
    base = world.remote_sha()

    def b_claims(attempt: int) -> None:
        if attempt == 0:
            out = claim(world.b, B)
            assert out.kind == "won", f"B's claim: {out.kind}: {out.message}"

    seen = seam_on_a(monkeypatch, world, b_claims)
    result = bounce(world, monkeypatch)

    out = flat(result.output)
    assert result.exit_code == 3, f"exit {result.exit_code}: {out}"
    assert re.search(r"lost to agent-B", out, re.I), out
    assert seen == [0], f"A pushed again after reading B's claim: {seen}"
    tip = world.remote_sha()
    assert git(world.remote, "rev-list", f"{base}..{tip}").split() == [tip], (
        "origin must hold B's claim and nothing else"
    )
    fm = frontmatter(world.remote_show(ITEM))
    assert fm["assignee"] == B and "bounce_sha" not in fm, fm
    assert fired(marker) == [], "the losing bounce fired hooks"


def test_a8_claim_loses_to_a_bounce(world, monkeypatch) -> None:
    """B's claim; A's bounce lands in B's seam. Reading e: one winner, the loser is
    non-zero (exit 3 is not pinned here: the bounced item has no holder)."""
    base = world.remote_sha()
    rec = Recorder(
        lambda attempt: ok(bounce(world, monkeypatch)) if attempt == 0 else None
    )

    out = claim(world.b, B, rec)

    assert out.kind in ("lost", "refused"), f"{out.kind}: {out.message}"
    assert out.exit_code != 0
    tip = world.remote_sha()
    assert git(world.remote, "rev-list", f"{base}..{tip}").split() == [tip], (
        "origin must hold A's bounce and nothing else"
    )
    fm = frontmatter(world.remote_show(ITEM))
    assert fm["bounces"] == 1 and not fm.get("assignee"), fm


def test_a8_winning_bounce_fires_status_change_once(world, monkeypatch, tmp_path) -> None:
    marker = install_hooks(world, tmp_path)
    ok(bounce(world, monkeypatch))
    changes = [line for line in fired(marker) if line.startswith("on_status_change")]
    assert len(changes) == 1, fired(marker)
    assert changes[0].startswith(f"on_status_change {ITEM_ID} backlog"), changes


# --- Acceptance 9: the agreement test (#575) ----------------------------------------------------------


def _agreement_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Repo:
    """EXP-1: ready, bounced, unrepaired, ranked first (it would lead every list).
    EXP-2: ready, never bounced. EXP-3: ready, bounced and since repaired."""
    stale = with_stamp(local_item("EXP-3"), sha="0" * 64, count=2)
    return local_repo(tmp_path, monkeypatch, {
        "EXP-1": with_stamp(local_item("EXP-1", priority="critical", rank=1)),
        "EXP-2": local_item("EXP-2"),
        "EXP-3": stale,
    })


def _ids(payload: Any) -> list[str]:
    payload = payload["items"] if isinstance(payload, dict) else payload
    return [e["id"] for e in payload]


def test_a9_pickable_reason_and_claim_agree(tmp_path, monkeypatch) -> None:
    repo = _agreement_repo(tmp_path, monkeypatch)
    reason = assert_unrepaired(repo, "EXP-1")
    assert pick(repo, "EXP-2") == (True, "pickable")
    assert pick(repo, "EXP-3") == (True, "pickable"), "a repaired item is pickable again"
    head = repo.head()

    out = repo.service().claim_item(
        "EXP-1", actor=A, sleep=lambda s: None, jitter=lambda lo, hi: 0.0
    )

    assert out.kind == "refused", out.message
    assert out.exit_code == 1
    assert reason in out.message, f"pickable says {reason!r}, claim says {out.message!r}"
    assert repo.head() == head


def test_a9_take_over_does_not_skip_the_bounce(tmp_path, monkeypatch) -> None:
    repo = local_repo(tmp_path, monkeypatch, {
        "EXP-1": with_stamp(local_item("EXP-1", assignee=B)),
    })
    head = repo.head()
    out = repo.service().claim_item(
        "EXP-1", actor=A, take_over=True, sleep=lambda s: None, jitter=lambda lo, hi: 0.0
    )
    assert out.kind == "refused", out.message
    assert UNREPAIRED in out.message, out.message
    assert repo.head() == head


def test_a9_next_and_list_pickable_skip_it(tmp_path, monkeypatch) -> None:
    repo = _agreement_repo(tmp_path, monkeypatch)

    listed = _ids(json_payload(run_cli(repo.root, monkeypatch, [
        "list", "--pickable", "--agent", A, "--json",
    ])))
    assert "EXP-1" not in listed and {"EXP-2", "EXP-3"} <= set(listed), listed

    got = json_payload(run_cli(repo.root, monkeypatch, ["next", "--agent", A, "--json"]))
    assert got["id"] != "EXP-1" and got["kind"] == "pick", got

    explained = run_cli(repo.root, monkeypatch, [
        "list", "--pickable", "--agent", A, "--explain",
    ])
    ok(explained)
    reason = pick(repo, "EXP-1")[1]
    assert reason in flat(explained.output), flat(explained.output)


def test_a9_claim_next_skips_it(tmp_path, monkeypatch) -> None:
    repo = _agreement_repo(tmp_path, monkeypatch)
    result = run_cli(repo.root, monkeypatch, ["claim", "--next", "--agent", A])
    ok(result)
    fm = frontmatter(repo.path("EXP-1").read_text())
    assert not fm.get("assignee"), "claim --next claimed the bounced item"


# --- Expected 5: validate ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key,stamp",
    [
        ("bounce_sha", {"sha": "not-a-hex-digest!"}),
        ("bounces", {"count": "two"}),
    ],
)
def test_validate_reports_a_malformed_stamp(tmp_path, monkeypatch, key, stamp) -> None:
    repo = local_repo(tmp_path, monkeypatch, {
        "EXP-1": with_stamp(local_item("EXP-1"), **stamp),
        "EXP-2": with_stamp(local_item("EXP-2")),
    })
    result = run_cli(repo.root, monkeypatch, ["validate", "--json"])
    assert result.exit_code == 1, result.output
    issues = json.loads(result.stdout)["issues"]
    mine = [i for i in issues if i.get("id") == "EXP-1" and key in i.get("message", "")]
    assert mine, f"no validate issue naming {key} for EXP-1: {issues}"
    assert not [i for i in issues if i.get("id") == "EXP-2"], (
        f"a well-formed stamp was reported: {issues}"
    )


def test_validate_accepts_a_well_formed_stamp(tmp_path, monkeypatch) -> None:
    local_repo(tmp_path, monkeypatch, {"EXP-1": with_stamp(local_item("EXP-1"))})
    result = CliRunner().invoke(main, ["validate", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["valid"] is True
