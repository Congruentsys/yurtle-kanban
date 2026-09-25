"""Issue #297 — `_unparseable_reason` on frontmatter nested too deeply for PyYAML.

Decided behaviour:
- `KanbanService._unparseable_reason(path, content)` on frontmatter whose `extra:` is a
  flow list nested 1000 deep (so `yaml.safe_load` raises RecursionError — checked here)
  returns "frontmatter nested too deeply to parse" and does not raise.
- The caller a user reaches — epic linking (`_update_item_related`) on an item whose
  file was overwritten with too-deep frontmatter after the scan, as #188's tests do —
  prints a warning saying "nested too deeply", links nothing, leaves the file alone,
  and no Traceback / RecursionError text escapes.
- Controls: plain broken YAML still gives "YAML error: …"; a valid item still links.

Everything runs in a subprocess: the real stack depth is what matters.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from yurtle_kanban.config import KanbanConfig, PathConfig

SRC = Path(__file__).resolve().parents[2] / "src"
TIMEOUT = 60
TOO_DEEP = 1000

_HEAD = (
    "---\n"
    "id: FEAT-001\n"
    'title: "Linked item"\n'
    "type: feature\n"
    "status: backlog\n"
    "priority: medium\n"
)
_TAIL = "---\n\n# Linked item\n\nBody text.\n"


def _nested(depth: int) -> str:
    return "extra: " + "[" * depth + "x" + "]" * depth + "\n"


DEEP_DOC = _HEAD + _nested(TOO_DEEP) + _TAIL
BROKEN_DOC = _HEAD + "extra: [unclosed\n" + _TAIL
VALID_DOC = _HEAD + _TAIL


def _run(script: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": str(SRC), "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        return subprocess.run(
            [sys.executable, "-c", textwrap.dedent(script)],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(f"subprocess hung for {TIMEOUT}s")


def _clean(proc: subprocess.CompletedProcess[str]) -> None:
    both = proc.stdout + proc.stderr
    assert proc.returncode == 0, both
    assert "Traceback" not in both, both
    assert "RecursionError" not in both, both
    assert "maximum recursion depth" not in both, both


_REASON_SCRIPT = """
    import json, sys
    from pathlib import Path
    from yurtle_kanban.service import KanbanService
    svc = KanbanService.__new__(KanbanService)
    content = Path(sys.argv[1]).read_text(encoding="utf-8")
    reason = svc._unparseable_reason(Path("FEAT-001-linked-item.md"), content)
    print(json.dumps({"reason": reason}))
"""


def _reason(tmp_path: Path, doc: str) -> str | None:
    src = tmp_path / "doc.md"
    src.write_text(doc, encoding="utf-8")
    script = _REASON_SCRIPT.replace("sys.argv[1]", repr(str(src)))
    proc = _run(script, tmp_path)
    _clean(proc)
    return json.loads(proc.stdout.strip().splitlines()[-1])["reason"]


class TestPrecondition:
    def test_safe_load_really_overflows(self, tmp_path: Path) -> None:
        script = f"""
            import yaml
            try:
                yaml.safe_load({_nested(TOO_DEEP)!r})
            except RecursionError:
                print("RECURSION")
            else:
                print("PARSED")
        """
        proc = _run(script, tmp_path)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert proc.stdout.strip() == "RECURSION", proc.stdout + proc.stderr


class TestUnparseableReason:
    def test_too_deep_says_nested_too_deeply(self, tmp_path: Path) -> None:
        assert _reason(tmp_path, DEEP_DOC) == "frontmatter nested too deeply to parse"

    def test_control_broken_yaml_keeps_yaml_error(self, tmp_path: Path) -> None:
        reason = _reason(tmp_path, BROKEN_DOC)
        assert reason is not None and reason.startswith("YAML error: "), reason
        assert "nested too deeply" not in reason, reason


# ---------------------------------------------------------------------------
# Epic linking on an item overwritten after the scan (#188's pattern)
# ---------------------------------------------------------------------------


@pytest.fixture
def software_repo(tmp_path: Path) -> Path:
    """A minimal git repo with the software theme and a good FEAT-001."""
    for args in (
        ["init", "-q", "-b", "main"],
        ["config", "user.email", "test@test.com"],
        ["config", "user.name", "Test"],
    ):
        subprocess.run(["git", *args], cwd=tmp_path, capture_output=True, check=True)
    for sub in ("features", "epics", "bugs"):
        (tmp_path / "work" / sub).mkdir(parents=True)
    (tmp_path / ".kanban").mkdir()
    KanbanConfig(
        theme="software",
        paths=PathConfig(
            root="work/",
            scan_paths=["work/features/", "work/epics/", "work/bugs/"],
        ),
    ).save(tmp_path / ".kanban" / "config.yaml")
    _item_path(tmp_path).write_text(VALID_DOC, encoding="utf-8")
    return tmp_path


def _item_path(repo: Path) -> Path:
    return repo / "work" / "features" / "FEAT-001-linked-item.md"


_LINK_SCRIPT = """
    import io, json
    from pathlib import Path
    from rich.console import Console
    from yurtle_kanban import epic_commands
    from yurtle_kanban.config import KanbanConfig
    from yurtle_kanban.service import KanbanService
    repo = Path.cwd()
    svc = KanbanService(KanbanConfig.load(repo / ".kanban" / "config.yaml"), repo)
    svc.scan()
    assert "FEAT-001" in svc._items, sorted(svc._items)
    Path(NEW_DOC_PATH).replace(ITEM_PATH)
    buf = io.StringIO()
    epic_commands.console = Console(file=buf, width=300, color_system=None)
    linked = epic_commands._update_item_related(svc, "FEAT-001", "EPIC-001")
    print(json.dumps({"linked": linked, "out": buf.getvalue()}))
"""


def _link_after_break(repo: Path, doc: str) -> tuple[subprocess.CompletedProcess[str], dict]:
    new_doc = repo / "new-doc.txt"
    new_doc.write_text(doc, encoding="utf-8")
    script = _LINK_SCRIPT.replace("NEW_DOC_PATH", repr(str(new_doc))).replace(
        "ITEM_PATH", repr(str(_item_path(repo)))
    )
    proc = _run(script, repo)
    _clean(proc)
    return proc, json.loads(proc.stdout.strip().splitlines()[-1])


class TestEpicLinkTooDeep:
    def test_warns_nested_too_deeply(self, software_repo: Path) -> None:
        _, res = _link_after_break(software_repo, DEEP_DOC)
        out = res["out"]
        assert res["linked"] is False, res
        assert "FEAT-001" in out, out
        assert "nested too deeply" in out, out
        assert "No frontmatter" not in out, out

    def test_file_unchanged(self, software_repo: Path) -> None:
        _link_after_break(software_repo, DEEP_DOC)
        assert _item_path(software_repo).read_text(encoding="utf-8") == DEEP_DOC

    def test_control_broken_yaml_says_yaml_error(self, software_repo: Path) -> None:
        _, res = _link_after_break(software_repo, BROKEN_DOC)
        out = res["out"]
        assert res["linked"] is False, res
        assert "YAML error" in out, out
        assert "nested too deeply" not in out, out
        assert _item_path(software_repo).read_text(encoding="utf-8") == BROKEN_DOC

    def test_control_valid_links(self, software_repo: Path) -> None:
        _, res = _link_after_break(software_repo, VALID_DOC)
        assert res["linked"] is True, res
        text = _item_path(software_repo).read_text(encoding="utf-8")
        assert "related: [EPIC-001]\n" in text, text
