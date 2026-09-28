"""Issue #956 (part 1) — a non-ASCII prefix name binds and resolves in `_first_written`.

Found in the PR #955 (#910) review. `_first_written`'s prefix pattern starts with
`[A-Za-z]`, so `@prefix é: <…>` is never read as a directive and `é:Beta` is never
read as a prefixed name: the fallback takes the subject whose IRI sorts first.

Decided behaviour ([steer] on #956, part 1): the prefix pattern follows Turtle's
PN_PREFIX — it starts with a letter in any script, then name characters and `.`,
and never ends in `.`. So `@prefix é: <…>` (and SPARQL `PREFIX é: <…>`) binds, and
`é:Name` resolves against it. A `.` inside a prefix (`ex.b:`) still works.

Part 2 (a relative `@prefix` IRI with no `@base`) is won't-fix and not tested here.

Every block has the parent id PAPER-130 and no subject named PAPER-130 (the
fallback path). In every edge case the subject written first does NOT sort first
by IRI, so "found nothing, take the first in IRI order" can't pass by accident.
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


def link_line(written: str) -> str:
    return f"{written} paper:hasHypothesis hyp:H130.1 ."


# (inner block, the subject the link goes on as rdflib sees it, as the link writes it)
EDGE_CASES = [
    pytest.param(
        PREFIXES
        + "@prefix é: <https://e.org/> .\n"
        + "\n"
        + "é:Beta a paper:Paper .\n"
        + ALPHA,
        URIRef("https://e.org/Beta"), "<https://e.org/Beta>",
        id="non-ascii-@prefix",
    ),
    pytest.param(
        PREFIXES
        + "PREFIX é: <https://e.org/>\n"
        + "\n"
        + "é:Beta a paper:Paper .\n"
        + ALPHA,
        URIRef("https://e.org/Beta"), "<https://e.org/Beta>",
        id="non-ascii-SPARQL-PREFIX",
    ),
    pytest.param(
        PREFIXES
        + "@prefix αβ: <https://g.org/> .\n"
        + "\n"
        + "αβ:Beta a paper:Paper .\n"
        + ALPHA,
        URIRef("https://g.org/Beta"), "<https://g.org/Beta>",
        id="non-latin-multi-letter-prefix",
    ),
    pytest.param(
        PREFIXES
        + "@prefix é.b: <https://e.org/> .\n"
        + "\n"
        + "é.b:Beta a paper:Paper .\n"
        + ALPHA,
        URIRef("https://e.org/Beta"), "<https://e.org/Beta>",
        id="non-ascii-prefix-with-inner-dot",
    ),
    pytest.param(
        PREFIXES
        + "@prefix é-1: <https://e.org/> .\n"
        + "\n"
        + "é-1:Beta a paper:Paper .\n"
        + ALPHA,
        URIRef("https://e.org/Beta"), "<https://e.org/Beta>",
        id="non-ascii-prefix-with-dash-and-digit",
    ),
    pytest.param(
        # a prefix can't end in `.`: `true.:Beta` is the literal `true`, the
        # statement's `.`, then the default-prefix name `:Beta` (rdflib agrees)
        PREFIXES
        + "@prefix : <https://d.org/> .\n"
        + "\n"
        + "[] paper:flag true.:Beta a paper:Paper .\n"
        + ALPHA,
        URIRef("https://d.org/Beta"), "<https://d.org/Beta>",
        id="prefix-never-ends-in-dot",
    ),
]

# ASCII prefixes (#910): green before and after.
CONTROL_CASES = [
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
        + "ex:Beta a paper:Paper .\n"
        + "ex:Alpha a paper:Paper .\n",
        URIRef("https://ex.org/Beta"), "<https://ex.org/Beta>",
        id="control-ascii-prefix",
    ),
    pytest.param(
        PREFIXES
        + "PREFIX ex: <https://ex.org/> ex:Beta a paper:Paper .\n"
        + "ex:Alpha a paper:Paper .\n",
        URIRef("https://ex.org/Beta"), "<https://ex.org/Beta>",
        id="control-ascii-SPARQL-PREFIX-same-line",
    ),
    pytest.param(
        PREFIXES
        + "@prefix base: <https://b.org/> .\n"
        + "\n"
        + "base:Zed a paper:Paper .\n"
        + ALPHA,
        URIRef("https://b.org/Zed"), "<https://b.org/Zed>",
        id="control-base-prefixed-name",
    ),
    pytest.param(
        PREFIXES
        + "@prefix : <https://d.org/> .\n"
        + "\n"
        + ":Beta a paper:Paper .\n"
        + ALPHA,
        URIRef("https://d.org/Beta"), "<https://d.org/Beta>",
        id="control-default-prefix",
    ),
    pytest.param(
        PREFIXES
        + "@prefix ex: <https://one/> .\n"
        + "ex:A a paper:Paper .\n"
        + "@prefix ex: <https://two/> .\n"
        + "ex:B a paper:Paper .\n"
        + "<https://one/0> a paper:Paper .\n",
        URIRef("https://one/A"), "<https://one/A>",
        id="control-prefix-redeclared-mid-block",
    ),
]

ALL_CASES = EDGE_CASES + CONTROL_CASES


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


def test_ascii_prefix_ending_in_dot_is_not_turtle():
    """Harness check: rdflib refuses a prefix name that ends in `.`."""
    with pytest.raises(Exception):
        _subjects(PREFIXES + "@prefix ex.: <https://e.org/> .\n\nex.:Beta a paper:Paper .\n")


@pytest.mark.parametrize("inner, subject, written", ALL_CASES)
def test_first_written_picks_the_subject_written_first(inner, subject, written):
    subjects, namespaces = _subjects(inner)
    assert KanbanService._first_written(inner, subjects, namespaces, BASE) == subject


def test_non_ascii_prefix_binds_where_declared():
    """The prefix binds from its own directive, not only via rdflib's final
    bindings: `é` is redeclared, and the first `é:A` resolves against the first."""
    inner = (
        PREFIXES
        + "@prefix é: <https://one/> .\n"
        + "é:A a paper:Paper .\n"
        + "@prefix é: <https://two/> .\n"
        + "é:B a paper:Paper .\n"
        + "<https://one/0> a paper:Paper .\n"
    )
    subjects, namespaces = _subjects(inner)
    assert subjects[0] != URIRef("https://one/A")
    assert KanbanService._first_written(inner, subjects, namespaces, BASE) == URIRef(
        "https://one/A"
    )


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
