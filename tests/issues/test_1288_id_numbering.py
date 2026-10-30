"""Issue #1288 — `id_numbering`: one number sequence for every item type (`global`), or one
per type (`per_type`, the default and today's behaviour).

The issue's Proposal and "Done when" are the spec. Reuses the #585/#590/#634 real-git harness
(a bare origin, clone A under test, rival clone B that pushes with plain git), here with a
two-board config (development: nautical, research: hdd), so the tests drive the real CLI and the
real origin compare-and-swap path.

Pinned readings of the issue (the partner's; see the report):

- the key sits at the top level of `.kanban/config.yaml`, beside `boards:`;
- an unknown value is refused by `validate` (non-zero, naming the key) AND by `create`
  (non-zero, naming the key, no item written): it is never silently treated as `per_type`;
- in `global` mode the next number for ANY prefix is the max over every DASHED id space on
  every board (`EXP-`, `CHORE-`, `H-`, `M-`, `PAPER-`, `EXPR-`, `LIT-`, `IDEA-R-`, ...; driver
  ruling (a)) in all three sources, plus one; paper-scoped `H{paper}.{n}`
  ids neither take a global number nor raise the global max;
- an explicit id whose number a DIFFERENT prefix already holds is refused in `global` mode, and
  the refusal names the holder (e.g. `CHORE-003`); a free explicit number still lands, below
  the global max too (driver ruling (b));
- `next-id` says which mode is active (its output names `id_numbering` and the mode), and the
  `create` and `next-id` help texts mention `id_numbering`;
- the core `create` command has no `--id` option (#634), so explicit ids are driven through
  `hypothesis create --id`, `measure create --id` and `create_item_and_push(item_id=...)`.

Per-type behaviour by default is guarded by the existing suite; the per_type cases here are only
the controls each global case needs.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from click.testing import CliRunner

from tests.issues.test_585_create_push_loop import _configure, git, output_of
from tests.issues.test_590_next_id_and_hdd_ids import ALLOC, b_push, frontmatter_id
from tests.issues.test_603_push_failure_messages import HDD_DIRS
from tests.test_634_explicit_ids_on_base import TIMINGS, on_timing
from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import main
from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.models import WorkItemType
from yurtle_kanban.service import KanbanService

ROOT = Path(__file__).resolve().parents[2]
_ORIG_RUN = subprocess.run

DEV_DIRS = ["kanban-work/expeditions", "kanban-work/chores", "kanban-work/hazards"]
RES_DIRS = [f"research/{d}" for d in HDD_DIRS]
BOARD_ROOTS = ("kanban-work/", "research/")

BASE_CONFIG = """\
version: "2.0"
boards:
  - name: development
    preset: nautical
    path: kanban-work/
  - name: research
    preset: hdd
    path: research/
