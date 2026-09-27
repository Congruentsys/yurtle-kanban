"""Expedition #576 — `yurtle-kanban update ID`: dependencies (part 2 of 2).

Spec: the issue body's Expected and Acceptance sections. This file covers:

- Acceptance 3: `--depends-on ""` writes `depends_on: []`; `--add-dep exp-3` stores
  `EXP-3`; `--depends-on` / `--related` replace, `--add-dep` / `--rm-dep` repeat.
- Acceptance 4 / §5: a cycle introduced by this edit is refused (exit 1, file
  unchanged) with a message naming it (`cycle: EXP-1 → EXP-3 → EXP-1`). An unrelated
  edit on an item that is already in a hand-made cycle succeeds.
- Acceptance 5 / §4: cycles are found across boards (a dev-board EXP and an hdd
  `H1.1`), by the CLI and by the service helpers `dependency_graph()` /
  `find_cycle(start)`.
- Acceptance 6 / §5: unknown target refused unless `--allow-unknown`; self-dependency
  refused; a target ID duplicated across boards refused; an unknown ID being updated
  refused; a bad title or priority refused. Every refusal writes and commits nothing.
- Acceptance 7 / §6: the commit holds only the item file (#584); its message names the
  fields and edges (`Update EXP-5: depends_on +EXP-3 -EXP-2, priority high`);
  `--no-commit` makes none.
- Acceptance 8: a no-op writes nothing, makes no commit, prints `no changes`, exits 0.
- Acceptance 9 / §7: `validate` reports a hand-made cycle (also cross-board) and a
  dangling `depends_on` target.

Refusals are checked as exit 1 with no traceback: the refusal type (`InputRefused`,
#666) is not on main yet, so no test imports it.

Fixture: a two-board repo (`development` nautical under `work/`, `research` hdd under
`research/`), items hand-written and committed:
EXP-1 [], EXP-2 [], EXP-3 [EXP-1], EXP-4 [EXP-3], EXP-5 [EXP-2], H1.1 [EXP-1].
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner, Result

from yurtle_kanban import config as config_mod
from yurtle_kanban.cli import main
from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.service import KanbanService

CONFIG = """\
version: "2.0"
boards:
  - name: development
    preset: nautical
    path: "work/"
  - name: research
    preset: hdd
    path: "research/"
default_board: development
"""

GRAPH: dict[str, list[str]] = {
    "EXP-1": [],
    "EXP-2": [],
    "EXP-3": ["EXP-1"],
    "EXP-4": ["EXP-3"],
    "EXP-5": ["EXP-2"],
    "H1.1": ["EXP-1"],
}


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=True
    ).stdout


def _flat(text: str) -> str:
    """Output with rich's line wrapping undone."""
    return " ".join(text.split())


def _item_text(item_id: str, deps: list[str], *, related: list[str] | None = None) -> str:
    hyp = item_id.startswith("H")
    rel = f"related: [{', '.join(related)}]\n" if related is not None else ""
    return (
        f"---\nid: {item_id}\ntitle: \"Item {item_id}\"\n"
        f"type: {'hypothesis' if hyp else 'expedition'}\n"
        f"status: {'draft' if hyp else 'ready'}\npriority: medium\n"
        f"depends_on: [{', '.join(deps)}]\n{rel}---\n\n# Item {item_id}\n\nSome body.\n"
    )


