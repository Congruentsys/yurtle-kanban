"""Issue #1020 — a prefixed name right after a blank-node label (`_:x:k`) is skipped.

Found in the PR #1019 (#1001) review. On the fallback path (no subject named after
the parent), `_first_written` matches the blank-node label `_:x` as its own token,
then the `:k` straight after it fails the prefixed name's lookbehind (the `x`
before it) and is never read.

Decided behaviour ([steer] on #1020, bucket 1): a blank-node label ends where
PN_CHARS end (no `:`), so `_:x:k` is `_:x` then `:k`, as rdflib reads it. The
blank-node token captures an immediately following prefixed name, which is then
resolved like any other.

A prefixed name with a non-empty prefix can never come straight after a label:
`_:xex:k` is the label `_:xex` then `:k` (rdflib agrees). So the "prefixed
variant" below is that shape, with `ex:k` also a subject, so reading `ex:k` out
of the label is wrong too.

Every block has the parent id PAPER-130 and no subject named PAPER-130. In every
edge case the only subject written before the others is the `:k` right after the
label, and `<https://a.org/Alpha>` (written later) sorts first by IRI, so both
"skip the `:k`" and "found nothing, take the first in IRI order" pick the wrong one.
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
    "@prefix : <https://d.org/> .\n"
    "@prefix ex: <https://ex.org/> .\n"
    "\n"
)
ALPHA = "<https://a.org/Alpha> a paper:Paper .\n"
K = URIRef("https://d.org/k")


def link_line(written: str) -> str:
    return f"{written} paper:hasHypothesis hyp:H130.1 ."


def block(first_line: str, *later: str) -> str:
    """`first_line` (the only early mention of a subject), then Alpha, then `later`."""
    return PREFIXES + first_line + "\n" + ALPHA + "".join(later)


# (inner block, the subject the link goes on as rdflib sees it, as the link writes it)
EDGE_CASES = [
    pytest.param(
        block('_:x:k "v" .', ":k a paper:Paper .\n"),
        K, "<https://d.org/k>",
        id="blank-then-empty-prefix-name",
    ),
    pytest.param(
        block('_:1:k "v" .', ":k a paper:Paper .\n"),
        K, "<https://d.org/k>",
        id="digit-first-blank-then-name",
    ),
    pytest.param(
        block('_:x-:k "v" .', ":k a paper:Paper .\n"),
        K, "<https://d.org/k>",
        id="blank-ending-in-dash-then-name",
    ),
    pytest.param(
        block('_:x:k:z "v" .', ":k:z a paper:Paper .\n"),
        URIRef("https://d.org/k:z"), "<https://d.org/k:z>",
        id="blank-then-name-with-colon",
    ),
    # the prefixed variant: `ex` belongs to the label, the name is `:k`, not `ex:k`
    pytest.param(
        block('_:xex:k "v" .', "ex:k a paper:Paper .\n", ":k a paper:Paper .\n"),
        K, "<https://d.org/k>",
        id="prefix-like-label-then-name",
    ),
    pytest.param(
        block('_:x.ex:k "v" .', "ex:k a paper:Paper .\n", ":k a paper:Paper .\n"),
        K, "<https://d.org/k>",
        id="dotted-prefix-like-label-then-name",
    ),
]

# green before and after
CONTROL_CASES = [
    pytest.param(
        block('_:x ex:k "v" .', "ex:k a paper:Paper .\n"),
        URIRef("https://ex.org/k"), "<https://ex.org/k>",
        id="control-blank-space-prefixed-name",
    ),
    pytest.param(
        block('_:x :k "v" .', ":k a paper:Paper .\n"),
        K, "<https://d.org/k>",
        id="control-blank-space-empty-prefix-name",
    ),
    pytest.param(
        block('[]:k "v" .', ":k a paper:Paper .\n"),
        K, "<https://d.org/k>",
        id="control-anon-blank-then-name",
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
    """Harness check: every block parses; the expected subject is one rdflib sees,
    and Alpha, written later, sorts first."""
    subjects, _ = _subjects(inner)
    assert subject in subjects
    assert subjects[0] == URIRef("https://a.org/Alpha") != subject


@pytest.mark.parametrize(
    "first_line, predicate",
    [
        ('_:x:k "v" .', "https://d.org/k"),
        ('_:x-:k "v" .', "https://d.org/k"),
        ('_:x:k:z "v" .', "https://d.org/k:z"),
        ('_:xex:k "v" .', "https://d.org/k"),
        ('_:x.ex:k "v" .', "https://d.org/k"),
        ('_:x ex:k "v" .', "https://ex.org/k"),
    ],
)
def test_rdflib_reads_the_label_then_the_name(first_line, predicate):
    """Harness check: rdflib reads the first line as a blank node, then the
    predicate named straight after it."""
    g = Graph(bind_namespaces="none")
    g.parse(data=PREFIXES + first_line + "\n", format="turtle", publicID=BASE)
    assert [str(p) for p in g.predicates()] == [predicate]


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