default_board: development
"""

# argv (without --push) for one create of each kind
CREATE = {
    "expedition": ["create", "expedition", "Exp item"],
    "chore": ["create", "chore", "Chore item"],
    "hazard": ["create", "hazard", "Haz item"],
    "hypothesis": ["hypothesis", "create", "Hyp item"],
    "measure": ["measure", "create", "Measure item", "--unit", "count", "--category", "coverage"],
    "experiment": ["experiment", "create", "--title", "Expr item"],
}
PREFIX = {"expedition": "EXP", "chore": "CHORE", "hazard": "HAZ", "hypothesis": "H",
          "measure": "M", "experiment": "EXPR"}
TYPE_DIR = {"EXP": "kanban-work/expeditions", "CHORE": "kanban-work/chores",
            "HAZ": "kanban-work/hazards", "H": "research/hypotheses", "M": "research/measures"}
TYPE_NAME = {"EXP": "expedition", "CHORE": "chore", "HAZ": "hazard", "H": "hypothesis",
             "M": "measure"}


def config_text(mode: str | None) -> str:
    return BASE_CONFIG + ("" if mode is None else f"id_numbering: {mode}\n")


def item_text(item_id: str, title: str = "Seeded") -> str:
    prefix = re.split(r"[-.]", item_id)[0]
    kind = TYPE_NAME.get(prefix, "expedition")
    return f'---\nid: {item_id}\ntitle: "{title}"\ntype: {kind}\nstatus: backlog\n---\n\n# {title}\n'


def seed_path(item_id: str) -> str:
    prefix = re.split(r"[-.]", item_id)[0]
    return f"{TYPE_DIR[prefix]}/{item_id}-seeded.md"


class Repo:
    """A bare origin (main) plus clones A (under test) and B (the rival), two boards."""

    def __init__(self, tmp_path: Path, mode: str | None) -> None:
        self.remote = tmp_path / "remote.git"
        self.a = tmp_path / "A"
        self.b = tmp_path / "B"
        git(tmp_path, "init", "--bare", "-b", "main", str(self.remote))
        git(tmp_path, "clone", str(self.remote), str(self.a))
        _configure(self.a)
        git(self.a, "checkout", "-B", "main")
        for d in DEV_DIRS + RES_DIRS:
            (self.a / d).mkdir(parents=True, exist_ok=True)
            (self.a / d / ".gitkeep").write_text("")
        (self.a / ".kanban").mkdir(exist_ok=True)
        (self.a / ".kanban" / "config.yaml").write_text(config_text(mode))
        git(self.a, "add", "-A")
        git(self.a, "commit", "-m", "seed")
        git(self.a, "push", "-u", "origin", "main")
        git(tmp_path, "clone", str(self.remote), str(self.b))
        _configure(self.b)

    # --- A ------------------------------------------------------------------------------
    def set_mode(self, mode: str | None) -> None:
        """Commit the config with `mode` on A's main and push it (origin reads it too)."""
        (self.a / ".kanban" / "config.yaml").write_text(config_text(mode))
        git(self.a, "add", ".kanban/config.yaml")
        git(self.a, "commit", "-m", f"id_numbering: {mode}")
        git(self.a, "pull", "-q", "--no-rebase", "origin", "main")
        git(self.a, "push", "origin", "main")
        config_mod._theme_cache.clear()

    def seed(self, files: dict[str, str], alloc: list[dict] | None = None) -> None:
        """Commit `files` (and append `alloc` records) on A's main and push them."""
        for rel, text in files.items():
            path = self.a / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        if alloc:
            lock = self.a / ALLOC
            records = json.loads(lock.read_text()) if lock.exists() else []
            lock.write_text(json.dumps(records + alloc, indent=2))
        git(self.a, "add", "-A")
        git(self.a, "commit", "-m", "seed items")
        git(self.a, "push", "origin", "main")

    def seed_ids(self, *ids: str) -> None:
        self.seed({seed_path(i): item_text(i) for i in ids})

    def cli(self, monkeypatch: pytest.MonkeyPatch, *argv: str):
        monkeypatch.chdir(self.a)
        config_mod._theme_cache.clear()
        return CliRunner().invoke(main, list(argv))

    def service(self) -> KanbanService:
        return KanbanService(KanbanConfig.load(self.a / ".kanban" / "config.yaml"), self.a)

    def local_ids(self) -> set[str]:
        found: set[str] = set()
        for top in BOARD_ROOTS:
            for md in (self.a / top).rglob("*.md"):
                fid = frontmatter_id(md.read_text())
                if fid:
                    found.add(fid)
        return found

    # --- origin -------------------------------------------------------------------------
    def remote_ids(self) -> set[str]:
        files = git(self.remote, "ls-tree", "-r", "--name-only", "main").split()
        found: set[str] = set()
        for p in files:
            if p.startswith(BOARD_ROOTS) and p.endswith(".md"):
                fid = frontmatter_id(git(self.remote, "show", f"main:{p}"))
                if fid:
                    found.add(fid)
        return found

    def remote_tip(self) -> str:
        return git(self.remote, "rev-parse", "main").strip()


def create(repo: Repo, monkeypatch, kind: str, push: bool = False) -> str:
    """Run one create of `kind` (pushed or local) and return the one id it made."""
    before = repo.remote_ids() if push else repo.local_ids()
    result = repo.cli(monkeypatch, *CREATE[kind], *(["--push"] if push else []))
    out = output_of(result)
    assert result.exit_code == 0, out
    after = repo.remote_ids() if push else repo.local_ids()
    new = after - before
    assert len(new) == 1, f"expected one new id, got {sorted(new)}: {out}"
    return new.pop()


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    config_mod._theme_cache.clear()
    yield
    config_mod._theme_cache.clear()


def make(tmp_path: Path, mode: str | None) -> Repo:
    return Repo(tmp_path, mode)


# --- 1. the config key ------------------------------------------------------------------------


