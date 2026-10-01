#!/usr/bin/env python3
"""Check that a published release tag matches both package version sources."""

from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    try:  # Python 3.10: the dev extra's tomli (#555) serves the tests
        import tomli as tomllib
    except ModuleNotFoundError:
        # publish.yml runs this on Python 3.11 before installing anything, so neither
        # source can be missing there; anywhere else, say what's needed (#1194)
        print(
            "check_release_version.py needs Python 3.11+, or tomli on 3.10 "
            "(publish.yml pins 3.11)",
            file=sys.stderr,
        )
        raise SystemExit(2) from None


def _package_module_version(path: Path) -> str:
    module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    values: list[ast.expr | None] = []
    for statement in module.body:
        if isinstance(statement, ast.Assign):
            targets = statement.targets
            value: ast.expr | None = statement.value
        elif isinstance(statement, ast.AnnAssign):
            targets = [statement.target]
            value = statement.value
        else:
            continue

        if any(isinstance(target, ast.Name) and target.id == "__version__" for target in targets):
            values.append(value)

    if len(values) != 1:
        raise ValueError(f"expected one __version__ assignment in {path}")
    if values[0] is None:
        raise ValueError(f"__version__ in {path} must have a value")

    version = ast.literal_eval(values[0])
    if not isinstance(version, str):
        raise ValueError(f"__version__ in {path} must be a string literal")
    return version


def validate_release_versions(release_tag: str, project_version: str, package_version: str) -> None:
    if package_version != project_version:
        raise ValueError(
            f"pyproject.toml version {project_version!r} does not match "
            f"__version__ {package_version!r}"
        )

    expected_tag = f"v{project_version}"
    if release_tag != expected_tag:
        raise ValueError(
            f"release tag {release_tag!r} does not match expected tag {expected_tag!r}"
        )


def check_release_version(repo_root: Path, release_tag: str) -> None:
    metadata = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))
    project_version = metadata["project"]["version"]
    if not isinstance(project_version, str):
        raise ValueError("pyproject.toml project.version must be a string")

    package_version = _package_module_version(repo_root / "src/yurtle_kanban/__init__.py")
    validate_release_versions(release_tag, project_version, package_version)


def main() -> int:
    release_tag = os.environ.get("RELEASE_TAG")
    if not release_tag:
        print("RELEASE_TAG is required", file=sys.stderr)
        return 1

    repo_root = Path(__file__).resolve().parent.parent
    try:
        check_release_version(repo_root, release_tag)
    except (OSError, KeyError, SyntaxError, TypeError, ValueError) as error:
        print(f"Release version check failed: {error}", file=sys.stderr)
        return 1

    print(f"Release tag {release_tag} matches package metadata")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
