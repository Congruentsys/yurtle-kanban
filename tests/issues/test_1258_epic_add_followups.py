# ruff: noqa: F811  (the borrowed `world` fixture)
"""Issue #1258: follow-ups to ``epic add`` / ``voyage add`` (from the review of #1254).

Decided shape, for plain ``epic add``, ``epic add --push`` and ``voyage add``:

1. A target that isn't an epic or voyage is refused. Either type counts on any theme
   (a board can mix presets, #1269); the tests run on nautical, linking to ``voyage``.
   Linking to an expedition, feature or bug exits 1 with a message naming the target
   and its type; nothing is written or pushed.
2. A self-link (``epic add VOY-001 VOY-001``) exits 1; nothing is written or pushed.
3. Plain ``epic add`` treats a padded spelling as already linked, as ``--push`` does
   (``_dup_key``): ``related: [VOY-1]`` already links ``VOY-001``, so it's a no-change
   "already linked", not a second entry.
4. ``--push`` keeps a CRLF item's line endings on origin. ``update --push`` is pinned
   too (``sync_and_push``'s ``Change`` keeps the endings of a file ``read`` returned).

The real-git harness is #1251's (tests/issues/test_1251_epic_add_push.py): a bare
remote, clone A under test and rival clone B, on the nautical theme.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.issues.test_574_claim import frontmatter, output_of
from tests.issues.test_574_sync_and_push import snapshot
from tests.issues.test_574_update_push import remote_bytes
from tests.issues.test_576_cli_update import _assert_only_changed
from tests.issues.test_585_create_push_loop import EXP_DIR, World, git
from tests.issues.test_590_next_id_and_hdd_ids import b_push
from tests.issues.test_1251_epic_add_push import (
    EPIC,
    EPIC_FILE,
    ITEM,
    ITEM_ID,
    _env,  # noqa: F401 (autouse fixture)
    invoke,
    item,
    plain,
    push_from_a,
    voyage,
    world,  # noqa: F401 (fixture)
)
from yurtle_kanban.config import KanbanConfig, PathConfig

MODES = [
    pytest.param("epic", False, id="epic-plain"),
    pytest.param("epic", True, id="epic-push"),
    pytest.param("voyage", False, id="voyage-plain"),
    pytest.param("voyage", True, id="voyage-push"),
]


def argv(group: str, target: str, item_id: str, push: bool) -> list[str]:
    return [group, "add", target, item_id, *(["--push"] if push else [])]


def state(world: World) -> tuple[str, dict[str, bytes]]:
    """Origin's tip and A's checkout: what must not move on a refusal."""
    return world.remote_sha(), snapshot(world.a)


def assert_nothing_changed(world: World, before: tuple[str, Any]) -> None:
    assert world.remote_sha() == before[0], "something was pushed"
    assert snapshot(world.a) == before[1], "A's checkout changed"


def related_after(world: World, push: bool) -> list[str]:
    text = remote_bytes(world, ITEM).decode() if push else (world.a / ITEM).read_text()
    return [str(r) for r in frontmatter(text).get("related") or []]


# --- 1. a target that isn't an epic/voyage is refused ---------------------------------


def seed_exp2(world: World) -> None:
    push_from_a(world, {f"{EXP_DIR}/EXP-002-two.md": plain("EXP-002", "Two")}, "EXP-002")


@pytest.mark.parametrize("group,push", MODES)
def test_expedition_target_is_refused(world, monkeypatch, group, push) -> None:
    """EXP-002 is an ordinary expedition on the nautical board, not a voyage (and
    not already in the item's `related:`, so a noop can't pass for a refusal)."""
    seed_exp2(world)
    before = state(world)

    result = invoke(world, monkeypatch, argv(group, "EXP-002", ITEM_ID, push))

    out = output_of(result)
    assert result.exit_code == 1, out
    assert "EXP-002" in out, out
    assert "expedition" in out.lower(), out
    assert_nothing_changed(world, before)


@pytest.mark.parametrize("push", [False, True], ids=["plain", "push"])
def test_folded_spelling_of_non_epic_target_is_refused(world, monkeypatch, push) -> None:
    """The type is the found item's, whatever spelling named it."""
    seed_exp2(world)
    before = state(world)

    result = invoke(world, monkeypatch, argv("epic", "exp-2", ITEM_ID, push))

    out = output_of(result)
    assert result.exit_code == 1, out
    assert "EXP-002" in out, out
    assert_nothing_changed(world, before)


