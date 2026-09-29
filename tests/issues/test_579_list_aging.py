"""Issue #579: aging on ``list`` (``--older-than``, ``--stale``, ``--stale-after``).

The spec is the issue body as revised after the adversarial review. It follows the
review's simpler alternative: there is no ``flow`` command, and aging lives on ``list``.
The tests below map to Acceptance 1-11.

Summary of the spec:

- ``since`` is when an item entered its current status. It comes from the first
  source that applies, and each row names that source in ``since_source``:
  ``history`` (the last node of the canonical history block, used only when its
  canonical status equals the item's current canonical status), then ``git`` (the
  author date ``%aI`` of the last commit that changed the file's ``status:`` line,
  found with ONE ``git log`` per invocation), then ``created`` (frontmatter
  ``created``, taken as midnight in the reader's local zone), then ``unknown``
  (kept, sorted last, never dropped).
- Stamps are timezone-aware when written (``move`` writes an offset). One read
  helper turns a naive stamp into the reader's local time, and ``metrics`` uses it
  too. A negative age is clamped to 0 with ``clock_skew: true``.
- Only the canonical history block is read. A yurtle block pasted into a comment
  is ignored.
- ``list --older-than D`` shows open (not finished, #575) items with age >= D, and
  unknown-age items last. ``list --stale`` shows canonical in-progress items with
  age >= ``--stale-after`` (default ``24h``), plus in-progress items of unknown age.
  DURATION is ``^\\d+(m|h|d|w)$``; anything else exits 2 and the message states the
  grammar. With either filter, rows are sorted by age, oldest first, with unknown
  last. The human table gains ``Age`` and ``Since`` columns, and each ``--stale``
  row prints ``yurtle-kanban claim ID --take-over --agent <you>``.
- ``--status`` accepts native or canonical names, resolved through each item's theme.
- Every ``list --json`` row carries ``since``, ``since_source``, ``age_seconds``,
  ``stale``, ``clock_skew`` and ``board``. Existing keys are unchanged: ``status``
  stays canonical.

Readings the test partner chose (the driver may challenge them):

a. The injected clock. The spec asks for "an injected, timezone-aware now" but
   names no seam. These tests patch ONE module attribute:
   ``yurtle_kanban.service._now`` (no arguments, returns an aware datetime). The
   aging code must look it up at call time. ``test_clock_seam_exists`` pins it.
   Patching uses ``raising=False``, so when the seam is missing the other tests
   fail on their assertions instead of on a harness error.
b. The subprocess spy for Acceptance 2 replaces ``subprocess.Popen``, which ``run``
   and ``check_output`` go through. It counts calls whose argv has a ``log`` word.
   ``list --json`` over a fixture where three committed items need the git source
   must make exactly one.
c. ``--json`` output with a filter is a JSON list of row objects, like plain
   ``list --json``.
d. ``since`` values are compared as instants. Each must parse with
   ``datetime.fromisoformat`` and carry an offset. Which offset it is written in
   is left open.
e. Acceptance 9's "on an hdd board" is read as ``--board research``: without
   ``--board``, ``--status in_progress`` also matches the nautical board's items,
   and ``active`` is not a nautical name.
f. The Acceptance 5 forgery case uses the writer's exact canonical block shape
   inside a comment, both on an item that has real history (the real block comes
   first) and on one that has none. In both cases ``since`` must not change.

Left open (deliberately not pinned):

- where ``parse_stamp`` lives, and its exact signature;
- ``stale`` for an in-progress item of unknown age (it is listed by ``--stale``);
- the ``board`` value on a single-board repo;
- the placeholder after ``--agent`` in the recovery command (``<you>`` or an actor);
- the order of rows with equal ages, and of plain ``list`` without a filter;
- whether the bad DURATION value itself is echoed in the error message;
- ``since`` for an item that has history but no ``kb:at`` that parses.
"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner, Result
from rich.console import Console

from yurtle_kanban import cli
from yurtle_kanban import config as config_mod
from yurtle_kanban import service as service_mod
from yurtle_kanban.cli import main
from yurtle_kanban.config import KanbanConfig
from yurtle_kanban.service import KanbanService

pytestmark = pytest.mark.usefixtures("claim_env")

A = "agent-A"
B = "agent-B"

CONFIG = """\
version: "2.0"
boards:
  - name: development
    preset: nautical
    path: "work/"
  - name: research
    preset: hdd
    path: "research/"
