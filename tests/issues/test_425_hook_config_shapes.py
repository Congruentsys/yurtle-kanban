"""Issue #425 — hook config-shape junk raises out of `HookEngine.trigger()`.

Follow-up to #417 (PR #423). `HookEngine._load_config` stores `frontmatter["hooks"]`
as-is, and `trigger()` / `_matching_hooks` assume every level has the right shape:
- `hooks: {on_create: [just-a-string]}` -> AttributeError in `_matching_hooks`;
- `hooks: {on_create: [{actions: 5}]}` -> TypeError ('int' object is not iterable);
- `hooks: {on_create: [{item_types: 5, actions: [{type: log}]}]}` -> TypeError;
- `actions: log` (a string) iterates characters -> three 'not a mapping' warnings.
`KanbanService` calls `trigger()` unwrapped (`_fire_create_hook`, `move_item`), so
these crash `create_item` and the CLI commands built on it.

Decided behaviour: hook config shapes are validated when the hooks file is loaded
(`HookEngine._load_config`); each bad part is dropped with ONE warning naming the
file and the event (and the field, where relevant):
- `hooks` not a mapping -> no hooks;
- an event value not a list (`on_create: 5`) -> that event is dropped;
- a hook definition not a mapping (`on_create: [just-a-string]`) -> that entry dropped;
- `actions` not a list (`actions: 5`, `actions: log`) -> that hook dropped;
- a filter of the wrong shape -> that hook dropped. The filter keys in
  `_matching_hooks` are `item_types` (a list) and `from` / `to` (a status string;
  here a non-null, non-string value is the wrong shape).
After any drop, the valid hooks in the same file still load and fire, and
`trigger()` never raises for these shapes — directly or via `create_item`.

Also pinned: the `_text(context.item_id)` guard in the action-failure warning
(#423) — an unprintable `item_id` with a `notify` action logs
`failed for <unprintable ...>` and does not raise.

Controls: valid configs load unchanged; the #330 non-mapping frontmatter cases
behave the same.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
import yaml

from tests.issues.test_388_junk_root_no_crash import (  # noqa: F401 (fixtures)
    _record_action_contexts,
    _warnings,
    warnings_log,
)
from tests.issues.test_417_hook_warnings_guarded import (
    _ctx,
    _trigger,
    _UnprintableId,
)
from yurtle_kanban import HookEngine
from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.hooks import HookContext, HookEvent
from yurtle_kanban.models import WorkItemType
from yurtle_kanban.service import KanbanService

# The file name deliberately avoids "hooks", so a warning that names the `hooks`
# field isn't satisfied by the path alone.
CONFIG_NAME = "cfg-425.yurtle.md"

VALID_HOOK = (
    "    - item_types: [expedition]\n"
    "      actions:\n"
    "        - type: log\n"
)


def _doc(hooks_yaml: str) -> str:
    """A hooks file whose `hooks:` value is `hooks_yaml` (indented under it)."""
    return (
        "---\n"
        "type: kanban-hooks\n"
        "id: hooks-425\n"
        "version: 1\n"
        f"hooks:{hooks_yaml}"
        "---\n"
        "# Hooks 425\n"
    )


def _write(tmp_path: Path, content: str) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    path = tmp_path / CONFIG_NAME
    path.write_text(content, encoding="utf-8")
    return path, repo


def _load(tmp_path: Path, content: str) -> tuple[HookEngine, Path]:
    path, repo = _write(tmp_path, content)
    return HookEngine(path, repo_root=repo), path


def _assert_one_warning(
    caplog: pytest.LogCaptureFixture, path: Path, *names: str
) -> str:
    warned = _warnings(caplog)
    assert len(warned) == 1, f"expected one load warning, got {warned!r}"
    for name in (path.name, *names):
        assert name in warned[0], f"warning doesn't name {name!r}: {warned[0]!r}"
    return warned[0]


# --- 1. `hooks` is not a mapping ------------------------------------------------------

HOOKS_NOT_MAPPING = [
    pytest.param(" 5\n", id="int"),
    pytest.param(" just text\n", id="str"),
    pytest.param("\n  - on_create\n", id="list"),
    pytest.param(
        "\n  - on_create:\n      - actions:\n          - type: log\n", id="list-of-maps"
    ),
]


@pytest.mark.parametrize("hooks_yaml", HOOKS_NOT_MAPPING)
def test_hooks_not_mapping_is_unconfigured_and_trigger_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hooks_yaml: str
) -> None:
    seen = _record_action_contexts(monkeypatch)
    engine, _ = _load(tmp_path, _doc(hooks_yaml))
    assert not engine.is_configured
    _trigger(engine, _ctx())
    assert seen == []


@pytest.mark.parametrize("hooks_yaml", HOOKS_NOT_MAPPING)
def test_hooks_not_mapping_one_warning(
    tmp_path: Path,
    warnings_log: pytest.LogCaptureFixture,  # noqa: F811
    hooks_yaml: str,
) -> None:
    engine, path = _load(tmp_path, _doc(hooks_yaml))
    _trigger(engine, _ctx())
    _assert_one_warning(warnings_log, path, "hooks")


# --- 2. an event value is not a list --------------------------------------------------

EVENT_NOT_LIST = [
    pytest.param("5", id="int"),
    pytest.param("just-text", id="str"),
    pytest.param("{actions: [{type: log}]}", id="mapping"),
]


def _event_doc(bad_value: str) -> str:
    # the bad on_create plus a valid hook on another event
    return _doc(
        "\n"
        f"  on_create: {bad_value}\n"
        "  on_status_change:\n"
        f"{VALID_HOOK}"
    )


def _status_ctx() -> HookContext:
    return HookContext(
        event=HookEvent.STATUS_CHANGE,
        item_id="E-1",
        item_type="expedition",
        old_status="backlog",
        new_status="in_progress",
    )


@pytest.mark.parametrize("bad_value", EVENT_NOT_LIST)
def test_event_not_list_dropped_others_fire(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad_value: str
) -> None:
    seen = _record_action_contexts(monkeypatch)
    engine, _ = _load(tmp_path, _event_doc(bad_value))
    assert engine.is_configured  # the valid on_status_change hook loaded
    _trigger(engine, _ctx())  # on_create: must not raise, nothing fires
    assert seen == [], f"the dropped on_create fired {len(seen)} action(s)"
    engine.trigger(HookEvent.STATUS_CHANGE, _status_ctx())
    assert len(seen) == 1, f"the valid on_status_change hook ran {len(seen)} times"
    assert engine._depth == 0


@pytest.mark.parametrize("bad_value", EVENT_NOT_LIST)
def test_event_not_list_one_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    warnings_log: pytest.LogCaptureFixture,  # noqa: F811
    bad_value: str,
) -> None:
    _record_action_contexts(monkeypatch)
    engine, path = _load(tmp_path, _event_doc(bad_value))
    _trigger(engine, _ctx())
    _assert_one_warning(warnings_log, path, "on_create")


# --- 3. a hook definition is not a mapping --------------------------------------------

HOOK_DEF_NOT_MAPPING = [
    pytest.param("just-a-string", id="str"),
    pytest.param("42", id="int"),
    pytest.param("[log]", id="list"),
    pytest.param("null", id="none"),
]


def _entry_doc(bad_entry: str) -> str:
    return _doc(
        "\n"
        "  on_create:\n"
        f"    - {bad_entry}\n"
        f"{VALID_HOOK}"
    )


@pytest.mark.parametrize("bad_entry", HOOK_DEF_NOT_MAPPING)
def test_hook_def_not_mapping_dropped_others_fire(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad_entry: str
) -> None:
    seen = _record_action_contexts(monkeypatch)
    engine, _ = _load(tmp_path, _entry_doc(bad_entry))
    _trigger(engine, _ctx())  # must not raise
    assert len(seen) == 1, f"the valid hook ran {len(seen)} times, want 1"
    assert engine._depth == 0


@pytest.mark.parametrize("bad_entry", HOOK_DEF_NOT_MAPPING)
def test_hook_def_not_mapping_one_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    warnings_log: pytest.LogCaptureFixture,  # noqa: F811
    bad_entry: str,
) -> None:
    _record_action_contexts(monkeypatch)
    engine, path = _load(tmp_path, _entry_doc(bad_entry))
    _trigger(engine, _ctx())
    _assert_one_warning(warnings_log, path, "on_create")


# --- 4. `actions` is not a list -------------------------------------------------------

ACTIONS_NOT_LIST = [
    pytest.param("5", id="int"),
    pytest.param("log", id="str"),
    pytest.param("{type: log}", id="mapping"),
]


def _field_doc(field_yaml: str) -> str:
    """on_create: a hook carrying `field_yaml` (with its own log action unless the
    field is `actions`), then the valid hook."""
    actions = "" if field_yaml.startswith("actions:") else "      actions:\n        - type: log\n"
    return _doc(
        "\n"
        "  on_create:\n"
        f"    - {field_yaml}\n"
        f"{actions}"
        f"{VALID_HOOK}"
    )


@pytest.mark.parametrize("bad_value", ACTIONS_NOT_LIST)
def test_actions_not_list_dropped_others_fire(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad_value: str
) -> None:
    seen = _record_action_contexts(monkeypatch)
    engine, _ = _load(tmp_path, _field_doc(f"actions: {bad_value}"))
    _trigger(engine, _ctx())  # must not raise
    assert len(seen) == 1, f"log actions ran {len(seen)} times, want 1 (the valid hook)"
    assert engine._depth == 0


@pytest.mark.parametrize("bad_value", ACTIONS_NOT_LIST)
def test_actions_not_list_one_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    warnings_log: pytest.LogCaptureFixture,  # noqa: F811
    bad_value: str,
) -> None:
    _record_action_contexts(monkeypatch)
    engine, path = _load(tmp_path, _field_doc(f"actions: {bad_value}"))
    _trigger(engine, _ctx())  # a str is one warning at load, not one per character
    _assert_one_warning(warnings_log, path, "on_create", "actions")


def test_actions_null_trigger_safe_others_fire(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`actions: null` (dropped or treated as no actions): never raises."""
    seen = _record_action_contexts(monkeypatch)
    engine, _ = _load(tmp_path, _field_doc("actions: null"))
    _trigger(engine, _ctx())
    assert len(seen) == 1


