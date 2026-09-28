"""Issue #645 — with ``--push``, a parent's turtle-block link rides in the SAME CAS commit.

Follow-up from the review of PR #642 (#623). ``literature/hypothesis/experiment create
--push`` with a parent landed the child on ``origin/<default>`` by compare-and-swap,
then committed the parent's inverse link on the CHECKED-OUT branch and ran a bare
``git push`` (``service._commit_and_push_file``). That could publish unrelated local
commits and split the child and its link across two branches.

Decided behaviour ([steer] on #645):

(a) From a FEATURE branch holding an unrelated unpushed commit, ``<type> create --push``
    with a parent that exists on origin/main lands exactly ONE new commit on
    origin/main, holding the child file and the parent's updated turtle block. The
    feature branch is not pushed, the unrelated commit is not published, and HEAD,
    the index and the tree are untouched. Covered for all three parent links:
    ``literature --idea``, ``hypothesis --paper``, ``experiment --hypothesis``.
(b) The same from main: one commit, holding the child and the parent link.
(c) A lost race, where a rival changes the parent file on origin between A's fetch and
    A's push: the retry rebuilds the parent update on the new base, so the rival's
    change is kept and the link is added, still in one commit.
(d) The parent is not on the base (it exists only locally): the create is refused with
    a clear message, and nothing is pushed.
(e) Control: without ``--push`` the parent update stays a local change, as today.
(f) From the #642 review: ``experiment run --push`` with an unchanged run config says
    'Run config unchanged' and pushes nothing.
(g) No bare ``"git", "push"`` call remains in service.py, and ``_commit_and_push_file``
    is gone.

Reuses the #585/#590/#634 real-git harness (a bare remote, clone A under test, rival
clone B, a ``subprocess.run`` wrapper recording pushes as #641's NetworkSpy does).
"""

from __future__ import annotations

import inspect
import re
import subprocess
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from rdflib import Graph, URIRef

from tests.issues.test_584_scoped_commit import _git, _head, _remote_head
from tests.issues.test_584_scoped_commit import _run as _run_584
from tests.issues.test_585_create_push_loop import (
    _ORIG_RUN,
    FEATURE,
    World,
    git,
    output_of,
    porcelain,
)
from tests.issues.test_603_push_failure_messages import HDD_DIRS, invoke, reconfigure
from tests.issues.test_614_registry_push import (  # noqa: F401  (fixtures)
    _on_remote,
    _unpushed_commit,
    hdd_repo,
    pushes,
)
from yurtle_kanban import config as config_mod
from yurtle_kanban import service as service_mod
from yurtle_kanban.service import KanbanService
from yurtle_kanban.turtle_builder import PREFIXES

UNRELATED = "unrelated unpushed work 645"
RIVAL_NOTE = "Rival note 645: kept across the retry."
PAPER = "research/papers/PAPER-130-A-paper.md"
TURTLE_RE = re.compile(r"```(?:turtle|yurtle)\s*\n(.*?)^```", re.S | re.M)


@pytest.fixture
def world(tmp_path, monkeypatch: pytest.MonkeyPatch) -> World:
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    return World(tmp_path)


@pytest.fixture(autouse=True)
def _clean_theme_cache() -> Iterator[None]:
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


class PushRecorder:
    """Wrap subprocess.run: record every git push, and call ``before(n)`` ahead of the
    n-th one (the #585 PushHook and the #641 NetworkSpy in one)."""

    def __init__(self, before: Callable[[int], None] | None = None) -> None:
        self.before = before
        self.pushes: list[list[str]] = []

    def __call__(self, *args: Any, **kwargs: Any) -> subprocess.CompletedProcess:
        cmd = args[0] if args else kwargs.get("args")
        if isinstance(cmd, (list, tuple)) and cmd and Path(str(cmd[0])).name == "git":
            words = [str(c) for c in cmd[1:]]
            if "push" in words:
                self.pushes.append(words)
                if len(self.pushes) > 25:
                    raise RuntimeError(f"unbounded push loop: {self.pushes}")
                if self.before is not None:
                    self.before(len(self.pushes))
        return _ORIG_RUN(*args, **kwargs)


def spy(monkeypatch, before: Callable[[int], None] | None = None) -> PushRecorder:
    rec = PushRecorder(before)
    monkeypatch.setattr(subprocess, "run", rec)
    return rec


# --- the three parent links -----------------------------------------------------------


