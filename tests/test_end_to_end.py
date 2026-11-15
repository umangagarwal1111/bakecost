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


def _recipe(conn, name):
    return db.get_recipe(conn, next(r["id"] for r in db.list_recipes(conn) if r["name"] == name))


def test_full_order_to_pdf(demo):
    settings = db.get_settings(demo)
    q = costing.build_quote([costing.OrderItem(_truffle(demo), 1, "kg")], db.list_fixed_costs(demo, active_only=True),
                            50, "margin", settings["tax_percent"], settings["rounding_step"], TODAY)
    p, item = q.price, q.items[0]
    assert item.factor == 1
    assert 250 < p.ingredient_cost < 450                    # realistic for a 1 kg truffle cake
    assert p.selling_price == pytest.approx(p.production_cost / 0.5)
    assert q.subtotal == pytest.approx(p.selling_price, abs=0.01)
    assert q.tax_amount == pytest.approx(q.subtotal * 0.05, abs=0.01)
    assert q.final_total == 1200                            # the demo-script number

    inv = db.save_invoice(demo, "Priya Sharma", "98450 00000", costing.invoice_line_items(q),
                          costing.cost_sheet(q), q.subtotal, q.tax_amount, q.final_total, TODAY.isoformat())
    pdf = invoice.build_invoice_pdf(inv, settings)
    assert pdf.startswith(b"%PDF") and len(pdf) > 1000
    internal = invoice.build_cost_sheet_pdf(inv, settings)
    assert internal.startswith(b"%PDF")
    assert db.list_invoices(demo)[0]["number"] == "INV-0001"


def test_two_kg_order_doubles_ingredient_cost(demo):
    one = costing.build_quote([costing.OrderItem(_truffle(demo), 1, "kg")], [], 0, "markup", 0, 0, TODAY)
    two = costing.build_quote([costing.OrderItem(_truffle(demo), 2000, "g")], [], 0, "markup", 0, 0, TODAY)
    assert two.price.ingredient_cost == pytest.approx(2 * one.price.ingredient_cost)


def test_multi_item_order(demo):
    fixed = db.list_fixed_costs(demo, active_only=True)
    cake, cookies = _truffle(demo), _recipe(demo, "Choco Chip Cookies")
    both = costing.build_quote([costing.OrderItem(cake, 1, "kg"), costing.OrderItem(cookies, 2, "dozen")],
                               fixed, 50, "margin", 5, 10, TODAY)
    only_cake = costing.build_quote([costing.OrderItem(cake, 1, "kg")], fixed, 50, "margin", 5, 10, TODAY)

    assert [i.recipe_name for i in both.items] == ["Chocolate Truffle Cake", "Choco Chip Cookies"]
    # per-order costs (packaging Rs 40, delivery Rs 50) are charged ONCE for the whole order
    fixed_by_name = dict(both.price.fixed_cost_lines)
    assert fixed_by_name["Packaging (box + board)"] == 40 and fixed_by_name["Delivery"] == 50
    # every fixed rupee is shared out between the lines, none lost or doubled
    assert sum(i.fixed_share for i in both.items) == pytest.approx(both.price.fixed_costs_total)
    # the invoice adds up to the paisa
    assert both.subtotal == pytest.approx(sum(i.amount for i in both.items))
    assert both.subtotal + both.tax_amount + both.round_off == pytest.approx(both.final_total)
    assert both.final_total % 10 == 0
    # adding cookies costs less than pricing them as a separate order (shared box and delivery)
    only_cookies = costing.build_quote([costing.OrderItem(cookies, 2, "dozen")], fixed, 50, "margin", 5, 10, TODAY)
    assert both.subtotal < only_cake.subtotal + only_cookies.subtotal


def test_multi_item_invoice_pdfs(demo):
    settings = db.get_settings(demo)
    q = costing.build_quote([costing.OrderItem(_truffle(demo), 1, "kg"),
                             costing.OrderItem(_recipe(demo, "Walnut Brownies"), 12, "pcs")],
                            db.list_fixed_costs(demo), 50, "margin", 5, 10, TODAY)
    inv = db.save_invoice(demo, "Ravi", "", costing.invoice_line_items(q), costing.cost_sheet(q),
                          q.subtotal, q.tax_amount, q.final_total, TODAY.isoformat())
    assert len(inv["line_items"]) == 2
    assert invoice.build_invoice_pdf(inv, settings).startswith(b"%PDF")
    assert invoice.build_cost_sheet_pdf(inv, settings).startswith(b"%PDF")


def test_empty_order_rejected(demo):
    with pytest.raises(ValueError):
        costing.build_quote([], [], 50, "margin", 5, 10, TODAY)


def test_old_single_item_cost_sheet_still_prints(demo):
    """Invoices saved before multi-item orders stored a flat cost breakdown."""
    old = {"recipe_name": "Cake", "order_text": "1 kg", "scale_factor": 1, "lines": [],
           "ingredients_subtotal": 100, "wastage_percent": 5, "wastage_amount": 5, "ingredient_cost": 105,
           "fixed_cost_lines": [], "production_cost": 105, "profit_mode": "margin", "profit_percent": 50,
           "profit_amount": 105, "selling_price": 210, "tax_percent": 0, "tax_amount": 0, "round_off": 0,
           "final_total": 210, "warnings": []}
    inv = {"number": "INV-0001", "date": "2026-11-10", "customer_name": "A", "cost_breakdown": old}
    assert invoice.build_cost_sheet_pdf(inv, db.get_settings(demo)).startswith(b"%PDF")


def test_pdf_survives_non_latin_customer_name(demo):
    settings = db.get_settings(demo)
    inv = {"number": "INV-0009", "date": "2026-11-10", "customer_name": "प्रिया", "customer_phone": "",
           "line_items": [{"description": "Cake ₹", "quantity_text": "1 kg", "amount": 100}],
           "subtotal": 100, "tax_amount": 0, "total": 100, "cost_breakdown": {"tax_percent": 0, "round_off": 0}}
    assert invoice.build_invoice_pdf(inv, settings).startswith(b"%PDF")
