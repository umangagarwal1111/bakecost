"""Fuzzy bill-name -> ingredient matching with confidence scores."""

import pytest

from modules.matching import (
    AUTO_SELECT_THRESHOLD, Candidate, best_match, normalize, rank_matches, should_preselect, similarity,
)
from seed import INGREDIENTS

LIBRARY = [Candidate(i, name, [a.strip() for a in aliases.split(",")])
           for i, (name, _cat, _unit, _price, aliases) in enumerate(INGREDIENTS, start=1)]


def test_normalize_drops_numbers_units_and_filler():
    assert normalize("AMUL Butter 500g PKT.") == ["amul", "butter"]
    assert normalize("Maida 1 KG loose") == ["maida"]


@pytest.mark.parametrize("bill_text, expected", [
    ("MAIDA 1KG", "Maida"),
    ("Amul Butter 500g", "Butter"),
    ("AMUL BUTTR 500G", "Butter"),               # OCR typo
    ("Sugar M30 5kg", "Sugar"),
    ("Icing Sugar 500g", "Icing Sugar"),         # must not pick plain Sugar
    ("Morde Dark Compound 500g", "Dark Compound Chocolate"),
    ("Nescafe Classic 100g", "Instant Coffee"),
    ("Kaju W320 250g", "Cashews"),
    ("Farm Eggs", "Eggs"),
    ("Cocoa Pwd 200g", "Cocoa Powder"),
])
def test_best_match_finds_right_ingredient(bill_text, expected):
    match = best_match(bill_text, LIBRARY)
    assert match is not None and match.ingredient_name == expected
    assert should_preselect(match), f"{bill_text} scored only {match.score}"


def test_unrelated_item_gets_no_confident_match():
    match = best_match("Surf Excel Detergent 1kg", LIBRARY)
    assert match is None or match.score < AUTO_SELECT_THRESHOLD


def test_exact_name_scores_one():
    assert similarity("Maida", "maida") == 1.0


def test_rank_is_sorted_and_limited():
    ranked = rank_matches("Sugar 1kg", LIBRARY, top_n=3)
    assert len(ranked) <= 3
    assert [m.score for m in ranked] == sorted((m.score for m in ranked), reverse=True)
    assert ranked[0].ingredient_name == "Sugar"


def test_empty_text_scores_zero():
    assert similarity("", "Sugar") == 0.0
    assert similarity("500 g", "Sugar") == 0.0
