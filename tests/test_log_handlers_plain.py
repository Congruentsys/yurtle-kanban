"""Log messages are plain text (#505).

Warnings interpolate text from repo files (item titles, hook definitions). If
package code ever logged through a Rich handler with markup on, a `[bold]` or
`[link=...]` in a cloned repo's file would be interpreted, not printed. The
decision on #505: no per-message escaping; instead pin that no package code
constructs a `RichHandler` unless it passes `markup=False` explicitly, and that
a markup-looking warning comes out verbatim.

Accepted gaps (#530). The static scan reads source text, so it can't follow
every dynamic path. These shapes are known misses, and we don't chase them:
- `extra=` given as a variable, or passed inside `**kw`;
- `object.__setattr__(h, 'markup', _)` and `builtins.setattr(h, 'markup', _)`;
- `setattr(h, k, _)` where `k` is a variable holding `'markup'`;
- `h.__dict__.update(markup=_)`.
src has none of them today. Each gap is pinned below as a strict xfail, so
closing one later is noticed and the xfail is removed.

What backstops them differs:
- The four attribute gaps (`object.__setattr__`, `builtins.setattr`, `setattr`
  with a variable key, `__dict__.update`) turn markup on in the handler itself.
  The runtime check (`test_no_markup_rich_handler_installed_after_import`)
  catches that, but only for a handler attached to the root or a
  `yurtle-kanban*` logger by the time the package, its CLI and its MCP server
  are imported. One switched on inside a command body, after import, is not
  seen.
- The two `extra=` gaps are NOT backstopped. Rich lets the record override the
  handler (`getattr(record, "markup", self.markup)`), so a `RichHandler(markup=False)`
  still renders markup for a record carrying `markup=True`, and the runtime check
  sees nothing (pinned by `test_record_markup_overrides_a_markup_false_handler`).
  Today the only protection is that src has no RichHandler reference at all. If a
  `markup=False` handler is ever added to src, these gaps are live.
"""

from __future__ import annotations

import ast
import importlib
import io
import logging
from pathlib import Path

import pytest
from rich.console import Console
from rich.logging import RichHandler

SRC = Path(__file__).resolve().parent.parent / "src" / "yurtle_kanban"
PREFIX = "yurtle-kanban"


def _is_safe_rich_call(node: ast.Call) -> bool:
    """`RichHandler(..., markup=False, ...)` with a literal `False` and no `**kwargs`."""
    markup = [kw for kw in node.keywords if kw.arg == "markup"]
    return (
        len(markup) == 1
        and isinstance(markup[0].value, ast.Constant)
        and markup[0].value.value is False
        and not any(kw.arg is None for kw in node.keywords)
    )


def _is_markup_key(node: ast.AST | None) -> bool:
    return isinstance(node, ast.Constant) and node.value == "markup"


def rich_handler_violations(source: str, filename: str = "<string>") -> list[str]:
    """Every way `source` could get Rich markup into a log line (#505, #518, #524).

    Flagged:
    - any reference to `RichHandler` other than a direct call passing a literal
      `markup=False` (no `**kwargs`): the bare name or any
      `from rich.logging import RichHandler as X` alias, an attribute ending in
      `.RichHandler`, or a string constant containing it — dictConfig's
      `'rich.logging.RichHandler'`, `getattr(rich.logging, 'RichHandler')`, and
      docstrings and other prose too, as a tripwire. The import that makes a safe
      call possible isn't itself a reference;
    - any reference to `fileConfig`, which can name any handler class from a file;
    - turning markup on after construction: a store to a `.markup` attribute,
      `setattr(_, 'markup', _)`, or a `[...]['markup'] = _` store such as
      `h.__dict__['markup'] = _`;
    - `'markup'` as a dict literal key anywhere (so `extra={'markup': _}`), and
      `extra=dict(markup=_)`.
    """
    tree = ast.parse(source, filename)
    names = {"RichHandler"}
    file_config = {"fileConfig"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "RichHandler":
                    names.add(alias.asname or alias.name)
                elif alias.name == "fileConfig":
                    file_config.add(alias.asname or alias.name)

    def refers(node: ast.AST) -> bool:
        return (
            (isinstance(node, ast.Name) and node.id in names)
            or (isinstance(node, ast.Attribute) and node.attr == "RichHandler")
            or (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and "RichHandler" in node.value
            )
        )

    def sets_markup(node: ast.AST) -> bool:
        if isinstance(node, ast.Attribute):
            return node.attr == "markup" and isinstance(node.ctx, ast.Store)
        if isinstance(node, ast.Subscript):
            return isinstance(node.ctx, ast.Store) and _is_markup_key(node.slice)
        if isinstance(node, ast.Dict):
            return any(_is_markup_key(k) for k in node.keys)
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "setattr" and len(node.args) >= 2:
                return _is_markup_key(node.args[1])
            return any(
                kw.arg == "extra"
                and isinstance(kw.value, ast.Call)
                and isinstance(kw.value.func, ast.Name)
                and kw.value.func.id == "dict"
                and any(k.arg == "markup" for k in kw.value.keywords)
                for kw in node.keywords
            )
        return False

    def file_config_ref(node: ast.AST) -> bool:
        return (isinstance(node, ast.Name) and node.id in file_config) or (
            isinstance(node, ast.Attribute) and node.attr == "fileConfig"
        )

    found: list[str] = []
    covered: set[int] = set()  # references already reported, or allowed, via their call
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, (ast.Name, ast.Attribute)) and refers(func):
            covered.update(id(n) for n in ast.walk(func))
            if not _is_safe_rich_call(node):
                found.append(f"{filename}:{node.lineno}: {ast.unparse(node)}")
    for node in ast.walk(tree):
        if id(node) in covered:
            continue
        if refers(node) or file_config_ref(node) or sets_markup(node):
            covered.update(id(n) for n in ast.walk(node))
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
        # #524: turning markup on after construction
        "h = make_handler()\nh.markup = True\n",
        "h = make_handler()\nh.markup |= True\n",
        "h = make_handler()\nsetattr(h, 'markup', True)\n",
        "h = make_handler()\nh.__dict__['markup'] = True\n",
        # #524: markup via extra=dict(...), or any 'markup' dict key
        "import logging\nlogging.getLogger('yurtle-kanban').warning('x', extra=dict(markup=True))\n",
        "opts = {'markup': True}\n",
        "opts = {}\nopts['markup'] = True\n",
        # #524: fileConfig can name any handler class from an ini file
        "import logging.config\nlogging.config.fileConfig('logging.ini')\n",
        "from logging.config import fileConfig\nfileConfig('logging.ini')\n",
        "from logging.config import fileConfig as fc\nfc('logging.ini')\n",
        # prose counts: a docstring naming the handler is flagged too (a tripwire)
        '"""Logs go through a RichHandler."""\n',
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
        # #524 controls
        "h = make_handler()\nprint(h.markup)\n",
        "h = make_handler()\nattr = 'level'\nsetattr(h, attr, 10)\n",
        "h = make_handler()\nh.__dict__['level'] = 10\n",
        "import logging\nlogging.getLogger('yurtle-kanban').warning('x', extra=dict(user=1))\n",
        "import logging\nlogging.getLogger('yurtle-kanban').warning('x', extra={'user': 1})\n",
        "opts = {'level': 'markup'}\nopts['level'] = 'markup'\n",
        "import logging.config\nlogging.config.dictConfig({'version': 1})\n",
        "from rich.text import Text\nt = Text.from_markup('[b]x[/b]')\n",
        '"""Warnings are plain text, never Rich markup."""\n',
    ],
)
def test_checker_passes_plain_handlers(source: str) -> None:
    assert rich_handler_violations(source, "planted.py") == []


