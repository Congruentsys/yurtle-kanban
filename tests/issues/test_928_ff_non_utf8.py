"""Issue #928 — a non-UTF-8 name on origin crashes the post-push fast-forward.

``_fast_forward_to`` (service.py ~2524) runs ``git merge --ff-only`` through
``_git_run`` in text mode. When origin holds a file whose name isn't UTF-8, git's
stderr names it with a raw ``\\xff`` byte, and decoding that stderr raises
``UnicodeDecodeError`` after the push has already landed: the user gets a traceback
for a create or an allocation that succeeded. Its contract is "never fails" (#603).

The [steer] on #928 (bucket 1) gives the spec: ``_fast_forward_to`` runs its git
calls raw (``text=False``) and decodes their output with ``errors="replace"`` for
display, so it never raises. When git names a file it couldn't check out, the
command still reports the push as landed (exit 0) and warns that the checkout
wasn't updated.

Reds:
- ``next-id EXP`` with a remote, origin holding ``EXP-001-\\xff.md``: exit 0, no
  traceback, the allocation landed on origin, and the output (or the log) says the
  checkout wasn't updated;
- ``create expedition "T" --push``, same origin: exit 0, no traceback, the item
  landed on origin, and the output says the checkout doesn't show it yet;
- ``_fast_forward_to`` called directly, the merge failing with a raw ``\\xff`` in its
  stderr (the git call monkeypatched to hand back bytes): returns False, no raise.
Where the filesystem takes the name (Linux), git checks it out and the fast-forward
succeeds, so no crash is reachable through the CLI there and the "not updated" check
applies only when the checkout was in fact left behind; the direct test is the
platform-independent red.
Controls (green before and after): the same next-id / create with a plain origin
fast-forward the checkout (HEAD equals origin/main afterwards).

The ``\\xff`` name is built in git objects only (``hash-object -w``, a temporary
index's ``update-index --cacheinfo``, ``write-tree``, ``commit-tree``, push): APFS
refuses it on disk (#895's review). Reuses the #585/#590 real-git harness.
"""

from __future__ import annotations

import logging
import os
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from tests.issues.test_585_create_push_loop import (
    _ORIG_RUN,
    EXP_DIR,
    World,
    _no_traceback,
    git,
    output_of,
)
from tests.test_634_explicit_ids_on_base import service
from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import main

BAD_NAME = f"{EXP_DIR}/EXP-001-".encode() + b"\xff.md"
PLAIN_NAME = f"{EXP_DIR}/EXP-001-plain.md"
ITEM = (
    "---\nid: EXP-001\ntitle: \"Odd name\"\ntype: expedition\nstatus: backlog\n---\n\n"
    "# Odd name\n"
)
NOT_UPDATED = ("not updated", "not in this checkout", "does not show")


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    return World(tmp_path)


@pytest.fixture(autouse=True)
def _clean_theme_cache() -> Iterator[None]:
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


def _git_bytes(cwd: Path, *args: bytes | str, env: dict[str, str] | None = None) -> str:
    done = _ORIG_RUN(
        ["git", *args], cwd=cwd, capture_output=True, check=False,
        env={**os.environ, **(env or {})},
    )
    assert done.returncode == 0, f"git {args!r}: {done.stderr!r}"
    return done.stdout.decode("utf-8", "replace").strip()


def push_bad_name(world: World, tmp_path: Path) -> None:
    """B commits ITEM at the non-UTF-8 BAD_NAME on top of origin/main, in git
    objects only (the filesystem may refuse the name), and pushes it."""
    b = world.b
    git(b, "fetch", "origin")
    base = git(b, "rev-parse", "origin/main").strip()
    body = tmp_path / "bad-body"
    body.write_text(ITEM)
    blob = _git_bytes(b, "hash-object", "-w", "--no-filters", "--", str(body))
    index = {"GIT_INDEX_FILE": str(tmp_path / "bad-index")}
    _git_bytes(b, "read-tree", base, env=index)
    _git_bytes(b, "update-index", "--add", "--cacheinfo", "100644", blob, BAD_NAME, env=index)
    tree = _git_bytes(b, "write-tree", env=index)
    sha = _git_bytes(b, "commit-tree", tree, "-p", base, "-m", "odd name")
    _git_bytes(b, "push", "origin", f"{sha}:refs/heads/main")
    git(world.a, "fetch", "origin")


