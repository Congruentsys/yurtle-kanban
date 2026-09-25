"""Issue #262 — cyclic YAML frontmatter values (anchor/alias recursion).

Decided behaviour:
- A frontmatter field whose YAML value is cyclic (`tags: &a [x, *a]`,
  `related: &r [*r]`, a mapping containing itself) is dropped at parse time: the
  item is still listed and the field is treated as absent (tags -> [], related -> []).
- ONE warning is recorded for that file, naming the field and saying "cyclic"
  (through `service.parse_warnings` or the "yurtle-kanban" logger; either way it
  reaches stderr of `list`).
- `list --json` and `export --format json` exit 0 with valid JSON holding the item.
- A hooks file whose `create_item` action has cyclic `tags` creates its item with
  the tags dropped (or logs a clean warning saying "cyclic") — no traceback.
- Non-cyclic anchors/aliases keep working (`tags: *b` -> ['x']).

Every CLI call runs in a subprocess with a timeout, so a hang fails, not blocks.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.models import WorkItemType
from yurtle_kanban.service import KanbanService

SRC = Path(__file__).resolve().parents[2] / "src"
TIMEOUT = 60


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout


def _commit_all(repo: Path, message: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--allow-empty", "-m", message)


def _cli(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": str(SRC), "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        return subprocess.run(
            [sys.executable, "-c", "from yurtle_kanban.cli import main; main()", *args],
            cwd=repo,
            env=env,
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(f"`{' '.join(args)}` hung for {TIMEOUT}s")


def _ok(proc: subprocess.CompletedProcess[str]) -> None:
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Traceback" not in proc.stdout + proc.stderr, proc.stdout + proc.stderr


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
    _ok(_cli(tmp_path, "init", "--theme", "software"))
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


# field name -> frontmatter lines making it cyclic, and what the field reads as
CYCLIC = {
    "tags-list": ("tags", "tags: &a [x, *a]\n", "tags", []),
    "tags-only-self": ("tags", "tags: &a [*a]\n", "tags", []),
    "related-self": ("related", "related: &r [*r]\n", "related", []),
    "tags-mapping": ("tags", "tags: &m {me: *m}\n", "tags", []),
    "depends-mapping": ("depends_on", "depends_on: &m {me: *m}\n", "depends_on", []),
}


def _cyclic_lines(stderr: str) -> list[str]:
    return [ln for ln in stderr.splitlines() if "cyclic" in ln.lower()]


class TestCyclicFieldDropped:
    @pytest.mark.parametrize("case", list(CYCLIC), ids=list(CYCLIC))
    def test_list_json(self, sw: Path, case: str) -> None:
        field, lines, key, expected = CYCLIC[case]
        _add_frontmatter(sw, lines)
        proc = _cli(sw, "list", "--json")
        _ok(proc)
        data = json.loads(proc.stdout)
        items = {d["id"]: d for d in data}
        assert "FEAT-001" in items, proc.stdout
        assert items["FEAT-001"][key] == expected

    @pytest.mark.parametrize("case", list(CYCLIC), ids=list(CYCLIC))
    def test_export_json(self, sw: Path, case: str) -> None:
        field, lines, key, expected = CYCLIC[case]
        _add_frontmatter(sw, lines)
        proc = _cli(sw, "export", "--format", "json")
        _ok(proc)
        data = json.loads(proc.stdout)
        items = {d["id"]: d for d in data["items"]}
        assert "FEAT-001" in items, proc.stdout
        assert items["FEAT-001"][key] == expected

    @pytest.mark.parametrize("case", list(CYCLIC), ids=list(CYCLIC))
    def test_one_warning_names_field(self, sw: Path, case: str) -> None:
        field, lines, _key, _expected = CYCLIC[case]
        path = _add_frontmatter(sw, lines)
        proc = _cli(sw, "list")
        _ok(proc)
        assert "FEAT-001" in proc.stdout, proc.stdout + proc.stderr
        warned = _cyclic_lines(proc.stderr)
        assert len(warned) == 1, proc.stderr
        assert field in warned[0], warned[0]
        assert path.name in warned[0] or "FEAT-001" in warned[0], warned[0]

    def test_metadata_mapping_dropped(self, sw: Path) -> None:
        """A cyclic mapping in a field that lands in metadata is dropped too."""
        _add_frontmatter(sw, "extra: &m {me: *m}\n")
        svc = _service(sw)
        item = svc.get_item("FEAT-001")
        assert item is not None
        assert "extra" not in item.metadata, item.metadata.get("extra")
        proc = _cli(sw, "list")
        _ok(proc)
        warned = _cyclic_lines(proc.stderr)
        assert len(warned) == 1 and "extra" in warned[0], proc.stderr

    def test_service_records_one_warning(self, sw: Path, caplog: pytest.LogCaptureFixture) -> None:
        """In-process: exactly one record for the file, in parse_warnings or the log."""
        path = _add_frontmatter(sw, "tags: &a [x, *a]\n")
        svc = _service(sw)
        with caplog.at_level(logging.WARNING, logger="yurtle-kanban"):
            items = svc.get_items()
        item = next(i for i in items if i.id == "FEAT-001")
        assert item.tags == []
        records = [
            r for p, r in dict.fromkeys(svc.parse_warnings) if p == path and "cyclic" in r.lower()
        ] + [
            r.getMessage()
            for r in caplog.records
            if r.name.startswith("yurtle-kanban") and "cyclic" in r.getMessage().lower()
        ]
        assert len(records) == 1, records
        assert "tags" in records[0], records[0]

    def test_other_fields_kept(self, sw: Path) -> None:
        """Only the cyclic field goes: the item keeps its title and other tags-free fields."""
        _add_frontmatter(sw, "tags: &a [x, *a]\nrelated: [FEAT-009]\n")
        proc = _cli(sw, "list", "--json")
        _ok(proc)
        item = next(d for d in json.loads(proc.stdout) if d["id"] == "FEAT-001")
        assert item["title"] == "Existing item"
        assert item["related"] == ["FEAT-009"]
        assert item["tags"] == []


class TestNonCyclicAnchorsControl:
    def test_alias_to_list(self, sw: Path) -> None:
        _add_frontmatter(sw, "base: &b [x]\ntags: *b\n")
        for args in (("list", "--json"), ("export", "--format", "json")):
            proc = _cli(sw, *args)
            _ok(proc)
            data = json.loads(proc.stdout)
            rows = data if isinstance(data, list) else data["items"]
            item = next(d for d in rows if d["id"] == "FEAT-001")
            assert item["tags"] == ["x"]
            assert not _cyclic_lines(proc.stderr), proc.stderr

    def test_shared_alias_twice(self, sw: Path) -> None:
        """The same anchor used by two fields is shared, not cyclic."""
        _add_frontmatter(sw, "tags: &t [x, y]\nrelated: *t\n")
        proc = _cli(sw, "list", "--json")
        _ok(proc)
        item = next(d for d in json.loads(proc.stdout) if d["id"] == "FEAT-001")
        assert item["tags"] == ["x", "y"]
        assert item["related"] == ["x", "y"]
        assert not _cyclic_lines(proc.stderr), proc.stderr

    # one field reusing a container inside itself: shared, not cyclic (review of #274)
    REUSED = {
        "extra-mapping": ("extra: {a: &x [1], b: *x}\n", "extra", {"a": [1], "b": [1]}),
        "depends-mapping": (
            "depends_on: {a: &x [FEAT-9], b: *x}\n",
            "depends_on",
            {"a": ["FEAT-9"], "b": ["FEAT-9"]},
        ),
        "tags-list": ("tags: [&t [x], *t]\n", "tags", [["x"], ["x"]]),
    }

    @pytest.mark.parametrize("case", list(REUSED), ids=list(REUSED))
    def test_reused_within_one_field_in_process(
        self, sw: Path, case: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        lines, key, expected = self.REUSED[case]
        path = _add_frontmatter(sw, lines)
        svc = _service(sw)
        with caplog.at_level(logging.WARNING, logger="yurtle-kanban"):
            items = svc.get_items()
        item = next((i for i in items if i.id == "FEAT-001"), None)
        assert item is not None, svc.parse_warnings
        value = item.metadata.get(key) if key == "extra" else getattr(item, key)
        assert value == expected
        noted = [r for p, r in svc.parse_warnings if p == path] + [
            r.getMessage() for r in caplog.records if "cyclic" in r.getMessage().lower()
        ]
        assert not noted, noted

    @pytest.mark.parametrize("case", list(REUSED), ids=list(REUSED))
    def test_reused_within_one_field_cli(self, sw: Path, case: str) -> None:
        lines, key, _expected = self.REUSED[case]
        _add_frontmatter(sw, lines)
        proc = _cli(sw, "list")
        _ok(proc)
        assert "FEAT-001" in proc.stdout, proc.stdout + proc.stderr
        assert not _cyclic_lines(proc.stderr), proc.stderr
        assert "skipped" not in proc.stderr, proc.stderr


class TestDeepNesting:
    """Deep but acyclic frontmatter keeps its item, as on main (review of #274)."""

    DEPTH = 400  # PyYAML parses ~500; a doubly recursive walk overflows near 340

    def test_deep_list_item_kept(self, sw: Path) -> None:
        deep = "[" * self.DEPTH + "x" + "]" * self.DEPTH
        _add_frontmatter(sw, f"extra: {deep}\n")
        proc = _cli(sw, "list", "--json")
        _ok(proc)
        assert "RecursionError" not in proc.stderr, proc.stderr
        assert "skipped" not in proc.stderr, proc.stderr
        assert not _cyclic_lines(proc.stderr), proc.stderr
        out = proc.stdout.lstrip()
        ids = [d["id"] for d in json.loads(out)] if out.startswith("[") else []
        assert "FEAT-001" in ids, proc.stdout + proc.stderr


HOOKS = (
    "---\n"
    "type: kanban-hooks\n"
    "version: 1\n"
    "hooks:\n"
    "  on_create:\n"
    "    - item_types: [feature]\n"
    "      actions:\n"
    "        - type: create_item\n"
    "          item_type: bug\n"
    '          title: "Follow-up"\n'
    "          tags: {tags}\n"
    "---\n"
    "# hooks\n"
)


def _write_hooks(repo: Path, tags: str) -> None:
    hooks = repo / ".kanban" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    (hooks / "kanban-hooks.yurtle.md").write_text(HOOKS.format(tags=tags), encoding="utf-8")
    _commit_all(repo, "hooks")


def _bug_rows(repo: Path) -> list[dict[str, Any]]:
    proc = _cli(repo, "list", "--json")
    _ok(proc)
    if not proc.stdout.lstrip().startswith("["):
        return []
    return [d for d in json.loads(proc.stdout) if d["item_type"] == "bug"]


class TestHookCreateItem:
    def test_cyclic_tags_dropped(self, sw: Path) -> None:
        _write_hooks(sw, "&a [x, *a]")
        proc = _cli(sw, "create", "feature", "Hello")
        _ok(proc)
        assert list(sw.rglob("FEAT-002*.md")), proc.stdout + proc.stderr
        bugs = _bug_rows(sw)
        if bugs:
            assert [b["title"] for b in bugs] == ["Follow-up"]
            assert bugs[0]["tags"] == [], bugs[0]["tags"]
        else:
            # the alternative: no bug, but a clean warning that says why
            assert _cyclic_lines(proc.stderr), proc.stdout + proc.stderr

    def test_control_plain_tags(self, sw: Path) -> None:
        _write_hooks(sw, "[x, y]")
        proc = _cli(sw, "create", "feature", "Hello")
        _ok(proc)
        bugs = _bug_rows(sw)
        assert [b["tags"] for b in bugs] == [["x", "y"]], proc.stdout + proc.stderr
