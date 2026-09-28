"""Issue #838 — the parent link attaches to the parent's own subject.

`KanbanService._modify_turtle_block` attached the inverse triple to whichever URI
subject rdflib listed first. In a block with `<#OTHER>` and `<#PAPER-130>`, that is
`<#OTHER>` as often as not: rdflib's subject order follows the hash seed.

Its declared-prefix check also matched `prefix` inside a longer word, so a comment
or a string holding `xprefix hyp: <...>` counted `hyp:` as declared.

Decided behaviour ([steer] on #838):

- the triple attaches to the URI subject whose local name (after `#` or the last
  `/`) equals the parent's id, case folded;
- if no subject matches, it falls back to the first URI subject, as before;
- the declared-prefix regex matches `prefix` as a whole word only;
- the #812 append-only output and the #737 states are unchanged.

rdflib lists subjects in the order of a set of triples, so which subject comes
"first" follows the hash seed. Two defences keep the red real:

- the in-process cases carry many decoy subjects before the parent's, each with its
  own predicate and object, so the current code picks a decoy in nearly every
  process;
- `test_subject_choice_does_not_follow_the_hash_seed` runs the link in fresh
  interpreters under fixed PYTHONHASHSEEDs, which makes the red deterministic.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from rdflib import URIRef

from tests.issues import test_812_append_link_textually as t812
from tests.issues.test_812_append_link_textually import (
    BASE,
    HYP_NS,
    PAPER_NS,
    PAPER_REL,
    commit_all,
    git,
    paper_file,
    paper_inner,
    parse,
    service,
    with_lines,
    write,
)

repo = t812.repo  # the #812 repo fixture

N_DECOYS = 200
HEADER_COMMENT = "# PAPER-130 - hand-written; keep it as it is\n"


def decoys(form: str) -> str:
    """`N_DECOYS` other URI subjects, written in `form` ('relative' or 'absolute')."""
    if form == "relative":
        subject = "<#OTHER{}>"
    else:
        subject = "<https://nusy.dev/paper/OTHER{}>"
    names = [""] + [f"-{i}" for i in range(1, N_DECOYS)]
    # each decoy its own triple: the paper subject has about a dozen, and the
    # current pick is the subject of whichever triple the set yields first
    return "".join(
        f"{subject.format(n)} paper:rank{i} paper:Venue{i} .\n" for i, n in enumerate(names)
    ) + "\n"


def two_subject_inner(subject_line: str, form: str) -> str:
    """The #812 paper block with other URI subjects BEFORE the paper's own."""
    return paper_inner(subject_line).replace(HEADER_COMMENT, decoys(form))


# (subject line, decoy form, the parent's subject as rdflib sees it, as written)
SUBJECTS = [
    pytest.param("<#PAPER-130> a paper:Paper ;", "relative",
                 URIRef(f"{BASE}#PAPER-130"), "<#PAPER-130>", id="relative"),
    pytest.param("<https://nusy.dev/paper/PAPER-130> a paper:Paper ;", "absolute",
                 PAPER_NS["PAPER-130"], "<https://nusy.dev/paper/PAPER-130>", id="absolute"),
    pytest.param("paper:PAPER-130 a paper:Paper ;", "absolute",
                 PAPER_NS["PAPER-130"], "<https://nusy.dev/paper/PAPER-130>", id="prefixed"),
    pytest.param("<#paper-130> a paper:Paper ;", "relative",
                 URIRef(f"{BASE}#paper-130"), "<#paper-130>", id="case-folded"),
]


def link_line(written: str) -> str:
    return f"{written} paper:hasHypothesis hyp:H130.1 ."


# --- 1. the link goes on the parent's own subject -----------------------------------


@pytest.mark.parametrize("subject_line, form, subject, written", SUBJECTS)
def test_link_parent_attaches_to_the_parents_subject(repo, subject_line, form, subject, written):
    before = paper_file(two_subject_inner(subject_line, form))
    path = write(repo, PAPER_REL, before)

    assert service(repo).link_parent("PAPER-130", "hypothesis", "H130.1") == "added"

    after = path.read_bytes().decode("utf-8")
    assert after == with_lines(before, link_line(written))
    assert (subject, PAPER_NS.hasHypothesis, HYP_NS["H130.1"]) in parse(after)