default_board: development
"""

NOW = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
HOUR = timedelta(hours=1)
DAY = timedelta(days=1)

# commit dates (author); every commit's COMMITTER date is COMMITTER_DATE, so a
# reader of %cI instead of %aI gets the wrong answer
DATE_BASE = "2026-05-26T08:00:00+00:00"
DATE_3 = "2026-06-11T14:00:00+02:00"  # EXP-3 review -> done: 12:00Z, 4d before NOW
DATE_4 = "2026-06-12T09:30:00-04:00"  # EXP-4 added: 13:30Z, 70.5h before NOW
DATE_11 = "2026-06-12T20:00:00+00:00"  # EXP-11 added: 64h before NOW
DATE_LATE = "2026-06-15T11:00:00+00:00"  # EXP-4 retitled: NOT a status change
COMMITTER_DATE = "2026-06-15T11:30:00+00:00"

NAIVE_REVIEW = "2026-06-14T12:00:00"  # EXP-8: a naive stamp (reader's local time)
CREATED_5 = date(2026, 6, 10)

AGING_KEYS = ("since", "since_source", "age_seconds", "stale", "clock_skew", "board")
EXISTING_KEYS = ("id", "title", "item_type", "status", "file_path", "priority", "assignee")
GRAMMAR = r"\d+(m|h|d|w)"


# --- harness ---------------------------------------------------------------------


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def history_block(entries: list[tuple[str, str]], by: str = "someone") -> str:
    """The status-history block exactly as `_history_text` writes it."""
    nodes = [
        f"    kb:status kb:{status} ;\n"
        f'    kb:at "{stamp}"^^xsd:dateTime ;\n'
        f'    kb:by "{by}" ;'
        for status, stamp in entries
    ]
    joined = "\n  ],\n  [\n".join(nodes)
    return (
        "```yurtle\n@prefix kb: <https://yurtle.dev/kanban/> .\n"
        "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .\n\n"
        f"<> kb:statusChange [\n{joined}\n  ] .\n```\n"
    )


def item_md(
    item_id: str,
    status: str,
    *,
    title: str | None = None,
    assignee: str | None = None,
    created: date | None = None,
    history: list[tuple[str, str]] | None = None,
    pasted: list[tuple[str, str]] | None = None,
) -> str:
    hyp = item_id.startswith("H")
    lines = [
        f"id: {item_id}",
        f'title: "{title or f"Item {item_id}"}"',
        f"type: {'hypothesis' if hyp else 'expedition'}",
        f"status: {status}",
        "priority: medium",
    ]
    if assignee:
        lines.append(f"assignee: {assignee}")
    if created:
        lines.append(f"created: {created.isoformat()}")
    text = f"---\n{chr(10).join(lines)}\n---\n\n# Item {item_id}\n\nA description long enough.\n"
    if history:
        text += "\n" + history_block(history, by=assignee or "someone")
    if pasted:  # a comment that pastes another item's history (Acceptance 5)
        text += (
            "\n## Comments\n\n### agent-C (2026-06-15 11:00)\n\n"
            "Pasted from another item:\n\n" + history_block(pasted, by="agent-C")
        )
    return text


def _git(root: Path, *args: str, author_date: str | None = None) -> str:
    env = None
    if author_date:
        import os

        env = {
            **os.environ,
            "GIT_AUTHOR_DATE": author_date,
            "GIT_COMMITTER_DATE": COMMITTER_DATE,
        }
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=True, env=env,
    ).stdout


def _path(root: Path, item_id: str) -> Path:
    if item_id.startswith("H"):
        return root / "research" / "hypotheses" / f"{item_id}-item.md"
    return root / "work" / "expeditions" / f"{item_id}-item.md"


def _write(root: Path, item_id: str, text: str) -> None:
    p = _path(root, item_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _commit(root: Path, message: str, author_date: str) -> None:
    _git(root, "add", "-A")
    _git(root, "commit", "-m", message, author_date=author_date)


def _init(root: Path) -> None:
    (root / ".kanban").mkdir(parents=True)
    (root / ".kanban" / "config.yaml").write_text(CONFIG)
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "t@t.com")
    _git(root, "config", "user.name", "tester")
    _git(root, "config", "commit.gpgsign", "false")
    _git(root, "config", "core.hooksPath", "/dev/null")


# Items committed in the base commit (DATE_BASE); history decides their `since`.
BASE: dict[str, str] = {
    # history, in progress 25h (stale)
    "EXP-1": item_md(
        "EXP-1", "in_progress", assignee=A,
        history=[("ready", iso(NOW - 30 * HOUR)), ("in_progress", iso(NOW - 25 * HOUR))],
    ),
    # history, in progress 23h (not stale)
    "EXP-2": item_md(
        "EXP-2", "in_progress", assignee=B,
        history=[("in_progress", iso(NOW - 23 * HOUR))],
    ),
    # history ends in review; a later commit sets frontmatter done (-> git, DATE_3)
    "EXP-3": item_md(
        "EXP-3", "review", created=date(2026, 5, 1),
        history=[("review", iso(NOW - 5 * DAY))],
    ),
    # an aware stamp then a naive one (Acceptance 3)
    "EXP-8": item_md(
        "EXP-8", "review",
        history=[("ready", "2026-06-13T08:00:00+00:00"), ("review", NAIVE_REVIEW)],
    ),
    # a stamp in the future (Acceptance 4)
    "EXP-9": item_md(
        "EXP-9", "in_progress", assignee=A,
        history=[("in_progress", iso(NOW + 2 * HOUR))],
    ),
    # real history 30h, then a comment pasting a later canonical block (Acceptance 5)
    "EXP-10": item_md(
        "EXP-10", "in_progress", assignee=B,
        history=[("in_progress", iso(NOW - 30 * HOUR))],
        pasted=[("in_progress", iso(NOW - 1 * HOUR))],
    ),
    # finished, old
    "EXP-12": item_md("EXP-12", "done", history=[("done", iso(NOW - 10 * DAY))]),
    # open, old
    "EXP-13": item_md("EXP-13", "ready", history=[("ready", iso(NOW - 10 * DAY))]),
    # open, young
    "EXP-14": item_md("EXP-14", "backlog", history=[("backlog", iso(NOW - 1 * DAY))]),
    # hdd: abandoned (canonical blocked, finished), old
    "H1.1": item_md("H1.1", "abandoned", history=[("blocked", iso(NOW - 10 * DAY))]),
    # hdd: active (canonical in_progress) 50h, stale
    "H1.2": item_md(
        "H1.2", "active", assignee=A, history=[("in_progress", iso(NOW - 50 * HOUR))],
    ),
    # hdd: active 10h, not stale
    "H1.3": item_md("H1.3", "active", history=[("in_progress", iso(NOW - 10 * HOUR))]),
}

ALL_IDS = {*BASE, "EXP-4", "EXP-5", "EXP-6", "EXP-11"}


@pytest.fixture
def board(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The aging fixture (see BASE and the commits below); cwd is its root."""
    root = tmp_path / "repo"
    _init(root)
    for item_id, text in BASE.items():
        _write(root, item_id, text)
    _commit(root, "base", DATE_BASE)
    # EXP-3: status line review -> done (its history still ends in review)
    _write(root, "EXP-3", BASE["EXP-3"].replace("status: review", "status: done"))
    _commit(root, "EXP-3 done", DATE_3)
    # EXP-4: no history, created by a commit; retitled later (no status change)
    _write(root, "EXP-4", item_md("EXP-4", "ready", created=date(2026, 5, 1)))
    _commit(root, "add EXP-4", DATE_4)
    _write(root, "EXP-4", item_md("EXP-4", "ready", title="Renamed", created=date(2026, 5, 1)))
    _commit(root, "retitle EXP-4", DATE_LATE)
    # EXP-11: no history of its own; a comment pastes a canonical block
    _write(root, "EXP-11", item_md("EXP-11", "ready", pasted=[("ready", iso(NOW - HOUR))]))
    _commit(root, "add EXP-11", DATE_11)
    # uncommitted: EXP-5 has `created:`, EXP-6 has nothing
    _write(root, "EXP-5", item_md("EXP-5", "ready", created=CREATED_5))
    _write(root, "EXP-6", item_md("EXP-6", "in_progress", assignee=A))

    monkeypatch.chdir(root)
    config_mod._theme_cache.clear()
    svc = KanbanService(KanbanConfig.load(root / ".kanban" / "config.yaml"), root)
    ids = {i.id for i in svc.get_items()}
    assert ids == ALL_IDS, f"fixture scan mismatch: {sorted(ids ^ ALL_IDS)}"
    config_mod._theme_cache.clear()
    return root


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> datetime:
    """The injected, timezone-aware now (reading a)."""
    monkeypatch.setattr(service_mod, "_now", lambda: NOW, raising=False)
    return NOW


