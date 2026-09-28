"""Issue #865: origin's config judges the fetched item's parse, legality and
``create --push``.

#831 made ``claim`` and ``update --push`` judge WIP limits, ignore patterns and board
paths by origin's config at the fetched commit (``_judge_at``). Three steps still used
the local config:

- the fetched item's status and type are parsed with the local theme;
- the claim's move-legality check uses the local theme and workflows;
- ``create --push`` allocation and the #777 parent lookup use local board paths.

Decided spec ([steer] on #865, bucket 1, extending #831's rule "a fetched tree is
judged by its own config" through ``_judge_at(rev)``):

1. Parse: the fetched item is parsed by the judge (origin's theme, including a
   ``.kanban/themes/<t>.yaml`` read from the rev, or a different theme origin's config
   picks). A status or type that only origin's theme names parses; no "unknown
   status" fallback to backlog, and the claim's holder/WIP logic sees the right status.
2. Legality: the claim's transition is checked against origin's theme and workflows
   (``.kanban/workflows/`` at the rev): it wins where origin allows and is refused
   where origin forbids.
3. ``create --push``: the id is allocated in the id space at origin's board paths, the
   item is written where origin's config places it, and the #777 parent lookup finds
   the parent at origin's board path with origin's ignore patterns applied (an
   ignored parent is 'missing').
4. Controls: with no config on origin the local config judges, as today. Gates stay
   local: a gate only the local config has still refuses.

Ambiguities resolved here (the test partner's reading; the driver may challenge):

a. Every scenario uses the #585 ``World`` (bare origin, clones A and B) and the #831
   pattern: B pushes a config/theme/workflow change A never pulls, and the service is
   built from A's LOCAL config, as the CLI builds it.
b. Parse is pinned through the claim's outcome: a ready-alias status only origin's
   theme names lets the claim win (locally it falls back to backlog, and backlog ->
   in_progress is illegal); an in-progress alias only origin names is refused as "in
   progress with no holder" (not "Illegal move"); a type only origin's theme names is
   found (locally the item doesn't parse: "does not hold it").
c. "Where origin's config places it" is pinned leniently: the new file is under
   origin's board root (``kanban-moved/``), not under the old local one.
d. ``test_claim_writes_origins_native_in_progress_name`` goes one step past the letter
   of the spec: once origin's theme (spec; hdd before #575) judges the claim, the
   status it writes must read back as in progress under that theme (spec
   ``implementing``, or the canonical ``in_progress``). Writing nautical's
   ``underway`` would put the item back in backlog on origin's board. Split out so it
   can be challenged on its own.
e. The WIP count of OTHER items at the fetched rev already goes through the judge
   (#831), so a WIP-full slot held under an origin-only status name is a control
   (green today), kept to guard the parse change.
f. Gates are config-driven (``gates:`` in config.yaml); "a gate present only locally"
   is a blocking gate in A's working-tree config that origin's config lacks.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import CliRunner

from tests.issues.test_573_states import _workflow_md
from tests.issues.test_574_claim import (
    OTHER,
    WIP_CONFIG,
    A,
    B,
    claim,
    frontmatter,
    item_text,
)
from tests.issues.test_574_sync_and_push import commit_files
from tests.issues.test_585_create_push_loop import EXP_DIR, World, git
from tests.issues.test_645_parent_in_cas import (
    HYP,
    PAPER,
    links,
    seed_on_origin,
    turtle_block,
)
from tests.issues.test_674_parent_edges import flat
from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import main
from yurtle_kanban.config import KanbanConfig, PathConfig
from yurtle_kanban.models import WorkItemType
from yurtle_kanban.service import KanbanService

REPO = Path(__file__).resolve().parents[2]
CONFIG = ".kanban/config.yaml"
THEME_OVERRIDE = ".kanban/themes/nautical.yaml"
WORKFLOW = ".kanban/workflows/expedition.yurtle.md"
ITEM = f"{EXP_DIR}/EXP-001-x.md"
ALLOC = ".kanban/_ID_ALLOCATIONS.json"

MOVED_ROOT = "kanban-moved/"
MOVED_EXP = "kanban-moved/expeditions"

WORKFLOW_READY_NO_CLAIM = {  # the default table, less ready -> in_progress
    "backlog": ["ready", "blocked"],
    "ready": ["backlog", "blocked"],
    "in_progress": ["review", "done", "blocked", "ready"],
    "review": ["done", "in_progress", "blocked"],
    "done": [],
    "blocked": ["ready", "in_progress", "backlog"],
}

GATED_CONFIG = """\
version: "2.0"
boards:
  - name: development
    preset: nautical
    path: kanban-work/
    gates:
      "* -> in_progress":
        - id: local_only_gate
          check: context.approved_865
          message: "the local-only gate 865 refuses"
