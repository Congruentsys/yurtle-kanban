"""Issue #1062: the fast-forward-refused note strips only each git line's leading
`error:`/`fatal:` label.

#1057 ran `_without_git_prefix` over the *joined* reason, so a label-shaped text
mid-line went too: an untracked file named `error: odd.md` blocking the
fast-forward was reported as `odd.md`, and `"an error: in text"` became
`"an in text"`. A bare `"error:"` left nothing: `(fast-forward refused: )`.

Now `_fast_forward_to` strips each kept git line's leading label before joining
(`_git_refusal`), keeps git's raw text for the logged warning, and falls back to
the raw text when nothing is left.
"""

from __future__ import annotations

import logging

from tests.issues.test_585_create_push_loop import git
from tests.issues.test_1043_ff_warning import (  # noqa: F401  (fixtures)
    _clean_theme_cache,
    world,
)
from tests.test_634_explicit_ids_on_base import service
from yurtle_kanban.service import _git_refusal, pull_note_text

ODD = "error: odd.md"


# --- unit: the one strip site, `_git_refusal`'s reason (#1138) --------------------


def _reason(line: str) -> str | None:
    return _git_refusal(line + "\n")[1]


def test_leading_label_dropped() -> None:
    assert _reason("fatal: Not possible to fast-forward, aborting.") == (
        "Not possible to fast-forward, aborting."
    )


def test_label_mid_text_kept() -> None:
    assert _reason("error: an error: in text") == "an error: in text"


def test_file_named_like_a_label_mid_line_kept() -> None:
    why = f"The following untracked working tree files would be overwritten: {ODD}"
    assert _reason(f"error: {why}") == why


def test_bare_label_falls_back_to_raw() -> None:
    assert _reason("  fatal:  ") == "fatal:"  # `error:` alone: see below


def test_note_never_reads_empty_refusal() -> None:
    note = pull_note_text("main", why="error:")
    assert "(fast-forward refused: )" not in note, note
    assert "(fast-forward refused: error:)" in note, note


def test_note_keeps_mid_text_label() -> None:
    note = pull_note_text("main", why="an error: in text")
    assert "(fast-forward refused: an error: in text)" in note, note


# --- unit: _git_refusal (per-line strip of git's output) ----------------------------


def test_multi_line_output_strips_each_leading_label() -> None:
    out = (
        "error: The following untracked working tree files would be overwritten "
        "by merge:\n"
        f"\t{ODD}\n"
        "Please move or remove them before you merge.\n"
        "Aborting\n"
        "fatal: Not possible to fast-forward, aborting.\n"
    )
    raw, reason = _git_refusal(out)
    assert reason == (
        "The following untracked working tree files would be overwritten by merge: "
        f"{ODD} Not possible to fast-forward, aborting."
    ), reason
    # the raw text, for the log, keeps git's labels
    assert raw is not None and raw.startswith("error: The following"), raw
    assert "fatal: Not possible" in raw, raw
    assert f"merge: {ODD}" in raw, raw


def test_hint_lines_dropped() -> None:
    out = "hint: Diverging branches can't be fast-forwarded.\nfatal: Not possible.\n"
    raw, reason = _git_refusal(out)
    assert reason == "Not possible.", reason
    assert raw == "fatal: Not possible.", raw


def test_bare_label_line_falls_back_to_raw() -> None:
    raw, reason = _git_refusal("error:\n")
    assert raw == "error:", raw
    assert reason == "error:", reason


def test_no_git_failure_lines() -> None:
    assert _git_refusal("hint: nothing to see\n") == (None, None)
    assert _git_refusal("") == (None, None)


# --- end to end: real git, an untracked `error: odd.md` blocks the fast-forward ----


def test_untracked_file_named_like_a_label_is_named_in_the_note(world, caplog) -> None:  # noqa: F811
    git(world.b, "pull", "--quiet", "origin", "main")
    (world.b / ODD).write_text("theirs\n")
    git(world.b, "add", "-A")
    git(world.b, "commit", "-m", "odd file from B")
    git(world.b, "push", "origin", "HEAD:refs/heads/main")
    git(world.a, "fetch", "origin")
    sha = world.remote_sha()
    (world.a / ODD).write_text("mine, untracked\n")

    svc = service(world)
    with caplog.at_level(logging.WARNING):
        assert svc._fast_forward_to("main", sha, warn=True) is False

    why = svc._ff_why
    assert why is not None
    assert ODD in why, f"the file name lost its 'error: ' (#1062): {why!r}"
    assert not why.lower().startswith(("error:", "fatal:")), why
    note = pull_note_text("main", why=why)
    assert ODD in note, note

    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "error: The following untracked" in logged, (
        f"the log keeps git's raw text: {logged!r}"
    )
    assert ODD in logged, logged