def invoke(args: list[str]) -> Result:
    return CliRunner().invoke(main, args)


def text_of(result: Result) -> str:
    return (result.stdout or "") + (result.stderr or "")


def rows(args: list[str]) -> list[dict[str, Any]]:
    result = invoke(["list", *args, "--json"])
    assert result.exit_code == 0, (
        f"list {' '.join(args)} --json: exit {result.exit_code}: "
        f"{result.exception!r}\n{text_of(result)}"
    )
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise AssertionError(f"stdout is not JSON ({e}):\n{result.stdout}") from e
    assert isinstance(data, list), f"not a list of rows (reading c): {data!r}"
    return data


class Row(dict):  # type: ignore[type-arg]
    """A JSON row whose missing key fails as an assertion, not a KeyError."""

    def __missing__(self, key: str) -> Any:
        raise AssertionError(f"{self.get('id')}: row lacks {key!r}: {dict(self)!r}")


def by_id(data: list[dict[str, Any]]) -> dict[str, Row]:
    return {r["id"]: Row(r) for r in data}


def ids(data: list[dict[str, Any]]) -> list[str]:
    return [r["id"] for r in data]


def since_of(row: dict[str, Any]) -> datetime:
    assert "since" in row, f"row lacks 'since': {row!r}"
    raw = row["since"]
    assert isinstance(raw, str), f"{row.get('id')}: since is not ISO text: {raw!r}"
    when = datetime.fromisoformat(raw)
    assert when.tzinfo is not None, f"{row.get('id')}: since has no UTC offset: {raw!r}"
    return when


