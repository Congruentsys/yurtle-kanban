"""
Transition gate evaluation for kanban workflow quality enforcement.

Gates are config-driven checks that validate conditions before allowing
status transitions. They complement WIP limits (capacity constraints)
with quality constraints (e.g., "assignee required", "self-review done").

Gates are defined per-board in config.yaml under a ``gates:`` key,
keyed by transition strings like ``"in_progress -> review"`` or
``"* -> in_progress"`` (wildcard).

Check expressions use safe dot-path evaluation (no eval/exec).
Unknown expressions fail closed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ._logging import get_logger

if TYPE_CHECKING:
    from .models import WorkItem

logger = get_logger("yurtle-kanban.gates")  # escapes control characters (#215)


# ── Data classes ────────────────────────────────────────────────────────


@dataclass
class GateDefinition:
    """A single gate check for a transition."""

    id: str
    check: str  # Dot-path expression (e.g., "item.assignee", "context.self_reviewed")
    message: str
    severity: str = "blocking"  # "blocking" | "advisory"


@dataclass
class GateResult:
    """Result of evaluating one gate."""

    gate_id: str
    passed: bool
    message: str
    severity: str


# ── Evaluator ───────────────────────────────────────────────────────────


class GateEvaluator:
    """Evaluate transition gates against a work item.

    Uses safe dot-path evaluation (no eval/exec).
    Fail-closed: unknown check paths return False.
    """

    def __init__(self, gate_configs: dict[str, list[dict]]) -> None:
        """Initialize from the ``gates`` dict in board config.

        Args:
            gate_configs: Mapping of transition key (e.g. ``"* -> in_progress"``)
                to list of gate definition dicts.
        """
        self._gates: dict[str, list[GateDefinition]] = self._parse_gates(gate_configs)

    def evaluate(
        self,
        item: WorkItem,
        from_status: str,
        to_status: str,
        context: dict[str, Any] | None = None,
    ) -> list[GateResult]:
        """Run all matching gates for this transition.

        Args:
            item: The work item being moved.
            from_status: Current status value (e.g. "in_progress").
            to_status: Target status value (e.g. "review").
            context: Extra key-value pairs from CLI flags
                (e.g. ``{"self_reviewed": True}``).

        Returns:
            List of GateResults (both passed and failed).
        """
        ctx = context or {}
        matching = self._match_gates(from_status, to_status)
        results: list[GateResult] = []

        for gate in matching:
            passed = self._evaluate_check(gate.check, item, ctx)
            results.append(
                GateResult(
                    gate_id=gate.id,
                    passed=passed,
                    message=gate.message,
                    severity=gate.severity,
                )
            )

        return results

    def get_blocking_failures(self, results: list[GateResult]) -> list[GateResult]:
        """Filter to only blocking failures."""
        return [r for r in results if not r.passed and r.severity == "blocking"]

    # ── Internal ────────────────────────────────────────────────────────

    def _parse_gates(
        self, gate_configs: dict[str, list[dict]]
    ) -> dict[str, list[GateDefinition]]:
        """Parse raw config dicts into GateDefinition objects."""
        parsed: dict[str, list[GateDefinition]] = {}

        for transition_key, gate_list in gate_configs.items():
            # Normalize key: strip whitespace around arrow
            normalized = self._normalize_transition_key(transition_key)
            if normalized is None:
                logger.warning(
                    f"Invalid transition gate key: {transition_key!r}. "
                    f"Expected format: 'from -> to' (e.g., 'in_progress -> review')"
                )
                continue

            definitions = []
            for gate_dict in gate_list:
                gate_id = gate_dict.get("id", "unnamed")
                check = gate_dict.get("check", "")
                message = gate_dict.get("message", f"Gate {gate_id} failed")
                severity = gate_dict.get("severity", "blocking")

                if severity not in ("blocking", "advisory"):
                    logger.warning(
                        f"Unknown gate severity {severity!r} for gate {gate_id}, "
                        f"defaulting to 'blocking'"
                    )
                    severity = "blocking"

                definitions.append(
                    GateDefinition(
                        id=gate_id,
                        check=check,
                        message=message,
                        severity=severity,
                    )
                )

            parsed[normalized] = definitions

        return parsed

    def _normalize_transition_key(self, key: str) -> str | None:
        """Normalize a transition key like 'in_progress -> review'.

        Returns normalized key or None if invalid format.
        """
        if " -> " not in key:
            # Try with just ->
            if "->" in key:
                parts = key.split("->", 1)
                return f"{parts[0].strip()} -> {parts[1].strip()}"
            return None

        parts = key.split(" -> ", 1)
        if len(parts) != 2:
            return None

        from_part = parts[0].strip()
        to_part = parts[1].strip()

        if not from_part or not to_part:
            return None

        return f"{from_part} -> {to_part}"

    def _match_gates(
        self, from_status: str, to_status: str
    ) -> list[GateDefinition]:
        """Find gates matching this transition (exact + wildcard).

        Match order:
        1. Exact: ``"in_progress -> review"``
        2. Wildcard source: ``"* -> review"``
        3. Wildcard target: ``"in_progress -> *"``
        """
        matched: list[GateDefinition] = []

        # Exact match
        exact_key = f"{from_status} -> {to_status}"
        if exact_key in self._gates:
            matched.extend(self._gates[exact_key])

        # Wildcard source: * -> to_status
        wildcard_src = f"* -> {to_status}"
        if wildcard_src in self._gates:
            matched.extend(self._gates[wildcard_src])

        # Wildcard target: from_status -> *
        wildcard_tgt = f"{from_status} -> *"
        if wildcard_tgt in self._gates:
            matched.extend(self._gates[wildcard_tgt])

        return matched

    def _evaluate_check(
        self, check: str, item: WorkItem, context: dict[str, Any]
    ) -> bool:
        """Safe dot-path evaluator. No eval(). Fail-closed.

        Supported patterns:
        - ``item.<field>`` — truthy check on WorkItem attribute
        - ``context.<key>`` — truthy check on context dict value

        Unknown expressions return False and log a warning.
        """
        check = check.strip()

        # item.<field> — truthy check on work item attribute
        if check.startswith("item."):
            field_name = check[5:]  # After "item."
            return self._check_item_field(item, field_name)

        # context.<key> — truthy check on context dict
        if check.startswith("context."):
            key = check[8:]  # After "context."
            return bool(context.get(key))

        # Unknown expression — fail closed
        logger.warning(
            f"Unknown gate check expression (fail-closed): {check!r}. "
            f"Supported: 'item.<field>', 'context.<key>'"
        )
        return False

    def _check_item_field(self, item: WorkItem, field_path: str) -> bool:
        """Check a field on a WorkItem. Supports nested dot access."""
        parts = field_path.split(".", 1)
        field_name = parts[0]

        if not hasattr(item, field_name):
            logger.warning(
                f"WorkItem has no attribute {field_name!r} (fail-closed)"
            )
            return False

        value = getattr(item, field_name)

        # Nested access (e.g., item.metadata.self_reviewed)
        if len(parts) > 1 and isinstance(value, dict):
            nested_key = parts[1]
            return bool(value.get(nested_key))

        return bool(value)
