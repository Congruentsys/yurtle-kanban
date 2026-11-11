"""Issue #1294: append-only numbered registers (decisions, directives, numbered lists),
stored as item files.

Spec: the issue's Proposal, with the driver's resolutions of its open questions:

1. the hidden ``directive`` type is left unchanged; register entries are a separate,
   non-work kind (``kb:RegisterEntry``), configured per register;
2. ``sequence:`` is ``integer`` (default) or ``alpha`` (a, b, …, z, aa, ab, …);
3. one file per entry;
4. #1290 is not a prerequisite: a register has its own id space and a reservation is a file;
5. exemptions live in the config-named ``exempt:`` file, rows (path, line, number,
   reason); a stale row is a ``validate`` error.

Real git throughout: a bare origin (default branch ``main``) and two clones, A (under
test) and B (the rival), as the #585/#1288 harnesses build them, with a v2 config holding
a nautical ``development`` board and two registers:

- ``decisions`` (``DEC``, cited ``D{n}``, integer, statuses ``[open, decided]``,
  relations ``[narrows, supersedes]``) at ``registers/decisions/``, outside every board;
- ``steps`` (``STEP``, cited ``S3{n}``, ``alpha``, default statuses) at
  ``kanban-work/registers/steps/``, INSIDE the board's path, so "entries are not work"
  is tested where the board's recursive scan does see the files.

Spellings the test partner chose where the issue gives none (the driver may challenge):

a. ``register alloc NAME --title T [--by ITEM] --agent A`` prints the number in
   ``cite_form`` (``D1``, ``S3aa``).
b. ``register fill NAME N --text-file F [--narrows N,…] [--supersedes N,…] --agent A``;
   ``register set NAME N --status S [--title T] --agent A``; N is the bare number
   (``3``), not the cite form.
c. ``register show NAME N``, ``register list NAME``, ``register cites NAME N`` print
   plain text: ``show`` names the cite form, title, status, the verbatim text and the
   derived "superseded by N" / "narrowed by N" (N bare or in cite form); ``cites`` names
   each citing file by its repo-relative path.
d. ``register index NAME`` prints the generated markdown; ``--write`` writes it to the
   register's ``index:`` path in the caller's tree (committing it is the caller's
   business; a write that commits or pushes itself is accepted too). The file written
   equals what the plain verb prints (trailing whitespace aside).
e. ``register import NAME --from FILE --pattern RE --agent A``: RE is a Python regex
   applied with ``re.MULTILINE`` that matches each entry's heading line, with named
   groups ``n`` (required), ``date`` and ``title`` (optional); an entry's text is the
   lines after its heading up to the next heading. Whether import pushes, commits
   locally or only writes files is not pinned: the tests publish whatever it leaves.
f. ``validate --since REV`` is a new option of the existing ``validate``.
g. The exempt file is a YAML list of mappings ``{path, line, number, reason}``;
   ``number`` is the bare number. An empty list (``[]``) means no exemptions.
h. An entry file's frontmatter ``id:`` is ``<id_prefix>-<number>`` (zero padding
   allowed, as ``_format_id`` pads), and a relation is written on the NEWER entry on a
   line containing ``narrows``/``supersedes`` and the target's id. That is used only to
   PLANT broken relations for ``validate``.
i. The graph: ``query --sparql … --json`` (``UnifiedGraph``) has each entry as a
   ``kb:RegisterEntry`` with ``kb:id`` and ``kb:register "<name>"``; ``kb:narrows`` /
   ``kb:supersedes`` from the newer entry; and the derived inverse ``kb:supersededBy``
   from the older one.
j. A refusal is a clean exit with a non-zero code that is NOT click's usage error (2), so
   a missing verb or option can never pass a refusal test.
k. ``set`` "appends an event": the old file's lines all survive, in order (a diff of
   inserts only), and the new text holds ``kb:statusChange``.
l. A refused config names the offending key, through ``validate`` and ``register list``.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner, Result

from tests.issues.test_574_claim import frontmatter, item_text
from tests.issues.test_574_sync_and_push import commit_files, dirty_feature_branch, snapshot
from tests.issues.test_585_create_push_loop import PushHook, _configure, git
from yurtle_kanban.cli import main

pytestmark = pytest.mark.usefixtures("claim_env")

_ORIG_RUN = subprocess.run
A = "agent-A"
B = "agent-B"
ITEM_ID = "EXP-001"
ITEM = f"kanban-work/expeditions/{ITEM_ID}-x.md"
DEC_DIR = "registers/decisions/"
STEP_DIR = "kanban-work/registers/steps/"
EXEMPT = "docs/registers/decisions-exempt.yaml"
INDEX = "docs/DECISIONS.md"

DECISIONS = f"""\
  - name: decisions
    path: {DEC_DIR}
    id_prefix: DEC
    cite_form: "D{{n}}"
    cite_pattern: '(?<![A-Za-z0-9_])D([1-9][0-9]*)(?![A-Za-z0-9_])'
    scan: ["docs/**"]
    statuses: [open, decided]
    relations: [narrows, supersedes]
    exempt: {EXEMPT}
    sequence: integer
"""
STEPS = f"""\
  - name: steps
    path: {STEP_DIR}
    id_prefix: STEP
    cite_form: "S3{{n}}"
    cite_pattern: '(?<![A-Za-z0-9_])S3([a-z]+)(?![A-Za-z0-9_])'
    scan: ["docs/**"]
    sequence: alpha
"""
BOARDS = """\
version: "2.0"
boards:
  - name: development
    preset: nautical
    path: kanban-work/
