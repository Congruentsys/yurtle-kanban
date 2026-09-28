"""Issue #880: ``_blobs_at`` hardening, and a stricter #580 stdin check.

Found in the PR #874 (#832) review. The issue's four points:

1. The #580 static check accepts any plain variable as ``stdin=``, so ``x =
   sys.stdin`` gets through. It must accept only a name bound by ``with
   tempfile.TemporaryFile() as <name>`` (or ``...DEVNULL``).
2. ``_blobs_at`` checks the object type but not the file mode: a symlink passed in
   directly comes back as its target path. (Unreachable today: ``_items_at`` keeps
   only regular files.)
3. ``_blobs_at`` runs ``ls-tree`` again right after ``_items_at`` has. The object
   ids should be passed through: one git call fewer, and point 2 is closed.
4. The ``cat-file --batch`` parser checks neither that each header's object id is
   the one requested nor that the output is as long as the stated size.

Harness: the #585 ``World`` and the #574 claim helpers, as in
tests/issues/test_814_wip_fails_closed.py and tests/issues/test_832_read_blobs_directly.py.

Ambiguities resolved here (the test partner's reading; the driver may challenge):

a. Point 1 is pinned twice. (i) A strict checker, written here, with self-tests
   (rejects ``x = sys.stdin``, accepts the tempfile form) and a scan of the real
   ``src/`` — green on main, since ``_blobs_at`` already binds ``with
   tempfile.TemporaryFile() as feed``. (ii) The #580 check itself
   (``tests/test_580_input_identity.py``, ``TestGitStdinDevnull``) is run over a
   synthetic ``src/`` holding ``x = sys.stdin; subprocess.run(["git", ...],
   stdin=x)`` and must FAIL on it — red on main, since it accepts any Name.
b. Points 2+3 are pinned through ``_items_at`` only (``_blobs_at``'s signature is
   left to the driver): one ``_items_at("HEAD", None)`` runs ``git ls-tree``
   exactly once, counted at ``subprocess.run`` (which ``_git_run`` goes through, so
   either route is seen). A committed symlinked ``.md`` on the board is still not
   an item (negative control).
c. Point 4 is injected by rewriting what the real ``git cat-file --batch`` prints
   (at ``subprocess.run``, bytes or text): a foreign object id in a header, two
   objects printed in swapped order, output cut short, and a stated size larger
   than the content. Each must make ``_items_at`` raise ``_TreeUnreadableError``;
   through ``claim`` (WIP limit set, not full, so a fail-open would WIN) the claim
   is refused with nothing pushed. Well-formed output still parses (control).
"""

from __future__ import annotations

import ast
import os
import subprocess
import textwrap
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tests.issues.test_574_claim import (
    OTHER,
    WIP_CONFIG,
    A,
    B,
    claim,
    item_text,
    push_from_a,
    service,
)
from tests.issues.test_574_sync_and_push import Recorder, snapshot
from tests.issues.test_585_create_push_loop import EXP_DIR, World
from yurtle_kanban import config as config_mod
from yurtle_kanban.service import _TreeUnreadableError

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src"


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    monkeypatch.delenv("YURTLE_AGENT", raising=False)
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


ITEMS = {
    f"{EXP_DIR}/EXP-001-x.md": item_text("ready"),
    f"{EXP_DIR}/EXP-002-y.md": item_text("in_progress", B, "EXP-002", "Y"),
    f"{EXP_DIR}/EXP-003-z.md": item_text("backlog", None, "EXP-003", "Z"),
}


@pytest.fixture
def world(tmp_path: Path) -> World:
    """Origin and both clones hold EXP-001..003 (A's HEAD is the tip)."""
    w = World(tmp_path)
    push_from_a(w, ITEMS, "seed EXP-001..003")
    return w


# =============================================================================
# 1. the #580 stdin check accepts only DEVNULL or a tempfile feed
# =============================================================================

