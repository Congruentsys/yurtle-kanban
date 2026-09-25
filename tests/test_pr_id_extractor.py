"""Tests for pr_id_extractor — Issue #28."""

from __future__ import annotations

from yurtle_kanban.pr_id_extractor import (
    extract_all,
    extract_id_from_branch,
    extract_ids_from_text,
)

# ---------------------------------------------------------------------------
# extract_ids_from_text
# ---------------------------------------------------------------------------


class TestExtractIdsFromText:
    def test_single_closes(self):
        assert extract_ids_from_text("Closes EXP-1023") == ["EXP-1023"]

    def test_single_fixes(self):
        assert extract_ids_from_text("Fixes CHORE-055") == ["CHORE-055"]

    def test_single_resolves(self):
        assert extract_ids_from_text("Resolves FEAT-042") == ["FEAT-042"]

    def test_multiple_keywords(self):
        text = "Closes EXP-1023 and Fixes CHORE-055"
        assert extract_ids_from_text(text) == ["EXP-1023", "CHORE-055"]

    def test_case_insensitive_keyword(self):
        assert extract_ids_from_text("closes exp-100") == ["EXP-100"]
        assert extract_ids_from_text("CLOSES EXP-100") == ["EXP-100"]
        assert extract_ids_from_text("Closes EXP-100") == ["EXP-100"]

    def test_case_insensitive_prefix(self):
        assert extract_ids_from_text("Closes exp-100") == ["EXP-100"]
        assert extract_ids_from_text("Closes Exp-100") == ["EXP-100"]

    def test_deduplication(self):
        text = "Closes EXP-100. Also fixes EXP-100"
        assert extract_ids_from_text(text) == ["EXP-100"]

    def test_no_keywords(self):
        assert extract_ids_from_text("This is a regular PR description") == []

    def test_empty_text(self):
        assert extract_ids_from_text("") == []

    def test_keyword_without_id(self):
        assert extract_ids_from_text("Closes the door") == []

    def test_all_prefixes(self):
        prefixes = [
            "EXP", "CHORE", "FEAT", "VOY", "BUG", "EPIC", "ISSUE",
            "TASK", "IDEA", "DIR", "HAZ", "SIG", "LIT", "PAPER",
            "H", "EXPR", "M",
        ]
        for prefix in prefixes:
            text = f"Closes {prefix}-42"
            result = extract_ids_from_text(text)
            assert result == [f"{prefix}-42"], f"Failed for prefix {prefix}"

    def test_multiline_body(self):
        text = """## Summary

        Implements the new feature.

        Closes EXP-1023
        Fixes CHORE-055

        ## Test plan
        - Unit tests added
        """
        assert extract_ids_from_text(text) == ["EXP-1023", "CHORE-055"]

    def test_inline_with_other_text(self):
        text = "This PR (Closes EXP-100) does stuff"
        assert extract_ids_from_text(text) == ["EXP-100"]

    def test_full_type_name_not_matched(self):
        """Full type names should NOT match — only actual kanban ID prefixes."""
        assert extract_ids_from_text("Closes EXPEDITION-100") == []
        assert extract_ids_from_text("Closes EXPERIMENT-100") == []
        assert extract_ids_from_text("Closes FEATURE-100") == []


# ---------------------------------------------------------------------------
# extract_id_from_branch
# ---------------------------------------------------------------------------


class TestExtractIdFromBranch:
    def test_exp_branch(self):
        assert extract_id_from_branch("exp-1023-hdd-knowledge-blocks") == "EXP-1023"

    def test_chore_branch(self):
        assert extract_id_from_branch("chore-055-cleanup") == "CHORE-055"

    def test_feat_branch(self):
        assert extract_id_from_branch("feat-042-new-feature") == "FEAT-042"

    def test_bug_branch(self):
        assert extract_id_from_branch("bug-007-null-pointer") == "BUG-007"

    def test_voy_branch(self):
        assert extract_id_from_branch("voy-003-migration") == "VOY-003"

    def test_h_branch(self):
        assert extract_id_from_branch("h-130-hypothesis") == "H-130"

    def test_m_branch(self):
        assert extract_id_from_branch("m-007-accuracy") == "M-007"

    def test_main_branch(self):
        assert extract_id_from_branch("main") is None

    def test_develop_branch(self):
        assert extract_id_from_branch("develop") is None

    def test_no_number(self):
        assert extract_id_from_branch("feature-branch") is None

    def test_full_type_name_not_matched(self):
        """Full type names (expedition, experiment, etc.) should NOT match.

        Only actual kanban ID prefixes (exp, expr, etc.) are valid.
        Regression test for review finding on PR #48.
        """
        assert extract_id_from_branch("expedition-100-title") is None
        assert extract_id_from_branch("experiment-100-title") is None
        assert extract_id_from_branch("literature-100-title") is None
        assert extract_id_from_branch("hypothesis-100-title") is None
        assert extract_id_from_branch("feature-100-title") is None

    def test_case_insensitive(self):
        assert extract_id_from_branch("EXP-100-title") == "EXP-100"

    def test_paper_branch(self):
        assert extract_id_from_branch("paper-119-title") == "PAPER-119"

    def test_expr_branch(self):
        assert extract_id_from_branch("expr-130-experiment") == "EXPR-130"


# ---------------------------------------------------------------------------
# extract_all
# ---------------------------------------------------------------------------


class TestExtractAll:
    def test_body_only(self):
        assert extract_all("Closes EXP-1023", "main") == ["EXP-1023"]

    def test_branch_only(self):
        assert extract_all("", "exp-1023-title") == ["EXP-1023"]

    def test_body_takes_priority(self):
        result = extract_all("Closes EXP-1023", "exp-1023-title")
        assert result == ["EXP-1023"]
        assert len(result) == 1  # no duplicate

    def test_body_and_branch_different_ids(self):
        result = extract_all("Closes EXP-1023", "chore-055-cleanup")
        assert result == ["EXP-1023", "CHORE-055"]

    def test_neither(self):
        assert extract_all("Regular PR", "main") == []

    def test_multiple_body_plus_branch(self):
        result = extract_all(
            "Closes EXP-100 and Fixes CHORE-200",
            "exp-100-title",
        )
        # EXP-100 from body, CHORE-200 from body, branch EXP-100 deduped
        assert result == ["EXP-100", "CHORE-200"]