ROOT = Path(__file__).resolve().parents[2]
SEEDS = range(12)
ONE_OTHER_INNER = (
    "@prefix paper: <https://nusy.dev/paper/> .\n"
    "@prefix hyp: <https://nusy.dev/hypothesis/> .\n"
    "\n"
    "<#OTHER> a paper:Venue .\n"
    "<#PAPER-130> a paper:Paper .\n"
)


def one_other_case(root: str) -> None:
    """Run in a fresh interpreter: the issue's own probe, one `<#OTHER>` before
    `<#PAPER-130>`, linked locally; print the line the link appended."""
    from yurtle_kanban.config import KanbanConfig, PathConfig

    repo = Path(root)
    git(repo, "init", "-q", "-b", "main")
    (repo / ".kanban").mkdir()
    (repo / "research" / "papers").mkdir(parents=True)
    KanbanConfig(
        theme="hdd",
        paths=PathConfig(root="research/", scan_paths=["research/papers/"]),
    ).save(repo / ".kanban" / "config.yaml")
    path = write(repo, PAPER_REL, paper_file(ONE_OTHER_INNER))
    state = service(repo).link_parent("PAPER-130", "hypothesis", "H130.1")
    added = path.read_text(encoding="utf-8").split("```turtle\n", 1)[1].split("```", 1)[0]
    print(state, "|", added.rstrip("\n").splitlines()[-1])


def test_subject_choice_does_not_follow_the_hash_seed(tmp_path):
    """Deterministic red: under every fixed hash seed the link lands on
    `<#PAPER-130>`, never on the `<#OTHER>` rdflib may list first."""
    code = (
        "import sys; from tests.issues.test_838_link_parent_subject import one_other_case; "
        "one_other_case(sys.argv[1])"
    )
    got = {}
    for seed in SEEDS:
        root = tmp_path / f"seed-{seed}"
        root.mkdir()
        env = {
            **os.environ,
            "PYTHONHASHSEED": str(seed),
            "PYTHONPATH": os.pathsep.join([str(ROOT / "src"), str(ROOT)]),
        }
        proc = subprocess.run(
            [sys.executable, "-c", code, str(root)],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=60,
        )
        assert proc.returncode == 0, proc.stderr
        got[seed] = proc.stdout.strip()
    want = "added | <#PAPER-130> paper:hasHypothesis hyp:H130.1 ."
    assert got == {seed: want for seed in SEEDS}


@pytest.mark.parametrize("subject_line, form, subject, written", SUBJECTS)
def test_push_blob_attaches_to_the_parents_subject(repo, subject_line, form, subject, written):
    before = paper_file(two_subject_inner(subject_line, form))
    write(repo, PAPER_REL, before)
    commit_all(repo)

    blobs, state = service(repo)._parent_link_blob("HEAD", "PAPER-130", "hypothesis", "H130.1")

    assert state is None
    assert blobs == {PAPER_REL: with_lines(before, link_line(written))}


# --- 2. idempotency is judged on the parent's subject -------------------------------


def linked_on_paper_inner() -> str:
    """Decoys first, then the paper subject, which already links H130.1 by hand."""
    return two_subject_inner("<#PAPER-130> a paper:Paper ;", "relative").replace(
        '    paper:pdf "PAPER-130.pdf" .\n',
        '    paper:pdf "PAPER-130.pdf" ;\n    paper:hasHypothesis hyp:H130.1 .\n',
    )


def test_link_parent_linked_on_the_parents_subject(repo):
    path = write(repo, PAPER_REL, paper_file(linked_on_paper_inner()))
    before = path.read_bytes()
    svc = service(repo)

    assert svc.parent_link_state("PAPER-130", "hypothesis", "H130.1") == "linked"
    assert svc.link_parent("PAPER-130", "hypothesis", "H130.1") == "linked"
    assert path.read_bytes() == before


def test_push_blob_linked_on_the_parents_subject(repo):
    write(repo, PAPER_REL, paper_file(linked_on_paper_inner()))
    commit_all(repo)

    blobs, state = service(repo)._parent_link_blob("HEAD", "PAPER-130", "hypothesis", "H130.1")

    assert (blobs, state) == ({}, "linked")


