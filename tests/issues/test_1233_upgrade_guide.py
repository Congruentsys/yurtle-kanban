"""Issue #1233 (A) — UPGRADING.md, the 2.x → 3.x upgrade guide.

Decided shape (issue #1233, tracking #1241): `UPGRADING.md` at the repo root, linked
from the README and from CHANGELOG.md's `## [3.0.0]` section, with a **2.x → 3.x**
section that covers:
- every 3.0.0 `### Removed` entry and every `**Breaking` entry (proved by #1232's
  `scripts/check_upgrade_guide.py 3.0.0`);
- B's (#1230) alias table: each 2.x form → its 3.x form, derived here from the
  `deprecated(...)` calls in the CLI, so a future alias fails this test until it is
  documented; the aliases warn and are removed in 4.0; `next --agent` changed meaning
  (2.x: an `--assignee` filter; 3.x: who is asking);
- actor resolution: `--agent` → `$YURTLE_AGENT` → git `user.name`, else an error; the
  assignee is never defaulted;
- native status names on nautical (hdd/spec) boards (`underway`, `harbor`, ...): read
  `status` from `list --json` / `show --json`;
- refusals on stderr, and the `--json` refusal shape `{"success": false, "error": ...}`;
- run `yurtle-kanban upgrade-check` (#1231) FIRST, and what it does not check;
- the removed resolutions `obsolete` and `merged`, with their replacements.
"""
from __future__ import annotations

import ast
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GUIDE = ROOT / "UPGRADING.md"
README = ROOT / "README.md"
CHANGELOG = ROOT / "CHANGELOG.md"
SCRIPT = ROOT / "scripts" / "check_upgrade_guide.py"
CLI = ROOT / "src" / "yurtle_kanban" / "cli.py"


def _guide() -> str:
    assert GUIDE.exists(), "UPGRADING.md does not exist at the repo root"
    return GUIDE.read_text(encoding="utf-8")


_HEADING = re.compile(r"^(#{1,6})\s+(.*)$", re.M)


def _section() -> str:
    """The guide's 2.x → 3.x section: its heading to the next heading of the same
    or a higher level."""
    text = _guide()
    for m in _HEADING.finditer(text):
        if "2.x" in m.group(2) and "3.x" in m.group(2):
            level = len(m.group(1))
            end = len(text)
            for n in _HEADING.finditer(text, m.end()):
                if len(n.group(1)) <= level:
                    end = n.start()
                    break
            return text[m.start():end]
    pytest.fail("UPGRADING.md has no heading naming both 2.x and 3.x (`## 2.x → 3.x`)")


def _token(t: str) -> re.Pattern[str]:
    """`t` as a whole option/word: `-a` doesn't match inside `--assign`, `--assign`
    doesn't match `--assignee`."""
    return re.compile(rf"(?<![\w-]){re.escape(t)}(?![\w-])")