default_board: development
"""


def config_text(registers: str | None = None, extra_decisions: str = "") -> str:
    regs = DECISIONS + extra_decisions + STEPS if registers is None else registers
    return BOARDS + "registers:\n" + regs


# --- harness ---------------------------------------------------------------------


class Reg:
    """A bare origin plus clones A (under test) and B (the rival)."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp = tmp_path
        self.remote = tmp_path / "remote.git"
        self.a = tmp_path / "A"
        self.b = tmp_path / "B"
        git(tmp_path, "init", "--bare", "-b", "main", str(self.remote))
        git(tmp_path, "clone", str(self.remote), str(self.a))
        _configure(self.a)
        git(self.a, "checkout", "-B", "main")
        files = {
            ".kanban/config.yaml": config_text(),
            ITEM: item_text("ready"),
            "kanban-work/chores/.gitkeep": "",
            DEC_DIR + ".gitkeep": "",
            STEP_DIR + ".gitkeep": "",
            EXEMPT: "[]\n",
            "README.md": "readme\n",
        }
        for rel, text in files.items():
            (self.a / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.a / rel).write_text(text)
        git(self.a, "add", "-A")
        git(self.a, "commit", "-m", "seed")
        git(self.a, "push", "-u", "origin", "main")
        git(tmp_path, "clone", str(self.remote), str(self.b))
        _configure(self.b)

    def sha(self) -> str:
        return git(self.remote, "rev-parse", "main").strip()

    def show(self, path: str, rev: str = "main") -> str:
        return git(self.remote, "show", f"{rev}:{path}")

    def files(self, prefix: str, rev: str = "main") -> list[str]:
        return [
            p for p in git(self.remote, "ls-tree", "-r", "--name-only", rev).split()
            if p.startswith(prefix) and p.endswith(".md")
        ]

    def entries(self, prefix: str = DEC_DIR, rev: str = "main") -> dict[int, list[str]]:
        """number -> [paths] of the integer register's entry files on origin (reading h)."""
        found: dict[int, list[str]] = {}
        for path in self.files(prefix, rev):
            item_id = str(frontmatter(self.show(path, rev)).get("id", ""))
            m = re.fullmatch(r"DEC-0*(\d+)", item_id)
            assert m, f"{path}: frontmatter id {item_id!r} is not DEC-<n>"
            found.setdefault(int(m.group(1)), []).append(path)
        return found

    def entry(self, n: int) -> str:
        paths = self.entries().get(n, [])
        assert len(paths) == 1, f"want one file for D{n} on origin, got {paths}"
        return paths[0]

    def entry_id(self, n: int) -> str:
        return str(frontmatter(self.show(self.entry(n)))["id"])


@pytest.fixture
def reg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Reg:
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    return Reg(tmp_path)


def flat(text: str | None) -> str:
    return " ".join((text or "").split())


def yk(cwd: Path, monkeypatch: pytest.MonkeyPatch, *argv: str) -> Result:
    monkeypatch.chdir(cwd)
    return CliRunner().invoke(main, list(argv))


def ok(result: Result) -> str:
    out = flat(result.output)
    assert result.exit_code == 0, f"exit {result.exit_code}: {result.exception!r}\n{out}"
    return out


def refused(result: Result) -> str:
    """A clean refusal (reading j): non-zero, not a usage error, no traceback."""
    out = flat(result.output)
    assert result.exception is None or isinstance(result.exception, SystemExit), (
        f"uncaught {result.exception!r}: {out}"
    )
    assert result.exit_code not in (0, 2), f"exit {result.exit_code}, want a refusal: {out}"
    assert "No such command" not in out and "No such option" not in out, out
    return out


def sync(clone: Path) -> None:
    git(clone, "fetch", "-q", "origin")
    git(clone, "checkout", "-q", "main")
    git(clone, "reset", "-q", "--hard", "origin/main")


def push_files(reg: Reg, files: dict[str, str | None], message: str) -> str:
    """Commit `files` (None deletes) on A's main and push; returns the new origin sha."""
    sync(reg.a)
    for rel, text in files.items():
        path = reg.a / rel
        if text is None:
            git(reg.a, "rm", "-q", rel)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    git(reg.a, "add", "-A")
    git(reg.a, "commit", "-q", "-m", message)
    git(reg.a, "push", "-q", "origin", "HEAD:refs/heads/main")
    return reg.sha()


def publish(reg: Reg, message: str) -> None:
    """Publish whatever a local-writing verb left in A (reading d, e)."""
    if git(reg.a, "status", "--porcelain", "--untracked-files=all").strip():
        git(reg.a, "add", "-A")
        git(reg.a, "commit", "-q", "-m", message)
    branch = git(reg.a, "symbolic-ref", "--short", "HEAD").strip()
    if git(reg.a, "rev-list", f"origin/{branch}..HEAD").strip():
        git(reg.a, "push", "-q", "origin", f"HEAD:refs/heads/{branch}")
    sync(reg.a)


def alloc(reg: Reg, mp: pytest.MonkeyPatch, title: str, name: str = "decisions",
          *extra: str, agent: str = A) -> str:
    sync(reg.a)
    return ok(yk(reg.a, mp, "register", "alloc", name, "--title", title, *extra,
                 "--agent", agent))


def text_file(reg: Reg, body: str, name: str = "text.md") -> str:
    path = reg.tmp / name
    path.write_text(body)
    return str(path)


def fill(reg: Reg, mp: pytest.MonkeyPatch, n: int, body: str, *extra: str) -> Result:
    sync(reg.a)
    return yk(reg.a, mp, "register", "fill", "decisions", str(n), "--text-file",
              text_file(reg, body, f"text-{n}.md"), *extra, "--agent", A)


def show(reg: Reg, mp: pytest.MonkeyPatch, n: int, name: str = "decisions") -> str:
    sync(reg.a)
    return ok(yk(reg.a, mp, "register", "show", name, str(n)))


