"""Issue #764 — the push-time holder check folds case.

The [steer] on #764 (bucket 1, the #732 ruling: an ID is one ID whatever its case) is
the spec. ``_id_key`` folds its text half, ``_stem_holds`` compares a case-folded stem,
and ``_holder_at`` compares ids and stems case-folded. So:

(a) ``measure create … --push --id`` whose id origin already holds in another case is
    refused naming the rival, exactly as the exact-case control is, and nothing is
    pushed (the #760-review repros: ``m-042-rival.md`` vs ``--id M-042``, ``thing.md``
    with ``id: m-042`` vs ``--id M-042``, ``M-042-rival.md`` vs ``--id m-042``).
(b) ``_holder_at`` finds the holder case-folded, by stem or by frontmatter.
(c) ``--parent`` resolution through ``_parent_link_blob`` finds an origin parent spelled
    in another case (``literature --idea idea-r-001``, ``experiment --hypothesis
    h130.1``): the link rides in the child's one commit.

Controls (green before and after): #661's separator rule stays (``EXP3`` / ``exp3`` are
not ``EXP-3``); a paper-scoped ``H1.2-Title`` does not hold ``H1``; ``EXPR-042`` is not
``EXP-042``; the exact-case refusal is unchanged.

Reuses the #585/#590/#634/#645/#661/#752 real-git harness: ``World`` (bare remote,
clone A under test, rival clone B), ``b_push`` (B pushes to origin/main).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from tests.issues.test_585_create_push_loop import EXP_DIR, World, git, output_of
from tests.issues.test_590_next_id_and_hdd_ids import b_push
from tests.issues.test_603_push_failure_messages import invoke
from tests.issues.test_645_parent_in_cas import (
    KINDS,
    Kind,
    assert_one_commit_with_link,
    seed_on_origin,
)
from tests.test_634_explicit_ids_on_base import (
    assert_untouched,
    hdd,
    remote_ids,
    service,
    to_feature_branch,
)
from yurtle_kanban import config as config_mod
from yurtle_kanban.models import WorkItemType
from yurtle_kanban.service import KanbanService

MEASURE_DIR = "research/measures"
TITLE_A = "Alpha From A"


@pytest.fixture
def world(tmp_path, monkeypatch: pytest.MonkeyPatch) -> World:
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    return World(tmp_path)


@pytest.fixture(autouse=True)
def _clean_theme_cache() -> Iterator[None]:
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


def item_text(item_id: str | None, item_type: str) -> str:
    id_line = f"id: {item_id}\n" if item_id is not None else ""
    return f'---\n{id_line}title: "Rival"\ntype: {item_type}\nstatus: backlog\n---\n\n# Rival\n'


# --- (a) measure create --push --id: a case-only collision is refused ---------------------

# (name, rival file under MEASURE_DIR, rival's frontmatter id or None, --id)
CASE_ONLY = [
    ("stem-lower-vs-upper", "m-042-rival.md", None, "M-042"),
    ("frontmatter-lower-vs-upper", "thing.md", "m-042", "M-042"),
    ("stem-upper-vs-lower", "M-042-rival.md", None, "m-042"),
    ("frontmatter-upper-vs-lower", "thing.md", "M-042", "m-042"),
]
EXACT = [
    ("stem-exact", "M-042-rival.md", None, "M-042"),
    ("frontmatter-exact", "thing.md", "M-042", "M-042"),
]


def _measure_rival_then_create(world, monkeypatch, name, rival_id, measure_id):
    hdd(world)
    feat = to_feature_branch(world)
    path = f"{MEASURE_DIR}/{name}"
    b_push(world, {path: item_text(rival_id, "measure")})
    result = invoke(world, monkeypatch, [
        "measure", "create", TITLE_A, "--unit", "ms", "--category", "performance",
        "--id", measure_id, "--push",
    ])
    return result, " ".join(output_of(result).split()), path, feat


def _assert_refused(world, result, out, name, path, feat) -> None:
    assert result.exit_code != 0, f"a case-only collision was pushed: {out}"
    assert name in out, f"the refusal does not name the rival {name}: {out}"
    assert "already taken" in out, out
    assert list(remote_ids(world, f"{MEASURE_DIR}/")) == [path], (
        f"something was pushed beside the rival: {list(remote_ids(world, MEASURE_DIR + '/'))}"
    )
    assert_untouched(world, feat)


@pytest.mark.parametrize(("case", "name", "rival_id", "measure_id"), CASE_ONLY,
                         ids=[c[0] for c in CASE_ONLY])
def test_cli_measure_case_only_collision_refused(
    world, monkeypatch, case, name, rival_id, measure_id
) -> None:
    result, out, path, feat = _measure_rival_then_create(
        world, monkeypatch, name, rival_id, measure_id
    )
    _assert_refused(world, result, out, name, path, feat)


@pytest.mark.parametrize(("case", "name", "rival_id", "measure_id"), EXACT,
                         ids=[c[0] for c in EXACT])
def test_control_cli_measure_exact_case_refused(
    world, monkeypatch, case, name, rival_id, measure_id
) -> None:
    result, out, path, feat = _measure_rival_then_create(
        world, monkeypatch, name, rival_id, measure_id
    )
    _assert_refused(world, result, out, name, path, feat)


def test_cli_case_only_refusal_says_what_exact_case_says(world, monkeypatch, tmp_path) -> None:
    """The case-only refusal is the exact-case one, word for word (bar the id typed)."""
    _, exact_out, _, _ = _measure_rival_then_create(
        world, monkeypatch, "M-042-rival.md", None, "M-042"
    )
    config_mod._theme_cache.clear()
    (tmp_path / "other").mkdir()
    other = World(tmp_path / "other")
    _, fold_out, _, _ = _measure_rival_then_create(
        other, monkeypatch, "M-042-rival.md", None, "m-042"
    )
    assert fold_out.replace("m-042", "M-042") == exact_out, (fold_out, exact_out)


def test_service_expedition_case_only_collision_refused(world) -> None:
    """Service path, nautical board: `exp-3` against origin's `EXP-003-rival.md`."""
    path = f"{EXP_DIR}/EXP-003-rival.md"
    feat = to_feature_branch(world)
    b_push(world, {path: item_text("EXP-003", "expedition")})
    result = service(world).create_item_and_push(
        WorkItemType.EXPEDITION, TITLE_A, item_id="exp-3"
    )

    assert result["success"] is False, f"exp-3 landed beside EXP-003: {result}"
    assert "EXP-003-rival.md" in result["message"], result["message"]
    assert list(remote_ids(world, f"{EXP_DIR}/")) == [path]
    assert_untouched(world, feat)