_SUBPROCESS_FUNCS = {"run", "Popen", "check_output", "check_call", "call"}


def _git_calls(tree: ast.AST) -> list[ast.Call]:
    calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and func.attr in _SUBPROCESS_FUNCS
            and isinstance(func.value, ast.Name)
            and func.value.id == "subprocess"
        ):
            continue
        first = node.args[0]
        if (
            isinstance(first, ast.List)
            and first.elts
            and isinstance(first.elts[0], ast.Constant)
            and first.elts[0].value == "git"
        ):
            calls.append(node)
    return calls


def _is_tempfile_call(expr: ast.expr) -> bool:
    return (
        isinstance(expr, ast.Call)
        and isinstance(expr.func, ast.Attribute)
        and expr.func.attr == "TemporaryFile"
        and isinstance(expr.func.value, ast.Name)
        and expr.func.value.id == "tempfile"
    )


def _rebinds(body: list[ast.stmt], name: str) -> bool:
    """`name` is assigned (or re-bound by another `with`/`for`/walrus) in `body`."""
    for stmt in body:
        for node in ast.walk(stmt):
            if isinstance(node, ast.Name) and node.id == name and isinstance(
                node.ctx, (ast.Store, ast.Del)
            ):
                return True
    return False


def stdin_offenders(source: str, filename: str = "<src>") -> list[int]:
    """Line numbers of `subprocess.<fn>(["git", ...])` calls whose `stdin=` is
    neither `...DEVNULL` nor a plain Name bound by an ENCLOSING `with
    tempfile.TemporaryFile() as <name>` (and not re-bound inside that `with`)."""
    tree = ast.parse(source, filename=filename)
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    bad = []
    for call in _git_calls(tree):
        stdin = next((k.value for k in call.keywords if k.arg == "stdin"), None)
        if stdin is None:
            bad.append(call.lineno)
            continue
        if ast.unparse(stdin).endswith("DEVNULL"):
            continue
        ok = False
        if isinstance(stdin, ast.Name):
            node: ast.AST = call
            while node in parents:
                node = parents[node]
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                    break
                if isinstance(node, (ast.With, ast.AsyncWith)) and any(
                    _is_tempfile_call(item.context_expr)
                    and isinstance(item.optional_vars, ast.Name)
                    and item.optional_vars.id == stdin.id
                    for item in node.items
                ):
                    ok = not _rebinds(node.body, stdin.id)
                    break
        if not ok:
            bad.append(call.lineno)
    return bad


SYS_STDIN = textwrap.dedent("""\
    import subprocess, sys
    x = sys.stdin
    subprocess.run(["git", "cat-file", "--batch"], stdin=x)
""")

TEMPFILE_FEED = textwrap.dedent("""\
    import subprocess, tempfile
    def read(oids):
        with tempfile.TemporaryFile() as feed:
            feed.write(oids)
            feed.seek(0)
            return subprocess.run(["git", "cat-file", "--batch"], stdin=feed)
""")


def test_checker_rejects_a_name_bound_to_sys_stdin() -> None:
    assert stdin_offenders(SYS_STDIN) == [3]


