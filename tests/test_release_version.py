"""Release tags must agree with both package version declarations."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/check_release_version.py"
SPEC = importlib.util.spec_from_file_location("check_release_version", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
check_release_version = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check_release_version)


def _write_versions(root: Path, project_version: str, package_version: str) -> None:
    (root / "pyproject.toml").write_text(
        f'[project]\nversion = "{project_version}"\n', encoding="utf-8"
    )
    module = root / "src/yurtle_kanban/__init__.py"
    module.parent.mkdir(parents=True)
    module.write_text(f'__version__ = "{package_version}"\n', encoding="utf-8")


def test_check_release_version_accepts_matching_tag_and_package_versions(
    tmp_path: Path,
) -> None:
    _write_versions(tmp_path, "2.3.0", "2.3.0")

    check_release_version.check_release_version(tmp_path, "v2.3.0")


def test_check_release_version_rejects_a_mismatched_release_tag(
    tmp_path: Path,
) -> None:
    _write_versions(tmp_path, "2.3.0", "2.3.0")

    with pytest.raises(ValueError, match="release tag"):
        check_release_version.check_release_version(tmp_path, "v2.2.0")


def test_check_release_version_rejects_mismatched_package_versions(
    tmp_path: Path,
) -> None:
    _write_versions(tmp_path, "2.3.0", "2.2.0")

    with pytest.raises(ValueError, match="does not match"):
        check_release_version.check_release_version(tmp_path, "v2.3.0")
