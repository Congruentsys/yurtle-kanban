"""Package log records can't smuggle control characters (#215, #234).

Warnings interpolate text from repo files (hook definitions, item titles). With no
handler configured, Python's last-resort handler writes them straight to stderr,
so an ESC sequence or newline in a cloned repo's file could clear the screen or
forge log lines. Every WARNING-or-higher record from a `yurtle-kanban*` logger —
any child logger too, however it was created — has non-printable characters in its
message and in its exception messages escaped when the record is made, before any
handler formats it. DEBUG and INFO are left alone (the SPARQL debug log is
multi-line on purpose), and so is a traceback's own layout.
"""

from __future__ import annotations

import logging
import traceback
from typing import Any

PREFIX = "yurtle-kanban"  # also covers `yurtle-kanban-mcp` and every child


def escape_nonprintable(text: str) -> str:
    """Printable text as is; any other character as its repr escape (`\\x1b`)."""
    if text.isprintable():
        return text
    return "".join(c if c.isprintable() else repr(c)[1:-1] for c in text)


def _escaped_traceback(exc_info: Any) -> str:
    """The traceback `Formatter.formatException` would print, with only each
    exception's message escaped (chained and grouped ones too); frames and the
    newlines between them stay as they are (#234)."""
    top = traceback.TracebackException(*exc_info)
    seen: set[int] = set()
    todo = [top]
    while todo:
        te = todo.pop()
        if id(te) in seen:
            continue
        seen.add(id(te))
        if isinstance(getattr(te, "_str", None), str):
            te._str = escape_nonprintable(te._str)
        todo += [t for t in (te.__cause__, te.__context__) if t is not None]
        todo += list(getattr(te, "exceptions", None) or [])
    text = "".join(top.format())
    return text[:-1] if text.endswith("\n") else text


_previous_factory = logging.getLogRecordFactory()


def _escaping_factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
    record = _previous_factory(*args, **kwargs)
    if record.levelno < logging.WARNING or not record.name.startswith(PREFIX):
        return record
    try:
        message = record.getMessage()
    except Exception:  # a bad %-format: leave it for the handler to report
        return record
    if not message.isprintable():
        record.msg, record.args = escape_nonprintable(message), None
    if record.exc_info and record.exc_info[1] is not None:
        record.exc_text = _escaped_traceback(record.exc_info)
    return record


if not getattr(logging.getLogRecordFactory(), "_yurtle_kanban", False):
    _escaping_factory._yurtle_kanban = True  # type: ignore[attr-defined]
    logging.setLogRecordFactory(_escaping_factory)


def get_logger(name: str) -> logging.Logger:
    """`logging.getLogger(name)`; importing this module installed the escaping."""
    return logging.getLogger(name)