class Repo:
    def __init__(self, root: Path):
        self.root = root

    def path(self, item_id: str) -> Path:
        if item_id.startswith("H"):
            return self.root / "research" / "hypotheses" / f"{item_id}-item.md"
        return self.root / "work" / "expeditions" / f"{item_id}-item.md"

    def rel(self, item_id: str) -> str:
        return self.path(item_id).relative_to(self.root).as_posix()

    def write(self, item_id: str, deps: list[str], **kw: object) -> None:
        p = self.path(item_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_item_text(item_id, deps, **kw), encoding="utf-8")  # type: ignore[arg-type]

    def commit(self, message: str) -> None:
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-m", message)

    def head(self) -> str:
        return _git(self.root, "rev-parse", "HEAD").strip()

    def snapshot(self) -> dict[Path, bytes]:
        return {p: p.read_bytes() for p in self.root.rglob("*.md") if ".git" not in p.parts}

    def deps(self, item_id: str) -> list[str]:
        return self.fm(item_id).get("depends_on")  # type: ignore[return-value]

    def fm(self, item_id: str) -> dict[str, object]:
        text = self.path(item_id).read_text(encoding="utf-8")
        return yaml.safe_load(text.split("---\n")[1])

    def service(self) -> KanbanService:
        return KanbanService(KanbanConfig.load(self.root / ".kanban" / "config.yaml"), self.root)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Repo:
    root = tmp_path / "repo"
    (root / ".kanban").mkdir(parents=True)
    (root / ".kanban" / "config.yaml").write_text(CONFIG)
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "t@t.com")
    _git(root, "config", "user.name", "T")
    r = Repo(root)
    for item_id, deps in GRAPH.items():
        r.write(item_id, deps)
    r.commit("fixture")
    config_mod._theme_cache.clear()
    monkeypatch.chdir(root)
    monkeypatch.setenv("YURTLE_AGENT", "tester")
    # sanity: both boards are scanned, H1.1 included
    ids = {i.id for i in r.service().get_items()}
    assert ids == set(GRAPH), ids
    return r


def _hand_cycle(repo: Repo) -> None:
    """EXP-6 ↔ EXP-7, written by hand (not through `update`)."""
    repo.write("EXP-6", ["EXP-7"])
    repo.write("EXP-7", ["EXP-6"])
    repo.commit("hand-made cycle")


def invoke(args: list[str], input: bytes | str | None = None) -> Result:
    return CliRunner().invoke(main, args, input=input)


def _ok(args: list[str]) -> Result:
    result = invoke(args)
    assert result.exit_code == 0, (
        f"{args} exited {result.exit_code}: {result.exception!r}\n{result.output}"
    )
    return result


def _refused(repo: Repo, args: list[str], *needles: str) -> str:
    """Exit 1, no traceback, nothing written, nothing committed; the (flattened)
    output names each of `needles`."""
    before, head = repo.snapshot(), repo.head()
    result = invoke(args)
    out = _flat(result.output)
    assert result.exit_code == 1, f"{args} exited {result.exit_code}:\n{result.output}"
    assert isinstance(result.exception, SystemExit), repr(result.exception)
    assert "Traceback" not in result.output, result.output
    assert repo.snapshot() == before, f"{args} wrote a file"
    assert repo.head() == head, f"{args} committed"
    assert not _git(repo.root, "status", "--porcelain").strip(), "working tree dirtied"
    for needle in needles:
        assert needle in out, f"{needle!r} not in output: {out}"
    return out


# ---------------------------------------------------------------------------
# Acceptance 3: writing dependencies and related
# ---------------------------------------------------------------------------


def test_add_dep_uppercases(repo: Repo) -> None:
    _ok(["update", "EXP-5", "--add-dep", "exp-3"])
    assert repo.deps("EXP-5") == ["EXP-2", "EXP-3"]


def test_add_dep_is_repeatable(repo: Repo) -> None:
    _ok(["update", "EXP-2", "--add-dep", "EXP-1", "--add-dep", "exp-3"])
    assert repo.deps("EXP-2") == ["EXP-1", "EXP-3"]


def test_add_dep_existing_target_is_not_duplicated(repo: Repo) -> None:
    _ok(["update", "EXP-5", "--add-dep", "EXP-3", "--add-dep", "exp-3"])
    assert repo.deps("EXP-5") == ["EXP-2", "EXP-3"]


def test_depends_on_empty_clears(repo: Repo) -> None:
    _ok(["update", "EXP-3", "--depends-on", ""])
    text = repo.path("EXP-3").read_text(encoding="utf-8")
    assert re.search(r"^depends_on: \[\]$", text, re.M), text
    assert repo.deps("EXP-3") == []


def test_depends_on_replaces(repo: Repo) -> None:
    _ok(["update", "EXP-5", "--depends-on", "exp-1,EXP-3"])
    assert repo.deps("EXP-5") == ["EXP-1", "EXP-3"]