@pytest.mark.xfail(strict=True, raises=AssertionError, reason="accepted gap #530")
@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            "import logging\ne = dict(markup=True)\n"
            "logging.getLogger('yurtle-kanban').warning('x', extra=e)\n",
            id="extra-variable",
        ),
        pytest.param(
            "import logging\nkw = {'extra': dict(markup=True)}\n"
            "logging.getLogger('yurtle-kanban').warning('x', **kw)\n",
            id="extra-via-kwargs",
        ),
        pytest.param(
            "h = make_handler()\nobject.__setattr__(h, 'markup', True)\n",
            id="object-setattr",
        ),
        pytest.param(
            "import builtins\nh = make_handler()\nbuiltins.setattr(h, 'markup', True)\n",
            id="builtins-setattr",
        ),
        pytest.param(
            "h = make_handler()\nk = 'markup'\nsetattr(h, k, True)\n",
            id="setattr-variable-key",
        ),
        pytest.param(
            "h = make_handler()\nh.__dict__.update(markup=True)\n",
            id="dict-update",
        ),
    ],
)
def test_accepted_gap_is_still_missed(source: str) -> None:
    """Pins a known miss (#530): if the checker starts flagging this shape, the
    strict xfail turns into a failure, so move the row to the flagged list."""
    assert rich_handler_violations(source, "planted.py")


def test_record_markup_overrides_a_markup_false_handler() -> None:
    """Why the `extra=` gaps have no runtime backstop (#530): a record carrying
    `markup=True` gets markup rendered even by a `RichHandler(markup=False)`, and
    `_markup_rich_handlers()` doesn't report that handler. If Rich ever stops
    letting the record win, this fails; then revisit the module docstring."""
    out = io.StringIO()
    handler = RichHandler(
        markup=False,
        console=Console(file=out, width=200, color_system=None),
        show_time=False,
        show_path=False,
    )
    logger = logging.getLogger(f"{PREFIX}.test530")
    logger.addHandler(handler)
    logger.propagate = False
    try:
        logger.warning("plain [bold]x[/bold]")
        logger.warning("rich [bold]y[/bold]", extra=dict(markup=True))
        assert _markup_rich_handlers() == []
    finally:
        logger.removeHandler(handler)
        logger.propagate = True
    text = out.getvalue()
    assert "plain [bold]x[/bold]" in text  # the handler alone leaves markup literal
    assert "rich y" in text and "[bold]y" not in text  # the record's markup=True wins


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
    assert violations == [], (
        "log messages must be plain text (#505). Any mention of RichHandler counts,"
        " docstrings and other prose in strings included (a tripwire, #524): reword it"
        " or pass markup=False.\n" + "\n".join(violations)
    )


# -- runtime -------------------------------------------------------------------


def _import_package() -> None:
    """Import the package, its CLI and its MCP server, all unconditionally.

    `yurtle_kanban.mcp.server` uses only the standard library and yurtle_kanban
    modules, never the `mcp` package, so there is nothing optional to skip (#551).
    If it ever starts depending on `mcp`, this import fails loudly, as it should.
    """
    importlib.import_module("yurtle_kanban")
    importlib.import_module("yurtle_kanban.cli")
    importlib.import_module("yurtle_kanban.mcp.server")


def _markup_rich_handlers() -> list[str]:
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
    """The #215/#234 record factory leaves `[...]` alone: a markup-looking warning
    from a package logger reaches a plain handler verbatim. This pins only the
    factory, not the behaviour of any package handler; the static scan above is
    what keeps markup-interpreting handlers out of the package."""
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
