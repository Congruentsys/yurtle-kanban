"""Issue #910 — `_first_written` tokenizer edge cases.

Found in the #909 (#876) review. All on the fallback path only: no URI subject of
the parent's turtle block is named after the parent, so the link goes on the
subject whose term is WRITTEN first (#876). The tokenizer got four things wrong:

1. The directive blanking wiped the whole line, so a triple written on the same
   line as `@prefix` / `@base` (or SPARQL `PREFIX` / `BASE`) was never read.
2. The directive pattern also matched a prefixed name whose prefix is literally
   `base` or `prefix` (`base:Zed …`), blanking that line.
3. PN_LOCAL stopped at `:`, `%` and `\\`: `ex:Aaa:x` read as `ex:Aaa`, `ex:Z%41`
   as `ex:Z`, `ex:a\\-b` as `ex:a`.
4. Every prefix used its final binding, and only the first `@base` was used.

Decided behaviour ([steer] on #910): only the directive STATEMENT is blanked (to
its `.`, or for SPARQL to its IRI); a directive is its keyword, whitespace, then a
`pname:` or `<`; PN_LOCAL follows the Turtle grammar (`:` inside, `%xx` kept
verbatim as rdflib keeps it, `\\`-escapes unescaped); prefixes and base apply
from where they are declared.

Every block has the parent id PAPER-130 and no subject named PAPER-130. In every
edge case the subject written first does NOT sort first by IRI, so "found
nothing, take the first in IRI order" can't pass by accident.
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


def link_line(written: str) -> str:
    return f"{written} paper:hasHypothesis hyp:H130.1 ."


# (inner block, the subject the link goes on as rdflib sees it, as the link writes it)
EDGE_CASES = [
    # 1. a triple on the directive's own line is read
    pytest.param(
        PREFIXES
        + "@prefix ex: <https://ex.org/> . ex:Beta a paper:Paper .\n"
        + "ex:Alpha a paper:Paper .\n",
        URIRef("https://ex.org/Beta"), "<https://ex.org/Beta>",
        id="triple-on-@prefix-line",
    ),
    pytest.param(
        PREFIXES
        + "@base <https://ex.org/> . <Beta> a paper:Paper .\n"
        + "<Alpha> a paper:Paper .\n",
        URIRef("https://ex.org/Beta"), "<https://ex.org/Beta>",
        id="triple-on-@base-line",
    ),
    pytest.param(
        PREFIXES
        + "PREFIX ex: <https://ex.org/> ex:Beta a paper:Paper .\n"
        + "ex:Alpha a paper:Paper .\n",
        URIRef("https://ex.org/Beta"), "<https://ex.org/Beta>",
        id="triple-on-SPARQL-PREFIX-line",
    ),
    pytest.param(
        PREFIXES
        + "BASE <https://ex.org/> <Beta> a paper:Paper .\n"
        + "<Alpha> a paper:Paper .\n",
        URIRef("https://ex.org/Beta"), "<https://ex.org/Beta>",
        id="triple-on-SPARQL-BASE-line",
    ),
    # 2. `base:` / `prefix:` are prefixed names, not directives
    pytest.param(
        PREFIXES
        + "@prefix base: <https://b.org/> .\n"
        + "\n"
        + "base:Zed a paper:Paper .\n"
        + "<https://a.org/Alpha> a paper:Paper .\n",
        URIRef("https://b.org/Zed"), "<https://b.org/Zed>",
        id="base-prefixed-name",
    ),
    pytest.param(
        PREFIXES
        + "@prefix prefix: <https://p.org/> .\n"
        + "\n"
        + "prefix:Zed a paper:Paper .\n"
        + "<https://a.org/Alpha> a paper:Paper .\n",
        URIRef("https://p.org/Zed"), "<https://p.org/Zed>",
        id="prefix-prefixed-name",
    ),
    # 3. PN_LOCAL per the Turtle grammar
    pytest.param(
        PREFIXES
        + "@prefix ex: <https://ex.org/> .\n"
        + "\n"
        + "ex:Aaa:x a paper:Paper .\n"
        + "ex:Aaa a paper:Paper .\n",
        URIRef("https://ex.org/Aaa:x"), "<https://ex.org/Aaa:x>",
        id="local-name-with-colon",
    ),
    pytest.param(
        PREFIXES
        + "@prefix ex: <https://ex.org/> .\n"
        + "\n"
        + "ex:Z%41 a paper:Paper .\n"
        + "ex:Z a paper:Paper .\n",
        URIRef("https://ex.org/Z%41"), "<https://ex.org/Z%41>",
        id="local-name-with-percent-escape",
    ),
    pytest.param(
        PREFIXES
        + "@prefix ex: <https://ex.org/> .\n"
        + "\n"
        + "ex:a\\-b a paper:Paper .\n"
        + "ex:a a paper:Paper .\n",
        URIRef("https://ex.org/a-b"), "<https://ex.org/a-b>",
        id="local-name-with-backslash-escape",
    ),
    # 4. prefixes and base apply from where they are declared
    pytest.param(
        PREFIXES
        + "@prefix ex: <https://one/> .\n"
        + "ex:A a paper:Paper .\n"
        + "@prefix ex: <https://two/> .\n"
        + "ex:B a paper:Paper .\n"
        + "<https://one/0> a paper:Paper .\n",
        URIRef("https://one/A"), "<https://one/A>",
        id="prefix-redeclared-mid-block",
    ),
    pytest.param(
        PREFIXES
        + "@base <https://one/> .\n"
        + "@base <https://two/> .\n"
        + "<B> a paper:Paper .\n"
        + "<https://one/A> a paper:Paper .\n",
        URIRef("https://two/B"), "<https://two/B>",
        id="base-redeclared",
    ),
]

# Controls (#876): green before and after.
CONTROL_CASES = [
    pytest.param(
        PREFIXES
        + "<#PAPER-10> a paper:Paper .\n"
        + "<#PAPER-1> a paper:Paper .\n",
        URIRef(f"{BASE}#PAPER-10"), "<#PAPER-10>",
        id="control-PAPER-10-before-PAPER-1",
    ),
    pytest.param(
        PREFIXES
        + "# see Venue\n"
        + "<#Other> a paper:Paper .\n"
        + "<#Venue> a paper:Paper .\n",
        URIRef(f"{BASE}#Other"), "<#Other>",
        id="control-comment-mentions-a-later-subject",
    ),
    pytest.param(
        PREFIXES
        + "<#Other> a paper:Paper .\n"
        + "<#> a paper:Paper .\n",
        URIRef(f"{BASE}#Other"), "<#Other>",
        id="control-empty-fragment-after",
    ),
    pytest.param(
        PREFIXES
        + "@prefix ex: <https://ex.org/Alpha/> .\n"
        + "\n"
        + "ex:Beta a paper:Paper .\n"
        + "ex:Alpha a paper:Paper .\n",
        URIRef("https://ex.org/Alpha/Beta"), "<https://ex.org/Alpha/Beta>",
        id="control-prefixed-Beta-before-Alpha",
    ),
    pytest.param(
        PREFIXES
        + "@prefix ex: <https://ex.org/> .\n"
        + "\n"
        + "ex:Beta a paper:Paper .\n"
        + "ex:Alpha a paper:Paper .\n",
        URIRef("https://ex.org/Beta"), "<https://ex.org/Beta>",
        id="control-prefixed-Beta-before-Alpha-plain-ns",
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
        + "# see Venue\n"
        + "<#PAPER-10> a paper:Paper .\n"
        + "<#> a paper:Paper .\n"
        + "<#Venue> a paper:Venue .\n"
        + "<#PAPER-130> a paper:Paper .\n",
        URIRef(f"{BASE}#PAPER-130"), "<#PAPER-130>",
        id="control-own-id-wins",
    ),
]

ALL_CASES = EDGE_CASES + CONTROL_CASES


def _subjects(inner: str) -> tuple[list[URIRef], dict]:
    g = Graph(bind_namespaces="none")
    g.parse(data=inner, format="turtle", publicID=BASE)
    return sorted({s for s in g.subjects() if isinstance(s, URIRef)}, key=str), dict(
        g.namespaces()
    )


@pytest.mark.parametrize("inner, subject, written", EDGE_CASES + CONTROL_CASES[:-1])
def test_rdflib_sees_the_expected_subject(inner, subject, written):
    """Harness check: the expected subject is one rdflib parses, and not the one
    that sorts first (so the IRI-order fallback can't satisfy an edge case)."""
    subjects, _ = _subjects(inner)
    assert subject in subjects
    assert len(subjects) >= 2


@pytest.mark.parametrize("inner, subject, written", EDGE_CASES)
def test_first_sorted_subject_is_not_the_answer(inner, subject, written):
    subjects, _ = _subjects(inner)
    assert subjects[0] != subject


@pytest.mark.parametrize("inner, subject, written", EDGE_CASES + CONTROL_CASES[:-1])
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
