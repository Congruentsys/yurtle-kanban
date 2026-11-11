"""Issue #1294, review round 2 (PR #1295 at bedd476, verdict CHANGES): partner tests for
the review's findings, under the driver's rulings.

- N1 (MAJOR) + m-a: ``validate --since REV`` becomes a STRUCTURAL check, replacing the
  line diff. The entry file now must equal the file at REV with insertions at exactly two
  cut points and nowhere else:
  (1) immediately before the old yurtle block's closing fence, a run with NO line that
      starts with three backticks;
  (2) only if the entry had no text and no digest at REV, right after the ``## Text``
      heading line: the text, which may hold any line (fences, ``## Text``) at all.
  Planted: the reviewer's ``smuggle2.sh`` / ``smuggle3.sh`` (a closer spelled
  ```` ```end ```` or a bare fence, then prose and a fresh ```` ```yurtle ```` opener; or the
  fresh opener alone) → refused. Legitimate: ``legit2.sh`` (a fill text ending in a
  ``## Text`` line; text holding fences and a ```` ```yurtle ```` line) and every verb
  (alloc, fill with relations, set, import) → rc 0; and a property test: a few hundred
  generated fill texts (fixed seed, as ``fuzzfill.py``), each through the real ``fill``
  verb, all pass ``validate --since`` and a reference implementation of the ruled rule.
  A ``~~~`` line is NOT planted: the entry parser knows only backtick fences, so a tilde
  line cannot move the block.
- m-b: two registers sharing one ``index:`` path, or an ``index:`` equal to another
  register's ``exempt:`` path → the config is refused, naming ``index``.
- m-c: a re-import that gives text for a number held as ``reserved`` or ``void`` → refused,
  naming the number, nothing written (as for a filled number, r1 m6).
- m-d: a hand-inserted ``reserved`` event on a filled entry → a validate error (as void).

The plants write the entry format the implementation writes (``<> kb:statusChange [ … ] .``
inside the ```` ```yurtle ```` block) and recompute the smuggled digest over the text a
last-block parser would see, so the plain digest check passes at bedd476 and only the
``--since`` rule can refuse them.
"""

from __future__ import annotations

import random
import re

import pytest

from tests.issues.test_585_create_push_loop import git
from tests.issues.test_1294_registers import (
    BOARDS,
    DECISIONS,
    EXEMPT,
    INDEX,
    OLD_LIST,
    PATTERN,
    STEPS,
    TEXT1,
    A,
    Reg,
    _import,
    _insert_only,
    alloc,
    fill,
    flat,
    ok,
    push_files,
    refused,
    show,
    sync,
    text_file,
    validate,
    yk,
)
from yurtle_kanban.registers import normalise_text, text_digest

pytestmark = pytest.mark.usefixtures("claim_env")

FENCE = "```"
HEADING = "\n## Text\n"
OPENER = "\n```yurtle\n"


@pytest.fixture
def reg(tmp_path, monkeypatch) -> Reg:
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    return Reg(tmp_path)


def event(status: str, digest: str | None = None) -> str:
    digest_line = f'    kb:textDigest "{digest}" ;\n' if digest else ""
    return (
        "<> kb:statusChange [\n"
        f"    kb:status kb:{status} ;\n{digest_line}"
        '    kb:at "2026-11-11T09:00:00+00:00"^^xsd:dateTime ;\n'
        '    kb:by "planter" ;\n'
        "  ] .\n"
    )


def closing_fence(text: str) -> int:
    """Offset of the closing fence line of the file's last yurtle block."""
    start = text.rfind(OPENER)
    assert start >= 0, text
    m = re.compile(r"^```", re.M).search(text, start + len(OPENER))
    assert m, text
    return m.start()


def before_fence(text: str, insert: str) -> str:
    i = closing_fence(text)
    return text[:i] + insert + text[i:]


def rc_names(res, *needles: str) -> str:
    out = flat(res.output)
    assert res.exit_code != 0, f"validate passed: {out}"
    for needle in needles:
        assert needle in out, f"{needle!r} not named: {out}"
    return out