def validate(reg: Reg, mp: pytest.MonkeyPatch, *extra: str) -> Result:
    sync(reg.a)
    return yk(reg.a, mp, "validate", *extra)


def one_commit(reg: Reg, base: str, under: str = DEC_DIR) -> list[str]:
    """Exactly one new commit on origin; it touches the register (and maybe .kanban/)."""
    tip = reg.sha()
    assert git(reg.remote, "rev-list", f"{base}..{tip}").split() == [tip], (
        "want exactly one new commit on origin's main"
    )
    files = commit_files(reg.remote, tip)
    assert any(f.startswith(under) for f in files), files
    stray = [f for f in files if not (f.startswith(under) or f.startswith(".kanban/"))]
    assert stray == [], f"the commit touched files outside the register: {stray}"
    return files


def three_reserved(reg: Reg, mp: pytest.MonkeyPatch) -> None:
    for title in ("First", "Second", "Third"):
        alloc(reg, mp, title)


TEXT1 = "The verbatim ruling text, line one.\nAnd its second line.\n"
TEXT2 = "Second ruling: supersedes the first.\n"
TEXT3 = "Third ruling: narrows the first.\n"


def related_three(reg: Reg, mp: pytest.MonkeyPatch) -> None:
    """D1 filled; D2 supersedes D1; D3 narrows D1."""
    three_reserved(reg, mp)
    ok(fill(reg, mp, 1, TEXT1))
    ok(fill(reg, mp, 2, TEXT2, "--supersedes", "1"))
    ok(fill(reg, mp, 3, TEXT3, "--narrows", "1"))


def sparql(reg: Reg, mp: pytest.MonkeyPatch, q: str) -> list[dict[str, str]]:
    sync(reg.a)
    out = yk(reg.a, mp, "query", "--sparql", q, "--json")
    assert out.exit_code == 0, out.output
    return json.loads(out.stdout)


def num(item_id: str) -> int:
    m = re.search(r"(\d+)$", item_id)
    assert m, item_id
    return int(m.group(1))


# --- the verbs exist ----------------------------------------------------------------


def test_register_group_lists_its_verbs() -> None:
    out = ok(CliRunner().invoke(main, ["register", "--help"]))
    for verb in ("alloc", "fill", "set", "show", "list", "index", "cites", "import"):
        assert re.search(rf"\b{verb}\b", out), f"`register {verb}` missing: {out}"


def test_validate_has_since_option() -> None:
    out = ok(CliRunner().invoke(main, ["validate", "--help"]))
    assert "--since" in out, out


# --- config ---------------------------------------------------------------------------


def test_good_config_lists_empty_registers(reg, monkeypatch) -> None:
    for name in ("decisions", "steps"):
        ok(yk(reg.a, monkeypatch, "register", "list", name))
    ok(validate(reg, monkeypatch))


def test_unknown_register_is_refused(reg, monkeypatch) -> None:
    out = refused(yk(reg.a, monkeypatch, "register", "list", "nonesuch"))
    assert "nonesuch" in out, out


_ONE = DECISIONS
BAD_CONFIGS = [
    ("not-a-list", BOARDS + "registers: 5\n", "registers"),
    ("no-name", BOARDS + "registers:\n" + _ONE.replace("  - name: decisions\n    ", "  - "),
     "name"),
    ("duplicate-name", config_text(_ONE + _ONE), "decisions"),
    ("bad-sequence", config_text(_ONE.replace("sequence: integer", "sequence: roman")),
     "sequence"),
    ("regex-does-not-compile",
     config_text(_ONE.replace("cite_pattern: '(?<![A-Za-z0-9_])D([1-9][0-9]*)(?![A-Za-z0-9_])'",
                              "cite_pattern: 'D([0-9'")),
     "cite_pattern"),
    ("regex-has-no-group",
     config_text(_ONE.replace("cite_pattern: '(?<![A-Za-z0-9_])D([1-9][0-9]*)(?![A-Za-z0-9_])'",
                              "cite_pattern: 'D[0-9]+'")),
     "cite_pattern"),
    ("prefix-of-a-work-type", config_text(_ONE.replace("id_prefix: DEC", "id_prefix: EXP")),
     "id_prefix"),
    ("prefix-shared-by-two-registers",
     config_text(_ONE + STEPS.replace("id_prefix: STEP", "id_prefix: DEC")), "id_prefix"),
    ("cite-form-without-n", config_text(_ONE.replace('cite_form: "D{n}"', 'cite_form: "D"')),
     "cite_form"),
]


@pytest.mark.parametrize("case,text,key", BAD_CONFIGS, ids=[c[0] for c in BAD_CONFIGS])
def test_bad_register_config_is_refused(reg, monkeypatch, case, text, key) -> None:
    (reg.a / ".kanban" / "config.yaml").write_text(text)
    out = refused(yk(reg.a, monkeypatch, "register", "list", "decisions"))
    assert key in out, f"{case}: the refusal does not name {key!r}: {out}"
    res = yk(reg.a, monkeypatch, "validate")
    out = flat(res.output)
    assert res.exit_code != 0 and key in out, f"{case}: validate did not refuse: {out}"


# --- alloc ------------------------------------------------------------------------------


def test_alloc_reserves_with_one_cas_commit(reg, monkeypatch) -> None:
    base = reg.sha()
    out = alloc(reg, monkeypatch, "Which licence", "decisions", "--by", ITEM_ID)
    assert re.search(r"(?<![A-Za-z0-9])D1(?![0-9])", out), f"alloc printed no D1: {out}"
    one_commit(reg, base)
    assert sorted(reg.entries()) == [1]
    text = reg.show(reg.entry(1))
    assert "Which licence" in text
    assert "kb:allocatedFor" in text and ITEM_ID in text, text
    shown = show(reg, monkeypatch, 1)
    assert "reserved" in shown.lower() and "Which licence" in shown, shown

    base = reg.sha()
    out = alloc(reg, monkeypatch, "Second question")
    assert re.search(r"(?<![A-Za-z0-9])D2(?![0-9])", out), out
    one_commit(reg, base)
    assert sorted(reg.entries()) == [1, 2]


