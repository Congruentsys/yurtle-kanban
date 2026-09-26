"""Log messages are plain text (#505).

Warnings interpolate text from repo files (item titles, hook definitions). If
package code ever logged through a Rich handler with markup on, a `[bold]` or
`[link=...]` in a cloned repo's file would be interpreted, not printed. The
decision on #505: no per-message escaping; instead pin that no package code
constructs a `RichHandler` unless it passes `markup=False` explicitly, and that
a markup-looking warning comes out verbatim.
"""

from __future__ import annotations

import ast
import importlib
import io
import logging
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src" / "yurtle_kanban"
PREFIX = "yurtle-kanban"


def rich_handler_violations(source: str, filename: str = "<string>") -> list[str]:
    """Every `RichHandler(...)` call in `source` that doesn't pass `markup=False`.

    Matches the bare name, any `from rich.logging import RichHandler as X` alias,
    and any attribute access ending in `.RichHandler` (`rich.logging.RichHandler`,
    `rl.RichHandler`). A `**kwargs` call can't be proved safe, so it's flagged.
    """
    tree = ast.parse(source, filename)
    names = {"RichHandler"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "RichHandler":
                    names.add(alias.asname or alias.name)
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_rich = (isinstance(func, ast.Name) and func.id in names) or (
            isinstance(func, ast.Attribute) and func.attr == "RichHandler"
        )
        if not is_rich:
            continue
        markup = [kw for kw in node.keywords if kw.arg == "markup"]
        splat = any(kw.arg is None for kw in node.keywords)
        safe = (
            len(markup) == 1
            and isinstance(markup[0].value, ast.Constant)
            and markup[0].value.value is False
            and not splat
        )
        if not safe:
            found.append(f"{filename}:{node.lineno}: {ast.unparse(node)}")
    return found


# -- the checker bites ---------------------------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        "from rich.logging import RichHandler\nh = RichHandler(markup=True)\n",
        "from rich.logging import RichHandler\nh = RichHandler()\n",
        "from rich.logging import RichHandler as RH\nh = RH(rich_tracebacks=True)\n",
        "import rich.logging\nh = rich.logging.RichHandler(markup=True)\n",
        "import rich.logging as rl\nh = rl.RichHandler()\n",
        "from rich.logging import RichHandler\nkw = {}\nh = RichHandler(markup=False, **kw)\n",
        "from rich.logging import RichHandler\nflag = False\nh = RichHandler(markup=flag)\n",
        "import logging\nfrom rich.logging import RichHandler\n"
        "logging.basicConfig(handlers=[RichHandler(markup=True)])\n",
        # #518: any other reference to RichHandler, not just a call
        "import logging.config\nlogging.config.dictConfig({'version': 1, 'handlers': "
        "{'h': {'class': 'rich.logging.RichHandler'}}})\n",
        "from rich.logging import RichHandler\nhandler_cls = RichHandler\n",
        "import rich.logging\nh = getattr(rich.logging, 'RichHandler')()\n",
        "import rich.logging\nhandler_cls = rich.logging.RichHandler\n",
        # #518: per-record markup via `extra`
        "import logging\nlogging.getLogger('yurtle-kanban').warning('x', extra={'markup': True})\n",
    ],
)
def test_checker_flags_markup_capable_rich_handler(source: str) -> None:
    assert rich_handler_violations(source, "planted.py")


@pytest.mark.parametrize(
    "source",
    [
        "from rich.logging import RichHandler\nh = RichHandler(markup=False)\n",
        "import rich.logging\nh = rich.logging.RichHandler(markup=False, show_path=False)\n",
        "import logging\nlogging.basicConfig(level=logging.INFO)\n",
        "from rich.console import Console\nConsole().print('[bold]x[/bold]')\n",
    ],
)
def test_checker_passes_plain_handlers(source: str) -> None:
    assert rich_handler_violations(source, "planted.py") == []


def test_checker_flags_a_planted_module_file(tmp_path: Path) -> None:
    planted = tmp_path / "planted.py"
    planted.write_text(
        "import logging\nfrom rich.logging import RichHandler\n"
        "logging.getLogger('yurtle-kanban').addHandler(RichHandler(markup=True))\n"
    )
    (hit,) = rich_handler_violations(planted.read_text(), str(planted))
    assert "planted.py:3" in hit


# -- static: package source ----------------------------------------------------


def test_no_package_module_builds_a_markup_rich_handler() -> None:
    modules = sorted(SRC.rglob("*.py"))
    assert modules, SRC
    violations = [
        v for path in modules for v in rich_handler_violations(path.read_text(), str(path))
    ]
    assert violations == [], "log messages must be plain text (#505):\n" + "\n".join(violations)


# -- runtime -------------------------------------------------------------------


def _import_package() -> None:
    importlib.import_module("yurtle_kanban")
    importlib.import_module("yurtle_kanban.cli")
    try:
        importlib.import_module("yurtle_kanban.mcp.server")
    except ImportError:  # optional `mcp` extra
        pass


def _markup_rich_handlers() -> list[str]:
    try:
        from rich.logging import RichHandler
    except ImportError:
        return []
    loggers = [logging.getLogger()] + [
        lg
        for name, lg in logging.Logger.manager.loggerDict.items()
        if name.startswith(PREFIX) and isinstance(lg, logging.Logger)
    ]
    return [
        f"{lg.name}: {h!r}"
        for lg in loggers
        for h in lg.handlers
        if isinstance(h, RichHandler) and getattr(h, "markup", True)
    ]


def test_no_markup_rich_handler_installed_after_import() -> None:
    _import_package()
    assert _markup_rich_handlers() == []


def test_markup_looking_warning_is_emitted_verbatim(caplog: pytest.LogCaptureFixture) -> None:
    _import_package()
    logger = logging.getLogger(f"{PREFIX}.test505")
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    try:
        with caplog.at_level(logging.WARNING, logger=logger.name):
            logger.warning("title %s", "[bold]x[/bold] [link=https://e.invalid]y[/link]")
    finally:
        logger.removeHandler(handler)
    expected = "title [bold]x[/bold] [link=https://e.invalid]y[/link]"
    assert stream.getvalue() == expected + "\n"
    assert [r.getMessage() for r in caplog.records if r.name == logger.name] == [expected]
