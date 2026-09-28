"""Issue #876 — the fallback subject is the one whose written term comes first.

When no URI subject of the parent's turtle block is named after the parent (#838),
`KanbanService._modify_turtle_block` falls back to "the subject written first". It
judged that by `turtle_content.find(local_name)`, a plain substring search, so:

- `<#PAPER-10>` written before `<#PAPER-1>` ties with it, and the IRI sort picks
  `<#PAPER-1>`;
- a comment near the top that mentions `Venue` makes `<#Venue>` win;
- a subject with an empty local name (`<#>`, `<https://x/>`) is found at 0 and
  always wins;
- a prefix declaration that happens to hold a subject's local name makes that
  subject win, whatever order the subjects are written in.

Decided behaviour ([steer] on #876): with no subject named after the parent, the
fallback subject is the one whose WRITTEN TERM comes first in the block. A subject's
written forms are its full IRI `<...>`, its `<#frag>` / relative form, and
`pfx:local` for each declared prefix, each searched outside comments as a whole
token. The own-id match (#838) and the single-subject fallback are unchanged.

Every block below has the parent id PAPER-130 but no subject named PAPER-130, so
every case here but the own-id control exercises the fallback.
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
    parse,
    service,
    with_lines,
    write,
)

repo = t812.repo  # the #812 repo fixture

PREFIXES = (
    "@prefix paper: <https://nusy.dev/paper/> .\n"
    "@prefix hyp: <https://nusy.dev/hypothesis/> .\n"
    "\n"
)

EX_NS = "https://ex.org/Alpha/"  # holds "Alpha", the second subject's local name


def link_line(written: str) -> str:
    return f"{written} paper:hasHypothesis hyp:H130.1 ."


# (inner block, the subject the link goes on as rdflib sees it, as the link writes it)
FALLBACK_CASES = [
    # 1. `<#PAPER-10>` first: `find("PAPER-1")` ties with it, and the sort picks PAPER-1
    pytest.param(
        PREFIXES
        + "<#PAPER-10> a paper:Paper .\n"
        + "<#PAPER-1> a paper:Paper .\n",
        URIRef(f"{BASE}#PAPER-10"), "<#PAPER-10>",
        id="PAPER-10-before-PAPER-1",
    ),
    # 2. a comment mentioning Venue does not write the subject `<#Venue>`
    pytest.param(
        PREFIXES
        + "# see Venue\n"
        + "<#Other> a paper:Paper .\n"
        + "<#Venue> a paper:Paper .\n",
        URIRef(f"{BASE}#Other"), "<#Other>",
        id="comment-mentions-a-later-subject",
    ),
    # 3. an empty local name is not written everywhere
    pytest.param(
        PREFIXES
        + "<#Other> a paper:Paper .\n"
        + "<#> a paper:Paper .\n",
        URIRef(f"{BASE}#Other"), "<#Other>",
        id="empty-fragment-after",
    ),
    pytest.param(
        PREFIXES
        + "<#Other> a paper:Paper .\n"
        + "<https://x/> a paper:Paper .\n",
        URIRef(f"{BASE}#Other"), "<#Other>",
        id="empty-path-local-after",
    ),
    # 4. prefixed forms: `ex:Beta` is written first, though `Alpha` sorts first and
    #    appears first as a substring (inside the prefix's own namespace IRI)
    pytest.param(
        PREFIXES
        + f"@prefix ex: <{EX_NS}> .\n"
        + "\n"
        + "ex:Beta a paper:Paper .\n"
        + "ex:Alpha a paper:Paper .\n",
        URIRef(f"{EX_NS}Beta"), f"<{EX_NS}Beta>",
        id="prefixed-Beta-before-Alpha",
    ),
]

# Controls: green before and after.
CONTROL_CASES = [
    # 4'. the prefixed case without a namespace that holds `Alpha`
    pytest.param(
        PREFIXES
        + "@prefix ex: <https://ex.org/> .\n"
        + "\n"
        + "ex:Beta a paper:Paper .\n"
        + "ex:Alpha a paper:Paper .\n",
        URIRef("https://ex.org/Beta"), "<https://ex.org/Beta>",
        id="control-prefixed-Beta-before-Alpha-plain-ns",
    ),
    # 6a. the own-id match wins over every subject written before it
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
    # 6b. one subject, not named after the parent: the fallback takes it
    pytest.param(
        PREFIXES
        + "# see Venue\n"
        + "<#Brain-Paper> a paper:Paper .\n",
        URIRef(f"{BASE}#Brain-Paper"), "<#Brain-Paper>",
        id="control-single-subject-fallback",
    ),
]


@pytest.mark.parametrize("inner, subject, written", FALLBACK_CASES + CONTROL_CASES)
def test_link_parent_uses_the_first_written_subject(repo, inner, subject, written):
    before = paper_file(inner)
    path = write(repo, PAPER_REL, before)

    assert service(repo).link_parent("PAPER-130", "hypothesis", "H130.1") == "added"

    after = path.read_text(encoding="utf-8")
    assert after == with_lines(before, link_line(written))
    assert (subject, PAPER_NS.hasHypothesis, HYP_NS["H130.1"]) in parse(after)


@pytest.mark.parametrize("inner, subject, written", FALLBACK_CASES + CONTROL_CASES)
def test_push_blob_uses_the_first_written_subject(repo, inner, subject, written):
    before = paper_file(inner)
    write(repo, PAPER_REL, before)
    commit_all(repo)

    blobs, state = service(repo)._parent_link_blob("HEAD", "PAPER-130", "hypothesis", "H130.1")

    assert state is None
    assert blobs == {PAPER_REL: with_lines(before, link_line(written))}


@pytest.mark.parametrize("inner, subject, written", FALLBACK_CASES + CONTROL_CASES)
def test_state_is_judged_on_the_first_written_subject(repo, inner, subject, written):
    """A link already on the first-written subject is 'linked'; nothing changes."""
    linked = inner + link_line(written) + "\n"
    path = write(repo, PAPER_REL, paper_file(linked))
    before = path.read_bytes()
    svc = service(repo)

    assert svc.parent_link_state("PAPER-130", "hypothesis", "H130.1") == "linked"
    assert svc.link_parent("PAPER-130", "hypothesis", "H130.1") == "linked"
    assert path.read_bytes() == before


# --- 5. the multi-subject fallback does not follow the hash seed -----------------------

ROOT = Path(__file__).resolve().parents[2]
SEEDS = range(12)
SEED_INNER = (
    PREFIXES
    + "# see Venue\n"
    + "<#PAPER-10> a paper:Paper .\n"
    + "<#Venue> a paper:Paper .\n"
    + "<#> a paper:Paper .\n"
    + "<#PAPER-1> a paper:Paper .\n"
)


def seed_case(root: str) -> None:
    """Run in a fresh interpreter: link PAPER-130 into the multi-subject fallback
    block; print the state and the line the link appended."""
    from yurtle_kanban.config import KanbanConfig, PathConfig

    repo = Path(root)
    git(repo, "init", "-q", "-b", "main")
    (repo / ".kanban").mkdir()
    (repo / "research" / "papers").mkdir(parents=True)
    KanbanConfig(
        theme="hdd",
        paths=PathConfig(root="research/", scan_paths=["research/papers/"]),
    ).save(repo / ".kanban" / "config.yaml")
    path = write(repo, PAPER_REL, paper_file(SEED_INNER))
    state = service(repo).link_parent("PAPER-130", "hypothesis", "H130.1")
    added = path.read_text(encoding="utf-8").split("```turtle\n", 1)[1].split("```", 1)[0]
    print(state, "|", added.rstrip("\n").splitlines()[-1])


def test_fallback_subject_does_not_follow_the_hash_seed(tmp_path):
    """Under every fixed hash seed the link lands on `<#PAPER-10>`, the subject
    written first."""
    code = (
        "import sys; from tests.issues.test_876_first_written_subject import seed_case; "
        "seed_case(sys.argv[1])"
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
    want = f"added | {link_line('<#PAPER-10>')}"
    assert got == {seed: want for seed in SEEDS}
