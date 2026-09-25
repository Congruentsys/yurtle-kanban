"""Issue #330 — hooks.py / workflow.py frontmatter that parses to a non-mapping.

`hooks._extract_frontmatter` and `WorkflowParser._extract_frontmatter` both do
`yaml.safe_load(...) or {}`, so a non-empty YAML list or bare scalar passes through
despite the `dict[str, Any]` return type. Their only callers `.get()` on it:

- `HookEngine._load_config` (reached by `HookEngine(path)` and by every
  `KanbanService(...)`, which loads `.kanban/hooks/kanban-hooks.yurtle.md`):
  `frontmatter.get("hooks", {})` raises AttributeError. `_load_config` swallows
  it with a blanket `except Exception` and logs "Failed to load hooks config ...
  'list' object has no attribute 'get'". The engine ends up unconfigured, which
  is the right end state — the misbehaviour is the spurious AttributeError warning.
- `WorkflowParser.parse_workflow_file` (public; `WorkflowParser` is exported
  from `yurtle_kanban`): `frontmatter.get("type")` raises AttributeError straight
  out to the caller. `load_all_workflows` / `load_workflow` / `KanbanService.
  get_workflow` catch it and log "Failed to parse workflow config ...".

Decided behaviour (#321 precedent): both helpers return `{}` for any non-mapping
frontmatter, the same as for broken YAML. A mapping is returned unchanged. So a
non-mapping hook file loads as "no hooks" without an AttributeError warning, and
a non-mapping workflow file is "not a kanban-workflow" (`parse_workflow_file`
returns None) without raising.

No CLI command is needed: `HookEngine`, `WorkflowParser` and `KanbanService` are
all public API and reach the helpers with real files, no monkeypatching.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import pytest

from yurtle_kanban import HookEngine, KanbanService, WorkflowParser
from yurtle_kanban.config import KanbanConfig, PathConfig
from yurtle_kanban.hooks import _extract_frontmatter as hooks_extract

# Non-empty, parseable, not a mapping.
NON_MAPPING = {
    "list": "---\n- a\n- b\n---\n\n# Body\n",
    "list-of-maps": "---\n- type: kanban-workflow\n  applies_to: feature\n---\n\n# Body\n",
    "str": "---\njust text\n---\n\n# Body\n",
    "int": "---\n5\n---\n\n# Body\n",
}
IDS = list(NON_MAPPING)

MAPPING_DOC = "---\ntype: kanban-workflow\nid: wf\napplies_to: feature\nversion: 2\n---\n"
MAPPING = {"type": "kanban-workflow", "id": "wf", "applies_to": "feature", "version": 2}
BROKEN_DOC = "---\nkey: [unclosed\n---\n"

VALID_WORKFLOW = (
    "---\n"
    "type: kanban-workflow\n"
    "id: feature-workflow\n"
    "applies_to: feature\n"
    "version: 1\n"
    "---\n\n# Feature Workflow\n"
)

VALID_HOOKS = (
    "---\n"
    "type: kanban-hooks\n"
    "hooks:\n"
    "  on_create:\n"
    "    - actions:\n"
    "        - type: log\n"
    "---\n"
)


def _wf_extract(content: str) -> object:
    return WorkflowParser(Path("unused"))._extract_frontmatter(content)


EXTRACTORS = {"hooks": hooks_extract, "workflow": _wf_extract}


def _attr_error_records(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if "has no attribute" in r.getMessage()]


# ---------------------------------------------------------------------------
# 1. Direct unit tests on both helpers
# ---------------------------------------------------------------------------


class TestExtractFrontmatter:
    @pytest.mark.parametrize("which", list(EXTRACTORS))
    @pytest.mark.parametrize("case", IDS, ids=IDS)
    def test_non_mapping_is_empty_dict(self, which: str, case: str) -> None:
        assert EXTRACTORS[which](NON_MAPPING[case]) == {}

    @pytest.mark.parametrize("which", list(EXTRACTORS))
    def test_mapping_unchanged(self, which: str) -> None:
        assert EXTRACTORS[which](MAPPING_DOC) == MAPPING

    @pytest.mark.parametrize("which", list(EXTRACTORS))
    def test_broken_yaml_is_empty_dict(self, which: str) -> None:
        assert EXTRACTORS[which](BROKEN_DOC) == {}

    @pytest.mark.parametrize("which", list(EXTRACTORS))
    def test_empty_block_is_empty_dict(self, which: str) -> None:
        assert EXTRACTORS[which]("---\n---\n") == {}


# ---------------------------------------------------------------------------
# 2. Public path: HookEngine(path)
# ---------------------------------------------------------------------------


class TestHookEngineNonMapping:
    @pytest.mark.parametrize("case", IDS, ids=IDS)
    def test_loads_as_unconfigured_without_attribute_error(
        self, tmp_path: Path, case: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        path = tmp_path / "kanban-hooks.yurtle.md"
        path.write_text(NON_MAPPING[case])
        with caplog.at_level(logging.DEBUG):
            engine = HookEngine(path)
        assert not engine.is_configured
        assert _attr_error_records(caplog) == []

    def test_control_mapping_loads(self, tmp_path: Path) -> None:
        path = tmp_path / "kanban-hooks.yurtle.md"
        path.write_text(VALID_HOOKS)
        assert HookEngine(path).is_configured


# ---------------------------------------------------------------------------
# 3. Public path: WorkflowParser
# ---------------------------------------------------------------------------


@pytest.fixture
def wf_dir(tmp_path: Path) -> Path:
    kanban = tmp_path / ".kanban"
    (kanban / "workflows").mkdir(parents=True)
    return kanban


class TestWorkflowParserNonMapping:
    @pytest.mark.parametrize("case", IDS, ids=IDS)
    def test_parse_workflow_file_returns_none(self, wf_dir: Path, case: str) -> None:
        path = wf_dir / "workflows" / "bad.yurtle.md"
        path.write_text(NON_MAPPING[case])
        assert WorkflowParser(wf_dir).parse_workflow_file(path) is None

    @pytest.mark.parametrize("case", IDS, ids=IDS)
    def test_load_all_skips_without_attribute_error(
        self, wf_dir: Path, case: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        (wf_dir / "workflows" / "bad.yurtle.md").write_text(NON_MAPPING[case])
        (wf_dir / "workflows" / "feature.yurtle.md").write_text(VALID_WORKFLOW)
        with caplog.at_level(logging.DEBUG):
            loaded = WorkflowParser(wf_dir).load_all_workflows()
        assert list(loaded) == ["feature"]
        assert _attr_error_records(caplog) == []

    def test_control_mapping_parses(self, wf_dir: Path) -> None:
        path = wf_dir / "workflows" / "feature.yurtle.md"
        path.write_text(VALID_WORKFLOW)
        wf = WorkflowParser(wf_dir).parse_workflow_file(path)
        assert wf is not None and wf.applies_to == "feature" and wf.id == "feature-workflow"


# ---------------------------------------------------------------------------
# 4. Public path: KanbanService (hooks file + workflow file on a real repo)
# ---------------------------------------------------------------------------


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    for args in (
        ["init", "-q", "-b", "main"],
        ["config", "user.email", "t@t.com"],
        ["config", "user.name", "T"],
    ):
        subprocess.run(["git", *args], cwd=tmp_path, capture_output=True, check=True)
    (tmp_path / ".kanban" / "hooks").mkdir(parents=True)
    (tmp_path / ".kanban" / "workflows").mkdir(parents=True)
    (tmp_path / "kanban-work" / "features").mkdir(parents=True)
    KanbanConfig(
        theme="software",
        paths=PathConfig(root="kanban-work/", scan_paths=["kanban-work/features/"]),
    ).save(tmp_path / ".kanban" / "config.yaml")
    return tmp_path


def _service(repo: Path) -> KanbanService:
    return KanbanService(KanbanConfig.load(repo / ".kanban" / "config.yaml"), repo)


class TestServiceNonMapping:
    @pytest.mark.parametrize("case", IDS, ids=IDS)
    def test_hooks_file_no_attribute_error(
        self, repo: Path, case: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        (repo / ".kanban" / "hooks" / "kanban-hooks.yurtle.md").write_text(NON_MAPPING[case])
        with caplog.at_level(logging.DEBUG):
            svc = _service(repo)
        assert not svc._hook_engine.is_configured
        assert _attr_error_records(caplog) == []

    @pytest.mark.parametrize("case", IDS, ids=IDS)
    def test_get_workflow_no_attribute_error(
        self, repo: Path, case: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        (repo / ".kanban" / "workflows" / "bad.yurtle.md").write_text(NON_MAPPING[case])
        (repo / ".kanban" / "workflows" / "feature.yurtle.md").write_text(VALID_WORKFLOW)
        svc = _service(repo)
        with caplog.at_level(logging.DEBUG):
            wf = svc.get_workflow("feature")
        assert wf is not None and wf.id == "feature-workflow"
        assert _attr_error_records(caplog) == []
