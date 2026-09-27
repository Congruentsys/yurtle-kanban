"""Issue #625 — follow-ups from the review of PR #621 (#603).

1. ``epic create --push --items X`` on a feature branch: the push lands the epic on
   origin/main but NOT in this checkout, yet ``_do_create`` still calls
   ``_update_item_related`` and writes the missing epic's id into X's ``related:``.
   Decided (steer, G1: no link to a missing epic): skip the local linking, leave X's
   file byte-for-byte unchanged, print no ``Linked`` line, and tell the user to pull
   and then run ``epic add`` (or ``voyage add``) for X.
2. Control: on the default branch the epic is in this checkout, so ``--items X``
   links X as before. Non-push ``--items`` on a feature branch also still links.
3. One shared pull-note helper: the note printed by ``epic create --push``, by an HDD
   ``idea create --push`` and by core ``create --push`` on a feature branch is the
   same text apart from the item id; and the note's literal lives in one source
   file (it is copied into ``cli.py``, ``hdd_commands.py`` and ``epic_commands.py``).

Reuses #585's real-git harness (bare remote + clone, feature branch scenario) and
#603's board reconfiguration helpers.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.issues.test_585_create_push_loop import (
    FEATURE,
    TITLE,
    World,
    git,
    output_of,
    scenario_feature_branch,
)
from tests.issues.test_603_push_failure_messages import (
    HDD_DIRS,
    invoke,
    nautical_with_voyages,
    reconfigure,
    remote_files_titled,
)
from yurtle_kanban import config as config_mod

SRC = Path(__file__).resolve().parents[2] / "src" / "yurtle_kanban"
X_ID = "EXP-001"
X_REL = f"kanban-work/expeditions/{X_ID}-linkable.md"
X_DOC = (
    "---\n"
    f"id: {X_ID}\n"
    'title: "Linkable"\n'
    "type: expedition\n"
    "status: backlog\n"
    "---\n\n# Linkable\n"
)
VOYAGES = "kanban-work/voyages/"


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    return World(tmp_path)


@pytest.fixture(autouse=True)
def _clean_theme_cache() -> Iterator[None]:
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


def seed_x(world: World) -> Path:
    """Commit expedition X on main and push it (so it is on every branch after)."""
    path = world.a / X_REL
    path.write_text(X_DOC)
    git(world.a, "add", "-A")
    git(world.a, "commit", "-m", f"seed {X_ID}")
    git(world.a, "push", "origin", "main")
    return path


def flat(out: str) -> str:
    return " ".join(out.split())


# --- 1. feature branch: no link to an epic that isn't in this checkout ----------------------


@pytest.mark.parametrize("group", ["epic", "voyage"])
def test_push_items_on_feature_branch_does_not_link_missing_epic(
    world, monkeypatch, group
) -> None:
    nautical_with_voyages(world)
    x = seed_x(world)
    scenario_feature_branch(world, monkeypatch)
    before = x.read_bytes()

    result = invoke(world, monkeypatch, [group, "create", TITLE, "--push", "--items", X_ID])
    out = output_of(result)
    text = flat(out)

    assert result.exit_code == 0, out
    assert git(world.a, "rev-parse", "--abbrev-ref", "HEAD").strip() == FEATURE
    assert len(remote_files_titled(world, TITLE, VOYAGES)) == 1, world.remote_files()
    assert not list((world.a / VOYAGES).glob("VOY-*.md")), "fixture: epic is local"

    assert x.read_bytes() == before, (
        f"{X_ID} was linked to an epic not in this checkout:\n{x.read_text()}"
    )
    assert not re.search(rf"Linked\s+{X_ID}", out), f"claims a link it must not make: {out}"
    assert re.search(r"\bpull\b", text, re.I), f"no hint to pull: {out}"
    assert re.search(rf"\b(epic|voyage) add\b[^.]*\b{X_ID}\b", text), (
        f"no hint to run `epic add ... {X_ID}`: {out}"
    )


# --- 2. controls: the epic is in this checkout, so --items links as before -------------------


def test_push_items_on_default_branch_links(world, monkeypatch) -> None:
    nautical_with_voyages(world)
    x = seed_x(world)

    result = invoke(world, monkeypatch, ["epic", "create", TITLE, "--push", "--items", X_ID])
    out = output_of(result)

    assert result.exit_code == 0, out
    epics = list((world.a / VOYAGES).glob("VOY-*.md"))
    assert len(epics) == 1, f"epic not in this checkout on main: {epics}"
    epic_id = re.match(r"(VOY-\d+)", epics[0].name).group(1)  # type: ignore[union-attr]
    assert epic_id in x.read_text(), f"{X_ID} not linked on main:\n{x.read_text()}"
    assert re.search(rf"Linked\s+{X_ID}", out), out


def test_no_push_items_on_feature_branch_links(world, monkeypatch) -> None:
    nautical_with_voyages(world)
    x = seed_x(world)
    scenario_feature_branch(world, monkeypatch)

    result = invoke(world, monkeypatch, ["epic", "create", TITLE, "--items", X_ID])
    out = output_of(result)

    assert result.exit_code == 0, out
    epics = list((world.a / VOYAGES).glob("VOY-*.md"))
    assert len(epics) == 1, epics
    epic_id = re.match(r"(VOY-\d+)", epics[0].name).group(1)  # type: ignore[union-attr]
    assert epic_id in x.read_text(), x.read_text()


# --- 3. one pull-note format for every create --push ----------------------------------------

ID_TOKEN = re.compile(r"\b[A-Z]+-\d+(?:\.\d+)?\b")


def _pull_note(out: str) -> str:
    """The pull note, whitespace-flattened, with any item id replaced by <ID>."""
    lines = [ln for ln in out.splitlines() if "not in this checkout" in ln]
    assert len(lines) == 1, f"expected one pull note line: {out}"
    return ID_TOKEN.sub("<ID>", flat(lines[0]))


def _note_for(world: World, monkeypatch, argv: list[str]) -> str:
    scenario_feature_branch(world, monkeypatch)
    result = invoke(world, monkeypatch, argv)
    out = output_of(result)
    assert result.exit_code == 0, out
    return _pull_note(out)


def test_epic_and_hdd_pull_notes_match(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    for name in ("epic", "hdd", "core"):
        (tmp_path / name).mkdir()
    w_epic = World(tmp_path / "epic")
    nautical_with_voyages(w_epic)
    epic_note = _note_for(w_epic, monkeypatch, ["epic", "create", TITLE, "--push"])

    w_hdd = World(tmp_path / "hdd")
    reconfigure(w_hdd, "hdd", "research/", [f"research/{d}/" for d in HDD_DIRS])
    hdd_note = _note_for(w_hdd, monkeypatch, ["idea", "create", TITLE, "--push"])

    w_core = World(tmp_path / "core")
    core_note = _note_for(w_core, monkeypatch, ["create", "expedition", TITLE, "--push"])

    assert epic_note == hdd_note == core_note, (epic_note, hdd_note, core_note)


def test_pull_note_text_is_defined_once() -> None:
    """The note's literal is in one helper, not copied per command (#625)."""
    holders = sorted(
        p.name for p in SRC.glob("*.py") if "; not in this checkout yet: " in p.read_text()
    )
    assert len(holders) == 1, f"pull-note text copied into {holders}"
