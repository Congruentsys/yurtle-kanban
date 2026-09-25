"""
Hook engine for event-driven kanban automation.

Fires configurable actions when kanban events occur (item creation,
status changes, blocking, etc.). Config is loaded from a Yurtle file
(.kanban/hooks/kanban-hooks.yurtle.md) with YAML frontmatter.

Actions are best-effort: failures are logged but never fail the
kanban operation.
"""

from __future__ import annotations

import json
import shlex
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

from ._logging import get_logger

logger = get_logger("yurtle-kanban.hooks")  # escapes control characters (#215)


# ─── Events ────────────────────────────────────────────────────────────────


class HookEvent(Enum):
    """Events that can trigger hooks."""

    ITEM_CREATED = "on_create"
    STATUS_CHANGE = "on_status_change"
    ASSIGNED = "on_assign"
    BLOCKED = "on_blocked"
    STALE_DETECTED = "on_stale"
    WIP_EXCEEDED = "on_wip_exceeded"


# ─── Context ───────────────────────────────────────────────────────────────


@dataclass
class HookContext:
    """Context passed to hook actions.

    Contains all information about the kanban event that triggered
    the hook. Used for template variable substitution and as the
    payload for NATS/log/shell actions.
    """

    event: HookEvent
    item_id: str
    item_type: str  # expedition, chore, idea, hypothesis, etc.
    title: str = ""
    old_status: str | None = None
    new_status: str | None = None
    assignee: str | None = None
    forced: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default="", init=False)
    # where relative paths and subprocesses resolve: the engine's repo, when it has
    # one; None keeps the process cwd (#347). Not part of the payload.
    repo_root: Path | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict."""
        return {
            **self.metadata,
            "event": self.event.value,
            "item_id": self.item_id,
            "item_type": self.item_type,
            "title": self.title,
            "old_status": self.old_status,
            "new_status": self.new_status,
            "assignee": self.assignee,
            "forced": self.forced,
            "timestamp": self.timestamp,
        }

    def render_path(self, template: str) -> str:
        """`render_template` for a file path: each substituted value is made
        path-safe (a `/` or `\\` becomes `_`, a whole `.` or `..` becomes `_`), so
        item data can't add directories or climb out; the template's own structure,
        `..` included, is the config author's and stays as written (#357)."""
        return self.render_template(template, _path_safe)

    def render_template(
        self, template: str, transform: Callable[[str], str] | None = None
    ) -> str:
        """Replace {var} placeholders with context values (each passed through
        `transform` when given)."""
        replacements = {
            "item_id": self.item_id,
            "item_type": self.item_type,
            "title": self.title,
            "event": self.event.value,
            "old_status": self.old_status or "",
            "new_status": self.new_status or "",
            "assignee": self.assignee or "",
            "timestamp": self.timestamp,
            "context": json.dumps(self.to_dict()),
        }
        result = template
        for key, value in replacements.items():
            text = str(value)
            result = result.replace(f"{{{key}}}", transform(text) if transform else text)
        return result


def _path_safe(value: str) -> str:
    """One substituted value, safe inside a path: no separators, never `.`/`..`
    (#357), no `:` (a Windows drive), never empty (a vanished segment) (#369)."""
    value = value.replace("/", "_").replace("\\", "_").replace(":", "_")
    return "_" if value in ("", ".", "..") else value


