"""Issue #1260 (driver's addition): bounce judges gates by origin's config too.

The [steer] on #1260 ruled that every pushed write reads gates from origin's config
(claim, bounce and move --push alike), retiring #865/#1041's "gates stay local". The
gate here blocks ``* -> backlog``; A claims first (gate-free), then the gate lands.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from click.testing import CliRunner

from tests.issues.test_574_claim import ITEM, ITEM_ID, A, claim, frontmatter, service
from tests.issues.test_574_sync_and_push import Recorder
from tests.issues.test_585_create_push_loop import World
from tests.issues.test_590_next_id_and_hdd_ids import b_push
from tests.issues.test_1260_move_claim_followups import CONFIG, gates_config_text
from yurtle_kanban.cli import main
from yurtle_kanban.config import KanbanConfig

pytestmark = pytest.mark.usefixtures("claim_env")

MESSAGE = "Bounce gate 1260 not satisfied"
GATE_ON_BACKLOG = {
    "* -> backlog": [
        {"id": "bounce_review", "check": "context.bounce_reviewed", "message": MESSAGE},
    ],
}


def claimed_by_a(world: World) -> None:
    out = claim(world.a, A)
    assert out.kind == "won", f"{out.kind}: {out.message}"


def bounce(world: World) -> object:
    rec = Recorder()
    return service(world.a).bounce_item(
        ITEM_ID, actor=A, reason="needs a design",
        sleep=rec.sleep, jitter=rec.jitter, seam=rec.seam,
    )


def test_bounce_gate_only_on_origin_refuses(world, tmp_path: Path) -> None:
    claimed_by_a(world)
    b_push(world, {CONFIG: gates_config_text(tmp_path, GATE_ON_BACKLOG)})
    assert not KanbanConfig.load(world.a / CONFIG).gates
    base = world.remote_sha()

    out = bounce(world)

    assert out.kind == "refused", f"{out.kind}: {out.message}"
    assert MESSAGE in out.message, out.message
    assert world.remote_sha() == base, "a gated bounce was pushed"


def test_bounce_gate_only_local_does_not_block(world, tmp_path: Path) -> None:
    claimed_by_a(world)
    (world.a / CONFIG).write_text(gates_config_text(tmp_path, GATE_ON_BACKLOG))
    assert KanbanConfig.load(world.a / CONFIG).gates
    assert "bounce_review" not in world.remote_show(CONFIG)

    out = bounce(world)

    assert out.kind == "won", f"a local-only gate must not block: {out.kind}: {out.message}"
    assert not frontmatter(world.remote_show(ITEM)).get("assignee")


def test_bounce_help_says_its_gates_are_origins() -> None:
    """#1275's review: bounce --help names origin as the source of its `* -> backlog`
    gates, as claim --help does (#1260)."""
    result = CliRunner().invoke(main, ["bounce", "--help"])
    assert result.exit_code == 0, result.output
    text = " ".join(result.output.split()).lower()
    assert re.search(r"origin's\W+\*\s*->\s*backlog\W+gates", text), text