def local_midnight(d: date) -> datetime:
    return datetime.combine(d, time()).astimezone()  # naive -> the reader's zone


def age(when: datetime) -> int:
    return int((NOW - when).total_seconds())


# --- the seam --------------------------------------------------------------------


def test_clock_seam_exists() -> None:
    """Reading a: `yurtle_kanban.service._now()` is the one clock, timezone-aware."""
    now = getattr(service_mod, "_now", None)
    assert callable(now), "yurtle_kanban.service._now (the injectable clock) is missing"
    value = now()
    assert isinstance(value, datetime) and value.tzinfo is not None, value


# --- Expected 5: every row carries the aging keys --------------------------------


def test_json_rows_carry_aging_keys(board: Path, clock: datetime) -> None:
    """Every `list --json` row has since/since_source/age_seconds/stale/clock_skew/
    board, keeps the existing keys, and `status` stays canonical."""
    data = by_id(rows([]))
    assert set(data) == ALL_IDS
    for item_id, row in data.items():
        missing = [k for k in (*AGING_KEYS, *EXISTING_KEYS) if k not in row]
        assert not missing, f"{item_id}: missing {missing}: {row!r}"
        assert row["since_source"] in ("history", "git", "created", "unknown"), row
        assert isinstance(row["stale"], bool), row
        assert isinstance(row["clock_skew"], bool), row
        assert row["age_seconds"] is None or isinstance(row["age_seconds"], int), row
    assert data["H1.2"]["status"] == "in_progress"  # canonical, not `active`
    assert data["H1.1"]["status"] == "blocked"  # canonical, not `abandoned`
    for item_id in ("EXP-13", "EXP-8", "EXP-14", "EXP-12", "H1.1"):
        assert data[item_id]["stale"] is False, f"{item_id} is not in progress"


# --- Acceptance 1: the source ladder -----------------------------------------------


def test_a1_history_source(board: Path, clock: datetime) -> None:
    row = by_id(rows([]))["EXP-1"]
    assert row["since_source"] == "history", row
    assert since_of(row) == NOW - 25 * HOUR
    assert row["age_seconds"] == 25 * 3600


def test_a1_history_disagreeing_with_frontmatter_falls_to_git(
    board: Path, clock: datetime
) -> None:
    """History ends in review, frontmatter says done: the git source, i.e. the
    AUTHOR date of the commit that changed the status line."""
    row = by_id(rows([]))["EXP-3"]
    assert row["since_source"] == "git", row
    expected = datetime.fromisoformat(DATE_3)
    assert since_of(row) == expected
    assert row["age_seconds"] == age(expected) == 4 * 86400