def _clean_hooks(raw: Any, path: Path) -> dict[str, list[dict]]:
    """The `hooks:` mapping with every part of the wrong shape dropped, one warning
    each, naming the file, the event and the field (#425)."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        logger.warning(f"{path}: `hooks` is not a mapping ({type(raw).__name__}); ignored")
        return {}
    cleaned: dict[str, list[dict]] = {}
    known = [e.value for e in HookEvent]
    for event, hook_list in raw.items():
        if event not in known:
            # a typo like `on_created` can never fire: say so (#432)
            logger.warning(
                f"{path}: `{_text(event)}` is not a hook event "
                f"(known: {', '.join(known)}); ignored"
            )
            continue
        if hook_list is None:
            continue
        if not isinstance(hook_list, list):
            logger.warning(
                f"{path}: hooks for `{_text(event)}` are not a list "
                f"({type(hook_list).__name__}); ignored"
            )
            continue
        kept = []
        for n, hook_def in enumerate(hook_list, 1):
            problem = _hook_problem(hook_def)
            if problem:
                logger.warning(f"{path}: `{_text(event)}` hook {n}: {problem}; ignored")
                continue
            kept.append(hook_def)
        cleaned[event] = kept
    return cleaned


def _hook_problem(hook_def: Any) -> str | None:
    """Why one hook definition can't be used, or None."""
    if not isinstance(hook_def, dict):
        return f"is not a mapping ({type(hook_def).__name__})"
    if isinstance(hook_def.get("item_types"), str):
        # `item_types: expedition` means the one type, as it always worked (#432)
        hook_def["item_types"] = [hook_def["item_types"]]
    for key in ("actions", "item_types"):
        value = hook_def.get(key)
        if value is not None and not isinstance(value, list):
            return f"`{key}` is not a list ({type(value).__name__})"
    for key in ("from", "to"):
        value = hook_def.get(key)
        if isinstance(value, bool):
            # YAML reads `yes`/`no`/`on`/`off` as booleans (#432)
            return f"`{key}` is a boolean ({value}); quote the status name, e.g. \"on\""
        if value is not None and not isinstance(value, str):
            return f"`{key}` is not a status name ({type(value).__name__})"
    return None


def _text(value: object) -> str:
    """`str(value)`, or its type name when even that raises (#417)."""
    try:
        return str(value)
    except Exception:
        return f"<unprintable {type(value).__name__}>"


def _describe(value: object) -> str:
    """`repr(value)`, or its type name when even that raises: a warning about a bad
    value must not itself crash (#408)."""
    try:
        return repr(value)
    except Exception:
        return f"<unprintable {type(value).__name__}>"


# ─── Engine ────────────────────────────────────────────────────────────────


class HookEngine:
    """Loads hook config from a Yurtle file and executes matching actions.

    Config is read from YAML frontmatter (the ``hooks:`` key).
    TTL blocks in the Yurtle file are ignored — they exist for
    graph queries by santiago-bosun.
    """

    _MAX_HOOK_DEPTH = 3

    def __init__(
        self, config_path: Path | str | None = None, repo_root: Path | str | None = None
    ):
        # actions resolve relative paths and run subprocesses here, not in the cwd
        # (#347); a str root or config path works like a Path (#348, #359)
        self._repo_root = Path(repo_root) if repo_root is not None else None
        self._hooks_config: dict[str, list[dict]] = {}
        self._callbacks: dict[str, Callable] = {}
        self._depth: int = 0
        config_path = Path(config_path) if config_path else None
        if config_path and config_path.exists():
            self._load_config(config_path)

    def set_callback(self, name: str, fn: Callable) -> None:
        """Register a callback for actions that need service access.

        Args:
            name: Callback name (e.g., "create_item").
            fn: Callable to invoke when the action fires.
        """
        self._callbacks[name] = fn

    @property
    def is_configured(self) -> bool:
        """Whether any hooks are loaded."""
        return bool(self._hooks_config)

    def _load_config(self, path: Path) -> None:
        """Load hook definitions from Yurtle file YAML frontmatter."""
        try:
            content = path.read_text(encoding="utf-8")
            frontmatter = _extract_frontmatter(content)
            # a bad shape is dropped here with a warning, so trigger() and
            # _matching_hooks only ever see lists of mappings (#425)
            self._hooks_config = _clean_hooks(frontmatter.get("hooks"), path)
            if self._hooks_config:
                hook_count = sum(len(v) for v in self._hooks_config.values())
                logger.info(f"Loaded {hook_count} hook(s) from {path}")
        except Exception as e:
            logger.warning(f"Failed to load hooks config from {path}: {e}")

    def trigger(self, event: HookEvent, context: HookContext) -> None:
        """Execute all hooks matching this event and context.

        Includes a recursion guard: if hook actions trigger further events
        (e.g., create_item fires on_create), depth is tracked and execution
        stops at ``_MAX_HOOK_DEPTH`` to prevent infinite loops.
        """
        if not self._hooks_config:
            return

        if self._depth >= self._MAX_HOOK_DEPTH:
            logger.warning(
                f"Hook depth limit ({self._MAX_HOOK_DEPTH}) reached "
                f"— skipping {event.value} for {_text(context.item_id)}"
            )
            return

        self._depth += 1
        try:
            root = context.repo_root
            if not isinstance(root, Path):
                # a copy: the caller's context is never changed, so one reused
                # across engines runs in each engine's own repo (#357); actions
                # always see a Path, even for a str root set by the caller (#375)
                if root is None:
                    root = self._repo_root
                else:
                    try:
                        root = Path(root)
                    except Exception:
                        # not a path at all (or a PathLike that raises): a hook never
                        # crashes its caller (#388, #399)
                        logger.warning(
                            f"hook context repo root {_describe(root)} is not a path; "
                            "using the engine's"
                        )
                        root = self._repo_root
                timestamp = context.timestamp
                context = replace(context, repo_root=root)
                # replace() re-runs __post_init__: keep the event's own time (#357)
                context.timestamp = timestamp
            matched = self._matching_hooks(event, context)
            for hook_def in matched:
                actions = hook_def.get("actions") or []
                for action in actions:
                    if not isinstance(action, dict):
                        # a bare string or number in `actions:` isn't an action (#417)
                        logger.warning(
                            f"Hook action {_describe(action)} is not a mapping; skipped"
                        )
                        continue
                    try:
                        _execute_action(action, context, self._callbacks)
                    except Exception as e:
                        # every value here is caller-supplied: format it guarded (#417)
                        logger.warning(
                            f"Hook action {_text(action.get('type', '?'))} failed "
                            f"for {_text(context.item_id)}: {_text(e)}"
                        )
        finally:
            self._depth -= 1

    def _matching_hooks(
        self, event: HookEvent, context: HookContext
    ) -> list[dict]:
        """Return hook definitions that match the event and context."""
        hook_list = self._hooks_config.get(event.value, [])
        matched = []

        for hook_def in hook_list:
            # Filter by item_types (if specified)
            item_types = hook_def.get("item_types")
            if item_types and context.item_type not in item_types:
                continue

            # Filter by from/to status (for on_status_change)
            from_status = hook_def.get("from")
            if from_status and context.old_status != from_status:
                continue

            to_status = hook_def.get("to")
            if to_status and context.new_status != to_status:
                continue

            matched.append(hook_def)

        return matched