# --- 5. a filter of the wrong shape ---------------------------------------------------

BAD_FILTERS = [
    pytest.param("item_types", "5", id="item_types-int"),
    pytest.param("item_types", "expedition", id="item_types-str"),
    pytest.param("item_types", "{expedition: 1}", id="item_types-mapping"),
    pytest.param("from", "5", id="from-int"),
    pytest.param("from", "[backlog, ready]", id="from-list"),
    pytest.param("from", "{backlog: 1}", id="from-mapping"),
    pytest.param("to", "5", id="to-int"),
    pytest.param("to", "[in_progress, done]", id="to-list"),
    pytest.param("to", "{in_progress: 1}", id="to-mapping"),
]


@pytest.mark.parametrize(("key", "bad_value"), BAD_FILTERS)
def test_bad_filter_dropped_others_fire(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, key: str, bad_value: str
) -> None:
    seen = _record_action_contexts(monkeypatch)
    engine, _ = _load(tmp_path, _field_doc(f"{key}: {bad_value}"))
    _trigger(engine, _ctx())  # must not raise
    assert len(seen) == 1, f"log actions ran {len(seen)} times, want 1 (the valid hook)"
    assert engine._depth == 0


@pytest.mark.parametrize(("key", "bad_value"), BAD_FILTERS)
def test_bad_filter_one_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    warnings_log: pytest.LogCaptureFixture,  # noqa: F811
    key: str,
    bad_value: str,
) -> None:
    _record_action_contexts(monkeypatch)
    engine, path = _load(tmp_path, _field_doc(f"{key}: {bad_value}"))
    _trigger(engine, _ctx())
    _assert_one_warning(warnings_log, path, "on_create", key)


