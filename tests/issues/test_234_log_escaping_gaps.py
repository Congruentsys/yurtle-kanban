"""#234: the #215 log escaping also covers child loggers and exception text.

Decided behaviour:

1. Child loggers: a WARNING+ record logged on ANY logger under the package names
   (`yurtle-kanban.<anything>`, `yurtle-kanban-mcp.<anything>`) is escaped when it
   reaches output -- including a logger created later with a plain
   `logging.getLogger("yurtle-kanban.newmod")` that has no handler of its own and
   propagates to a handler on the parent package logger.
2. Static guard: no module in src/yurtle_kanban except `_logging.py` calls
   `logging.getLogger("yurtle-kanban...")` (third-party loggers such as rdflib's are
   allowed).
3. Exception text: `logger.exception(...)` / `exc_info=True` for an exception whose
   message carries control characters puts no raw ESC byte and no forged line on the
   output; the traceback's exception line is escaped.
4. Controls: DEBUG/INFO untouched; printable warnings unchanged; a printable
   traceback keeps its usual multi-line structure (only the exception MESSAGE's
   control characters are escaped, not the newlines between frames).

Round 2 (review of PR #248):

5. `importlib.reload(yurtle_kanban._logging)` must not make the record factory call
   itself: afterwards a package record is still escaped and a foreign logger's record
   passes through unchanged, with no RecursionError.
6. Exception notes (`add_note`) and a SyntaxError's `msg` / source `text` are escaped
   in the traceback too.
7. A bad %-format with `exc_info` still gets an escaped `record.exc_text`.
"""

from __future__ import annotations

import ast
import importlib
import io
import logging
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

import yurtle_kanban
import yurtle_kanban._logging
import yurtle_kanban.gates
import yurtle_kanban.hooks
import yurtle_kanban.query
import yurtle_kanban.service
import yurtle_kanban.workflow

PAYLOAD = "x\x1b[2J\nFORGED"
SRC_DIR = Path(yurtle_kanban.__file__).resolve().parent


@pytest.fixture(autouse=True, scope="module")
def _import_mcp_server() -> None:
    """The "yurtle-kanban-mcp" logger is created when the MCP server module loads."""
    pytest.importorskip("yurtle_kanban.mcp.server")


def _capture(logger: logging.Logger, fmt: str) -> tuple[logging.Handler, io.StringIO]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter(fmt))
    handler.setLevel(logging.DEBUG)
    return handler, stream


@pytest.fixture
def on_logger(request: pytest.FixtureRequest) -> Iterator[tuple[logging.Logger, io.StringIO]]:
    """A capturing handler (given format) on the named logger; propagation off."""
    name, fmt = request.param
    logger = logging.getLogger(name)
    handler, stream = _capture(logger, fmt)
    old_level, old_propagate = logger.level, logger.propagate
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    try:
        yield logger, stream
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)
        logger.propagate = old_propagate


# --- 1. child loggers ------------------------------------------------------------

CHILD_CASES = [
    ("yurtle-kanban", "yurtle-kanban.zz_test_child"),
    ("yurtle-kanban", "yurtle-kanban.zz_test_pkg.deeper"),
    ("yurtle-kanban-mcp", "yurtle-kanban-mcp.zz_test_child"),
]


@pytest.fixture(params=CHILD_CASES, ids=lambda c: c[1])
def child(request: pytest.FixtureRequest) -> Iterator[tuple[logging.Logger, io.StringIO]]:
    """A plain-getLogger child with NO handler; the capture sits on the parent."""
    parent_name, child_name = request.param
    parent = logging.getLogger(parent_name)
    handler, stream = _capture(parent, "%(message)s")
    old_level, old_propagate = parent.level, parent.propagate
    parent.addHandler(handler)
    parent.setLevel(logging.DEBUG)
    parent.propagate = False
    child_logger = logging.getLogger(child_name)
    assert not child_logger.handlers
    try:
        yield child_logger, stream
    finally:
        parent.removeHandler(handler)
        parent.setLevel(old_level)
        parent.propagate = old_propagate


def _message(stream: io.StringIO) -> str:
    text = stream.getvalue()
    assert text.endswith("\n"), repr(text)
    return text[:-1]


