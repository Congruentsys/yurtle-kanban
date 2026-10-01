"""Release tags must agree with both package version declarations."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/check_release_version.py"



def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_release_version", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def check_release_version() -> ModuleType:
    return _load()


def _write_versions(root: Path, project_version: str, package_version: str) -> None:
    (root / "pyproject.toml").write_text(
        f'[project]\nversion = "{project_version}"\n', encoding="utf-8"
    )
    module = root / "src/yurtle_kanban/__init__.py"
    module.parent.mkdir(parents=True)
    module.write_text(f'__version__ = "{package_version}"\n', encoding="utf-8")


def test_check_release_version_accepts_matching_tag_and_package_versions(
    tmp_path: Path, check_release_version: ModuleType
) -> None:
    _write_versions(tmp_path, "2.3.0", "2.3.0")

    check_release_version.check_release_version(tmp_path, "v2.3.0")


def test_check_release_version_rejects_a_mismatched_release_tag(
    tmp_path: Path, check_release_version: ModuleType
) -> None:
    _write_versions(tmp_path, "2.3.0", "2.3.0")

    with pytest.raises(
        ValueError, match=r"^release tag 'v2\.2\.0' does not match expected tag 'v2\.3\.0'$"
    ):
        check_release_version.check_release_version(tmp_path, "v2.2.0")


def test_check_release_version_rejects_mismatched_package_versions(
    tmp_path: Path, check_release_version: ModuleType
) -> None:
    # The tag is fine here; pin the pyproject/__version__ wording
    # itself, not the "does not match" both messages share (#1194).
    _write_versions(tmp_path, "2.3.0", "2.2.0")

    with pytest.raises(
        ValueError,
        match=r"^pyproject\.toml version '2\.3\.0' does not match __version__ '2\.2\.0'$",
    ):
        check_release_version.check_release_version(tmp_path, "v2.3.0")


def test_package_mismatch_is_reported_even_when_the_tag_also_mismatches(
    tmp_path: Path, check_release_version: ModuleType
) -> None:
    _write_versions(tmp_path, "2.3.0", "2.2.0")

    with pytest.raises(ValueError, match=r"^pyproject\.toml version .* __version__ "):
        check_release_version.check_release_version(tmp_path, "v9.9.9")


@pytest.mark.parametrize("tag", [None, ""], ids=["unset", "empty"])
def test_main_rejects_an_unset_or_empty_release_tag(
    tag: str | None,
    check_release_version: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    if tag is None:
        monkeypatch.delenv("RELEASE_TAG", raising=False)
    else:
        monkeypatch.setenv("RELEASE_TAG", tag)

    assert check_release_version.main() == 1
    captured = capsys.readouterr()
    assert "RELEASE_TAG is required" in captured.err
    assert captured.out == ""


def test_script_without_tomllib_or_tomli_exits_2_saying_what_it_needs(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # neither tomllib nor tomli: a None entry in sys.modules makes each import fail the
    # same way on any Python, so this runs everywhere (#1194)
    monkeypatch.setitem(sys.modules, "tomllib", None)
    monkeypatch.setitem(sys.modules, "tomli", None)

    with pytest.raises(SystemExit) as exit_info:
        _load()

    assert exit_info.value.code == 2
    captured = capsys.readouterr()
    assert "check_release_version.py needs Python 3.11+, or tomli on 3.10 (publish.yml pins 3.11)" in captured.err
    assert "Traceback" not in captured.err