@pytest.mark.parametrize("mode", [None, "per_type"])
def test_absent_or_per_type_numbers_each_type_on_its_own(tmp_path, monkeypatch, mode) -> None:
    """Control (today's behaviour): EXP-001 then CHORE-001 then H-001."""
    repo = make(tmp_path, mode)
    got = [create(repo, monkeypatch, k) for k in ("expedition", "chore", "hypothesis")]
    assert got == ["EXP-001", "CHORE-001", "H-001"], got


@pytest.mark.parametrize("mode", ["per_type", "global"])
def test_validate_accepts_both_known_values(tmp_path, monkeypatch, mode) -> None:
    """Control for the refusal below: a known value validates clean."""
    repo = make(tmp_path, mode)
    result = repo.cli(monkeypatch, "validate")
    assert result.exit_code == 0, output_of(result)


@pytest.mark.parametrize("bad", ["globl", "pertype"])
def test_validate_refuses_unknown_value(tmp_path, monkeypatch, bad) -> None:
    repo = make(tmp_path, bad)
    result = repo.cli(monkeypatch, "validate")
    out = output_of(result)
    assert result.exit_code != 0, f"validate accepted id_numbering: {bad}: {out}"
    assert "id_numbering" in out, out


@pytest.mark.parametrize("bad", ["globl", "pertype"])
def test_create_refuses_unknown_value_not_silently_per_type(tmp_path, monkeypatch, bad) -> None:
    repo = make(tmp_path, bad)
    before = repo.local_ids()
    result = repo.cli(monkeypatch, *CREATE["expedition"])
    out = output_of(result)
    assert result.exit_code != 0, f"create ran with id_numbering: {bad}: {out}"
    assert "id_numbering" in out, out
    assert repo.local_ids() == before, "an item was written under an unknown id_numbering"


# --- 2. global: consecutive numbers across types and boards ------------------------------------


def test_global_exp_chore_hypothesis_are_consecutive(tmp_path, monkeypatch) -> None:
    repo = make(tmp_path, "global")
    got = [create(repo, monkeypatch, k) for k in ("expedition", "chore", "hypothesis")]
    assert got == ["EXP-001", "CHORE-002", "H-003"], got


def test_global_sequence_continues_across_more_types(tmp_path, monkeypatch) -> None:
    repo = make(tmp_path, "global")
    kinds = ["hypothesis", "measure", "expedition", "hazard", "chore", "expedition"]
    got = [create(repo, monkeypatch, k) for k in kinds]
    assert got == ["H-001", "M-002", "EXP-003", "HAZ-004", "CHORE-005", "EXP-006"], got


# --- 3. global over existing per-type history, from each source --------------------------------


@pytest.mark.parametrize("kind", ["expedition", "chore", "hypothesis", "measure"])
def test_global_next_is_above_every_type(tmp_path, monkeypatch, kind) -> None:
    """The max (HAZ-005) is held by none of the types created, so no case is per-type."""
    repo = make(tmp_path, "global")
    repo.seed_ids("EXP-002", "CHORE-003", "HAZ-005")
    assert create(repo, monkeypatch, kind) == f"{PREFIX[kind]}-006"


@pytest.mark.parametrize("mode", [None, "per_type"])
def test_per_type_control_over_same_history(tmp_path, monkeypatch, mode) -> None:
    repo = make(tmp_path, mode)
    repo.seed_ids("EXP-002", "CHORE-003", "HAZ-005")
    got = [create(repo, monkeypatch, k) for k in ("chore", "expedition", "hypothesis")]
    assert got == ["CHORE-004", "EXP-003", "H-001"], got


def _seed_allocation_only(repo: Repo) -> None:
    repo.seed({}, [{"id": "HAZ-009", "prefix": "HAZ", "number": 9}])


def _seed_filename_only(repo: Repo) -> None:
    # a file with no frontmatter id: only its name carries the number
    repo.seed({"kanban-work/hazards/HAZ-009-notes.md": "# notes, no frontmatter\n"})


def _seed_frontmatter_only(repo: Repo) -> None:
    # the name carries no id: only the frontmatter does (#590 item 5)
    repo.seed({"research/measures/notes.md": item_text("M-009")})


@pytest.mark.parametrize(
    "seeder", [_seed_allocation_only, _seed_filename_only, _seed_frontmatter_only],
    ids=["allocations", "filename", "frontmatter"],
)
def test_global_counts_each_source_of_another_prefix(tmp_path, monkeypatch, seeder) -> None:
    repo = make(tmp_path, "global")
    seeder(repo)
    assert create(repo, monkeypatch, "chore") == "CHORE-010"