class TestChildLoggers:
    def test_child_warning_escaped(self, child: tuple[logging.Logger, io.StringIO]) -> None:
        logger, stream = child
        logger.warning(PAYLOAD)
        message = _message(stream)
        assert "\x1b" not in message and "\n" not in message, repr(message)
        assert message == "x\\x1b[2J\\nFORGED"

    def test_child_percent_args_escaped(self, child: tuple[logging.Logger, io.StringIO]) -> None:
        logger, stream = child
        logger.error("title=%s", PAYLOAD)
        assert _message(stream) == "title=x\\x1b[2J\\nFORGED"

    def test_child_debug_untouched(self, child: tuple[logging.Logger, io.StringIO]) -> None:
        logger, stream = child
        logger.setLevel(logging.DEBUG)
        try:
            logger.debug(PAYLOAD)
        finally:
            logger.setLevel(logging.NOTSET)
        assert _message(stream) == PAYLOAD

    def test_child_printable_warning_unchanged(
        self, child: tuple[logging.Logger, io.StringIO]
    ) -> None:
        logger, stream = child
        logger.warning("plain café warning")
        assert _message(stream) == "plain café warning"


# --- 2. static guard -------------------------------------------------------------


def _package_getlogger_calls() -> list[str]:
    hits: list[str] = []
    for path in sorted(SRC_DIR.rglob("*.py")):
        if path.name == "_logging.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            arg = node.args[0]
            if (
                name == "getLogger"
                and isinstance(arg, ast.Constant)
                and isinstance(arg.value, str)
                and arg.value.startswith("yurtle-kanban")
            ):
                hits.append(f"{path.relative_to(SRC_DIR)}:{node.lineno} {arg.value}")
    return hits


class TestStaticGuard:
    def test_no_plain_getlogger_for_package_loggers(self) -> None:
        assert _package_getlogger_calls() == []


# --- 3. exception text -----------------------------------------------------------

MCP_TRACEBACK = ("yurtle-kanban-mcp", "%(levelname)s %(name)s: %(message)s")


def _raise(message: str) -> None:
    raise ValueError(message)


def _assert_no_forgery(text: str) -> None:
    assert "\x1b" not in text, repr(text)
    lines = text.splitlines()
    assert not any(line.startswith("FORGED") for line in lines), repr(text)
    assert any("ValueError: bad" in line for line in lines), repr(text)


@pytest.mark.parametrize("on_logger", [MCP_TRACEBACK], indirect=True)
class TestExceptionText:
    def test_exception_message_escaped(self, on_logger: tuple[logging.Logger, io.StringIO]) -> None:
        logger, stream = on_logger
        try:
            _raise("bad\x1b[2J\nFORGED")
        except ValueError:
            logger.exception("failed")
        _assert_no_forgery(stream.getvalue())

    def test_warning_exc_info_escaped(self, on_logger: tuple[logging.Logger, io.StringIO]) -> None:
        logger, stream = on_logger
        try:
            _raise("bad\x1b[2J\nFORGED")
        except ValueError:
            logger.warning("w", exc_info=True)
        _assert_no_forgery(stream.getvalue())

    def test_printable_traceback_keeps_structure(
        self, on_logger: tuple[logging.Logger, io.StringIO]
    ) -> None:
        logger, stream = on_logger
        try:
            _raise("plain failure")
        except ValueError:
            logger.exception("failed")
        lines = stream.getvalue().splitlines()
        assert lines[0] == "ERROR yurtle-kanban-mcp: failed"
        assert lines[1] == "Traceback (most recent call last):"
        assert sum(line.lstrip().startswith("File ") for line in lines) >= 2
        assert lines[-1] == "ValueError: plain failure"


# --- 4. controls on the package loggers themselves -------------------------------


@pytest.mark.parametrize(
    "on_logger",
    [(name, "%(message)s") for name in ("yurtle-kanban", "yurtle-kanban-mcp")],
    indirect=True,
)
class TestControls:
    def test_debug_untouched(self, on_logger: tuple[logging.Logger, io.StringIO]) -> None:
        logger, stream = on_logger
        logger.debug(PAYLOAD)
        assert _message(stream) == PAYLOAD

    def test_info_untouched(self, on_logger: tuple[logging.Logger, io.StringIO]) -> None:
        logger, stream = on_logger
        logger.info(PAYLOAD)
        assert _message(stream) == PAYLOAD

    def test_printable_warning_unchanged(
        self, on_logger: tuple[logging.Logger, io.StringIO]
    ) -> None:
        logger, stream = on_logger
        logger.warning("plain café warning %s", 42)
        assert _message(stream) == "plain café warning 42"


