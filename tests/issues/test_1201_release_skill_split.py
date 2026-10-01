"""Issue #1201 — the release skill is split (Captain's ruling, 2026-10-01).

"this process for the FOSS repos and then without the public facing aspects for
nusy-product-team and other internal repos".

- `skills/release-foss/SKILL.md` (shipped): a public repo's release. Bump, CHANGELOG,
  reviewed release PR, tag after merge, a public GitHub release with notes cut to fit
  GitHub's 125,000-character cap, the registry publish confirmed live, and credit to
  external contributors.
- `skills/release/SKILL.md` (shipped, name kept so internal consumers keep working): the
  same flow WITHOUT the public parts. No public notes, no registry, no contributor thanks.
- `.claude/skills/release-yurtle-kanban/SKILL.md` (repo-local, never shipped): this
  repo's own procedure, following release-foss with changelog.d, both version files,
  scripts/release_notes.py and publish.yml.

The shipped skills are installed by `init` into a stranger's repo, so neither may name a
path that only exists here: that is how step 8 told consumers to run a script they don't
have (#1201's report).
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

REPO = Path(__file__).resolve().parents[2]
INTERNAL = REPO / "skills" / "release" / "SKILL.md"
FOSS = REPO / "skills" / "release-foss" / "SKILL.md"
LOCAL = REPO / ".claude" / "skills" / "release-yurtle-kanban" / "SKILL.md"

# Paths and names that exist only in THIS repo. A shipped skill naming one tells a
# consumer to run something they don't have.
REPO_ONLY = (
    "scripts/release_notes.py",
    "release_notes.py",
    "scripts/assemble_changelog.py",
    "assemble_changelog",
    "check_release_version",
    "src/yurtle_kanban",
    "yurtle_kanban",
    "yurtle-kanban",
    "publish.yml",
    "changelog.d",
)

# Public-facing release steps: the internal skill has none of them.
PUBLIC_MARKERS = (
    "pypi",
    "pip index",
    "npm publish",
    "cargo publish",
    "twine",
    "registry",
    "125,000",
    "125000",
    "--notes-file",
    "thank",
    "@login",
)


def text(path: Path) -> str:
    assert path.is_file(), f"{path.relative_to(REPO)} does not exist"
    return path.read_text(encoding="utf-8")


def frontmatter(path: Path) -> dict[str, str]:
    body = text(path)
    assert body.startswith("---\n"), f"{path.relative_to(REPO)} has no frontmatter"
    block = body[4 : body.index("\n---", 4)]
    out: dict[str, str] = {}
    for line in block.splitlines():
        key, sep, value = line.partition(":")
        if sep and not line.startswith(" "):
            out[key.strip()] = value.strip()
    return out


def step_headings(path: Path) -> list[str]:
    return [ln for ln in text(path).splitlines() if ln.startswith("### ")]


# --- all three -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path, name",
    [(INTERNAL, "release"), (FOSS, "release-foss"), (LOCAL, "release-yurtle-kanban")],
    ids=["internal", "foss", "local"],
)
def test_captain_only_and_named_for_its_dir(path: Path, name: str) -> None:
    fm = frontmatter(path)
    assert fm.get("name") == name, fm
    assert fm.get("disable-model-invocation") == "true", (
        f"{path.relative_to(REPO)} must stay Captain-only (disable-model-invocation: true)"
    )
    assert fm.get("description"), "a skill needs a description"


@pytest.mark.parametrize("path", [INTERNAL, FOSS, LOCAL], ids=["internal", "foss", "local"])
def test_no_stale_trailer(path: Path) -> None:
    assert "Claude Opus 4.5" not in text(path), "stale model name in the commit trailer"


@pytest.mark.parametrize("path", [INTERNAL, FOSS, LOCAL], ids=["internal", "foss", "local"])
def test_tags_after_the_reviewed_merge(path: Path) -> None:
    body = text(path)
    low = body.lower()
    assert "other than the author" in low, "the release PR is reviewed by a non-author"
    assert body.index("gh pr merge") < body.index("git tag -a"), "tag AFTER the merge"


# --- shipped skills are generic ----------------------------------------------------------


@pytest.mark.parametrize("path", [INTERNAL, FOSS], ids=["internal", "foss"])
def test_shipped_skill_names_no_repo_only_path(path: Path) -> None:
    body = text(path)
    hits = [frag for frag in REPO_ONLY if frag in body]
    assert not hits, f"shipped {path.relative_to(REPO)} names this repo's {hits}"


@pytest.mark.parametrize("path", [INTERNAL, FOSS], ids=["internal", "foss"])
def test_shipped_skill_keeps_the_search_not_glob_warning(path: Path) -> None:
    body = text(path)
    assert "v2.1.0" in body, "the v2.1.0 __version__ warning was dropped"
    assert 'grep -rn "__version__"' in body, "version detection must search, not glob"


def test_internal_skill_has_no_public_steps() -> None:
    low = text(INTERNAL).lower()
    hits = [m for m in PUBLIC_MARKERS if m in low]
    assert not hits, f"the internal release skill has public-facing steps: {hits}"
    for heading in step_headings(INTERNAL):
        h = heading.lower()
        for word in ("notes", "publish", "registry", "credit", "contributor"):
            assert word not in h, f"internal step heading is public-facing: {heading}"


def test_internal_skill_points_public_repos_at_release_foss() -> None:
    assert "release-foss" in text(INTERNAL)


def test_internal_skill_github_release_only_if_deploy_needs_it() -> None:
    low = text(INTERNAL).lower()
    assert "only if your deploy is triggered by a release" in low


def test_foss_skill_covers_the_public_steps() -> None:
    body = text(FOSS)
    low = body.lower()
    assert "gh release create" in body and "--verify-tag" in body
    assert "125,000" in body, "GitHub's release-body cap is not stated"
    assert "registry" in low, "the package registry publish is not covered"
    assert "publish workflow" in low, "confirming the publish workflow fired is not covered"
    assert "thank" in low and "@login" in body, "external contributors are not credited"
    assert "changelog" in low


def test_foss_skill_says_how_to_cut_notes_to_fit() -> None:
    low = text(FOSS).lower()
    assert "condensed" in low, "no fallback when the section is over the cap"
    assert "link" in low


# --- this repo's own procedure is repo-local ---------------------------------------------


def test_local_skill_names_this_repos_specifics() -> None:
    body = text(LOCAL)
    for needle in (
        "python scripts/assemble_changelog.py X.Y.Z",
        "git add -A changelog.d",
        "python scripts/release_notes.py X.Y.Z",
        "pyproject.toml",
        "src/yurtle_kanban/__init__.py",
        "publish.yml",
        "check_release_version.py",
        "gh release create vX.Y.Z --verify-tag",
        "release-foss",
        "PyPI",
    ):
        assert needle in body, f"repo-local release skill does not mention {needle!r}"


def test_local_skill_stages_both_version_files_and_fragment_deletions() -> None:
    adds = [ln for ln in text(LOCAL).splitlines() if ln.strip().startswith("git add")]
    joined = "\n".join(adds)
    assert "pyproject.toml" in joined and "src/yurtle_kanban/__init__.py" in joined, adds
    assert "CHANGELOG.md" in joined, adds
    assert re.search(r"git add -A changelog\.d", joined), adds


def test_local_skill_says_nonzero_notes_exit_stops() -> None:
    assert "non-zero" in text(LOCAL).lower()


def test_local_skill_says_release_is_captains_call() -> None:
    body = text(LOCAL)
    assert "Captain" in body and "#1195" in body


def test_local_skill_is_not_shipped() -> None:
    assert not (REPO / "skills" / "release-yurtle-kanban").exists()


# --- shipping ----------------------------------------------------------------------------


def test_init_installs_both_shipped_release_skills(tmp_path: Path, monkeypatch) -> None:
    # The installer reads _get_skills_dir(), which prefers an INSTALLED share/ copy;
    # point it at this checkout's skills/ so the test sees what this tree ships.
    import yurtle_kanban.cli as cli

    monkeypatch.setattr(cli, "_get_skills_dir", lambda: REPO / "skills")
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, capture_output=True, check=True)
    result = CliRunner().invoke(cli.main, ["init", "--theme", "software"])
    assert result.exit_code == 0, result.output

    installed = tmp_path / ".claude" / "skills"
    assert (installed / "release" / "SKILL.md").read_bytes() == INTERNAL.read_bytes()
    assert (installed / "release-foss" / "SKILL.md").read_bytes() == FOSS.read_bytes()
    assert not (installed / "release-yurtle-kanban").exists()


def test_release_foss_is_package_data() -> None:
    py = (REPO / "pyproject.toml").read_text()
    # skills/**/*.md is included and skills/ maps to share/yurtle-kanban/skills
    assert '"skills/**/*.md"' in py and '"skills" = "share/yurtle-kanban/skills"' in py
    assert FOSS.is_file()


def test_readme_lists_release_foss() -> None:
    readme = (REPO / "README.md").read_text()
    assert "/release-foss" in readme
    assert "skills/release-foss" in readme, "the manual-install line omits release-foss"


def test_contributing_points_maintainers_at_the_local_skill() -> None:
    contributing = (REPO / "CONTRIBUTING.md").read_text()
    assert "/release-yurtle-kanban" in contributing


def test_no_shipped_skill_asks_gh_pr_list_for_author_association() -> None:
    """r1 B1: `gh pr list` has no `authorAssociation` JSON field ("Unknown JSON field"),
    so a credit step built on it finds no contributors; `gh search prs` has it."""
    for skill in sorted((REPO / "skills").glob("*/SKILL.md")):
        for block in re.findall(r"```[a-z]*\n(.*?)```", skill.read_text(), re.S):
            joined = block.replace("\\\n", " ")  # a `\` line continuation is one command
            for command in re.findall(r"gh pr list[^\n]*", joined):
                assert "authorAssociation" not in command, (skill, command)


def test_foss_credit_step_keeps_only_prs_in_this_release() -> None:
    """r1 follow-up: the date search is day-granular, so the credit step filters to the
    PR numbers in `<tag>..HEAD`, and names who counts as external."""
    text = (REPO / "skills/release-foss/SKILL.md").read_text()
    assert "gh search prs" in text and "--limit 1000" in text
    assert 'git log "$TAG"..HEAD' in text and "COLLABORATOR" in text
