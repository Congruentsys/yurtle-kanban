"""Issue #1001 — more `_first_written` tokenizer edges.

Found in the PR #999 (#956) review, all on the fallback path (no subject named
after the parent). Three misreads:

1. A keyword followed by `.`: `true.x:Beta` is read as one prefix `true.x`, but
   rdflib reads `true`, the statement's `.`, then `x:Beta`. Same for `false.é:B`.
2. Python's `\\w` lacks combining marks (Mn/Mc), `·` (U+00B7) and U+203F–2040,
   which Turtle's PN_CHARS allows after the first character. A decomposed `é`
   (`e` + U+0301) prefix isn't read as a declaration; a local name with one of
   these is cut short.
3. A blank-node label with a `.` inside: `_:a.b:knows` (blank node `_:a.b`, then
   `:knows`) is read with `b:knows` as a prefixed name.

Decided behaviour ([steer] on #1001, bucket 1): a keyword (`a`, `true`, `false`)
followed by `.` ends there; after the first character PN_PREFIX and PN_LOCAL
admit combining marks, `·` and U+203F–2040; blank-node labels are matched as
their own token and skipped.

Every block has the parent id PAPER-130 and no subject named PAPER-130. In every
edge case the subject written first does NOT sort first by IRI, so "found
nothing, take the first in IRI order" can't pass by accident; where the misread
resolves to another subject, that subject is the one that sorts first.
"""

from __future__ import annotations

import pytest
from rdflib import Graph, URIRef

from tests.issues import test_812_append_link_textually as t812
from tests.issues.test_812_append_link_textually import (
    BASE,
    HYP_NS,
    PAPER_NS,
    PAPER_REL,
    commit_all,
    paper_file,
    parse,
    service,
    with_lines,
    write,
)
from yurtle_kanban.service import KanbanService

repo = t812.repo  # the #812 repo fixture

PREFIXES = (
    "@prefix paper: <https://nusy.dev/paper/> .\n"
    "@prefix hyp: <https://nusy.dev/hypothesis/> .\n"
    "\n"
)
ALPHA = "<https://a.org/Alpha> a paper:Paper .\n"
E_DECOMPOSED = "é"  # `e` + COMBINING ACUTE ACCENT (Mn), not the precomposed `é`


def link_line(written: str) -> str:
    return f"{written} paper:hasHypothesis hyp:H130.1 ."


# (inner block, the subject the link goes on as rdflib sees it, as the link writes it)
EDGE_CASES = [
    # 1. a keyword followed by `.` ends there
    pytest.param(
        PREFIXES
        + "@prefix x: <https://x.org/> .\n"
        + "\n"
        + "[] paper:flag true.x:Beta a paper:Paper .\n"
        + ALPHA,
        URIRef("https://x.org/Beta"), "<https://x.org/Beta>",
        id="true-dot-prefixed-name",
    ),
    pytest.param(
        PREFIXES
        + "@prefix é: <https://e.org/> .\n"
        + "\n"
        + "[] paper:flag false.é:B a paper:Paper .\n"
        + ALPHA,
        URIRef("https://e.org/B"), "<https://e.org/B>",
        id="false-dot-non-ascii-prefixed-name",
    ),
    # 2. combining marks, `·` and U+203F-2040 after the first character
    pytest.param(
        PREFIXES
        + f"@prefix {E_DECOMPOSED}: <https://e.org/> .\n"
        + "\n"
        + f"{E_DECOMPOSED}:Beta a paper:Paper .\n"
        + ALPHA,
        URIRef("https://e.org/Beta"), "<https://e.org/Beta>",
        id="decomposed-e-@prefix",
    ),
    pytest.param(
        PREFIXES
        + f"PREFIX {E_DECOMPOSED}: <https://e.org/>\n"
        + "\n"
        + f"{E_DECOMPOSED}:Beta a paper:Paper .\n"
        + ALPHA,
        URIRef("https://e.org/Beta"), "<https://e.org/Beta>",
        id="decomposed-e-SPARQL-PREFIX",
    ),
    pytest.param(
        PREFIXES
        + "@prefix a·b: <https://m.org/> .\n"
        + "\n"
        + "a·b:Beta a paper:Paper .\n"
        + ALPHA,
        URIRef("https://m.org/Beta"), "<https://m.org/Beta>",
        id="prefix-with-middle-dot",
    ),
    pytest.param(
        PREFIXES
        + "@prefix a‿b: <https://m.org/> .\n"
        + "\n"
        + "a‿b:Beta a paper:Paper .\n"
        + ALPHA,
        URIRef("https://m.org/Beta"), "<https://m.org/Beta>",
        id="prefix-with-undertie",
    ),
    pytest.param(
        # cut at the mark, `ex:Be` is the other subject (and sorts first)
        PREFIXES
        + "@prefix ex: <https://ex.org/> .\n"
        + "\n"
        + "ex:Béta a paper:Paper .\n"
        + "ex:Be a paper:Paper .\n",
        URIRef("https://ex.org/Béta"), "<https://ex.org/Béta>",
        id="local-name-with-combining-mark",
    ),
    pytest.param(
        PREFIXES
        + "@prefix ex: <https://ex.org/> .\n"
        + "\n"
        + "ex:a·b a paper:Paper .\n"
        + "ex:a a paper:Paper .\n",
        URIRef("https://ex.org/a·b"), "<https://ex.org/a·b>",
        id="local-name-with-middle-dot",
    ),
    pytest.param(
        PREFIXES
        + "@prefix ex: <https://ex.org/> .\n"
        + "\n"
        + "ex:a‿b a paper:Paper .\n"
        + "ex:a a paper:Paper .\n",
        URIRef("https://ex.org/a‿b"), "<https://ex.org/a‿b>",
        id="local-name-with-undertie-U+203F",
    ),
    pytest.param(
        PREFIXES
        + "@prefix ex: <https://ex.org/> .\n"
        + "\n"
        + "ex:a⁀b a paper:Paper .\n"
        + "ex:a a paper:Paper .\n",
        URIRef("https://ex.org/a⁀b"), "<https://ex.org/a⁀b>",
        id="local-name-with-tie-U+2040",
    ),
    # 3. a blank-node label with `.` inside is skipped, never read as `b:knows`;
    # `b:knows` is itself a subject (written later, sorting first)
    pytest.param(
        PREFIXES
        + "@prefix : <https://d.org/> .\n"
        + "@prefix b: <https://b.org/> .\n"
        + "\n"
        + "_:a.b:knows :Beta .\n"
        + ":Beta a paper:Paper .\n"
        + "b:knows a paper:Paper .\n",
        URIRef("https://d.org/Beta"), "<https://d.org/Beta>",
        id="blank-node-label-with-dot",
    ),
    pytest.param(
        PREFIXES
        + "@prefix : <https://d.org/> .\n"
        + "@prefix y.b: <https://b.org/> .\n"
        + "\n"
        + "_:x.y.b:knows :Beta .\n"
        + ":Beta a paper:Paper .\n"
        + "y.b:knows a paper:Paper .\n",
        URIRef("https://d.org/Beta"), "<https://d.org/Beta>",
        id="blank-node-label-with-two-dots",
    ),
    pytest.param(
        PREFIXES
        + "@prefix : <https://d.org/> .\n"
        + "@prefix b: <https://b.org/> .\n"
        + "\n"
        + "_:1.b:knows :Beta .\n"
        + ":Beta a paper:Paper .\n"
        + "b:knows a paper:Paper .\n",
        URIRef("https://d.org/Beta"), "<https://d.org/Beta>",
        id="blank-node-label-digit-first-with-dot",
    ),
]