# --- 5. reloading _logging (round 2) --------------------------------------------


class _Records(logging.Handler):
    """Stores records unformatted, so a test can inspect what the factory built."""

    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def records() -> Iterator[Callable[[str], _Records]]:
    """`attach(name)` puts a storing handler on a logger; all undone afterwards."""
    attached: list[tuple[logging.Logger, _Records, int, bool]] = []

    def attach(name: str) -> _Records:
        logger = logging.getLogger(name)
        handler = _Records()
        attached.append((logger, handler, logger.level, logger.propagate))
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        logger.propagate = False
        return handler

    try:
        yield attach
    finally:
        for logger, handler, level, propagate in attached:
            logger.removeHandler(handler)
            logger.setLevel(level)
            logger.propagate = propagate


@pytest.fixture
def reloaded_logging() -> Iterator[None]:
    """Reload `_logging`; restore the record factory AND the module's globals after.

    Restoring the globals matters: a reload rebinds them in place, and the factory
    still installed looks names up there, so without this a broken reload would
    leak into every later test.
    """
    module = yurtle_kanban._logging
    saved_factory = logging.getLogRecordFactory()
    saved_globals = dict(vars(module))
    try:
        importlib.reload(module)
        yield
    finally:
        logging.setLogRecordFactory(saved_factory)
        vars(module).clear()
        vars(module).update(saved_globals)


@pytest.mark.usefixtures("reloaded_logging")
class TestReload:
    def test_package_record_still_escaped(self, records: Callable[[str], _Records]) -> None:
        store = records("yurtle-kanban.zz_reload")
        logging.getLogger("yurtle-kanban.zz_reload").warning(PAYLOAD)
        [record] = store.records
        assert record.getMessage() == "x\\x1b[2J\\nFORGED"

    def test_foreign_record_passes_through(self, records: Callable[[str], _Records]) -> None:
        store = records("zz_foreign")
        logging.getLogger("zz_foreign").warning(PAYLOAD)
        [record] = store.records
        assert record.getMessage() == PAYLOAD


# --- 6. exception notes and SyntaxError fields (round 2) ------------------------


def _raise_with_note() -> None:
    error = ValueError("plain")
    error.add_note("bad\x1b[2J\nFORGED")
    raise error


def _raise_syntax_error() -> None:
    raise SyntaxError("bad\x1b[2J\nFORGED", ("f.py", 1, 1, "src\x1b[2Jline\n"))


@pytest.mark.parametrize("on_logger", [MCP_TRACEBACK], indirect=True)
class TestExceptionExtras:
    @pytest.mark.skipif(sys.version_info < (3, 11), reason="add_note is 3.11+")
    def test_note_escaped(self, on_logger: tuple[logging.Logger, io.StringIO]) -> None:
        logger, stream = on_logger
        try:
            _raise_with_note()
        except ValueError:
            logger.exception("failed")
        text = stream.getvalue()
        assert "\x1b" not in text, repr(text)
        assert not any(line.startswith("FORGED") for line in text.splitlines()), repr(text)
        assert "ValueError: plain" in text

    def test_syntax_error_fields_escaped(
        self, on_logger: tuple[logging.Logger, io.StringIO]
    ) -> None:
        logger, stream = on_logger
        try:
            _raise_syntax_error()
        except SyntaxError:
            logger.exception("failed")
        text = stream.getvalue()
        assert "\x1b" not in text, repr(text)
        assert not any(line.startswith("FORGED") for line in text.splitlines()), repr(text)
        assert "SyntaxError: bad" in text


# --- 7. bad %-format with exc_info (round 2) ------------------------------------


class TestBadFormatWithExcInfo:
    def test_exc_text_escaped(self, records: Callable[[str], _Records]) -> None:
        store = records("yurtle-kanban.zz_badfmt")
        logger = logging.getLogger("yurtle-kanban.zz_badfmt")
        try:
            _raise("bad\x1b[2J\nFORGED")
        except ValueError:
            logger.warning("%d", "notanint", exc_info=True)
        [record] = store.records
        assert isinstance(record.exc_text, str), record.exc_text
        assert "\x1b" not in record.exc_text, repr(record.exc_text)
        assert "ValueError: bad\\x1b[2J\\nFORGED" in record.exc_text
