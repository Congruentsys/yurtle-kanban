"""Issue #277 — billion-laughs YAML (a shared-alias DAG) in frontmatter.

Decided behaviour:
- A frontmatter field whose EXPANDED size (nodes counted with aliases expanded: a
  shared alias counts each time it is used) exceeds a bound (10,000 nodes) is
  dropped at parse time with ONE warning naming the file, the field and saying
  "too large" (like #262's cyclic warning). The item is still listed.
- The Turtle/graph path must not re-expand the dropped field either, so `list`,
  `list --json` and `board` finish quickly with small output.
- Large-but-legit values (500 plain tags) and small shared aliases are kept, no warning.

Every CLI call runs in a subprocess with a timeout, so a hang fails, not blocks.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.models import WorkItemType
from yurtle_kanban.service import KanbanService

SRC = Path(__file__).resolve().parents[2] / "src"
TIMEOUT = 20  # a hang fails the test; the bound on a passing run is FAST
FAST = 10.0
MAX_OUT = 1_000_000  # bytes of stdout


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout


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


def _ok(proc: subprocess.CompletedProcess[str], elapsed: float) -> None:
    assert proc.returncode == 0, proc.stdout[:2000] + proc.stderr[:2000]
    assert "Traceback" not in proc.stderr, proc.stderr[:4000]
    assert elapsed < FAST, f"took {elapsed:.1f}s"
    assert len(proc.stdout.encode()) < MAX_OUT, f"stdout is {len(proc.stdout)} chars"


def _service(repo: Path) -> KanbanService:
    return KanbanService(KanbanConfig.load(repo / ".kanban" / "config.yaml"), repo)


@pytest.fixture
def sw(tmp_path: Path) -> Path:
    """A software-theme board with one committed feature, FEAT-001."""
    for args in (
        ["init", "-q", "-b", "main"],
        ["config", "user.email", "test@test.com"],
        ["config", "user.name", "Test"],
        ["config", "commit.gpgsign", "false"],
    ):
        _git(tmp_path, *args)
    proc, _ = _cli(tmp_path, "init", "--theme", "software")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    from yurtle_kanban import config as config_mod

    config_mod._theme_cache.clear()
    _service(tmp_path).create_item(WorkItemType.FEATURE, "Existing item", description="Body")
    _commit_all(tmp_path, "seed")
    return tmp_path


def _item_file(repo: Path, item_id: str = "FEAT-001") -> Path:
    found = [p for p in repo.rglob(f"{item_id}*.md") if ".git" not in p.parts]
    assert len(found) == 1, found
    return found[0]


def _add_frontmatter(repo: Path, lines: str) -> Path:
    """Insert `lines` right after FEAT-001's opening `---`, dropping the lines already
    there for the same keys (a duplicate key would win over the inserted one)."""
    path = _item_file(repo)
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), text[:80]
    head, sep, body = text[4:].partition("\n---")
    keys = tuple(ln.split(":", 1)[0] + ":" for ln in lines.splitlines())
    kept = [ln for ln in head.split("\n") if not ln.startswith(keys)]
    path.write_text("---\n" + lines + "\n".join(kept) + sep + body, encoding="utf-8")
    _commit_all(repo, "edit")
    return path


def _laughs(levels: int = 9, width: int = 10) -> str:
    """One flow value, all anchors defined inside it: level k is a list of `width`
    copies of level k-1, so it expands to width**levels leaves (1e9 by default)
    while the text is a few hundred bytes."""
    node = "&a0 [" + ", ".join(["lol"] * width) + "]"
    for k in range(1, levels):
        node = f"&a{k} [" + node + f", *a{k - 1}" * (width - 1) + "]"
    return node


def _too_large_lines(stderr: str) -> list[str]:
    return [ln for ln in stderr.splitlines() if "too large" in ln.lower()]


def _json_item(proc: subprocess.CompletedProcess[str]) -> dict:
    rows = json.loads(proc.stdout)
    items = {d["id"]: d for d in rows}
    assert "FEAT-001" in items, proc.stdout[:2000]
    return items["FEAT-001"]


def _metadata_probe(repo: Path, key: str) -> dict:
    """In a subprocess (so a hang is a failure): FEAT-001's `key` in metadata and
    its triple count."""
    code = (
        "import json, sys\n"
        "from pathlib import Path\n"
        "from yurtle_kanban.config import KanbanConfig\n"
        "from yurtle_kanban.service import KanbanService\n"
        "r = Path.cwd()\n"
        "s = KanbanService(KanbanConfig.load(r / '.kanban' / 'config.yaml'), r)\n"
        "i = s.get_item('FEAT-001')\n"
        f"print(json.dumps({{'found': i is not None, 'has': i is not None and {key!r} in "
        "i.metadata, 'triples': len(i.graph) if i is not None and i.graph else 0}))\n"
    )
    proc, elapsed = _run(repo, [sys.executable, "-c", code], f"get_item probe {key}")
    _ok(proc, elapsed)
    return json.loads(proc.stdout.strip().splitlines()[-1])


# field -> frontmatter lines holding a billion-laughs value in it
HUGE = {
    "tags": f"tags: {_laughs()}\n",
    "extra": f"extra: {_laughs()}\n",
}


class TestHugeFieldDropped:
    @pytest.mark.parametrize("field", list(HUGE))
    def test_list_json(self, sw: Path, field: str) -> None:
        _add_frontmatter(sw, HUGE[field])
        proc, elapsed = _cli(sw, "list", "--json")
        _ok(proc, elapsed)
        item = _json_item(proc)
        assert item["tags"] == []
        assert item["title"] == "Existing item"
        assert item["triple_count"] < 10_000, item["triple_count"]

    @pytest.mark.parametrize("field", list(HUGE))
    def test_list_one_warning(self, sw: Path, field: str) -> None:
        path = _add_frontmatter(sw, HUGE[field])
        proc, elapsed = _cli(sw, "list")
        _ok(proc, elapsed)
        assert "FEAT-001" in proc.stdout, proc.stdout[:2000] + proc.stderr[:2000]
        warned = _too_large_lines(proc.stderr)
        assert len(warned) == 1, proc.stderr[:4000]
        assert f"`{field}`" in warned[0] or field in warned[0], warned[0]
        assert path.name in warned[0] or "FEAT-001" in warned[0], warned[0]

    @pytest.mark.parametrize("field", list(HUGE))
    def test_board(self, sw: Path, field: str) -> None:
        _add_frontmatter(sw, HUGE[field])
        proc, elapsed = _cli(sw, "board")
        _ok(proc, elapsed)
        assert "FEAT-001" in proc.stdout, proc.stdout[:2000] + proc.stderr[:2000]
        assert "lol" not in proc.stdout, proc.stdout[:2000]
        assert len(_too_large_lines(proc.stderr)) <= 1, proc.stderr[:4000]

    def test_extra_dropped_from_metadata(self, sw: Path) -> None:
        _add_frontmatter(sw, HUGE["extra"])
        probe = _metadata_probe(sw, "extra")
        assert probe["found"], probe
        assert not probe["has"], probe
        assert probe["triples"] < 10_000, probe

    def test_other_fields_kept(self, sw: Path) -> None:
        """Only the huge field goes; a sibling field next to it survives."""
        _add_frontmatter(sw, HUGE["tags"] + "related: [FEAT-009]\n")
        proc, elapsed = _cli(sw, "list", "--json")
        _ok(proc, elapsed)
        item = _json_item(proc)
        assert item["tags"] == []
        assert item["related"] == ["FEAT-009"]


class TestControls:
    def test_many_plain_tags_kept(self, sw: Path) -> None:
        tags = [f"t{i}" for i in range(500)]
        _add_frontmatter(sw, "tags: [" + ", ".join(tags) + "]\n")
        for args in (("list", "--json"), ("list",), ("board",)):
            proc, elapsed = _cli(sw, *args)
            _ok(proc, elapsed)
            assert not _too_large_lines(proc.stderr), proc.stderr[:4000]
            if args == ("list", "--json"):
                assert _json_item(proc)["tags"] == tags

    def test_small_shared_alias_kept(self, sw: Path) -> None:
        _add_frontmatter(sw, "base: &b [x]\ntags: *b\n")
        proc, elapsed = _cli(sw, "list", "--json")
        _ok(proc, elapsed)
        assert _json_item(proc)["tags"] == ["x"]
        assert not _too_large_lines(proc.stderr), proc.stderr[:4000]

    def test_small_shared_alias_in_metadata_kept(self, sw: Path) -> None:
        _add_frontmatter(sw, "extra: {a: &x [1, 2], b: *x, c: *x}\n")
        probe = _metadata_probe(sw, "extra")
        assert probe["found"] and probe["has"], probe
        proc, elapsed = _cli(sw, "list")
        _ok(proc, elapsed)
        assert not _too_large_lines(proc.stderr), proc.stderr[:4000]