# #956 and #910 shapes: green before and after.
CONTROL_CASES = [
    pytest.param(
        PREFIXES
        + "@prefix : <https://d.org/> .\n"
        + "\n"
        + "[] paper:flag true.:Beta a paper:Paper .\n"
        + ALPHA,
        URIRef("https://d.org/Beta"), "<https://d.org/Beta>",
        id="control-prefix-never-ends-in-dot",
    ),
    pytest.param(
        PREFIXES
        + "@prefix é: <https://e.org/> .\n"
        + "\n"
        + "é:Beta a paper:Paper .\n"
        + ALPHA,
        URIRef("https://e.org/Beta"), "<https://e.org/Beta>",
        id="control-precomposed-e-@prefix",
    ),
    pytest.param(
        PREFIXES
        + "@prefix ex.b: <https://e.org/> .\n"
        + "\n"
        + "ex.b:Beta a paper:Paper .\n"
        + ALPHA,
        URIRef("https://e.org/Beta"), "<https://e.org/Beta>",
        id="control-ascii-prefix-with-inner-dot",
    ),
    pytest.param(
        PREFIXES
        + "@prefix ex: <https://ex.org/> .\n"
        + "\n"
        + "ex:a-b.c a paper:Paper .\n"
        + "ex:a a paper:Paper .\n",
        URIRef("https://ex.org/a-b.c"), "<https://ex.org/a-b.c>",
        id="control-plain-local-with-dash-and-dot",
    ),
    pytest.param(
        PREFIXES
        + "@prefix ex: <https://ex.org/> .\n"
        + "\n"
        + "ex:Aaa:x a paper:Paper .\n"
        + "ex:Aaa a paper:Paper .\n",
        URIRef("https://ex.org/Aaa:x"), "<https://ex.org/Aaa:x>",
        id="control-local-name-with-colon",
    ),
    pytest.param(
        # a blank-node label with no dot, then a prefixed name after a space
        PREFIXES
        + "@prefix : <https://d.org/> .\n"
        + "@prefix b: <https://b.org/> .\n"
        + "\n"
        + "_:b :knows :Beta .\n"
        + ":Beta a paper:Paper .\n"
        + "b:knows a paper:Paper .\n",
        URIRef("https://d.org/Beta"), "<https://d.org/Beta>",
        id="control-blank-node-label-without-dot",
    ),
]

ALL_CASES = EDGE_CASES + CONTROL_CASES


