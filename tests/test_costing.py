"""Scaling, cost estimate, fixed costs, margin vs markup, tax and rounding."""

from datetime import date

import pytest

from modules.costing import (
    FixedCost, IngredientLine, apply_fixed_costs, apply_profit, estimate_cost,
    estimate_weight_kg, price_order, round_to_nearest, scale_factor,
)
from modules.units import UnitConversionError

TODAY = date(2026, 11, 10)


# --- scaling ---------------------------------------------------------------

def test_scale_500g_recipe_to_1kg_order():
    assert scale_factor(500, "g", 1, "kg") == pytest.approx(2.0)


def test_scale_piece_recipe_by_dozen():
    assert scale_factor(12, "pcs", 2, "dozen") == pytest.approx(2.0)


def test_scale_down():
    assert scale_factor(1, "kg", 250, "g") == pytest.approx(0.25)


def test_scale_weight_recipe_with_piece_order_fails():
    with pytest.raises(UnitConversionError):
        scale_factor(1, "kg", 6, "pcs")


@pytest.mark.parametrize("yq, oq", [(0, 1), (1, 0), (-1, 1)])
def test_scale_rejects_non_positive(yq, oq):
    with pytest.raises(ValueError):
        scale_factor(yq, "kg", oq, "kg")


# --- cost estimate ---------------------------------------------------------

def _lines():
    return [
        IngredientLine("Maida", 250, "g", "g", 0.045, "2026-11-01"),        # 11.25
        IngredientLine("Milk", 1, "cup", "ml", 0.068, "2026-11-01"),        # 240 ml -> 16.32
        IngredientLine("Eggs", 2, "pcs", "pcs", 7.0, "2026-11-01"),         # 14.00
    ]


def test_estimate_cost_sums_lines_and_adds_wastage():
    est = estimate_cost(_lines(), factor=1, wastage_percent=5, today=TODAY)
    assert est.ingredients_subtotal == pytest.approx(11.25 + 16.32 + 14.0)
    assert est.wastage_amount == pytest.approx(est.ingredients_subtotal * 0.05)
    assert est.total == pytest.approx(est.ingredients_subtotal * 1.05)
    assert est.warnings == []


def test_estimate_cost_scales_every_line():
    one = estimate_cost(_lines(), 1, 0, TODAY).total
    two = estimate_cost(_lines(), 2, 0, TODAY).total
    assert two == pytest.approx(2 * one)


def test_missing_price_is_flagged_not_fatal():
    lines = _lines() + [IngredientLine("Saffron", 1, "g", "g", None, None)]
    est = estimate_cost(lines, 1, 0, TODAY)
    saffron = est.lines[-1]
    assert saffron.cost == 0
    assert "no price in the library" in saffron.flags


def test_stale_price_is_flagged():
    lines = [IngredientLine("Walnuts", 60, "g", "g", 1.4, "2026-08-01")]  # 101 days old
    est = estimate_cost(lines, 1, 0, TODAY)
    assert est.lines[0].cost == pytest.approx(84)
    assert any("days old" in f for f in est.lines[0].flags)


def test_price_exactly_60_days_old_is_not_stale():
    lines = [IngredientLine("Sugar", 100, "g", "g", 0.048, "2026-09-11")]  # 60 days
    assert estimate_cost(lines, 1, 0, TODAY).lines[0].flags == []


def test_unconvertible_unit_is_flagged():
    lines = [IngredientLine("Mystery Powder", 1, "cup", "g", 0.1, "2026-11-01")]
    est = estimate_cost(lines, 1, 0, TODAY)
    assert est.lines[0].cost == 0
    assert est.lines[0].flags[0].startswith("unit problem")


def test_weight_for_piece_recipe_sums_mass_ingredients():
    est = estimate_cost(_lines(), 1, 0, TODAY)
    # 250 g maida + 240 ml milk; eggs (pcs) are ignored
    assert estimate_weight_kg(est.lines, "pcs", 12, "pcs") == pytest.approx(0.49)
    assert estimate_weight_kg(est.lines, "g", 1, "kg") == pytest.approx(1.0)


# --- pricing ---------------------------------------------------------------

def test_markup_vs_margin_differ_for_same_percentage():
    assert apply_profit(100, 50, "markup") == pytest.approx(150)
    assert apply_profit(100, 50, "margin") == pytest.approx(200)


def test_margin_definition_holds():
    price = apply_profit(320, 40, "margin")
    assert (price - 320) / price == pytest.approx(0.40)


def test_margin_of_100_percent_rejected():
    with pytest.raises(ValueError):
        apply_profit(100, 100, "margin")


def test_unknown_profit_mode_rejected():
    with pytest.raises(ValueError):
        apply_profit(100, 10, "double")


@pytest.mark.parametrize("amount, step, expected", [
    (1192.3, 5, 1190), (1192.5, 5, 1195), (1197.5, 5, 1200), (1194.99, 10, 1190),
    (1195, 10, 1200), (102.5, 5, 105), (99.456, 0, 99.46),
])
def test_round_half_up(amount, step, expected):
    assert round_to_nearest(amount, step) == pytest.approx(expected)


def test_fixed_cost_types():
    rules = [FixedCost("Box", "per_order", 40), FixedCost("Gas", "per_kg", 25),
             FixedCost("Overheads", "percent_of_cost", 10)]
    out = dict(apply_fixed_costs(300, rules, weight_kg=2))
    assert out == {"Box": 40, "Gas": 50, "Overheads": 30}


def test_price_order_full_pipeline():
    rules = [FixedCost("Box", "per_order", 40), FixedCost("Gas", "per_kg", 20)]
    p = price_order(ingredient_cost=300, fixed_costs=rules, weight_kg=1, profit_percent=50,
                    profit_mode="margin", tax_percent=5, rounding_step=10)
    assert p.production_cost == pytest.approx(360)
    assert p.selling_price == pytest.approx(720)          # 360 / (1 - 0.5)
    assert p.profit_amount == pytest.approx(360)
    assert p.tax_amount == pytest.approx(36)
    assert p.total_before_rounding == pytest.approx(756)
    assert p.final_total == 760
    assert p.round_off == pytest.approx(4)
    # every number on the invoice adds up
    assert p.selling_price + p.tax_amount + p.round_off == pytest.approx(p.final_total)


def test_negative_tax_rejected():
    with pytest.raises(ValueError):
        price_order(100, [], 1, 10, "markup", -5, 5)
