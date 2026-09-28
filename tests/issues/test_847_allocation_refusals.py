"""Issue #847 — allocation refusals say "nothing was changed"; ``next-id --json``
refuses in JSON.

Follow-up from the PR #843 review (#818). Decided behaviour ([steer] on #847,
bucket 1):

1. ``_parse_allocations`` — shared by ``create`` and ``next-id`` — refuses a corrupt
   ``.kanban/_ID_ALLOCATIONS.json`` with a message ending "nothing was changed", not
   "nothing was created": that is true of both callers.
2. ``next-id --json``: EVERY refusal (a corrupt allocations file, a malformed
   prefix, an unencodable prefix, no actor, a refused commit) prints the refusal
   dict — ``success: False``, ``id: None``, ``prefix``, ``number: None``,
   ``message`` — as parseable JSON on stdout and exits 1, as its remote path already
   does. Without ``--json`` the refusal still prints the error and exits 1.
3. ``KanbanService.allocate_next_id`` (the steer as amended on #847): an input
   refusal — a corrupt local allocations file, a malformed or unencodable prefix —
   still RAISES ``InputRefused`` (the #786 design MCP relies on); only the CLI's
   ``--json`` turns it into the dict. A corrupt origin, no actor or a refused commit
   comes back as the dict, with ``number: None``.

Reuses the #585/#590/#818 real-git harness: a bare remote, clone A under test.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from typing import Any

import pytest
from click.testing import CliRunner

from tests.issues.test_585_create_push_loop import (
    World,
    git,
    output_of,
    run_create,
)
from tests.issues.test_818_allocations_refuse_corrupt import (
    drop_remote,
    local_snapshot,
    seed_local,
    seed_origin,
    service,
)
from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import main
from yurtle_kanban.models import InputRefused, InvalidText
from yurtle_kanban.service import _parse_allocations

KEYS = {"success", "id", "prefix", "number", "message"}
CHANGED = "nothing was changed"
CREATED = "nothing was created"
BAD_PREFIXES = {"dotdot": "../x", "digit-first": "1AB"}


@pytest.fixture
def world(tmp_path, monkeypatch: pytest.MonkeyPatch) -> World:
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    monkeypatch.delenv("YURTLE_AGENT", raising=False)
    return World(tmp_path)


@pytest.fixture(autouse=True)
def _clean_theme_cache() -> Iterator[None]:
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


# --- helpers ------------------------------------------------------------------------------


def next_id(world: World, monkeypatch: pytest.MonkeyPatch, *args: str) -> Any:
    monkeypatch.chdir(world.a)
    return CliRunner().invoke(main, ["next-id", *args])


def json_refusal(result: Any) -> dict[str, Any]:
    """The refusal `next-id --json` printed on stdout, checked for shape and exit 1."""
    out = result.stdout
    assert out.strip(), (
        f"next-id --json printed nothing on stdout; the refusal went elsewhere:\n"
        f"{output_of(result)}"
    )
    try:
        payload = json.loads(out)
    except ValueError:
        pytest.fail(f"next-id --json stdout is not JSON:\n{out}")
    assert isinstance(payload, dict), payload
    assert payload.get("success") is False, payload
    assert result.exit_code == 1, f"exit {result.exit_code}: {output_of(result)}"
    assert_refusal_dict(payload)
    return payload


def assert_refusal_dict(result: dict[str, Any]) -> None:
    assert KEYS <= set(result), f"refusal dict lacks {KEYS - set(result)}: {result}"
    assert result["success"] is False, result
    assert result["id"] is None, result
    assert result["number"] is None, result
    assert isinstance(result["message"], str) and result["message"].strip(), result


def service_result(call: Any) -> dict[str, Any]:
    """The dict `allocate_next_id` returns; raising a refusal is a failure (#847)."""
    try:
        result = call()
    except (InputRefused, ValueError) as e:
        pytest.fail(f"allocate_next_id raised {type(e).__name__} instead of returning: {e}")
    assert isinstance(result, dict), result
    return result


def ends_changed(message: str) -> None:
    flat = " ".join(message.split())
    assert flat.rstrip(" .)").endswith(CHANGED), f"does not end {CHANGED!r}: {flat!r}"
    assert CREATED not in flat, f"still says {CREATED!r}: {flat!r}"
    assert "_ID_ALLOCATIONS.json" in flat, f"does not name the file: {flat!r}"


def no_actor(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    """No git user.name anywhere and no $YURTLE_AGENT (as #580's `no_global_git`)."""
    home = world.a.parent / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.delenv("YURTLE_AGENT", raising=False)
    git(world.a, "config", "--unset", "user.name")


def failing_pre_commit(world: World) -> None:
    hooks = world.a.parent / "hooks"
    hooks.mkdir(exist_ok=True)
    hook = hooks / "pre-commit"
    hook.write_text("#!/bin/sh\necho 'pre-commit says no' >&2\nexit 1\n")
    hook.chmod(0o755)
    git(world.a, "config", "core.hooksPath", str(hooks))


# --- 1. the wording: "nothing was changed" -----------------------------------------------------


@pytest.mark.parametrize("bad", ["{not json", "{}", '"x"', "42", ""])
def test_parse_allocations_refusal_says_nothing_was_changed(bad: str) -> None:
    with pytest.raises(InputRefused) as caught:
        _parse_allocations(bad, ".kanban/_ID_ALLOCATIONS.json")
    ends_changed(str(caught.value))


def test_next_id_remote_corrupt_origin_says_nothing_was_changed(world, monkeypatch) -> None:
    seed_origin(world, "{not json")
    payload = json_refusal(next_id(world, monkeypatch, "EXP", "--json"))
    ends_changed(payload["message"])


def test_next_id_no_sync_corrupt_local_says_nothing_was_changed(world, monkeypatch) -> None:
    drop_remote(world)
    seed_local(world, "{}")
    result = next_id(world, monkeypatch, "EXP", "--no-sync")
    assert result.exit_code == 1, output_of(result)
    ends_changed(result.output)  # output_of appends SystemExit's code


def test_create_push_corrupt_origin_says_nothing_was_changed(world, monkeypatch) -> None:
    seed_origin(world, "{not json")
    result = run_create(world, monkeypatch)
    assert result.exit_code != 0, output_of(result)
    flat = " ".join(output_of(result).split())
    assert CHANGED in flat, flat
    assert CREATED not in flat, flat


def test_create_push_corrupt_local_says_nothing_was_changed(world, monkeypatch) -> None:
    drop_remote(world)
    seed_local(world, "42")
    before = local_snapshot(world)
    result = run_create(world, monkeypatch)
    assert result.exit_code != 0, output_of(result)
    flat = " ".join(output_of(result).split())
    assert CHANGED in flat, flat
    assert CREATED not in flat, flat
    assert local_snapshot(world) == before


# --- 2. next-id --json: every refusal is JSON on stdout, exit 1 ------------------------------


def test_next_id_json_corrupt_local_no_remote(world, monkeypatch) -> None:
    drop_remote(world)
    seed_local(world, "{not json")
    before = local_snapshot(world)
    payload = json_refusal(next_id(world, monkeypatch, "EXP", "--json"))
    assert payload["prefix"] == "EXP", payload
    ends_changed(payload["message"])
    assert local_snapshot(world) == before


def test_next_id_json_corrupt_local_no_sync(world, monkeypatch) -> None:
    """`--no-sync` with a remote takes the local path: the checkout's own file."""
    seed_origin(world, "{}")
    payload = json_refusal(next_id(world, monkeypatch, "EXP", "--no-sync", "--json"))
    assert payload["prefix"] == "EXP", payload
    ends_changed(payload["message"])


def test_control_next_id_json_corrupt_origin_is_json(world, monkeypatch) -> None:
    """Control: the remote path already refuses in JSON (#590)."""
    seed_origin(world, "{not json")
    payload = json_refusal(next_id(world, monkeypatch, "EXP", "--json"))
    assert payload["prefix"] == "EXP", payload


@pytest.mark.parametrize("prefix", list(BAD_PREFIXES.values()), ids=list(BAD_PREFIXES))
def test_next_id_json_malformed_prefix(world, monkeypatch, prefix: str) -> None:
    payload = json_refusal(next_id(world, monkeypatch, prefix, "--no-sync", "--json"))
    assert "prefix" in payload["message"].lower(), payload
    assert not (world.a / ".kanban" / "_ID_ALLOCATIONS.json").exists()


@pytest.mark.parametrize("prefix", list(BAD_PREFIXES.values()), ids=list(BAD_PREFIXES))
def test_next_id_json_malformed_prefix_with_remote(world, monkeypatch, prefix: str) -> None:
    before = world.remote_sha()
    payload = json_refusal(next_id(world, monkeypatch, prefix, "--json"))
    assert "prefix" in payload["message"].lower(), payload
    assert world.remote_sha() == before


def test_control_next_id_json_no_actor(world, monkeypatch) -> None:
    """Control: no actor already comes back as the dict (#620)."""
    no_actor(world, monkeypatch)
    payload = json_refusal(next_id(world, monkeypatch, "EXP", "--no-sync", "--json"))
    assert "actor" in payload["message"].lower(), payload
    assert payload["prefix"] == "EXP", payload


def test_next_id_json_refused_commit_has_number_none(world, monkeypatch) -> None:
    """A refused local commit (#584) is a refusal dict too: `number` is present, None."""
    drop_remote(world)
    failing_pre_commit(world)
    payload = json_refusal(next_id(world, monkeypatch, "EXP", "--json"))
    assert payload["prefix"] == "EXP", payload


# --- 3. without --json: the error is printed, exit 1 ----------------------------------------


def test_control_next_id_plain_corrupt_local_prints_error(world, monkeypatch) -> None:
    drop_remote(world)
    seed_local(world, "{not json")
    result = next_id(world, monkeypatch, "EXP")
    out = output_of(result)
    assert result.exit_code == 1, out
    assert "_ID_ALLOCATIONS.json" in " ".join(out.split()), out
    assert "Traceback" not in out, out


@pytest.mark.parametrize("prefix", list(BAD_PREFIXES.values()), ids=list(BAD_PREFIXES))
def test_control_next_id_plain_malformed_prefix_prints_error(
    world, monkeypatch, prefix: str
) -> None:
    result = next_id(world, monkeypatch, prefix, "--no-sync")
    out = output_of(result)
    assert result.exit_code == 1, out
    assert "prefix" in out.lower(), out
    assert "Traceback" not in out, out


def test_control_next_id_plain_no_actor_prints_error(world, monkeypatch) -> None:
    no_actor(world, monkeypatch)
    result = next_id(world, monkeypatch, "EXP", "--no-sync")
    out = output_of(result)
    assert result.exit_code == 1, out
    assert "actor" in out.lower(), out


# --- 4. the service: input refusals raise InputRefused (#786); the CLI's --json
# turns them into the refusal dict (steer amended on #847) -------------------------------


def test_allocate_next_id_corrupt_local_raises_changed(world, monkeypatch) -> None:
    drop_remote(world)
    seed_local(world, "{not json")
    monkeypatch.chdir(world.a)
    with pytest.raises(InputRefused) as info:
        service(world).allocate_next_id("EXP")
    ends_changed(str(info.value))


def test_allocate_next_id_corrupt_local_no_sync_raises_changed(world, monkeypatch) -> None:
    seed_origin(world, '"x"')
    monkeypatch.chdir(world.a)
    with pytest.raises(InputRefused) as info:
        service(world).allocate_next_id("EXP", sync_remote=False)
    ends_changed(str(info.value))


def test_control_allocate_next_id_corrupt_origin_returns_dict(world, monkeypatch) -> None:
    seed_origin(world, "{}")
    monkeypatch.chdir(world.a)
    result = service_result(lambda: service(world).allocate_next_id("EXP"))
    assert_refusal_dict(result)


def test_control_allocate_next_id_no_actor_returns_dict(world, monkeypatch) -> None:
    no_actor(world, monkeypatch)
    monkeypatch.chdir(world.a)
    result = service_result(
        lambda: service(world).allocate_next_id("EXP", sync_remote=False)
    )
    assert_refusal_dict(result)


def test_allocate_next_id_refused_commit_returns_full_dict(world, monkeypatch) -> None:
    drop_remote(world)
    failing_pre_commit(world)
    monkeypatch.chdir(world.a)
    result = service_result(lambda: service(world).allocate_next_id("EXP"))
    assert_refusal_dict(result)


@pytest.mark.parametrize("sync", [True, False], ids=["remote", "no-sync"])
def test_allocate_next_id_unencodable_prefix_raises(world, monkeypatch, sync: bool) -> None:
    """`EXP\\udcff` (argv bytes b"EXP\\xff") is refused by raising, with or without
    the remote; test_219's TestAllocateNextId pins the no-remote repo as well."""
    seed_local(world, "[]")
    monkeypatch.chdir(world.a)
    before = local_snapshot(world)
    # the UTF-8 refusal itself, not the prefix-shape one that would follow it (#935)
    with pytest.raises(InvalidText) as info:
        service(world).allocate_next_id("EXP\udcff", sync_remote=sync)
    assert "invalid UTF-8" in str(info.value), str(info.value)
    assert local_snapshot(world) == before, "the refused allocation wrote to A's checkout"


# --- 5. controls: a good allocation is unchanged ---------------------------------------------


def test_control_next_id_json_success_shape(world, monkeypatch) -> None:
    drop_remote(world)
    result = next_id(world, monkeypatch, "EXP", "--json")
    assert result.exit_code == 0, output_of(result)
    payload = json.loads(result.stdout)
    assert payload["success"] is True, payload
    assert payload["id"] == "EXP-001", payload
    assert payload["number"] == 1, payload