def test_push_judges_target_type_on_origin(world, monkeypatch) -> None:
    """--push judges the target as origin has it: a rival retyped VOY-001 to an
    expedition, which A hasn't seen."""
    b_push(world, {EPIC_FILE: voyage(EPIC, "First voyage").replace(
        "type: voyage", "type: expedition")})
    before = state(world)

    result = invoke(world, monkeypatch, argv("epic", EPIC, ITEM_ID, True))

    out = output_of(result)
    assert result.exit_code == 1, out
    assert EPIC in out and "expedition" in out.lower(), out
    assert_nothing_changed(world, before)


# --- 2. a self-link is refused ------------------------------------------------------


@pytest.mark.parametrize("group,push", MODES)
def test_self_link_is_refused(world, monkeypatch, group, push) -> None:
    before = state(world)

    result = invoke(world, monkeypatch, argv(group, EPIC, EPIC, push))

    out = output_of(result)
    assert result.exit_code == 1, out
    assert EPIC in out, out
    assert_nothing_changed(world, before)


@pytest.mark.parametrize("push", [False, True], ids=["plain", "push"])
def test_self_link_under_another_spelling_is_refused(world, monkeypatch, push) -> None:
    """`voy-1` is VOY-001 (`_dup_key`): still a self-link."""
    before = state(world)

    result = invoke(world, monkeypatch, argv("epic", EPIC, "voy-1", push))

    assert result.exit_code == 1, output_of(result)
    assert_nothing_changed(world, before)


# --- 3. a padded spelling already links ---------------------------------------------


@pytest.mark.parametrize("group,push", MODES)
@pytest.mark.parametrize("spelling", ["VOY-1", "voy-01"])
def test_padded_spelling_is_already_linked(world, monkeypatch, group, push, spelling) -> None:
    """`related: [VOY-1]` already links VOY-001: a no-change "already linked", no
    second entry (plain is red; --push pins #1251's `_dup_key` check)."""
    push_from_a(world, {ITEM: item(related=f"[EXP-001, {spelling}]")}, "padded link")
    before = state(world)

    result = invoke(world, monkeypatch, argv(group, EPIC, ITEM_ID, push))

    out = output_of(result)
    assert result.exit_code == 0, out
    assert "already linked" in out.lower(), out
    assert related_after(world, push) == ["EXP-001", spelling]
    assert_nothing_changed(world, before)


# --- 4. --push keeps CRLF line endings ------------------------------------------------


def crlf(text: str) -> bytes:
    return text.replace("\n", "\r\n").encode()


def assert_all_crlf(data: bytes) -> None:
    assert b"\r\n" in data, data
    assert data.count(b"\n") == data.count(b"\r\n"), f"a bare LF crept in: {data!r}"


def seed_crlf_item(world: World) -> None:
    (world.a / ITEM).write_bytes(crlf(item()))
    git(world.a, "add", ITEM)
    git(world.a, "commit", "-m", "EXP-003 as CRLF")
    git(world.a, "push", "origin", f"HEAD:refs/heads/{world.default}")
    assert remote_bytes(world, ITEM) == crlf(item())


@pytest.mark.parametrize("group", ["epic", "voyage"])
def test_add_push_keeps_crlf_on_origin(world, monkeypatch, group) -> None:
    seed_crlf_item(world)

    result = invoke(world, monkeypatch, argv(group, EPIC, ITEM_ID, True))

    assert result.exit_code == 0, output_of(result)
    data = remote_bytes(world, ITEM)
    assert_all_crlf(data)
    assert data == crlf(item(related=f"[EXP-001, {EPIC}]"))
    assert (world.a / ITEM).read_bytes() == data, "A's fast-forwarded file lost CRLF"


def test_add_push_without_remote_keeps_crlf(world, monkeypatch) -> None:
    seed_crlf_item(world)
    git(world.a, "remote", "remove", "origin")

    result = invoke(world, monkeypatch, argv("epic", EPIC, ITEM_ID, True))

    assert result.exit_code == 0, output_of(result)
    assert (world.a / ITEM).read_bytes() == crlf(item(related=f"[EXP-001, {EPIC}]"))


