"""Issue #637 — follow-ups from the review of PR #632 (#625).

1. One source for the pull-note wording. ``_click.pull_note()`` builds the CLI line
   "Pushed to origin/<b>; not in this checkout yet: pull <b> to see it", while
   ``service.create_item_and_push`` builds its own copy for ``result["message"]``
   (what API and MCP callers see). Decided: both come from one plain-text core. Tested
   by behaviour, not by name: for a push that lands off-checkout (feature branch), the
   service's ``result["message"]`` contains the plain text of ``pull_note(result)``
   (Rich markup stripped), and both name ``origin/<branch>`` and ``pull <branch>``.
   (``pull_note`` itself is named here only because it is the existing CLI entry point
   the issue names; no new helper name is asserted.)
2. The default-branch ``--items`` warning of ``voyage create --push`` says "voyage",
   not "epic" (use ``type_label``). Control: a software-theme ``epic create --push``
   still says "epic".

Reuses #585's real-git harness and #603/#625's board helpers.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from rich.text import Text

from tests.issues.test_585_create_push_loop import (
    TITLE,
    World,
    output_of,
    scenario_feature_branch,
)
from tests.issues.test_603_push_failure_messages import (
    invoke,
    nautical_with_voyages,
    reconfigure,
)
from tests.issues.test_625_epic_items_pull_note import seed_x
from yurtle_kanban import config as config_mod
from yurtle_kanban._click import pull_note
from yurtle_kanban.models import WorkItemType


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    return World(tmp_path)


@pytest.fixture(autouse=True)
def _clean_theme_cache() -> Iterator[None]:
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


def flat(text: str) -> str:
    return " ".join(text.split())


def plain(markup: str) -> str:
    """Rich markup rendered to its plain text."""
    return flat(Text.from_markup(markup).plain)


# --- 1. one source for the pull-note wording -----------------------------------------------


def test_service_message_carries_the_cli_pull_note_text(world, monkeypatch) -> None:
    scenario_feature_branch(world, monkeypatch)
    monkeypatch.chdir(world.a)
    from yurtle_kanban.cli import get_service

    result = get_service().create_item_and_push(WorkItemType.EXPEDITION, TITLE)

    assert result.get("success"), result
    assert result.get("local") is False, f"fixture: push should land off-checkout: {result}"
    branch = result["branch"]
    message = flat(result["message"])
    note = plain(pull_note(result))

    assert f"origin/{branch}" in note and f"pull {branch}" in note, note
    assert f"origin/{branch}" in message and f"pull {branch}" in message, message
    assert note in message, (
        "service message does not carry the CLI pull-note text "
        f"(two copies of the wording):\n  note:    {note!r}\n  message: {message!r}"
    )


# --- 2. the default-branch --items warning names the right kind ----------------------------


def _push_warning(out: str) -> str:
    """The --push warning sentence, whitespace-flattened (so a wrapped line still reads)."""
    found = re.findall(r"--push committed only[^.;]*", flat(out), re.I)
    assert len(found) == 1, f"expected one --push warning: {out}"
    return found[0]


def test_voyage_items_warning_says_voyage(world, monkeypatch) -> None:
    nautical_with_voyages(world)
    seed_x(world)

    result = invoke(world, monkeypatch, ["voyage", "create", TITLE, "--push", "--items", "EXP-001"])
    out = output_of(result)
    assert result.exit_code == 0, out

    warning = _push_warning(out)
    assert re.search(r"\bvoyage\b", warning, re.I), f"warning doesn't say voyage: {warning}"
    assert not re.search(r"\bepic\b", warning, re.I), f"voyage warning says epic: {warning}"


def test_software_epic_items_warning_says_epic(world, monkeypatch) -> None:
    reconfigure(world, "software", "kanban-work/", ["kanban-work/epics/", "kanban-work/tasks/"])

    result = invoke(world, monkeypatch, ["epic", "create", TITLE, "--push", "--items", "TASK-999"])
    out = output_of(result)
    assert result.exit_code == 0, out
    assert "EPIC-" in out, f"fixture: expected a software epic: {out}"

    warning = _push_warning(out)
    assert re.search(r"\bepic\b", warning, re.I), f"epic warning doesn't say epic: {warning}"
    assert not re.search(r"\bvoyage\b", warning, re.I), warning
