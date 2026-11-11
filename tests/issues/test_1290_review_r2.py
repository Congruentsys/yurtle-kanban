"""Issue #1290, review r2 of PR #1296, MINOR 1: with no `$YURTLE_AGENT`, a solo user's
`next-id` -> `create --id` is refused (ruling 1: git `user.name` never makes a record your
own), and the refusal must say how to claim one's own reservation: set `$YURTLE_AGENT`.

The hint is for the case the review names: the record's `allocated_by` is this checkout's git
`user.name`. A record by a DIFFERENT user.name is refused with no such hint (control).
"""

from __future__ import annotations

import json

import pytest

from tests.issues.test_585_create_push_loop import git, output_of
from tests.issues.test_1288_id_numbering import _env, make  # noqa: F401  (autouse fixture)
from tests.issues.test_1290_explicit_id_reservations import ENTRY, MODES, PUSH
from tests.issues.test_1290_review_r1 import cli_in

HYP = ENTRY["hypothesis"][0]


def next_id_in(clone, monkeypatch, prefix: str) -> str:
    result, out = cli_in(clone, monkeypatch, "next-id", prefix, "--json")
    assert result.exit_code == 0, out
    return json.loads(output_of(result))["id"]


@pytest.mark.parametrize("mode", MODES)
@PUSH
def test_own_user_name_reservation_refused_with_yurtle_agent_hint(
    tmp_path, monkeypatch, mode, push
) -> None:
    repo = make(tmp_path, mode)  # clone A's git user.name is "A"
    assert git(repo.a, "config", "user.name").strip() == "A"
    monkeypatch.delenv("YURTLE_AGENT", raising=False)
    assert next_id_in(repo.a, monkeypatch, "H") == "H-001"
    git(repo.a, "pull", "-q", "--no-rebase", "origin", "main")
    tip = repo.remote_tip()
    result, out = cli_in(repo.a, monkeypatch, *HYP("H-001"), *(["--push"] if push else []))
    assert result.exit_code != 0, f"ruling 1: user.name alone made the record A's own: {out}"
    assert "reserved" in out, out
    assert "YURTLE_AGENT" in out, f"the refusal does not say to set $YURTLE_AGENT: {out}"
    assert "H-001" not in repo.local_ids()
    assert repo.remote_tip() == tip


@pytest.mark.parametrize("mode", MODES)
@PUSH
def test_control_other_user_name_reservation_refused_without_hint(
    tmp_path, monkeypatch, mode, push
) -> None:
    repo = make(tmp_path, mode)  # clone B's git user.name is "B"
    monkeypatch.delenv("YURTLE_AGENT", raising=False)
    assert next_id_in(repo.b, monkeypatch, "H") == "H-001"
    git(repo.a, "pull", "-q", "--no-rebase", "origin", "main")
    tip = repo.remote_tip()
    result, out = cli_in(repo.a, monkeypatch, *HYP("H-001"), *(["--push"] if push else []))
    assert result.exit_code != 0, out
    assert "reserved" in out and "B" in out, out
    assert "YURTLE_AGENT" not in out, f"a hint for someone else's reservation: {out}"
    assert "H-001" not in repo.local_ids()
    assert repo.remote_tip() == tip
