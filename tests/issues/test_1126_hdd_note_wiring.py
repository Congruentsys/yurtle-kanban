"""Issue #1126: every hdd create's `--push` path prints the pull note.

#1063 pinned the pull-note wiring end to end for `idea create --push` only. The
other hdd creates reach the note through the same shared `_print_created_file`
(which calls `_click.pull_note`), so a create that stopped using the helper would
silently lose the note with nothing going red. This static check finds every
`<group>.command("create")` in `hdd_commands.py` that takes a `--push` option —
discovered from the click decorators, not a hard-coded list — and asserts its
`if push:` branch calls `_print_created_file` (or `pull_note` directly) on its
success path — not only in the failure branch of `if result["success"]` (#1137),
after an early success exit, or in an `except` handler (#1147).
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


def _mentions_success(test: ast.expr) -> bool:
    """`test` reads "success": a subscript key (`result["success"]`), an attribute
    (`result.success`) or a bare name (`success`)."""
    return any(
        (isinstance(n, ast.Constant) and n.value == "success")
        or (isinstance(n, ast.Attribute) and n.attr == "success")
        or (isinstance(n, ast.Name) and n.id == "success")
        for n in ast.walk(test)
    )


def _success_polarity(test: ast.expr) -> bool | None:
    """For a test that mentions success: True when its body runs only on success,
    False when only on failure, None when that can't be decided (#1147).

    `not` inverts. An `and` keeps the one polarity its success operands agree on
    (`result["success"] and x` → True). An `or` over success is undecided:
    `not result["success"] or strict` runs its body on failure.
    """
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        inner = _success_polarity(test.operand)
        return None if inner is None else not inner
    if isinstance(test, ast.BoolOp):
        polarities = {_success_polarity(v) for v in test.values if _mentions_success(v)}
        if isinstance(test.op, ast.And) and len(polarities) == 1:
            return polarities.pop()
        return None
    if isinstance(test, ast.Compare) and len(test.ops) == 1:
        # `result["success"] == False` / `is False` / `!= True` (r1 F3): the
        # comparison against a bool constant sets the polarity; any other is unsure
        other = test.comparators[0]
        if isinstance(other, ast.Constant) and isinstance(other.value, bool):
            if isinstance(test.ops[0], (ast.Eq, ast.Is)):
                return other.value
            if isinstance(test.ops[0], (ast.NotEq, ast.IsNot)):
                return not other.value
        return None
    return True


def _always_exits(stmts: list[ast.stmt]) -> bool:
    """`stmts` never falls through: it ends in return / raise / break / continue /
    `exit(...)`, or in an if/else whose branches both do."""
    if not stmts:
        return False
    last = stmts[-1]
    if isinstance(last, (ast.Return, ast.Raise, ast.Break, ast.Continue)):
        return True
    if isinstance(last, ast.Expr) and _call_name(last.value) == "exit":
        return True
    if isinstance(last, ast.If):
        return _always_exits(last.body) and _always_exits(last.orelse)
    return False


def _success_path_calls_note(stmts: list[ast.stmt]) -> bool:
    """A note helper is called in `stmts` on the success path (#1137, #1147).

    `if <success>:` walks only its body, `if not <success>:` only its `else:`,
    an undecided success test neither; an if that doesn't read success walks
    both. After an `if` whose success-or-undecided body always exits, the rest
    of the block runs only on failure, so it is not walked. `except` handlers
    are failure paths and are never walked. When unsure, a call doesn't count.
    """
    for stmt in stmts:
        if isinstance(stmt, ast.If):
            if any(_call_name(n) in NOTE_HELPERS for n in ast.walk(stmt.test)):
                return True
            if not _mentions_success(stmt.test):
                if _success_path_calls_note(stmt.body) or _success_path_calls_note(stmt.orelse):
                    return True
                continue
            polarity = _success_polarity(stmt.test)
            if polarity is True and _success_path_calls_note(stmt.body):
                return True
            if polarity is False and _success_path_calls_note(stmt.orelse):
                return True
            if polarity is not False and _always_exits(stmt.body):
                return False  # what follows runs only on failure (or can't tell)
            continue
        nested = [
            body
            for field in ("body", "orelse", "finalbody")
            if isinstance(body := getattr(stmt, field, None), list)
        ]  # `except` handlers are left out: failure paths (#1147)
        if nested:  # for / while / with / try: walk each block the same way
            if any(_success_path_calls_note(block) for block in nested):
                return True
            continue
        if any(_call_name(n) in NOTE_HELPERS for n in ast.walk(stmt)):
            return True
    return False


def _push_branch_prints_note(func: ast.FunctionDef) -> bool:
    """Some `if ...push...:` body in `func` calls a note helper on its success path."""
    for node in ast.walk(func):
        if isinstance(node, ast.If) and _tests_push(node.test):
            if _success_path_calls_note(node.body):
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

@other.command("create")
@click.option("--push", is_flag=True)
def note_only_on_failure(push):
    if push:
        result = service.create_item_and_push()
        if result["success"]:
            console.print(result["id"])
        else:
            _print_created_file(result)

@other.command("create")
@click.option("--push", is_flag=True)
def note_only_when_not_success(push):
    if push:
        result = service.create_item_and_push()
        if not result["success"]:
            _print_created_file(result)
            raise SystemExit(1)

@other.command("create")
@click.option("--push", is_flag=True)
def note_on_success(push):
    if push:
        result = service.create_item_and_push()
        if result["success"]:
            _print_created_file(result)
        else:
            raise SystemExit(1)

@other.command("create")
@click.option("--push", is_flag=True)
def note_after_guard(push):
    if push:
        result = service.create_item_and_push()
        if not result["success"]:
            raise SystemExit(1)
        _print_created_file(result)

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
    assert found == [
        "thing_create",
        "other_create",
        "helper_outside_push",
        "note_only_on_failure",
        "note_only_when_not_success",
        "note_on_success",
        "note_after_guard",
    ]
    assert missing == [
        "thing_create",
        "helper_outside_push",
        "note_only_on_failure",
        "note_only_when_not_success",
    ]


SYNTHETIC_1147 = """
import click

@other.command("create")
@click.option("--push", is_flag=True)
def note_after_early_success_return(push):
    if push:
        result = service.create_item_and_push()
        if result["success"]:
            console.print(result["id"])
            return
        _print_created_file(result)

@other.command("create")
@click.option("--push", is_flag=True)
def note_when_not_success_or_strict(push, strict):
    if push:
        result = service.create_item_and_push()
        if not result["success"] or strict:
            _print_created_file(result)

@other.command("create")
@click.option("--push", is_flag=True)
def note_when_success_or_strict(push, strict):
    if push:
        result = service.create_item_and_push()
        if result["success"] or strict:
            _print_created_file(result)

@other.command("create")
@click.option("--push", is_flag=True)
def note_when_success_is_false(push):
    if push:
        result = service.create_item_and_push()
        if result["success"] == False:
            _print_created_file(result)

@other.command("create")
@click.option("--push", is_flag=True)
def note_when_success_is_true(push):
    if push:
        result = service.create_item_and_push()
        if result["success"] is True:
            _print_created_file(result)

@other.command("create")
@click.option("--push", is_flag=True)
def note_only_in_except(push):
    if push:
        try:
            result = service.create_item_and_push()
        except RuntimeError:
            _print_created_file({})

@other.command("create")
@click.option("--push", is_flag=True)
def note_in_try_body(push):
    if push:
        try:
            result = service.create_item_and_push()
            _print_created_file(result)
        except RuntimeError:
            raise SystemExit(1)
"""


def test_checker_flags_early_return_negated_or_and_except_only() -> None:
    """#1147: three shapes that reach the note only on failure are flagged."""
    found, missing = _unwired(SYNTHETIC_1147)
    assert found == [
        "note_after_early_success_return",
        "note_when_not_success_or_strict",
        "note_when_success_or_strict",
        "note_when_success_is_false",
        "note_when_success_is_true",
        "note_only_in_except",
        "note_in_try_body",
    ]
    assert missing == [
        "note_after_early_success_return",
        "note_when_not_success_or_strict",
        "note_when_success_or_strict",  # r1 F1: runs on failure when `strict`
        "note_when_success_is_false",  # r1 F3
        "note_only_in_except",
    ]