def push_plain(world: World) -> None:
    git(world.b, "fetch", "origin")
    git(world.b, "reset", "--hard", "origin/main")
    (world.b / PLAIN_NAME).write_text(ITEM)
    git(world.b, "add", "-A")
    git(world.b, "commit", "-m", "plain")
    git(world.b, "push", "origin", "HEAD:refs/heads/main")


def invoke(world: World, monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> Any:
    monkeypatch.chdir(world.a)
    return CliRunner().invoke(main, argv)


def says_not_updated(text: str) -> bool:
    low = " ".join(text.lower().split())
    return any(p in low for p in NOT_UPDATED)


def a_head(world: World) -> str:
    return git(world.a, "rev-parse", "HEAD").strip()


# --- next-id --------------------------------------------------------------------------------


def test_next_id_survives_non_utf8_name_on_origin(world, tmp_path, monkeypatch, caplog) -> None:
    push_bad_name(world, tmp_path)
    before = world.remote_sha()
    with caplog.at_level(logging.WARNING):
        result = invoke(world, monkeypatch, ["next-id", "EXP"])
    out = output_of(result)

    _no_traceback(result, out)
    assert result.exit_code == 0, out
    assert world.remote_sha() != before, "the allocation never landed on origin"
    assert "EXP-002" in out, out
    if a_head(world) != world.remote_sha():  # APFS: the \xff file can't be checked out
        assert says_not_updated(out) or says_not_updated(caplog.text), (
            f"no word that the checkout wasn't updated: {out!r} / {caplog.text!r}"
        )


def test_control_next_id_fast_forwards(world, monkeypatch) -> None:
    push_plain(world)
    result = invoke(world, monkeypatch, ["next-id", "EXP"])
    out = output_of(result)

    assert result.exit_code == 0, out
    assert "EXP-002" in out, out
    assert a_head(world) == world.remote_sha(), "the checkout was not fast-forwarded"


# --- create --push --------------------------------------------------------------------------


def test_create_push_survives_non_utf8_name_on_origin(world, tmp_path, monkeypatch) -> None:
    push_bad_name(world, tmp_path)
    before = world.remote_sha()
    result = invoke(world, monkeypatch, ["create", "expedition", "T", "--push"])
    out = output_of(result)

    _no_traceback(result, out)
    assert result.exit_code == 0, out
    assert world.remote_sha() != before, "the item never landed on origin"
    assert "EXP-002" in out, out
    if a_head(world) != world.remote_sha():  # APFS: the \xff file can't be checked out
        assert says_not_updated(out), f"no word that the checkout wasn't updated: {out!r}"


def test_control_create_push_fast_forwards(world, monkeypatch) -> None:
    push_plain(world)
    result = invoke(world, monkeypatch, ["create", "expedition", "T", "--push"])
    out = output_of(result)

    assert result.exit_code == 0, out
    assert "EXP-002" in out, out
    assert a_head(world) == world.remote_sha(), "the checkout was not fast-forwarded"


# --- _fast_forward_to directly -------------------------------------------------------------


def _merge_fails_with_raw_ff(real: Any) -> Any:
    """subprocess.run: a `git merge` fails with a raw \\xff in its stderr, as git's
    does when it names a file it couldn't check out; decoded as text mode would."""

    def fake(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess:
        cmd = args[0] if args else kwargs.get("args")
        if isinstance(cmd, (list, tuple)) and "merge" in [str(c) for c in cmd[1:]]:
            stderr = b"error: unable to create file " + BAD_NAME + b": Illegal byte sequence\n"
            if kwargs.get("text") or kwargs.get("universal_newlines") or kwargs.get("encoding"):
                stderr.decode("utf-8")  # what text mode does: raises on the \xff
            return subprocess.CompletedProcess(cmd, 128, b"", stderr)
        return real(*args, **kwargs)

    return fake


def test_fast_forward_to_never_raises_on_raw_ff_stderr(world, monkeypatch) -> None:
    sha = a_head(world)
    monkeypatch.setattr(subprocess, "run", _merge_fails_with_raw_ff(_ORIG_RUN))
    got = service(world)._fast_forward_to("main", sha)
    assert got is False, got


def test_control_fast_forward_to_plain(world) -> None:
    push_plain(world)
    git(world.a, "fetch", "origin")
    sha = world.remote_sha()
    assert service(world)._fast_forward_to("main", sha) is True
    assert a_head(world) == sha
