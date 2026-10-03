"""Issue #1260: follow-ups to ``move --push`` (#1251, PR #1257) and ``claim``.

1. ``move --help`` lists exit code 3 (lost: refused by a holder after a rejected
   push) along with 0/1/4/5/6/8.
2. ``move --help`` documents that, with ``--push``, a move to the status origin
   already has is a noop (exit 0). Plain ``move`` refuses it as illegal; that
   difference is deliberate (a retried push must be idempotent).
3. ``claim`` judges gates on ORIGIN's config, as ``move --push`` does (#865,
   #1257): ``judge._evaluate_gates``, not the local service's. A blocking gate
   configured only in origin's config refuses the claim; one configured only in
   the local config does not block it.
4. Pins for cases only #1257's reviewer probed: ``move --push`` judges gates on
   origin's config too (origin-only refuses, local-only does not block); a lost
   race to a holder is exit 3 and fires no hooks; with ``--assign`` a retried win
   fires the assigned hook exactly once.

Harness: tests/issues/test_1251_move_push.py and test_574_claim.py. "Only on
origin" is B pushing a config A has not fetched; "only local" is A's working-tree
config, uncommitted, with origin's config gate-free.

Ambiguities resolved here (the test partner's reading; the driver may challenge):

a. Help wording is matched loosely on whitespace-normalised ``--help`` output:
   exit 3 is a ``3`` within a few words of "lost"; the noop is "noop"/"no-op"
   with "already" in the same sentence. The current "0 moved (or already there)"
   does not say noop, so it does not satisfy (2).
b. The lost race runs at the service level (``move_item_push`` with a seam); the
   CLI has no seam. Its outcome is ``lost`` with ``exit_code == 3``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from tests.issues.test_574_claim import (
    GATE_FAILING,
    ITEM,
    ITEM_ID,
    A,
    B,
    b_claims,
    fired,
    frontmatter,
    install_hooks,
    output_of,
    service,
)
from tests.issues.test_574_sync_and_push import Recorder, rival
from tests.issues.test_585_create_push_loop import World, git
from tests.issues.test_590_next_id_and_hdd_ids import b_push
from yurtle_kanban.cli import main
from yurtle_kanban.config import KanbanConfig, PathConfig
from yurtle_kanban.models import WorkItemStatus

pytestmark = pytest.mark.usefixtures("claim_env")

CONFIG = ".kanban/config.yaml"
GATE_MESSAGE = "Self-review gate 574b not satisfied"
LOST = 3


# --- harness ---------------------------------------------------------------------


def invoke(world: World, monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> Any:
    monkeypatch.chdir(world.a)
    return CliRunner().invoke(main, argv)


def gates_config_text(tmp_path: Path, gates: dict[str, list[dict[str, str]]]) -> str:
    """The config `set_gates` writes, as text (nautical, the world's paths)."""
    config = KanbanConfig(
        theme="nautical",
        paths=PathConfig(
            root="kanban-work/",
            scan_paths=["kanban-work/expeditions/", "kanban-work/signals/"],
        ),
        gates=gates,
    )
    path = tmp_path / "gates-config.yaml"
    config.save(path)
    return path.read_text()


def gates_only_on_origin(world: World, tmp_path: Path) -> None:
    """B pushes a blocking gate into origin's config; A's config stays gate-free."""
    b_push(world, {CONFIG: gates_config_text(tmp_path, GATE_FAILING)})
    local = KanbanConfig.load(world.a / CONFIG)
    assert not local.gates, "A must not see origin's gate locally"


def gates_only_local(world: World, tmp_path: Path) -> None:
    """A's working-tree config has a blocking gate (uncommitted); origin's has none."""
    (world.a / CONFIG).write_text(gates_config_text(tmp_path, GATE_FAILING))
    assert KanbanConfig.load(world.a / CONFIG).gates, "A's local gate did not take"
    assert "self_review" not in world.remote_show(CONFIG), "origin must not have the gate"


def remote_status(world: World) -> str:
    return frontmatter(world.remote_show(ITEM))["status"]


def move_push(
    clone: Path, new_status: WorkItemStatus, rec: Recorder | None = None, **kw: Any
) -> Any:
    rec = rec or Recorder()
    return service(clone).move_item_push(
        ITEM_ID, new_status, sleep=rec.sleep, jitter=rec.jitter, seam=rec.seam, **kw
    )


def move_help() -> str:
    result = CliRunner().invoke(main, ["move", "--help"])
    assert result.exit_code == 0, result.output
    return " ".join(result.output.split())


# --- 1. move --help lists exit 3 ---------------------------------------------------


def test_move_help_lists_exit_3_lost() -> None:
    text = move_help()

    assert re.search(r"\b3\b\W*(\w+\W+){0,4}lost|lost\W*(\w+\W+){0,4}\b3\b", text, re.I), (
        f"move --help must list exit 3 (lost): {text}"
    )


def test_move_help_still_lists_other_exit_codes() -> None:
    text = move_help()

    for code in ("0", "1", "4", "5", "6", "8"):
        assert re.search(rf"\b{code}\b", text), f"exit {code} missing: {text}"


# --- 2. move --help documents the same-status noop ---------------------------------


def test_move_help_documents_same_status_noop() -> None:
    text = move_help()

    sentences = re.split(r"(?<=[.;])\s+", text)
    assert any(
        re.search(r"\bno-?op\b", s, re.I) and re.search(r"already|exit 0|\b0\b", s, re.I)
        for s in sentences
    ), f"move --help must say a --push move to origin's current status is a noop: {text}"


# --- 3. claim judges gates on origin's config ---------------------------------------


def test_claim_gate_only_on_origin_refuses(world, monkeypatch, tmp_path) -> None:
    gates_only_on_origin(world, tmp_path)
    base = world.remote_sha()

    result = invoke(world, monkeypatch, ["claim", ITEM_ID, "--agent", A])

    out = output_of(result)
    assert result.exit_code == 1, out
    assert GATE_MESSAGE in out, out
    assert world.remote_sha() == base, "a gated claim was pushed"


def test_claim_item_gate_only_on_origin_refuses(world, tmp_path) -> None:
    gates_only_on_origin(world, tmp_path)
    base = world.remote_sha()
    rec = Recorder()

    out = service(world.a).claim_item(
        ITEM_ID, actor=A, sleep=rec.sleep, jitter=rec.jitter, seam=rec.seam
    )

    assert out.kind == "refused", out.message
    assert GATE_MESSAGE in out.message, out.message
    assert world.remote_sha() == base


def test_claim_gate_only_local_does_not_block(world, monkeypatch, tmp_path) -> None:
    gates_only_local(world, tmp_path)

    result = invoke(world, monkeypatch, ["claim", ITEM_ID, "--agent", A])

    assert result.exit_code == 0, output_of(result)
    fm = frontmatter(world.remote_show(ITEM))
    assert fm["status"] == "underway"
    assert fm["assignee"] == A


# --- 4. pins for move --push ----------------------------------------------------------


def test_move_push_gate_only_on_origin_refuses(world, monkeypatch, tmp_path) -> None:
    gates_only_on_origin(world, tmp_path)
    base = world.remote_sha()

    result = invoke(
        world, monkeypatch, ["move", ITEM_ID, "in_progress", "--push", "--agent", A]
    )

    out = output_of(result)
    assert result.exit_code == 1, out
    assert GATE_MESSAGE in out, out
    assert world.remote_sha() == base


def test_move_push_gate_only_local_does_not_block(world, monkeypatch, tmp_path) -> None:
    gates_only_local(world, tmp_path)

    result = invoke(
        world, monkeypatch, ["move", ITEM_ID, "in_progress", "--push", "--agent", A]
    )

    assert result.exit_code == 0, output_of(result)
    assert remote_status(world) == "underway"


def test_move_push_lost_to_holder_exits_3_and_fires_no_hooks(world, tmp_path) -> None:
    marker = install_hooks(world, tmp_path)
    rec = Recorder(lambda attempt: b_claims(world) if attempt == 0 else None)

    out = move_push(world.a, WorkItemStatus.IN_PROGRESS, rec, actor=A)

    assert out.kind == "lost", out.message
    assert out.exit_code == LOST
    assert B in out.message, out.message
    assert frontmatter(world.remote_show(ITEM))["assignee"] == B
    assert fired(marker) == [], fired(marker)


def test_move_push_assign_retried_win_fires_assigned_hook_once(world, tmp_path) -> None:
    marker = install_hooks(world, tmp_path)
    rec = Recorder(lambda attempt: rival(world) if attempt == 0 else None)

    out = move_push(world.a, WorkItemStatus.IN_PROGRESS, rec, actor=A, assignee=A)

    assert out.kind == "won", out.message
    assert rec.seams == [0, 1], "A did not retry"
    assigned = [line for line in fired(marker) if line.startswith("on_assign ")]
    assert assigned == [f"on_assign {ITEM_ID} in_progress {A}"], fired(marker)
    changes = [line for line in fired(marker) if line.startswith("on_status_change ")]
    assert len(changes) == 1, fired(marker)


def test_control_gates_helpers(world, tmp_path) -> None:
    """The two seeding helpers put the gate where they say."""
    gates_only_on_origin(world, tmp_path)
    assert "self_review" in world.remote_show(CONFIG)
    git(world.a, "fetch", "origin")
    assert "self_review" not in (world.a / CONFIG).read_text()
