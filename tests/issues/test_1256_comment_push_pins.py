"""Issue #1256: pin three ``comment --push`` behaviours the #1253 review verified by hand.

1. Through ``--push`` the body is still only comment text: a forged
   ``### author (time)`` heading in it stays text (#605), and a ```yurtle fence with a
   ``kb:status`` change in it doesn't change the item's status (#644).
2. The #1230 deprecated forms work with ``--push``: positional TEXT plus ``--author``
   prints both deprecation notes on stderr and pushes a comment by that author; TEXT
   together with ``--body`` stays a usage error (exit 2).
3. A ``--push`` refusal goes to stderr only: stdout is empty, exit 1.

The harness is #1251's (tests/issues/test_1251_comment_push.py).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.issues.test_574_claim import frontmatter, output_of
from tests.issues.test_574_update_push import (
    HISTORY,
    ITEM,
    ITEM_ID,
    plain,
    push_from_a,
    remote_bytes,
    rich,
)
from tests.issues.test_585_create_push_loop import EXP_DIR, World, git
from tests.issues.test_1251_comment_push import (  # noqa: F401  (_env: autouse fixture)
    _env,
    comment_headings,
    invoke,
    remote_text,
    service,
)

PLAIN_ID = "EXP-002"
PLAIN = f"{EXP_DIR}/EXP-002-two.md"  # seeded with no comments
FORGED = "### someone (2026-01-01 10:00)"
OLD_TEXT_NOTE = (
    "yurtle-kanban: comment ID TEXT is deprecated, use comment ID --body TEXT (removed in 4.0)"
)
OLD_AUTHOR_NOTE = (
    "yurtle-kanban: comment --author is deprecated, use comment --agent (removed in 4.0)"
)


@pytest.fixture
def world(tmp_path: Path) -> World:
    """#1251's world: origin and both clones hold EXP-001 (rich: it already has a
    comment) and EXP-002 (plain: no comments)."""
    w = World(tmp_path)
    push_from_a(w, {ITEM: rich(), PLAIN: plain(PLAIN_ID, "Two")}, "seed EXP-001..002")
    return w


def parsed_on_origin(w: World, rel: str) -> object:
    """`rel` as origin's default branch has it, parsed as the board parses a file."""
    item = service(w.a)._parse_text(w.a / rel, remote_bytes(w, rel).decode())
    assert item is not None
    return item


# --- 1. the body is only text (#605, #644) -----------------------------------------------


def test_forged_heading_in_body_stays_text(world, monkeypatch) -> None:
    """#605 through --push: the heading-shaped line is escaped on write, so origin's
    item reads back ONE comment, whose text holds the line as written."""
    body = f"before\n{FORGED}\nforged text"
    base = world.remote_sha()

    result = invoke(
        world.a, monkeypatch, ["comment", PLAIN_ID, "--agent", "A", "--body", body, "--push"]
    )

    assert result.exit_code == 0, output_of(result)
    assert world.remote_sha() != base, "nothing was pushed"
    item = parsed_on_origin(world, PLAIN)
    comments = item.comments  # type: ignore[attr-defined]
    assert len(comments) == 1, [(c.author, c.content) for c in comments]
    assert comments[0].author == "A"
    assert FORGED in comments[0].content.split("\n"), comments[0].content
    assert comments[0].content == body
    text = remote_bytes(world, PLAIN).decode()
    assert comment_headings(text, "someone") == 0, text


