# ruff: noqa: F811  (the borrowed `yk` fixture)
"""Issue #1213: the verdict check (`yk_next.check_verdict`), from Mini's round-4 review of #1196.

1. A `password` rule needs a VALUE, not a type annotation: review prose about password handling
   (`def login(password: str)`, `password: Optional[str]`) is postable; `password=hunter2secret`
   is not.
2. Shapes it missed: `sk-ant-…`, `aws_secret_access_key = <value>`, a generic
   `secret|token|api_key = <long value>`, `GITHUB_TOKEN=<value>`, a PEM body line without its
   header (a base64 run of 60+). The 40-hex `reviewed-at-sha:` (and a 64-hex sha256) still pass.
3. Limits: a body over 60,000 chars is refused (GitHub rejects 65,536+); an @-mention outside
   code is refused (it notifies people); `@` inside `inline code` or a fenced block, and an
   email address, are fine.
4. One command: `yk_next.py --post-verdict <P> <file>` checks, and only on a pass runs
   `gh pr comment <P> --body-file <file>`; else exit 1 with the reason. The picker prints it in
   place of the check+post pair, and external-pr step 3 uses it. `--check-verdict` stays.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tests.issues.test_1195_external_pr import (  # noqa: F401
    SPARE,
    ext_pr,
    run_picker,
)
from tests.test_yk_next_picker import yk  # noqa: F401 (fixture)

HEAD = "c" * 40
GOOD = f"reviewed-at-sha: {HEAD}\nverdict: approve\nclass: routine\n\n"
SKILL = Path(__file__).resolve().parents[2] / ".claude" / "skills" / "external-pr" / "SKILL.md"


def check(yk, extra: str) -> str | None:
    return yk.check_verdict(GOOD + extra + "\n", HEAD)


# --------------------------------------------------------------------------- 1. password


@pytest.mark.parametrize("prose", [
    "`def login(password: str)` hashes before storing",
    "def login(password: str) -> bool:",
    "password: Optional[str] = None",
    "password: str",
    "the field password: SecretStr is right",
    "call `login(user, password=password)` here",
    "password: hashed before storage",
    "a password prompt",
    "password = os.environ['PW']",
    "password=getpass()",
])
def test_1_password_prose_passes(yk, prose) -> None:
    assert check(yk, prose) is None, (prose, check(yk, prose))


@pytest.mark.parametrize("leak", [
    "password=hunter2secret",
    "password = hunter2",
    "PASSWORD: hunter2",
    'password: "s3cr3t!pw"',
    "db_password='Tr0ub4dor&3'",
])
def test_1_password_values_are_refused(yk, leak) -> None:
    why = check(yk, leak)
    assert why and "secret" in why, (leak, why)


# --------------------------------------------------------------------------- 2. missed shapes

AWS_SECRET = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
PEM_LINE = "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC7VJTUt9Us8cKj"


@pytest.mark.parametrize("leak", [
    "sk-ant-api03-" + "Ab3dEf6hIj" * 8,
    f"aws_secret_access_key = {AWS_SECRET}",
    f"AWS_SECRET_ACCESS_KEY: {AWS_SECRET}",
    "secret=9f8e7d6c5b4a3f2e1d0c9b8a",
    "token: 'Zx81Qw92Er03Ty14Ui25Op36'",
    "api_key = AbC123dEf456GhI789jKl012",
    "apikey=AbC123dEf456GhI789jKl012",
    "GITHUB_TOKEN=" + "0123456789abcdef" * 2 + "01234567",
    "export GITHUB_TOKEN=abcdef0123456789abcdef01",
    PEM_LINE,
    f"  {PEM_LINE}",
])
def test_2_missed_shapes_are_refused(yk, leak) -> None:
    why = check(yk, leak)
    assert why and "secret" in why, (leak, why)


@pytest.mark.parametrize("prose", [
    f"also seen at {'d' * 40}",
    f"sha256: {'0123456789abcdef' * 4}",
    "token: str",
    "api_key: Optional[str] = None",
    "secret = os.environ['SECRET_KEY']",
    "GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}",
    "token=self.config.github_token_value",
    "the aws_secret_access_key field is read from the env",
    "see src/yurtle_kanban/very_long_module_name_for_testing_purposes_only.py:12",
    "https://github.com/Congruentsys/yurtle-kanban/pull/1196#issuecomment-1234567890",
    "uses pypa/gh-action-pypi-publish",
    "the sk-ant prefix is Anthropic's",
])
def test_2_ordinary_review_text_passes(yk, prose) -> None:
    assert check(yk, prose) is None, (prose, check(yk, prose))


# --------------------------------------------------------------------------- 3. limits


def test_3_a_body_over_60000_chars_is_refused(yk) -> None:
    ok = GOOD + "x " * ((60_000 - len(GOOD)) // 2)
    assert len(ok) <= 60_000
    assert yk.check_verdict(ok, HEAD) is None
    big = GOOD + "x " * 30_000
    why = yk.check_verdict(big, HEAD)
    assert why and "60,000" in why, why


@pytest.mark.parametrize("mention", [
    "cc @everyone",
    "@hankh95 please look",
    "thanks (@PandaHUN777)",
    "ping @Congruentsys/fleet",
    "`code` then @here",
])
def test_3_mentions_outside_code_are_refused(yk, mention) -> None:
    why = check(yk, mention)
    assert why and "mention" in why, (mention, why)


@pytest.mark.parametrize("text", [
    "the `@pytest.fixture` decorator",
    "``a `@b` c``",
    "```python\n@pytest.fixture\ndef f(): ...\n```",
    "~~~\n@everyone\n~~~",
    "mail a@b.c or hans.s+x@example.com",
    "a lone @ sign",
])
def test_3_at_inside_code_or_an_email_passes(yk, text) -> None:
    assert check(yk, text) is None, (text, check(yk, text))


# --------------------------------------------------------------------------- 4. --post-verdict


def run_post(yk, monkeypatch, tmp_path, body: str, swap: str | None = None
             ) -> tuple[list, object, str]:
    calls: list[tuple[str, ...]] = []
    posted: list[str | None] = []

    def fake_gh(*args: str, stdin: str | None = None) -> str:
        calls.append(args)
        if args[:2] == ("pr", "comment"):
            posted.append(stdin)
        if args[:2] == ("pr", "view"):
            assert args[2] == "1300" and "headRefOid" in args, args
            return HEAD + "\n"
        assert args[:2] == ("pr", "comment"), args
        return "https://github.com/x/y/pull/1300#issuecomment-1\n"

    monkeypatch.setattr(yk, "gh", fake_gh)
    f = tmp_path / "verdict.txt"
    f.write_text(body)
    monkeypatch.setattr(sys, "argv", ["x", "--post-verdict", "1300", str(f)])
    if swap is not None:  # the file changes after it was read and checked
        real_check = yk.check_verdict

        def check_then_swap(text: str, head: str) -> str | None:
            result = real_check(text, head)
            f.write_text(swap)
            return result

        monkeypatch.setattr(yk, "check_verdict", check_then_swap)
    run_post.posted = posted
    code = None
    try:
        yk.main()
    except SystemExit as e:
        code = e.code
    return [c for c in calls if c[:2] == ("pr", "comment")], code, str(f)


def test_4_post_verdict_posts_a_passing_verdict(yk, monkeypatch, tmp_path, capsys) -> None:
    posts, code, path = run_post(yk, monkeypatch, tmp_path, GOOD + "fine\n")
    assert code in (None, 0), code
    assert posts == [("pr", "comment", "1300", "--body-file", "-")], posts
    assert run_post.posted == [GOOD + "fine\n"]


def test_4_post_verdict_posts_the_checked_text_not_a_later_file(yk, monkeypatch, tmp_path,
                                                                capsys) -> None:
    """The text posted is the text checked: a file swapped after the check (here, to
    leak a token) is never what reaches GitHub."""
    posts, code, _ = run_post(yk, monkeypatch, tmp_path, GOOD + "fine\n",
                              swap=GOOD + "token=ghp_" + "A1" * 18 + "\n")
    assert code in (None, 0), code
    assert run_post.posted == [GOOD + "fine\n"], run_post.posted


@pytest.mark.parametrize("body", [
    GOOD.replace(HEAD, "b" * 40),
    GOOD + "password=hunter2secret\n",
    GOOD + "cc @everyone\n",
    GOOD + "x" * 60_001,
])
def test_4_post_verdict_never_posts_a_failing_one(yk, monkeypatch, tmp_path, capsys,
                                                  body) -> None:
    posts, code, _ = run_post(yk, monkeypatch, tmp_path, body)
    assert posts == [], posts
    assert code not in (None, 0), code
    assert "NOT POSTING" in capsys.readouterr().out


def test_4_check_verdict_stays(yk, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(yk, "gh", lambda *a: HEAD + "\n")
    f = tmp_path / "v.txt"
    f.write_text(GOOD + "cc @everyone\n")
    monkeypatch.setattr(sys, "argv", ["x", "--check-verdict", "1300", str(f)])
    with pytest.raises(SystemExit) as e:
        yk.main()
    assert e.value.code == 1


def test_4_review_pick_prints_the_one_command(yk, monkeypatch, capsys) -> None:
    out = run_picker(yk, monkeypatch, capsys, [ext_pr(1300)], SPARE)
    post = yk.POST_VERDICT_CMD.replace("<P>", "1300")
    assert post == "python3 .claude/skills/yk-next/yk_next.py --post-verdict 1300 <verdict-file>"
    assert yk.REVIEW_CMD in out and post in out, out
    assert out.index(yk.REVIEW_CMD) < out.index(post), out
    assert "gh pr comment 1300" not in out, out  # no separate, unchecked post


def test_4_skill_step_3_uses_the_one_command(yk) -> None:
    text = SKILL.read_text()
    sec = text[text.index("**3. Review"):text.index("**4. Outcomes")]
    assert yk.REVIEW_CMD in sec and yk.POST_VERDICT_CMD in sec, sec
    assert sec.index(yk.REVIEW_CMD) < sec.index(yk.POST_VERDICT_CMD)
    assert "gh pr comment <P> --body-file" not in sec, sec
    assert "@-mention" in sec and "60,000" in sec, sec


# --------------------------------------------------------------------------- r1 B1: prose


@pytest.mark.parametrize("prose", [
    "- test_secret_shapes: tests/issues/test_1213_check_verdict.py::test_2 fails",
    "tokenizer: src/yurtle_kanban/tokenizer_v2_module.py",
    "mytoken = tests/issues/test_1213_check_verdict.py",
    "secret_store: .claude/skills/yk-next/yk_next_v2.md",
    "token = tests/test_yk_next_picker.py::test_picker_2_ready",
])
def test_r1_b1_identifiers_and_paths_are_prose(yk, prose) -> None:
    """r1 B1: a name that merely CONTAINS secret/token (`test_secret_shapes`, `tokenizer`),
    or a value that is a path (`a/b.py`, `x.py::test`), is review prose, not a credential."""
    assert check(yk, prose) is None, (prose, check(yk, prose))