def test_alloc_never_touches_the_callers_checkout(reg, monkeypatch) -> None:
    dirty_feature_branch(reg)  # A on a feature branch, unstaged, staged and untracked work
    before = snapshot(reg.a)
    base = reg.sha()
    res = yk(reg.a, monkeypatch, "register", "alloc", "decisions", "--title", "From a branch",
             "--agent", A)
    ok(res)
    assert snapshot(reg.a) == before, "alloc changed the caller's checkout"
    one_commit(reg, base)
    assert git(reg.remote, "rev-parse", "--verify", "-q", "refs/heads/feat/side-work",
               check=False).strip() == "", "alloc pushed the feature branch"
    assert sorted(reg.entries()) == [1]


def test_reserved_number_counts_as_taken(reg, monkeypatch) -> None:
    three_reserved(reg, monkeypatch)
    out = alloc(reg, monkeypatch, "Fourth")
    assert re.search(r"(?<![A-Za-z0-9])D4(?![0-9])", out), out
    assert sorted(reg.entries()) == [1, 2, 3, 4]


def _b_alloc(reg: Reg, title: str) -> None:
    """B allocates through the real CLI in its own process (subprocess.run is A's hook)."""
    env = dict(os.environ, PYTHONPATH="", GIT_TERMINAL_PROMPT="0")
    res = _ORIG_RUN(
        [sys.executable, "-m", "yurtle_kanban.cli", "register", "alloc", "decisions",
         "--title", title, "--agent", B],
        cwd=reg.b, capture_output=True, text=True, env=env, check=False,
    )
    assert res.returncode == 0, f"B's alloc failed: {res.stdout}{res.stderr}"


def test_planted_race_gives_two_distinct_numbers(reg, monkeypatch) -> None:
    sync(reg.a)
    git(reg.b, "fetch", "-q", "origin")
    raced: list[int] = []

    def rival(n: int) -> None:
        if n == 1:
            _b_alloc(reg, "Rival from B")
            raced.append(n)

    hook = PushHook(rival)
    monkeypatch.setattr(subprocess, "run", hook)
    res = yk(reg.a, monkeypatch, "register", "alloc", "decisions", "--title", "Mine from A",
             "--agent", A)
    monkeypatch.setattr(subprocess, "run", _ORIG_RUN)
    out = ok(res)
    assert raced == [1], "the rival never ran in A's seam"
    assert hook.pushes >= 2, "A's first push was not rejected and retried: no CAS"
    entries = reg.entries()
    assert sorted(entries) == [1, 2], entries
    assert all(len(p) == 1 for p in entries.values()), f"duplicate number: {entries}"
    assert "Rival from B" in reg.show(entries[1][0])
    assert "Mine from A" in reg.show(entries[2][0])
    assert re.search(r"(?<![A-Za-z0-9])D2(?![0-9])", out), out


def test_control_a_mutant_without_cas_collides(reg, monkeypatch) -> None:
    """The race above is real: an allocator that takes max+1 from its own stale tree and
    merges instead of re-fetching (the mutant) lands two files with number 1, and validate
    sees the duplicate."""
    sync(reg.a)
    stale = reg.tmp / "stale"
    git(reg.tmp, "clone", "-q", str(reg.remote), str(stale))
    _configure(stale)
    mutant_n = len([p for p in (stale / DEC_DIR).glob("*.md")]) + 1  # from the stale tree
    alloc(reg, monkeypatch, "Honest one")  # the real verb takes D1 meanwhile
    assert mutant_n == 1
    twin = reg.show(reg.entry(1)).replace("Honest one", "Mutant one")
    (stale / DEC_DIR / "DEC-001-mutant-one.md").write_text(twin)
    git(stale, "add", "-A")
    git(stale, "commit", "-q", "-m", "mutant alloc")
    git(stale, "pull", "-q", "--no-rebase", "--no-edit", "origin", "main")
    git(stale, "push", "-q", "origin", "HEAD:refs/heads/main")
    assert len(reg.entries()[1]) == 2, "the mutant did not collide"
    res = validate(reg, monkeypatch)
    out = flat(res.output)
    assert res.exit_code != 0, out
    assert re.search(r"duplicate", out, re.I) and re.search(r"D1\b|DEC-0*1\b", out), out


# --- fill ------------------------------------------------------------------------------


def test_fill_writes_text_once_with_digest(reg, monkeypatch) -> None:
    three_reserved(reg, monkeypatch)
    base = reg.sha()
    ok(fill(reg, monkeypatch, 1, TEXT1))
    files = one_commit(reg, base)
    assert files == [reg.entry(1)] or set(files) - {reg.entry(1)} <= {
        f for f in files if f.startswith(".kanban/")}, files
    text = reg.show(reg.entry(1))
    assert "The verbatim ruling text, line one." in text and "And its second line." in text
    assert re.search(r"kb:textDigest\s+\"?[0-9a-f]{64}", text), f"no kb:textDigest:\n{text}"
    shown = show(reg, monkeypatch, 1)
    assert "line one" in shown and "open" in shown.lower(), shown  # first configured status
    assert "reserved" not in shown.lower(), shown

    base = reg.sha()
    out = refused(fill(reg, monkeypatch, 1, "Overwritten text.\n"))
    assert reg.sha() == base, "a second fill wrote to origin"
    assert "Overwritten" not in reg.show(reg.entry(1))
    assert re.search(r"already|filled|once", out, re.I), out