def set_verb(reg: Reg, mp, n: int, *extra: str) -> None:
    sync(reg.a)
    ok(yk(reg.a, mp, "register", "set", "decisions", str(n), *extra, "--agent", A))


# --- the ruled rule, as a reference ------------------------------------------------------


def ruled_violation(old: str, new: str) -> str | None:
    """None when `new` is `old` grown at the two ruled cut points only (driver, r2 N1)."""
    close = closing_fence(old)
    h = old.find(HEADING)
    assert h >= 0, old
    cut_text = h + len(HEADING)
    text_at_rev = normalise_text(old[cut_text: old.rfind(OPENER)]).strip()
    first_fill = not text_at_rev and "kb:textDigest" not in old
    head, mid, tail = old[:cut_text], old[cut_text:close], old[close:]
    if not (new.startswith(head) and new.endswith(tail)
            and len(new) >= len(head) + len(mid) + len(tail)):
        return "the file outside the cut points changed"
    middle = new[len(head): len(new) - len(tail)]
    for k in range(len(middle) - len(mid) + 1):
        if middle[k: k + len(mid)] != mid:
            continue
        inserted_text, inserted_block = middle[:k], middle[k + len(mid):]
        if inserted_text and not first_fill:
            continue
        if any(line.startswith(FENCE) for line in inserted_block.splitlines()):
            continue
        return None
    return "not old plus insertions at the two cut points"


# --- N1: smuggled fences ------------------------------------------------------------------


def _prefixes(text: str) -> str:
    return "".join(line + "\n" for line in text.splitlines() if line.startswith("@prefix"))


def _smuggle(text: str, closer: str | None) -> str:
    """Insert, before the old closing fence, a closer (or none), new prose, a fresh
    yurtle opener and one event whose digest matches the text a last-block parser sees."""
    run = ""
    if closer is not None:
        run += f"{closer}\n\nWe decide NOT X. (rewritten)\n\n"
    run += "```yurtle\n" + _prefixes(text) + "\n"
    planted = before_fence(text, run + event("decided", "PLACEHOLDER"))
    h = planted.find(HEADING) + len(HEADING)
    seen = normalise_text(planted[h: planted.rfind(OPENER)])
    return planted.replace("PLACEHOLDER", text_digest(seen))


SMUGGLES = {"closer-backticks-end": "```end", "closer-bare-backticks": "```",
            "fresh-opener-only": None}


@pytest.fixture
def related(reg, monkeypatch) -> tuple[Reg, str]:
    """D1 filled, D2 fills --supersedes 1; returns (reg, BASE)."""
    alloc(reg, monkeypatch, "First")
    alloc(reg, monkeypatch, "Second")
    ok(fill(reg, monkeypatch, 1, TEXT1))
    ok(fill(reg, monkeypatch, 2, "Second ruling: supersedes the first.\n",
            "--supersedes", "1"))
    assert re.search(r"superseded by D?2\b", show(reg, monkeypatch, 1), re.I)
    return reg, reg.sha()


@pytest.mark.parametrize("n", [1, 2], ids=["text-of-D1", "relation-of-D2"])
@pytest.mark.parametrize("closer", list(SMUGGLES), ids=list(SMUGGLES))
def test_since_refuses_a_smuggled_fence(related, monkeypatch, closer, n) -> None:
    reg, base = related
    ok(validate(reg, monkeypatch, "--since", base))  # control
    path = reg.entry(n)
    old = reg.show(path)
    new = _smuggle(old, SMUGGLES[closer])
    _insert_only(old, new)  # the hard case: every old line survives, in order
    assert ruled_violation(old, new) is not None  # the reference rule refuses it
    push_files(reg, {path: new}, f"smuggle {closer} into D{n}")
    rc_names(validate(reg, monkeypatch, "--since", base), path)


# --- N1 / m-a: legitimate verb output passes ---------------------------------------------------