def test_a1_no_history_committed_is_git(board: Path, clock: datetime) -> None:
    """No history; created by a commit and later retitled. The retitle does not
    touch `status:`, so `since` is the creating commit's author date, not
    `created:` (git outranks it)."""
    row = by_id(rows([]))["EXP-4"]
    assert row["since_source"] == "git", row
    expected = datetime.fromisoformat(DATE_4)
    assert since_of(row) == expected
    assert row["age_seconds"] == age(expected)


def test_a1_uncommitted_with_created_is_created(board: Path, clock: datetime) -> None:
    row = by_id(rows([]))["EXP-5"]
    assert row["since_source"] == "created", row
    expected = local_midnight(CREATED_5)
    assert since_of(row) == expected
    assert row["age_seconds"] == age(expected)


def test_a1_neither_is_unknown_listed_last(board: Path, clock: datetime) -> None:
    row = by_id(rows([]))["EXP-6"]
    assert row["since_source"] == "unknown", row
    assert row["since"] is None, row
    assert row["age_seconds"] is None, row
    listed = ids(rows(["--older-than", "1m"]))
    assert "EXP-6" in listed, f"unknown-age item dropped: {listed}"
    assert listed[-1] == "EXP-6", f"unknown-age item not last: {listed}"


# --- Acceptance 2: one git log per invocation -------------------------------------


