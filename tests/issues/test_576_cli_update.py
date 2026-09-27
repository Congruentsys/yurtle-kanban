"""Expedition #576 — `yurtle-kanban update ID`: fields and body (part 1 of 2).

Spec: the issue body's Expected and Acceptance sections (revised after the
adversarial review). This file covers:

- Acceptance 1: byte-identical round-trip. On #583's hostile fixtures (a real
  `move` history block, a `## Comments` section, CRLF line endings, unknown
  frontmatter keys, an hdd native status), each flag changes only its own
  frontmatter line(s) (plus the H1 for `--title`); every other byte is identical.
- Acceptance 2 and Expected §3 (`body_span`, through behaviour): `--body` /
  `--body-file -` (#580's `read_text_option`) replace only the part of the body span
  after the H1 (the whole span when there is no H1). The frontmatter, the H1, the
  history fence and the comments stay byte-identical. The span ends at the canonical
  status-history block, not at any ```yurtle fence.
- Expected §2: `update` never writes RDF for these fields.
- Acceptance 10: `WorkItem` has no `to_yurtle` and no `blocks` (source/AST).

Dependencies, refusals, commits and `validate` are in test_576_cli_update_deps.py.
Fixtures are #583's (`_build`, `_custom`, `_leading_block`), plus a second item on the
same board so the dependency flags have a real target.
"""

from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner, Result

from tests.issues.test_583_update_field_level import (
    BODY,
    FIXTURES,
    KNOWLEDGE,
    NO_H1,
    Item,
    _build,
    _custom,
    _git,
    _leading_block,
    _service,
)
from yurtle_kanban.cli import main

REPO = Path(__file__).resolve().parents[2]
MODELS = REPO / "src" / "yurtle_kanban" / "models.py"
SRC = REPO / "src" / "yurtle_kanban"


def invoke(args: list[str], input: bytes | str | None = None) -> Result:
    """The CLI in the current directory (the fixture has chdir'd into the repo)."""
    return CliRunner().invoke(main, args, input=input)


def _ok(args: list[str], input: bytes | str | None = None) -> Result:
    result = invoke(args, input)
    assert result.exit_code == 0, (
        f"{args} exited {result.exit_code}: {result.exception!r}\n{result.output}"
    )
    return result


