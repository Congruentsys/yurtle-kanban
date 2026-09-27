"""Issue #661 — the dash is part of the id; lone-CR files; remember the default branch.

The [steer] decisions are the spec. Reuses the #585/#590/#634/#641 real-git harness
(a bare remote, clone A under test, rival clone B).

1. The dash is part of the id: `_holder_at` compares the text before the number,
   separator included, plus the integer number. `EXP-3` still collides with
   `EXP-003`, but `EXP3` does not, and `H1` does not collide with `H-001`.
2. Lone-CR files: an item file with old-Mac (`\\r`-only) line endings is skipped by
   the scan with one warning naming it and saying to convert to LF; LF and CRLF
   files are still read.
3. Remember the default branch: after fetching the resolved default, it is recorded
   as `refs/remotes/origin/HEAD`, so the offline `_fetched_default` finds the real
   default (`master`) instead of a stale `origin/main`.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator

import pytest

from tests.issues.test_585_create_push_loop import (
    EXP_DIR,
    FEATURE,
    World,
    git,
    output_of,
    porcelain,
)
from tests.issues.test_590_next_id_and_hdd_ids import ALLOC, allocated, b_push
from tests.issues.test_603_push_failure_messages import invoke
from tests.issues.test_641_id_allocation import (
    _EXP_005,
    NetworkSpy,
    b_push_to,
    hdd,
)
from tests.test_634_explicit_ids_on_base import (
    assert_untouched,
    remote_ids,
    service,
    to_feature_branch,
)
from yurtle_kanban import config as config_mod
from yurtle_kanban.models import WorkItemType

TITLE_A = "Alpha From A"


@pytest.fixture
def world(tmp_path, monkeypatch: pytest.MonkeyPatch) -> World:
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    return World(tmp_path)


@pytest.fixture(autouse=True)
def _clean_theme_cache() -> Iterator[None]:
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


# --- 1. the dash is part of the id ---------------------------------------------------------

_EXP_HOLDERS = {
    "filename": f"{EXP_DIR}/EXP-003-rival.md",
    "frontmatter": f"{EXP_DIR}/rival-notes.md",
}


def _exp_003_rival(world: World, path: str) -> None:
    b_push(world, {
        path: '---\nid: EXP-003\ntitle: "Rival"\ntype: expedition\nstatus: backlog\n---\n'
    })


@pytest.mark.parametrize("holder", list(_EXP_HOLDERS))
def test_dashed_explicit_id_still_refused_against_padded(world, holder) -> None:
    """Control: `EXP-3` is `EXP-003`."""
    path = _EXP_HOLDERS[holder]
    feat = to_feature_branch(world)
    _exp_003_rival(world, path)
    result = service(world).create_item_and_push(
        WorkItemType.EXPEDITION, TITLE_A, item_id="EXP-3"
    )

    assert result["success"] is False, result
    assert path.rsplit("/", 1)[1] in result["message"], result["message"]
    assert list(remote_ids(world, f"{EXP_DIR}/")) == [path]
    assert_untouched(world, feat)


@pytest.mark.parametrize("holder", list(_EXP_HOLDERS))
def test_undashed_explicit_id_lands_beside_dashed(world, holder) -> None:
    """`EXP3` is not `EXP-003`: a different id space, so it lands."""
    path = _EXP_HOLDERS[holder]
    feat = to_feature_branch(world)
    _exp_003_rival(world, path)
    result = service(world).create_item_and_push(
        WorkItemType.EXPEDITION, TITLE_A, item_id="EXP3"
    )

    assert result["success"] is True, f"EXP3 was refused as EXP-003: {result['message']}"
    ids = sorted(i for i in remote_ids(world, f"{EXP_DIR}/").values() if i)
    assert ids == ["EXP-003", "EXP3"], ids
    assert git(world.a, "rev-parse", FEATURE).strip() == feat[0]
    assert porcelain(world.a) == []


_H_RIVAL = "research/hypotheses/H-001-rival.md"


def _h_001_rival(world: World) -> None:
    b_push(world, {_H_RIVAL: '---\nid: H-001\ntitle: "Rival"\ntype: hypothesis\n---\n'})


def test_dashed_hypothesis_id_still_refused_against_padded(world) -> None:
    """Control: `H-1` is `H-001`."""
    hdd(world)
    feat = to_feature_branch(world)
    _h_001_rival(world)
    result = service(world).create_item_and_push(
        WorkItemType.HYPOTHESIS, TITLE_A, item_id="H-1"
    )

    assert result["success"] is False, result
    assert "H-001-rival.md" in result["message"], result["message"]
    assert list(remote_ids(world, "research/hypotheses/")) == [_H_RIVAL]
    assert_untouched(world, feat)


def test_undashed_hypothesis_id_lands_beside_dashed(world) -> None:
    hdd(world)
    feat = to_feature_branch(world)
    _h_001_rival(world)
    result = service(world).create_item_and_push(
        WorkItemType.HYPOTHESIS, TITLE_A, item_id="H1"
    )

    assert result["success"] is True, f"H1 was refused as H-001: {result['message']}"
    ids = sorted(i for i in remote_ids(world, "research/hypotheses/").values() if i)
    assert ids == ["H-001", "H1"], ids
    assert git(world.a, "rev-parse", FEATURE).strip() == feat[0]


def test_cli_dashed_hypothesis_id_still_refused(world, monkeypatch) -> None:
    """Control, CLI path."""
    hdd(world)
    feat = to_feature_branch(world)
    _h_001_rival(world)
    result = invoke(world, monkeypatch, ["hypothesis", "create", TITLE_A, "--id", "H-1", "--push"])
    out = " ".join(output_of(result).split())

    assert result.exit_code != 0, out
    assert "H-001-rival.md" in out, out
    assert list(remote_ids(world, "research/hypotheses/")) == [_H_RIVAL]
    assert_untouched(world, feat)


def test_cli_undashed_hypothesis_id_lands(world, monkeypatch) -> None:
    hdd(world)
    to_feature_branch(world)
    _h_001_rival(world)
    result = invoke(world, monkeypatch, ["hypothesis", "create", TITLE_A, "--id", "H1", "--push"])
    out = " ".join(output_of(result).split())

    assert result.exit_code == 0, f"H1 was refused as H-001: {out}"
    ids = sorted(i for i in remote_ids(world, "research/hypotheses/").values() if i)
    assert ids == ["H-001", "H1"], ids


# --- 2. lone-CR item files are skipped, with one warning ------------------------------------

_ITEM = '---\nid: {id}\ntitle: "{title}"\ntype: expedition\nstatus: backlog\n---\n\n# {title}\n'
_CR_NAME = "EXP-002-old-mac.md"


def _board_with_line_endings(world: World) -> None:
    d = world.a / EXP_DIR
    (d / "EXP-001-lf.md").write_bytes(_ITEM.format(id="EXP-001", title="Lf").encode())
    (d / _CR_NAME).write_bytes(
        _ITEM.format(id="EXP-002", title="Old Mac").replace("\n", "\r").encode()
    )
    (d / "EXP-003-crlf.md").write_bytes(
        _ITEM.format(id="EXP-003", title="Crlf").replace("\n", "\r\n").encode()
    )


def test_scan_skips_lone_cr_file_with_one_warning(world) -> None:
    _board_with_line_endings(world)
    svc = service(world)
    ids = sorted(item.id for item in svc.scan())

    assert "EXP-002" not in ids, f"a lone-CR file was read as an item: {ids}"
    assert len(svc.parse_warnings) == 1, svc.parse_warnings
    path, reason = svc.parse_warnings[0]
    assert path.name == _CR_NAME, svc.parse_warnings
    assert "LF" in reason, reason


def test_scan_reads_lf_and_crlf_files(world) -> None:
    """Controls: LF and CRLF files are items."""
    _board_with_line_endings(world)
    ids = sorted(item.id for item in service(world).scan())
    assert "EXP-001" in ids and "EXP-003" in ids, ids


def test_cli_list_omits_lone_cr_file_and_warns_once(world, monkeypatch) -> None:
    _board_with_line_endings(world)
    result = invoke(world, monkeypatch, ["list"])
    out = output_of(result)

    assert result.exit_code == 0, out
    assert "EXP-001" in out and "EXP-003" in out, out
    assert "Old Mac" not in out.replace(_CR_NAME, ""), f"lone-CR item listed: {out}"
    warnings = [line for line in out.splitlines() if "warning" in line.lower()]
    assert len(warnings) == 1, warnings
    assert _CR_NAME in warnings[0] and "LF" in warnings[0], warnings


def test_cli_show_does_not_find_lone_cr_item(world, monkeypatch) -> None:
    _board_with_line_endings(world)
    result = invoke(world, monkeypatch, ["show", "EXP-002"])
    out = output_of(result)
    assert result.exit_code != 0, f"a lone-CR item was shown: {out}"


def test_cli_show_reads_crlf_item(world, monkeypatch) -> None:
    """Control."""
    _board_with_line_endings(world)
    result = invoke(world, monkeypatch, ["show", "EXP-003"])
    out = output_of(result)
    assert result.exit_code == 0, out
    assert "Crlf" in out, out


# --- 3. the fetched default is remembered as origin/HEAD ------------------------------------


@pytest.fixture
def master_world(tmp_path, monkeypatch: pytest.MonkeyPatch) -> World:
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    w = World(tmp_path, default="master", remote_add=True)
    assert git(w.a, "symbolic-ref", "-q", "refs/remotes/origin/HEAD", check=False) == ""
    return w


def _origin_head(world: World) -> str:
    return git(world.a, "symbolic-ref", "-q", "refs/remotes/origin/HEAD", check=False).strip()


def test_synced_next_id_records_origin_head(master_world) -> None:
    result = service(master_world).allocate_next_id(
        "EXP", sync_remote=True, commit_allocation=False
    )
    assert result["success"] is True, result
    assert _origin_head(master_world) == "refs/remotes/origin/master", (
        f"origin/HEAD not recorded: {_origin_head(master_world)!r}"
    )


def test_create_push_records_origin_head(master_world, monkeypatch) -> None:
    result = invoke(master_world, monkeypatch, ["create", "expedition", TITLE_A, "--push"])
    assert result.exit_code == 0, output_of(result)
    assert _origin_head(master_world) == "refs/remotes/origin/master", (
        f"origin/HEAD not recorded: {_origin_head(master_world)!r}"
    )


def test_no_sync_floors_at_master_despite_stale_origin_main(master_world, monkeypatch) -> None:
    """B pushed EXP-005; A's `create --push` from a feature branch landed EXP-006 on
    origin/master (not in A's checkout). A stale origin/main at the seed commit
    appears, and the remote goes away: the floor is still origin/master."""
    w = master_world
    seed = git(w.a, "rev-parse", "refs/remotes/origin/master").strip()
    to_feature_branch(w)
    b_push_to(w, _EXP_005, [{"id": "EXP-005", "prefix": "EXP", "number": 5}])
    result = invoke(w, monkeypatch, ["create", "expedition", "First From A", "--push"])
    out = output_of(result)
    assert result.exit_code == 0, out
    assert "EXP-006" in out, out
    assert "EXP-006" in git(w.a, "show", f"refs/remotes/origin/master:{ALLOC}")
    assert not list((w.a / EXP_DIR).glob("EXP-00*.md"))

    git(w.a, "update-ref", "refs/remotes/origin/main", seed)
    git(w.a, "remote", "set-url", "origin", str(w.a.parent / "missing.git"))
    spy = NetworkSpy()
    monkeypatch.setattr(subprocess, "run", spy)
    result = invoke(w, monkeypatch, ["next-id", "EXP", "--no-sync", "--json"])
    out = output_of(result)

    assert result.exit_code == 0, out
    assert spy.calls == [], f"--no-sync talked to the remote: {spy.calls}"
    assert allocated(out) == "EXP-007", f"floored at the stale origin/main: {out}"