class Kind:
    """One parent-link command: how to seed its parent, and what the link looks like."""

    def __init__(
        self, name: str, seed: list[list[str]], argv: list[str], child_dir: str,
        parent_rel: str, predicate: str, child_prefix: str,
    ) -> None:
        self.name = name
        self.seed = seed
        self.argv = argv
        self.child_dir = child_dir
        self.parent_rel = parent_rel
        self.predicate = predicate
        self.child_prefix = child_prefix

    def __repr__(self) -> str:
        return self.name


KINDS = [
    Kind(
        "literature",
        [["idea", "create", "An idea", "--type", "research"]],
        ["literature", "create", "Lit From A", "--idea", "IDEA-R-001", "--push"],
        "research/literature/",
        "research/ideas/IDEA-R-001-An-idea.md",
        "idea:hasLiterature",
        "lit",
    ),
    Kind(
        "hypothesis",
        [["paper", "create", "130", "A paper"]],
        ["hypothesis", "create", "Hyp From A", "--paper", "130", "--push"],
        "research/hypotheses/",
        PAPER,
        "paper:hasHypothesis",
        "hyp",
    ),
    Kind(
        "experiment",
        [
            ["paper", "create", "130", "A paper"],
            ["hypothesis", "create", "A hyp", "--paper", "130"],
        ],
        ["experiment", "create", "--hypothesis", "H130.1", "--title", "Exp From A", "--push"],
        "research/experiments/",
        "research/hypotheses/H130.1-A-hyp.md",
        "hyp:hasExperiment",
        "expr",
    ),
]
HYP = KINDS[1]


def hdd(world: World) -> None:
    reconfigure(world, "hdd", "research/", [f"research/{d}/" for d in HDD_DIRS])


def seed_on_origin(world: World, monkeypatch, kind: Kind) -> None:
    """Create the parent (locally, no --push), commit it on main and push it."""
    hdd(world)
    for argv in kind.seed:
        config_mod._theme_cache.clear()
        result = invoke(world, monkeypatch, argv)
        assert result.exit_code == 0, output_of(result)
    git(world.a, "add", "-A")
    git(world.a, "commit", "-m", f"seed {kind.name} parent")
    git(world.a, "push", "origin", "main")
    assert kind.parent_rel in world.remote_files(), world.remote_files()
    config_mod._theme_cache.clear()


def turtle_block(text: str) -> str:
    m = TURTLE_RE.search(text)
    assert m, f"no turtle block in:\n{text}"
    return m.group(1)


def frontmatter_id(text: str) -> str:
    m = re.search(r'^id:\s*"?([^"\s]+)"?\s*$', text, re.M)
    assert m, text
    return m.group(1)


def links(block: str, kind: Kind, child_id: str) -> bool:
    """The block parses and holds `<parent> kind.predicate <child>`: prefixed or in
    full, however the link was written (#812)."""
    ns, local = kind.predicate.split(":", 1)
    predicate = URIRef(PREFIXES[ns] + local)
    child = URIRef(PREFIXES[kind.child_prefix] + child_id)
    g = Graph()
    g.parse(data=block, format="turtle", publicID="urn:yurtle:block")
    return (None, predicate, child) in g


def commits_since(world: World, base: str) -> list[str]:
    return git(world.remote, "rev-list", f"{base}..main").split()


def touched(world: World, sha: str) -> set[str]:
    return set(git(world.remote, "show", "--name-only", "--format=", sha).split())


def assert_one_commit_with_link(world: World, kind: Kind, base: str) -> tuple[str, str]:
    """origin/main moved by exactly ONE commit, holding the child and the parent link.
    Returns (child path, child id)."""
    new = commits_since(world, base)
    assert len(new) == 1, (
        f"expected ONE commit on origin/main, got {len(new)}: "
        + git(world.remote, "log", "--format=%s", f"{base}..main")
    )
    files = touched(world, new[0])
    children = [p for p in files if p.startswith(kind.child_dir) and p.endswith(".md")]
    assert len(children) == 1, f"no single child file in the commit: {files}"
    assert kind.parent_rel in files, (
        f"the parent's link is not in the child's commit: {sorted(files)}"
    )
    assert files <= {children[0], kind.parent_rel, ".kanban/_ID_ALLOCATIONS.json"}, files
    child_id = frontmatter_id(world.remote_show(children[0]))
    block = turtle_block(world.remote_show(kind.parent_rel))
    assert links(block, kind, child_id), (
        f"origin's {kind.parent_rel} does not link {child_id}:\n{block}"
    )
    return children[0], child_id