def test_fill_of_an_unallocated_number_is_refused(reg, monkeypatch) -> None:
    three_reserved(reg, monkeypatch)
    base = reg.sha()
    refused(fill(reg, monkeypatch, 9, "Nothing reserved this.\n"))
    assert reg.sha() == base


@pytest.mark.parametrize("flag,target,why", [
    ("--narrows", "9", "missing"),
    ("--supersedes", "3", "newer"),
    ("--narrows", "2", "itself"),
])
def test_fill_relations_point_to_older_existing_entries(reg, monkeypatch, flag, target,
                                                         why) -> None:
    three_reserved(reg, monkeypatch)
    ok(fill(reg, monkeypatch, 1, TEXT1))
    base = reg.sha()
    before = reg.show(reg.entry(2))
    out = refused(fill(reg, monkeypatch, 2, TEXT2, flag, target))
    assert reg.sha() == base, f"{why}: the refused fill wrote to origin"
    assert reg.show(reg.entry(2)) == before
    assert target in out, out
    # control: the same fill to an older, existing entry lands
    ok(fill(reg, monkeypatch, 2, TEXT2, flag, "1"))


def test_relations_live_on_the_newer_entry_and_old_files_never_change(reg, monkeypatch) -> None:
    three_reserved(reg, monkeypatch)
    ok(fill(reg, monkeypatch, 1, TEXT1))
    old = reg.show(reg.entry(1))
    ok(fill(reg, monkeypatch, 2, TEXT2, "--supersedes", "1"))
    ok(fill(reg, monkeypatch, 3, TEXT3, "--narrows", "1"))
    assert reg.show(reg.entry(1)) == old, "a later entry edited the older file"
    assert "kb:supersedes" in reg.show(reg.entry(2))
    assert "kb:narrows" in reg.show(reg.entry(3))
    shown = show(reg, monkeypatch, 1)
    assert re.search(r"superseded by D?2\b", shown, re.I), shown
    assert re.search(r"narrowed by D?3\b", shown, re.I), shown


# --- set --------------------------------------------------------------------------------


def _insert_only(old: str, new: str) -> None:
    ops = difflib.SequenceMatcher(a=old.splitlines(), b=new.splitlines(),
                                  autojunk=False).get_opcodes()
    changed = [op for op in ops if op[0] != "equal"]
    assert changed and all(op[0] == "insert" for op in changed), (
        f"not a pure append: {changed}\n--- old\n{old}\n--- new\n{new}"
    )


def test_set_appends_status_and_title_events(reg, monkeypatch) -> None:
    three_reserved(reg, monkeypatch)
    ok(fill(reg, monkeypatch, 1, TEXT1))
    path = reg.entry(1)

    old = reg.show(path)
    base = reg.sha()
    ok(yk(reg.a, monkeypatch, "register", "set", "decisions", "1", "--status", "decided",
          "--agent", A))
    assert commit_files(reg.remote, reg.sha()) == [path] and \
        git(reg.remote, "rev-list", f"{base}..main").split() == [reg.sha()]
    new = reg.show(path)
    _insert_only(old, new)
    assert "kb:statusChange" in new and A in new, new
    assert "decided" in show(reg, monkeypatch, 1).lower()

    old = new
    sync(reg.a)
    ok(yk(reg.a, monkeypatch, "register", "set", "decisions", "1", "--status", "decided",
          "--title", "Licence chosen", "--agent", A))
    new = reg.show(path)
    _insert_only(old, new)
    assert "Licence chosen" in show(reg, monkeypatch, 1)
    assert "line one" in show(reg, monkeypatch, 1)  # the text never changes


def test_set_refuses_an_unconfigured_status(reg, monkeypatch) -> None:
    three_reserved(reg, monkeypatch)
    ok(fill(reg, monkeypatch, 1, TEXT1))
    base = reg.sha()
    sync(reg.a)
    out = refused(yk(reg.a, monkeypatch, "register", "set", "decisions", "1", "--status",
                     "bogus", "--agent", A))
    assert "bogus" in out and reg.sha() == base, out


# --- show / list ---------------------------------------------------------------------------


def test_list_names_every_entry_and_its_status(reg, monkeypatch) -> None:
    three_reserved(reg, monkeypatch)
    ok(fill(reg, monkeypatch, 1, TEXT1))
    sync(reg.a)
    out = ok(yk(reg.a, monkeypatch, "register", "list", "decisions"))
    for token in ("D1", "D2", "D3", "First", "Second", "Third", "reserved", "open"):
        assert token in out, f"{token!r} not in list: {out}"


# --- index ---------------------------------------------------------------------------------


def _with_index(reg: Reg) -> None:
    push_files(reg, {".kanban/config.yaml": config_text(
        DECISIONS.replace("    sequence: integer\n",
                          f"    sequence: integer\n    index: {INDEX}\n") + STEPS)},
        "configure the index")


def test_index_is_generated_and_derives_statuses(reg, monkeypatch) -> None:
    _with_index(reg)
    related_three(reg, monkeypatch)
    old = reg.show(reg.entry(1))
    sync(reg.a)
    res = yk(reg.a, monkeypatch, "register", "index", "decisions")
    ok(res)
    printed = res.output
    for token in ("D1", "D2", "D3", "First", "Second", "Third"):
        assert token in printed, f"{token!r} not in the index:\n{printed}"
    row1 = next(line for line in printed.splitlines() if re.search(r"\bD1\b", line))
    assert re.search(r"superseded by D?2\b", row1, re.I), row1
    assert re.search(r"narrowed by D?3\b", row1, re.I), row1
    assert reg.show(reg.entry(1)) == old

    ok(yk(reg.a, monkeypatch, "register", "index", "decisions", "--write"))
    written = (reg.a / INDEX).read_text()
    assert written.rstrip() == printed.rstrip(), "--write differs from the generated index"
    publish(reg, "commit the generated index")
    ok(validate(reg, monkeypatch))