def test_rm_dep(repo: Repo) -> None:
    _ok(["update", "EXP-5", "--rm-dep", "exp-2"])
    assert repo.deps("EXP-5") == []


def test_related_replaces_and_clears(repo: Repo) -> None:
    _ok(["update", "EXP-5", "--related", "EXP-1,EXP-3"])
    assert repo.fm("EXP-5").get("related") == ["EXP-1", "EXP-3"]
    assert repo.deps("EXP-5") == ["EXP-2"], "--related touched depends_on"
    _ok(["update", "EXP-5", "--related", ""])
    assert repo.fm("EXP-5").get("related") in ([], None)


def test_parsed_item_sees_new_dependency(repo: Repo) -> None:
    _ok(["update", "EXP-5", "--add-dep", "EXP-3"])
    item = repo.service().get_item("EXP-5")
    assert item is not None and item.depends_on == ["EXP-2", "EXP-3"], item


# ---------------------------------------------------------------------------
# Acceptance 4 / §5: cycles introduced by this edit
# ---------------------------------------------------------------------------


def test_introduced_cycle_is_refused_and_named(repo: Repo) -> None:
    _refused(repo, ["update", "EXP-1", "--add-dep", "EXP-3"], "cycle: EXP-1 → EXP-3 → EXP-1")


def test_longer_introduced_cycle_names_the_whole_path(repo: Repo) -> None:
    _refused(
        repo,
        ["update", "exp-1", "--add-dep", "exp-4"],
        "cycle: EXP-1 → EXP-4 → EXP-3 → EXP-1",
    )


def test_cycle_through_depends_on_replace_is_refused(repo: Repo) -> None:
    _refused(repo, ["update", "EXP-2", "--depends-on", "EXP-5"], "cycle: EXP-2 → EXP-5 → EXP-2")


def test_cycle_refusal_in_a_multi_flag_edit_writes_nothing(repo: Repo) -> None:
    _refused(repo, ["update", "EXP-1", "--title", "Renamed", "--add-dep", "EXP-3"], "cycle:")


def test_title_on_already_cyclic_item_succeeds(repo: Repo) -> None:
    _hand_cycle(repo)
    _ok(["update", "EXP-6", "--title", "Renamed"])
    assert repo.fm("EXP-6")["title"] == "Renamed"
    assert repo.deps("EXP-6") == ["EXP-7"]


def test_unrelated_dep_on_already_cyclic_item_succeeds(repo: Repo) -> None:
    """Only cycles through the NEW targets are refused: EXP-1 does not lead back to
    EXP-6, so the existing EXP-6 ↔ EXP-7 cycle does not block the edit."""
    _hand_cycle(repo)
    _ok(["update", "EXP-6", "--add-dep", "EXP-1"])
    assert repo.deps("EXP-6") == ["EXP-7", "EXP-1"]


# ---------------------------------------------------------------------------
# Acceptance 5 / §4: across boards
# ---------------------------------------------------------------------------


def test_cross_board_cycle_is_refused(repo: Repo) -> None:
    """H1.1 (hdd board) depends on EXP-1 (dev board): EXP-1 → H1.1 closes a cycle."""
    _refused(repo, ["update", "EXP-1", "--add-dep", "h1.1"], "cycle: EXP-1 → H1.1 → EXP-1")


def test_cross_board_cycle_from_the_hdd_side_is_refused(repo: Repo) -> None:
    repo.write("EXP-2", ["H1.1"])
    repo.commit("EXP-2 depends on H1.1")
    _refused(repo, ["update", "h1.1", "--add-dep", "exp-2"], "cycle: H1.1 → EXP-2 → H1.1")


def test_cross_board_dependency_is_written(repo: Repo) -> None:
    _ok(["update", "EXP-2", "--add-dep", "h1.1"])
    assert repo.deps("EXP-2") == ["H1.1"]


