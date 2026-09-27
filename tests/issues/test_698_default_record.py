"""#698: whether a fetched default branch is recorded as origin/HEAD is an explicit
argument, not instance state set by an earlier call."""

from __future__ import annotations

import inspect
import subprocess

from tests.issues.test_685_stem_and_symref import _git, _world
from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.service import KanbanService


def _origin_head(local) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "symbolic-ref", "-q", "refs/remotes/origin/HEAD"],
        cwd=local, capture_output=True, text=True,
    )


def test_fetch_default_takes_record_as_a_required_keyword():
    param = inspect.signature(KanbanService._fetch_default).parameters["record"]
    assert param.kind is inspect.Parameter.KEYWORD_ONLY
    assert param.default is inspect.Parameter.empty


def test_resolve_default_says_whether_it_guessed(tmp_path):
    local = _world(tmp_path)  # the remote advertises no HEAD
    svc = KanbanService(KanbanConfig.load(local / ".kanban" / "config.yaml"), local)
    assert svc._resolve_default() == ("main", False)
    _git(tmp_path / "remote.git", "symbolic-ref", "HEAD", "refs/heads/main")
    assert svc._resolve_default() == ("main", True)


def test_no_instance_flag_decides_recording(tmp_path):
    local = _world(tmp_path)
    svc = KanbanService(KanbanConfig.load(local / ".kanban" / "config.yaml"), local)
    svc._resolve_default()  # a guess: nothing may linger from it
    assert not hasattr(svc, "_guessed_default")
    assert svc._fetch_default("main", record=True).returncode == 0
    assert _origin_head(local).stdout.strip() == "refs/remotes/origin/main"