def only_main_pushed(rec: PushRecorder) -> None:
    assert rec.pushes, "nothing was pushed"
    for words in rec.pushes:
        assert any(w.endswith(":refs/heads/main") for w in words), (
            f"a push that is not the CAS push to refs/heads/main: {words}"
        )


# --- (a) from a feature branch with an unrelated unpushed commit ---------------------


@pytest.mark.parametrize("kind", KINDS, ids=repr)
def test_feature_branch_one_commit_with_link(world, monkeypatch, kind) -> None:
    seed_on_origin(world, monkeypatch, kind)
    git(world.a, "checkout", "-b", FEATURE)
    (world.a / "feature.txt").write_text("feature work\n")
    git(world.a, "add", "feature.txt")
    git(world.a, "commit", "-m", "feature work")
    git(world.a, "push", "-u", "origin", FEATURE)
    feat_remote = world.remote_sha(FEATURE)
    (world.a / "other.txt").write_text("the user's own unpushed work\n")
    git(world.a, "add", "other.txt")
    git(world.a, "commit", "-m", UNRELATED)
    unrelated = git(world.a, "rev-parse", "HEAD").strip()
    parent_before = (world.a / kind.parent_rel).read_text()
    base = world.remote_sha()

    rec = spy(monkeypatch)
    result = invoke(world, monkeypatch, kind.argv)
    out = output_of(result)

    assert result.exit_code == 0, out
    assert_one_commit_with_link(world, kind, base)
    only_main_pushed(rec)
    assert world.remote_sha(FEATURE) == feat_remote, "the feature branch was pushed"
    reached = git(world.remote, "for-each-ref", "--contains", unrelated, check=False)
    assert reached.strip() == "", f"the unrelated local commit was published: {reached}"
    assert git(world.a, "rev-parse", "--abbrev-ref", "HEAD").strip() == FEATURE
    assert git(world.a, "rev-parse", "HEAD").strip() == unrelated, (
        "a commit was made on the checked-out feature branch: "
        + git(world.a, "log", "-1", "--format=%s")
    )
    assert porcelain(world.a) == [], f"the tree or index was touched: {porcelain(world.a)}"
    assert (world.a / kind.parent_rel).read_text() == parent_before, (
        "the parent file in the feature checkout was rewritten"
    )


# --- (b) from main ---------------------------------------------------------------------


def test_main_one_commit_with_link(world, monkeypatch) -> None:
    seed_on_origin(world, monkeypatch, HYP)
    base = world.remote_sha()

    rec = spy(monkeypatch)
    result = invoke(world, monkeypatch, HYP.argv)
    out = output_of(result)

    assert result.exit_code == 0, out
    child, child_id = assert_one_commit_with_link(world, HYP, base)
    only_main_pushed(rec)
    assert git(world.a, "rev-parse", "HEAD").strip() == world.remote_sha(), (
        "main checkout not fast-forwarded to origin/main"
    )
    assert porcelain(world.a) == [], porcelain(world.a)
    assert links(turtle_block((world.a / PAPER).read_text()), HYP, child_id)
    assert (world.a / child).exists()


# --- (c) a lost race on the parent file -----------------------------------------------


def test_lost_race_rebuilds_parent_on_new_base(world, monkeypatch) -> None:
    seed_on_origin(world, monkeypatch, HYP)
    rival: dict[str, str] = {}

    def rival_edits_parent(n: int) -> None:
        if n != 1:
            return
        git(world.b, "fetch", "origin")
        git(world.b, "reset", "--hard", "origin/main")
        path = world.b / PAPER
        path.write_text(path.read_text() + f"\n{RIVAL_NOTE}\n")
        git(world.b, "commit", "-am", "rival")
        git(world.b, "push", "origin", "HEAD:refs/heads/main")
        rival["sha"] = git(world.b, "rev-parse", "HEAD").strip()

    rec = spy(monkeypatch, rival_edits_parent)
    result = invoke(world, monkeypatch, HYP.argv)
    out = output_of(result)

    assert result.exit_code == 0, out
    assert "sha" in rival, "the rival never ran: A made no push"
    assert len(rec.pushes) >= 2, f"A did not retry after losing the race: {rec.pushes}"
    assert_one_commit_with_link(world, HYP, rival["sha"])
    assert RIVAL_NOTE in world.remote_show(PAPER), "the rival's change to the parent was lost"
    only_main_pushed(rec)
    assert porcelain(world.a) == [], porcelain(world.a)


# --- (d) the parent is not on the base ------------------------------------------------


