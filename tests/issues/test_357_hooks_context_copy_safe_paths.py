"""Issue #357 — follow-ups from the #347 review (PR #354).

1. `HookEngine.trigger()` set `context.repo_root` in place when it was None, so a
   caller reusing one `HookContext` across two engines kept the first engine's root.
   Decided: `trigger()` works on a copy; the caller's context is never mutated, and
   each engine's actions still run in its own repo.
2. A `log` path template such as `logs/{title}.jsonl` could render to `logs/../../x`
   and escape the repo. Decided: values substituted into a log path are path-safe —
   a `/` or `\\` inside a value becomes `_`, and a value that is exactly `.` or `..`
   becomes `_`. The config author's own path structure (absolute, relative, or with
   `..`) is kept as written.
3. The nested `create_item` *action* (a hook creating an item whose own on_create
   fires) was untested with the cwd elsewhere.
4. Shell templates already use `shlex.quote`; they stay as they are (control).

Helpers are reused from the #347 tests.

#369 later made a `:` in a substituted log-path value `_`, so a `{timestamp}` log
path uses the caller's timestamp in that path-safe form.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from tests.issues.test_347_hooks_repo_cwd import (
    LOG_DEFAULT,
    _assert_cwd_untouched,
    _hooks_doc,
    _setup,
)
from yurtle_kanban import HookEngine
from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.hooks import HookContext, HookEvent
from yurtle_kanban.service import KanbanService


def _ctx(**kwargs: str) -> HookContext:
    fields = {"item_id": "E-1", "item_type": "expedition", **kwargs}
    return HookContext(event=HookEvent.ITEM_CREATED, **fields)


def _files_under(root: Path) -> set[Path]:
    return {p.resolve() for p in root.rglob("*") if p.is_file()}


def _log_service(tmp_path: Path, path_template: str) -> tuple[Path, Path, KanbanService]:
    repo, elsewhere, _ = _setup(
        tmp_path, f"        - type: log\n          path: {path_template!r}\n"
    )
    return repo, elsewhere, KanbanService(KanbanConfig(), repo)


# --- 1. no mutation of the caller's context ---------------------------------------


def _engine(tmp_path: Path, name: str) -> tuple[Path, HookEngine]:
    root = tmp_path / name
    hooks = tmp_path / f"{name}-hooks.yurtle.md"
    root.mkdir()
    hooks.write_text(_hooks_doc(LOG_DEFAULT), encoding="utf-8")
    return root, HookEngine(hooks, repo_root=root)


def test_trigger_does_not_mutate_context_repo_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, engine = _engine(tmp_path, "repo")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    ctx = _ctx()
    engine.trigger(HookEvent.ITEM_CREATED, ctx)
    assert ctx.repo_root is None, "trigger() mutated the caller's HookContext"
    log = root / ".kanban" / "hooks.log"
    assert log.is_file(), "the action did not run in the engine's repo"
    assert '"item_id": "E-1"' in log.read_text(encoding="utf-8")
    _assert_cwd_untouched(elsewhere)


def test_one_context_two_engines_each_log_in_own_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root_a, engine_a = _engine(tmp_path, "repo-a")
    root_b, engine_b = _engine(tmp_path, "repo-b")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    ctx = _ctx()
    engine_a.trigger(HookEvent.ITEM_CREATED, ctx)
    engine_b.trigger(HookEvent.ITEM_CREATED, ctx)
    log_a = root_a / ".kanban" / "hooks.log"
    log_b = root_b / ".kanban" / "hooks.log"
    assert log_a.is_file()
    assert len(log_a.read_text(encoding="utf-8").splitlines()) == 1, (
        "engine B's action ran in engine A's repo"
    )
    assert log_b.is_file(), "engine B's log did not land in its own repo"
    _assert_cwd_untouched(elsewhere)


# --- 2. log-path templates are path-safe ------------------------------------------


def test_title_traversal_stays_inside_logs_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, elsewhere, service = _log_service(tmp_path, "logs/{title}.jsonl")
    monkeypatch.chdir(elsewhere)
    before = _files_under(tmp_path)
    service._hook_engine.trigger(HookEvent.ITEM_CREATED, _ctx(title="../../escape"))
    new = _files_under(tmp_path) - before
    logs_dir = (repo / "logs").resolve()
    outside = sorted(str(p) for p in new if logs_dir not in p.parents)
    assert outside == [], f"log path escaped repo/logs/: {outside}"
    written = [p for p in new if p.parent == logs_dir]
    assert len(written) == 1, f"expected one log file directly in repo/logs/, got {new}"
    assert "escape" in written[0].name
    _assert_cwd_untouched(elsewhere)


def test_item_id_with_slash_makes_no_subdirectory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, elsewhere, service = _log_service(tmp_path, "logs/{item_id}.jsonl")
    monkeypatch.chdir(elsewhere)
    service._hook_engine.trigger(HookEvent.ITEM_CREATED, _ctx(item_id="a/b"))
    logs = repo / "logs"
    assert not (logs / "a").exists(), "a `/` in item_id created a subdirectory"
    files = [p for p in logs.iterdir() if p.is_file()]
    assert len(files) == 1 and "b" in files[0].name, list(logs.rglob("*"))
    _assert_cwd_untouched(elsewhere)


def test_value_exactly_dotdot_stays_under_logs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, elsewhere, service = _log_service(tmp_path, "logs/{assignee}/x.jsonl")
    monkeypatch.chdir(elsewhere)
    before = _files_under(tmp_path)
    service._hook_engine.trigger(HookEvent.ITEM_CREATED, _ctx(assignee=".."))
    new = _files_under(tmp_path) - before
    logs_dir = (repo / "logs").resolve()
    assert len(new) == 1, new
    (written,) = new
    assert logs_dir in written.parents, f"`..` as a value escaped repo/logs/: {written}"
    assert not (repo / "x.jsonl").exists()
    _assert_cwd_untouched(elsewhere)


# --- 2. controls: the author's own path structure is kept -------------------------


def test_author_dotdot_in_template_is_honoured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, elsewhere, service = _log_service(tmp_path, "../outside/{item_id}.jsonl")
    monkeypatch.chdir(elsewhere)
    service._hook_engine.trigger(HookEvent.ITEM_CREATED, _ctx(item_id="EXP-1"))
    assert (tmp_path / "outside" / "EXP-1.jsonl").is_file()
    _assert_cwd_untouched(elsewhere)


def test_absolute_template_is_honoured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target_dir = tmp_path / "abs-logs"
    repo, elsewhere, service = _log_service(
        tmp_path, f"{target_dir.as_posix()}/{{item_id}}.jsonl"
    )
    monkeypatch.chdir(elsewhere)
    service._hook_engine.trigger(HookEvent.ITEM_CREATED, _ctx(item_id="EXP-1"))
    assert (target_dir / "EXP-1.jsonl").is_file()
    _assert_cwd_untouched(elsewhere)


@pytest.mark.parametrize(
    ("template", "fields", "expected"),
    [
        ("logs/{item_id}.jsonl", {"item_id": "EXP-1"}, "logs/EXP-1.jsonl"),
        ("logs/{title}.jsonl", {"title": "Some Title"}, "logs/Some Title.jsonl"),
    ],
    ids=["item_id", "title"],
)
def test_plain_values_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    template: str,
    fields: dict[str, str],
    expected: str,
) -> None:
    repo, elsewhere, service = _log_service(tmp_path, template)
    monkeypatch.chdir(elsewhere)
    service._hook_engine.trigger(HookEvent.ITEM_CREATED, _ctx(**fields))
    assert (repo / expected).is_file()


# --- 3. nested create_item action with the cwd elsewhere --------------------------

NESTED_DOC = (
    "---\n"
    "type: kanban-hooks\n"
    "id: hooks-357\n"
    "version: 1\n"
    "hooks:\n"
    "  on_create:\n"
    "    - item_types: [expedition]\n"
    "      actions:\n"
    "        - type: log\n"
    "        - type: create_item\n"
    "          item_type: chore\n"
    "          title: 'Follow-up for {item_id}'\n"
    "    - item_types: [chore]\n"
    "      actions:\n"
    "        - type: log\n"
    "---\n"
    "# Hooks 357\n"
)


def test_nested_create_item_action_logs_in_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, elsewhere, hooks = _setup(tmp_path, LOG_DEFAULT)
    hooks.write_text(NESTED_DOC, encoding="utf-8")
    service = KanbanService(KanbanConfig(), repo)
    monkeypatch.chdir(elsewhere)
    service._hook_engine.trigger(HookEvent.ITEM_CREATED, _ctx(item_id="E-1"))
    log = repo / ".kanban" / "hooks.log"
    assert log.is_file(), "no <repo>/.kanban/hooks.log"
    entries = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    types = [e["item_type"] for e in entries]
    assert "expedition" in types, f"parent hook entry missing: {entries}"
    assert "chore" in types, f"nested create_item's on_create entry missing: {entries}"
    chore = next(e for e in entries if e["item_type"] == "chore")
    assert chore["title"] == "Follow-up for E-1"
    _assert_cwd_untouched(elsewhere)


# --- 4. shell templates are unchanged (control) -----------------------------------


def test_shell_template_output_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, elsewhere, _ = _setup(
        tmp_path,
        "        - type: shell\n"
        "          command: printf '%s|%s' {item_id} {title} > out.txt\n",
    )
    service = KanbanService(KanbanConfig(), repo)
    monkeypatch.chdir(elsewhere)
    service._hook_engine.trigger(
        HookEvent.ITEM_CREATED, _ctx(item_id="a/b", title="../../escape; echo hi")
    )
    out = repo / "out.txt"
    assert out.is_file()
    assert out.read_text(encoding="utf-8") == "a/b|../../escape; echo hi"
    _assert_cwd_untouched(elsewhere)


# --- round 2: the copy keeps the caller's timestamp (and every other field) --------

FIXED_TS = "2020-01-02T03:04:05.000006+00:00"


def _fixed_ctx(**kwargs: str) -> HookContext:
    ctx = _ctx(**kwargs)
    ctx.timestamp = FIXED_TS
    return ctx


def _log_entries(log: Path) -> list[dict]:
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def test_logged_timestamp_is_callers_timestamp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, engine = _engine(tmp_path, "repo")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    ctx = _fixed_ctx(title="T", assignee="mini")
    ctx.metadata["extra"] = "kept"
    engine.trigger(HookEvent.ITEM_CREATED, ctx)
    (entry,) = _log_entries(root / ".kanban" / "hooks.log")
    assert entry["timestamp"] == FIXED_TS, "the action saw a new timestamp, not ctx's"
    assert entry == ctx.to_dict(), "the action saw a context that differs from ctx"
    assert ctx.timestamp == FIXED_TS and ctx.repo_root is None


def test_two_engines_log_the_same_callers_timestamp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root_a, engine_a = _engine(tmp_path, "repo-a")
    root_b, engine_b = _engine(tmp_path, "repo-b")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    ctx = _ctx()
    original = ctx.timestamp
    time.sleep(0.01)  # a fresh timestamp taken at trigger time would differ
    engine_a.trigger(HookEvent.ITEM_CREATED, ctx)
    time.sleep(0.01)
    engine_b.trigger(HookEvent.ITEM_CREATED, ctx)
    (a,) = _log_entries(root_a / ".kanban" / "hooks.log")
    (b,) = _log_entries(root_b / ".kanban" / "hooks.log")
    assert a["timestamp"] == original, "engine A logged a trigger-time timestamp"
    assert b["timestamp"] == original, "engine B logged a trigger-time timestamp"
    assert ctx.timestamp == original and ctx.repo_root is None


def test_timestamp_placeholder_in_log_path_is_callers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, elsewhere, service = _log_service(tmp_path, "logs/{timestamp}.jsonl")
    monkeypatch.chdir(elsewhere)
    service._hook_engine.trigger(HookEvent.ITEM_CREATED, _fixed_ctx())
    files = [p.name for p in (repo / "logs").iterdir()]
    # the caller's FIXED_TS, path-safe: each `:` in a substituted value becomes `_` (#369)
    assert files == ["2020-01-02T03_04_05.000006+00_00.jsonl"], files
    _assert_cwd_untouched(elsewhere)


def test_timestamp_placeholder_in_shell_is_callers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, elsewhere, _ = _setup(
        tmp_path, "        - type: shell\n          command: printf '%s' {timestamp} > ts.txt\n"
    )
    service = KanbanService(KanbanConfig(), repo)
    monkeypatch.chdir(elsewhere)
    service._hook_engine.trigger(HookEvent.ITEM_CREATED, _fixed_ctx())
    assert (repo / "ts.txt").read_text(encoding="utf-8") == FIXED_TS