# --- 4. create --push / next-id on the origin path --------------------------------------------


def test_push_allocates_from_origins_global_max(tmp_path, monkeypatch) -> None:
    """A has not fetched B's CHORE-007; its pushed expedition still lands above it."""
    repo = make(tmp_path, "global")
    b_push(repo, {seed_path("CHORE-007"): item_text("CHORE-007", "Rival")},
           [{"id": "CHORE-007", "prefix": "CHORE", "number": 7}])
    assert "CHORE-007" not in repo.local_ids()  # precondition: A is stale
    assert create(repo, monkeypatch, "expedition", push=True) == "EXP-008"


@pytest.mark.parametrize("kind", ["expedition", "hypothesis", "measure"])
@pytest.mark.parametrize("timing", TIMINGS)
def test_rival_of_other_type_never_shares_the_number(tmp_path, monkeypatch, timing, kind) -> None:
    """B pushes CHORE-001 before A runs ("stale") or just before A's first push ("race")."""
    repo = make(tmp_path, "global")

    def rival() -> None:
        b_push(repo, {seed_path("CHORE-001"): item_text("CHORE-001", "Rival")},
               [{"id": "CHORE-001", "prefix": "CHORE", "number": 1}])

    on_timing(repo, monkeypatch, timing, rival)
    result = repo.cli(monkeypatch, *CREATE[kind], "--push")  # the rival may land mid-run
    assert result.exit_code == 0, output_of(result)
    assert repo.remote_ids() == {"CHORE-001", f"{PREFIX[kind]}-002"}, repo.remote_ids()


def test_next_id_on_origin_is_global_and_create_counts_its_record(tmp_path, monkeypatch) -> None:
    repo = make(tmp_path, "global")
    b_push(repo, {seed_path("CHORE-004"): item_text("CHORE-004", "Rival")})
    result = repo.cli(monkeypatch, "next-id", "EXP", "--json")
    out = output_of(result)
    assert result.exit_code == 0, out
    assert json.loads(out)["id"] == "EXP-005", out
    # the EXP-005 allocation record on origin raises the global max for every prefix
    assert create(repo, monkeypatch, "chore", push=True) == "CHORE-006"


# --- 5. round trip per_type -> global -> per_type ---------------------------------------------


@pytest.mark.parametrize("push", [False, True], ids=["local", "push"])
def test_round_trip_never_reuses_a_number_in_any_prefix(tmp_path, monkeypatch, push) -> None:
    repo = make(tmp_path, None)
    got = [create(repo, monkeypatch, k, push) for k in ("expedition", "expedition", "chore")]
    assert got == ["EXP-001", "EXP-002", "CHORE-001"], got

    repo.set_mode("global")
    step = [create(repo, monkeypatch, k, push) for k in ("chore", "expedition", "hypothesis")]
    assert step == ["CHORE-003", "EXP-004", "H-005"], step
    got += step

    repo.set_mode("per_type")
    step = [create(repo, monkeypatch, k, push) for k in ("chore", "expedition", "hypothesis",
                                                          "hazard")]
    assert step == ["CHORE-004", "EXP-005", "H-006", "HAZ-001"], step
    got += step

    by_prefix: dict[str, list[str]] = {}
    for i in got:
        by_prefix.setdefault(i.rsplit("-", 1)[0], []).append(i)
    for prefix, ids in by_prefix.items():
        assert len(ids) == len(set(ids)), f"{prefix} reused a number: {ids}"
    result = repo.cli(monkeypatch, "validate")
    assert result.exit_code == 0, output_of(result)


# --- 6. paper-scoped H{paper}.{n} keeps its own space -----------------------------------------


def _seed_paper_history(repo: Repo) -> None:
    repo.seed(
        {seed_path("EXP-005"): item_text("EXP-005"),
         "research/hypotheses/H130.9-seeded.md":
             '---\nid: H130.9\ntitle: "Seeded"\ntype: hypothesis\npaper: PAPER-130\n---\n'},
        [{"id": "H130.12", "prefix": "H130.", "number": 12}],
    )


