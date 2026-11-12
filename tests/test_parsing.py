"""Regex parsing of bill lines and recipe lines."""

import pytest

from modules.parsing import parse_bill, parse_bill_line, parse_quantity, parse_recipe, parse_recipe_line


@pytest.mark.parametrize("line, name, qty, unit, amount", [
    ("Maida 1kg            2     45.00     90.00", "Maida", 2.0, "kg", 90.0),
    ("Amul Butter 500g     1    280.00    280.00", "Amul Butter", 500.0, "g", 280.0),
    ("Eggs                30      7.00    210.00", "Eggs", 30.0, "pcs", 210.0),
    ("Sugar 5 kg                          240.00", "Sugar", 5.0, "kg", 240.0),
    ("Milk 1L x 2                         136.00", "Milk", 2.0, "l", 136.0),
    ("MAIDA 1KG 2 x 45.00 90.00", "MAIDA", 2.0, "kg", 90.0),
    ("Cocoa Powder 200g   Rs. 1,210.00", "Cocoa Powder", 200.0, "g", 1210.0),
    ("Maida 1kg 45.00 90.00", "Maida", 2.0, "kg", 90.0),           # rate + amount => 2 packs
])
def test_parse_bill_line(line, name, qty, unit, amount):
    item = parse_bill_line(line)
    assert item is not None
    assert (item.name, item.quantity, item.unit, item.amount) == (name, qty, unit, amount)


@pytest.mark.parametrize("line", [
    "TOTAL                    934.00", "CGST 2.5%  11.68", "Bill No: 1234  Date: 08/11/2026",
    "Thank you! Visit again", "------------------", "", "Item   Qty   Rate   Amount",
])
def test_non_item_lines_are_skipped(line):
    assert parse_bill_line(line) is None


def test_item_without_quantity_needs_user_input():
    item = parse_bill_line("Vanilla Essence          45.00")
    assert item.name == "Vanilla Essence" and item.quantity is None and item.amount == 45.0


def test_parse_bill_collects_items_and_skipped():
    text = "SHREE BAKERY MART\nMaida 1kg 2 45.00 90.00\nTOTAL 90.00\n"
    res = parse_bill(text)
    assert [i.name for i in res.items] == ["Maida"]
    assert "TOTAL 90.00" in res.skipped


@pytest.mark.parametrize("text, value", [
    ("2", 2.0), ("1/2", 0.5), ("1 1/2", 1.5), ("½", 0.5), ("1½", 1.5), ("0.75", 0.75), ("", None),
])
def test_parse_quantity(text, value):
    assert parse_quantity(text) == (pytest.approx(value) if value is not None else None)


@pytest.mark.parametrize("line, name, qty, unit", [
    ("2 cups maida", "maida", 2.0, "cup"),
    ("1 1/2 tsp baking powder", "baking powder", 1.5, "tsp"),
    ("½ cup cocoa powder", "cocoa powder", 0.5, "cup"),
    ("200 g sugar", "sugar", 200.0, "g"),
    ("200g butter, softened", "butter", 200.0, "g"),
    ("3 eggs", "eggs", 3.0, "pcs"),
    ("- 2 tbsp oil", "oil", 2.0, "tbsp"),
    ("Butter - 100 g", "Butter", 100.0, "g"),
    ("Vanilla essence: 1 tsp", "Vanilla essence", 1.0, "tsp"),
    ("1 cup of milk", "milk", 1.0, "cup"),
])
def test_parse_recipe_line(line, name, qty, unit):
    item = parse_recipe_line(line)
    assert (item.name, item.quantity, item.unit) == (name, pytest.approx(qty), unit)


def test_recipe_line_without_quantity_kept_for_review():
    item = parse_recipe_line("Salt a pinch")
    assert item.quantity is None and item.name == "Salt a pinch"


def test_parse_whole_recipe():
    text = """Eggless Vanilla Cake
    Makes: 500 g
    Ingredients:
    1 1/2 cups maida
    3/4 cup sugar
    1 cup curd
    Method
    1. Mix 2 cups of everything
    """
    r = parse_recipe(text)
    assert r.title == "Eggless Vanilla Cake"
    assert (r.yield_quantity, r.yield_unit) == (500, "g")
    assert [l.name for l in r.lines] == ["maida", "sugar", "curd"]   # method ignored
