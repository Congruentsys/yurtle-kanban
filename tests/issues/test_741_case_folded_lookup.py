# ruff: noqa: F811  -- the `repo` fixture imported from #576's module is re-bound as an arg
"""#741: an item whose file says `id: exp-9` is reachable by `EXP-9`.

The CLI and MCP upper-case the ID they are given; `get_item` (and the writer lookup
`_current_item` built on it) fall back to the case-folded index (#732's
`_folded_items`) when the exact ID misses. An exact match still wins; the file keeps
its own spelling; case-only duplicates stay refused (#732). `measure create --id` and
`hypothesis create --id` check existence through `get_item`, so an explicit ID that
differs only in case from an existing one is refused as already existing.

Fixture: #576's two-board repo (`development` nautical under `work/`, `research` hdd
under `research/`), plus `work/expeditions/exp-9-item.md` saying `id: exp-9`.
"""

from __future__ import annotations

from pathlib import Path

from tests.issues.test_576_cli_update_deps import (  # noqa: F401
    Repo,
    _flat,
    _item_text,
    _ok,
    _refused,
    invoke,
    repo,
)
from yurtle_kanban.mcp import server as mcp_server


def _lower(repo: Repo, item_id: str = "exp-9") -> Path:
    """Write and commit an expedition whose file says `id: <item_id>` (lower-case)."""
    repo.write(item_id, [])
    repo.commit(f"lower-case {item_id}")
    path = repo.path(item_id)
    assert f"id: {item_id}\n" in path.read_text(encoding="utf-8")
    return path


def _upper_dup(repo: Repo) -> Path:
    """An `EXP-9` file next to the `exp-9` one: a case-only duplicate (#732)."""
    dup = repo.root / "work" / "expeditions" / "EXP-9-other.md"
    dup.write_text(_item_text("EXP-9", []), encoding="utf-8")
    repo.commit("upper-case EXP-9 duplicate")
    return dup


# ---------------------------------------------------------------------------
# The service lookup
# ---------------------------------------------------------------------------


def test_get_item_finds_a_lowercase_file_id_by_its_upper_cased_id(repo: Repo) -> None:
    _lower(repo)
    item = repo.service().get_item("EXP-9")
    assert item is not None, "get_item('EXP-9') missed the file that says id: exp-9"
    assert item.id == "exp-9"  # the file's own spelling, not rewritten


def test_current_item_finds_a_lowercase_file_id(repo: Repo) -> None:
    _lower(repo)
    item = repo.service()._current_item("EXP-9")
    assert item is not None and item.id == "exp-9"


def test_exact_lowercase_lookup_still_works(repo: Repo) -> None:
    """Control: the exact spelling was always found."""
    _lower(repo)
    item = repo.service().get_item("exp-9")
    assert item is not None and item.id == "exp-9"


def test_exact_match_wins_over_the_folded_one(repo: Repo) -> None:
    """Control (guards the fix): with both spellings on disk, each exact ID gets its
    own file, not whichever the folded index kept last."""
    lower = _lower(repo)
    upper = _upper_dup(repo)
    svc = repo.service()
    a, b = svc.get_item("EXP-9"), svc.get_item("exp-9")
    assert a is not None and a.file_path.resolve() == upper.resolve()
    assert b is not None and b.file_path.resolve() == lower.resolve()


# ---------------------------------------------------------------------------
# CLI: update / move / show / comment EXP-9
# ---------------------------------------------------------------------------


def test_cli_update_reaches_a_lowercase_file_id(repo: Repo) -> None:
    _lower(repo)
    _ok(["update", "EXP-9", "--priority", "high"])
    fm = repo.fm("exp-9")
    assert fm["priority"] == "high"
    assert fm["id"] == "exp-9", "update rewrote the file's ID spelling"


def test_cli_update_with_a_lowercase_argument_reaches_it_too(repo: Repo) -> None:
    _lower(repo)
    _ok(["update", "exp-9", "--priority", "high"])
    assert repo.fm("exp-9")["priority"] == "high"


def test_cli_move_reaches_a_lowercase_file_id(repo: Repo) -> None:
    _lower(repo)
    _ok(["move", "EXP-9", "in_progress", "--force"])
    moved = repo.service().get_item("exp-9")
    assert moved is not None and moved.status.value == "in_progress", moved
    assert repo.fm("exp-9")["id"] == "exp-9"


def test_cli_show_reaches_a_lowercase_file_id(repo: Repo) -> None:
    _lower(repo)
    result = _ok(["show", "EXP-9"])
    assert "Item exp-9" in _flat(result.output), result.output


def test_cli_comment_reaches_a_lowercase_file_id(repo: Repo) -> None:
    _lower(repo)
    _ok(["comment", "EXP-9", "--body", "a note for nine", "--agent", "tester"])
    text = repo.path("exp-9").read_text(encoding="utf-8")
    assert "a note for nine" in text
    assert "id: exp-9\n" in text


# ---------------------------------------------------------------------------
# MCP: kanban_update_item / kanban_move_item / kanban_get_item / kanban_add_comment
# ---------------------------------------------------------------------------


def test_mcp_update_reaches_a_lowercase_file_id(repo: Repo) -> None:
    _lower(repo)
    mcp = mcp_server.KanbanMCPServer(repo_root=repo.root)
    out = mcp.handle_tool_call("kanban_update_item", {"item_id": "exp-9", "priority": "high"})
    assert out.get("success"), out
    assert repo.fm("exp-9")["priority"] == "high"
    assert repo.fm("exp-9")["id"] == "exp-9"