LEGIT_TEXTS = {
    "ends-with-text-heading": "Background.\n\n## Text\n",
    "starts-with-text-heading": "## Text\nStarts with the heading.\n",
    "embedded-code-fence": "Intro.\n```python\nprint(1)\n```\nAfter.\n",
    "embedded-yurtle-block": 'Quoted:\n```yurtle\n<> kb:title "quoted" .\n```\nDone.\n',
    "bare-fences-and-heading": "```\n## Text\n```\n",
}


@pytest.mark.parametrize("case", list(LEGIT_TEXTS), ids=list(LEGIT_TEXTS))
def test_since_accepts_a_fill_whatever_its_text(reg, monkeypatch, case) -> None:
    alloc(reg, monkeypatch, "One")
    base = reg.sha()
    old = reg.show(reg.entry(1))
    ok(fill(reg, monkeypatch, 1, LEGIT_TEXTS[case]))
    new = reg.show(reg.entry(1))
    assert ruled_violation(old, new) is None, "fill's own output breaks the ruled rule"
    ok(validate(reg, monkeypatch, "--since", base))
    ok(validate(reg, monkeypatch))
    assert normalise_text(LEGIT_TEXTS[case]).splitlines()[0] in show(reg, monkeypatch, 1)


def test_since_accepts_every_verb(reg, monkeypatch) -> None:
    seed = reg.sha()
    for title in ("One", "Two", "Three"):
        alloc(reg, monkeypatch, title)
    allocated = reg.sha()
    ok(fill(reg, monkeypatch, 1, TEXT1))
    ok(fill(reg, monkeypatch, 2, "Supersedes one.\n", "--supersedes", "1"))
    ok(fill(reg, monkeypatch, 3, "Narrows one.\n", "--narrows", "1"))
    set_verb(reg, monkeypatch, 1, "--status", "decided", "--title", "One, decided")
    set_verb(reg, monkeypatch, 2, "--status", "open")
    _import(reg, monkeypatch, "## 5 — 2026-10-15 — Five\nFive text.\n\n"
            "## 7 — 2026-10-17 — Seven\nSeven text.\n", PATTERN)
    for rev in (seed, allocated):
        ok(validate(reg, monkeypatch, "--since", rev))


POOL = ["x", "", "## Text", "```yurtle", "```", "```end", "~~~", "---", "# D1: First",
        "@prefix kb: <https://yurtle.dev/kanban/> .", "<> kb:statusChange [", "  ] .",
        "    kb:status kb:reserved ;", "title: First", "created: 1999-01-01",
        "Plain prose line.", "  indented", "## Comments"]
CASES = 300


def test_property_fill_output_always_passes_since(reg, monkeypatch) -> None:
    """CASES generated texts (seed 1294), each through the real `fill` verb. The clone has
    no remote, so each verb commits locally (fast) as `create --push` does offline."""
    rng = random.Random(1294)
    texts: list[str] = []
    while len(texts) < CASES:
        t = "\n".join(rng.choice(POOL) for _ in range(rng.randint(1, 5)))
        if normalise_text(t).strip():
            texts.append(t + rng.choice(["", "\n"]))
    git(reg.a, "remote", "remove", "origin")
    for i in range(CASES):
        ok(yk(reg.a, monkeypatch, "register", "alloc", "decisions", "--title", f"T{i}",
              "--agent", A))
    base = git(reg.a, "rev-parse", "HEAD").strip()
    olds = {p.name: p.read_text() for p in (reg.a / "registers/decisions").glob("*.md")}
    for i, t in enumerate(texts, start=1):
        res = yk(reg.a, monkeypatch, "register", "fill", "decisions", str(i), "--text-file",
                 text_file(reg, t, "prop.md"), "--agent", A)
        assert res.exit_code == 0, f"fill {i} of {t!r} failed: {flat(res.output)}"
    bad = []
    for p in (reg.a / "registers/decisions").glob("*.md"):
        why = ruled_violation(olds[p.name], p.read_text())
        if why:
            bad.append((p.name, why))
    assert bad == [], f"fill output breaking the ruled rule: {bad[:5]}"
    res = yk(reg.a, monkeypatch, "validate", "--since", base)
    assert res.exit_code == 0, f"--since refused verb output: {flat(res.output)[:2000]}"


