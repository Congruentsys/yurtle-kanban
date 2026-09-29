"""Issue #1126: every hdd create's `--push` path prints the pull note.

#1063 pinned the pull-note wiring end to end for `idea create --push` only. The
other hdd creates reach the note through the same shared `_print_created_file`
(which calls `_click.pull_note`), so a create that stopped using the helper would
silently lose the note with nothing going red. This static check finds every
`<group>.command("create")` in `hdd_commands.py` that takes a `--push` option —
discovered from the click decorators, not a hard-coded list — and asserts its
`if push:` branch calls `_print_created_file` (or `pull_note` directly).
"""

from __future__ import annotations

import ast
from pathlib import Path

import yurtle_kanban.hdd_commands as hdd_commands

NOTE_HELPERS = {"_print_created_file", "pull_note"}
MIN_CREATES = 6  # idea, literature, paper, hypothesis, experiment, measure


def _call_name(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _is_create_command(dec: ast.expr) -> bool:
    """`@<group>.command("create")`."""
    return (
        isinstance(dec, ast.Call)
        and isinstance(dec.func, ast.Attribute)
        and dec.func.attr == "command"
        and bool(dec.args)
        and isinstance(dec.args[0], ast.Constant)
        and dec.args[0].value == "create"
    )


def _is_push_option(dec: ast.expr) -> bool:
    """`@click.option("--push", ...)`."""
    return (
        isinstance(dec, ast.Call)
        and _call_name(dec) == "option"
        and any(isinstance(a, ast.Constant) and a.value == "--push" for a in dec.args)
    )


def _push_creates(tree: ast.Module) -> list[ast.FunctionDef]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and any(_is_create_command(d) for d in node.decorator_list)
        and any(_is_push_option(d) for d in node.decorator_list)
    ]


def _tests_push(test: ast.expr) -> bool:
    return any(isinstance(n, ast.Name) and n.id == "push" for n in ast.walk(test))


def _push_branch_prints_note(func: ast.FunctionDef) -> bool:
    """Some `if ...push...:` body in `func` calls a note helper."""
    for node in ast.walk(func):
        if isinstance(node, ast.If) and _tests_push(node.test):
            for stmt in node.body:
                if any(_call_name(n) in NOTE_HELPERS for n in ast.walk(stmt)):
                    return True
    return False


def _unwired(source: str) -> tuple[list[str], list[str]]:
    creates = _push_creates(ast.parse(source))
    found = [f.name for f in creates]
    missing = [f.name for f in creates if not _push_branch_prints_note(f)]
    return found, missing


def test_every_hdd_create_push_path_prints_the_pull_note() -> None:
    source = Path(hdd_commands.__file__).read_text(encoding="utf-8")
    found, missing = _unwired(source)
    assert len(found) >= MIN_CREATES, (
        f"expected >= {MIN_CREATES} hdd creates with --push, found {found}"
    )
    assert not missing, (
        f"hdd create --push path(s) no longer print the pull note via "
        f"{sorted(NOTE_HELPERS)}: {missing}"
    )


SYNTHETIC = """
import click

@thing.command("create")
@click.option("--push", is_flag=True)
def thing_create(push):
    if push:
        result = service.create_item_and_push()
        console.print(result["file"])

@other.command("create")
@click.option("--push", is_flag=True)
def other_create(push):
    if push:
        result = service.create_item_and_push()
        _print_created_file(result)

@other.command("create")
@click.option("--push", is_flag=True)
def helper_outside_push(push):
    _print_created_file({})
    if push:
        pass

@other.command("list")
@click.option("--push", is_flag=True)
def not_a_create(push):
    pass

@other.command("create")
def create_without_push():
    pass
"""


def test_checker_flags_a_push_create_without_the_note() -> None:
    found, missing = _unwired(SYNTHETIC)
    assert found == ["thing_create", "other_create", "helper_outside_push"]
    assert missing == ["thing_create", "helper_outside_push"]