def _commit_all(root: Path, message: str) -> None:
    if subprocess.run(
        ["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True, check=True
    ).stdout.strip():
        _git(root, "add", "-A")
        _git(root, "commit", "-m", message)


def _head(root: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True
    ).stdout.strip()


class Pair:
    """#583's hostile item plus a second item on the same board (a dependency target)."""

    def __init__(self, item: Item, other: str):
        self.item = item
        self.other = other


def _pair(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str) -> Pair:
    it = _build(tmp_path, monkeypatch, name)
    item_type = FIXTURES[name][1]
    _ok(["create", item_type, "Beta two", "-p", "medium"])
    _commit_all(it.root, "second item")
    others = [i.id for i in _service(it.root).get_items() if i.id != it.id]
    assert len(others) == 1, others
    return Pair(it, others[0])


@pytest.fixture(params=list(FIXTURES))
def pair(request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Pair:
    return _pair(tmp_path, monkeypatch, request.param)


@pytest.fixture(params=list(FIXTURES))
def item(request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Item:
    return _build(tmp_path, monkeypatch, request.param)


# ---------------------------------------------------------------------------
# byte-level checks
# ---------------------------------------------------------------------------


def _fm_close(lines: list[bytes]) -> int:
    assert lines[0].rstrip(b"\r") == b"---", lines[:2]
    return next(i for i in range(1, len(lines)) if lines[i].rstrip(b"\r") == b"---")


def _key_of(line: bytes) -> str | None:
    m = re.match(rb"^([A-Za-z_][\w-]*):", line)
    return m.group(1).decode() if m else None


def _assert_only_changed(
    old: bytes, new: bytes, expected: dict[str, object], h1: str | None = None
) -> None:
    """`new` is `old` with only the frontmatter keys in `expected` (set to those
    values; a key may be added) and, when `h1` is given, the `# Title` line changed.
    Everything else, line endings included, is byte-identical."""
    crlf = b"\r\n" in old
    old_lines, new_lines = old.split(b"\n"), new.split(b"\n")
    o_close, n_close = _fm_close(old_lines), _fm_close(new_lines)

    # frontmatter: the untouched keys are identical and in the same order
    keep_old = [ln for ln in old_lines[1:o_close] if _key_of(ln) not in expected]
    keep_new = [ln for ln in new_lines[1:n_close] if _key_of(ln) not in expected]
    assert keep_new == keep_old, (
        f"frontmatter lines other than {sorted(expected)} changed:\n{new.decode()}"
    )
    for key, value in expected.items():
        lines = [ln for ln in new_lines[1:n_close] if _key_of(ln) == key]
        assert len(lines) == 1, f"expected one `{key}:` line, got {lines!r}:\n{new.decode()}"
        assert lines[0].endswith(b"\r") == crlf, f"line ending changed: {lines[0]!r}"
        parsed = yaml.safe_load(lines[0].rstrip(b"\r").decode())[key]
        assert parsed == value, f"{key}: {parsed!r} != {value!r}"

    # after the frontmatter: identical, except the H1 for a title change
    tail_old, tail_new = old_lines[o_close:], new_lines[n_close:]
    if h1 is not None:
        idx = next(i for i, ln in enumerate(tail_old) if ln.startswith(b"# "))
        eol = b"\r" if tail_old[idx].endswith(b"\r") else b""
        tail_old = [*tail_old[:idx], f"# {h1}".encode() + eol, *tail_old[idx + 1 :]]
    assert tail_new == tail_old, f"bytes after the frontmatter changed:\n{new.decode()}"

    if crlf:
        assert new.count(b"\n") == new.count(b"\r\n"), f"bare LF written:\n{new!r}"
    else:
        assert b"\r" not in new, f"LF file gained CR:\n{new!r}"


def _history(data: bytes) -> bytes:
    start = data.index(b"```yurtle")
    return data[start : data.index(b"```", start + 3) + 3]


def _comments(data: bytes) -> bytes:
    return data[data.index(b"## Comments") :]


# ---------------------------------------------------------------------------
# Acceptance 1: round-trip, one flag at a time
# ---------------------------------------------------------------------------

# flag -> (argv after the ID, {key: expected value}, new H1 or None).
# `{other}` / `{OTHER}`: the second item's ID, lower / upper case.
FLAGS: dict[str, tuple[list[str], dict[str, object], str | None]] = {
    "title": (["--title", "Beta prime"], {"title": "Beta prime"}, "Beta prime"),
    "priority": (["--priority", "LOW"], {"priority": "low"}, None),
    "tag": (["--tag", "gamma", "--tag", "delta"], {"tags": ["alpha", "gamma", "delta"]}, None),
    "untag": (["--untag", "alpha"], {"tags": []}, None),
    "depends-on": (["--depends-on", "{other}"], {"depends_on": ["{OTHER}"]}, None),
    "add-dep": (["--add-dep", "{other}"], {"depends_on": ["{OTHER}"]}, None),
    "related": (["--related", "{OTHER}"], {"related": ["{OTHER}"]}, None),
}


def _fill(value: object, other: str) -> object:
    if isinstance(value, str):
        return value.replace("{other}", other.lower()).replace("{OTHER}", other.upper())
    if isinstance(value, list):
        return [_fill(v, other) for v in value]
    return value


@pytest.mark.parametrize("flag", list(FLAGS))
def test_round_trip_changes_only_that_field(pair: Pair, flag: str) -> None:
    argv, expected, h1 = FLAGS[flag]
    it = pair.item
    old = it.path.read_bytes()
    _ok(["update", it.id, *[str(_fill(a, pair.other)) for a in argv]])
    new = it.path.read_bytes()
    assert _history(new) == _history(old), f"history block changed:\n{new.decode()}"
    assert _comments(new) == _comments(old), f"comments changed:\n{new.decode()}"
    assert re.search(rb"^status: " + it.status.encode() + rb"\r?$", new, re.M), (
        f"native status {it.status!r} rewritten:\n{new.decode()}"
    )
    _assert_only_changed(old, new, {k: _fill(v, pair.other) for k, v in expected.items()}, h1)


def test_several_flags_at_once(pair: Pair) -> None:
    it = pair.item
    old = it.path.read_bytes()
    _ok(["update", it.id, "--title", "Beta prime", "--priority", "critical",
         "--add-dep", pair.other.lower()])
    _assert_only_changed(
        old,
        it.path.read_bytes(),
        {"title": "Beta prime", "priority": "critical", "depends_on": [pair.other.upper()]},
        "Beta prime",
    )


def test_lowercase_item_id_argument(pair: Pair) -> None:
    """The CLI upper-cases the ID being updated, as `move`/`show` do."""
    it = pair.item
    old = it.path.read_bytes()
    _ok(["update", it.id.lower(), "--priority", "low"])
    _assert_only_changed(old, it.path.read_bytes(), {"priority": "low"})


@pytest.mark.parametrize("flag", ["add-dep", "related"])
def test_update_writes_no_rdf(pair: Pair, flag: str) -> None:
    """Expected §2: frontmatter is authoritative; no `kb:dependsOn`/`kb:related`
    triple and no new fenced block is written for these fields."""
    it = pair.item
    old = it.path.read_bytes()
    argv, _, _ = FLAGS[flag]
    _ok(["update", it.id, *[str(_fill(a, pair.other)) for a in argv]])
    new = it.path.read_bytes()
    for rdf in (b"kb:dependsOn", b"kb:related", b"kb:depends_on"):
        assert rdf not in new, f"{rdf!r} written:\n{new.decode()}"
    assert new.count(b"```") == old.count(b"```"), f"a fenced block was added:\n{new.decode()}"


# ---------------------------------------------------------------------------
# Acceptance 2 / §3: the body span
# ---------------------------------------------------------------------------


def _assert_body_replaced(old: bytes, new: bytes, *present: bytes) -> None:
    h1_end = old.index(b"\n", old.index(b"# Alpha one")) + 1
    assert new[:h1_end] == old[:h1_end], f"frontmatter/H1 changed:\n{new.decode()}"
    fence = old.index(b"```yurtle")
    assert new.endswith(old[fence:]), f"history fence / comments changed:\n{new.decode()}"
    assert _history(new) == _history(old)
    assert _comments(new) == _comments(old)
    span = new[h1_end : len(new) - len(old[fence:])]
    for text in present:
        assert text in span, f"{text!r} not in the body span {span!r}"
    assert BODY.encode() not in new, f"old body kept:\n{new.decode()}"
    if b"\r\n" in old:
        assert new.count(b"\n") == new.count(b"\r\n"), f"bare LF written:\n{new!r}"


def test_body_file_stdin_replaces_only_the_body(item: Item) -> None:
    """Acceptance 2: `--body-file -` (stdin, CRLF input normalised by #580)."""
    old = item.path.read_bytes()
    head = _head(item.root)
    _ok(["update", item.id, "--body-file", "-"], input=b"Fresh body one.\r\n\r\nline two\r\n")
    new = item.path.read_bytes()
    _assert_body_replaced(old, new, b"Fresh body one.", b"line two")
    assert _head(item.root) != head, "the body change was not committed"
    parsed = _service(item.root).get_item(item.id)
    assert parsed is not None
    assert parsed.description == "Fresh body one.\n\nline two", repr(parsed.description)
    assert [c.content.replace("\r\n", "\n") for c in parsed.comments] == ["a note worth keeping"]


def test_body_text_option(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    it = _build(tmp_path, monkeypatch, "nautical")
    old = it.path.read_bytes()
    _ok(["update", it.id, "--body", "Inline new body."])
    _assert_body_replaced(old, it.path.read_bytes(), b"Inline new body.")


def test_body_and_body_file_are_exclusive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    it = _build(tmp_path, monkeypatch, "nautical")
    old = it.path.read_bytes()
    result = invoke(["update", it.id, "--body", "x", "--body-file", "-"], input="y")
    assert result.exit_code != 0, result.output
    assert "mutually exclusive" in result.output, result.output  # #580's wording
    assert "Traceback" not in result.output
    assert it.path.read_bytes() == old


def test_body_without_h1_replaces_the_whole_span(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§3: no H1 → `--body` replaces the whole span (from the closing `---`); a
    fenced ```bash block with a `# install` line is body, not a heading."""
    it = _custom(tmp_path, monkeypatch, NO_H1)
    old = it.path.read_bytes()
    _ok(["update", it.id, "--body", "Fresh."])
    new = it.path.read_bytes()
    fm_close = old.index(b"\n---\n", 4) + len(b"\n---\n")
    assert new[:fm_close] == old[:fm_close], f"frontmatter changed:\n{new.decode()}"
    for gone in (b"Body text.", b"pip install thing", b"```bash"):
        assert gone not in new, f"{gone!r} left behind:\n{new.decode()}"
    parsed = _service(it.root).get_item(it.id)
    assert parsed is not None and parsed.description == "Fresh.", parsed


@pytest.mark.parametrize("crlf", [False, True], ids=["lf", "crlf"])
def test_body_keeps_a_leading_knowledge_block_and_h1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, crlf: bool
) -> None:
    """§3: span start is the closing `---`, but `--body` replaces only what follows
    the H1: a knowledge block above the H1 and the H1 itself stay byte-identical."""
    it, block = _leading_block(tmp_path, monkeypatch, "turtle", "", crlf)
    old = it.path.read_bytes()
    _ok(["update", it.id, "--body", "New body."])
    new = it.path.read_bytes()
    h1_end = old.index(b"\n", old.index(b"# Seed")) + 1
    assert new[:h1_end] == old[:h1_end], f"block/H1 changed:\n{new.decode()}"
    assert new.count(block) == 1
    assert b"Old body." not in new and b"New body." in new, new.decode()


HAND_YURTLE = (
    "\n# Alpha one\n\nIntro.\n\n"
    + KNOWLEDGE["yurtle"]
    + "\nTail para.\n"
)


def test_body_span_ends_at_the_history_block_not_any_yurtle_fence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§3, literally: the span ends at the CANONICAL status-history block (or
    `## Comments`), so a hand-written ```yurtle knowledge block after the H1 is body
    and `--body` replaces it; the real history block and comments are untouched.

    (Today's #583 `_replace_body` stops at the first ```yurtle fence of any kind; see
    the decision note in the PR. Delete this test if that behaviour is kept.)"""
    it = _custom(tmp_path, monkeypatch, HAND_YURTLE)
    svc = _service(it.root)
    from yurtle_kanban.models import WorkItemStatus

    svc.move_item(
        it.id, WorkItemStatus.REVIEW, commit=False, validate_workflow=False,
        skip_wip_check=True, skip_gates=True,
    )
    svc.add_comment(it.id, "keep me", "bob", commit=False)
    _commit_all(it.root, "history + comment")
    old = it.path.read_bytes()
    hist_start = old.index(b"kb:statusChange")
    hist_fence = old.rindex(b"```yurtle", 0, hist_start)
    _ok(["update", it.id, "--body", "Only this."])
    new = it.path.read_bytes()
    assert new.endswith(old[hist_fence:]), f"history/comments changed:\n{new.decode()}"
    for gone in (b"Intro.", b'kb:note "kept"', b"Tail para."):
        assert gone not in new, f"{gone!r} left in the body:\n{new.decode()}"
    assert b"Only this." in new


# ---------------------------------------------------------------------------
# Acceptance 10: WorkItem has no to_yurtle and no blocks
# ---------------------------------------------------------------------------


def _workitem_class() -> ast.ClassDef:
    tree = ast.parse(MODELS.read_text(encoding="utf-8"))
    return next(
        n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "WorkItem"
    )


def _defined_names(cls: ast.ClassDef) -> set[str]:
    names: set[str] = set()
    for node in cls.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            names.add(node.name)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.Assign):
            names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    return names


def test_workitem_has_no_to_yurtle() -> None:
    assert "to_yurtle" not in _defined_names(_workitem_class())
    hits = [p for p in SRC.rglob("*.py") if "to_yurtle" in p.read_text(encoding="utf-8")]
    assert not hits, f"`to_yurtle` still referenced in {hits}"


def test_workitem_has_no_blocks_field() -> None:
    cls = _workitem_class()
    assert "blocks" not in _defined_names(cls)
    attrs = {
        n.attr
        for n in ast.walk(cls)
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "self"
    }
    assert "blocks" not in attrs, "WorkItem still reads self.blocks"
    from yurtle_kanban.models import WorkItem

    assert "blocks" not in {f for f in getattr(WorkItem, "__dataclass_fields__", {})}
