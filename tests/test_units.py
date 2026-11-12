"""Unit conversion: same-dimension ratios, density bridges, clear errors."""

import pytest

from modules.units import UnitConversionError, convert, density_for, normalize_unit, to_base


@pytest.mark.parametrize("value, src, dst, expected", [
    (1, "kg", "g", 1000),
    (250, "g", "kg", 0.25),
    (1.5, "L", "ml", 1500),
    (500, "ml", "l", 0.5),
    (2, "dozen", "pcs", 24),
    (3, "tbsp", "tsp", 9),
    (1, "cup", "ml", 240),
])
def test_same_dimension(value, src, dst, expected):
    assert convert(value, src, dst) == pytest.approx(expected)


@pytest.mark.parametrize("text, canonical", [
    ("Kgs", "kg"), ("KG.", "kg"), ("gms", "g"), ("Grams", "g"), ("ltr", "l"),
    ("Tablespoons", "tbsp"), ("tsp", "tsp"), ("cups", "cup"), ("nos", "pcs"), ("doz", "dozen"),
])
def test_normalize_unit_spellings(text, canonical):
    assert normalize_unit(text) == canonical


def test_cup_of_flour_to_grams_uses_density():
    # 1 cup = 240 ml, maida density 0.52 g/ml -> 124.8 g (the classic "1 cup flour ~ 125 g")
    assert convert(1, "cup", "g", "Maida") == pytest.approx(124.8)


def test_most_specific_density_wins():
    assert density_for("Icing Sugar") == 0.50          # not plain "sugar" (0.83)
    assert density_for("Milk Powder") == 0.50          # not "milk" (1.03)
    assert density_for("Sugar") == 0.83


def test_grams_to_cups_round_trip():
    grams = convert(0.5, "cup", "g", "Butter")
    assert convert(grams, "g", "cup", "Butter") == pytest.approx(0.5)


def test_egg_pieces_to_grams():
    assert convert(2, "pcs", "g", "Eggs") == pytest.approx(100)


def test_to_base_for_liquid_in_cups():
    assert to_base(1, "cup", "ml", "Milk") == pytest.approx(240)


def test_unknown_unit_raises_clear_error():
    with pytest.raises(UnitConversionError, match="Unknown unit 'handful'"):
        convert(1, "handful", "g")


def test_volume_to_mass_without_density_raises():
    with pytest.raises(UnitConversionError, match="density"):
        convert(1, "cup", "g", "Mystery Powder")


def test_count_to_mass_without_piece_weight_raises():
    with pytest.raises(UnitConversionError, match="one piece weighs"):
        convert(3, "pcs", "g", "Cake Board")


def test_negative_quantity_rejected():
    with pytest.raises(UnitConversionError):
        convert(-1, "kg", "g")


def test_to_base_rejects_non_base_target():
    with pytest.raises(UnitConversionError):
        to_base(1, "kg", "kg")