def test_a2_one_git_log_per_invocation(
    board: Path, clock: datetime, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []
    real = subprocess.Popen

    class Spy(real):  # type: ignore[misc, valid-type]
        def __init__(self, args: Any, *a: Any, **kw: Any) -> None:
            argv = [args] if isinstance(args, (str, bytes)) else list(args)
            calls.append([str(x) for x in argv])
            super().__init__(args, *a, **kw)

    monkeypatch.setattr(subprocess, "Popen", Spy)
    data = by_id(rows([]))
    monkeypatch.setattr(subprocess, "Popen", real)
    # three committed items need the git source...
    for item_id in ("EXP-3", "EXP-4", "EXP-11"):
        assert data[item_id]["since_source"] == "git", data[item_id]
    # ...and one `git log` serves them all
    logs = [c for c in calls if c and Path(c[0]).name.startswith("git") and "log" in c]
    assert len(logs) == 1, f"expected one git log, got {len(logs)}: {logs}"


# --- Acceptance 3: naive and aware stamps together ---------------------------------


def test_a3_naive_and_aware_in_one_history(board: Path, clock: datetime) -> None:
    row = by_id(rows([]))["EXP-8"]
    assert row["since_source"] == "history", row
    expected = datetime.fromisoformat(NAIVE_REVIEW).astimezone()  # reader's local time
    assert since_of(row) == expected
    assert row["age_seconds"] == age(expected)


@pytest.mark.parametrize("args", [["metrics", "EXP-8", "--json"], ["metrics", "--json"]])
def test_a3_metrics_on_mixed_stamps(board: Path, clock: datetime, args: list[str]) -> None:
    """`metrics` (one item and board-wide) on the same fixture: no TypeError."""
    result = invoke(args)
    assert result.exception is None or isinstance(result.exception, SystemExit), (
        f"{' '.join(args)} raised {result.exception!r}"
    )
    assert result.exit_code == 0, text_of(result)
    json.loads(result.stdout)


# --- Acceptance 4: a stamp in the future --------------------------------------------


def test_a4_future_stamp_clamped(board: Path, clock: datetime) -> None:
    data = by_id(rows([]))
    assert data["EXP-9"]["age_seconds"] == 0, data["EXP-9"]
    assert data["EXP-9"]["clock_skew"] is True, data["EXP-9"]
    assert data["EXP-1"]["clock_skew"] is False, data["EXP-1"]


# --- Acceptance 5: a comment can't forge history ------------------------------------


def test_a5_comment_block_does_not_change_since(board: Path, clock: datetime) -> None:
    data = by_id(rows([]))
    real = data["EXP-10"]  # real history first, pasted block in a comment
    assert real["since_source"] == "history", real
    assert since_of(real) == NOW - 30 * HOUR
    only = data["EXP-11"]  # the pasted block is the only yurtle block
    assert only["since_source"] == "git", only
    assert since_of(only) == datetime.fromisoformat(DATE_11)


# --- Acceptance 6: --stale with the default 24h ------------------------------------


def test_a6_stale_default(board: Path, clock: datetime) -> None:
    data = rows(["--stale"])
    # in progress >= 24h, oldest first, then unknown age last
    assert ids(data) == ["H1.2", "EXP-10", "EXP-1", "EXP-6"], data
    rows_by = by_id(data)
    for item_id in ("H1.2", "EXP-10", "EXP-1"):
        assert rows_by[item_id]["stale"] is True, rows_by[item_id]
    # EXP-2 (23h), EXP-9 (future), H1.3 (10h) are in progress but not stale
    assert by_id(rows([]))["EXP-2"]["stale"] is False


def test_stale_after_moves_the_threshold(board: Path, clock: datetime) -> None:
    """Expected 4: `--stale-after 26h` drops EXP-1 (25h)."""
    assert ids(rows(["--stale", "--stale-after", "26h"])) == ["H1.2", "EXP-10", "EXP-6"]


# --- Acceptance 7: --older-than excludes finished ----------------------------------


def test_a7_older_than_excludes_finished(board: Path, clock: datetime) -> None:
    listed = ids(rows(["--older-than", "2d"]))
    # EXP-12 (done) and H1.1 (hdd abandoned) are 10 days old but finished
    assert "EXP-12" not in listed and "H1.1" not in listed, listed
    assert listed == ["EXP-13", "EXP-5", "EXP-4", "EXP-11", "H1.2", "EXP-6"], listed


# --- Acceptance 8: a bad DURATION ---------------------------------------------------


@pytest.mark.parametrize(
    "args",
    [
        ["--stale", "--stale-after", "5x"],
        ["--older-than", "5x"],
        ["--older-than", "10"],
    ],
)
def test_a8_bad_duration_exits_2_naming_grammar(
    board: Path, clock: datetime, args: list[str]
) -> None:
    result = invoke(["list", *args])
    text = text_of(result)
    assert GRAMMAR in text, f"the grammar {GRAMMAR} is not in the message:\n{text}"
    assert result.exit_code == 2, f"exit {result.exit_code}:\n{text}"


# --- Acceptance 9: native/canonical --status; board on every row --------------------


def test_a9_status_native_and_canonical_agree(board: Path, clock: datetime) -> None:
    native = ids(rows(["--board", "research", "--status", "active"]))
    canonical = ids(rows(["--board", "research", "--status", "in_progress"]))
    assert sorted(native) == sorted(canonical) == ["H1.2", "H1.3"], (native, canonical)


def test_a9_rows_carry_board(board: Path, clock: datetime) -> None:
    data = by_id(rows([]))  # no --board: every board
    assert set(data) == ALL_IDS
    for item_id, row in data.items():
        want = "research" if item_id.startswith("H") else "development"
        assert row.get("board") == want, f"{item_id}: {row.get('board')!r}"


# --- Acceptance 10: move writes an offset -------------------------------------------


def test_a10_move_writes_utc_offset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "repo"
    _init(root)
    _write(root, "EXP-1", item_md("EXP-1", "ready"))
    _commit(root, "seed", DATE_BASE)
    monkeypatch.chdir(root)
    config_mod._theme_cache.clear()
    result = invoke(["move", "EXP-1", "in_progress", "--agent", A])
    assert result.exit_code == 0, f"{result.exception!r}\n{text_of(result)}"
    stamps = re.findall(r'kb:at "([^"]+)"', _path(root, "EXP-1").read_text())
    assert stamps, "move wrote no history stamp"
    assert re.search(r"(Z|[+-]\d{2}:\d{2})$", stamps[-1]), f"no UTC offset: {stamps[-1]!r}"
    assert datetime.fromisoformat(stamps[-1]).tzinfo is not None


# --- Acceptance 11: --stale prints the recovery command ----------------------------


def test_a11_stale_human_output_has_takeover_command(
    board: Path, clock: datetime, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "console", Console(width=400))  # no wrapping
    result = invoke(["list", "--stale"])
    text = text_of(result)
    assert result.exit_code == 0, f"{result.exception!r}\n{text}"
    for item_id in ("H1.2", "EXP-10", "EXP-1", "EXP-6"):
        cmd = f"yurtle-kanban claim {item_id} --take-over --agent"
        assert cmd in text, f"no recovery command for {item_id}:\n{text}"
    assert "claim EXP-2 " not in text, f"EXP-2 (23h) is not stale:\n{text}"
    # the human table gains Age and Since columns (Expected 4)
    assert "Age" in text and "Since" in text, text
