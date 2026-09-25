# ruff: noqa: F811  -- pytest fixtures imported from the #365 test module are re-bound as args
"""Issue #381 — theme wording follow-ups from the review of PR #379 (#365).

- ``changelog.d/365.md`` says the fall-through comes "with one warning ("is empty")",
  but a theme emptied by ``_drop_bad_sections`` gets one warning per dropped section
  plus "has nothing left once its bad sections are ignored". **Do:** "with a warning".
- A symlink loop (``software.yaml -> software.yaml``) warns "is a symlink to a
  missing file". **Do:** "is a symlink that can't be followed" (covers both a loop
  and a dangling link).

Decided behaviour:

1. A repo-local ``software.yaml`` that is a symlink loop, or a dangling symlink, logs
   one warning naming the file that says it "is a symlink that can't be followed";
   loop and dangling get the same wording. The lookup still falls through to the
   built-in. Skipped where symlinks aren't supported.
2. ``changelog.d/365.md`` no longer claims "with one warning", still mentions
   "is empty" and "#365", and still matches ``changelog.d/README.md``'s format
   (a section line, then a bullet).
3. Controls: the #365 tests stay green (run alongside); a normal file override wins.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
import yaml

from tests.issues.test_365_empty_theme_fallthrough import (  # noqa: F401 (fixtures)
    BUILTIN,
    VALID_OVERRIDE,
    _builtin_software,
    _clean_theme_cache,
    _repo,
    _theme_file,
    _warnings,
    warnings_log,
)
from yurtle_kanban import config as config_mod

WORDING = "is a symlink that can't be followed"
OLD_WORDING = "missing file"

REPO_ROOT = Path(__file__).resolve().parents[2]
FRAGMENT = REPO_ROOT / "changelog.d" / "365.md"
SECTIONS = ("Added", "Changed", "Deprecated", "Removed", "Fixed", "Security")
SECTION_LINE = re.compile(r"<!-- section: (\w+) -->")


def _symlink(link: Path, target: Path | str) -> Path:
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError) as e:
        pytest.skip(f"symlinks unavailable: {e}")
    return link


def _loop(repo: Path, name: str) -> Path:
    """``.kanban/themes/<name>.yaml -> <name>.yaml`` (points at itself)."""
    link = _theme_file(repo, name)
    _symlink(link, link.name)
    assert link.is_symlink() and not link.exists()
    return link


def _dangling(repo: Path, name: str) -> Path:
    """``.kanban/themes/<name>.yaml`` pointing at a file that doesn't exist."""
    link = _theme_file(repo, name)
    _symlink(link, repo / "no-such-target.yaml")
    assert link.is_symlink() and not link.exists()
    return link


MAKERS = {"loop": _loop, "dangling": _dangling}


def _naming(caplog: pytest.LogCaptureFixture, link: Path) -> list[str]:
    """Warnings that name ``link``. Unlike #365's helper this never resolves it:
    resolving a loop raises, and the link itself is what the warning must name."""
    forms = {str(link), str(link.absolute())}
    return [m for m in _warnings(caplog) if any(f in m for f in forms)]


# ---------------------------------------------------------------------------
# 1. An unfollowable symlink: one warning, "can't be followed", falls through
# ---------------------------------------------------------------------------


class TestUnfollowableSymlink:
    @pytest.mark.parametrize("kind", list(MAKERS))
    def test_falls_through_to_builtin(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
    ) -> None:
        repo = _repo(tmp_path / "repo")
        MAKERS[kind](repo, BUILTIN)
        monkeypatch.chdir(repo)
        assert config_mod._load_builtin_theme(BUILTIN, repo) == _builtin_software()

    @pytest.mark.parametrize("kind", list(MAKERS))
    def test_one_warning_says_cant_be_followed(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        kind: str,
        warnings_log: pytest.LogCaptureFixture,
    ) -> None:
        repo = _repo(tmp_path / "repo")
        link = MAKERS[kind](repo, BUILTIN)
        monkeypatch.chdir(repo)
        config_mod._load_builtin_theme(BUILTIN, repo)
        named = _naming(warnings_log, link)
        assert len(named) == 1, _warnings(warnings_log)
        assert WORDING in named[0], named
        assert OLD_WORDING not in named[0], named

    def test_loop_and_dangling_same_wording(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        warnings_log: pytest.LogCaptureFixture,
    ) -> None:
        texts = {}
        for kind, make in MAKERS.items():
            config_mod._theme_cache.clear()
            warnings_log.clear()
            repo = _repo(tmp_path / kind)
            link = make(repo, BUILTIN)
            monkeypatch.chdir(repo)
            config_mod._load_builtin_theme(BUILTIN, repo)
            named = _naming(warnings_log, link)
            assert len(named) == 1, _warnings(warnings_log)
            texts[kind] = named[0].replace(str(link), "<FILE>")
        assert texts["loop"] == texts["dangling"], texts
        assert WORDING in texts["loop"], texts


# ---------------------------------------------------------------------------
# 2. changelog.d/365.md: no "with one warning", format intact
# ---------------------------------------------------------------------------


class TestChangelogFragment:
    def test_no_one_warning_claim(self) -> None:
        text = FRAGMENT.read_text()
        assert "with one warning" not in " ".join(text.split()), text

    def test_still_mentions_is_empty_and_issue(self) -> None:
        text = FRAGMENT.read_text()
        assert "is empty" in text, text
        assert "#365" in text, text

    def test_matches_readme_format(self) -> None:
        lines = FRAGMENT.read_text().splitlines()
        assert lines, "empty fragment"
        m = SECTION_LINE.fullmatch(lines[0].strip())
        assert m and m.group(1) in SECTIONS, lines[0]
        assert any(line.startswith("- ") for line in lines[1:]), lines


# ---------------------------------------------------------------------------
# 3. Controls
# ---------------------------------------------------------------------------


class TestControls:
    def test_valid_file_override_wins(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        warnings_log: pytest.LogCaptureFixture,
    ) -> None:
        repo = _repo(tmp_path / "repo", {BUILTIN: VALID_OVERRIDE})
        monkeypatch.chdir(repo)
        assert config_mod._load_builtin_theme(BUILTIN, repo) == yaml.safe_load(VALID_OVERRIDE)
        assert not _warnings(warnings_log), _warnings(warnings_log)

    def test_symlink_to_valid_file_wins(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        warnings_log: pytest.LogCaptureFixture,
    ) -> None:
        """A followable symlink is a normal override, with no warning."""
        repo = _repo(tmp_path / "repo")
        target = repo / "real-software.yaml"
        target.write_text(VALID_OVERRIDE)
        _symlink(_theme_file(repo, BUILTIN), target)
        monkeypatch.chdir(repo)
        assert config_mod._load_builtin_theme(BUILTIN, repo) == yaml.safe_load(VALID_OVERRIDE)
        assert not _warnings(warnings_log), _warnings(warnings_log)