# --- m-b: a shared index --------------------------------------------------------------------


SHARED_INDEX = [
    ("index-shared-by-two-registers",
     BOARDS + "registers:\n"
     + DECISIONS.replace("    sequence: integer\n", f"    sequence: integer\n    index: {INDEX}\n")
     + STEPS.replace("    sequence: alpha\n", f"    sequence: alpha\n    index: {INDEX}\n")),
    ("index-is-another-registers-exempt",
     BOARDS + "registers:\n" + DECISIONS
     + STEPS.replace("    sequence: alpha\n", f"    sequence: alpha\n    index: {EXEMPT}\n")),
]


@pytest.mark.parametrize("case,text", SHARED_INDEX, ids=[c[0] for c in SHARED_INDEX])
def test_a_shared_index_path_is_refused(reg, monkeypatch, case, text) -> None:
    ok(yk(reg.a, monkeypatch, "register", "list", "decisions"))  # control: the seed config
    (reg.a / ".kanban" / "config.yaml").write_text(text)
    out = refused(yk(reg.a, monkeypatch, "register", "list", "decisions"))
    assert "index" in out, f"{case}: the refusal does not name `index`: {out}"
    res = yk(reg.a, monkeypatch, "validate")
    assert res.exit_code != 0 and "index" in flat(res.output), f"{case}: {flat(res.output)}"


# --- m-c: a re-import giving text for a reserved or void number ---------------------------------


def _reimport_refused(reg: Reg, mp, body: str, cite: str) -> None:
    tip = reg.sha()
    files = {p: reg.show(p) for p in reg.files("registers/decisions/")}
    src = reg.tmp / "again.md"
    src.write_text(body)
    sync(reg.a)
    out = refused(yk(reg.a, mp, "register", "import", "decisions", "--from", str(src),
                     "--pattern", PATTERN, "--agent", A))
    assert re.search(rf"(?<![A-Za-z0-9]){cite}(?![0-9])", out), out
    assert reg.sha() == tip, "a refused import wrote to origin"
    assert {p: reg.show(p) for p in reg.files("registers/decisions/")} == files
    assert not git(reg.a, "status", "--porcelain").strip()


def test_reimport_with_text_for_a_reserved_number_is_refused(reg, monkeypatch) -> None:
    alloc(reg, monkeypatch, "Reserved one")
    _reimport_refused(reg, monkeypatch, OLD_LIST, "D1")
    assert "reserved" in show(reg, monkeypatch, 1).lower()


def test_reimport_with_text_for_a_void_number_is_refused(reg, monkeypatch) -> None:
    _import(reg, monkeypatch, OLD_LIST, PATTERN)  # D3 is void
    body = OLD_LIST + "\n## 3 — 2026-10-14 — Third rule\nThird text, late.\n"
    _reimport_refused(reg, monkeypatch, body, "D3")
    assert "void" in show(reg, monkeypatch, 3).lower()


# --- m-d: a reserved event on a filled entry ------------------------------------------------------


def test_a_reserved_event_on_a_filled_entry_is_an_error(reg, monkeypatch) -> None:
    alloc(reg, monkeypatch, "First")
    alloc(reg, monkeypatch, "Second")  # stays reserved: the control
    ok(fill(reg, monkeypatch, 1, TEXT1))
    set_verb(reg, monkeypatch, 1, "--status", "decided")
    base = reg.sha()
    ok(validate(reg, monkeypatch))  # control: a reserved entry that was never filled
    path = reg.entry(1)
    push_files(reg, {path: before_fence(reg.show(path), event("reserved"))}, "rewind")
    rc_names(validate(reg, monkeypatch), path)
    rc_names(validate(reg, monkeypatch, "--since", base), path)
