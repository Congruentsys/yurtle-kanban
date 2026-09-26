"""Packaging extras stay honest about the MCP server (#555).

Captain ruling (option A): the bundled MCP server is a hand-rolled JSON-RPC
loop over stdio and never imports the third-party ``mcp`` distribution, so the
``[mcp]`` extra must not pull it in. The extra is kept (empty) so that
``pip install yurtle-kanban[mcp]`` still resolves, and the
``yurtle-kanban-mcp`` console script keeps pointing at the server.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"
SRC_PKG = REPO_ROOT / "src" / "yurtle_kanban"


def _pyproject() -> dict:
    with PYPROJECT.open("rb") as fh:
        return tomllib.load(fh)


def _extras() -> dict[str, list[str]]:
    return _pyproject()["project"]["optional-dependencies"]


def _requirement_name(req: str) -> str:
    """Normalised distribution name of a PEP 508 requirement string."""
    match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", req)
    assert match, f"unparseable requirement: {req!r}"
    return re.sub(r"[-_.]+", "-", match.group(1)).lower()


def _self_extras(req: str) -> list[str]:
    """Extras named in a self-referencing requirement like ``yurtle-kanban[a,b]``."""
    match = re.match(r"\s*[A-Za-z0-9._-]+\s*\[([^\]]*)\]", req)
    if not match:
        return []
    return [e.strip() for e in match.group(1).split(",") if e.strip()]


def test_mcp_extra_exists_and_is_empty() -> None:
    extras = _extras()
    assert "mcp" in extras, "the [mcp] extra must remain so yurtle-kanban[mcp] resolves"
    assert extras["mcp"] == [], (
        f"[mcp] extra must be empty (the server never imports `mcp`); got {extras['mcp']!r}"
    )


def test_no_extra_requires_the_mcp_distribution() -> None:
    offenders = {
        name: req
        for name, reqs in _extras().items()
        for req in reqs
        if _requirement_name(req) == "mcp"
    }
    assert offenders == {}, f"extras still depend on the `mcp` distribution: {offenders!r}"


def test_all_extra_still_resolves_mcp() -> None:
    extras = _extras()
    all_reqs = extras["all"]
    referenced: set[str] = set()
    for req in all_reqs:
        if _requirement_name(req) == "yurtle-kanban":
            referenced.update(_self_extras(req))
    for extra in referenced:
        assert extra in extras, f"[all] references undefined extra {extra!r}"
    assert "mcp" in referenced, f"[all] should still include the mcp extra; got {all_reqs!r}"


def test_no_source_module_imports_mcp_package() -> None:
    offenders: list[str] = []
    for path in sorted(SRC_PKG.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "mcp" or alias.name.startswith("mcp."):
                        offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
            elif isinstance(node, ast.ImportFrom):
                # Relative imports (level > 0) are intra-package, not the `mcp` dist.
                mod = node.module or ""
                if node.level == 0 and (mod == "mcp" or mod.startswith("mcp.")):
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
    assert offenders == [], (
        "the [mcp] extra is empty, so no module may import the `mcp` package: "
        + ", ".join(offenders)
    )


def test_mcp_console_script_entry_point() -> None:
    scripts = _pyproject()["project"]["scripts"]
    assert scripts.get("yurtle-kanban-mcp") == "yurtle_kanban.mcp.server:run_server"
    server_src = (SRC_PKG / "mcp" / "server.py").read_text(encoding="utf-8")
    funcs = {
        n.name
        for n in ast.walk(ast.parse(server_src))
        if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
    }
    assert "run_server" in funcs, "entry point target run_server missing from mcp/server.py"