# --- (b) _holder_at / _id_key / _stem_holds fold case --------------------------------------

# (case, {file name under EXP_DIR: frontmatter id or None}, id asked, expected holder name)
HOLDER_FOLDS = [
    ("stem-lower", {"exp-003-rival.md": None}, "EXP-003", "exp-003-rival.md"),
    ("stem-lower-padded", {"exp-003-rival.md": None}, "EXP-3", "exp-003-rival.md"),
    ("stem-upper", {"EXP-003-rival.md": None}, "exp-003", "EXP-003-rival.md"),
    ("stem-upper-exact-text", {"EXP-003.md": None}, "exp-003", "EXP-003.md"),
    ("stem-mixed", {"Exp-003-rival.md": None}, "eXP-3", "Exp-003-rival.md"),
    ("frontmatter-lower", {"thing.md": "exp-003"}, "EXP-003", "thing.md"),
    ("frontmatter-upper", {"thing.md": "EXP-003"}, "exp-3", "thing.md"),
    ("paper-scoped-lower", {"h130.2-x.md": None}, "H130.2", "h130.2-x.md"),
]
HOLDER_CONTROLS = [
    ("exact-stem", {"EXP-003-rival.md": None}, "EXP-003", "EXP-003-rival.md"),
    ("exact-padded", {"EXP-003-rival.md": None}, "EXP-3", "EXP-003-rival.md"),
    ("exact-frontmatter", {"thing.md": "EXP-003"}, "EXP-003", "thing.md"),
    ("undashed-vs-dashed", {"EXP-003-rival.md": None}, "EXP3", None),
    ("undashed-lower-vs-dashed", {"EXP-003-rival.md": None}, "exp3", None),
    ("dashed-vs-undashed-stem", {"exp3-rival.md": None}, "EXP-3", None),
    ("undashed-frontmatter", {"thing.md": "EXP-003"}, "exp3", None),
    ("paper-scoped-not-H1", {"H1.2-Title.md": None}, "H1", None),
    ("paper-scoped-lower-not-H1", {"h1.2-title.md": None}, "H1", None),
    ("longer-prefix-stem", {"EXPR-042-x.md": None}, "EXP-042", None),
    ("longer-prefix-stem-lower", {"expr-042-x.md": None}, "EXP-042", None),
    ("longer-prefix-frontmatter", {"thing.md": "expr-042"}, "EXP-042", None),
    ("other-number", {"exp-0031-x.md": None}, "EXP-3", None),
]


def _holder(world: World, files: dict[str, str | None], item_id: str) -> str | None:
    b_push(world, {
        f"{EXP_DIR}/{name}": item_text(fid, "expedition") for name, fid in files.items()
    })
    git(world.a, "fetch", "origin")
    return service(world)._holder_at("origin/main", item_id)


@pytest.mark.parametrize(("case", "files", "item_id", "expected"), HOLDER_FOLDS,
                         ids=[c[0] for c in HOLDER_FOLDS])
