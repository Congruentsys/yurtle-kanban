"""Issue #181 — `LineEndings.apply` must stay fast on genuinely mixed-ending files.

A file whose line endings are not all the same takes the per-line mapping path.
On main that path runs difflib over the whole file, which is quadratic on long
runs of identical lines (~130 s projected for 50k lines). A frontmatter edit near
the top should only need the changed middle diffed.

Timing tests run each 50k-line case in a subprocess with a hard timeout, so the
red run stays short; a TimeoutExpired is a failure.

The correctness tests are controls: they pin what `apply()` already does (and
must keep doing) — untouched lines keep their exact ending, changed or added
lines take the majority ending, the final line's ending (or lack of one) is kept,
and a lone-`\\r` (classic Mac) file keeps `\\r` on edited lines.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from yurtle_kanban.service import KanbanService, LineEndings

SRC = Path(__file__).resolve().parents[2] / "src"
HERE = Path(__file__).resolve().parent
BOUND = 2.0  # seconds, per read()+apply()
TIMEOUT = 10  # seconds, for the whole subprocess
N = 50_000

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def raw_of(pairs: list[tuple[str, str]]) -> str:
    """Raw file text from (line, ending) pairs; the ending may be ""."""
    return "".join(line + end for line, end in pairs)


def roundtrip(raw: str, edit) -> str:
    """read(raw), apply `edit` to the LF text, and apply() the result."""
    text, eol = LineEndings.read(raw)
    return eol.apply(edit(text))


def replace_line(pairs: list[tuple[str, str]], i: int, new: str) -> list[tuple[str, str]]:
    return pairs[:i] + [(new, pairs[i][1])] + pairs[i + 1 :]


def big_mixed(n: int | None = None) -> list[tuple[str, str]]:
    """A frontmatter-headed file of `n` lines, mostly "same" + LF, a few CRLF.

    CRLF lines sit near the start (right after the frontmatter, and one inside
    it), in the middle, and near the end — including the last line.
    """
    n = N if n is None else n
    head = [("---", "\n"), ("id: X-1", "\r\n"), ("status: ready", "\n"), ("---", "\n")]
    body = [("same", "\n")] * (n - len(head))
    pairs = head + body
    for i in (4, 5, 7, n // 3, n // 2, n - 3, n - 1):
        pairs[i] = (pairs[i][0], "\r\n")
    return pairs


def _timed_case(kind: str) -> None:
    """Subprocess entry: run one 50k-line case, check bytes, print elapsed."""
    pairs = big_mixed()
    raw = raw_of(pairs)
    if kind == "change":
        expected = raw_of(replace_line(pairs, 2, "status: done"))

        def edit(t: str) -> str:
            return t.replace("status: ready", "status: done", 1)

    elif kind == "insert":
        expected = raw_of(pairs[:3] + [("priority: high", "\n")] + pairs[3:])

        def edit(t: str) -> str:
            return t.replace("status: ready\n", "status: ready\npriority: high\n", 1)

    elif kind == "append":
        # The last line ends in CRLF; appended line takes the majority (LF)
        expected = raw + "tail\n"

        def edit(t: str) -> str:
            return t + "tail\n"

    elif kind == "service":
        path = Path(sys.argv[2])
        path.write_bytes(raw.encode("utf-8"))
        expected = raw_of(replace_line(pairs, 2, "status: done"))
        start = time.perf_counter()
        text, eol = KanbanService._read_item_text(path)
        text = text.replace("status: ready", "status: done", 1)
        KanbanService._write_item_text(path, text, eol)
        elapsed = time.perf_counter() - start
        assert path.read_bytes().decode("utf-8") == expected, "wrong bytes"
        print(f"ELAPSED {elapsed:.3f}")
        return
    else:
        raise SystemExit(f"unknown case {kind}")
    start = time.perf_counter()
    out = roundtrip(raw, edit)
    elapsed = time.perf_counter() - start
    assert out == expected, "wrong bytes"
    print(f"ELAPSED {elapsed:.3f}")


def run_timed(kind: str, *extra: str) -> float:
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(SRC), str(HERE)])}
    code = (
        "import sys; from test_181_lineendings_chunked import _timed_case; "
        "_timed_case(sys.argv[1])"
    )
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code, kind, *extra],
            env=env, capture_output=True, text=True, timeout=TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(f"{kind}: 50k-line mixed file took over {TIMEOUT}s (bound {BOUND}s)")
    assert proc.returncode == 0, proc.stderr
    elapsed = float(proc.stdout.split("ELAPSED")[-1])
    return elapsed


# ---------------------------------------------------------------------------
# 1. Timing bound (red on main)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["change", "insert", "append"])
def test_50k_mixed_file_edit_is_fast(kind: str) -> None:
    elapsed = run_timed(kind)
    assert elapsed < BOUND, f"{kind}: {elapsed:.2f}s >= {BOUND}s"


def test_50k_mixed_item_file_service_write_is_fast(tmp_path: Path) -> None:
    elapsed = run_timed("service", str(tmp_path / "X-1.md"))
    assert elapsed < BOUND, f"service write: {elapsed:.2f}s >= {BOUND}s"


# ---------------------------------------------------------------------------
# 2. Correctness controls (pass now and after)
# ---------------------------------------------------------------------------

MIXED = [
    ("---", "\n"),
    ("id: A", "\r\n"),
    ("title: t", "\r\n"),
    ("status: ready", "\n"),
    ("owner: o", "\r\n"),
    ("---", "\n"),
    ("body 1", "\n"),
    ("body 2", "\r\n"),
    ("body 3", "\n"),
    ("body 4", "\n"),
]  # 6 LF vs 4 CRLF: majority LF


def test_changed_line_takes_majority_neighbours_keep_their_ending() -> None:
    out = roundtrip(raw_of(MIXED), lambda t: t.replace("status: ready", "status: done"))
    assert out == raw_of(replace_line(MIXED, 3, "status: done"))


def test_changed_crlf_line_takes_majority_lf() -> None:
    out = roundtrip(raw_of(MIXED), lambda t: t.replace("title: t", "title: u"))
    expected = MIXED[:2] + [("title: u", "\n")] + MIXED[3:]
    assert out == raw_of(expected)


def test_crlf_majority_file_changed_line_takes_crlf() -> None:
    pairs = [("a", "\r\n"), ("b", "\n"), ("c", "\r\n"), ("d", "\r\n")]
    out = roundtrip(raw_of(pairs), lambda t: t.replace("b", "B"))
    assert out == raw_of([("a", "\r\n"), ("B", "\r\n"), ("c", "\r\n"), ("d", "\r\n")])


def test_inserted_line_takes_majority() -> None:
    out = roundtrip(
        raw_of(MIXED), lambda t: t.replace("status: ready\n", "status: ready\nprio: 1\n"),
    )
    assert out == raw_of(MIXED[:4] + [("prio: 1", "\n")] + MIXED[4:])


def test_deleted_middle_line_keeps_other_endings() -> None:
    out = roundtrip(raw_of(MIXED), lambda t: t.replace("owner: o\n", ""))
    assert out == raw_of(MIXED[:4] + MIXED[5:])


def test_line_count_change_before_crlf_lines_keeps_them() -> None:
    def edit(t: str) -> str:
        return t.replace("id: A\n", "id: A\nx: 1\ny: 2\n").replace("body 3\n", "")

    out = roundtrip(raw_of(MIXED), edit)
    expected = MIXED[:2] + [("x: 1", "\n"), ("y: 2", "\n")] + MIXED[2:8] + MIXED[9:]
    assert out == raw_of(expected)


def test_final_line_without_newline_stays_without() -> None:
    pairs = MIXED[:-1] + [("body 4", "")]
    out = roundtrip(raw_of(pairs), lambda t: t.replace("status: ready", "status: done"))
    assert out == raw_of(replace_line(pairs, 3, "status: done"))


def test_final_line_without_newline_edited_stays_without() -> None:
    pairs = MIXED[:-1] + [("body 4", "")]
    out = roundtrip(raw_of(pairs), lambda t: t.replace("body 4", "body four"))
    assert out == raw_of(pairs[:-1] + [("body four", "")])


def test_final_crlf_kept_when_line_appended() -> None:
    pairs = MIXED[:-1] + [("body 4", "\r\n")]
    out = roundtrip(raw_of(pairs), lambda t: t + "tail\n")
    assert out == raw_of(pairs + [("tail", "\n")])


def test_final_crlf_kept_on_edit_near_top() -> None:
    pairs = MIXED[:-1] + [("body 4", "\r\n")]
    out = roundtrip(raw_of(pairs), lambda t: t.replace("status: ready", "status: done"))
    assert out == raw_of(replace_line(pairs, 3, "status: done"))


@pytest.mark.parametrize("touched", [4, 6])
def test_identical_run_one_crlf_edit_adjacent(touched: int) -> None:
    pairs = [("same", "\n")] * 12
    pairs[5] = ("same", "\r\n")  # the only CRLF, between the touched lines

    def edit(t: str) -> str:
        lines = t.split("\n")
        lines[touched] = "diff"
        return "\n".join(lines)

    out = roundtrip(raw_of(pairs), edit)
    assert out == raw_of(replace_line(pairs, touched, "diff"))


def test_identical_run_one_crlf_edit_it() -> None:
    pairs = [("same", "\n")] * 12
    pairs[5] = ("same", "\r\n")

    def edit(t: str) -> str:
        lines = t.split("\n")
        lines[5] = "diff"
        return "\n".join(lines)

    out = roundtrip(raw_of(pairs), edit)
    assert out == raw_of(pairs[:5] + [("diff", "\n")] + pairs[6:])


def test_unedited_mixed_text_roundtrips_exactly() -> None:
    raw = raw_of(big_mixed(2_000))
    assert roundtrip(raw, lambda t: t) == raw


# ---------------------------------------------------------------------------
# 3. Lone `\r` (classic Mac) files keep `\r` on edited and added lines
# ---------------------------------------------------------------------------


def test_lone_cr_file_keeps_cr_on_edited_and_added_lines() -> None:
    raw = "---\rid: A\rstatus: ready\r---\rbody\r"
    out = roundtrip(
        raw, lambda t: t.replace("status: ready", "status: done") + "added\n",
    )
    assert out == "---\rid: A\rstatus: done\r---\rbody\radded\r"


def test_lone_cr_file_without_final_newline() -> None:
    raw = "a\rb\rc"
    out = roundtrip(raw, lambda t: t.replace("\nb\n", "\nB\nnew\n"))
    assert out == "a\rB\rnew\rc"