# ─── Actions ───────────────────────────────────────────────────────────────


def _execute_action(
    action: dict, context: HookContext, callbacks: dict[str, Callable] | None = None
) -> None:
    """Dispatch to the appropriate action handler."""
    action_type = action.get("type", "")

    if action_type == "nats_publish":
        _action_nats_publish(action, context)
    elif action_type == "log":
        _action_log(action, context)
    elif action_type == "shell":
        _action_shell(action, context)
    elif action_type == "create_item":
        _action_create_item(action, context, callbacks or {})
    elif action_type == "notify":
        _action_notify(action, context)
    else:
        logger.warning(f"Unknown action type: {action_type}")


def _action_nats_publish(action: dict, context: HookContext) -> None:
    """Publish event to a NATS subject via the ``nats`` CLI."""
    subject = action.get("subject", f"ship.kanban.{context.event.value}")
    subject = context.render_template(subject)
    payload = json.dumps(context.to_dict())

    try:
        subprocess.run(
            ["nats", "pub", subject, payload],
            cwd=context.repo_root,
            capture_output=True,
            text=True,
            timeout=10,
        )
        logger.debug(f"Published to {subject}: {context.item_id}")
    except FileNotFoundError:
        logger.debug("nats CLI not found — skipping nats_publish")
    except subprocess.TimeoutExpired:
        logger.warning(f"nats pub timed out for {subject}")
    except Exception as e:
        logger.warning(f"nats_publish failed: {e}")


def _action_log(action: dict, context: HookContext) -> None:
    """Append a JSON line to a log file."""
    log_path = action.get("path", ".kanban/hooks.log")
    log_path = Path(context.render_path(log_path))
    if context.repo_root is not None and not log_path.is_absolute():
        log_path = context.repo_root / log_path

    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        entry = context.to_dict()
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        logger.debug(f"Logged event to {log_path}: {context.item_id}")
    except Exception as e:
        logger.warning(f"log action failed: {e}")