def test_service_graph_helpers_span_boards(repo: Repo) -> None:
    """§4: `dependency_graph()` over all boards and `find_cycle(start)`."""
    repo.write("EXP-1", ["H1.1"])  # hand-made cross-board cycle
    repo.commit("cross-board cycle")
    svc = repo.service()
    graph = svc.dependency_graph()
    assert set(graph["EXP-1"]) == {"H1.1"}, graph
    assert set(graph["H1.1"]) == {"EXP-1"}, graph
    assert set(graph["EXP-5"]) == {"EXP-2"}, graph
    cycle = svc.find_cycle("EXP-1")
    assert cycle == ["EXP-1", "H1.1", "EXP-1"], cycle
    assert svc.find_cycle("EXP-5") is None


# ---------------------------------------------------------------------------
# Acceptance 6 / §5: other refusals
# ---------------------------------------------------------------------------


def test_unknown_target_is_refused(repo: Repo) -> None:
    _refused(repo, ["update", "EXP-5", "--add-dep", "exp-404"], "EXP-404")


def test_unknown_target_in_replace_is_refused(repo: Repo) -> None:
    _refused(repo, ["update", "EXP-5", "--depends-on", "EXP-1,EXP-404"], "EXP-404")


def test_unknown_target_with_allow_unknown_is_written(repo: Repo) -> None:
    _ok(["update", "EXP-5", "--add-dep", "exp-404", "--allow-unknown"])
    assert repo.deps("EXP-5") == ["EXP-2", "EXP-404"]


def test_self_dependency_is_refused(repo: Repo) -> None:
    _refused(repo, ["update", "EXP-2", "--add-dep", "exp-2"], "EXP-2")


def test_self_dependency_by_replace_is_refused(repo: Repo) -> None:
    _refused(repo, ["update", "EXP-2", "--depends-on", "EXP-1,EXP-2"], "EXP-2")


def test_self_dependency_refused_even_with_allow_unknown(repo: Repo) -> None:
    _refused(repo, ["update", "EXP-2", "--add-dep", "EXP-2", "--allow-unknown"], "EXP-2")


def test_duplicated_target_id_is_refused(repo: Repo) -> None:
    """EXP-9 exists on both boards: which one the edge means is ambiguous."""
    dup_a = repo.root / "work" / "expeditions" / "EXP-9-a.md"
    dup_b = repo.root / "research" / "ideas" / "EXP-9-b.md"
    dup_b.parent.mkdir(parents=True, exist_ok=True)
    dup_a.write_text(_item_text("EXP-9", []), encoding="utf-8")
    dup_b.write_text(
        _item_text("EXP-9", []).replace("type: expedition", "type: idea"), encoding="utf-8"
    )
    repo.commit("duplicate EXP-9")
    _refused(repo, ["update", "EXP-2", "--add-dep", "exp-9"], "EXP-9")


def test_unknown_item_is_refused(repo: Repo) -> None:
    _refused(repo, ["update", "EXP-404", "--title", "x"], "EXP-404")


@pytest.mark.parametrize("title", ["", "   ", "two\nlines"], ids=["empty", "blank", "newline"])
def test_bad_title_is_refused(repo: Repo, title: str) -> None:
    _refused(repo, ["update", "EXP-2", "--title", title])


def test_bad_priority_is_refused(repo: Repo) -> None:
    _refused(repo, ["update", "EXP-2", "--priority", "whenever"], "whenever")


def test_no_assign_blocks_or_superseded_options(repo: Repo) -> None:
    """Out of scope by decision: assignment, `blocks`, `superseded_by` (`--push`
    arrives with #574, so it is not pinned here)."""
    _ok(["update", "EXP-2", "--priority", "low"])  # the command exists
    for flag in ("--assign", "--unassign", "--blocks", "--superseded-by"):
        result = invoke(["update", "EXP-5", flag, "X"])
        assert result.exit_code == 2 and "No such option" in result.output, (flag, result.output)


# ---------------------------------------------------------------------------
# Acceptance 7 / §6: the commit
# ---------------------------------------------------------------------------


def _subject(repo: Repo) -> str:
    return _git(repo.root, "log", "-1", "--format=%s").strip()


def _committed(repo: Repo, base: str) -> set[str]:
    out = _git(repo.root, "log", "--name-only", "--format=", f"{base}..HEAD")
    return {line for line in out.splitlines() if line.strip()}