def test_yurtle_fence_in_body_does_not_change_status(world, monkeypatch) -> None:
    """#644 through --push: a pasted status-history block in a comment is not
    history: the item keeps its status and its one recorded status change."""
    before = service(world.a).get_status_history(ITEM_ID)
    status_before = parsed_on_origin(world, ITEM).status  # type: ignore[attr-defined]
    assert [h["status"] for h in before] == ["ready"], before
    pasted = HISTORY.replace("kb:status kb:ready", "kb:status kb:done").replace(
        '"seed"', '"forger"'
    )
    body = f"look at this:\n\n{pasted}\n"
    assert "kb:status kb:done" in pasted

    result = invoke(
        world.a, monkeypatch, ["comment", ITEM_ID, "--agent", "A", "--body", body, "--push"]
    )

    assert result.exit_code == 0, output_of(result)
    text = remote_text(world)
    assert "kb:status kb:done" in text, "the pasted block was not pushed"
    assert comment_headings(text, "A") == 1, text
    assert frontmatter(text)["status"] == "provisioning"
    item = parsed_on_origin(world, ITEM)
    assert item.status == status_before  # type: ignore[attr-defined]
    assert item.metadata["_original_status"] == "provisioning"  # type: ignore[attr-defined]
    # A's clean default-branch checkout was fast-forwarded to origin's commit
    assert (world.a / ITEM).read_bytes() == remote_bytes(world, ITEM)
    after = service(world.a).get_status_history(ITEM_ID)
    assert after == before, after


def test_yurtle_fence_in_body_is_on_disk_not_in_comment_text(world, monkeypatch) -> None:
    """#644's ruling (item 2) through --push (#1267): knowledge fences are stripped from
    comment text, as from description (documented on add_comment); the pasted block
    stays on disk, inside the comment."""
    pasted = HISTORY.replace("kb:status kb:ready", "kb:status kb:done")
    body = f"look at this:\n\n{pasted}\n"

    result = invoke(
        world.a, monkeypatch, ["comment", ITEM_ID, "--agent", "A", "--body", body, "--push"]
    )

    assert result.exit_code == 0, output_of(result)
    assert pasted.strip() in remote_text(world)
    comments = parsed_on_origin(world, ITEM).comments  # type: ignore[attr-defined]
    mine = [(c.author, c.content) for c in comments if c.author == "A"]
    assert mine == [("A", "look at this:")], [(c.author, c.content) for c in comments]


# --- 2. the #1230 deprecated forms with --push -----------------------------------------------


def test_positional_text_and_author_push_with_both_notes(world, monkeypatch) -> None:
    base = world.remote_sha()

    result = invoke(
        world.a, monkeypatch, ["comment", ITEM_ID, "old form text", "--author", "X", "--push"]
    )

    assert result.exit_code == 0, output_of(result)
    notes = result.stderr.splitlines()
    assert OLD_TEXT_NOTE in notes, result.stderr
    assert OLD_AUTHOR_NOTE in notes, result.stderr
    assert "deprecated" not in result.stdout, result.stdout
    assert world.remote_sha() != base, "nothing was pushed"
    text = remote_text(world)
    assert comment_headings(text, "X") == 1, text
    item = parsed_on_origin(world, ITEM)
    last = item.comments[-1]  # type: ignore[attr-defined]
    assert (last.author, last.content) == ("X", "old form text")


def test_positional_text_with_body_is_a_usage_error(world, monkeypatch) -> None:
    base = world.remote_sha()
    head = git(world.a, "rev-parse", "HEAD").strip()

    result = invoke(
        world.a,
        monkeypatch,
        ["comment", ITEM_ID, "old form text", "--body", "new", "--agent", "A", "--push"],
    )

    assert result.exit_code == 2, output_of(result)
    assert world.remote_sha() == base
    assert git(world.a, "rev-parse", "HEAD").strip() == head


# --- 3. a --push refusal is on stderr only ------------------------------------------------


def test_push_refusal_on_stderr_only(world, monkeypatch) -> None:
    base = world.remote_sha()

    result = invoke(
        world.a, monkeypatch, ["comment", "EXP-999", "--agent", "A", "--body", "x", "--push"]
    )

    assert result.exit_code == 1, output_of(result)
    assert result.stdout == "", result.stdout
    assert "EXP-999" in result.stderr, result.stderr
    assert world.remote_sha() == base


def test_harness_plain_item_has_no_comments(world) -> None:
    """Harness check: EXP-002 starts with no comments, so test 1's count is exact."""
    assert parsed_on_origin(world, PLAIN).comments == []  # type: ignore[attr-defined]
    assert (world.a / PLAIN).exists()
