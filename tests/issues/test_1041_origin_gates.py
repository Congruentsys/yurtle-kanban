"""Issue #1041: ``claim``/``bounce`` judge gates by the local config; bounce's usage
line; ``bounced_at``/``bounced_by`` unvalidated.

Found in the PR #1025 (#578) review.

1. ``_claim_change`` and ``_bounce_change`` evaluate gates with
   ``self._evaluate_gates`` (the LOCAL config), while the status label and history
   name come from ``judge`` (origin's config at the fetched rev, #831/#865).
   Expected: gates come from origin's config too.
2. ``cli.py``'s module usage line for ``bounce`` leaves out ``[--take-over]``.
3. ``validate`` doesn't check ``bounced_at``/``bounced_by``.

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
from tests.issues.test_585_create_push_loop import World, git
from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import main
from yurtle_kanban.config import KanbanConfig, PathConfig

pytestmark = pytest.mark.usefixtures("claim_env")

CONFIG = ".kanban/config.yaml"
CLAIM_GATE_MSG = "the 1041 in-progress gate refuses"
BOUNCE_GATE_MSG = "the 1041 backlog gate refuses"


# --- harness ---------------------------------------------------------------------


def config_text(tmp_path: Path, gates: dict[str, list[dict[str, Any]]] | None) -> str:
    """World's single-board nautical config, with `gates` (v1 top level)."""
    config = KanbanConfig(
        theme="nautical",
        paths=PathConfig(
            root="kanban-work/",
            scan_paths=["kanban-work/expeditions/", "kanban-work/signals/"],
        ),
        gates=gates or {},
    )
    path = tmp_path / "cfg-1041" / "config.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    config.save(path)
    return path.read_text()


def gate(transition: str, message: str) -> dict[str, list[dict[str, Any]]]:
    return {transition: [{
        "id": "gate_1041", "check": "context.approved_1041", "message": message,
    }]}


def b_pushes(world: World, files: dict[str, str]) -> None:
    """B, on a fresh origin/main, writes `files`, commits and pushes. A never pulls."""
    git(world.b, "fetch", "origin")
    git(world.b, "reset", "--hard", f"origin/{world.default}")
    for rel, text in files.items():
        path = world.b / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    git(world.b, "add", "-A")
    git(world.b, "commit", "-m", "rival: 1041")
    git(world.b, "push", "origin", f"HEAD:refs/heads/{world.default}")
    config_mod._theme_cache.clear()


def origin_only(world: World, tmp_path: Path, gates: dict[str, Any]) -> None:
    """Origin's config has `gates`; A's local config has none and A doesn't pull."""
    b_pushes(world, {CONFIG: config_text(tmp_path, gates)})
    assert "gate_1041" not in (world.a / CONFIG).read_text(), "A must not have the gate"
    assert "gate_1041" in world.remote_show(CONFIG)


def local_only(world: World, tmp_path: Path, gates: dict[str, Any]) -> None:
    """A's stale working-tree config has `gates`; origin removed them (B pushed a
    config without them, and A never pulled)."""
    b_pushes(world, {CONFIG: config_text(tmp_path, None) + "# gates removed on origin\n"})
    (world.a / CONFIG).write_text(config_text(tmp_path, gates))
    config_mod._theme_cache.clear()
    assert "gate_1041" not in world.remote_show(CONFIG)


# --- 1. claim: `* -> in_progress` gates come from origin's config --------------------------


# Item 1 (gates judged by origin's config) is not changed: #865's ruling keeps gate
# checks local, pinned by test_865/test_831 ([steer] on #1041).


def test_module_usage_line_for_bounce_names_take_over() -> None:
    lines = [
        line.strip() for line in (cli_mod.__doc__ or "").splitlines()
        if line.strip().startswith("yurtle-kanban bounce")
    ]
    assert lines, f"cli.py's usage block has no bounce line: {cli_mod.__doc__!r}"
    assert any("--take-over" in line for line in lines), lines


# --- 4. validate checks bounced_at / bounced_by --------------------------------------------


def validate_issues(repo_root: Path, monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    result = run_cli(repo_root, monkeypatch, ["validate", "--json"])
    assert result.exit_code == 1, result.output
    return json.loads(result.stdout)["issues"]


@pytest.mark.parametrize(
    "at",
    ["yesterday afternoon", '"2026-09-27T10:00:00"'],
    ids=["free-text", "no-offset"],
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
