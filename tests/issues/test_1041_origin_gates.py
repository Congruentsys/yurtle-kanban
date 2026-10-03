"""Issue #1041: ``claim``/``bounce`` judge gates by the local config; bounce's usage
line; ``bounced_at``/``bounced_by`` unvalidated.

Found in the PR #1025 (#578) review.

1. ``_claim_change`` and ``_bounce_change`` evaluate gates with
   ``self._evaluate_gates`` (the LOCAL config), while the status label and history
   name come from ``judge`` (origin's config at the fetched rev, #831/#865).
   Expected: gates come from origin's config too.
2. ``cli.py``'s module usage line for ``bounce`` leaves out ``[--take-over]``.
3. ``validate`` doesn't check ``bounced_at``/``bounced_by``.

Item 1 was first ruled out ([steer] on #1041: gates stay local, #865), then landed by
#1260 (claim and bounce judge gates by origin's config, as move --push). Readings a-c
below describe the retired gate tests; their scaffolding was removed (#1051).

Readings the test partner chose (the driver may challenge them):

a. Every origin scenario uses the #585 ``World`` and the #831/#865 pattern: B pushes a
   config A never pulls, and the service is built from A's LOCAL config, as the CLI
   builds it. "A gate only A's stale local config has" is A's working-tree config
   with the gate, origin's without (as #865's gate control writes it).
b. Gates are v1 top-level ``gates:`` whose ``check`` reads a context key nobody
   passes, so the gate always fails when it runs. A refusal is pinned as
   ``Gate check failed`` plus the gate's own message; a win as ``won`` with origin
   holding the new status.
c. This SUPERSEDES #865's ``test_control_gate_only_in_local_config_still_refuses``
   (and #831's help-text pin that gate checks read the local tree): those pinned the
   old carve-out "gates stay local" and must be retired or inverted with the fix.
d. The usage line is pinned on ``yurtle_kanban.cli.__doc__``: the line that starts
   ``yurtle-kanban bounce`` names ``--take-over``.
e. ``validate --json``: a malformed ``bounced_at`` (free text; an ISO-8601 time
   without an offset) and an empty ``bounced_by`` (``""`` or a bare key) each give
   an issue whose ``id`` is the item's and whose message names the key. The issue
   ``type`` string is open. A real bounce's own stamp must validate clean (control).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

import yurtle_kanban.cli as cli_mod
from tests.issues.test_574_claim import ITEM, ITEM_ID, service
from tests.issues.test_578_bounce import (
    bounce,
    local_item,
    local_repo,
    ok,
    run_cli,
    sync_a,
    with_stamp,
)
from yurtle_kanban.cli import main

pytestmark = pytest.mark.usefixtures("claim_env")

# Item 1 (gates judged by origin's config) is not changed: #865's ruling keeps gate
# checks local, pinned by test_865/test_831 ([steer] on #1041).


# --- 2. bounce's usage line names --take-over ----------------------------------------------


def test_module_usage_line_for_bounce_names_take_over() -> None:
    lines = [
        line.strip() for line in (cli_mod.__doc__ or "").splitlines()
        if line.strip().startswith("yurtle-kanban bounce")
    ]
    assert lines, f"cli.py's usage block has no bounce line: {cli_mod.__doc__!r}"
    assert any("--take-over" in line for line in lines), lines


# --- 3. validate checks bounced_at / bounced_by --------------------------------------------


def validate_issues(repo_root: Path, monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    result = run_cli(repo_root, monkeypatch, ["validate", "--json"])
    assert result.exit_code == 1, result.output
    return json.loads(result.stdout)["issues"]


@pytest.mark.parametrize(
    "at",
    ["yesterday afternoon", '"2026-09-27T10:00:00"', "2026-09-27T10:00:00"],
    ids=["free-text", "no-offset", "no-offset-unquoted"],  # unquoted: a YAML datetime (#1051)
)
def test_validate_reports_a_malformed_bounced_at(tmp_path, monkeypatch, at) -> None:
    repo = local_repo(tmp_path, monkeypatch, {
        "EXP-1": with_stamp(local_item("EXP-1"), at=at),
        "EXP-2": with_stamp(local_item("EXP-2")),
    })
    issues = validate_issues(repo.root, monkeypatch)
    mine = [i for i in issues if i.get("id") == "EXP-1" and "bounced_at" in i.get("message", "")]
    assert mine, f"no validate issue naming bounced_at for EXP-1: {issues}"
    assert not [i for i in issues if i.get("id") == "EXP-2"], (
        f"a well-formed stamp was reported: {issues}"
    )


@pytest.mark.parametrize("by", ['""', ""], ids=["empty-string", "bare-key"])
def test_validate_reports_an_empty_bounced_by(tmp_path, monkeypatch, by) -> None:
    repo = local_repo(tmp_path, monkeypatch, {
        "EXP-1": with_stamp(local_item("EXP-1"), by=by),
        "EXP-2": with_stamp(local_item("EXP-2")),
    })
    issues = validate_issues(repo.root, monkeypatch)
    mine = [i for i in issues if i.get("id") == "EXP-1" and "bounced_by" in i.get("message", "")]
    assert mine, f"no validate issue naming bounced_by for EXP-1: {issues}"
    assert not [i for i in issues if i.get("id") == "EXP-2"], (
        f"a well-formed stamp was reported: {issues}"
    )


def test_a_real_bounce_stamp_validates_clean(world, monkeypatch) -> None:
    """Control: what `bounce` itself writes is well formed."""
    ok(bounce(world, monkeypatch))
    sync_a(world)
    assert "bounced_at" in world.remote_show(ITEM)
    monkeypatch.chdir(world.a)
    result = CliRunner().invoke(main, ["validate", "--json"])
    issues = json.loads(result.stdout)["issues"] if result.stdout.strip() else []
    stamp_issues = [
        i for i in issues
        if i.get("id") == ITEM_ID and "bounce" in i.get("message", "")
    ]
    assert not stamp_issues, f"bounce's own stamp was reported: {issues}"
    assert service(world.a).get_item(ITEM_ID) is not None


def test_validate_help_names_the_bounce_stamp_keys() -> None:
    """`validate --help` names every stamp key it checks (#1051)."""
    text = " ".join(CliRunner().invoke(main, ["validate", "--help"]).output.split())
    for key in ("bounce_sha", "bounces", "bounced_at", "bounced_by"):
        assert key in text, (key, text)