def test_parent_only_local_is_refused(world, monkeypatch) -> None:
    hdd(world)
    result = invoke(world, monkeypatch, ["paper", "create", "130", "A paper"])
    assert result.exit_code == 0, output_of(result)
    assert porcelain(world.a) == [f"?? {PAPER}"], porcelain(world.a)
    paper_before = (world.a / PAPER).read_text()
    head, base = git(world.a, "rev-parse", "HEAD").strip(), world.remote_sha()
    config_mod._theme_cache.clear()

    rec = spy(monkeypatch)
    result = invoke(world, monkeypatch, HYP.argv)
    out = " ".join(output_of(result).split())

    assert result.exit_code != 0, f"a parent missing from origin/main was not refused:\n{out}"
    assert "Traceback" not in out, out
    assert "PAPER-130" in out, f"the refusal does not name the parent:\n{out}"
    assert re.search(r"origin|default branch|remote", out), (
        f"the refusal does not say the parent is not on the remote's default branch:\n{out}"
    )
    assert world.remote_sha() == base, "something was pushed"
    assert rec.pushes == [], f"pushed despite refusing: {rec.pushes}"
    assert git(world.a, "rev-parse", "HEAD").strip() == head, "a local commit was made"
    assert porcelain(world.a) == [f"?? {PAPER}"], porcelain(world.a)
    assert (world.a / PAPER).read_text() == paper_before, "the local parent was rewritten"


# --- (e) control: without --push the parent update stays local ------------------------


def test_without_push_parent_update_is_local(world, monkeypatch) -> None:
    seed_on_origin(world, monkeypatch, HYP)
    head, base = git(world.a, "rev-parse", "HEAD").strip(), world.remote_sha()

    rec = spy(monkeypatch)
    result = invoke(world, monkeypatch, HYP.argv[:-1])  # no --push
    out = output_of(result)

    assert result.exit_code == 0, out
    assert rec.pushes == [], rec.pushes
    assert world.remote_sha() == base
    assert git(world.a, "rev-parse", "HEAD").strip() == head, "a commit was made"
    status = porcelain(world.a)
    assert f" M {PAPER}" in status, f"the parent is not a local, unstaged change: {status}"
    children = [s for s in status if s.startswith("?? research/hypotheses/")]
    assert len(children) == 1, status
    child_id = frontmatter_id((world.a / children[0][3:]).read_text())
    assert links(turtle_block((world.a / PAPER).read_text()), HYP, child_id)


# --- (f) experiment run --push with an unchanged run config ---------------------------


def test_experiment_run_unchanged_config_does_not_push(
    hdd_repo, monkeypatch, pushes  # noqa: F811  (the imported fixtures)
) -> None:
    repo, remote = hdd_repo
    args = ["experiment", "run", "EXPR-130", "--being", "b-v1", "--push"]
    first = _run_584(repo, monkeypatch, args)
    assert first.exit_code == 0, first.output
    landed = _git(repo, "show", "--name-only", "--format=", "HEAD").split()
    assert len(landed) == 1 and landed[0].endswith("/config.yaml"), landed
    run_dir = repo / Path(landed[0]).parent

    # the run folder the second run returns is the one already committed, unchanged
    monkeypatch.setattr(
        KanbanService, "create_experiment_run", lambda self, **kw: run_dir
    )
    sha = _unpushed_commit(repo)
    head, rhead = _head(repo), _remote_head(remote)
    pushes.clear()
    result = _run_584(repo, monkeypatch, args)

    assert result.exit_code == 0, result.output
    assert "Run config unchanged" in " ".join(result.output.split()), result.output
    assert "Committed and pushed" not in result.output, result.output
    assert pushes == [], f"pushed with an unchanged run config: {pushes}"
    assert _head(repo) == head, "a commit was made for an unchanged run config"
    assert _remote_head(remote) == rhead
    assert not _on_remote(remote, sha), "the unrelated local commit was published"


# --- (g) no bare push remains in service.py --------------------------------------------


def test_no_bare_git_push_in_service() -> None:
    source = Path(inspect.getfile(service_mod)).read_text()
    hits = [
        f"{n}: {line.strip()}"
        for n, line in enumerate(source.splitlines(), 1)
        if re.search(r"""["']git["']\s*,\s*["']push["']""", line)
    ]
    assert hits == [], "bare git push left in service.py:\n" + "\n".join(hits)
    assert "_commit_and_push_file" not in source, (
        "_commit_and_push_file (the parent link's bare push) is still in service.py"
    )
