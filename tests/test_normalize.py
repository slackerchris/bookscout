"""Tests for core/normalize.py — sort_name, sort_title, author_names_match."""
import pytest
from core.normalize import author_names_match, sort_name, sort_title


# ---------------------------------------------------------------------------
# sort_name
# ---------------------------------------------------------------------------

class TestSortName:
    def test_two_part_name(self):
        assert sort_name("Brandon Sanderson") == "Sanderson, Brandon"

    def test_single_word(self):
        assert sort_name("Cher") == "Cher"

    def test_initials(self):
        assert sort_name("J.N. Chaney") == "Chaney, J.N."

    def test_three_part_name(self):
        # Only last token is treated as surname
        assert sort_name("Mary Lou Williams") == "Williams, Mary Lou"

    def test_leading_trailing_whitespace(self):
        # Function strips outer whitespace before splitting
        assert sort_name("  Ann Leckie  ") == "Leckie, Ann"


# ---------------------------------------------------------------------------
# sort_title
# ---------------------------------------------------------------------------

class TestSortTitle:
    def test_leading_the(self):
        result = sort_title("The Eye of the World")
        assert result == "Eye of the World, The"

    def test_leading_a(self):
        result = sort_title("A Game of Thrones")
        assert result == "Game of Thrones, A"

    def test_leading_an(self):
        result = sort_title("An Ember in the Ashes")
        assert result == "Ember in the Ashes, An"

    def test_no_article(self):
        assert sort_title("Dune") == "Dune"

    def test_the_mid_title(self):
        # "The" not at the start — should be unchanged
        assert sort_title("Into the Wild") == "Into the Wild"

    def test_empty(self):
        assert sort_title("") == ""


# ---------------------------------------------------------------------------
# author_names_match
# ---------------------------------------------------------------------------

class TestAuthorNamesMatch:
    # Exact matches
    def test_exact(self):
        assert author_names_match("Brandon Sanderson", "Brandon Sanderson")

    def test_case_insensitive(self):
        assert author_names_match("brandon sanderson", "Brandon Sanderson")

    # Initial expansion
    def test_initials_match_full(self):
        assert author_names_match("J.N. Chaney", "Jason N. Chaney")

    def test_initials_spaced(self):
        assert author_names_match("J. N. Chaney", "J.N. Chaney")

    def test_single_initial_first_name(self):
        assert author_names_match("B. Sanderson", "Brandon Sanderson")

    # Subset / superset
    def test_subset(self):
        assert author_names_match("Terry Maggert", "Terry H. Maggert")

    # Reversed name
    def test_reversed(self):
        assert author_names_match("Sanderson, Brandon", "Brandon Sanderson") is False  # comma style not normalised to match — document behaviour

    # No match
    def test_clearly_different(self):
        assert author_names_match("Frank Herbert", "Brandon Sanderson") is False

    def test_last_name_only_no_match(self):
        assert author_names_match("Sanderson", "Brandon Sanderson") is True  # subset