def test_mcp_move_reaches_a_lowercase_file_id(repo: Repo) -> None:
    _lower(repo)
    mcp = mcp_server.KanbanMCPServer(repo_root=repo.root)
    out = mcp.handle_tool_call(
        "kanban_move_item", {"item_id": "EXP-9", "new_status": "in_progress", "force": True}
    )
    assert "error" not in out, out
    moved = repo.service().get_item("exp-9")
    assert moved is not None and moved.status.value == "in_progress", moved


def test_mcp_get_item_reaches_a_lowercase_file_id(repo: Repo) -> None:
    _lower(repo)
    mcp = mcp_server.KanbanMCPServer(repo_root=repo.root)
    out = mcp.handle_tool_call("kanban_get_item", {"item_id": "EXP-9"})
    assert out.get("item"), out
    assert out["item"]["id"] == "exp-9"


def test_mcp_comment_reaches_a_lowercase_file_id(repo: Repo) -> None:
    _lower(repo)
    mcp = mcp_server.KanbanMCPServer(repo_root=repo.root)
    out = mcp.handle_tool_call(
        "kanban_add_comment", {"item_id": "EXP-9", "comment": "mcp note", "author": "tester"}
    )
    assert "error" not in out, out
    assert "mcp note" in repo.path("exp-9").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Case-only duplicates stay refused (#732) — controls
# ---------------------------------------------------------------------------


def test_cli_update_refuses_a_case_only_duplicate_naming_both_files(repo: Repo) -> None:
    lower = _lower(repo)
    upper = _upper_dup(repo)
    _refused(
        repo, ["update", "EXP-9", "--priority", "high"],
        lower.relative_to(repo.root).as_posix(), upper.relative_to(repo.root).as_posix(),
    )


def test_mcp_update_refuses_a_case_only_duplicate(repo: Repo) -> None:
    _lower(repo)
    _upper_dup(repo)
    before = repo.snapshot()
    mcp = mcp_server.KanbanMCPServer(repo_root=repo.root)
    out = mcp.handle_tool_call("kanban_update_item", {"item_id": "exp-9", "priority": "high"})
    assert "error" in out and "EXP-9" in out["error"].upper(), out
    assert repo.snapshot() == before


# ---------------------------------------------------------------------------
# Control: a normal upper-case item is unchanged
# ---------------------------------------------------------------------------


def test_upper_case_item_still_updates_moves_and_shows(repo: Repo) -> None:
    _lower(repo)  # its presence must not disturb EXP-1
    _ok(["update", "EXP-1", "--priority", "high"])
    assert repo.fm("EXP-1")["priority"] == "high"
    _ok(["move", "EXP-1", "in_progress", "--force"])
    assert repo.service().get_item("EXP-1").status.value == "in_progress"  # type: ignore[union-attr]
    assert "Item EXP-1" in _flat(_ok(["show", "EXP-1"]).output)
    assert repo.service().get_item("EXP-1").id == "EXP-1"  # type: ignore[union-attr]


def test_unknown_id_is_still_not_found(repo: Repo) -> None:
    """Control: the fallback finds case variants only, not anything else."""
    _lower(repo)
    assert repo.service().get_item("EXP-99") is None
    _refused(repo, ["update", "EXP-99", "--priority", "high"], "EXP-99")


# ---------------------------------------------------------------------------
# create with an explicit ID (the main `create` has no --id; `measure create --id`
# and `hypothesis create --id` do, and check existence through `get_item`)
# ---------------------------------------------------------------------------


def _lower_measure(repo: Repo) -> Path:
    path = repo.root / "research" / "measures" / "m-042-latency.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\nid: m-042\ntitle: \"Latency\"\ntype: measure\nstatus: draft\n"
        "priority: medium\n---\n\n# Latency\n\nSome body.\n",
        encoding="utf-8",
    )
    repo.commit("lower-case measure m-042")
    assert repo.service().get_item("m-042") is not None, "fixture measure not scanned"
    return path


def test_measure_create_refuses_an_id_that_exists_in_another_case(repo: Repo) -> None:
    _lower_measure(repo)
    _refused(
        repo,
        ["measure", "create", "Latency again", "--unit", "ms", "--category", "performance",
         "--id", "M-042"],
        "already exists",
    )


def test_measure_create_refuses_an_id_that_exists_in_the_same_case(repo: Repo) -> None:
    """Control: the exact-case collision was always refused."""
    path = _lower_measure(repo)
    path.write_text(path.read_text(encoding="utf-8").replace("id: m-042", "id: M-042"))
    repo.commit("upper-case the measure")
    _refused(
        repo,
        ["measure", "create", "Latency again", "--unit", "ms", "--category", "performance",
         "--id", "M-042"],
        "already exists",
    )


def test_hypothesis_create_refuses_an_id_that_exists_in_another_case(repo: Repo) -> None:
    path = repo.root / "research" / "hypotheses" / "h130.1-item.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_item_text("H130.1", []).replace("id: H130.1", "id: h130.1"))
    repo.commit("lower-case hypothesis h130.1")
    assert repo.service().get_item("h130.1") is not None, "fixture hypothesis not scanned"
    _refused(repo, ["hypothesis", "create", "Another claim", "--id", "H130.1"], "already exists")


def test_create_has_no_explicit_id_option(repo: Repo) -> None:
    """Scope note: the generic `create` allocates its own ID, so `create EXP-9` over an
    `exp-9` file cannot be asked for. If `--id` is ever added it must be covered."""
    result = invoke(["create", "--help"])
    assert "--id" not in result.output