def _module():
    spec = importlib.util.spec_from_file_location("check_upgrade_guide", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- the aliases, derived from the code ------------------------------------------

def _const(node: ast.AST) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _dict_keys(node: ast.AST, func: ast.AST) -> list[str]:
    """The string keys of `node`: a dict literal, or a name the enclosing function
    assigns one to."""
    if isinstance(node, ast.Dict):
        return [k for k in (_const(key) for key in node.keys if key is not None) if k]
    if isinstance(node, ast.Name):
        for sub in ast.walk(func):
            if (
                isinstance(sub, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == node.id for t in sub.targets)
                and isinstance(sub.value, ast.Dict)
            ):
                return _dict_keys(sub.value, func)
    raise AssertionError(f"can't read the old forms of a deprecated() call: {ast.dump(node)}")


def derived_aliases() -> list[tuple[str, str, str]]:
    """(command, old spelling, new form) for every `deprecated(command, new, value,
    old)` call in the CLI (#1230)."""
    tree = ast.parse(CLI.read_text(encoding="utf-8"))
    found: list[tuple[str, str, str]] = []
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for call in ast.walk(func):
            if not (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id == "deprecated"
            ):
                continue
            command, new = _const(call.args[0]), _const(call.args[1])
            assert command and new, f"deprecated() in {func.name} without literal names"
            for old in _dict_keys(call.args[3], func):
                found.append((command, old, new))
    return sorted(set(found))


def _new_flag(new: str) -> str:
    """The option a new form names: `ID --body TEXT` -> `--body`."""
    m = re.search(r"--[\w-]+", new)
    return m.group(0) if m else new


def test_aliases_derived_from_code_cover_b_table() -> None:
    """The derivation itself works: B's whole table (#1230) comes out of the code."""
    got = {(c, o, _new_flag(n)) for c, o, n in derived_aliases()}
    expected = {
        ("move", "-a", "--assign"),
        ("create", "--assignee", "--assign"), ("create", "-a", "--assign"),
        ("create", "--description", "--body"), ("create", "-d", "--body"),
        ("comment", "ID TEXT", "--body"),
        ("comment", "--author", "--agent"), ("comment", "-a", "--agent"),
        ("next", "--assignee", "--agent"), ("next", "-a", "--agent"),
        ("list", "-a", "--assignee"),
    }
    assert expected <= got, f"missing from the code's aliases: {expected - got}"


# --- 1. the file and its section ---------------------------------------------------

def test_guide_exists_with_2x_to_3x_section() -> None:
    assert _section().strip()


# --- 2. every Removed / Breaking entry --------------------------------------------

def test_check_upgrade_guide_api_passes_for_3_0_0() -> None:
    changelog = CHANGELOG.read_text(encoding="utf-8")
    guide = GUIDE.read_text(encoding="utf-8") if GUIDE.exists() else None
    assert _module().check(changelog, "3.0.0", guide) == []


def test_check_upgrade_guide_cli_passes_for_3_0_0() -> None:
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "3.0.0"],
        cwd=ROOT, capture_output=True, text=True, timeout=60,
    )
    assert r.returncode == 0, r.stderr


# --- 3. every alias the code defines ----------------------------------------------

@pytest.mark.parametrize(
    "command,old,new", derived_aliases(), ids=lambda v: str(v).replace(" ", "_")
)
def test_every_alias_in_guide(command: str, old: str, new: str) -> None:
    """Some line of the 2.x → 3.x section (a table row, a bullet) names the command,
    the old spelling and the new option."""
    flag = _new_flag(new)
    lines = [
        line for line in _section().splitlines()
        if _token(command).search(line) and _token(old).search(line) and _token(flag).search(line)
    ]
    assert lines, f"no line of the 2.x → 3.x section has `{command}`, `{old}` and `{flag}`"


def test_body_file_named() -> None:
    """`--body` / `--body-file`: both 3.x spellings of the removed text options."""
    assert _token("--body-file").search(_section())


# --- 4. warn until 4.0; next --agent changed meaning ------------------------------

def test_aliases_warn_and_go_in_4_0() -> None:
    s = _section()
    assert re.search(r"\b4\.0\b", s), "the guide doesn't say the aliases go in 4.0"
    assert re.search(r"deprecat|warn", s, re.I)


def test_next_agent_meaning_changed() -> None:
    s = _section()
    paras = [p for p in re.split(r"\n\s*\n", s) if "next --agent" in p or
             ("next" in p and "--agent" in p and "--assignee" in p)]
    assert any(
        "--assignee" in p and re.search(r"ask|actor|who", p, re.I) for p in paras
    ), "the guide doesn't say next --agent changed from an --assignee filter to the asker"


# --- 5. actor resolution ------------------------------------------------------------