# --- 3. control: no subject matches, the first URI subject is used ---------------------


def test_no_matching_subject_falls_back_to_the_only_subject(repo):
    """Control (green before and after): the block's one URI subject is not named
    after the parent, so the link falls back to it."""
    before = paper_file(paper_inner("<#Brain-Paper> a paper:Paper ;"))
    path = write(repo, PAPER_REL, before)

    assert service(repo).link_parent("PAPER-130", "hypothesis", "H130.1") == "added"

    after = path.read_text(encoding="utf-8")
    assert after == with_lines(before, link_line("<#Brain-Paper>"))
    assert (URIRef(f"{BASE}#Brain-Paper"), PAPER_NS.hasHypothesis, HYP_NS["H130.1"]) in parse(after)


def test_local_name_must_equal_the_id_not_contain_it(repo):
    """Control: `<#PAPER-1300>` is not PAPER-130's subject; with it alone in the
    block, the fallback still links onto it."""
    before = paper_file(paper_inner("<#PAPER-1300> a paper:Paper ;"))
    path = write(repo, PAPER_REL, before)

    assert service(repo).link_parent("PAPER-130", "hypothesis", "H130.1") == "added"

    assert path.read_text(encoding="utf-8") == with_lines(before, link_line("<#PAPER-1300>"))


# --- 4. `xprefix` does not declare a prefix ---------------------------------------


# `hyp:` is declared by a RELATIVE IRI, so the declaration's text never matches its
# resolved namespace and the prefix does not count as declared: the child is written
# in full. Only a line holding `prefix hyp: <https://nusy.dev/hypothesis/>` could
# make it count — and `xprefix` is not the word `prefix`.
RELATIVE_HYP_INNER = (
    "@base <https://nusy.dev/paper/> .\n"
    "@prefix paper: <https://nusy.dev/paper/> .\n"
    "@prefix hyp: <../hypothesis/> .\n"
    "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n"
    "{extra}"
    "\n"
    "<PAPER-130> a paper:Paper ;\n"
    '    rdfs:label "Brain Architecture"{label_extra} .\n'
)
FULL_LINE = (
    "<https://nusy.dev/paper/PAPER-130> paper:hasHypothesis "
    "<https://nusy.dev/hypothesis/H130.1> ."
)
XPREFIX_CASES = [
    pytest.param("", "", id="control-no-mention"),
    pytest.param("# xprefix hyp: <https://nusy.dev/hypothesis/>\n", "", id="comment"),
    pytest.param("# see Xprefix hyp: <https://nusy.dev/hypothesis/>\n", "", id="comment-upper"),
    pytest.param(
        "", ' ;\n    rdfs:comment "xprefix hyp: <https://nusy.dev/hypothesis/>"', id="string"
    ),
]


@pytest.mark.parametrize("extra, label_extra", XPREFIX_CASES)
def test_xprefix_does_not_declare_a_prefix(repo, extra, label_extra):
    before = paper_file(RELATIVE_HYP_INNER.format(extra=extra, label_extra=label_extra))
    path = write(repo, PAPER_REL, before)

    assert service(repo).link_parent("PAPER-130", "hypothesis", "H130.1") == "added"

    after = path.read_text(encoding="utf-8")
    assert after == with_lines(before, FULL_LINE)
    assert (PAPER_NS["PAPER-130"], PAPER_NS.hasHypothesis, HYP_NS["H130.1"]) in parse(after)


@pytest.mark.parametrize("extra, label_extra", XPREFIX_CASES)
def test_xprefix_does_not_declare_a_prefix_push(repo, extra, label_extra):
    before = paper_file(RELATIVE_HYP_INNER.format(extra=extra, label_extra=label_extra))
    write(repo, PAPER_REL, before)
    commit_all(repo)

    blobs, state = service(repo)._parent_link_blob("HEAD", "PAPER-130", "hypothesis", "H130.1")

    assert state is None
    assert blobs == {PAPER_REL: with_lines(before, FULL_LINE)}