def test_validate_refuses_a_stale_or_edited_index(reg, monkeypatch) -> None:
    _with_index(reg)
    related_three(reg, monkeypatch)
    sync(reg.a)
    ok(yk(reg.a, monkeypatch, "register", "index", "decisions", "--write"))
    publish(reg, "commit the generated index")
    ok(validate(reg, monkeypatch))  # control

    # planted: a hand edit of the committed index
    good = reg.show(INDEX)
    push_files(reg, {INDEX: good.replace("Second", "Hand-edited")}, "hand edit")
    res = validate(reg, monkeypatch)
    assert res.exit_code != 0 and INDEX in flat(res.output), flat(res.output)
    push_files(reg, {INDEX: good}, "restore")
    ok(validate(reg, monkeypatch))

    # planted: the register moved on, the committed index did not
    sync(reg.a)
    ok(yk(reg.a, monkeypatch, "register", "set", "decisions", "2", "--status", "open",
          "--title", "Renamed second", "--agent", A))
    res = validate(reg, monkeypatch)
    assert res.exit_code != 0 and INDEX in flat(res.output), flat(res.output)
    sync(reg.a)
    ok(yk(reg.a, monkeypatch, "register", "index", "decisions", "--write"))
    publish(reg, "regenerate")
    ok(validate(reg, monkeypatch))


# --- cites and exemptions ---------------------------------------------------------------------


def _docs(reg: Reg) -> None:
    push_files(reg, {
        "docs/a.md": "We follow D2 here.\n",
        "docs/b.md": "D2 and D3 both apply.\nNot D20, nor xD2.\n",
        "docs/deep/c.md": "Deep cite of D2.\n",
        "other/notes.md": "D2 outside the scan globs.\n",
        EXEMPT: yaml.safe_dump([{"path": "docs/b.md", "line": 2, "number": 20,
                                 "reason": "quotes D20 and D2 as test data"}]),
    }, "docs that cite")


def _cites(reg: Reg, mp: pytest.MonkeyPatch, n: int) -> str:
    sync(reg.a)
    return ok(yk(reg.a, mp, "register", "cites", "decisions", str(n)))


def test_cites_scans_globs_with_the_pattern(reg, monkeypatch) -> None:
    three_reserved(reg, monkeypatch)
    _docs(reg)
    out = _cites(reg, monkeypatch, 2)
    for path in ("docs/a.md", "docs/b.md", "docs/deep/c.md"):
        assert path in out, f"{path} not listed: {out}"
    assert "other/notes.md" not in out, "a file outside `scan` was listed"
    assert EXEMPT not in out, "the exempt file itself was listed"
    out3 = _cites(reg, monkeypatch, 3)
    assert "docs/b.md" in out3 and "docs/a.md" not in out3, out3
    ok(validate(reg, monkeypatch))  # D20 on b.md:2 is exempt; D2, D3 exist (reserved)


def test_cites_honours_an_exemption(reg, monkeypatch) -> None:
    three_reserved(reg, monkeypatch)
    _docs(reg)
    assert "docs/a.md" in _cites(reg, monkeypatch, 2)
    push_files(reg, {EXEMPT: yaml.safe_dump([
        {"path": "docs/b.md", "line": 2, "number": 20, "reason": "test data"},
        {"path": "docs/a.md", "line": 1, "number": 2, "reason": "a quoted token"},
    ])}, "exempt a.md")
    out = _cites(reg, monkeypatch, 2)
    assert "docs/a.md" not in out, f"an exempt token was listed as a cite: {out}"
    assert "docs/b.md" in out, out


def test_validate_refuses_an_unregistered_cite(reg, monkeypatch) -> None:
    three_reserved(reg, monkeypatch)
    _docs(reg)
    ok(validate(reg, monkeypatch))  # control
    push_files(reg, {"docs/e.md": "Per D9, we stop.\n"}, "dangling cite")
    res = validate(reg, monkeypatch)
    out = flat(res.output)
    assert res.exit_code != 0 and "docs/e.md" in out and re.search(r"\bD?9\b", out), out
    # an exemption row for it clears the error
    rows = yaml.safe_load(reg.show(EXEMPT)) + [
        {"path": "docs/e.md", "line": 1, "number": 9, "reason": "quoted"}]
    push_files(reg, {EXEMPT: yaml.safe_dump(rows)}, "exempt it")
    ok(validate(reg, monkeypatch))


def test_validate_refuses_a_stale_exemption(reg, monkeypatch) -> None:
    three_reserved(reg, monkeypatch)
    _docs(reg)
    ok(validate(reg, monkeypatch))  # control: b.md:2 holds D20
    push_files(reg, {"docs/b.md": "D2 and D3 both apply.\nNo cite here any more.\n"},
               "the exempt line moved on")
    res = validate(reg, monkeypatch)
    out = flat(res.output)
    assert res.exit_code != 0 and "docs/b.md" in out and re.search(r"exempt|stale", out, re.I), out


# --- import ------------------------------------------------------------------------------------


OLD_LIST = """\
# Directives

## 1 — 2026-10-12 — First rule
Do the first thing.

## 2 — 2026-10-13 — Second rule
Second text
line two.

## 4 — 2026-10-15 — Fourth rule
Fourth text.
"""
PATTERN = r"^## (?P<n>\d+) — (?P<date>\d{4}-\d{2}-\d{2}) — (?P<title>.+)$"