@pytest.mark.parametrize(
    "snippet",
    [
        # no stdin at all: inherited
        'import subprocess\nsubprocess.run(["git", "status"])\n',
        # a PIPE the caller never fills
        'import subprocess\nsubprocess.run(["git", "status"], stdin=subprocess.PIPE)\n',
        # sys.stdin spelled out
        'import subprocess, sys\nsubprocess.run(["git", "status"], stdin=sys.stdin)\n',
        # a name bound by some other `with`
        'import subprocess\nwith open("/dev/tty") as x:\n'
        '    subprocess.run(["git", "cat-file", "--batch"], stdin=x)\n',
        # the tempfile name, re-bound to stdin inside the `with`
        'import subprocess, sys, tempfile\nwith tempfile.TemporaryFile() as x:\n'
        '    x = sys.stdin\n    subprocess.run(["git", "cat-file", "--batch"], stdin=x)\n',
        # the tempfile `with` has ended; `x` is a leftover name
        'import subprocess, tempfile\nwith tempfile.TemporaryFile() as x:\n    pass\n'
        'subprocess.run(["git", "cat-file", "--batch"], stdin=x)\n',
        # a tempfile bound to a DIFFERENT name
        'import subprocess, sys, tempfile\nx = sys.stdin\n'
        'with tempfile.TemporaryFile() as feed:\n'
        '    subprocess.run(["git", "cat-file", "--batch"], stdin=x)\n',
    ],
    ids=["unset", "pipe", "sys-stdin", "other-with", "rebound", "after-with", "other-name"],
)
def test_checker_rejects_other_stdins(snippet: str) -> None:
    assert stdin_offenders(snippet) != []


@pytest.mark.parametrize(
    "snippet",
    [
        TEMPFILE_FEED,
        'import subprocess\nsubprocess.run(["git", "status"], stdin=subprocess.DEVNULL)\n',
    ],
    ids=["tempfile-feed", "devnull"],
)
def test_checker_accepts_devnull_and_the_tempfile_feed(snippet: str) -> None:
    """Negative control: the two legitimate forms pass."""
    assert stdin_offenders(snippet) == []


def test_every_git_subprocess_in_src_passes_devnull_or_a_tempfile_feed() -> None:
    """The strict check over the real src/ (green on main: `_blobs_at` binds its
    feed with `with tempfile.TemporaryFile() as feed`)."""
    offenders, literal = [], 0
    for path in sorted((SRC / "yurtle_kanban").rglob("*.py")):
        text = path.read_text()
        literal += len(_git_calls(ast.parse(text)))
        offenders += [f"{path.relative_to(SRC)}:{n}" for n in stdin_offenders(text, str(path))]
    assert literal >= 4, f"found only {literal} literal git calls — is the scan broken?"
    assert offenders == [], f"git calls with a stdin that may be the caller's: {offenders}"


def test_580_check_itself_refuses_a_name_bound_to_sys_stdin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The #580 check in tests/test_580_input_identity.py, run over a synthetic
    src/ that holds `x = sys.stdin; subprocess.run(["git", ...], stdin=x)` (padded
    with enough well-formed calls to pass its "is the scan broken?" floors), must
    FAIL. On main it passes: it accepts any plain Name."""
    import tests.test_580_input_identity as t580

    pkg = tmp_path / "yurtle_kanban"
    pkg.mkdir()
    good = "\n".join(
        f'    subprocess.run(["git", "status"], stdin=subprocess.DEVNULL)  # {i}'
        for i in range(4)
    )
    runner = "\n".join(f'    self._git_run("status")  # {i}' for i in range(16))
    (pkg / "fake.py").write_text(
        "import subprocess\nimport sys\n\n\ndef f(self):\n"
        f"{good}\n{runner}\n"
        "    x = sys.stdin\n"
        '    subprocess.run(["git", "cat-file", "--batch"], stdin=x)\n'
    )
    monkeypatch.setattr(t580, "SRC", tmp_path)
    check = t580.TestGitStdinDevnull().test_every_git_subprocess_in_src_passes_stdin_devnull

    with pytest.raises(AssertionError) as caught:
        check()

    assert "fake.py" in str(caught.value), (
        f"the #580 check failed for another reason: {caught.value}"
    )


def test_control_580_check_accepts_the_tempfile_feed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Negative control: the same synthetic src/ with the feed bound by `with
    tempfile.TemporaryFile() as feed` passes the #580 check."""
    import tests.test_580_input_identity as t580

    pkg = tmp_path / "yurtle_kanban"
    pkg.mkdir()
    good = "\n".join(
        f'    subprocess.run(["git", "status"], stdin=subprocess.DEVNULL)  # {i}'
        for i in range(4)
    )
    runner = "\n".join(f'    self._git_run("status")  # {i}' for i in range(16))
    (pkg / "fake.py").write_text(
        "import subprocess\nimport tempfile\n\n\ndef f(self):\n"
        f"{good}\n{runner}\n"
        "    with tempfile.TemporaryFile() as feed:\n"
        '        subprocess.run(["git", "cat-file", "--batch"], stdin=feed)\n'
    )
    monkeypatch.setattr(t580, "SRC", tmp_path)

    t580.TestGitStdinDevnull().test_every_git_subprocess_in_src_passes_stdin_devnull()