def test_commits_only_the_item_file(repo: Repo) -> None:
    (repo.root / "notes.txt").write_text("unrelated\n")
    _git(repo.root, "add", "notes.txt")
    base = repo.head()
    _ok(["update", "EXP-5", "--add-dep", "EXP-3"])
    assert repo.head() != base, "no commit made"
    assert _committed(repo, base) == {repo.rel("EXP-5")}
    assert _git(repo.root, "diff", "--cached", "--name-only").split() == ["notes.txt"]


def test_commit_message_names_fields_and_edges(repo: Repo) -> None:
    _ok(["update", "EXP-5", "--add-dep", "EXP-3", "--rm-dep", "EXP-2", "--priority", "high"])
    subject = _subject(repo)
    assert subject.startswith("Update EXP-5: "), subject
    assert "depends_on +EXP-3 -EXP-2" in subject, subject
    assert "priority high" in subject, subject


def test_commit_message_title(repo: Repo) -> None:
    _ok(["update", "EXP-5", "--title", "Renamed"])
    subject = _subject(repo)
    assert subject.startswith("Update EXP-5: "), subject
    assert "title" in subject, subject


def test_no_commit_writes_but_does_not_commit(repo: Repo) -> None:
    head = repo.head()
    _ok(["update", "EXP-5", "--add-dep", "EXP-3", "--no-commit"])
    assert repo.head() == head
    assert repo.deps("EXP-5") == ["EXP-2", "EXP-3"]
    assert _git(repo.root, "status", "--porcelain").split() == ["M", repo.rel("EXP-5")]


# ---------------------------------------------------------------------------
# Acceptance 8: no-op
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        pytest.param(["--priority", "medium"], id="priority"),
        pytest.param(["--add-dep", "exp-2"], id="add-dep"),
        pytest.param(["--depends-on", "EXP-2"], id="depends-on"),
        pytest.param(["--rm-dep", "EXP-1"], id="rm-absent"),
        pytest.param(["--title", "Item EXP-5"], id="title"),
    ],
)
def test_noop_update(repo: Repo, argv: list[str]) -> None:
    before, head = repo.snapshot(), repo.head()
    result = _ok(["update", "EXP-5", *argv])
    assert "no changes" in result.output, result.output
    assert repo.snapshot() == before
    assert repo.head() == head


# ---------------------------------------------------------------------------
# Acceptance 9 / §7: validate
# ---------------------------------------------------------------------------


def test_validate_clean_graph_passes(repo: Repo) -> None:
    """Control: a cross-board dependency (H1.1 → EXP-1) is not an issue."""
    result = _ok(["validate"])
    assert "cycle" not in result.output.lower(), result.output


def _broken(repo: Repo) -> None:
    _hand_cycle(repo)
    repo.write("EXP-2", ["EXP-404"])  # dangling
    repo.commit("dangling dependency")


def test_validate_reports_cycle_and_dangling(repo: Repo) -> None:
    _broken(repo)
    result = invoke(["validate"])
    out = _flat(result.output)
    assert result.exit_code == 1, result.output
    assert "cycle" in out.lower(), out
    assert "EXP-6" in out and "EXP-7" in out, out
    assert "EXP-404" in out and "EXP-2" in out, out


def test_validate_json_reports_cycle_and_dangling(repo: Repo) -> None:
    _broken(repo)
    result = invoke(["validate", "--json"])
    assert result.exit_code == 1, result.output
    data = json.loads(result.output)
    assert data["valid"] is False
    issues = [json.dumps(i) for i in data["issues"]]
    assert any("cycle" in i.lower() and "EXP-6" in i and "EXP-7" in i for i in issues), issues
    assert any("EXP-404" in i and "EXP-2" in i for i in issues), issues


def test_validate_reports_cross_board_cycle(repo: Repo) -> None:
    repo.write("EXP-1", ["H1.1"])
    repo.commit("cross-board cycle")
    result = invoke(["validate"])
    out = _flat(result.output)
    assert result.exit_code == 1, result.output
    assert "cycle" in out.lower() and "EXP-1" in out and "H1.1" in out, out
