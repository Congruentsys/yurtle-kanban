"""Issue #296 — every path that builds an RDF graph from raw item text is guarded.

Follow-up to #277 (review of PR #290). Decided behaviour:
- `hdd backfill` (with and without --dry-run) and the parent inverse-reference path
  (`update_parent_turtle_block`, reached by `hypothesis create --paper N`) re-parse
  the raw file text; a too-large field is blanked there too, like #277's scan path,
  so neither hangs.
- A kept field that aliases an anchor defined inside a dropped field is blanked in
  the graph text too, so the graph still parses (not empty) instead of failing on
  an undefined alias. The field itself stays in the item's metadata.

Every call runs in a subprocess with a timeout, so a hang fails, not blocks.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from yurtle_kanban.config import KanbanConfig, PathConfig
from yurtle_kanban.models import WorkItemType
from yurtle_kanban.service import KanbanService

SRC = Path(__file__).resolve().parents[2] / "src"
TIMEOUT = 20  # a hang fails the test; the bound on a passing run is FAST
FAST = 10.0
MAX_OUT = 1_000_000  # bytes of stdout

HDD_DIRS = ("ideas", "literature", "papers", "hypotheses", "experiments", "measures")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout


def _init_git(repo: Path) -> None:
    for args in (
        ["init", "-q", "-b", "main"],
        ["config", "user.email", "test@test.com"],
        ["config", "user.name", "Test"],
        ["config", "commit.gpgsign", "false"],
    ):
        _git(repo, *args)


def _commit_all(repo: Path, message: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--allow-empty", "-m", message)


def _env() -> dict[str, str]:
    return {**os.environ, "PYTHONPATH": str(SRC), "PYTHONDONTWRITEBYTECODE": "1"}


def _run(repo: Path, argv: list[str], label: str) -> tuple[subprocess.CompletedProcess[str], float]:
    start = time.monotonic()
    try:
        proc = subprocess.run(
            argv, cwd=repo, env=_env(), capture_output=True, text=True, timeout=TIMEOUT
        )
    except subprocess.TimeoutExpired:
        pytest.fail(f"`{label}` hung for {TIMEOUT}s")
    return proc, time.monotonic() - start


def _cli(repo: Path, *args: str) -> tuple[subprocess.CompletedProcess[str], float]:
    argv = [sys.executable, "-c", "from yurtle_kanban.cli import main; main()", *args]
    return _run(repo, argv, " ".join(args))


def _py(repo: Path, code: str, label: str) -> tuple[subprocess.CompletedProcess[str], float]:
    return _run(repo, [sys.executable, "-c", code], label)


def _ok(proc: subprocess.CompletedProcess[str], elapsed: float) -> None:
    assert proc.returncode == 0, proc.stdout[:2000] + proc.stderr[:2000]
    assert "Traceback" not in proc.stderr, proc.stderr[:4000]
    assert elapsed < FAST, f"took {elapsed:.1f}s"
    assert len(proc.stdout.encode()) < MAX_OUT, f"stdout is {len(proc.stdout)} chars"


def _laughs(levels: int = 9, width: int = 10) -> str:
    """One flow value, all anchors defined inside it: level k is a list of `width`
    copies of level k-1, so it expands to width**levels leaves (1e9 by default)
    while the text is a few hundred bytes. `&a0` is a 10-item list."""
    node = "&a0 [" + ", ".join(["lol"] * width) + "]"
    for k in range(1, levels):
        node = f"&a{k} [" + node + f", *a{k - 1}" * (width - 1) + "]"
    return node


_SERVICE = (
    "import json\n"
    "from pathlib import Path\n"
    "from yurtle_kanban.config import KanbanConfig\n"
    "from yurtle_kanban.service import KanbanService\n"
    "r = Path.cwd()\n"
    "s = KanbanService(KanbanConfig.load(r / '.kanban' / 'config.yaml'), r)\n"
)

# ---------------------------------------------------------------------------
# HDD board
# ---------------------------------------------------------------------------

PAPER_TURTLE = (
    "@prefix paper: <https://nusy.dev/paper/> .\n"
    "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .\n"
    "\n"
    "<#PAPER-130> a paper:Paper ;\n"
    '    rdfs:label "Brain Architecture" .\n'
)


@pytest.fixture
def hdd(tmp_path: Path) -> Path:
    """An HDD-theme board (research/*) with nothing in it yet."""
    _init_git(tmp_path)
    (tmp_path / ".kanban").mkdir()
    for d in HDD_DIRS:
        (tmp_path / "research" / d).mkdir(parents=True)
    KanbanConfig(
        theme="hdd",
        paths=PathConfig(
            root="research/", scan_paths=[f"research/{d}/" for d in HDD_DIRS]
        ),
    ).save(tmp_path / ".kanban" / "config.yaml")
    return tmp_path


def _hyp_file(repo: Path, tags: str) -> Path:
    """H130.1, a hypothesis with no turtle block (so backfill has work to do)."""
    path = repo / "research" / "hypotheses" / "H130.1-Accuracy.md"
    path.write_text(
        "---\n"
        "id: H130.1\n"
        'title: "Accuracy improves"\n'
        "type: hypothesis\n"
        "status: draft\n"
        "created: 2026-01-01\n"
        "paper: 130\n"
        f"tags: {tags}\n"
        "---\n"
        "\n"
        "# H130.1: Accuracy improves\n",
        encoding="utf-8",
    )
    _commit_all(repo, "hyp")
    return path


def _paper_file(repo: Path, tags: str) -> Path:
    """PAPER-130 with a turtle block (the inverse-reference target)."""
    path = repo / "research" / "papers" / "PAPER-130-Brain-Architecture.md"
    path.write_text(
        "---\n"
        "id: PAPER-130\n"
        'title: "Brain Architecture"\n'
        "type: paper\n"
        "status: draft\n"
        "created: 2026-01-01\n"
        f"tags: {tags}\n"
        "---\n"
        "\n"
        "# PAPER-130: Brain Architecture\n"
        "\n"
        "```turtle\n" + PAPER_TURTLE + "```\n"
        "\n"
        "## Content\n",
        encoding="utf-8",
    )
    _commit_all(repo, "paper")
    return path


class TestBackfill:
    @pytest.mark.parametrize("dry_run", [True, False], ids=["dry-run", "write"])
    def test_huge_tags_backfill_finishes(self, hdd: Path, dry_run: bool) -> None:
        path = _hyp_file(hdd, _laughs())
        args = ["hdd", "backfill"] + (["--dry-run"] if dry_run else [])
        proc, elapsed = _cli(hdd, *args)
        _ok(proc, elapsed)
        assert "H130.1" in proc.stdout, proc.stdout[:2000] + proc.stderr[:2000]
        text = path.read_text(encoding="utf-8")
        if dry_run:
            assert "```turtle" not in text, text[:2000]
        else:
            assert "```turtle" in text, text[:2000]
            assert "lol" not in text.split("---", 2)[2], text[:2000]

    @pytest.mark.parametrize("dry_run", [True, False], ids=["dry-run", "write"])
    def test_plain_tags_backfill_control(self, hdd: Path, dry_run: bool) -> None:
        path = _hyp_file(hdd, "[a, b]")
        args = ["hdd", "backfill"] + (["--dry-run"] if dry_run else [])
        proc, elapsed = _cli(hdd, *args)
        _ok(proc, elapsed)
        assert "H130.1" in proc.stdout, proc.stdout[:2000] + proc.stderr[:2000]
        assert ("```turtle" in path.read_text(encoding="utf-8")) is not dry_run


class TestParentInverseReference:
    def test_service_update_parent_huge_tags(self, hdd: Path) -> None:
        path = _paper_file(hdd, _laughs())
        code = _SERVICE + (
            "ok = s.update_parent_turtle_block('PAPER-130', 'hypothesis', 'H130.1')\n"
            "print(json.dumps({'ok': ok}))\n"
        )
        proc, elapsed = _py(hdd, code, "update_parent_turtle_block")
        _ok(proc, elapsed)
        assert json.loads(proc.stdout.strip().splitlines()[-1]) == {"ok": True}
        assert "hasHypothesis" in path.read_text(encoding="utf-8")

    def test_cli_hypothesis_create_links_huge_parent(self, hdd: Path) -> None:
        path = _paper_file(hdd, _laughs())
        proc, elapsed = _cli(hdd, "hypothesis", "create", "Accuracy improves", "--paper", "130")
        _ok(proc, elapsed)
        assert "Created" in proc.stdout, proc.stdout[:2000] + proc.stderr[:2000]
        assert "could not update" not in proc.stdout, proc.stdout[:2000]
        assert "hasHypothesis" in path.read_text(encoding="utf-8")

    def test_service_update_parent_plain_control(self, hdd: Path) -> None:
        path = _paper_file(hdd, "[a, b]")
        code = _SERVICE + (
            "ok = s.update_parent_turtle_block('PAPER-130', 'hypothesis', 'H130.1')\n"
            "p = s.get_item('PAPER-130')\n"
            "print(json.dumps({'ok': ok, 'triples': len(p.graph) if p.graph else 0}))\n"
        )
        proc, elapsed = _py(hdd, code, "update_parent_turtle_block control")
        _ok(proc, elapsed)
        out = json.loads(proc.stdout.strip().splitlines()[-1])
        assert out["ok"] is True, out
        assert out["triples"] > 0, out
        assert "hasHypothesis" in path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Undefined alias after blanking (software board, like #277's fixture)
# ---------------------------------------------------------------------------


@pytest.fixture
def sw(tmp_path: Path) -> Path:
    """A software-theme board with one committed feature, FEAT-001."""
    _init_git(tmp_path)
    proc, _ = _cli(tmp_path, "init", "--theme", "software")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    from yurtle_kanban import config as config_mod

    config_mod._theme_cache.clear()
    svc = KanbanService(KanbanConfig.load(tmp_path / ".kanban" / "config.yaml"), tmp_path)
    svc.create_item(WorkItemType.FEATURE, "Existing item", description="Body")
    _commit_all(tmp_path, "seed")
    return tmp_path


def _item_file(repo: Path, item_id: str = "FEAT-001") -> Path:
    found = [p for p in repo.rglob(f"{item_id}*.md") if ".git" not in p.parts]
    assert len(found) == 1, found
    return found[0]


def _set_frontmatter(repo: Path, lines: str) -> Path:
    """Insert `lines` right after FEAT-001's opening `---`, dropping the lines already
    there for the same keys."""
    path = _item_file(repo)
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), text[:80]
    head, sep, body = text[4:].partition("\n---")
    keys = tuple(ln.split(":", 1)[0] + ":" for ln in lines.splitlines())
    kept = [ln for ln in head.split("\n") if not ln.startswith(keys)]
    path.write_text("---\n" + lines + "\n".join(kept) + sep + body, encoding="utf-8")
    _commit_all(repo, "edit")
    return path


def _list_item(repo: Path) -> dict:
    proc, elapsed = _cli(repo, "list", "--json")
    _ok(proc, elapsed)
    items = {d["id"]: d for d in json.loads(proc.stdout)}
    assert "FEAT-001" in items, proc.stdout[:2000] + proc.stderr[:2000]
    return items["FEAT-001"]


def _probe(repo: Path) -> dict:
    code = _SERVICE + (
        "i = s.get_item('FEAT-001')\n"
        "print(json.dumps({'found': i is not None, 'extra': i is not None and "
        "i.metadata.get('extra'), 'triples': len(i.graph) if i is not None and i.graph "
        "else 0}))\n"
    )
    proc, elapsed = _py(repo, code, "get_item probe")
    _ok(proc, elapsed)
    return json.loads(proc.stdout.strip().splitlines()[-1])


class TestUndefinedAlias:
    LINES = f"tags: {_laughs()}\nextra: *a0\n"

    def test_graph_not_empty(self, sw: Path) -> None:
        baseline = _list_item(sw)["triple_count"]
        assert baseline > 0, baseline
        _set_frontmatter(sw, self.LINES)
        item = _list_item(sw)
        assert item["tags"] == [], item
        assert item["title"] == "Existing item", item
        assert 0 < item["triple_count"] < 10_000, item["triple_count"]

    def test_extra_kept_in_metadata(self, sw: Path) -> None:
        _set_frontmatter(sw, self.LINES)
        probe = _probe(sw)
        assert probe["found"], probe
        assert probe["extra"] == ["lol"] * 10, probe
        assert 0 < probe["triples"] < 10_000, probe


class TestControls:
    def test_normal_item_triples_unchanged(self, sw: Path) -> None:
        """A plain `extra:` list adds its triples; nothing is blanked for it."""
        base = _list_item(sw)["triple_count"]
        _set_frontmatter(sw, "extra: [x, y]\n")
        with_extra = _list_item(sw)["triple_count"]
        assert with_extra >= base > 0, (base, with_extra)
        probe = _probe(sw)
        assert probe["extra"] == ["x", "y"], probe
        assert probe["triples"] == with_extra, probe

    def test_small_shared_alias_keeps_triples(self, sw: Path) -> None:
        """An alias to a KEPT anchor is not blanked: same triples as the literal copy."""
        _set_frontmatter(sw, "base: [x]\ntags: [x]\n")
        literal = _list_item(sw)["triple_count"]
        _set_frontmatter(sw, "base: &b [x]\ntags: *b\n")
        aliased = _list_item(sw)
        assert aliased["tags"] == ["x"], aliased
        assert aliased["triple_count"] == literal, (aliased["triple_count"], literal)