def _action_shell(action: dict, context: HookContext) -> None:
    """Run a shell command with template variable substitution.

    Template values are escaped with shlex.quote() to prevent injection
    via item titles or other user-controlled context fields.
    """
    command = action.get("command", "")
    if not command:
        return

    # Use shlex.quote() on all substituted values to prevent injection
    safe_replacements = {
        "item_id": shlex.quote(context.item_id),
        "item_type": shlex.quote(context.item_type),
        "title": shlex.quote(context.title),
        "event": shlex.quote(context.event.value),
        "old_status": shlex.quote(context.old_status or ""),
        "new_status": shlex.quote(context.new_status or ""),
        "assignee": shlex.quote(context.assignee or ""),
        "timestamp": shlex.quote(context.timestamp),
        "context": shlex.quote(json.dumps(context.to_dict())),
    }
    for key, value in safe_replacements.items():
        command = command.replace(f"{{{key}}}", value)
    timeout = action.get("timeout", 30)

    try:
        result = subprocess.run(
            command,
            cwd=context.repo_root,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            logger.warning(
                f"shell action exited {result.returncode}: "
                f"{result.stderr.strip()[:200]}"
            )
        else:
            logger.debug(f"shell action succeeded: {command[:80]}")
    except subprocess.TimeoutExpired:
        logger.warning(f"shell action timed out after {timeout}s: {command[:80]}")
    except Exception as e:
        logger.warning(f"shell action failed: {e}")


def _action_create_item(
    action: dict, context: HookContext, callbacks: dict[str, Callable]
) -> None:
    """Create a new kanban item via the registered service callback.

    Requires a ``create_item`` callback registered via
    ``HookEngine.set_callback("create_item", fn)``.  The callback
    receives ``item_type``, ``title``, ``priority``, and ``tags``.
    """
    callback = callbacks.get("create_item")
    if not callback:
        logger.debug("create_item action skipped — no callback registered")
        return

    item_type = context.render_template(action.get("item_type", "chore"))
    title = context.render_template(action.get("title", f"Auto: {context.item_id}"))
    priority = action.get("priority", "medium")
    tags = action.get("tags", [])

    try:
        result = callback(
            item_type=item_type,
            title=title,
            priority=priority,
            tags=tags,
        )
        if result:
            logger.debug(f"create_item action created: {result.get('item_id', '?')}")
        else:
            logger.debug("create_item action returned no result")
    except Exception as e:
        logger.warning(f"create_item action failed: {e}")


def _action_notify(action: dict, context: HookContext) -> None:
    """Send a human-readable notification to a NATS channel.

    Uses the noesis-ship wire protocol format (type, group, from, message).
    Publishes to ``ship.channel.{channel}`` via the ``nats`` CLI.
    """
    channel = action.get("channel", "bosun")
    channel = context.render_template(channel)
    message = action.get("message", f"{context.item_id}: {context.title}")
    message = context.render_template(message)

    subject = f"ship.channel.{channel}"
    payload = json.dumps({
        "type": "channel_message",
        "group": channel,
        "from": "kanban-hooks",
        "fromId": "yurtle-kanban",
        "message": message,
        "timestamp": context.timestamp,
    })

    try:
        subprocess.run(
            ["nats", "pub", subject, payload],
            cwd=context.repo_root,
            capture_output=True,
            text=True,
            timeout=10,
        )
        logger.debug(f"Notified {subject}: {message[:80]}")
    except FileNotFoundError:
        logger.debug("nats CLI not found — skipping notify")
    except subprocess.TimeoutExpired:
        logger.warning(f"notify timed out for {subject}")
    except Exception as e:
        logger.warning(f"notify action failed: {e}")


# ─── Helpers ───────────────────────────────────────────────────────────────


def _extract_frontmatter(content: str) -> dict[str, Any]:
    """Extract YAML frontmatter from a Yurtle markdown file."""
    if not content.startswith("---"):
        return {}

    end = content.find("---", 3)
    if end == -1:
        return {}

    try:
        data = yaml.safe_load(content[3:end])
    except Exception as e:
        logger.warning(f"Failed to parse hook frontmatter: {e}")
        return {}
    # a YAML list or scalar is no hooks config, like broken YAML (#321, #330)
    return data if isinstance(data, dict) else {}