def test_update_push_keeps_crlf_on_origin(world, monkeypatch) -> None:
    seed_crlf_item(world)

    result = invoke(world, monkeypatch, ["update", ITEM_ID, "--priority", "high", "--push"])

    assert result.exit_code == 0, output_of(result)
    data = remote_bytes(world, ITEM)
    assert_all_crlf(data)
    assert data == crlf(item().replace("priority: medium", "priority: high"))


def test_control_plain_add_keeps_crlf(world, monkeypatch) -> None:
    """Plain `epic add` already keeps CRLF (#151): the line --push must match."""
    seed_crlf_item(world)
    git(world.a, "reset", "--hard")  # A's file is the CRLF one

    result = invoke(world, monkeypatch, argv("epic", EPIC, ITEM_ID, False))

    assert result.exit_code == 0, output_of(result)
    assert (world.a / ITEM).read_bytes() == crlf(item(related=f"[EXP-001, {EPIC}]"))


# --- controls: a real voyage / epic still links --------------------------------------


@pytest.mark.parametrize("group,push", MODES)
def test_control_real_voyage_links(world, monkeypatch, group, push) -> None:
    base = world.remote_sha()
    old = remote_bytes(world, ITEM)

    result = invoke(world, monkeypatch, argv(group, EPIC, ITEM_ID, push))

    assert result.exit_code == 0, output_of(result)
    new = remote_bytes(world, ITEM) if push else (world.a / ITEM).read_bytes()
    _assert_only_changed(old, new, {"related": ["EXP-001", EPIC]})
    assert (world.remote_sha() != base) is push


def software_world(tmp_path: Path) -> World:
    """The same harness on the software theme: EPIC-001 (an epic), FEAT-001 and
    FEAT-002 (features), BUG-001 (a bug)."""
    w = World(tmp_path)
    KanbanConfig(
        theme="software",
        paths=PathConfig(
            root="kanban-work/",
            scan_paths=["kanban-work/expeditions/", "kanban-work/signals/"],
        ),
    ).save(w.a / ".kanban" / "config.yaml")

    def sw(item_id: str, kind: str) -> str:
        return (
            f'---\nid: {item_id}\ntitle: "{item_id}"\ntype: {kind}\nstatus: backlog\n'
            f"priority: medium\nrelated: []\n---\n\n# {item_id}\n\nA description.\n"
        )

    push_from_a(
        w,
        {
            f"{EXP_DIR}/EPIC-001-epic.md": sw("EPIC-001", "epic"),
            f"{EXP_DIR}/FEAT-001-one.md": sw("FEAT-001", "feature"),
            f"{EXP_DIR}/FEAT-002-two.md": sw("FEAT-002", "feature"),
            f"{EXP_DIR}/BUG-001-bug.md": sw("BUG-001", "bug"),
        },
        "software theme seed",
    )
    return w


@pytest.mark.parametrize("push", [False, True], ids=["plain", "push"])
def test_control_software_epic_links(tmp_path, monkeypatch, push) -> None:
    w = software_world(tmp_path)
    rel = f"{EXP_DIR}/FEAT-002-two.md"

    result = invoke(w, monkeypatch, argv("epic", "EPIC-001", "FEAT-002", push))

    assert result.exit_code == 0, output_of(result)
    text = remote_bytes(w, rel).decode() if push else (w.a / rel).read_text()
    assert frontmatter(text)["related"] == ["EPIC-001"]


@pytest.mark.parametrize("push", [False, True], ids=["plain", "push"])
@pytest.mark.parametrize("target,kind", [("FEAT-001", "feature"), ("BUG-001", "bug")])
def test_software_non_epic_target_is_refused(tmp_path, monkeypatch, push, target, kind):
    w = software_world(tmp_path)
    before = state(w)

    result = invoke(w, monkeypatch, argv("epic", target, "FEAT-002", push))

    out = output_of(result)
    assert result.exit_code == 1, out
    assert target in out and kind in out.lower(), out
    assert_nothing_changed(w, before)