def test_actor_resolution() -> None:
    s = _section()
    for needle in ("--agent", "YURTLE_AGENT", "user.name"):
        assert needle in s, f"actor resolution: {needle} missing"
    assert re.search(r"\berror\b|refus", s, re.I)
    assert re.search(r"assignee[^.\n]*never[^.\n]*default|never[^.\n]*default[^.\n]*assignee",
                     s, re.I), "the guide doesn't say the assignee is never defaulted"


# --- 6. native status names ---------------------------------------------------------

def test_native_status_names() -> None:
    s = _section()
    assert "underway" in s and "harbor" in s
    assert "nautical" in s.lower()
    assert "list --json" in s and "show --json" in s
    assert re.search(r"\bstatus\b", s)


# --- 7. refusals and JSON -----------------------------------------------------------

def test_refusals_on_stderr_and_json_shape() -> None:
    s = _section()
    assert "stderr" in s
    assert '{"success": false' in s
    assert '"error"' in s


# --- 8. upgrade-check first, and what it does not check ---------------------------

def test_upgrade_check_first() -> None:
    s = _section()
    first = s.find("yurtle-kanban upgrade-check")
    assert first != -1, "the guide doesn't tell you to run `yurtle-kanban upgrade-check`"
    # the flag table starts at the first line naming an alias's command, old and new
    offset, rows = 0, []
    for line in s.splitlines(keepends=True):
        if any(
            _token(c).search(line) and _token(o).search(line) and _token(_new_flag(n)).search(line)
            for c, o, n in derived_aliases()
        ):
            rows.append(offset)
        offset += len(line)
    assert rows, "the guide has no flag table"
    table = rows[0]
    assert first < table, "upgrade-check is first mentioned after the flag table"
    assert re.search(r"\bfirst\b", s, re.I)


def test_upgrade_check_not_checked_listed() -> None:
    s = _section()
    assert re.search(r"(does not|doesn't|not) check", s, re.I)
    assert "Python API" in s
    assert "stderr" in s


# --- 9. removed resolutions ---------------------------------------------------------

def test_removed_resolutions() -> None:
    s = _section()
    for word in ("obsolete", "merged", "wont_do", "superseded", "duplicate"):
        assert _token(word).search(s), f"resolutions: {word} missing"


# --- 10. links ----------------------------------------------------------------------

def test_readme_links_guide() -> None:
    assert re.search(r"\]\(\.?/?UPGRADING\.md(#[^)]*)?\)", README.read_text(encoding="utf-8")), \
        "README.md has no link to UPGRADING.md"


def test_changelog_3_0_0_mentions_guide() -> None:
    text = CHANGELOG.read_text(encoding="utf-8")
    m = re.search(r"^## \[3\.0\.0\].*?(?=^## \[)", text, re.M | re.S)
    assert m, "CHANGELOG.md has no ## [3.0.0] section"
    assert "UPGRADING.md" in m.group(0), "CHANGELOG's 3.0.0 section doesn't mention UPGRADING.md"


# --- 11. the guide and upgrade-check agree on the resolution replacements --------------------


def test_guide_and_upgrade_check_agree_on_resolutions() -> None:
    """Air's request on #1232: `upgrade_check.REMOVED_RESOLUTIONS` (what the scanner suggests,
    #1242) and the guide's resolutions table name the same replacements, so they can't drift."""
    from yurtle_kanban.upgrade_check import REMOVED_RESOLUTIONS

    rows = {
        m.group(1): line
        for line in _section().splitlines()
        if (m := re.match(r"\|\s*`resolution: (\w+)`\s*\|", line))
    }
    assert set(rows) == set(REMOVED_RESOLUTIONS), (rows.keys(), REMOVED_RESOLUTIONS.keys())
    for value, suggestion in REMOVED_RESOLUTIONS.items():
        for replacement in re.findall(r"`([^`]+)`", suggestion):
            assert replacement in rows[value], (
                f"upgrade-check suggests `{replacement}` for {value}; the guide's row doesn't"
            )
