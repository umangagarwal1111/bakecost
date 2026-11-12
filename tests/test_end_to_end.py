"""The 'definition of done' flow without the UI:
load demo data -> 1 kg chocolate truffle cake -> 50 % margin + tax -> PDF invoice."""

from datetime import date

import pytest

import seed
from modules import costing, db, invoice

TODAY = date(2026, 11, 10)


@pytest.fixture
def demo():
    conn = db.connect(":memory:")
    seed.load_demo_data(conn, today=TODAY)
    return conn


def _truffle(conn):
    rid = next(r["id"] for r in db.list_recipes(conn) if r["name"] == "Chocolate Truffle Cake")
    return db.get_recipe(conn, rid)


def test_demo_data_counts(demo):
    c = db.counts(demo)
    assert c["ingredients"] == 25 and c["recipes"] == 4


def test_every_demo_recipe_costs_without_unit_problems(demo):
    for r in db.list_recipes(demo):
        recipe = db.get_recipe(demo, r["id"])
        est = costing.estimate_cost(costing.lines_from_recipe(recipe), 1, recipe["wastage_percent"], TODAY)
        assert est.total > 0
        assert not [w for w in est.warnings if "unit problem" in w or "no price" in w], est.warnings


def test_stale_walnut_price_flagged_on_brownies(demo):
    rid = next(r["id"] for r in db.list_recipes(demo) if r["name"] == "Walnut Brownies")
    recipe = db.get_recipe(demo, rid)
    est = costing.estimate_cost(costing.lines_from_recipe(recipe), 1, 8, TODAY)
    assert any(w.startswith("Walnuts") and "days old" in w for w in est.warnings)


def test_full_order_to_pdf(demo):
    settings = db.get_settings(demo)
    q = costing.build_quote(_truffle(demo), db.list_fixed_costs(demo, active_only=True), 1, "kg",
                            50, "margin", settings["tax_percent"], settings["rounding_step"], TODAY)
    p = q.price
    assert q.factor == 1
    assert 250 < p.ingredient_cost < 450                    # realistic for a 1 kg truffle cake
    assert p.selling_price == pytest.approx(p.production_cost / 0.5)
    assert p.tax_amount == pytest.approx(p.selling_price * 0.05)
    assert p.final_total % 10 == 0

    sheet = costing.cost_sheet(q)
    inv = db.save_invoice(demo, "Priya Sharma", "98450 00000",
                          [{"description": q.recipe_name, "quantity_text": q.order_text, "amount": p.selling_price}],
                          sheet, p.selling_price, p.tax_amount, p.final_total, TODAY.isoformat())
    pdf = invoice.build_invoice_pdf(inv, settings)
    assert pdf.startswith(b"%PDF") and len(pdf) > 1000
    internal = invoice.build_cost_sheet_pdf(inv, settings)
    assert internal.startswith(b"%PDF")
    assert db.list_invoices(demo)[0]["number"] == "INV-0001"


def test_two_kg_order_doubles_ingredient_cost(demo):
    fixed = []
    one = costing.build_quote(_truffle(demo), fixed, 1, "kg", 0, "markup", 0, 0, TODAY)
    two = costing.build_quote(_truffle(demo), fixed, 2000, "g", 0, "markup", 0, 0, TODAY)
    assert two.price.ingredient_cost == pytest.approx(2 * one.price.ingredient_cost)


def test_pdf_survives_non_latin_customer_name(demo):
    settings = db.get_settings(demo)
    inv = {"number": "INV-0009", "date": "2026-11-10", "customer_name": "प्रिया", "customer_phone": "",
           "line_items": [{"description": "Cake ₹", "quantity_text": "1 kg", "amount": 100}],
           "subtotal": 100, "tax_amount": 0, "total": 100, "cost_breakdown": {"tax_percent": 0, "round_off": 0}}
    assert invoice.build_invoice_pdf(inv, settings).startswith(b"%PDF")