# --- 6. the issue's repros, verbatim: trigger() never raises --------------------------

ISSUE_REPROS = [
    pytest.param("{on_create: [just-a-string]}", id="hook-def-str"),
    pytest.param("{on_create: [{actions: 5}]}", id="actions-int"),
    pytest.param(
        "{on_create: [{item_types: 5, actions: [{type: log}]}]}", id="item_types-int"
    ),
    pytest.param("{on_create: [{actions: log}]}", id="actions-str"),
]


@pytest.mark.parametrize("hooks_yaml", ISSUE_REPROS)
def test_issue_repro_trigger_does_not_raise(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    warnings_log: pytest.LogCaptureFixture,  # noqa: F811
    hooks_yaml: str,
) -> None:
    seen = _record_action_contexts(monkeypatch)
    path, repo = _write(tmp_path, _doc(f" {hooks_yaml}\n"))
    engine = HookEngine(path, repo_root=repo)
    _trigger(
        engine,
        HookContext(event=HookEvent.ITEM_CREATED, item_id="E-1", item_type="expedition"),
    )
    assert seen == []
    assert engine._depth == 0
    warned = _warnings(warnings_log)
    assert len(warned) == 1, f"expected one warning, got {warned!r}"


# --- 7. public path: KanbanService.create_item ----------------------------------------