@pytest.mark.parametrize("push", [False, True], ids=["local", "push"])
def test_paper_scoped_ids_unaffected_and_do_not_bump_global(tmp_path, monkeypatch, push) -> None:
    repo = make(tmp_path, "global")
    _seed_paper_history(repo)
    flag = ["--push"] if push else []

    def paper_hyp() -> str:
        before = repo.remote_ids() if push else repo.local_ids()
        result = repo.cli(monkeypatch, "hypothesis", "create", "Paper hyp", "--paper", "130",
                          *flag)
        assert result.exit_code == 0, output_of(result)
        new = (repo.remote_ids() if push else repo.local_ids()) - before
        assert len(new) == 1, new
        return new.pop()

    assert paper_hyp() == "H130.13"  # its own space: past H130.12, not the global 006
    # neither H130.13 nor H130.12 raised the global max: EXP-005 is the max, so 006
    assert create(repo, monkeypatch, "chore", push) == "CHORE-006"
    assert paper_hyp() == "H130.14"  # and the global CHORE-006 did not move the paper space


# --- 6b. ruled (driver): every dashed prefix on every board is in the global sequence ---------

OTHER_HDD = {  # id -> (path, type): the HDD prefixes no create above uses
    "PAPER-130": ("research/papers/PAPER-130-seeded.md", "paper"),
    "EXPR-130": ("research/experiments/EXPR-130-seeded.md", "experiment"),
    "LIT-130": ("research/literature/LIT-130-seeded.md", "literature"),
    "IDEA-R-130": ("research/ideas/IDEA-R-130-seeded.md", "idea"),
}


@pytest.mark.parametrize("held", sorted(OTHER_HDD))
def test_global_every_dashed_prefix_raises_the_max(tmp_path, monkeypatch, held) -> None:
    """Driver ruling (a): PAPER-, EXPR-, LIT-, IDEA-R- take part too; PAPER-130 → EXP-131."""
    repo = make(tmp_path, "global")
    path, kind = OTHER_HDD[held]
    repo.seed({path: f'---\nid: {held}\ntitle: "Seeded"\ntype: {kind}\nstatus: draft\n---\n'})
    assert create(repo, monkeypatch, "expedition") == "EXP-131"


@pytest.mark.parametrize("push", [False, True], ids=["local", "push"])
def test_global_experiment_takes_the_next_global_number(tmp_path, monkeypatch, push) -> None:
    """Driver ruling (a): an EXPR created in global mode is numbered in the one sequence."""
    repo = make(tmp_path, "global")
    got = [create(repo, monkeypatch, k, push) for k in ("expedition", "chore", "experiment")]
    assert got == ["EXP-001", "CHORE-002", "EXPR-003"], got


# --- 7. explicit ids in global mode -----------------------------------------------------------

EXPLICIT_HYP = ["hypothesis", "create", "Explicit hyp", "--id"]
EXPLICIT_M = ["measure", "create", "Explicit m", "--unit", "count", "--category", "coverage",
              "--id"]


@pytest.mark.parametrize("argv,item_id", [(EXPLICIT_HYP, "H-003"), (EXPLICIT_M, "M-003")],
                         ids=["hypothesis", "measure"])
@pytest.mark.parametrize("push", [False, True], ids=["local", "push"])
def test_global_explicit_id_clashing_with_other_prefix_refused(
    tmp_path, monkeypatch, argv, item_id, push
) -> None:
    repo = make(tmp_path, "global")
    repo.seed_ids("CHORE-003")
    tip = repo.remote_tip()
    result = repo.cli(monkeypatch, *argv, item_id, *(["--push"] if push else []))
    out = " ".join(output_of(result).split())
    assert result.exit_code != 0, f"{item_id} took CHORE-003's number: {out}"
    assert "CHORE-003" in out, out
    assert item_id not in repo.local_ids()
    assert repo.remote_tip() == tip, "something was pushed"


def test_global_explicit_service_id_clashing_refused(tmp_path, monkeypatch) -> None:
    repo = make(tmp_path, "global")
    repo.seed_ids("CHORE-003")
    tip = repo.remote_tip()
    monkeypatch.chdir(repo.a)
    result = repo.service().create_item_and_push(
        WorkItemType.EXPEDITION, "Explicit exp", item_id="EXP-003"
    )
    assert result["success"] is False, result
    assert "CHORE-003" in result["message"], result["message"]
    assert repo.remote_tip() == tip
    assert "EXP-003" not in repo.remote_ids()


@pytest.mark.parametrize("mode", [None, "per_type"])
@pytest.mark.parametrize("push", [False, True], ids=["local", "push"])
def test_per_type_explicit_id_sharing_a_number_is_allowed(tmp_path, monkeypatch, mode, push):
    """Control (today's behaviour): per_type lets H-003 sit beside CHORE-003."""
    repo = make(tmp_path, mode)
    repo.seed_ids("CHORE-003")
    result = repo.cli(monkeypatch, *EXPLICIT_HYP, "H-003", *(["--push"] if push else []))
    assert result.exit_code == 0, output_of(result)
    assert "H-003" in (repo.remote_ids() if push else repo.local_ids())