def _import(reg: Reg, mp: pytest.MonkeyPatch, body: str, pattern: str,
            name: str = "decisions") -> None:
    src = reg.tmp / f"old-{name}.md"
    src.write_text(body)
    sync(reg.a)
    ok(yk(reg.a, mp, "register", "import", name, "--from", str(src), "--pattern", pattern,
          "--agent", A))
    publish(reg, f"import {name}")


def test_import_keeps_numbers_dates_text_and_voids_gaps(reg, monkeypatch) -> None:
    _import(reg, monkeypatch, OLD_LIST, PATTERN)
    assert sorted(reg.entries()) == [1, 2, 3, 4]
    s1 = show(reg, monkeypatch, 1)
    assert "First rule" in s1 and "Do the first thing." in s1 and "2026-10-12" in s1, s1
    assert "Second text\nline two." in reg.show(reg.entry(2))
    s2 = show(reg, monkeypatch, 2)
    assert "2026-10-13" in s2 and "line two" in s2, s2
    assert "void" in show(reg, monkeypatch, 3).lower()
    assert "Fourth text." in show(reg, monkeypatch, 4)
    ok(validate(reg, monkeypatch))  # imported text matches its digests
    out = alloc(reg, monkeypatch, "After the import")
    assert re.search(r"(?<![A-Za-z0-9])D5(?![0-9])", out), f"a gap was refilled: {out}"


def test_import_is_idempotent(reg, monkeypatch) -> None:
    _import(reg, monkeypatch, OLD_LIST, PATTERN)
    tip = reg.sha()
    files = {p: reg.show(p) for p in reg.files(DEC_DIR)}
    _import(reg, monkeypatch, OLD_LIST, PATTERN)
    assert reg.sha() == tip, "a second import changed origin"
    assert {p: reg.show(p) for p in reg.files(DEC_DIR)} == files


# --- alpha sequences -----------------------------------------------------------------------------


def test_alpha_sequence_starts_at_a(reg, monkeypatch) -> None:
    out = alloc(reg, monkeypatch, "Step one", "steps")
    assert re.search(r"(?<![A-Za-z0-9])S3a(?![a-z])", out), out
    out = alloc(reg, monkeypatch, "Step two", "steps")
    assert re.search(r"(?<![A-Za-z0-9])S3b(?![a-z])", out), out


def test_alpha_sequence_rolls_z_to_aa(reg, monkeypatch) -> None:
    letters = "abcdefghijklmnopqrstuvwxyz"
    body = "".join(f"### S3{c} — 2026-10-01 — Step {c}\nText of {c}.\n\n" for c in letters)
    _import(reg, monkeypatch, body,
            r"^### S3(?P<n>[a-z]+) — (?P<date>\d{4}-\d{2}-\d{2}) — (?P<title>.+)$", "steps")
    assert len(reg.files(STEP_DIR)) == 26
    out = alloc(reg, monkeypatch, "After z", "steps")
    assert re.search(r"(?<![A-Za-z0-9])S3aa(?![a-z])", out), out
    out = alloc(reg, monkeypatch, "After aa", "steps")
    assert re.search(r"(?<![A-Za-z0-9])S3ab(?![a-z])", out), out


# --- not work -------------------------------------------------------------------------------------


def test_entries_are_not_work(reg, monkeypatch) -> None:
    alloc(reg, monkeypatch, "Step one", "steps")
    alloc(reg, monkeypatch, "Step two", "steps")
    alloc(reg, monkeypatch, "A decision")
    step_ids = [str(frontmatter(reg.show(p))["id"]) for p in reg.files(STEP_DIR)]
    assert len(step_ids) == 2, step_ids
    dec_id = reg.entry_id(1)
    sync(reg.a)

    res = yk(reg.a, monkeypatch, "next", "--json", "--agent", A)
    assert json.loads(res.stdout)["id"] == ITEM_ID, res.output
    board = yk(reg.a, monkeypatch, "board", "--all").output
    assert "STEP" not in board and "Step one" not in board and "DEC" not in board, board
    lst = yk(reg.a, monkeypatch, "list", "--json").stdout
    assert "STEP-" not in lst and "DEC-" not in lst, lst
    ok(validate(reg, monkeypatch))

    for entry_id in (step_ids[0], dec_id):
        base = reg.sha()
        refused(yk(reg.a, monkeypatch, "claim", entry_id, "--agent", A))
        assert reg.sha() == base
        sync(reg.a)
        refused(yk(reg.a, monkeypatch, "move", entry_id, "done", "--agent", A))
        assert reg.sha() == base
        assert not git(reg.a, "status", "--porcelain").strip()


def test_entries_never_take_a_work_number(reg, monkeypatch) -> None:
    """Registers have their own id space, under `id_numbering: global` too (#1288): an
    alloc moves no work-item sequence."""
    push_files(reg, {".kanban/config.yaml": config_text() + "id_numbering: global\n"},
               "one number sequence for every work type")
    for _ in range(3):
        alloc(reg, monkeypatch, "A decision")
    sync(reg.a)
    res = yk(reg.a, monkeypatch, "create", "expedition", "Next work", "--push")
    assert res.exit_code == 0, res.output
    sync(reg.a)
    names = [p.name for p in (reg.a / "kanban-work" / "expeditions").glob("EXP-*.md")]
    assert any(n.startswith("EXP-002") for n in names), names


# --- validate -------------------------------------------------------------------------------------


def test_validate_refuses_a_duplicate_number(reg, monkeypatch) -> None:
    three_reserved(reg, monkeypatch)
    ok(validate(reg, monkeypatch))  # control
    path = reg.entry(2)
    twin = path.replace(".md", "-twin.md")
    push_files(reg, {twin: reg.show(path).replace("Second", "Twin")}, "plant a twin")
    res = validate(reg, monkeypatch)
    out = flat(res.output)
    assert res.exit_code != 0 and re.search(r"D2\b|DEC-0*2\b", out), out