SERVICE_CASES = [
    *ISSUE_REPROS,
    pytest.param(" 5", id="hooks-int"),
    pytest.param("{on_create: 5}", id="event-int"),
    pytest.param("{on_create: [{from: [a, b], actions: [{type: log}]}]}", id="from-list"),
]


def _service_repo(tmp_path: Path, hooks_value: str, extra_hook: str = "") -> Path:
    repo = tmp_path / "repo"
    hooks = repo / ".kanban" / "hooks" / "kanban-hooks.yurtle.md"
    hooks.parent.mkdir(parents=True)
    hooks.write_text(_doc(f" {hooks_value.strip()}\n{extra_hook}"), encoding="utf-8")
    return repo


@pytest.mark.parametrize("hooks_yaml", SERVICE_CASES)
def test_create_item_does_not_raise(tmp_path: Path, hooks_yaml: str) -> None:
    repo = _service_repo(tmp_path, hooks_yaml)
    service = KanbanService(KanbanConfig(), repo)
    item = service.create_item(WorkItemType.FEATURE, "Hooked item")
    assert item.file_path is not None and Path(item.file_path).is_file()


def test_create_item_bad_hook_dropped_valid_hook_fires(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    hooks = repo / ".kanban" / "hooks" / "kanban-hooks.yurtle.md"
    hooks.parent.mkdir(parents=True)
    hooks.write_text(
        _doc(
            "\n"
            "  on_create:\n"
            "    - just-a-string\n"
            "    - item_types: 5\n"
            "      actions:\n"
            "        - type: log\n"
            "    - actions: log\n"
            "    - item_types: [feature]\n"
            "      actions:\n"
            "        - type: log\n"
        ),
        encoding="utf-8",
    )
    service = KanbanService(KanbanConfig(), repo)
    item = service.create_item(WorkItemType.FEATURE, "Hooked item")
    log = repo / ".kanban" / "hooks.log"
    assert log.is_file(), "the valid hook didn't fire"
    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 and item.id in lines[0], lines


# --- 8. the `_text(context.item_id)` guard in the action-failure warning ---------------

NOTIFY_DOC = _doc(
    "\n"
    "  on_create:\n"
    "    - item_types: [expedition]\n"
    "      actions:\n"
    "        - type: notify\n"
)


def test_unprintable_item_id_notify_failure_warning(
    tmp_path: Path,
    warnings_log: pytest.LogCaptureFixture,  # noqa: F811
) -> None:
    """notify renders `{context}` via json.dumps, which fails on the unprintable id;
    the failure warning must format that id guarded (#423)."""
    engine, _ = _load(tmp_path, NOTIFY_DOC)
    assert engine.is_configured
    _trigger(engine, _ctx(_UnprintableId()))
    assert engine._depth == 0
    warned = _warnings(warnings_log)
    assert len(warned) == 1, f"expected one warning, got {warned!r}"
    assert "notify" in warned[0]
    assert "failed for <unprintable _UnprintableId>" in warned[0], warned[0]


# --- controls -------------------------------------------------------------------------

VALID_HOOKS_YAML = (
    "\n"
    "  on_create:\n"
    "    - item_types: [expedition, chore]\n"
    "      actions:\n"
    "        - type: log\n"
    "        - type: log\n"
    "          path: logs/{item_id}.jsonl\n"
    "    - actions:\n"
    "        - type: log\n"
    "  on_status_change:\n"
    "    - from: backlog\n"
    "      to: in_progress\n"
    "      item_types: [expedition]\n"
    "      actions:\n"
    "        - type: log\n"
    "  on_blocked:\n"
    "    - actions:\n"
    "        - type: frobnicate\n"
)


def test_control_valid_config_loads_unchanged(
    tmp_path: Path,
    warnings_log: pytest.LogCaptureFixture,  # noqa: F811
) -> None:
    content = _doc(VALID_HOOKS_YAML)
    engine, _ = _load(tmp_path, content)
    expected = yaml.safe_load(content.split("---\n")[1])["hooks"]
    assert engine._hooks_config == expected
    assert _warnings(warnings_log) == []


def test_control_valid_config_fires(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    warnings_log: pytest.LogCaptureFixture,  # noqa: F811
) -> None:
    seen = _record_action_contexts(monkeypatch)
    engine, _ = _load(tmp_path, _doc(VALID_HOOKS_YAML))
    _trigger(engine, _ctx())  # both on_create hooks: 3 log actions
    assert len(seen) == 3
    engine.trigger(HookEvent.STATUS_CHANGE, _status_ctx())
    assert len(seen) == 4
    other = _status_ctx()
    other.old_status = "ready"  # `from` filter excludes it
    engine.trigger(HookEvent.STATUS_CHANGE, other)
    assert len(seen) == 4
    assert _warnings(warnings_log) == []


def test_control_hooks_null_is_unconfigured_silently(
    tmp_path: Path,
    warnings_log: pytest.LogCaptureFixture,  # noqa: F811
) -> None:
    engine, _ = _load(tmp_path, _doc(" null\n"))
    assert not engine.is_configured
    _trigger(engine, _ctx())
    assert _warnings(warnings_log) == []


NON_MAPPING_FRONTMATTER = {
    "list": "---\n- a\n- b\n---\n\n# Body\n",
    "list-of-maps": "---\n- type: kanban-workflow\n  applies_to: feature\n---\n\n# Body\n",
    "str": "---\njust text\n---\n\n# Body\n",
    "int": "---\n5\n---\n\n# Body\n",
}


@pytest.mark.parametrize("case", list(NON_MAPPING_FRONTMATTER))
def test_control_330_non_mapping_frontmatter_unchanged(
    tmp_path: Path, case: str, caplog: pytest.LogCaptureFixture
) -> None:
    """#330: non-mapping frontmatter is still 'no hooks', with no warning at all."""
    path, repo = _write(tmp_path, NON_MAPPING_FRONTMATTER[case])
    with caplog.at_level(logging.DEBUG):
        engine = HookEngine(path, repo_root=repo)
        _trigger(engine, _ctx())
    assert not engine.is_configured
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []
