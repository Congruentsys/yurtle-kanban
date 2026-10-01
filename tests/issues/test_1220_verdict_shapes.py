# ruff: noqa: F811  (the borrowed `yk` fixture)
"""Issue #1220: verdict-check follow-ups from the approved review of #1219 (#1213).

1. `PASSWORD_CODE`'s dotted and call branches: the head must be a digit-free identifier
   (`self.pw`, `cfg.password`, `getpass()`, `Optional[str]` post; `hunter2.v1`, `s3cret.pass`,
   `admin(123)` are refused).
2. `CODE_VALUE`'s segment caps are the same (15) for every segment, as its comment says: a token
   split into a 16-char head and short tails (`abcd1234efgh5678_ijkl9012mnop`) is refused.
3. New shapes: SendGrid `SG.<x>.<y>`, Stripe `sk_live_` / `sk_test_` / `rk_live_`, and a
   `Bearer <token>`; prose about bearer tokens and a `Bearer <token>` placeholder still post.
4. Prose: `$VAR` / `${…}` references, hash-algorithm names as a password value, and an
   identifier value under a longer lower-case key (`test_password: test_1213_reset_flow`) post;
   a bare `password:` or an upper-case env key still takes placeholders only.
5. A refusal ends with a `RE-RUN NOTE: …` line the driver passes into the re-run brief, and
   external-pr step 3 says so.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tests.test_yk_next_picker import yk  # noqa: F401 (fixture)

HEAD = "c" * 40
GOOD = f"reviewed-at-sha: {HEAD}\nverdict: approve\nclass: routine\n\n"
SKILL = Path(__file__).resolve().parents[2] / ".claude" / "skills" / "external-pr" / "SKILL.md"


def check(yk, extra: str) -> str | None:
    return yk.check_verdict(GOOD + extra + "\n", HEAD)


def refused(yk, leak: str) -> None:
    why = check(yk, leak)
    assert why and "secret" in why, (leak, why)


def posts(yk, prose: str) -> None:
    assert check(yk, prose) is None, (prose, check(yk, prose))


# --------------------------------------------------------------------------- 1. dotted / call


@pytest.mark.parametrize("prose", [
    "password: self.pw",
    "password=cfg.password",
    "password = settings.db.password",
    "password=getpass()",
    "password: Optional[str]",
    "password = os.environ['PW']",
    "password=self.get_password()",
])
def test_1_digit_free_dotted_and_call_heads_post(yk, prose) -> None:
    posts(yk, prose)


@pytest.mark.parametrize("leak", [
    "password: hunter2.v1",
    "password: s3cret.pass",
    "password=admin(123)",
    "password=Summer.2024",
    "password: p4ss[word]",
])
def test_1_digits_in_a_dotted_or_call_value_are_refused(yk, leak) -> None:
    refused(yk, leak)


# --------------------------------------------------------------------------- 2. caps


def test_2_a_16_char_first_segment_is_not_an_identifier(yk) -> None:
    refused(yk, "token: abcd1234efgh5678_ijkl9012mnop")
    posts(yk, "token: abcd1234efgh567_ijkl9012mnop_x")  # 15 + short tails: an identifier


# --------------------------------------------------------------------------- 3. new shapes

SG_KEY = "SG." + "Ab3_dEf6-hIj9kLm" + "." + "Nop2Qr5_St8-Uvw1Xyz4AbCd"


@pytest.mark.parametrize("leak", [
    SG_KEY,
    f"SENDGRID={SG_KEY}",
    "STRIPE_KEY=sk_live_" + "Ab3dEf6hIj9k" * 2,
    "use sk_test_" + "4eC39HqLyjWDarjtT1zdp7dc",
    "rk_live_" + "Zx81Qw92Er03Ty14Ui25",
    "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.abc123",
    "-H 'Authorization: Bearer ab12cd34ef56gh78ij90kl12'",
    "bearer Zx81Qw92Er03Ty14Ui25Op36==",
])
def test_3_sendgrid_stripe_and_bearer_are_refused(yk, leak) -> None:
    refused(yk, leak)


@pytest.mark.parametrize("prose", [
    "a Bearer token is read from the env",
    "Authorization: Bearer <token>",
    "Authorization: Bearer $GITHUB_TOKEN",
    "Authorization: Bearer ${{ secrets.TOKEN }}",
    "Bearer YOUR_ACCESS_TOKEN_GOES_HERE",
    "the SG. prefix is SendGrid's; sk_live_ and rk_live_ are Stripe's",
    "sk_test_<key>",
])
def test_3_bearer_and_stripe_prose_posts(yk, prose) -> None:
    posts(yk, prose)


# --------------------------------------------------------------------------- 4. prose


@pytest.mark.parametrize("prose", [
    "`--password=$PGPASSWORD`",
    "--password=$PGPASSWORD is read from the env",
    "password: ${DB_PASSWORD}",
    "token: ${{ secrets.X }}",
    "password: pbkdf2_sha256",
    "`password: pbkdf2_sha256`",
    "password: argon2id",
    "password: argon2",
    "password = bcrypt",
    "password: scrypt",
    "password: sha256",
    "password: sha512",
    "password: md5",
    "test_password: test_1213_reset_flow",
    "- test_reset_password: test_1213_reset_flow fails",
])
def test_4_references_algorithms_and_test_names_post(yk, prose) -> None:
    posts(yk, prose)


@pytest.mark.parametrize("leak", [
    "password: test_1213_reset_flow",       # a bare key: placeholders only
    "PASSWORD: summer_2024",
    "DB_PASSWORD=admin_pass_2024",           # an upper-case env key: placeholders only
    "password: $ecret123",                   # not an env-var reference
    "password: sha256hunter2",               # not an algorithm name
    "test_password: Hunter2_secret",         # not an identifier
    "db_password: summer_2024",              # only test_… keys hold identifier values
    "password=${DB_PW:-Hunter2secret}",      # r1: a shell default is a real password
    "password: ${PGPASS}x9Kq2",              # r1: a reference with a value glued on
    "password: ${{x}}Hunter2secret",         # r2: a GitHub expression with a value glued on
    "password: Welcome.Home2024",            # r1: a dotted tail with digits
])
def test_4_values_under_bare_or_env_keys_are_still_refused(yk, leak) -> None:
    refused(yk, leak)


def test_4_the_1213_leaks_still_refuse(yk) -> None:
    """Every #1213 leak case keeps refusing (they are re-run in test_1213_check_verdict.py;
    this samples the password ones the #1220 exemptions sit next to)."""
    for leak in ("password=hunter2secret", "password = hunter2", "PASSWORD: hunter2",
                 'password: "s3cr3t!pw"', "db_password='Tr0ub4dor&3'",
                 '"password": "hunter2xyz"', "password: summer_2024",
                 "password: hashed_pw_v2"):
        refused(yk, leak)


# --------------------------------------------------------------------------- 5. re-run note


def run_cli(yk, monkeypatch, tmp_path, flag: str, body: str) -> tuple[object, str, list]:
    posted: list = []

    def fake_gh(*args: str, stdin: str | None = None) -> str:
        if args[:2] == ("pr", "comment"):
            posted.append(stdin)
            return "https://example/1\n"
        return HEAD + "\n"

    monkeypatch.setattr(yk, "gh", fake_gh)
    f = tmp_path / "verdict.txt"
    f.write_text(body)
    monkeypatch.setattr(sys, "argv", ["x", flag, "1300", str(f)])
    code = None
    try:
        yk.main()
    except SystemExit as e:
        code = e.code
    return code, str(f), posted


@pytest.mark.parametrize("flag", ["--post-verdict", "--check-verdict"])
def test_5_a_refusal_ends_with_a_re_run_note(yk, monkeypatch, tmp_path, capsys, flag) -> None:
    code, _, posted = run_cli(yk, monkeypatch, tmp_path, flag,
                              GOOD + "fine\n\npassword=hunter2secret\n")
    out = capsys.readouterr().out
    assert code == 1 and posted == [], (code, posted)
    last = out.strip().splitlines()[-1]
    assert last.startswith("RE-RUN NOTE: "), out
    assert "line 7" in last and "rewrite that line without the value" in last, last
    assert "hunter2" not in last, last  # the note never carries the value into the brief


def test_5_a_non_secret_refusal_has_a_note_too(yk, monkeypatch, tmp_path, capsys) -> None:
    code, _, _ = run_cli(yk, monkeypatch, tmp_path, "--post-verdict", GOOD + "cc @everyone\n")
    last = capsys.readouterr().out.strip().splitlines()[-1]
    assert code == 1 and last.startswith("RE-RUN NOTE: ") and "mention" in last, last


def test_5_skill_step_3_passes_the_note_into_the_re_run(yk) -> None:
    text = SKILL.read_text()
    sec = text[text.index("**3. Review"):text.index("**4. Outcomes")]
    assert "RE-RUN NOTE" in sec and "brief" in sec[sec.index("RE-RUN NOTE"):], sec



def test_r1_integer_index_is_code(yk) -> None:
    """r1: `pw_hash[0]`-style indexing is review prose (it posted before #1220)."""
    assert yk.check_verdict(GOOD + "password: pw_hash[0]\n", HEAD) is None
