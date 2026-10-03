"""Issue #1274 (from the review of PR #1272): older git's ref-lock race counts when its
`error: cannot lock ref … but expected …` line comes without the `remote: ` prefix,
and still never without a real `(failed to update ref)` refusal line."""

from __future__ import annotations

from yurtle_kanban.service import _lost_race

SHA_A, SHA_B = "1" * 40, "2" * 40
LOCK = f"error: cannot lock ref 'refs/heads/main': is at {SHA_A} but expected {SHA_B}"
REFUSED = " ! [remote rejected] main -> main (failed to update ref)"
TAIL = "error: failed to push some refs to '/srv/origin.git'"


def test_unprefixed_older_git_lock_line_is_a_lost_race() -> None:
    assert _lost_race("\n".join(["To /srv/origin.git", LOCK, REFUSED, TAIL]))


def test_unprefixed_lock_line_without_the_refusal_is_not() -> None:
    hook = " ! [remote rejected] main -> main (pre-receive hook declined)"
    assert not _lost_race("\n".join(["To /srv/origin.git", LOCK, hook, TAIL]))


def test_held_lock_reference_already_exists_is_not_retried() -> None:
    err = "\n".join([
        "remote: error: cannot lock ref 'refs/heads/main': Unable to create "
        "'/srv/origin.git/refs/heads/main.lock': File exists.",
        "To /srv/origin.git",
        " ! [remote rejected] main -> main (reference already exists)",
        TAIL,
    ])
    assert not _lost_race(err)