# =============================================================================
# 2+3. one ls-tree per read; symlinks are never items
# =============================================================================


def spy_git(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Every git command started through `subprocess.run` (so through `_git_run`
    too), in order."""
    seen: list[list[str]] = []
    real_run = subprocess.run

    def run(cmd: Any, *args: Any, **kwargs: Any) -> Any:
        if isinstance(cmd, (list, tuple)) and cmd and cmd[0] == "git":
            seen.append([str(c) for c in cmd])
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    return seen


def test_items_at_runs_ls_tree_once(world, monkeypatch) -> None:
    svc = service(world.a)
    seen = spy_git(monkeypatch)

    items = svc._items_at("HEAD", None)

    assert {i.id for i in items} == {"EXP-001", "EXP-002", "EXP-003"}
    ls_trees = [c for c in seen if c[1:2] == ["ls-tree"]]
    assert len(ls_trees) == 1, (
        f"{len(ls_trees)} ls-tree calls for one read of the board:\n"
        + "\n".join(" ".join(c) for c in ls_trees)
    )
    assert any(c[1:3] == ["cat-file", "--batch"] for c in seen), "blobs not read in a batch"


def test_control_symlinked_md_on_the_board_is_not_an_item(world) -> None:
    """A committed symlink `.md` on the board (to an item text off the board) is
    skipped, as a scan skips it — neither its target path nor its target's text
    becomes an item."""
    target = world.a / "notes" / "EXP-009.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(item_text("in_progress", B, "EXP-009", "Linked"))
    link = world.a / EXP_DIR / "EXP-009-link.md"
    os.symlink(os.path.relpath(target, link.parent), link)
    push_from_a(world, {}, "a symlinked item")

    items = {i.id for i in service(world.a)._items_at("HEAD", None)}

    assert items == {"EXP-001", "EXP-002", "EXP-003"}, items


# =============================================================================
# 4. the cat-file --batch parser checks object ids and lengths
# =============================================================================


def _as_bytes(out: Any) -> bytes:
    return out.encode("utf-8", "surrogateescape") if isinstance(out, str) else out or b""


def _objects(out: bytes) -> list[tuple[bytes, bytes, bytes]]:
    """Split well-formed batch output into (oid, type, content)."""
    objs, at = [], 0
    while at < len(out):
        end = out.index(b"\n", at)
        oid, kind, size = out[at:end].split(b" ")
        body = out[end + 1:end + 1 + int(size)]
        objs.append((oid, kind, body))
        at = end + 1 + int(size) + 1
    return objs


def _join(objs: list[tuple[bytes, bytes, bytes]]) -> bytes:
    return b"".join(o + b" " + k + b" " + str(len(c)).encode() + b"\n" + c + b"\n"
                    for o, k, c in objs)


def foreign_oid(out: bytes) -> bytes:
    """The first header names an object that was not asked for."""
    objs = _objects(out)
    oid, kind, body = objs[0]
    objs[0] = (b"f" * len(oid), kind, body)
    return _join(objs)


def swapped(out: bytes) -> bytes:
    """Two objects, each with its own true header, printed in the wrong order."""
    objs = _objects(out)
    assert len(objs) >= 2, "need two objects to swap"
    objs[0], objs[1] = objs[1], objs[0]
    return _join(objs)


def cut_short(out: bytes) -> bytes:
    """The output ends inside the last object's content."""
    return out[:-20]


def oversized(out: bytes) -> bytes:
    """The last header states more bytes than follow it."""
    objs = _objects(out)
    body = b"".join(
        o + b" " + k + b" " + str(len(c)).encode() + b"\n" + c + b"\n" for o, k, c in objs[:-1]
    )
    oid, kind, last = objs[-1]
    return body + oid + b" " + kind + b" " + str(len(last) + 40).encode() + b"\n" + last + b"\n"


def rewrite_batch(
    monkeypatch: pytest.MonkeyPatch, mutate: Callable[[bytes], bytes]
) -> list[bytes]:
    """Run the real `git cat-file --batch` and hand back `mutate(stdout)`, however
    it is started (`subprocess.run` directly or through `_git_run`), text or bytes.
    Returns each original stdout seen."""
    seen: list[bytes] = []
    real_run = subprocess.run

    def run(cmd: Any, *args: Any, **kwargs: Any) -> Any:
        done = real_run(cmd, *args, **kwargs)
        if isinstance(cmd, (list, tuple)) and list(cmd[:3]) == ["git", "cat-file", "--batch"]:
            raw = _as_bytes(done.stdout)
            seen.append(raw)
            new = mutate(raw) if done.returncode == 0 else raw
            out: Any = new.decode("utf-8", "surrogateescape") if isinstance(
                done.stdout, str
            ) else new
            return subprocess.CompletedProcess(done.args, done.returncode, out, done.stderr)
        return done

    monkeypatch.setattr(subprocess, "run", run)
    return seen


MALFORMED = pytest.mark.parametrize(
    "mutate", [foreign_oid, swapped, cut_short, oversized],
    ids=["foreign-oid", "swapped", "cut-short", "oversized"],
)


@MALFORMED
def test_items_at_refuses_malformed_batch_output(world, monkeypatch, mutate) -> None:
    svc = service(world.a)
    seen = rewrite_batch(monkeypatch, mutate)

    with pytest.raises(_TreeUnreadableError):
        svc._items_at("HEAD", None)

    assert seen, "no `git cat-file --batch` was run"


@MALFORMED
def test_claim_refuses_on_malformed_batch_output(world, monkeypatch, mutate) -> None:
    """WIP limit 1 and B's item on origin NOT in progress: a fail-open parse would
    let A's claim win. It must be refused, with nothing pushed or written."""
    push_from_a(world, {".kanban/config.yaml": WIP_CONFIG,
                        OTHER: item_text("ready", B, "EXP-002", "Y")}, "board: wip 1")
    base = world.remote_sha()
    before = snapshot(world.a)
    seen = rewrite_batch(monkeypatch, mutate)
    rec = Recorder()

    out = claim(world.a, A, rec)

    assert seen, "no `git cat-file --batch` was run: WIP did not read the fetched tree"
    assert out.kind == "refused", f"{out.kind}: {out.message}"
    assert out.exit_code == 1
    assert world.remote_sha() == base, "the refused claim changed origin"
    assert rec.seams == [], "a push was attempted"
    assert snapshot(world.a) == before, "the refused claim wrote to A's checkout"


def test_control_well_formed_batch_output_still_parses(world, monkeypatch) -> None:
    """Negative control: output passed through (re-assembled by the same helpers)
    reads every item, with its committed status."""
    svc = service(world.a)
    seen = rewrite_batch(monkeypatch, lambda out: _join(_objects(out)))

    items = {i.id: i for i in svc._items_at("HEAD", None)}

    assert seen, "no `git cat-file --batch` was run"
    assert set(items) == {"EXP-001", "EXP-002", "EXP-003"}
    assert items["EXP-002"].status.value == "in_progress"
    assert items["EXP-002"].assignee == B