def _inner(case_id: str) -> str:
    return next(c.values[0] for c in ALL_CASES if c.id == case_id)


def _subjects(inner: str) -> tuple[list[URIRef], dict]:
    g = Graph(bind_namespaces="none")
    g.parse(data=inner, format="turtle", publicID=BASE)
    return sorted({s for s in g.subjects() if isinstance(s, URIRef)}, key=str), dict(
        g.namespaces()
    )


@pytest.mark.parametrize("inner, subject, written", ALL_CASES)
def test_rdflib_sees_the_expected_subject(inner, subject, written):
    """Harness check: every block parses and the expected subject is one rdflib
    sees, beside at least one other."""
    subjects, _ = _subjects(inner)
    assert subject in subjects
    assert len(subjects) >= 2


@pytest.mark.parametrize("inner, subject, written", EDGE_CASES)
def test_first_sorted_subject_is_not_the_answer(inner, subject, written):
    subjects, _ = _subjects(inner)
    assert subjects[0] != subject


def test_decomposed_e_stays_decomposed():
    """Harness check: rdflib keeps the combining mark as written (no NFC), so the
    subject IRI really holds U+0301."""
    subjects, _ = _subjects(_inner("local-name-with-combining-mark"))
    assert URIRef("https://ex.org/Béta") in subjects
    assert URIRef("https://ex.org/Béta") not in subjects


def test_blank_node_label_keeps_its_dot():
    """Harness check: rdflib reads `_:a.b:knows :Beta` as blank node `_:a.b`,
    predicate `:knows`, object `:Beta` — no `b:knows` in predicate position."""
    g = Graph(bind_namespaces="none")
    g.parse(data=_inner("blank-node-label-with-dot"), format="turtle", publicID=BASE)
    assert list(g.predicates(None, URIRef("https://d.org/Beta"))) == [
        URIRef("https://d.org/knows")
    ]


@pytest.mark.parametrize("inner, subject, written", ALL_CASES)
def test_first_written_picks_the_subject_written_first(inner, subject, written):
    subjects, namespaces = _subjects(inner)
    assert KanbanService._first_written(inner, subjects, namespaces, BASE) == subject


@pytest.mark.parametrize("inner, subject, written", ALL_CASES)
def test_link_parent_uses_the_first_written_subject(repo, inner, subject, written):
    before = paper_file(inner)
    path = write(repo, PAPER_REL, before)

    assert service(repo).link_parent("PAPER-130", "hypothesis", "H130.1") == "added"

    after = path.read_text(encoding="utf-8")
    assert after == with_lines(before, link_line(written))
    assert (subject, PAPER_NS.hasHypothesis, HYP_NS["H130.1"]) in parse(after)


@pytest.mark.parametrize("inner, subject, written", ALL_CASES)
def test_push_blob_uses_the_first_written_subject(repo, inner, subject, written):
    before = paper_file(inner)
    write(repo, PAPER_REL, before)
    commit_all(repo)

    blobs, state = service(repo)._parent_link_blob("HEAD", "PAPER-130", "hypothesis", "H130.1")

    assert state is None
    assert blobs == {PAPER_REL: with_lines(before, link_line(written))}


@pytest.mark.parametrize("inner, subject, written", ALL_CASES)
def test_state_is_judged_on_the_first_written_subject(repo, inner, subject, written):
    """A link already on the first-written subject is 'linked'; nothing changes."""
    linked = inner + link_line(written) + "\n"
    path = write(repo, PAPER_REL, paper_file(linked))
    before = path.read_bytes()
    svc = service(repo)

    assert svc.parent_link_state("PAPER-130", "hypothesis", "H130.1") == "linked"
    assert svc.link_parent("PAPER-130", "hypothesis", "H130.1") == "linked"
    assert path.read_bytes() == before


# --- round 2 (PR #1019 review): a keyword-like prefix with a PN_CHARS extra ------------


@pytest.mark.parametrize(
    "prefix", ["tru" + "ë", "true·x", "false·y", "true‿y"],
    ids=["true-decomposed-diaeresis", "true-middot", "false-middot", "true-undertie"],
)
def test_keyword_like_prefix_with_pn_extra_binds(prefix: str) -> None:
    """`truë:` (a decomposed ë) and friends are prefixes, not the `true` keyword."""
    from rdflib import Graph, URIRef

    from yurtle_kanban.service import KanbanService

    text = (
        f"@prefix {prefix}: <https://m.org/> .\n"
        f"{prefix}:Beta <https://p.org/p> 1 .\n"
        "<https://a.org/Z> <https://p.org/p> 2 .\n"
    )
    g = Graph(bind_namespaces="none")
    g.parse(data=text, format="turtle", publicID="urn:yurtle:block")
    subjects = sorted({s for s in g.subjects() if isinstance(s, URIRef)}, key=str)
    assert URIRef("https://m.org/Beta") in subjects, subjects
    got = KanbanService._first_written(text, subjects, dict(g.namespaces()), "urn:yurtle:block")
    assert str(got) == "https://m.org/Beta", got