def test_holder_at_folds_case(world, case, files, item_id, expected) -> None:
    held = _holder(world, files, item_id)
    assert held == f"{EXP_DIR}/{expected}", f"{item_id} not held by {expected}: {held}"


@pytest.mark.parametrize(("case", "files", "item_id", "expected"), HOLDER_CONTROLS,
                         ids=[c[0] for c in HOLDER_CONTROLS])
def test_control_holder_at(world, case, files, item_id, expected) -> None:
    held = _holder(world, files, item_id)
    want = None if expected is None else f"{EXP_DIR}/{expected}"
    assert held == want, f"{item_id}: {held}"


@pytest.mark.parametrize(("a", "b"), [("exp-3", "EXP-003"), ("Idea-R-4", "IDEA-R-004"),
                                      ("h130.2", "H130.2")])
def test_id_key_folds_text_half(a, b) -> None:
    assert KanbanService._id_key(a) == KanbanService._id_key(b)


@pytest.mark.parametrize(("a", "b"), [("exp3", "EXP-3"), ("EXPR-3", "exp-3"), ("h1", "H-1")])
def test_control_id_key_keeps_the_separator(a, b) -> None:
    assert KanbanService._id_key(a) != KanbanService._id_key(b)


@pytest.mark.parametrize(("stem", "item_id"), [
    ("exp-003-Title", "EXP-3"),
    ("EXP-003-Title", "exp-3"),
    ("exp-003.v2", "EXP-003"),
    ("h130.2-x", "H130.2"),
])
def test_stem_holds_folds_case(stem, item_id) -> None:
    key = KanbanService._id_key(item_id)
    assert key is not None and KanbanService._stem_holds(stem, key)


@pytest.mark.parametrize(("stem", "item_id"), [
    ("h1.2-title", "H1"),
    ("exp3-x", "EXP-3"),
    ("expr-003-x", "EXP-3"),
    ("exp-0031", "EXP-3"),
])
def test_control_stem_holds_does_not_hold(stem, item_id) -> None:
    key = KanbanService._id_key(item_id)
    assert key is not None and not KanbanService._stem_holds(stem, key)


# --- (c) --push with a parent spelled in another case --------------------------------------

LIT, EXPR = KINDS[0], KINDS[2]


def _other_case(kind: Kind, parent_arg: str) -> Kind:
    """`kind`, with its parent argument typed as `parent_arg`."""
    argv = list(kind.argv)
    flag = "--idea" if kind is LIT else "--hypothesis"
    argv[argv.index(flag) + 1] = parent_arg
    return Kind(kind.name, kind.seed, argv, kind.child_dir, kind.parent_rel,
                kind.predicate, kind.child_prefix)


PARENT_FOLDS = [
    ("literature-lower", LIT, "idea-r-001"),
    ("literature-mixed", LIT, "Idea-R-1"),
    ("experiment-lower", EXPR, "h130.1"),
]


@pytest.mark.parametrize(("case", "kind", "parent_arg"), PARENT_FOLDS,
                         ids=[c[0] for c in PARENT_FOLDS])
def test_parent_in_other_case_is_linked(world, monkeypatch, case, kind, parent_arg) -> None:
    """The parent is on origin (and local): typed in another case, it is linked there."""
    kind = _other_case(kind, parent_arg)
    seed_on_origin(world, monkeypatch, kind)
    base = world.remote_sha()

    result = invoke(world, monkeypatch, kind.argv)
    out = output_of(result)

    assert result.exit_code == 0, f"{parent_arg} not found on origin: {out}"
    assert_one_commit_with_link(world, kind, base)


@pytest.mark.parametrize(("case", "kind", "parent_arg"), PARENT_FOLDS,
                         ids=[c[0] for c in PARENT_FOLDS])
def test_origin_only_parent_in_other_case_is_linked(
    world, monkeypatch, case, kind, parent_arg
) -> None:
    """The parent is on origin only (A is a commit behind): still found, still linked."""
    kind = _other_case(kind, parent_arg)
    seed_on_origin(world, monkeypatch, kind)
    git(world.a, "reset", "--hard", "HEAD~1")
    assert not (world.a / kind.parent_rel).exists()
    base = world.remote_sha()

    result = invoke(world, monkeypatch, kind.argv)
    out = output_of(result)

    assert result.exit_code == 0, out
    assert_one_commit_with_link(world, kind, base)


@pytest.mark.parametrize("kind", [LIT, EXPR], ids=repr)
def test_control_parent_exact_case_is_linked(world, monkeypatch, kind) -> None:
    seed_on_origin(world, monkeypatch, kind)
    base = world.remote_sha()

    result = invoke(world, monkeypatch, kind.argv)

    assert result.exit_code == 0, output_of(result)
    assert_one_commit_with_link(world, kind, base)
