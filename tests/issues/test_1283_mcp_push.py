"""Issue #1283: the MCP write tools whose CLI twins push by default (#1279) push too.

Captain's ruling (2026-11-11), the issue's option (a): the MCP tools that write push
by default, like the CLI, with a ``push: false`` parameter to stay local.

Which MCP tools are in scope. The server offers five tools that write:

- ``kanban_move_item``    -> CLI ``move``    (pushes by default since #1279): IN.
- ``kanban_add_comment``  -> CLI ``comment`` (pushes by default since #1279): IN.
- ``kanban_create_item``  -> CLI ``create``  (``--push`` opt-in): OUT, a control.
- ``kanban_update_item``  -> CLI ``update``  (``--push`` opt-in): OUT, a control. Its
  ``related`` edit is not ``epic add``: the CLI's ``update --related`` stays opt-in.
- ``kanban_next_id``      -> its own ``sync_remote`` (default true), unchanged: OUT.

There is no MCP tool for ``rank`` or ``epic add`` / ``voyage add``, so nothing else.

Harness: #1279's and #1251's (the #585 ``World``: a bare origin, clone A under test
and rival clone B; races forced by wrapping ``KanbanService.sync_and_push`` with a
``Recorder`` seam, as tests/issues/test_1251_comment_push.py does). The MCP server is
``KanbanMCPServer(repo_root=A)`` called through ``handle_tool_call``, as the other
MCP tests call it.

Readings chosen by the test partner (the driver may challenge):

a. "Pushes by default" is #1279's reading (a): with no ``push`` argument (or
   ``push: null``, which #728 makes "omitted"), in a clone with an origin, origin gets
   exactly ONE new commit, touching only the item, with #1251's subject (``Move .. to
   ..`` / ``Add comment to ..``); A's clean default-branch checkout is fast-forwarded
   and stays clean. The reply is a success: ``success`` is true and it carries a
   ``message``; its other keys are not pinned.
b. The server is long-lived: after a pushed move, its own ``kanban_get_item`` reads
   the item as the fast-forwarded checkout has it (the status a fresh service reads),
   never a stale cached copy (#732's concern, carried).
c. A clean checkout on a non-default branch is untouched (HEAD, branch, index,
   status, every file's bytes), and the change still lands on origin's default branch.
d. A lost race is retried as the CLI's is: a rival's unrelated commit between A's
   fetch and A's push gives a second attempt (seams ``[0, 1]``), A's one commit lands
   on top of the rival's, and the reply is a success.
e. Origin's tree judges, as with the CLI: an item that only A's checkout has is
   refused (an ``error`` naming it, nothing pushed, A's HEAD unchanged).
f. ``push: false`` is the plain local path the tool has today: one local commit
   touching only the item, with the subject above; origin unchanged.
g. No ``origin`` remote: the CLI's plain path (#1279 reading d): one local commit,
   reply ``message`` ``Moved EXP-001 to in_progress`` / ``Added comment to EXP-001``
   (the CLI's plain output). With ``push: true`` and no remote the CLI's ``--push``
   gives ``local`` (exit 0, committed here): a success with one local commit.
h. Offline (origin unreachable): the CLI exits 4 with the Outcome's message. In
   MCP's error shape that is ``{"error": <the same message the service's *_push
   Outcome gives>, "exit_code": 4, ...}`` (so an agent can tell unreachable from
   refused, as the CLI's exit code does); nothing falls back to local (the checkout's
   snapshot is unchanged).
i. ``push`` must be a JSON boolean: ``"false"`` is refused with the server's existing
   boolean message (``push must be true or false (a JSON boolean)``), nothing written.
j. Schemas: both tools advertise ``push`` as ``{"type": "boolean", "default": true}``,
   not required. Controls: ``kanban_create_item`` / ``kanban_update_item`` called
   without ``push`` leave origin unchanged; if either advertises ``push``, its
   default is false.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.issues.test_574_claim import frontmatter, item_text, push_from_a
from tests.issues.test_574_sync_and_push import Recorder, commit_files, rival, snapshot
from tests.issues.test_585_create_push_loop import EXP_DIR, World, git
from yurtle_kanban import config as config_mod
from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.mcp.server import KanbanMCPServer
from yurtle_kanban.models import WorkItemStatus
from yurtle_kanban.service import KanbanService

ITEM_ID = "EXP-001"
ITEM = f"{EXP_DIR}/EXP-001-x.md"
AGENT = "agent-A"
TOOLS = ["kanban_move_item", "kanban_add_comment"]


# --- harness ---------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    monkeypatch.delenv("YURTLE_AGENT", raising=False)
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


@pytest.fixture
def world(tmp_path: Path) -> World:
    """Origin and both clones hold EXP-001 (ready, no comments)."""
    w = World(tmp_path)
    push_from_a(w, {ITEM: item_text("ready")}, "seed EXP-001")
    return w


def server(clone: Path) -> KanbanMCPServer:
    return KanbanMCPServer(repo_root=clone)


def service(clone: Path) -> KanbanService:
    return KanbanService(KanbanConfig.load(clone / ".kanban" / "config.yaml"), clone)


def args_for(tool: str, item_id: str = ITEM_ID, **extra: Any) -> dict[str, Any]:
    if tool == "kanban_move_item":
        base = {"item_id": item_id, "new_status": "in_progress", "agent": AGENT}
    else:
        base = {"item_id": item_id, "comment": "a note", "author": AGENT}
    return {**base, **extra}


def call(clone: Path, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    return server(clone).handle_tool_call(tool, args)


def head(clone: Path) -> str:
    return git(clone, "rev-parse", "HEAD").strip()


def subject(repo: Path, sha: str) -> str:
    return git(repo, "log", "-1", "--format=%s", sha).strip()


def assert_subject(tool: str, msg: str) -> None:
    if tool == "kanban_move_item":
        assert msg == f"Move {ITEM_ID} to in_progress", msg
    else:
        assert msg == f"Add comment to {ITEM_ID}", msg


def assert_changed(tool: str, text: str) -> None:
    if tool == "kanban_move_item":
        assert frontmatter(text)["status"] == "underway", frontmatter(text)
    else:
        assert f"### {AGENT} (" in text and "a note" in text, text


def assert_success(result: dict[str, Any]) -> None:
    assert "error" not in result, result
    assert result.get("success") is True, result
    assert isinstance(result.get("message"), str), result


def one_new_commit(repo: Path, base: str, tip: str) -> None:
    assert git(repo, "rev-list", f"{base}..{tip}").split() == [tip], (
        f"expected exactly one new commit {base}..{tip}"
    )


def seam_for(monkeypatch: pytest.MonkeyPatch, clone: Path, rec: Recorder) -> None:
    """Give `clone`'s `sync_and_push` calls `rec`'s seam (#1251's harness)."""
    original = KanbanService.sync_and_push
    target = clone.resolve()

    def wrapped(self: KanbanService, mutate: Any, **kw: Any) -> Any:
        if Path(self.repo_root).resolve() == target:
            kw.update(sleep=rec.sleep, jitter=rec.jitter, seam=rec.seam)
        return original(self, mutate, **kw)

    monkeypatch.setattr(KanbanService, "sync_and_push", wrapped)


# --- a. no push argument pushes one commit ------------------------------------------


@pytest.mark.parametrize("tool", TOOLS)
@pytest.mark.parametrize("push_arg", [{}, {"push": None}, {"push": True}],
                         ids=["omitted", "null", "true"])
def test_default_pushes_one_commit_and_fast_forwards(world, tool, push_arg) -> None:
    base = world.remote_sha()

    result = call(world.a, tool, args_for(tool, **push_arg))

    assert_success(result)
    tip = world.remote_sha()
    assert tip != base, f"{tool} did not push"
    one_new_commit(world.remote, base, tip)
    assert commit_files(world.remote, tip) == [ITEM]
    assert_subject(tool, subject(world.remote, tip))
    assert_changed(tool, world.remote_show(ITEM))
    assert head(world.a) == tip, "A's clean default-branch checkout was not fast-forwarded"
    assert (world.a / ITEM).read_text() == world.remote_show(ITEM)
    assert git(world.a, "status", "--porcelain", "--untracked-files=all") == ""


# --- b. the long-lived server reads its own pushed write ------------------------------------


def test_server_reads_the_pushed_move_back(world) -> None:
    srv = server(world.a)
    assert srv.handle_tool_call("kanban_get_item", {"item_id": ITEM_ID})["item"]["status"]

    result = srv.handle_tool_call("kanban_move_item", args_for("kanban_move_item"))

    assert_success(result)
    assert world.remote_sha() == head(world.a), "the move was not pushed"
    fresh = service(world.a).get_item(ITEM_ID)
    assert fresh is not None
    got = srv.handle_tool_call("kanban_get_item", {"item_id": ITEM_ID})
    assert got["item"]["status"] == fresh.to_dict()["status"], (got, fresh.to_dict())


# --- c. a clean non-default-branch checkout is untouched ---------------------------------


@pytest.mark.parametrize("tool", TOOLS)
def test_clean_feature_branch_untouched(world, tool) -> None:
    git(world.a, "checkout", "-b", "feature")
    before = snapshot(world.a)
    base = world.remote_sha()

    result = call(world.a, tool, args_for(tool))

    assert_success(result)
    tip = world.remote_sha()
    one_new_commit(world.remote, base, tip)
    assert commit_files(world.remote, tip) == [ITEM]
    assert_changed(tool, world.remote_show(ITEM))
    assert snapshot(world.a) == before, "A's feature-branch checkout changed"


# --- d. a lost race is retried ------------------------------------------------------------


@pytest.mark.parametrize("tool", TOOLS)
def test_lost_race_is_retried(world, monkeypatch, tool) -> None:
    git(world.a, "checkout", "-b", "feature")  # A's tree never moves: origin alone tells
    rec = Recorder(lambda attempt: rival(world) if attempt == 0 else None)
    seam_for(monkeypatch, world.a, rec)
    base = world.remote_sha()

    result = call(world.a, tool, args_for(tool))

    assert_success(result)
    assert rec.seams == [0, 1], f"{tool} did not retry after the lost race: {rec.seams}"
    commits = git(world.remote, "rev-list", f"{base}..{world.remote_sha()}").split()
    assert len(commits) == 2, commits  # the rival's, then A's on top
    tip = commits[0]
    assert commit_files(world.remote, tip) == [ITEM]
    assert_subject(tool, subject(world.remote, tip))
    assert "other-0.txt" in world.remote_files(), "the rival's commit was lost"
    assert_changed(tool, world.remote_show(ITEM))


# --- e. origin's tree judges ------------------------------------------------------------------


@pytest.mark.parametrize("tool", TOOLS)
def test_item_only_in_local_checkout_is_refused(world, tool) -> None:
    rel = f"{EXP_DIR}/EXP-006-six.md"
    (world.a / rel).write_text(item_text("ready", item_id="EXP-006", title="Six"))
    git(world.a, "add", rel)
    git(world.a, "commit", "-m", "EXP-006, local only")
    base = world.remote_sha()
    before = head(world.a)

    result = call(world.a, tool, args_for(tool, item_id="EXP-006"))

    assert "error" in result, result
    assert "EXP-006" in result["error"], result
    assert world.remote_sha() == base, "pushed"
    assert head(world.a) == before, "committed locally"


# --- f. push: false is the local path -----------------------------------------------------------


@pytest.mark.parametrize("tool", TOOLS)
def test_push_false_commits_locally_only(world, tool) -> None:
    base = world.remote_sha()
    before = head(world.a)

    result = call(world.a, tool, args_for(tool, push=False))

    assert_success(result)
    assert world.remote_sha() == base, f"{tool} push:false pushed"
    tip = head(world.a)
    one_new_commit(world.a, before, tip)
    assert commit_files(world.a, tip) == [ITEM]
    assert_subject(tool, subject(world.a, tip))
    assert_changed(tool, (world.a / ITEM).read_text())


# --- g. no origin remote ----------------------------------------------------------------------------


@pytest.mark.parametrize("tool,said", [
    ("kanban_move_item", f"Moved {ITEM_ID} to in_progress"),
    ("kanban_add_comment", f"Added comment to {ITEM_ID}"),
])
def test_no_origin_default_is_plain_local_commit(world, tool, said) -> None:
    git(world.a, "remote", "remove", "origin")
    before = head(world.a)

    result = call(world.a, tool, args_for(tool))

    assert_success(result)
    assert result["message"] == said, result
    tip = head(world.a)
    one_new_commit(world.a, before, tip)
    assert commit_files(world.a, tip) == [ITEM]
    assert_subject(tool, subject(world.a, tip))
    assert_changed(tool, (world.a / ITEM).read_text())


@pytest.mark.parametrize("tool", TOOLS)
def test_no_origin_push_true_commits_locally(world, tool) -> None:
    git(world.a, "remote", "remove", "origin")
    before = head(world.a)

    result = call(world.a, tool, args_for(tool, push=True))

    assert_success(result)
    tip = head(world.a)
    one_new_commit(world.a, before, tip)
    assert commit_files(world.a, tip) == [ITEM]
    assert_changed(tool, (world.a / ITEM).read_text())


# --- h. offline: the CLI's exit-4 refusal, in MCP's error shape ----------------------------------


def unreachable(world: World) -> None:
    git(world.a, "remote", "set-url", "origin", str(world.a.parent / "missing.git"))


def service_outcome(world: World, tool: str) -> Any:
    svc = service(world.a)
    if tool == "kanban_move_item":
        return svc.move_item_push(ITEM_ID, WorkItemStatus.from_string("in_progress"), actor=AGENT)
    return svc.add_comment_push(ITEM_ID, "a note", AGENT)


@pytest.mark.parametrize("tool", TOOLS)
def test_unreachable_origin_is_the_clis_exit_4_refusal(world, tool) -> None:
    unreachable(world)
    git(world.a, "checkout", "-b", "feature")
    before = snapshot(world.a)
    expected = service_outcome(world, tool)
    assert expected.kind == "unreachable" and int(expected.exit_code) == 4, expected
    assert snapshot(world.a) == before

    result = call(world.a, tool, args_for(tool))

    assert result.get("success") is not True, result
    assert result.get("error") == expected.message, result
    assert result.get("exit_code") == 4, result
    assert snapshot(world.a) == before, "an unreachable origin must not fall back to local"


@pytest.mark.parametrize("tool", TOOLS)
def test_unreachable_origin_on_main_does_not_commit_locally(world, tool) -> None:
    unreachable(world)
    before = snapshot(world.a)

    result = call(world.a, tool, args_for(tool))

    assert "error" in result, result
    assert snapshot(world.a) == before, "an unreachable origin must not fall back to local"


def test_control_unreachable_push_false_still_commits_locally(world) -> None:
    unreachable(world)
    before = head(world.a)

    result = call(world.a, "kanban_add_comment", args_for("kanban_add_comment", push=False))

    assert_success(result)
    one_new_commit(world.a, before, head(world.a))


# --- i. push is a JSON boolean ---------------------------------------------------------------------


@pytest.mark.parametrize("tool", TOOLS)
@pytest.mark.parametrize("bad", ["false", 0, "no"])
def test_push_non_boolean_is_refused(world, tool, bad) -> None:
    base = world.remote_sha()
    before = snapshot(world.a)

    result = call(world.a, tool, args_for(tool, push=bad))

    assert result.get("error") == "push must be true or false (a JSON boolean)", result
    assert world.remote_sha() == base
    assert snapshot(world.a) == before


# --- j. schemas, and the opt-in controls -------------------------------------------------------------


def tool_schema(name: str) -> dict[str, Any]:
    tools = {t["name"]: t for t in KanbanMCPServer(repo_root=Path.cwd()).get_tools()}
    return tools[name]["inputSchema"]


@pytest.mark.parametrize("tool", TOOLS)
def test_schema_advertises_push_default_true(tool) -> None:
    schema = tool_schema(tool)
    spec = schema["properties"].get("push")
    assert spec is not None, f"{tool} does not advertise push"
    assert spec.get("type") == "boolean", spec
    assert spec.get("default") is True, spec
    assert "push" not in schema.get("required", []), schema
    assert spec.get("description"), spec


@pytest.mark.parametrize("tool", ["kanban_create_item", "kanban_update_item"])
def test_control_opt_in_tools_push_default_is_not_true(tool) -> None:
    spec = tool_schema(tool)["properties"].get("push")
    assert spec is None or spec.get("default") is False, spec


@pytest.mark.parametrize("tool,args", [
    ("kanban_update_item", {"item_id": ITEM_ID, "title": "Retitled"}),
    ("kanban_create_item", {"item_type": "feature", "title": "A new one"}),
])
def test_control_opt_in_tools_do_not_push(world, tool, args) -> None:
    base = world.remote_sha()

    result = call(world.a, tool, args)

    assert "error" not in result, result
    assert world.remote_sha() == base, f"{tool} pushed without being asked"


def test_control_fixture_seeds_origin(world) -> None:
    assert frontmatter(world.remote_show(ITEM))["status"] == "ready"
    assert head(world.a) == world.remote_sha()