@pytest.mark.parametrize("item_id", ["H-010", "H130.3"], ids=["free-dashed", "paper-scoped"])
def test_global_explicit_id_not_clashing_lands(tmp_path, monkeypatch, item_id) -> None:
    """Control: a number no other prefix holds, or a paper-scoped id, still lands."""
    repo = make(tmp_path, "global")
    repo.seed_ids("CHORE-003")
    result = repo.cli(monkeypatch, *EXPLICIT_HYP, item_id, "--push")
    assert result.exit_code == 0, output_of(result)
    assert item_id in repo.remote_ids()


@pytest.mark.parametrize("push", [False, True], ids=["local", "push"])
def test_global_free_explicit_id_below_the_max_is_allowed(tmp_path, monkeypatch, push) -> None:
    """Driver ruling (b): refused only when another prefix holds the number; H-004 is free
    while CHORE-009 is the global max, so it is created."""
    repo = make(tmp_path, "global")
    repo.seed_ids("CHORE-009")
    result = repo.cli(monkeypatch, *EXPLICIT_HYP, "H-004", *(["--push"] if push else []))
    assert result.exit_code == 0, output_of(result)
    assert "H-004" in (repo.remote_ids() if push else repo.local_ids())


# --- 8. visibility: next-id and help ----------------------------------------------------------


def test_next_id_global_gives_the_global_number(tmp_path, monkeypatch) -> None:
    repo = make(tmp_path, "global")
    repo.seed_ids("CHORE-004")
    result = repo.cli(monkeypatch, "next-id", "EXP", "--no-sync", "--no-commit", "--json")
    out = output_of(result)
    assert result.exit_code == 0, out
    assert json.loads(out)["id"] == "EXP-005", out


@pytest.mark.parametrize("mode,word", [("global", "global"), ("per_type", "per_type"),
                                       (None, "per_type")])
def test_next_id_says_which_mode_is_active(tmp_path, monkeypatch, mode, word) -> None:
    repo = make(tmp_path, mode)
    result = repo.cli(monkeypatch, "next-id", "EXP", "--no-sync", "--no-commit")
    out = output_of(result)
    assert result.exit_code == 0, out
    assert "id_numbering" in out and word in out, out
    other = "per_type" if word == "global" else "global"
    assert other not in out, f"next-id names the inactive mode {other}: {out}"


@pytest.mark.parametrize("command", ["create", "next-id"])
def test_help_mentions_id_numbering(command) -> None:
    result = CliRunner().invoke(main, [command, "--help"])
    assert result.exit_code == 0, result.output
    assert "id_numbering" in result.output, result.output


# --- 9. padding -------------------------------------------------------------------------------


def test_global_padding_at_least_three_digits_and_never_truncates(tmp_path, monkeypatch) -> None:
    repo = make(tmp_path, "global")
    assert create(repo, monkeypatch, "hypothesis") == "H-001"
    assert create(repo, monkeypatch, "chore") == "CHORE-002"
    repo.seed_ids("HAZ-1203")
    assert create(repo, monkeypatch, "expedition") == "EXP-1204"


# --- 10. docs ---------------------------------------------------------------------------------


def test_readme_or_reference_documents_id_numbering() -> None:
    docs = [ROOT / "README.md", *sorted((ROOT / "docs").rglob("*.md"))]
    documenting = [
        p for p in docs
        if "id_numbering" in (text := p.read_text(encoding="utf-8"))
        and "per_type" in text and "global" in text
    ]
    assert documenting, "no README/docs page documents id_numbering with per_type and global"


def test_changelog_fragment_exists() -> None:
    """The #1288 changelog entry: its fragment, or, once a release has assembled the
    fragments away, its bullet in CHANGELOG.md (#1188, #1224)."""
    fragment = ROOT / "changelog.d" / "1288.md"
    if fragment.exists():
        entry = fragment.read_text(encoding="utf-8")
    else:
        bullets = re.split(r"\n(?=- )", (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"))
        found = [b for b in bullets if "#1288" in b]
        assert found, "the #1288 entry is in neither changelog.d/1288.md nor CHANGELOG.md"
        entry = found[0]
    assert "id_numbering" in entry, entry