def test_validate_refuses_a_text_digest_mismatch(reg, monkeypatch) -> None:
    three_reserved(reg, monkeypatch)
    ok(fill(reg, monkeypatch, 1, TEXT1))
    ok(validate(reg, monkeypatch))  # control
    path = reg.entry(1)
    push_files(reg, {path: reg.show(path).replace("line one", "line 1")}, "edit filled text")
    res = validate(reg, monkeypatch)
    out = flat(res.output)
    assert res.exit_code != 0 and re.search(r"digest", out, re.I), out


def test_validate_since_refuses_a_non_append_change(reg, monkeypatch) -> None:
    three_reserved(reg, monkeypatch)
    ok(fill(reg, monkeypatch, 1, TEXT1))
    path = reg.entry(1)
    before_set = reg.sha()
    sync(reg.a)
    ok(yk(reg.a, monkeypatch, "register", "set", "decisions", "1", "--status", "decided",
          "--agent", A))
    after_set = reg.sha()
    ok(validate(reg, monkeypatch, "--since", before_set))  # control: an appended event
    # planted: the event is taken back out (an edit that is not an append; digest intact)
    push_files(reg, {path: reg.show(path, before_set)}, "drop the event")
    res = validate(reg, monkeypatch, "--since", after_set)
    out = flat(res.output)
    assert res.exit_code != 0 and path in out, out
    ok(validate(reg, monkeypatch, "--since", reg.sha()))  # nothing changed since the tip


def _relation_line_retarget(reg: Reg, n: int, rel: str, old_id: str, new_id: str) -> None:
    path = reg.entry(n)
    text = reg.show(path)
    lines = [line for line in text.splitlines() if rel in line and old_id in line]
    assert lines, f"no `{rel}` line naming {old_id} in D{n} (reading h):\n{text}"
    push_files(reg, {path: text.replace(old_id, new_id)}, f"retarget D{n}'s {rel}")


def test_validate_refuses_a_relation_to_a_missing_entry(reg, monkeypatch) -> None:
    three_reserved(reg, monkeypatch)
    ok(fill(reg, monkeypatch, 1, TEXT1))
    ok(fill(reg, monkeypatch, 3, TEXT3, "--narrows", "1"))
    ok(validate(reg, monkeypatch))  # control
    id1 = reg.entry_id(1)
    _relation_line_retarget(reg, 3, "narrows", id1, id1.replace("1", "9"))
    res = validate(reg, monkeypatch)
    out = flat(res.output)
    assert res.exit_code != 0 and re.search(r"narrows", out, re.I), out


def test_validate_refuses_a_relation_to_a_newer_entry(reg, monkeypatch) -> None:
    for title in ("One", "Two", "Three", "Four"):
        alloc(reg, monkeypatch, title)
    ok(fill(reg, monkeypatch, 1, TEXT1))
    ok(fill(reg, monkeypatch, 3, TEXT3, "--supersedes", "1"))
    ok(validate(reg, monkeypatch))  # control
    _relation_line_retarget(reg, 3, "supersedes", reg.entry_id(1), reg.entry_id(4))
    res = validate(reg, monkeypatch)
    out = flat(res.output)
    assert res.exit_code != 0 and re.search(r"newer|older|later", out, re.I), out


def test_validate_refuses_a_relation_cycle(reg, monkeypatch) -> None:
    three_reserved(reg, monkeypatch)
    ok(fill(reg, monkeypatch, 1, TEXT1))
    ok(fill(reg, monkeypatch, 2, TEXT2, "--narrows", "1"))
    ok(fill(reg, monkeypatch, 3, TEXT3, "--narrows", "2"))
    ok(validate(reg, monkeypatch))  # control
    _relation_line_retarget(reg, 2, "narrows", reg.entry_id(1), reg.entry_id(3))
    res = validate(reg, monkeypatch)
    out = flat(res.output)
    assert res.exit_code != 0 and re.search(r"cycle", out, re.I), out


# --- the graph ------------------------------------------------------------------------------------


def test_graph_has_entries_relations_and_the_inverse(reg, monkeypatch) -> None:
    related_three(reg, monkeypatch)
    rows = sparql(reg, monkeypatch,
                  "SELECT ?id ?r WHERE { ?e a kb:RegisterEntry ; kb:id ?id ; kb:register ?r }")
    assert sorted(num(r["id"]) for r in rows) == [1, 2, 3], rows
    assert {r["r"] for r in rows} == {"decisions"}, rows

    def pairs(pred: str) -> set[tuple[int, int]]:
        got = sparql(reg, monkeypatch,
                     f"SELECT ?s ?o WHERE {{ ?a kb:{pred} ?b . ?a kb:id ?s . ?b kb:id ?o }}")
        return {(num(r["s"]), num(r["o"])) for r in got}

    assert pairs("supersedes") == {(2, 1)}
    assert pairs("narrows") == {(3, 1)}
    assert pairs("supersededBy") == {(1, 2)}, "the inverse is not derived in the graph"


def test_entries_are_not_typed_as_directives(reg, monkeypatch) -> None:
    """Driver ruling 1: the hidden `directive` type is untouched; entries are their own kind."""
    alloc(reg, monkeypatch, "A decision")
    rows = sparql(reg, monkeypatch, "SELECT ?e WHERE { ?e a kb:Directive }")
    assert rows == [], rows
    rows = sparql(reg, monkeypatch, "SELECT ?e WHERE { ?e a kb:RegisterEntry }")
    assert len(rows) == 1, rows
