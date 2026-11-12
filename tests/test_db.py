"""Database layer on an in-memory SQLite database."""

import pytest

from modules import db


@pytest.fixture
def conn():
    return db.connect(":memory:")


def test_add_ingredient_with_price_starts_history(conn):
    iid = db.add_ingredient(conn, "Maida", "g", 0.045, "Flours", "apf", on_date="2026-11-01")
    ing = db.get_ingredient(conn, iid)
    assert ing["current_price_per_base_unit"] == 0.045 and ing["last_updated"] == "2026-11-01"
    assert len(db.price_history(conn, iid)) == 1


def test_update_price_logs_history_newest_first(conn):
    iid = db.add_ingredient(conn, "Butter", "g", 0.5, on_date="2026-10-01")
    db.update_price(conn, iid, 0.56, quantity_bought=500, source="bill", on_date="2026-11-01")
    hist = db.price_history(conn, iid)
    assert [h["source"] for h in hist] == ["bill", "manual"]
    assert db.get_ingredient(conn, iid)["current_price_per_base_unit"] == 0.56


def test_duplicate_name_rejected_case_insensitively(conn):
    db.add_ingredient(conn, "Sugar", "g")
    with pytest.raises(ValueError, match="already"):
        db.add_ingredient(conn, "sugar", "g")


def test_bad_base_unit_rejected_by_schema(conn):
    with pytest.raises(Exception):
        db.add_ingredient(conn, "Thing", "kg")


def test_cannot_delete_ingredient_used_in_recipe(conn):
    iid = db.add_ingredient(conn, "Eggs", "pcs", 7)
    db.save_recipe(conn, "Omelette Cake", 1, "kg", [{"ingredient_id": iid, "quantity": 2, "unit": "pcs"}])
    with pytest.raises(ValueError, match="Omelette Cake"):
        db.delete_ingredient(conn, iid)


def test_save_and_edit_recipe_replaces_items(conn):
    a = db.add_ingredient(conn, "Maida", "g", 0.045)
    b = db.add_ingredient(conn, "Sugar", "g", 0.048)
    rid = db.save_recipe(conn, "Sponge", 500, "g", [{"ingredient_id": a, "quantity": 125, "unit": "g"}])
    db.save_recipe(conn, "Sponge", 500, "g", [{"ingredient_id": b, "quantity": 1, "unit": "cup"}], recipe_id=rid)
    items = db.get_recipe(conn, rid)["items"]
    assert [(i["ingredient_name"], i["unit"]) for i in items] == [("Sugar", "cup")]


def test_recipe_needs_items(conn):
    with pytest.raises(ValueError):
        db.save_recipe(conn, "Empty", 1, "kg", [])


def test_settings_defaults_and_types(conn):
    s = db.get_settings(conn)
    assert s["tax_percent"] == 0.0 and s["invoice_counter"] == 1
    db.save_settings(conn, {"tax_percent": 18, "rounding_step": 10})
    s = db.get_settings(conn)
    assert s["tax_percent"] == 18.0 and isinstance(s["rounding_step"], int)


def test_unknown_setting_rejected(conn):
    with pytest.raises(ValueError):
        db.save_settings(conn, {"colour": "red"})


def test_invoice_numbers_increment(conn):
    first = db.save_invoice(conn, "Asha", "", [], {}, 100, 5, 105)
    second = db.save_invoice(conn, "Ravi", "", [], {}, 100, 5, 105)
    assert (first["number"], second["number"]) == ("INV-0001", "INV-0002")


def test_fixed_costs_validation(conn):
    with pytest.raises(ValueError):
        db.replace_fixed_costs(conn, [{"name": "Gas", "type": "per_hour", "value": 5}])
    db.replace_fixed_costs(conn, [{"name": "Gas", "type": "per_kg", "value": 20, "active": False}])
    assert db.list_fixed_costs(conn, active_only=True) == []