default_board: development
"""


# --- harness ---------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    monkeypatch.delenv("YURTLE_AGENT", raising=False)
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


def push_from_a(world: World, files: dict[str, str], message: str) -> None:
    """Commit `files` on A's main and push them; B follows origin."""
    for rel, text in files.items():
        path = world.a / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    git(world.a, "add", "-A")
    git(world.a, "commit", "-m", message)
    git(world.a, "push", "origin", f"HEAD:refs/heads/{world.default}")
    git(world.b, "fetch", "origin")
    git(world.b, "reset", "--hard", f"origin/{world.default}")
    config_mod._theme_cache.clear()


def b_change(
    world: World,
    files: dict[str, str] | None = None,
    moves: dict[str, str] | None = None,
    removes: list[str] | None = None,
) -> None:
    """B, on a fresh origin/main: `git mv` each of `moves`, `git rm` `removes`, write
    `files`, commit and push. A never pulls it."""
    git(world.b, "fetch", "origin")
    git(world.b, "reset", "--hard", f"origin/{world.default}")
    for src, dst in (moves or {}).items():
        (world.b / dst).parent.mkdir(parents=True, exist_ok=True)
        git(world.b, "mv", src, dst)
    if removes:
        git(world.b, "rm", "-q", *removes)
    for rel, text in (files or {}).items():
        path = world.b / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    git(world.b, "add", "-A")
    git(world.b, "commit", "-m", "rival: 865")
    git(world.b, "push", "origin", f"HEAD:refs/heads/{world.default}")
    config_mod._theme_cache.clear()


def nautical_with(**changes: Any) -> str:
    """The built-in nautical theme, with `status_mappings` / `item_types` entries added
    and `transitions` set, as YAML text for an origin-only `.kanban/themes/` override."""
    theme = yaml.safe_load((REPO / "themes" / "nautical.yaml").read_text())
    theme["status_mappings"].update(changes.get("status_mappings", {}))
    theme["item_types"].update(changes.get("item_types", {}))
    if "transitions" in changes:
        theme["transitions"] = changes["transitions"]
    return yaml.safe_dump(theme, sort_keys=False)


def config_yaml(tmp_path: Path, config: KanbanConfig) -> str:
    path = tmp_path / "cfg-865" / "config.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    config.save(path)
    return path.read_text()


def world_paths(**kw: Any) -> PathConfig:
    return PathConfig(
        root="kanban-work/",
        scan_paths=["kanban-work/expeditions/", "kanban-work/signals/"],
        **kw,
    )


def service(clone: Path) -> KanbanService:
    return KanbanService(KanbanConfig.load(clone / CONFIG), clone)


