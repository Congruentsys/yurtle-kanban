"""Append-only numbered registers, stored as item files (#1294).

A register (decisions, directives, a numbered list) is configured under the
top-level `registers:` key of the board config. Each entry is one markdown file
of the non-work type `register-entry` (`kb:RegisterEntry`): frontmatter for its
id, the verbatim text written once under `## Text`, and a ```` ```yurtle ````
block that only ever grows. Every change after `alloc` is an insertion: the text
once (`fill`), relation triples on the NEWER entry (`kb:narrows`,
`kb:supersedes`), and `kb:statusChange` events for status and title (`set`). The
older entry's "superseded by N" / "narrowed by N" is derived, never written.

Writes go through `KanbanService.sync_and_push`: one compare-and-swap commit on
origin's default branch, the caller's checkout never touched. A register has its
own id space under `id_numbering: per_type` and `global` alike, and its entries
are never work: the scan skips them, so `next`, `claim`, `board`, `list` and
`move` never see one.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click
import yaml
from rdflib import RDF, BNode, Graph, Literal, Namespace
from rdflib.namespace import XSD

from ._click import Group, refuse
from .inputs import resolve_actor
from .models import InputRefused
from .sync import Change, NoOp, Outcome, Read, Refuse

if TYPE_CHECKING:
    from .service import KanbanService

ENTRY_TYPE = "register-entry"
RESERVED = "reserved"
VOID = "void"
RELATIONS = ("narrows", "supersedes")
SEQUENCES = ("integer", "alpha")
DEFAULT_STATUSES = ("in_force",)
_KEYS = frozenset({
    "name", "path", "id_prefix", "cite_form", "cite_pattern", "scan", "statuses",
    "relations", "index", "exempt", "sequence",
})
_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")
_STATUS_RE = re.compile(r"[a-z][a-z0-9_-]*")
_PREFIX_RE = re.compile(r"[A-Z][A-Z0-9]*")
_ITEM_ID_RE = re.compile(r"[A-Za-z]+-[A-Za-z0-9]+(?:\.[A-Za-z0-9]+)*")

KB = Namespace("https://yurtle.dev/kanban/")
ITEM = Namespace("https://yurtle.dev/kanban/item/")


# --- config -------------------------------------------------------------------------


def to_letters(n: int) -> str:
    """`n` in the bijective base-26 letter sequence: 1 -> a, 26 -> z, 27 -> aa."""
    out = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        out = chr(97 + r) + out
    return out


def from_letters(token: str) -> int | None:
    """The inverse of `to_letters`; None for anything but lower-case letters."""
    if not re.fullmatch(r"[a-z]+", token):
        return None
    n = 0
    for ch in token:
        n = n * 26 + ord(ch) - 96
    return n


@dataclass(frozen=True)
class Register:
    """One configured register (#1294)."""

    name: str
    path: str  # relative to the repo root, posix, no trailing slash
    id_prefix: str
    cite_form: str
    cite_pattern: re.Pattern[str]
    scan: tuple[str, ...] = ()
    statuses: tuple[str, ...] = DEFAULT_STATUSES
    relations: tuple[str, ...] = RELATIONS
    index: str | None = None
    exempt: str | None = None
    sequence: str = "integer"

    def render(self, n: int) -> str:
        """`n` as the sequence writes it: `3`, or `c` for `alpha`."""
        return to_letters(n) if self.sequence == "alpha" else str(n)

    def parse(self, token: object) -> int | None:
        """A bare number (`3`; `c` for `alpha`) as its integer, else None."""
        text = str(token).strip()
        if self.sequence == "alpha":
            return from_letters(text)
        return int(text) if re.fullmatch(r"[1-9][0-9]*", text) else None

    def parse_cite(self, token: object) -> int | None:
        """A cited number: `parse`, but a zero-padded integer (`007`) is the integer
        it pads (a cite of an id is padded as the id is)."""
        text = str(token).strip()
        if self.sequence == "integer" and (m := re.fullmatch(r"0*([1-9][0-9]*)", text)):
            return int(m.group(1))
        return self.parse(text)

    def cite(self, n: int) -> str:
        return self.cite_form.replace("{n}", self.render(n))

    def entry_id(self, n: int) -> str:
        return f"{self.id_prefix}-{n:03d}"

    def number_of(self, item_id: object) -> int | None:
        """The number of an id in this register's space (`DEC-007` -> 7), else None."""
        m = re.fullmatch(rf"{re.escape(self.id_prefix)}-0*([1-9][0-9]*)", str(item_id), re.I)
        return int(m.group(1)) if m else None


def _shape(value: object) -> str:
    return f"{type(value).__name__} {value!r}"


def _str_list(value: object, where: str, key: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise InputRefused(f"{where}: `{key}` must be a list of strings, got {_shape(value)}")
    return tuple(value)


def _default_pattern(cite_form: str, sequence: str) -> str:
    before, _, after = cite_form.partition("{n}")
    number = "([a-z]+)" if sequence == "alpha" else "0*([1-9][0-9]*)"  # padded too
    tail = "(?![a-z])" if sequence == "alpha" else "(?![0-9])"
    return rf"(?<![A-Za-z0-9_]){re.escape(before)}{number}{re.escape(after)}{tail}"


def load_registers(
    raw: object, work_prefixes: set[str] | frozenset[str] = frozenset()
) -> list[Register]:
    """The `registers:` list of a config, checked; a bad one is an `InputRefused`
    naming the offending key. `work_prefixes` are the id prefixes work items use,
    which no register may share."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise InputRefused(f"`registers` must be a list of register mappings, got {_shape(raw)}")
    found: list[Register] = []
    prefixes: dict[str, str] = {}
    work = {p.upper() for p in work_prefixes}
    for i, spec in enumerate(raw):
        where = f"registers[{i}]"
        if not isinstance(spec, dict):
            raise InputRefused(f"{where} must be a mapping, got {_shape(spec)}")
        name = spec.get("name")
        if not isinstance(name, str) or not _NAME_RE.fullmatch(name):
            raise InputRefused(
                f"{where}: `name` is required: letters, digits, `_` and `-`, got {_shape(name)}"
            )
        where = f"register '{name}'"
        if any(r.name == name for r in found):
            raise InputRefused(f"{where} is configured twice: each `name` must be unique")
        unknown = sorted(str(k) for k in spec if k not in _KEYS)
        if unknown:
            raise InputRefused(f"{where}: unknown key `{unknown[0]}`")
        path = _folder(spec.get("path"), where, "path")
        for other in found:
            if _nested(path, other.path):
                raise InputRefused(
                    f"{where}: `path` {path} overlaps register '{other.name}''s "
                    f"{other.path}: each register has a folder of its own"
                )
        prefix = spec.get("id_prefix")
        if not isinstance(prefix, str) or not _PREFIX_RE.fullmatch(prefix):
            raise InputRefused(
                f"{where}: `id_prefix` must be upper-case letters and digits, got {_shape(prefix)}"
            )
        if prefix.upper() in work:
            raise InputRefused(
                f"{where}: `id_prefix` {prefix} is a work item type's prefix: a register "
                "has its own id space"
            )
        if prefix.upper() in prefixes:
            raise InputRefused(
                f"{where}: `id_prefix` {prefix} is already register "
                f"'{prefixes[prefix.upper()]}''s"
            )
        prefixes[prefix.upper()] = name
        sequence = spec.get("sequence", "integer")
        if sequence not in SEQUENCES:
            raise InputRefused(
                f"{where}: `sequence` must be {' or '.join(SEQUENCES)}, got {_shape(sequence)}"
            )
        cite_form = spec.get("cite_form", f"{prefix}-{{n}}")
        if not isinstance(cite_form, str) or cite_form.count("{n}") != 1:
            raise InputRefused(
                f"{where}: `cite_form` must hold `{{n}}` exactly once, got {_shape(cite_form)}"
            )
        pattern_text = spec.get("cite_pattern", _default_pattern(cite_form, sequence))
        if not isinstance(pattern_text, str):
            raise InputRefused(
                f"{where}: `cite_pattern` must be a regex, got {_shape(pattern_text)}"
            )
        try:
            pattern = re.compile(pattern_text)
        except re.error as e:
            raise InputRefused(f"{where}: `cite_pattern` does not compile: {e}") from None
        if pattern.groups < 1:
            raise InputRefused(
                f"{where}: `cite_pattern` needs a group: group 1 is the cited number"
            )
        statuses = _str_list(spec.get("statuses", list(DEFAULT_STATUSES)), where, "statuses")
        bad = [s for s in statuses if not _STATUS_RE.fullmatch(s) or s in (RESERVED, VOID)]
        if not statuses or bad or len(set(statuses)) != len(statuses):
            raise InputRefused(
                f"{where}: `statuses` must be distinct lower-case names, not reserved or "
                f"void, got {statuses!r}"
            )
        relations = _str_list(spec.get("relations", list(RELATIONS)), where, "relations")
        if any(r not in RELATIONS for r in relations):
            raise InputRefused(
                f"{where}: `relations` may name only {', '.join(RELATIONS)}, got {relations!r}"
            )
        scan = _str_list(spec.get("scan", []), where, "scan")
        optional: dict[str, str | None] = {}
        for key in ("index", "exempt"):
            value = spec.get(key)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise InputRefused(f"{where}: `{key}` must be a path, got {_shape(value)}")
            optional[key] = _folder(value, where, key) if value else None
        found.append(Register(
            name=name, path=path, id_prefix=prefix, cite_form=cite_form,
            cite_pattern=pattern, scan=scan, statuses=statuses, relations=relations,
            index=optional["index"], exempt=optional["exempt"], sequence=sequence,
        ))
    for reg in found:
        for key in ("index", "exempt"):
            value = getattr(reg, key)
            inside = next((o for o in found if value and (
                value == o.path or value.startswith(o.path + "/"))), None)
            if inside is not None:
                raise InputRefused(
                    f"register '{reg.name}': `{key}` {value} is inside register "
                    f"'{inside.name}''s folder {inside.path}"
                )
        if reg.index is not None:
            twin = next((o for o in found if o is not reg and reg.index in (o.index, o.exempt)),
                        None)
            if twin is not None:
                which = "index" if twin.index == reg.index else "exempt"
                raise InputRefused(
                    f"register '{reg.name}': `index` {reg.index} is also register "
                    f"'{twin.name}''s `{which}`: each register's index is its own file"
                )
    return found


def _folder(value: object, where: str, key: str) -> str:
    """A repo-relative path from the config: no `.`/`..` part, not absolute."""
    if not isinstance(value, str) or not value.strip():
        raise InputRefused(f"{where}: `{key}` is required, got {_shape(value)}")
    text = value.strip()
    parts = [p for p in text.split("/") if p]
    if text.startswith("/") or not parts or any(p in (".", "..") for p in parts):
        raise InputRefused(
            f"{where}: `{key}` must be a path inside the repo with no `.` or `..` "
            f"part, got {text!r}"
        )
    return "/".join(parts)


def _nested(a: str, b: str) -> bool:
    """Whether folder `a` is `b` or inside it, or `b` inside `a`."""
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def register_prefixes(raw: object) -> set[str]:
    """The id prefixes of a `registers:` value, upper-cased, read leniently: what
    a work-item allocator leaves out of its sequences (#1294). A config that
    `load_registers` would refuse still yields the prefixes it names."""
    if not isinstance(raw, list):
        return set()
    return {
        spec["id_prefix"].upper() for spec in raw
        if isinstance(spec, dict) and isinstance(spec.get("id_prefix"), str)
    }


# --- entries --------------------------------------------------------------------------


@dataclass
class Entry:
    """One entry file, parsed."""

    number: int
    item_id: str
    path: str  # relative to the repo root, posix
    title: str
    status: str
    created: str | None = None
    text: str | None = None
    digest: str | None = None
    relations: list[tuple[str, str]] = field(default_factory=list)  # (kind, target id)
    allocated_for: str | None = None
    events: list[dict[str, str]] = field(default_factory=list)
    allocations: list[str] = field(default_factory=list)  # every kb:allocatedFor
    front_allocated_for: str | None = None  # the frontmatter's `allocated_for`


_REGULAR = ("100644", "100755")
_HEAD_BYTES = 64 * 1024  # a frontmatter is read from a file's first bytes
_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)^---[^\n]*\n", re.S | re.M)
_TEXT_HEADING = "\n## Text\n\n"
_BLOCK_OPEN = "```yurtle\n"
_PREFIXES = (
    "@prefix kb: <https://yurtle.dev/kanban/> .\n"
    "@prefix item: <https://yurtle.dev/kanban/item/> .\n"
    "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .\n"
)
_ESCAPES = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t"}
_UNESCAPES = {"\\": "\\", '"': '"', "n": "\n", "r": "\r", "t": "\t"}


def _lit(value: str) -> str:
    return '"' + "".join(_ESCAPES.get(ch, ch) for ch in value) + '"'


def _unlit(text: str) -> str:
    return re.sub(r"\\(.)", lambda m: _UNESCAPES.get(m.group(1), m.group(1)), text)


def _value(raw: str) -> str:
    """A Turtle object as this module writes one, as text: `kb:x` -> x,
    `item:ID` -> ID, `"s"` / `"s"^^xsd:t` -> s."""
    raw = raw.strip()
    m = re.fullmatch(r'"((?:[^"\\]|\\.)*)"(?:\^\^\S+)?', raw)
    if m:
        return _unlit(m.group(1))
    return raw.split(":", 1)[1] if ":" in raw else raw


def text_digest(text: str) -> str:
    """sha256 of an entry's verbatim text, its line endings LF and its outer blank
    lines dropped, as `fill` writes it."""
    return hashlib.sha256((normalise_text(text) + "\n").encode("utf-8")).hexdigest()


def normalise_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").strip("\n")


def _block_span(content: str) -> tuple[int, int] | None:
    """(start of the block's body, start of its closing fence line) of the entry's
    yurtle block: the LAST one in the file."""
    start = content.rfind("\n" + _BLOCK_OPEN)
    if start < 0:
        return None
    body = start + 1 + len(_BLOCK_OPEN)
    close = content.find("\n```", body - 1)
    if close < 0:
        return None
    return body, close + 1


def parse_entry(reg: Register, path: str, content: str) -> Entry | None:
    """An entry file's facts, or None when its frontmatter id is not in `reg`'s
    space."""
    content = content.replace("\r\n", "\n")
    m = _FRONTMATTER_RE.match(content)
    if not m:
        return None
    try:
        front = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        return None
    if not isinstance(front, dict):
        return None
    number = reg.number_of(front.get("id", ""))
    if number is None:
        return None
    created = front.get("created")
    front_for = front.get("allocated_for")
    entry = Entry(
        number=number, item_id=str(front["id"]), path=path,
        title=str(front.get("title") or reg.cite(number)), status=RESERVED,
        created=None if created is None else str(created),
        front_allocated_for=None if front_for is None else str(front_for),
    )
    span = _block_span(content)
    heading = content.find(_TEXT_HEADING, m.end() - 1)
    if heading >= 0 and span is not None:
        body = normalise_text(content[heading + len(_TEXT_HEADING): span[0] - len(_BLOCK_OPEN)])
        entry.text = body or None
    if span is None:
        return entry
    event: dict[str, str] | None = None
    for line in content[span[0]: span[1]].splitlines():
        stripped = line.strip()
        if event is not None:
            if stripped.startswith("]"):
                entry.events.append(event)
                event = None
            elif pm := re.fullmatch(r"kb:(\w+)\s+(.+?)\s*;?", stripped):
                event[pm.group(1)] = _value(pm.group(2))
            continue
        if re.fullmatch(r"<>\s+kb:statusChange\s+\[", stripped):
            event = {}
        elif rm := re.fullmatch(r"<>\s+kb:(narrows|supersedes)\s+(\S+?)\s*\.", stripped):
            entry.relations.append((rm.group(1), _value(rm.group(2))))
        elif am := re.fullmatch(r"<>\s+kb:allocatedFor\s+(.+?)\s*\.", stripped):
            entry.allocated_for = _value(am.group(1))
            entry.allocations.append(entry.allocated_for)
    for ev in entry.events:
        entry.status = ev.get("status", entry.status)
        entry.title = ev.get("title", entry.title)
        entry.digest = ev.get("textDigest", entry.digest)
    return entry


def _event(status: str, actor: str, at: str, **extra: str) -> str:
    lines = [f"    kb:status kb:{status} ;"]
    for key, value in extra.items():
        lines.append(f"    kb:{key} {_lit(value)} ;")
    lines += [f'    kb:at "{at}"^^xsd:dateTime ;', f"    kb:by {_lit(actor)} ;"]
    return "<> kb:statusChange [\n" + "\n".join(lines) + "\n  ] .\n"


def _target(value: str) -> str:
    return f"item:{value}" if _ITEM_ID_RE.fullmatch(value) else _lit(value)


def render_entry(
    reg: Register, n: int, title: str, actor: str, at: str, *, created: object,
    allocated_for: str | None = None, text: str | None = None, status: str = RESERVED,
) -> str:
    """A new entry file: reserved (no text), or written whole by `import`."""
    front: dict[str, Any] = {
        "id": reg.entry_id(n), "title": title, "type": ENTRY_TYPE, "register": reg.name,
        "cite": reg.cite(n),
    }
    if created is not None:
        front["created"] = created
    if allocated_for:
        front["allocated_for"] = allocated_for
    head = yaml.safe_dump(front, sort_keys=False, allow_unicode=True, width=1 << 20)
    body = f"\n{normalise_text(text)}\n" if text else ""
    block = (
        f"<> a kb:RegisterEntry ;\n    kb:id {_lit(reg.entry_id(n))} ;\n"
        f"    kb:register {_lit(reg.name)} ;\n    kb:numericId {n} .\n"
    )
    if allocated_for:
        block += f"<> kb:allocatedFor {_target(allocated_for)} .\n"
    extra = {"title": title}
    if text:
        extra["textDigest"] = text_digest(text)
    block += _event(status, actor, at, **extra)
    return (
        f"---\n{head}---\n\n# {reg.cite(n)}: {title}\n\n## Text\n{body}\n"
        f"```yurtle\n{_PREFIXES}\n{block}```\n"
    )


def _append(content: str, block_lines: str) -> str:
    """`content` with `block_lines` inserted before the yurtle block's closing fence."""
    span = _block_span(content)
    if span is None:
        raise InputRefused("the entry file has no yurtle block")
    return content[: span[1]] + block_lines + content[span[1]:]


def _slug(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug[:50].rstrip("-")


def _now() -> str:
    from . import service as service_mod

    return service_mod._now().isoformat(timespec="seconds")


def _check_line(value: str, what: str) -> str:
    value = value.strip()
    if not value or "\n" in value or "\r" in value:
        raise InputRefused(f"{what} must be one non-empty line")
    return value


def _board_prefixes(service: KanbanService) -> set[str]:
    """The id prefixes of the item types the CONFIGURED boards' themes define: what
    a register may not share. A theme that does not load is the board's to report."""
    config = service.config
    try:
        if config.is_multi_board:
            themes = [b.get_theme(service.repo_root, config.theme_source) for b in config.boards]
        else:
            themes = [config.get_theme()]
    except Exception:
        return set()
    out = set()
    for theme in themes:
        for type_id, type_def in ((theme or {}).get("item_types") or {}).items():
            pre = type_def.get("id_prefix") if isinstance(type_def, dict) else None
            out.add(str(pre or str(type_id)[:4]).upper())
    return out


# --- the registers of one repo --------------------------------------------------------


class Registers:
    """The registers of the repo `service` serves: reads of the working tree, and
    the writing verbs as `sync_and_push` mutations."""

    def __init__(self, service: KanbanService) -> None:
        self.service = service
        self.root = service.repo_root
        self.registers = load_registers(service.config.registers, _board_prefixes(service))

    def get(self, name: str) -> Register:
        for reg in self.registers:
            if reg.name == name:
                return reg
        known = ", ".join(r.name for r in self.registers) or "none configured"
        raise InputRefused(f"no register named '{name}' (registers: {known})")

    # paths

    def _git_rel(self, rel: str) -> str:
        """A repo-root-relative path as the git work tree names it."""
        top = self.service._git_toplevel()
        found = self.service._repo_relative(self.root / rel, top)
        if found is None:
            raise InputRefused(f"{rel} is outside the git repository at {top}")
        return found.as_posix()

    def _root_rel(self, git_rel: str) -> str:
        top = self.service._git_toplevel()
        found = self.service._repo_relative(top / git_rel, self.root)
        return found.as_posix() if found is not None else git_rel

    # reading entries

    def _names(self, reg: Register, rev: str | None) -> list[str]:
        """Git-relative `.md` paths under the register's folder at `rev` (None:
        the working tree)."""
        folder = self._git_rel(reg.path)
        if rev is None:
            base = self.service._git_toplevel() / folder
            if not base.is_dir():
                return []
            return sorted(
                f"{folder}/{p.relative_to(base).as_posix()}" for p in base.rglob("*.md")
            )
        listed = self.service._git_z(
            "ls-tree", "-r", "-z", "--name-only", "--full-tree", rev, "--", folder
        )
        return sorted(n for n in listed.stdout.split("\0") if n.endswith(".md"))

    def _modes(self, rev: str, *paths: str) -> dict[str, str]:
        """git path -> blob mode of every file at `rev` under `paths` (all: none)."""
        listed = self.service._git_z("ls-tree", "-r", "-z", "--full-tree", rev, "--", *paths)
        out = {}
        for record in listed.stdout.split("\0"):
            meta, _, name = record.partition("\t")
            if name:
                out[name] = meta.split(" ", 1)[0]
        return out

    def _irregular(self, reg: Register, rev: str | None) -> list[str]:
        """Git paths under the register's folder that are not regular files: a blob
        whose mode at `rev` (None: HEAD, and the working tree) is not 100644/100755,
        such as a symlink. Never an entry, never skipped silently."""
        folder = self._git_rel(reg.path)
        modes = self._modes(rev or "HEAD", folder)
        bad = {n for n, mode in modes.items() if mode not in _REGULAR}
        if rev is None:
            base = self.service._git_toplevel() / folder
            if base.is_dir():
                bad |= {f"{folder}/{p.relative_to(base).as_posix()}"
                        for p in base.rglob("*") if p.is_symlink()}
        return sorted(bad)

    def _blob(self, rev: str, name: str) -> str | None:
        """The committed bytes of `name` at `rev`, as git stores them (no filter,
        no working tree), decoded as UTF-8; None when absent or undecodable."""
        shown = self.service._git_run("cat-file", "blob", f"{rev}:{name}", text=False)
        if shown.returncode != 0:
            return None
        out = shown.stdout
        try:
            return out.decode("utf-8") if isinstance(out, bytes) else out
        except UnicodeDecodeError:
            return None

    def _committed(
        self, reg: Register, rev: str
    ) -> tuple[dict[int, list[Entry]], list[str], list[str]]:
        """`reg`'s entries as commit `rev` stores them (blobs, never the working
        tree): entries by number, the git paths of regular `.md` blobs that parse as
        no entry of `reg`, and the git paths that are not regular files."""
        folder = self._git_rel(reg.path)
        found: dict[int, list[Entry]] = {}
        unparsed: list[str] = []
        irregular: list[str] = []
        for name, mode in sorted(self._modes(rev, folder).items()):
            if not name.endswith(".md"):
                continue
            if mode not in _REGULAR:
                irregular.append(name)
                continue
            content = self._blob(rev, name)
            entry = None if content is None else parse_entry(reg, self._root_rel(name), content)
            if entry is None:
                unparsed.append(name)
            else:
                found.setdefault(entry.number, []).append(entry)
        return found, unparsed, irregular

    def _number_of_name(self, reg: Register, name: str) -> int | None:
        stem = name.rsplit("/", 1)[-1]
        m = re.match(rf"{re.escape(reg.id_prefix)}-0*([1-9][0-9]*)(?![0-9])", stem, re.I)
        return int(m.group(1)) if m else None

    def _numbered(self, reg: Register, rev: str | None) -> dict[int, list[str]]:
        found: dict[int, list[str]] = {}
        for name in self._names(reg, rev):
            n = self._number_of_name(reg, name)
            if n is not None:
                found.setdefault(n, []).append(name)
        return found

    def entries(self, reg: Register) -> tuple[dict[int, list[Entry]], list[str]]:
        """The working tree's entries by number, and the `.md` files in the folder
        that are no entry of the register (repo-root-relative)."""
        found: dict[int, list[Entry]] = {}
        strays: list[str] = []
        top = self.service._git_toplevel()
        irregular = set(self._irregular(reg, None))
        for name in self._names(reg, None):
            if name in irregular:
                continue  # `problems` names it
            rel = self._root_rel(name)
            try:
                content = (top / name).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                strays.append(rel)
                continue
            entry = parse_entry(reg, rel, content)
            if entry is None:
                strays.append(rel)
            else:
                found.setdefault(entry.number, []).append(entry)
        return dict(sorted(found.items())), strays

    def one(self, reg: Register, n: int) -> Entry:
        found = self.entries(reg)[0].get(n, [])
        if not found:
            raise InputRefused(f"{reg.cite(n)} is not an entry of register '{reg.name}'")
        if len(found) > 1:
            raise InputRefused(
                f"{reg.cite(n)} is held by {len(found)} files: "
                + ", ".join(e.path for e in found)
            )
        return found[0]

    def number(self, reg: Register, token: str) -> int:
        n = reg.parse(token)
        if n is None:
            kind = "letters (a, b, … aa)" if reg.sequence == "alpha" else "a positive integer"
            raise InputRefused(f"{token!r} is not a number of register '{reg.name}': {kind}")
        return n

    # derived

    @staticmethod
    def derived(reg: Register, entries: dict[int, list[Entry]]) -> dict[int, list[str]]:
        """number -> "superseded by D2", "narrowed by D3": the inverse of each newer
        entry's relations, and each entry's own "supersedes D1"."""
        out: dict[int, list[str]] = {}
        for n, held in entries.items():
            for entry in held:
                for kind, target in entry.relations:
                    t = reg.number_of(target)
                    shown = reg.cite(t) if t is not None else target
                    out.setdefault(n, []).append(f"{kind} {shown}")
                    if t is not None:
                        word = "superseded" if kind == "supersedes" else "narrowed"
                        out.setdefault(t, []).append(f"{word} by {reg.cite(n)}")
        return out

    def index(self, reg: Register) -> str:
        """The generated markdown index of `reg`, from the working tree."""
        return self.index_text(reg, self.entries(reg)[0])

    def index_text(self, reg: Register, entries: dict[int, list[Entry]]) -> str:
        """The generated markdown index of `reg`'s `entries`."""
        derived = self.derived(reg, entries)
        lines = [
            f"# Register: {reg.name}",
            "",
            f"<!-- Generated by `yurtle-kanban register index {reg.name} --write`; "
            "do not edit by hand. -->",
            "",
            "| Entry | Date | Title | Status |",
            "|---|---|---|---|",
        ]
        for n, held in entries.items():
            for entry in held:
                status = "; ".join([entry.status, *derived.get(n, [])])
                cells = [reg.cite(n), entry.created or "", entry.title, status]
                lines.append("| " + " | ".join(c.replace("|", "\\|") for c in cells) + " |")
        return "\n".join(lines) + "\n"

    # cites

    def _scanned(self, reg: Register) -> list[str]:
        """Repo-root-relative files the register's `scan` globs match, less its own
        exempt and index files."""
        skip = {reg.exempt, reg.index}
        found: set[str] = set()
        for glob in reg.scan:
            rx = _glob_regex(glob)
            literal = []
            for part in glob.split("/"):
                if any(ch in part for ch in "*?["):
                    break
                literal.append(part)
            start = self.root.joinpath(*literal) if literal else self.root
            if start.is_file():
                candidates = [start]
            elif start.is_dir():
                candidates = []
                for dirpath, dirnames, filenames in os.walk(start):
                    dirnames[:] = [d for d in dirnames if d != ".git"]
                    candidates += [Path(dirpath) / f for f in filenames]
            else:
                continue
            for path in candidates:
                rel = path.relative_to(self.root).as_posix()
                if rel not in skip and rx.fullmatch(rel):
                    found.add(rel)
        return sorted(found)

    def _cites_in(self, reg: Register, rel: str) -> list[tuple[int, int, str]] | None:
        """(line, number, token) of every cite in a file; None when it can't be
        read as UTF-8."""
        try:
            text = (self.root / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None
        out = []
        for line_no, line in enumerate(text.split("\n"), 1):
            for m in reg.cite_pattern.finditer(line):
                n = reg.parse_cite(m.group(1) or "")
                if n is not None:
                    out.append((line_no, n, m.group(0)))
        return out

    def exemptions(self, reg: Register) -> list[dict[str, Any]]:
        """The rows of the register's exempt file: {path, line, number, reason},
        `number` as its integer. A missing file is no rows."""
        if reg.exempt is None:
            return []
        path = self.root / reg.exempt
        if not path.is_file():
            return []
        try:
            rows = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as e:
            raise InputRefused(f"{reg.exempt}: unreadable exempt file: {e}") from None
        if not isinstance(rows, list):
            raise InputRefused(f"{reg.exempt}: the exempt file must be a YAML list")
        out = []
        for i, row in enumerate(rows, 1):
            n = reg.parse(row.get("number", "")) if isinstance(row, dict) else None
            line = row.get("line") if isinstance(row, dict) else None
            if (
                n is None or not isinstance(row.get("path"), str)
                or isinstance(line, bool) or not isinstance(line, int)
            ):
                raise InputRefused(
                    f"{reg.exempt}: row {i} must be a mapping of path, line, number, reason"
                )
            out.append({**row, "number": n})
        return out

    def cites(
        self, reg: Register
    ) -> tuple[list[tuple[str, int, int, str]], list[str], list[str]]:
        """Every cite the scan finds and no exemption covers, as (path, line,
        number, token); a problem for each stale exemption row; and a problem for
        each scanned file that is not UTF-8 (it is never skipped silently)."""
        rows = self.exemptions(reg)
        exempt = {(r["path"], r["line"], r["number"]) for r in rows}
        found = []
        unreadable = []
        for rel in self._scanned(reg):
            cites = self._cites_in(reg, rel)
            if cites is None:
                unreadable.append(f"{rel}: not readable as UTF-8 text, so register "
                                  f"'{reg.name}' cannot check its cites")
                continue
            found += [(rel, line, n, token) for line, n, token in cites
                      if (rel, line, n) not in exempt]
        stale = []
        for r in rows:
            held = {(line, n) for line, n, _ in self._cites_in(reg, r["path"]) or []}
            if (r["line"], r["number"]) not in held:
                stale.append(
                    f"{r['path']}:{r['line']}: stale exemption in {reg.exempt}: the line "
                    f"no longer cites {reg.cite(r['number'])}"
                )
        return found, stale, unreadable

    # validate

    def problems(self, since: str | None = None) -> list[str]:
        """Every problem `validate` reports for the registers."""
        out: list[str] = []
        for reg in self.registers:
            entries, strays = self.entries(reg)
            for name in self._irregular(reg, None):
                out.append(f"{self._root_rel(name)} is in register '{reg.name}''s folder but "
                           "is not a regular file (a symlink?): an entry is a plain file")
            for rel in strays:
                out.append(f"{rel} is in register '{reg.name}''s folder but is no "
                           f"{reg.id_prefix}-<n> entry")
            for n, held in entries.items():
                if len(held) > 1:
                    out.append(
                        f"duplicate entry {reg.cite(n)} ({reg.entry_id(n)}) in register "
                        f"'{reg.name}': " + ", ".join(e.path for e in held)
                    )
            for held in entries.values():
                for entry in held:
                    out += self._entry_problems(reg, entry, entries)
            out += self._cycles(reg, entries)
            try:
                found, stale, unreadable = self.cites(reg)
            except InputRefused as e:
                out.append(str(e))
            else:
                out += stale + unreadable
                out += [
                    f"{rel}:{line} cites {token}, which is not an entry of register "
                    f"'{reg.name}'"
                    for rel, line, n, token in found if n not in entries
                ]
            if reg.index is not None:
                path = self.root / reg.index
                held_text = path.read_text(encoding="utf-8") if path.is_file() else None
                if held_text is None:
                    out.append(f"{reg.index}: register '{reg.name}''s index is missing: run "
                               f"`register index {reg.name} --write`")
                elif held_text.rstrip() != self.index(reg).rstrip():
                    out.append(f"{reg.index}: register '{reg.name}''s index is stale or "
                               f"edited: run `register index {reg.name} --write`")
        out += self._stray_entry_files()
        if since is not None:
            out += self._since(since)
        return out

    def _stray_entry_files(self) -> list[str]:
        """Each committed `type: register-entry` file outside every register folder:
        it is on no board and in no register, so it would vanish unreported. Every
        `.md` file HEAD holds is listed and its frontmatter parsed: nothing
        `.gitattributes` says can hide one."""
        folders = [self._git_rel(reg.path) + "/" for reg in self.registers]
        top = self.service._git_toplevel()
        out = []
        for name, mode in sorted(self._modes("HEAD").items()):
            if (not name.endswith(".md") or mode not in _REGULAR
                    or any(name.startswith(f) for f in folders)):
                continue
            try:
                with (top / name).open("rb") as f:
                    head_bytes = f.read(_HEAD_BYTES)
            except OSError:
                continue
            if b"register-entry" not in head_bytes:
                continue
            m = _FRONTMATTER_RE.match(head_bytes.decode("utf-8", "replace").replace("\r\n", "\n"))
            try:
                front = yaml.safe_load(m.group(1)) if m else None
            except yaml.YAMLError:
                continue
            if isinstance(front, dict) and front.get("type") == ENTRY_TYPE:
                out.append(f"{self._root_rel(name)} is a register entry outside every "
                           "register's folder: move it into its register or delete it")
        return out

    def _entry_problems(
        self, reg: Register, entry: Entry, entries: dict[int, list[Entry]]
    ) -> list[str]:
        out = []
        cite = reg.cite(entry.number)
        if entry.digest is not None and (
            entry.text is None or text_digest(entry.text) != entry.digest
        ):
            out.append(f"{entry.path}: {cite}'s text does not match its kb:textDigest")
        if entry.digest is None and entry.text is not None:
            out.append(f"{entry.path}: {cite} has text but no kb:textDigest")
        if len(entry.allocations) > 1:
            out.append(f"{entry.path}: {cite} has {len(entry.allocations)} kb:allocatedFor "
                       "facts: a number is allocated once")
        if entry.allocations[:1] != ([] if entry.front_allocated_for is None
                                     else [entry.front_allocated_for]):
            out.append(f"{entry.path}: {cite}'s kb:allocatedFor "
                       f"({entry.allocations[0] if entry.allocations else 'none'}) and "
                       f"frontmatter allocated_for ({entry.front_allocated_for or 'none'}) "
                       "disagree")
        digests = [ev for ev in entry.events if "textDigest" in ev]
        if len(digests) > 1:
            out.append(f"{entry.path}: {cite} has {len(digests)} kb:textDigest events: its "
                       "text is written once")
        filled = entry.text is not None or entry.digest is not None
        written = False  # an event with a digest seen: the fill
        for ev in entry.events:
            written = written or "textDigest" in ev
            status = ev.get("status")
            if status is None or status in reg.statuses:
                continue
            if status == RESERVED and not written:
                continue  # alloc's event, before any fill
            if status == VOID and not filled:
                continue  # a gap `import` voided
            why = (f"a filled entry is never {status}" if status in (RESERVED, VOID)
                   else f"not a status of register '{reg.name}'")
            out.append(f"{entry.path}: {cite} has an event with status {status!r}: {why}")
        for kind, target in entry.relations:
            t = reg.number_of(target)
            if kind not in reg.relations:
                out.append(f"{entry.path}: {cite} {kind} {target}, a relation register "
                           f"'{reg.name}' does not allow")
            if t is None or t not in entries:
                out.append(f"{entry.path}: {cite} {kind} {target}, which is not an entry "
                           f"of register '{reg.name}'")
            elif t >= entry.number:
                out.append(f"{entry.path}: {cite} {kind} {reg.cite(t)}, which is not older "
                           "(a relation points to an older entry)")
        return out

    @staticmethod
    def _cycles(reg: Register, entries: dict[int, list[Entry]]) -> list[str]:
        edges: dict[int, set[int]] = {}
        for n, held in entries.items():
            for entry in held:
                for _, target in entry.relations:
                    t = reg.number_of(target)
                    if t is not None and t in entries:
                        edges.setdefault(n, set()).add(t)
        out: list[str] = []
        state: dict[int, int] = {}
        reported: set[frozenset[int]] = set()

        def visit(n: int, trail: list[int]) -> None:
            state[n] = 1
            for t in sorted(edges.get(n, ())):
                if state.get(t) == 1:
                    loop = trail[trail.index(t):] + [n] if t in trail else [n, t]
                    key = frozenset(loop)
                    if key not in reported:
                        reported.add(key)
                        names = " -> ".join(reg.cite(x) for x in [*loop, loop[0]])
                        out.append(f"relation cycle in register '{reg.name}': {names}")
                elif state.get(t) is None:
                    visit(t, [*trail, n])
            state[n] = 2

        for n in sorted(edges):
            if state.get(n) is None:
                visit(n, [])
        return out

    def _since(self, rev: str) -> list[str]:
        """Both sides read from git objects, `rev`'s blobs and HEAD's, never the
        working tree (uncommitted edits are plain `validate`'s concern). `rev`'s
        entries are judged by identity, under the registers `rev`'s own config
        names: each (register, id) at `rev` must be held by exactly one file in that
        register's CURRENT folder, append-only against the `rev` file
        (`_append_only_violation`), wherever it now lives (a pure move or rename is
        fine). A `rev` path still at HEAD that is not that holder is "left behind";
        a path that is not a regular file, or a `.md` blob that parses as no entry,
        at `rev` or under a current folder at HEAD, is an error, never skipped. A
        register `rev` had and today's config lacks is an error when it held
        entries at `rev`."""
        svc = self.service
        ok = svc._git_run("rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}")
        if ok.returncode != 0:
            return [f"--since {rev}: not a commit"]
        try:
            then = self._registers_at(rev)
        except InputRefused as e:
            return [f"--since {rev}: the registers config at {rev} does not load: {e}"]
        now = {reg.name: reg for reg in self.registers}
        out = []
        head = svc._git_run("rev-parse", "--verify", "--quiet", "HEAD^{commit}")
        if head.returncode != 0:
            return [f"--since {rev}: there is no HEAD commit to measure"]
        tip = head.stdout.strip()
        head_tree = self._modes(tip)
        for cur in self.registers:  # HEAD's side: every blob under a current folder
            _, unparsed, irregular = self._committed(cur, tip)
            for name in irregular:
                out.append(f"{self._root_rel(name)}: not a regular file at HEAD (a symlink?) "
                           f"in register '{cur.name}''s folder: an entry is a plain file")
            for name in unparsed:
                out.append(f"{self._root_rel(name)}: its blob at HEAD is no entry of register "
                           f"'{cur.name}' (stored through a filter, such as git-lfs?); an "
                           "entry is stored as its own text")
        for old_reg in then:
            cur = now.get(old_reg.name)
            entries_then, unparsed, irregular = self._committed(old_reg, rev)
            if cur is None and (entries_then or unparsed or irregular):
                out.append(f"register '{old_reg.name}' held entries at {rev} and is gone "
                           "from the config now: a register's entries are never removed")
            held = self._committed(cur, tip)[0] if cur is not None else {}
            for name in irregular:
                out.append(f"{self._root_rel(name)}: not a regular file at {rev} (a "
                           f"symlink?) in register '{old_reg.name}''s folder: an entry is a "
                           "plain file, so nothing since can be measured against it")
            for name in unparsed:
                out.append(f"{self._root_rel(name)}: its blob at {rev} is no entry of "
                           f"register '{old_reg.name}' (stored through a filter, such as "
                           "git-lfs?), so nothing since can be measured against it")
            for olds in entries_then.values():
                for old_entry in olds:
                    rel = old_entry.path
                    old = self._blob(rev, self._git_rel(rel)) or ""
                    holders = [e for e in held.get(old_entry.number, [])
                               if e.item_id == old_entry.item_id]
                    if len(holders) != 1:
                        shown = ", ".join(e.path for e in holders) or "none"
                        out.append(f"{rel}: {old_entry.item_id} must be held by exactly one "
                                   f"file in register '{old_reg.name}''s folder at HEAD, as at "
                                   f"{rev}; held by: {shown}")
                    else:
                        holder = holders[0]
                        new = self._blob(tip, self._git_rel(holder.path)) or ""
                        why = _append_only_violation(old, new)
                        if why is not None:
                            moved = "" if holder.path == rel else f" (moved to {holder.path})"
                            out.append(f"{holder.path}: {old_entry.item_id} changed since {rev} "
                                       "other than by appending to its yurtle block (an entry "
                                       f"is append-only; at {rev} it was {rel}){moved}: {why}")
                    if self._git_rel(rel) in head_tree and not (
                        len(holders) == 1 and holders[0].path == rel
                    ):
                        out.append(f"{rel}: left behind: {old_entry.item_id}'s file at {rev} "
                                   "is still at HEAD but is not the entry's file now")
        return out

    def _registers_at(self, rev: str) -> list[Register]:
        """The registers the config at `rev` configures (the config path the repo
        uses: `.yurtle-kanban/config.yaml`, else `.kanban/config.yaml`)."""
        for folder in (".yurtle-kanban", ".kanban"):
            name = self._git_rel(f"{folder}/config.yaml")
            shown = self.service._git_run("show", f"{rev}:{name}")
            if shown.returncode != 0:
                continue
            try:
                data = yaml.safe_load(shown.stdout) or {}
            except yaml.YAMLError as e:
                raise InputRefused(str(e)) from None
            return load_registers(data.get("registers") if isinstance(data, dict) else None)
        return []

    # graph

    def add_to_graph(self, graph: Graph) -> None:
        """Each entry as a `kb:RegisterEntry`, with its relations and their derived
        inverse `kb:supersededBy`."""
        for reg in self.registers:
            for held in self.entries(reg)[0].values():
                for entry in held:
                    node = ITEM[entry.item_id]
                    graph.add((node, RDF.type, KB.RegisterEntry))
                    graph.add((node, KB.id, Literal(entry.item_id)))
                    graph.add((node, KB.register, Literal(reg.name)))
                    graph.add((node, KB.title, Literal(entry.title)))
                    graph.add((node, KB.status, KB[entry.status]))
                    graph.add((node, KB.numericId, Literal(entry.number, datatype=XSD.integer)))
                    if entry.digest:
                        graph.add((node, KB.textDigest, Literal(entry.digest)))
                    if entry.allocated_for:
                        graph.add((node, KB.allocatedFor, ITEM[entry.allocated_for]
                                   if _ITEM_ID_RE.fullmatch(entry.allocated_for)
                                   else Literal(entry.allocated_for)))
                    for ev in entry.events:
                        bn = BNode()
                        graph.add((node, KB.statusChange, bn))
                        if "status" in ev:
                            graph.add((bn, KB.status, KB[ev["status"]]))
                        if "at" in ev:
                            graph.add((bn, KB.at, Literal(ev["at"], datatype=XSD.dateTime)))
                        if "by" in ev:
                            graph.add((bn, KB.by, Literal(ev["by"])))
                    for kind, target in entry.relations:
                        if not _ITEM_ID_RE.fullmatch(target):
                            continue
                        graph.add((node, KB[kind], ITEM[target]))
                        if kind == "supersedes":
                            graph.add((ITEM[target], KB.supersededBy, node))

    # writes

    def _entries_at(
        self, reg: Register, read: Read, files: dict[str, str]
    ) -> dict[int, list[Entry]]:
        """The entries of `read`'s tree with `files` (git-relative path -> text, the
        change being built) written over it."""
        folder = self._git_rel(reg.path) + "/"
        names = set(self._names(reg, read.rev))
        names |= {k for k in files if k.startswith(folder) and k.endswith(".md")}
        found: dict[int, list[Entry]] = {}
        for name in sorted(names):
            content = files[name] if name in files else read(name)
            entry = None if content is None else parse_entry(reg, self._root_rel(name), content)
            if entry is not None:
                found.setdefault(entry.number, []).append(entry)
        return dict(sorted(found.items()))

    def _change(self, reg: Register, read: Read, files: dict[str, str], message: str,
                data: object = None) -> Change:
        """A Change writing `files`, and with an `index:` configured the index
        regenerated from the same tree, in the same commit."""
        if reg.index is not None:
            files = {**files, self._git_rel(reg.index):
                     self.index_text(reg, self._entries_at(reg, read, files))}
        return Change(files, message, data=data)

    def _entry_at(self, reg: Register, read: Read, n: int) -> tuple[str, str] | Refuse:
        held = self._numbered(reg, read.rev).get(n, [])
        if not held:
            return Refuse(f"{reg.cite(n)} is not allocated in register '{reg.name}'")
        if len(held) > 1:
            return Refuse(f"{reg.cite(n)} is held by {len(held)} files: {', '.join(held)}")
        content = read(held[0])
        if content is None:
            return Refuse(f"{held[0]} could not be read")
        return held[0], content

    def alloc(self, reg: Register, title: str, actor: str, by: str | None) -> Outcome:
        title = _check_line(title, "--title")
        if by is not None:
            by = _check_line(by, "--by")
        at = _now()

        def mutate(read: Read, attempt: int) -> Change | NoOp | Refuse:
            n = max(self._numbered(reg, read.rev), default=0) + 1
            slug = _slug(title)
            name = reg.entry_id(n) + (f"-{slug}" if slug else "") + ".md"
            rel = f"{self._git_rel(reg.path)}/{name}"
            text = render_entry(reg, n, title, actor, at, created=date.fromisoformat(at[:10]),
                                allocated_for=by)
            return self._change(
                reg, read, {rel: text}, f"Allocate {reg.cite(n)} ({reg.entry_id(n)}) in "
                f"register {reg.name}: {title}", data=reg.cite(n),
            )

        return self.service.sync_and_push(mutate)

    def fill(
        self, reg: Register, n: int, text: str, actor: str,
        relations: list[tuple[str, int]],
    ) -> Outcome:
        text = normalise_text(text)
        if not text.strip():
            raise InputRefused("the text is empty")
        for kind, _ in relations:
            if kind not in reg.relations:
                raise InputRefused(f"register '{reg.name}' does not allow --{kind}")
        at = _now()

        def mutate(read: Read, attempt: int) -> Change | NoOp | Refuse:
            found = self._entry_at(reg, read, n)
            if isinstance(found, Refuse):
                return found
            rel, content = found
            entry = parse_entry(reg, rel, content)
            if entry is None:
                return Refuse(f"{rel} is no entry of register '{reg.name}'")
            if entry.status == VOID:
                return Refuse(f"{reg.cite(n)} is void: a gap is never filled")
            if entry.text is not None or entry.digest is not None:
                return Refuse(f"{reg.cite(n)} is already filled: its text is written once")
            numbered = self._numbered(reg, read.rev)
            lines = ""
            for kind, t in relations:
                if t == n:
                    return Refuse(f"{reg.cite(n)} cannot {kind[:-1]} itself ({t})")
                if t not in numbered:
                    return Refuse(f"--{kind} {t}: {reg.cite(t)} is not an entry of register "
                                  f"'{reg.name}'")
                if t > n:
                    return Refuse(f"--{kind} {t}: {reg.cite(t)} is newer than {reg.cite(n)}; "
                                  "a relation points to an older entry")
                lines += f"<> kb:{kind} item:{reg.entry_id(t)} .\n"
            heading = content.find(_TEXT_HEADING)
            if heading < 0:
                return Refuse(f"{rel} has no `## Text` section")
            cut = heading + len(_TEXT_HEADING)
            new = content[:cut] + text + "\n\n" + content[cut:]
            new = _append(new, lines + _event(
                reg.statuses[0], actor, at, textDigest=text_digest(text)))
            return self._change(reg, read, {rel: new},
                                f"Fill {reg.cite(n)} in register {reg.name}", data=reg.cite(n))

        return self.service.sync_and_push(mutate)

    def set_event(
        self, reg: Register, n: int, actor: str, status: str | None, title: str | None
    ) -> Outcome:
        if status is None and title is None:
            raise InputRefused("give --status, --title or both")
        if status is not None and status not in reg.statuses:
            raise InputRefused(
                f"status {status!r} is not one of register '{reg.name}''s: "
                + ", ".join(reg.statuses)
            )
        if title is not None:
            title = _check_line(title, "--title")
        at = _now()

        def mutate(read: Read, attempt: int) -> Change | NoOp | Refuse:
            found = self._entry_at(reg, read, n)
            if isinstance(found, Refuse):
                return found
            rel, content = found
            entry = parse_entry(reg, rel, content)
            if entry is None:
                return Refuse(f"{rel} is no entry of register '{reg.name}'")
            if status is not None and entry.status in (RESERVED, VOID):
                return Refuse(f"{reg.cite(n)} is {entry.status}: fill it before setting a status")
            if (status in (None, entry.status)) and (title in (None, entry.title)):
                return NoOp(f"{reg.cite(n)} already says so")
            extra = {} if title is None else {"title": title}
            new = _append(content, _event(status or entry.status, actor, at, **extra))
            what = ", ".join(x for x in (status, title and f"title {title!r}") if x)
            return self._change(reg, read, {rel: new},
                                f"Set {reg.cite(n)} in register {reg.name}: {what}",
                                data=reg.cite(n))

        return self.service.sync_and_push(mutate)

    def import_list(self, reg: Register, source: str, pattern: str, actor: str) -> Outcome:
        try:
            rx = re.compile(pattern, re.M)
        except re.error as e:
            raise InputRefused(f"--pattern does not compile: {e}") from None
        if "n" not in rx.groupindex:
            raise InputRefused("--pattern needs a named group `n`: the entry's number")
        heads = list(rx.finditer(source))
        if not heads:
            raise InputRefused("--pattern matched no heading in the file")
        found: dict[int, dict[str, Any]] = {}
        for i, m in enumerate(heads):
            n = self.number(reg, m.group("n"))
            if n in found:
                raise InputRefused(f"{reg.cite(n)} appears twice in the file")
            end = heads[i + 1].start() if i + 1 < len(heads) else len(source)
            groups = m.groupdict()
            found[n] = {
                "text": normalise_text(source[m.end(): end]),
                "title": (groups.get("title") or "").strip() or reg.cite(n),
                "date": (groups.get("date") or "").strip() or None,
            }
        at = _now()

        def mutate(read: Read, attempt: int) -> Change | NoOp | Refuse:
            numbered = self._numbered(reg, read.rev)
            folder = self._git_rel(reg.path)
            files: dict[str, str] = {}
            for n in range(1, max(found) + 1):
                if n in numbered:
                    spec = found.get(n)
                    if spec is None or not spec["text"] or len(numbered[n]) != 1:
                        continue
                    content = read(numbered[n][0])
                    held = None if content is None else parse_entry(reg, numbered[n][0],
                                                                    content)
                    if held is not None and held.text is None:
                        return Refuse(
                            f"{reg.cite(n)} is {held.status}: import does not fill a "
                            "held number (use `register fill`, or leave it out of the "
                            "file); nothing was imported"
                        )
                    if held is not None and held.text is not None and (
                        normalise_text(held.text) != spec["text"]
                    ):
                        return Refuse(
                            f"{reg.cite(n)} is already filled with other text than the "
                            "file gives it: an entry's text never changes; nothing was "
                            "imported"
                        )
                    continue
                spec = found.get(n)
                if spec is None:
                    title, status, text, created = f"{reg.cite(n)} (void)", VOID, None, None
                else:
                    title, created = spec["title"], spec["date"]
                    text = spec["text"] or None
                    status = reg.statuses[0] if text else RESERVED
                try:
                    created = date.fromisoformat(created) if created else None
                except ValueError:
                    pass
                slug = _slug(title)
                name = reg.entry_id(n) + (f"-{slug}" if slug else "") + ".md"
                files[f"{folder}/{name}"] = render_entry(
                    reg, n, title, actor, at, created=created, text=text, status=status
                )
            if not files:
                return NoOp(f"register {reg.name} already holds every imported number")
            cites = ", ".join(reg.cite(self._number_of_name(reg, f) or 0) for f in files)
            return self._change(reg, read, files, f"Import {len(files)} entries into "
                                f"register {reg.name}: {cites}")

        return self.service.sync_and_push(mutate)


def _append_only_violation(old: str, new: str) -> str | None:
    """Why `new` is not `old` grown as an entry may grow, or None (#1294 review r2).

    Structural, not a line diff: `new` must be `old` with insertions at exactly two
    cut points. (1) Immediately before the old yurtle block's closing fence: whole
    lines, none starting with three backticks, so no insertion can close the block
    early or open another. (2) Only when `old` had no text and no digest (the first
    fill): right after the `## Text` heading line, any text at all."""
    span = _block_span(old)
    heading = old.find(_TEXT_HEADING[:-1])  # "\n## Text\n"
    if span is None or heading < 0 or heading >= span[0]:
        return "the entry at the base had no `## Text` section and yurtle block"
    close = span[1]
    cut = heading + len(_TEXT_HEADING) - 1
    body = normalise_text(old[cut: span[0] - len(_BLOCK_OPEN)])
    first_fill = not body.strip() and "kb:textDigest" not in old[span[0]: close]
    head, mid, tail = old[:cut], old[cut:close], old[close:]
    if not (new.startswith(head) and new.endswith(tail)
            and len(new) >= len(head) + len(mid) + len(tail)):
        return "a line outside the yurtle block (or the block's own lines) changed"
    middle = new[len(head): len(new) - len(tail)]
    starts = [0]
    if first_fill:  # wherever the old rest of the file follows the inserted text
        starts, k = [], middle.find(mid)
        while k >= 0:
            starts.append(k)
            k = middle.find(mid, k + 1)
    for k in starts:
        if not middle.startswith(mid, k):
            continue
        appended = middle[k + len(mid):]
        if appended and not appended.endswith("\n"):
            continue
        if any(line.startswith("```") for line in appended.splitlines()):
            continue
        return None
    if not middle.startswith(mid) and not first_fill:
        return "text was inserted, but the entry already had its text"
    return "lines were inserted other than before the yurtle block's closing fence"


def _glob_regex(glob: str) -> re.Pattern[str]:
    """A `scan` glob as a regex over repo-relative posix paths: `**` spans
    folders, `*` and `?` stay within one."""
    out = ""
    i = 0
    while i < len(glob):
        if glob.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
        elif glob.startswith("**", i):
            out += ".*"
            i += 2
        elif glob[i] == "*":
            out += "[^/]*"
            i += 1
        elif glob[i] == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(glob[i])
            i += 1
    return re.compile(out)


# --- CLI ------------------------------------------------------------------------------


def _open(name: str) -> tuple[Registers, Register]:
    from .cli import get_service

    regs = Registers(get_service())
    return regs, regs.get(name)


def _actor(agent: str | None, regs: Registers) -> str:
    return resolve_actor(agent, cwd=regs.root)


def _finish(outcome: Outcome) -> None:
    """Print a write's outcome: the cite form on stdout, the message on stderr;
    a refusal exits with the outcome's code."""
    code = int(outcome.exit_code)
    if code != 0:
        refuse(outcome.message, exit_code=code)
    if outcome.data is not None and outcome.kind != "noop":
        click.echo(str(outcome.data))
    click.echo(outcome.message, err=True)


@click.group(cls=Group)
def register() -> None:
    """Append-only numbered registers: decisions, directives, numbered lists (#1294).

    Each entry is a file under the register's `path`; configure registers under
    `registers:` in the board config. Entries are never work items.
    """


@register.command("alloc")
@click.argument("name")
@click.option("--title", required=True, help="The entry's one-line title")
@click.option("--by", "by", help="The item this number is taken for (kb:allocatedFor)")
@click.option("--agent", help="Who allocates (else $YURTLE_AGENT, then git user.name)")
def register_alloc(name: str, title: str, by: str | None, agent: str | None) -> None:
    """Take the next number: one pushed commit creating a reserved entry; prints it."""
    regs, reg = _open(name)
    _finish(regs.alloc(reg, title, _actor(agent, regs), by))


def _relation_numbers(regs: Registers, reg: Register, kind: str, values: tuple[str, ...]
                      ) -> list[tuple[str, int]]:
    out = []
    for value in values:
        for token in filter(None, (t.strip() for t in value.split(","))):
            out.append((kind, regs.number(reg, token)))
    return out


@register.command("fill")
@click.argument("name")
@click.argument("number")
@click.option("--text-file", required=True, help="The verbatim text (- for stdin)")
@click.option("--narrows", multiple=True, help="Older entries this one narrows (N,…)")
@click.option("--supersedes", multiple=True, help="Older entries this one supersedes (N,…)")
@click.option("--agent", help="Who fills (else $YURTLE_AGENT, then git user.name)")
def register_fill(
    name: str, number: str, text_file: str, narrows: tuple[str, ...],
    supersedes: tuple[str, ...], agent: str | None,
) -> None:
    """Write an entry's text once, with its relations to older entries."""
    regs, reg = _open(name)
    n = regs.number(reg, number)
    try:
        if text_file == "-":
            text = sys.stdin.read()
        else:
            text = Path(text_file).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise InputRefused(f"--text-file {text_file}: {e}") from None
    relations = (_relation_numbers(regs, reg, "narrows", narrows)
                 + _relation_numbers(regs, reg, "supersedes", supersedes))
    _finish(regs.fill(reg, n, text, _actor(agent, regs), relations))


@register.command("set")
@click.argument("name")
@click.argument("number")
@click.option("--status", help="A status the register configures")
@click.option("--title", help="A new one-line title")
@click.option("--agent", help="Who sets (else $YURTLE_AGENT, then git user.name)")
def register_set(
    name: str, number: str, status: str | None, title: str | None, agent: str | None
) -> None:
    """Append a status or title event to an entry."""
    regs, reg = _open(name)
    _finish(regs.set_event(reg, regs.number(reg, number), _actor(agent, regs), status, title))


@register.command("show")
@click.argument("name")
@click.argument("number")
def register_show(name: str, number: str) -> None:
    """Show one entry: title, status, what relates to it, and its text."""
    regs, reg = _open(name)
    n = regs.number(reg, number)
    entry = regs.one(reg, n)
    derived = regs.derived(reg, regs.entries(reg)[0]).get(n, [])
    click.echo(f"{reg.cite(n)} ({entry.item_id}): {entry.title}")
    click.echo(f"status: {entry.status}")
    if entry.created:
        click.echo(f"date: {entry.created}")
    if entry.allocated_for:
        click.echo(f"allocated for: {entry.allocated_for}")
    for line in derived:
        click.echo(line)
    click.echo(f"file: {entry.path}")
    if entry.text is not None:
        click.echo("")
        click.echo(entry.text)


@register.command("list")
@click.argument("name")
def register_list(name: str) -> None:
    """List every entry with its status and title."""
    regs, reg = _open(name)
    entries = regs.entries(reg)[0]
    if not entries:
        click.echo(f"register {reg.name}: no entries")
        return
    for n, held in entries.items():
        for entry in held:
            click.echo(f"{reg.cite(n)}\t{entry.status}\t{entry.title}")


@register.command("index")
@click.argument("name")
@click.option("--write", "write", is_flag=True, help="Write it to the register's `index:` path")
def register_index(name: str, write: bool) -> None:
    """Print the generated markdown index (or write it to the configured path)."""
    regs, reg = _open(name)
    text = regs.index(reg)
    if not write:
        click.echo(text, nl=False)
        return
    if reg.index is None:
        raise InputRefused(f"register '{reg.name}' configures no `index:` path")
    path = regs.root / reg.index
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    click.echo(f"Wrote {reg.index}", err=True)


@register.command("cites")
@click.argument("name")
@click.argument("number")
def register_cites(name: str, number: str) -> None:
    """List the files (and lines) that cite an entry, exemptions aside."""
    regs, reg = _open(name)
    n = regs.number(reg, number)
    found, _, unreadable = regs.cites(reg)
    for problem in unreadable:
        click.echo(f"warning: {problem}", err=True)
    hits = [(rel, line) for rel, line, m, _ in found if m == n]
    if not hits:
        click.echo(f"no file cites {reg.cite(n)}")
    for rel, line in hits:
        click.echo(f"{rel}:{line}")


@register.command("import")
@click.argument("name")
@click.option("--from", "source", required=True, help="A markdown file holding a numbered list")
@click.option("--pattern", required=True,
              help="Regex matching each entry's heading line: groups n, and date, title")
@click.option("--agent", help="Who imports (else $YURTLE_AGENT, then git user.name)")
def register_import(name: str, source: str, pattern: str, agent: str | None) -> None:
    """Split a numbered markdown list into entries, one pushed commit; gaps become
    void entries; numbers already held are left alone."""
    regs, reg = _open(name)
    try:
        text = Path(source).read_text(encoding="utf-8").replace("\r\n", "\n")
    except (OSError, UnicodeDecodeError) as e:
        raise InputRefused(f"--from {source}: {e}") from None
    _finish(regs.import_list(reg, text, pattern, _actor(agent, regs)))


def register_problems(service: KanbanService, since: str | None) -> list[dict[str, str]]:
    """`validate`'s issues for the registers: a bad config, else each problem."""
    try:
        regs = Registers(service)
    except InputRefused as e:
        return [{"type": "bad_register_config", "message": f"{e} (#1294)"}]
    try:
        return [{"type": "register", "message": m} for m in regs.problems(since)]
    except (InputRefused, OSError, subprocess.SubprocessError) as e:
        return [{"type": "register", "message": str(e)}]


__all__ = [
    "ENTRY_TYPE", "Entry", "Register", "Registers", "load_registers", "register",
    "register_prefixes", "register_problems",
]