def invoke(world: World, monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> Any:
    monkeypatch.chdir(world.a)
    return CliRunner().invoke(main, argv)


def assert_one_claim_commit(world: World, base: str, statuses: set[str]) -> dict[str, Any]:
    """Origin moved by one commit on `base`, touching only the item, which A holds at
    one of `statuses`."""
    tip = world.remote_sha()
    assert git(world.remote, "rev-list", f"{base}..{tip}").split() == [tip], (
        "origin must get exactly one new commit"
    )
    assert commit_files(world.remote, tip) == [ITEM], "the claim commit touched other files"
    fm = frontmatter(world.remote_show(ITEM))
    assert fm["assignee"] == A, fm
    assert fm["status"] in statuses, fm
    return fm


def local_config(world: World) -> str:
    return (world.a / CONFIG).read_text()


@pytest.fixture
def world(tmp_path: Path) -> World:
    """Origin and both clones hold EXP-001 at `ready`, unassigned, under World's
    single-board nautical config."""
    w = World(tmp_path)
    push_from_a(w, {ITEM: item_text("ready")}, "seed EXP-001")
    return w


# --- 1. parse: origin's theme names the fetched item's status and type ---------------------


def test_status_only_origins_theme_names_parses(world) -> None:
    """`berthed` is a ready alias only origin's theme override names: the claim wins.
    Locally it is unknown, falls back to backlog, and backlog -> in_progress is
    illegal."""
    b_change(world, {
        THEME_OVERRIDE: nautical_with(status_mappings={"berthed": "ready"}),
        ITEM: item_text("berthed"),
    })
    assert not (world.a / THEME_OVERRIDE).exists(), "A must not have origin's theme"
    base = world.remote_sha()

    out = claim(world.a, A)

    assert out.kind == "won", (
        f"origin's theme maps berthed -> ready, so the claim is legal: "
        f"{out.kind}: {out.message}"
    )
    assert_one_claim_commit(world, base, {"underway", "in_progress"})


def test_in_progress_alias_only_origin_names_is_no_holder(world) -> None:
    """`docked` is an in-progress alias only origin's theme names, with no holder: the
    claim is refused as in progress with no holder, not as an illegal move from
    backlog (the local fallback)."""
    b_change(world, {
        THEME_OVERRIDE: nautical_with(status_mappings={"docked": "in_progress"}),
        ITEM: item_text("docked"),
    })
    base = world.remote_sha()

    out = claim(world.a, A)

    assert out.kind == "refused", f"{out.kind}: {out.message}"
    assert "no holder" in out.message, (
        f"origin's theme reads docked as in progress; the claim judged another "
        f"status: {out.message}"
    )
    assert world.remote_sha() == base


def test_type_only_origins_theme_names_parses(world) -> None:
    """`sortie` is an item type only origin's theme override names: the item is found
    and the claim wins. Locally it doesn't parse ("does not hold it")."""
    sortie = {
        "sortie": {
            "id_prefix": "SRT", "icon": "s", "plural": "Sorties",
            "path": "kanban-work/expeditions/", "description": "Short runs",
        },
    }
    b_change(world, {
        THEME_OVERRIDE: nautical_with(item_types=sortie),
        ITEM: item_text("ready").replace("type: expedition", "type: sortie"),
    })
    base = world.remote_sha()

    out = claim(world.a, A)

    assert out.kind == "won", (
        f"origin's theme names the type sortie: {out.kind}: {out.message}"
    )
    assert_one_claim_commit(world, base, {"underway", "in_progress"})


def test_control_wip_counts_other_item_by_origins_status_name(world) -> None:
    """Control (green today, #831's judge counts WIP): EXP-002 holds the one
    in-progress slot under `docked`, a name only origin's theme has."""
    b_change(world, {
        CONFIG: WIP_CONFIG,
        THEME_OVERRIDE: nautical_with(status_mappings={"docked": "in_progress"}),
        OTHER: item_text("docked", B, "EXP-002", "Y"),
    })
    base = world.remote_sha()

    out = claim(world.a, A)

    assert out.kind == "refused", f"{out.kind}: {out.message}"
    assert "wip" in out.message.lower(), out.message
    assert world.remote_sha() == base


# --- 2. legality: origin's theme and workflows judge the claim's move ----------------------


def _spec_on_origin(world: World, tmp_path: Path) -> None:
    """Origin's config picks the spec theme (proposed -> implementing is legal);
    EXP-001 is `proposed` there. A keeps nautical, where `proposed` is no status (it
    falls back to backlog) and backlog -> in_progress is illegal.

    #575: this was hdd `draft`, but hdd has no ready column, so claim now refuses
    every hdd item (not pickable). spec's `proposed` maps to canonical ready."""
    b_change(world, {
        CONFIG: config_yaml(tmp_path, KanbanConfig(theme="spec", paths=world_paths())),
        ITEM: item_text("proposed"),
    })
    assert "spec" not in local_config(world)


def test_origins_theme_allows_the_claim(world, tmp_path) -> None:
    _spec_on_origin(world, tmp_path)  # a pickable item under origin's theme (#575)
    base = world.remote_sha()

    out = claim(world.a, A)

    assert out.kind == "won", (
        f"origin's spec theme allows proposed -> implementing: {out.kind}: {out.message}"
    )
    tip = world.remote_sha()
    assert git(world.remote, "rev-list", f"{base}..{tip}").split() == [tip]
    assert frontmatter(world.remote_show(ITEM))["assignee"] == A


def test_claim_writes_origins_native_in_progress_name(world, tmp_path) -> None:
    """Ambiguity d: the status written reads back as in progress under origin's theme
    (spec `implementing` or canonical `in_progress`), never nautical's `underway`.
    (#575: origin's theme is spec, not hdd, whose items are never pickable.)"""
    _spec_on_origin(world, tmp_path)
    base = world.remote_sha()

    out = claim(world.a, A)

    assert out.kind == "won", f"{out.kind}: {out.message}"
    assert_one_claim_commit(world, base, {"implementing", "in_progress"})


def test_origins_theme_transitions_forbid_the_claim(world) -> None:
    """Origin's theme override has `transitions` with no provisioning -> underway:
    the claim is refused as an illegal move. Locally (no transitions) it is legal."""
    transitions = {
        "harbor": ["provisioning", "stranded"],
        "provisioning": ["harbor", "stranded"],
        "underway": ["approaching_port", "arrived", "stranded", "provisioning"],
        "approaching_port": ["arrived", "underway", "stranded"],
        "arrived": [],
        "stranded": ["provisioning", "harbor"],
    }
    b_change(world, {THEME_OVERRIDE: nautical_with(transitions=transitions)})
    base = world.remote_sha()

    out = claim(world.a, A)

    assert out.kind == "refused", (
        f"origin's theme forbids ready -> in_progress: {out.kind}: {out.message}"
    )
    assert "Illegal move" in out.message, out.message
    assert world.remote_sha() == base


def test_origins_workflow_forbids_the_claim(world) -> None:
    """Origin has `.kanban/workflows/expedition.yurtle.md` without ready ->
    in_progress; A has no workflow (the default table allows it)."""
    b_change(world, {
        WORKFLOW: _workflow_md(WORKFLOW_READY_NO_CLAIM, applies_to="expedition"),
    })
    assert not (world.a / WORKFLOW).exists()
    base = world.remote_sha()

    out = claim(world.a, A)

    assert out.kind == "refused", (
        f"origin's workflow forbids ready -> in_progress: {out.kind}: {out.message}"
    )
    assert "Illegal move" in out.message, out.message
    assert world.remote_sha() == base


def test_origins_missing_workflow_allows_the_claim(world) -> None:
    """A has an (untracked) workflow forbidding ready -> in_progress; origin has a
    config and no workflow: origin's default lifecycle allows the claim."""
    local = world.a / WORKFLOW
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_text(_workflow_md(WORKFLOW_READY_NO_CLAIM, applies_to="expedition"))
    assert CONFIG in world.remote_files() and WORKFLOW not in world.remote_files()
    base = world.remote_sha()

    out = claim(world.a, A)

    assert out.kind == "won", (
        f"origin has no workflow, so ready -> in_progress is legal there: "
        f"{out.kind}: {out.message}"
    )
    assert_one_claim_commit(world, base, {"underway", "in_progress"})


# --- 3. create --push: origin's board paths and ignore patterns ----------------------------


def _moved_board(world: World, tmp_path: Path) -> None:
    """Origin's config moves the board to `kanban-moved/` (EXP-001 moved with it) and
    holds EXP-007 there. A never pulls it: its board is still `kanban-work/`."""
    moved = KanbanConfig(
        theme="nautical",
        paths=PathConfig(
            root=MOVED_ROOT,
            scan_paths=[f"{MOVED_EXP}/", "kanban-moved/signals/"],
        ),
    )
    b_change(
        world,
        {
            CONFIG: config_yaml(tmp_path, moved),
            f"{MOVED_EXP}/EXP-007-seven.md": item_text("backlog", None, "EXP-007", "Seven"),
        },
        moves={ITEM: f"{MOVED_EXP}/EXP-001-x.md"},
    )
    assert "kanban-moved" not in local_config(world)
    git(world.a, "fetch", "-q", "origin")


def _created(world: World, base: str) -> str:
    new = git(world.remote, "rev-list", f"{base}..main").split()
    assert len(new) == 1, f"the create did not land as one commit: {new}"
    items = [
        f for f in commit_files(world.remote, new[0])
        if f.endswith(".md") and f != ALLOC
    ]
    assert len(items) == 1, items
    return items[0]


def test_create_push_allocates_in_origins_id_space(world, tmp_path) -> None:
    _moved_board(world, tmp_path)
    base = world.remote_sha()

    result = service(world.a).create_item_and_push(WorkItemType.EXPEDITION, "New")

    assert result["success"], result.get("message")
    assert result["id"] == "EXP-008", (
        f"origin's board (kanban-moved/) holds EXP-007, so the next id is EXP-008: "
        f"{result['id']}"
    )
    assert "EXP-008" in _created(world, base)


def test_create_push_writes_where_origins_config_places_it(world, tmp_path) -> None:
    _moved_board(world, tmp_path)
    base = world.remote_sha()

    result = service(world.a).create_item_and_push(WorkItemType.EXPEDITION, "New")

    assert result["success"], result.get("message")
    rel = _created(world, base)
    assert rel.startswith(MOVED_ROOT), (
        f"origin's config puts the board at {MOVED_ROOT}; the item went to {rel}"
    )


def test_control_create_push_without_origin_config_uses_local(world) -> None:
    """Control (green today): origin has no config, so A's board paths judge."""
    b_change(world, removes=[CONFIG])
    assert CONFIG not in world.remote_files()
    # off the default branch, so the landed create can't fast-forward A's checkout to
    # origin's config-less tree
    git(world.a, "checkout", "-q", "-b", "feat-865")
    base = world.remote_sha()

    result = service(world.a).create_item_and_push(WorkItemType.EXPEDITION, "New")

    assert result["success"], result.get("message")
    assert result["id"] == "EXP-002", result["id"]
    assert _created(world, base).startswith(f"{EXP_DIR}/"), _created(world, base)


PAPER_EXTRA = "research-extra/papers/PAPER-130-A-paper.md"
PAPER_SHELVED = "research/papers/shelved/PAPER-130-A-paper.md"
MISSING_LINE = "PAPER-130 is not on any board"
HDD_SCAN = [
    f"research/{d}/"
    for d in ("ideas", "literature", "papers", "hypotheses", "experiments", "measures")
]


def test_parent_found_at_origins_board_path(world, tmp_path, monkeypatch) -> None:
    """Origin moved PAPER-130 to `research-extra/papers/`, a board path only origin's
    config names: `hypothesis create --paper 130 --push` links it there, in the
    child's commit."""
    seed_on_origin(world, monkeypatch, HYP)
    extra = KanbanConfig(
        theme="hdd",
        paths=PathConfig(root="research/", scan_paths=[*HDD_SCAN, "research-extra/papers/"]),
    )
    b_change(world, {CONFIG: config_yaml(tmp_path, extra)}, moves={PAPER: PAPER_EXTRA})
    assert "research-extra" not in local_config(world)
    base = world.remote_sha()

    result = invoke(world, monkeypatch, list(HYP.argv))

    out = flat(result)
    assert result.exit_code == 0, out
    new = git(world.remote, "rev-list", f"{base}..main").split()
    assert len(new) == 1, new
    files = set(commit_files(world.remote, new[0]))
    children = [f for f in files if f.startswith(HYP.child_dir) and f.endswith(".md")]
    assert len(children) == 1, files
    assert PAPER_EXTRA in files, (
        f"the parent at origin's board path was not linked in the child's commit: "
        f"{sorted(files)}\n{out}"
    )
    child_id = frontmatter(world.remote_show(children[0]))["id"]
    assert links(turtle_block(world.remote_show(PAPER_EXTRA)), HYP, str(child_id))


def test_parent_ignored_by_origins_config_is_missing(world, tmp_path, monkeypatch) -> None:
    """PAPER-130's only copy is under `shelved/`, which only origin's config ignores:
    the parent is 'missing' (the child is created, the shelved file untouched)."""
    seed_on_origin(world, monkeypatch, HYP)
    (world.a / PAPER_SHELVED).parent.mkdir(parents=True, exist_ok=True)
    git(world.a, "mv", PAPER, PAPER_SHELVED)
    git(world.a, "commit", "-q", "-m", "shelve PAPER-130")
    git(world.a, "push", "-q", "origin", "main")
    shelving = KanbanConfig(
        theme="hdd",
        paths=PathConfig(
            root="research/", scan_paths=HDD_SCAN,
            ignore=["**/archive/**", "**/templates/**", "**/shelved/**"],
        ),
    )
    b_change(world, {CONFIG: config_yaml(tmp_path, shelving)})
    assert "shelved" not in local_config(world)
    text = world.remote_show(PAPER_SHELVED)
    base = world.remote_sha()

    result = invoke(world, monkeypatch, list(HYP.argv))

    out = flat(result)
    assert world.remote_show(PAPER_SHELVED) == text, (
        "--push linked the parent origin's config ignores:\n"
        + git(world.remote, "diff", base, "main", "--", PAPER_SHELVED)
    )
    assert result.exit_code == 0, out
    assert MISSING_LINE in out, f"--push did not call PAPER-130 missing:\n{out}"


# --- 4. controls: no config on origin, and gates stay local --------------------------------


def test_control_no_origin_config_local_workflow_judges(world) -> None:
    """Origin has no config: A's own (untracked) workflow judges, and forbids."""
    b_change(world, removes=[CONFIG])
    local = world.a / WORKFLOW
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_text(_workflow_md(WORKFLOW_READY_NO_CLAIM, applies_to="expedition"))
    base = world.remote_sha()

    out = claim(world.a, A)

    assert out.kind == "refused", f"{out.kind}: {out.message}"
    assert "Illegal move" in out.message, out.message
    assert world.remote_sha() == base


def test_control_no_origin_config_local_theme_parses(world) -> None:
    """Origin has no config: A's own (untracked) theme override parses `berthed`."""
    b_change(world, {ITEM: item_text("berthed")}, removes=[CONFIG])
    override = world.a / THEME_OVERRIDE
    override.parent.mkdir(parents=True, exist_ok=True)
    override.write_text(nautical_with(status_mappings={"berthed": "ready"}))
    git(world.a, "checkout", "-q", "-b", "feat-865")
    base = world.remote_sha()

    out = claim(world.a, A)

    assert out.kind == "won", f"{out.kind}: {out.message}"
    assert_one_claim_commit(world, base, {"underway", "in_progress"})


def test_control_gate_only_in_local_config_still_refuses(world) -> None:
    """Gates stay local: a blocking gate only A's config has refuses the claim, though
    origin's config (which judges WIP, paths and now legality) has no gate."""
    (world.a / CONFIG).write_text(GATED_CONFIG)
    assert "gates" not in world.remote_show(CONFIG)
    base = world.remote_sha()

    out = claim(world.a, A)

    assert out.kind == "refused", (
        f"the local-only gate must still run: {out.kind}: {out.message}"
    )
    assert "Gate check failed" in out.message, out.message
    assert world.remote_sha() == base


def test_non_md_or_non_utf8_file_in_origins_workflows_is_skipped(world) -> None:
    """PR #916 review: a binary `.DS_Store` (and a non-UTF-8 `.md`) in origin's
    `.kanban/workflows/` is skipped as the working-tree reader skips it, never a
    refusal of every claim; origin's real workflow still judges."""
    b_change(world, {
        WORKFLOW: _workflow_md(WORKFLOW_READY_NO_CLAIM, applies_to="expedition"),
    })
    git(world.b, "fetch", "origin")
    git(world.b, "reset", "--hard", f"origin/{world.default}")
    folder = world.b / ".kanban" / "workflows"
    (folder / ".DS_Store").write_bytes(b"\x00\x05\x16\x07\xff\xfe binary")
    (folder / "latin1.md").write_bytes("caf\xe9\n".encode("latin-1"))
    git(world.b, "add", "-A")
    git(world.b, "commit", "-m", "junk in workflows")
    git(world.b, "push", "origin", f"HEAD:{world.default}")
    base = world.remote_sha()

    out = claim(world.a, A)

    # the junk is skipped; origin's expedition workflow still forbids the move
    assert out.kind == "refused", f"{out.kind}: {out.message}"
    assert "Illegal move" in out.message, out.message
    assert "UTF-8" not in out.message, out.message
    assert world.remote_sha() == base


def test_junk_only_in_origins_workflows_lets_the_claim_win(world) -> None:
    """Only a `.DS_Store` in origin's workflows folder: the default lifecycle
    allows ready -> in_progress, so the claim wins."""
    git(world.b, "fetch", "origin")
    git(world.b, "reset", "--hard", f"origin/{world.default}")
    folder = world.b / ".kanban" / "workflows"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / ".DS_Store").write_bytes(b"\x00\x05\x16\x07\xff\xfe binary")
    git(world.b, "add", "-A")
    git(world.b, "commit", "-m", "a .DS_Store")
    git(world.b, "push", "origin", f"HEAD:{world.default}")

    out = claim(world.a, A)

    assert out.kind == "won", f"{out.kind}: {out.message}"
